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

import os
import pwd
import sys
from pathlib import Path

import yaml

VCOWS = "/usr/local/bin/vcows"


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


def ssh_config(keyfile: str | None, known_hosts: str | None) -> str:
    lines = [
        "# Written by the vcows container entrypoint from target.libvirt.",
        "# Both libvirt and the OpenTofu provider run ssh, so this is the one",
        "# place that reaches both. Delete or override by mounting your own.",
        "Host *",
        "  BatchMode yes",
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
    except (OSError, yaml.YAMLError):
        # Not our error to report. `vcows validate` says it properly.
        return
    if not isinstance(cfg, dict):
        return

    target = (cfg.get("target") or {}).get("libvirt") or {}
    keyfile, known_hosts = target.get("ssh_keyfile"), target.get("known_hosts")
    if not keyfile and not known_hosts:
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
    if destination.exists():
        # Somebody mounted their own. Theirs wins.
        return

    try:
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_text(ssh_config(keyfile, known_hosts))
        destination.chmod(0o600)
    except OSError as exc:
        print(f"vcows: could not write {destination}: {exc}", file=sys.stderr)


def main() -> None:
    install(sys.argv[1:])
    os.execv(VCOWS, ["vcows", *sys.argv[1:]])


if __name__ == "__main__":
    main()
