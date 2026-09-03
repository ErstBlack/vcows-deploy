#!/usr/bin/python3
"""Put the operator's SSH credentials where `ssh` will actually look, then exec.

**Why this exists at all.** The config carries `ssh_key` and `known_hosts`, and
the obvious mechanism -- URI query parameters -- does not work. Measured against
a real hypervisor, in this image:

* libvirt's own client honours `keyfile=` on `qemu+ssh` but **ignores
  `known_hosts=`**, which is a libssh/libssh2 parameter. Without it, `ssh` falls
  back to the default path and the connection dies with "Host key verification
  failed" and no hint as to why.
* `HOME` is not a lever either: OpenSSH resolves `~` from the passwd entry, so
  exporting it changes nothing.

What the client *does* do is run `ssh`, so it reads `~/.ssh/config`. Writing that
file is the one mechanism that reaches it.

**The key is copied now.** Both fields carry the credential itself rather than a
path to a mounted file, so this writes three files: the key, the known_hosts and
a `config` naming the two fixed paths below. That is one bind mount fewer for a
site to get right, and it means nothing the operator wrote is interpolated into
`~/.ssh/config` -- the injection `_path` used to refuse has no route left. The
copy lives in the container's own filesystem and goes with `--rm`.

This is container glue, deliberately, and it is why `cli.py` contains none of it:
outside the image an operator's own `~/.ssh` is already correct, and a tool that
wrote into it would be overstepping.
"""

from __future__ import annotations

import logging
import os
import pwd
import sys
import time
from pathlib import Path

import yaml

VCOWS = "/usr/local/bin/vcows"

#: Duplicated from `orchestrator/cli.py` rather than imported: this runs *before*
#: `vcows` is a process, it lives outside `/opt/vcows`, and it imports nothing
#: from the package today. Importing `orchestrator` here would make a
#: package-level import error surface as a broken entrypoint instead of the
#: sentence `vcows` itself would have printed a moment later.
log = logging.getLogger("vcows.entrypoint")


class _Stderr(logging.StreamHandler):
    """Resolve ``sys.stderr`` when writing, not when constructed.

    Duplicated from `orchestrator.__init__` rather than imported, for the same
    reason `log` above is. A plain ``StreamHandler`` binds its stream once, so a
    handler built here would keep writing to whatever ``sys.stderr`` was at that
    instant -- which in-process, under pytest, is the previous test's capture.
    """

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        """Swallowed: the property above is the only answer."""


def _configure_logging() -> None:
    """The window before the exec. `orchestrator` configures its own at package
    import, a moment from now.

    An unusable ``VCOWS_LOG_LEVEL`` is left to `orchestrator._log_level`, which
    reports it in one place rather than two -- so a bad value falls back here
    silently rather than stopping the entrypoint.
    """
    logging.Formatter.converter = time.gmtime
    level = os.environ.get("VCOWS_LOG_LEVEL", "INFO").upper()
    if level not in logging.getLevelNamesMapping():
        level = "INFO"
    handler = _Stderr()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%SZ"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


#: Where each credential lands, under the ``.ssh`` of whatever home `ssh` will
#: read. Fixed rather than taken from the config: these are the only two values
#: `ssh_config` interpolates, so nothing the operator wrote reaches a file that
#: is parsed one directive per line.
KEY_NAME = "vcows_key"
HOSTS_NAME = "vcows_known_hosts"

#: Verbs that open no connection, so nothing needs installing for them.
#:
#: `orchestrator/cli.py`'s ``cmd_validate`` says "Offline only. No connection is
#: opened and nothing is written", and inside the image that was false: `install`
#: ran for every verb, and `config_path` matches on "a non-flag argument that is
#: an existing file" rather than on the verb, so `vcows validate config.yaml`
#: wrote ~/.ssh/config before any schema check ran. The docstring was right and
#: this was wrong, which is the decision ledger item 2.1 asked for.
#:
#: A closed set, and the default is to install: a verb added later and not named
#: here still gets its SSH config rather than silently losing it and failing at
#: the connection with an error that mentions neither.
OFFLINE = frozenset({"validate", "version"})


def home() -> Path | None:
    """The passwd entry's home -- the one `ssh` uses. `HOME` is not consulted.

    A container run with `--user` for a UID absent from /etc/passwd has no entry
    at all, which is R6's rootless trap in a different guise. Nothing is written
    in that case and the operator's own mounts have to be correct.
    """
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return None


def config_path(argv: list[str]) -> Path | None:
    """The config among the arguments, found by being a file that exists.

    Every subcommand that needs one takes it as the sole positional path, and
    `version` takes none. Matching on existence rather than position keeps this
    working if a flag is added before it.
    """
    for arg in argv:
        if not arg.startswith("-") and Path(arg).is_file():
            return Path(arg)
    return None


def credential(value: object, field: str) -> str | None:
    """One credential's text, or a refusal. ``None`` means the operator set none.

    Only the type is checked. The value goes into a file of its own now, not into
    ``~/.ssh/config``, so there is nothing to append a directive to and nothing
    to escape -- but this still runs before `vcows validate`, and a YAML list or
    number would otherwise be a ``TypeError`` out of the write rather than the
    sentence `validate` was about to print. The value is never quoted back:
    ``ssh_key`` is the private key.

    The trailing newline is not cosmetic. ``ssh_key: |-`` chomps it, and OpenSSH
    refuses a key whose last line has no terminator -- a failure that names
    neither the config nor the chomping indicator that caused it.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{field} must be the contents of the file, as text -- not a path to "
            f"it and not empty. Run `vcows validate` to see what was set."
        )
    return value if value.endswith("\n") else value + "\n"


def ssh_config(keyfile: Path | None, known_hosts: Path | None) -> str:
    """The `~/.ssh/config` naming the two files `install` writes beside it.

    Both arguments are paths this module chose, so nothing here is interpolated
    from the operator's YAML.
    """
    lines = [
        "# Written by the vcows container entrypoint from target.libvirt.",
        "# libvirt's client runs ssh, so this is the one place that reaches it.",
        "# Delete or override by mounting your own.",
        "Host *",
        "  BatchMode yes",
        # A tunnel that stops answering otherwise hangs the run forever. D42
        # gives `create` no timeout on purpose -- a multi-GB `vol-upload` has
        # no resume, so any clock long enough to be safe is too long to be
        # useful -- and this is the other half of that: it bounds a
        # *dead* connection at three minutes without putting a clock on a live
        # transfer, because keepalives only fire when nothing is moving.
        "  ServerAliveInterval 30",
        "  ServerAliveCountMax 6",
    ]
    if keyfile is not None:
        lines += [f"  IdentityFile {keyfile}", "  IdentitiesOnly yes"]
    if known_hosts is not None:
        # StrictHostKeyChecking stays on: refusing `no_verify=1` in the URI (R-D)
        # would be pointless if the same hole were opened here.
        lines += [f"  UserKnownHostsFile {known_hosts}", "  StrictHostKeyChecking yes"]
    return "\n".join(lines) + "\n"


def _write(path: Path, body: str) -> None:
    """Create `path` at 0600 and write `body`, or raise ``FileExistsError``.

    `os.open` with `O_EXCL` and the mode on the call, rather than `exists()` then
    `write_text` then `chmod`. Two things were wrong with that shape.

    `write_text` creates at `0o666 & ~umask`, and the image's umask is 0022 --
    measured, `podman run --rm --entrypoint sh IMAGE -c umask`. So a file now
    holding the private key itself would be 0644 until the `chmod` on the next
    line, and `ssh` refuses a group-readable key anyway.
    `orchestrator/cli.py`'s `os.umask(0o077)` cannot close the window: `main`
    `execv`s into vcows *after* these writes. The mode argument here is honoured
    at creation, and 0022 does not touch owner bits, so the file is 0600 from the
    syscall that makes it.

    `exists()` and `write_text` are also two syscalls around a call that
    *truncates*, so a file arriving between them was silently clobbered -- what
    the "theirs wins" branch in `install` exists to prevent. `O_EXCL` makes that
    one atomic operation, and `FileExistsError` is its own `OSError` subclass, so
    the caller can tell the two outcomes apart.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(body)


def install(argv: list[str]) -> None:
    path = config_path(argv)
    if path is None:
        return

    try:
        cfg = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        # Not our error to report -- `vcows validate` says it properly, which is
        # why this stays DEBUG. What is only knowable here is the consequence:
        # no SSH config was installed, so the connection failure that follows
        # will not mention the config that caused it.
        log.debug("%s could not be read, so no SSH config was installed: %s", path, exc)
        return
    if not isinstance(cfg, dict):
        log.debug("no SSH config installed: %s is not a mapping", path)
        return

    # Two `isinstance` checks rather than `or {}`, because this is the operator's
    # YAML and either level can be a string, a list or a number. `.get` on one of
    # those is an `AttributeError` out of the entrypoint -- a traceback where
    # `vcows validate`, a moment later, has a sentence.
    target = cfg.get("target")
    if not isinstance(target, dict):
        log.debug("no SSH config installed: `target` is not a mapping")
        return
    libvirt = target.get("libvirt")
    if not isinstance(libvirt, dict):
        log.debug("no SSH config installed: `target.libvirt` is not a mapping")
        return
    # Before `home()`, so a value that is not text is refused even for a UID with
    # no passwd entry -- where there is nothing to write but still something to
    # say.
    try:
        key = credential(libvirt.get("ssh_key"), "ssh_key")
        known_hosts = credential(libvirt.get("known_hosts"), "known_hosts")
    except ValueError as exc:
        log.warning("%s", exc)
        return
    if key is None and known_hosts is None:
        return

    where = home()
    if where is None:
        log.warning(
            "no passwd entry for this UID, so ~/.ssh cannot be located; "
            "mount your SSH config yourself"
        )
        return

    ssh_dir = where / ".ssh"
    key_path = ssh_dir / KEY_NAME if key is not None else None
    hosts_path = ssh_dir / HOSTS_NAME if known_hosts is not None else None

    # `config` first, and the order matters. It is the file an operator mounts to
    # take this over, so a `config` that is already there has to stop the run
    # *before* the key is copied in -- otherwise the "theirs wins" message below
    # would be false about a private key already sitting in the container.
    #
    # The `mkdir` is deliberately outside the inner catch: with `exist_ok=True`
    # its only `FileExistsError` is `~/.ssh` existing as a regular file, which is
    # not "somebody mounted their own config" and must not claim to be.
    try:
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            _write(ssh_dir / "config", ssh_config(key_path, hosts_path))
            if key is not None:
                _write(ssh_dir / KEY_NAME, key)
            if known_hosts is not None:
                _write(ssh_dir / HOSTS_NAME, known_hosts)
        except FileExistsError as exc:
            # Somebody mounted their own. Theirs wins -- and it is said out loud,
            # because the alternative is debugging a credential the config named
            # and nothing ever installed.
            log.warning(
                "%s already exists; leaving it alone. This run's ssh_key and "
                "known_hosts were not installed.",
                exc.filename,
            )
            return
    except OSError as exc:
        # Name the consequence, as the "already exists" branch above does. The
        # next thing the operator sees is `Host key verification failed` out of
        # ssh, which points at nothing. Measured under `--user 4242`, where the
        # synthesised passwd entry's home is `/` and unwritable: two messages,
        # and without this line the second looks like the error.
        log.warning(
            "could not write into %s: %s. This run's ssh_key and known_hosts "
            "were not installed, so the connection will use whatever ssh finds "
            "on its own -- likely failing with a host key or permission error "
            "that does not mention this.",
            ssh_dir,
            exc,
        )


def verb(argv: list[str]) -> str | None:
    """The subcommand, which is the first non-flag argument.

    `vcows`'s only top-level flag is `--version`, which takes no value, and every
    other flag belongs to a subparser and so follows the verb. Position among the
    non-flags rather than membership, so a config file that happens to be named
    `validate` is not mistaken for the verb.
    """
    for arg in argv:
        if not arg.startswith("-"):
            return arg
    return None


def main() -> None:
    _configure_logging()
    if verb(sys.argv[1:]) not in OFFLINE:
        install(sys.argv[1:])
    # S606 flags shell-less execution, which is the safe half of the pair:
    # execv with an argv list cannot be shell-injected, and replacing the
    # process is what an entrypoint is for.
    os.execv(VCOWS, ["vcows", *sys.argv[1:]])  # noqa: S606


if __name__ == "__main__":
    main()
