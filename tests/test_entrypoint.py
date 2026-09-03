"""The container entrypoint's one job: fill ~/.ssh, then get out of the way.

This runs before `vcows` exists as a process -- `install()` parses the config
itself and `os.execv`s afterwards -- so nothing the schema rejects has been
rejected yet by the time these files are written. That ordering is why the type
check lives here as well as in `TARGET_SCHEMA`, and it is the whole reason this
module is tested at all rather than left to the image gate.

The credentials are contents now, not paths, so what is asserted here is where
they land and at what mode. The key comes from `conftest.SSH_KEY`, which is the
one fixture key in the suite and is assembled so that no file on disk -- source
or `.pyc` -- ever holds a whole PEM header. See the note beside it.

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
import yaml

from container import entrypoint
from tests.conftest import KNOWN_HOSTS as GOOD_HOSTS
from tests.conftest import SSH_KEY as GOOD_KEY

#: The two fixed paths under the home the entrypoint finds, relative to `.ssh`.
#: Fixed rather than configurable is what makes `ssh_config` interpolate nothing
#: the operator wrote.
KEY_NAME = entrypoint.KEY_NAME
HOSTS_NAME = entrypoint.HOSTS_NAME


# -- the rendered file -------------------------------------------------------


def test_the_written_config_carries_exactly_the_documented_directives():
    body = entrypoint.ssh_config(Path("/h/.ssh/k"), Path("/h/.ssh/kh"))
    assert [ln.strip() for ln in body.splitlines() if not ln.startswith("#")] == [
        "Host *",
        "BatchMode yes",
        "ServerAliveInterval 30",
        "ServerAliveCountMax 6",
        "IdentityFile /h/.ssh/k",
        "IdentitiesOnly yes",
        "UserKnownHostsFile /h/.ssh/kh",
        "StrictHostKeyChecking yes",
    ]


def test_either_credential_alone_writes_only_its_own_half():
    assert "UserKnownHostsFile" not in entrypoint.ssh_config(Path("/h/k"), None)
    assert "IdentityFile" not in entrypoint.ssh_config(None, Path("/h/kh"))


# -- values that are not credentials ----------------------------------------


#: A YAML mapping is not a list of strings, and `target.libvirt.ssh_key` can be
#: any of these before `vcows validate` has run. Each used to be an unhandled
#: `TypeError` out of the write.
NOT_A_CREDENTIAL = [42, ["a", "b"], {"path": "/k"}, True, "", "   \n"]


@pytest.mark.parametrize("field", ["ssh_key", "known_hosts"])
@pytest.mark.parametrize("value", NOT_A_CREDENTIAL)
def test_a_credential_that_is_not_text_is_refused_by_name(field, value):
    """The operator's next move is to edit the key that was refused, so the
    message names it. The value never appears: `ssh_key` is the private key."""
    with pytest.raises(ValueError, match=field):
        entrypoint.credential(value, field)


def test_a_credential_gains_the_trailing_newline_openssh_wants():
    """`ssh_key: |-` in YAML strips it, and OpenSSH rejects a key whose final
    line has no terminator -- a failure that names neither the config nor the
    chomping indicator that caused it."""
    assert entrypoint.credential("no newline", "ssh_key") == "no newline\n"


# -- install() ---------------------------------------------------------------


def config_file(tmp_path, ssh_key=GOOD_KEY, known_hosts=GOOD_HOSTS):
    """A config carrying the credentials themselves, as a site now writes one."""
    libvirt: dict = {}
    if ssh_key is not None:
        libvirt["ssh_key"] = ssh_key
    if known_hosts is not None:
        libvirt["known_hosts"] = known_hosts
    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump({"target": {"libvirt": libvirt}}))
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


def test_install_writes_both_credentials_and_a_config_naming_them(tmp_path, fake_home):
    """The whole of what changed: the key is *copied* into the container now.

    Both halves. `known_hosts` is the one the URI cannot carry at all, so a run
    that installed only the key would fail at the host key check.
    """
    entrypoint.install([str(config_file(tmp_path))])
    ssh = fake_home / ".ssh"
    key, hosts = ssh / KEY_NAME, ssh / HOSTS_NAME

    assert key.read_text() == GOOD_KEY
    assert hosts.read_text() == GOOD_HOSTS
    body = (ssh / "config").read_text()
    assert f"IdentityFile {key}" in body
    assert f"UserKnownHostsFile {hosts}" in body
    # The key file above all: ssh refuses a private key any group can read, so
    # a wrong mode here is a connection failure as well as an exposure.
    for path in (key, hosts, ssh / "config"):
        assert path.stat().st_mode & 0o777 == 0o600, path


#: Explicit ids, because pytest builds one from the values otherwise -- and
#: `.pytest_cache/v/cache/nodeids` then holds the whole fixture key, which the
#: gitleaks gate reads and reports. Measured: one finding, in the cache only.
@pytest.mark.parametrize(
    "field, value, name, directive",
    [
        pytest.param("ssh_key", GOOD_KEY, KEY_NAME, "IdentityFile", id="ssh_key"),
        pytest.param(
            "known_hosts",
            GOOD_HOSTS,
            HOSTS_NAME,
            "UserKnownHostsFile",
            id="known_hosts",
        ),
    ],
)
def test_install_writes_the_one_credential_it_was_given(
    tmp_path, fake_home, field, value, name, directive
):
    """Either alone is a usable config: a key for a host already in the default
    known_hosts, or a known_hosts for a key an agent supplies."""
    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump({"target": {"libvirt": {field: value}}}))

    entrypoint.install([str(path)])
    ssh = fake_home / ".ssh"
    assert (ssh / name).read_text() == value
    assert f"{directive} {ssh / name}" in (ssh / "config").read_text()
    assert sorted(p.name for p in ssh.iterdir()) == sorted(["config", name])


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
    assert (home / ".ssh" / KEY_NAME).read_text() == GOOD_KEY


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
    credential the config named and nothing installed looks like a vcows bug.

    The config is written *first* for this reason. Writing the key first would
    leave the operator's own config governing a private key vcows had already
    copied into the container, and the message below would be false.
    """
    ssh = fake_home / ".ssh"
    ssh.mkdir(mode=0o700)
    (ssh / "config").write_text("Host *\n  IdentityFile /mine\n")

    entrypoint.install([str(config_file(tmp_path))])
    assert (ssh / "config").read_text() == "Host *\n  IdentityFile /mine\n"
    assert not (ssh / KEY_NAME).exists(), "the key was copied in anyway"
    err = capsys.readouterr().err
    assert "already exists" in err
    assert str(ssh / "config") in err, "the warning has to name the file that won"


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

    ssh = fake_home / ".ssh"
    for name in ("config", KEY_NAME, HOSTS_NAME):
        assert (ssh / name).stat().st_mode & 0o777 == 0o600, name
    # The directory too, and for the same reason: `mkdir` without a mode creates
    # at 0o777 & ~umask, which under this umask is 0755 -- a world-readable
    # directory now holding the private key itself.
    assert ssh.stat().st_mode & 0o777 == 0o700


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
    # The directory and the errno are the two facts the operator has to act on.
    # The phrase, not the bare path: the OSError's own text repeats the path,
    # so a message that dropped the directory would still contain it.
    assert f"could not write into {blocked / '.ssh'}:" in err
    assert "Not a directory" in err


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

    assert (fake_home / ".ssh" / KEY_NAME).read_text() == GOOD_KEY


def test_a_config_file_named_like_an_offline_verb_is_not_read_as_one(
    tmp_path, fake_home, no_exec, monkeypatch
):
    """`verb` takes the first non-flag argument rather than testing membership,
    so a config that happens to be called `validate` cannot suppress the install
    for a `deploy`."""
    named = config_file(tmp_path).rename(tmp_path / "validate")
    monkeypatch.setattr(sys, "argv", ["entrypoint", "deploy", str(named)])
    entrypoint.main()

    assert (fake_home / ".ssh" / KEY_NAME).read_text() == GOOD_KEY


def test_install_writes_nothing_when_a_credential_is_not_text(
    tmp_path, fake_home, capsys
):
    """Refused before anything is written, and refused without echoing the value:
    `ssh_key` is the private key itself now, so a message quoting what it found
    would put key material in `podman logs`."""
    path = config_file(tmp_path, ssh_key={"path": "/run/secrets/k"})
    entrypoint.install([str(path)])
    assert not (fake_home / ".ssh").exists()
    err = capsys.readouterr().err
    assert "ssh_key" in err
    assert "/run/secrets/k" not in err


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

    assert (fake_home / ".ssh" / KEY_NAME).read_text() == GOOD_KEY


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
