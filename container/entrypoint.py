#!/usr/bin/python3
"""Put the operator's SSH credentials where `ssh` will actually look, then exec.

**Why this exists at all.** The config carries `ssh_keyfile` and `known_hosts`,
and the obvious mechanism -- URI query parameters -- does not work. Measured
against a real hypervisor, in this image:

* libvirt's own client honours `keyfile=` on `qemu+ssh` but **ignores
  `known_hosts=`**, which is a libssh/libssh2 parameter. Without it, `ssh` falls
  back to the default path and the connection dies with "Host key verification
  failed" and no hint as to why.
* The provider's `qemu+ssh` spells it `knownhosts`, with no underscore, and its
  `qemu+sshcmd` -- the only transport that reaches a modern split-daemon host --
  fails on either spelling.
* `HOME` is not a lever either: OpenSSH and Go's `os/user` both resolve `~` from
  the passwd entry, so exporting it changes nothing.

What both clients *do* share is that they run `ssh`, so they both read
`~/.ssh/config`. Writing that file is the one mechanism that reaches both, and it
leaves the key and the known_hosts file exactly where they were mounted, read
only. Nothing here copies key material.

This is container glue, deliberately, and it is why `cli.py` contains none of it:
outside the image an operator's own `~/.ssh` is already correct, and a tool that
wrote into it would be overstepping.
"""

from __future__ import annotations

import contextlib
import logging
import os
import pwd
import re
import sys
import time
from pathlib import Path

import yaml

VCOWS = "/usr/local/bin/vcows"

#: Duplicated from `orchestrator/cli.py` rather than imported, for the same
#: reason `SSH_PATH` below is: this runs *before* `vcows` is a process, it lives
#: outside `/opt/vcows`, and it imports nothing from the package today. Importing
#: `orchestrator` here would make a package-level import error surface as a
#: broken entrypoint instead of the sentence `vcows` itself would have printed a
#: moment later.
log = logging.getLogger("vcows.entrypoint")

#: An absolute path with no whitespace in it. Mirrors ``SSH_PATH_PATTERN`` in
#: ``orchestrator/backends/libvirt/schema.py``, and is duplicated rather than
#: imported on purpose: this runs *before* `vcows` is a process at all, so the
#: schema's rejection of the same value would arrive after the file below had
#: already been written and read.
SSH_PATH = re.compile(r"^/[^\s]*\Z")

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


def _path(value: object, field: str) -> str | None:
    """One credential path, or a refusal. ``None`` means the operator set none.

    ``ssh`` reads its config one directive per line, so a value carrying a
    newline does not become a path -- it becomes whatever follows it.
    ``ProxyCommand`` is command execution on the next connection, and
    ``StrictHostKeyChecking no`` reopens from here exactly the hole R-D refuses
    in the URI.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not SSH_PATH.match(value):
        raise ValueError(
            f"{field} must be an absolute path with no whitespace in it. It is "
            f"written into ~/.ssh/config verbatim, where a newline would append "
            f"directives of its own. Run `vcows validate` to see the value."
        )
    return value


def ssh_config(keyfile: str | None, known_hosts: str | None) -> str:
    """Raises ``ValueError`` rather than interpolating anything questionable."""
    keyfile = _path(keyfile, "ssh_keyfile")
    known_hosts = _path(known_hosts, "known_hosts")
    lines = [
        "# Written by the vcows container entrypoint from target.libvirt.",
        "# Both libvirt and the OpenTofu provider run ssh, so this is the one",
        "# place that reaches both. Delete or override by mounting your own.",
        "Host *",
        "  BatchMode yes",
        # A tunnel that stops answering otherwise hangs the run forever. D42
        # gives `plan` and `apply` no timeout on purpose -- a multi-GB
        # `vol-upload` has no resume, so any clock long enough to be safe is too
        # long to be useful -- and this is the other half of that: it bounds a
        # *dead* connection at three minutes without putting a clock on a live
        # transfer, because keepalives only fire when nothing is moving.
        "  ServerAliveInterval 30",
        "  ServerAliveCountMax 6",
    ]
    if keyfile:
        lines += [f"  IdentityFile {keyfile}", "  IdentitiesOnly yes"]
    if known_hosts:
        # StrictHostKeyChecking stays on: refusing `no_verify=1` in the URI (R-D)
        # would be pointless if the same hole were opened here.
        lines += [f"  UserKnownHostsFile {known_hosts}", "  StrictHostKeyChecking yes"]
    return "\n".join(lines) + "\n"


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
    keyfile, known_hosts = libvirt.get("ssh_keyfile"), libvirt.get("known_hosts")
    if not keyfile and not known_hosts:
        return

    # Before `home()`, so a poisoned config is refused even for a UID with no
    # passwd entry -- where there is nothing to write but still something to say.
    try:
        body = ssh_config(keyfile, known_hosts)
    except ValueError as exc:
        print(f"vcows: {exc}", file=sys.stderr)
        return

    where = home()
    if where is None:
        print(
            "vcows: no passwd entry for this UID, so ~/.ssh cannot be located; "
            "mount your SSH config yourself",
            file=sys.stderr,
        )
        return

    ssh_dir = where / ".ssh"
    destination = ssh_dir / "config"

    # `os.open` with `O_EXCL` and the mode on the call, rather than `exists()`
    # then `write_text` then `chmod`. Two things were wrong with that shape.
    #
    # `write_text` creates at `0o666 & ~umask`, and the image's umask is 0022 --
    # measured, `podman run --rm --entrypoint sh IMAGE -c umask`. So the file
    # naming the operator's private key path was 0644 until the `chmod` on the
    # next line. `orchestrator/cli.py`'s `os.umask(0o077)` cannot close that:
    # `main` below `execv`s into vcows *after* this write, so it is set strictly
    # too late, and nothing else in the image sets one. The mode argument here is
    # honoured at creation, and 0022 does not touch owner bits, so the file is
    # 0600 from the syscall that makes it.
    #
    # `exists()` and `write_text` are also two syscalls around a call that
    # *truncates*, so a config arriving between them was silently clobbered --
    # exactly what the "theirs wins" branch below exists to prevent. `O_EXCL`
    # makes that one atomic operation.
    #
    # The two messages stay separable because `FileExistsError` is its own
    # `OSError` subclass and is caught first. The `mkdir` is deliberately outside
    # that inner catch: with `exist_ok=True` its only `FileExistsError` is
    # `~/.ssh` existing as a regular file, which is not "somebody mounted their
    # own config" and must not claim to be.
    try:
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Somebody mounted their own. Theirs wins -- and it is said out loud,
            # because the alternative is debugging a credential the config named
            # and nothing ever installed.
            print(
                f"vcows: {destination} already exists; leaving it alone. This "
                f"run's ssh_keyfile and known_hosts were not installed.",
                file=sys.stderr,
            )
            return
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
    except OSError as exc:
        # Name the consequence, as the "already exists" branch above does. The
        # next thing the operator sees is `Host key verification failed` out of
        # ssh, which points at nothing. Measured under `--user 4242`, where the
        # synthesised passwd entry's home is `/` and unwritable: two messages,
        # and without this line the second looks like the error.
        print(
            f"vcows: could not write {destination}: {exc}. This run's "
            f"ssh_keyfile and known_hosts were not installed, so the connection "
            f"will use whatever ssh finds on its own -- likely failing with a "
            f"host key or permission error that does not mention this.",
            file=sys.stderr,
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
    # `vcows` configures its own logging a moment from now; this covers the
    # window before the exec. An unusable VCOWS_LOG_LEVEL is left entirely to
    # `cli._log_level`, which reports it in one place rather than two.
    logging.Formatter.converter = time.gmtime
    with contextlib.suppress(ValueError):
        logging.basicConfig(
            level=os.environ.get("VCOWS_LOG_LEVEL", "INFO").upper(),
            stream=sys.stderr,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
            force=True,
        )
    if verb(sys.argv[1:]) not in OFFLINE:
        install(sys.argv[1:])
    # S606 flags shell-less execution, which is the safe half of the pair:
    # execv with an argv list cannot be shell-injected, and replacing the
    # process is what an entrypoint is for.
    os.execv(VCOWS, ["vcows", *sys.argv[1:]])  # noqa: S606


if __name__ == "__main__":
    main()
