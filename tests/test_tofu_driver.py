"""The driver, against a `tofu` that is not OpenTofu.

A fake binary on PATH is the only way to pin the things that actually go wrong
here: which flags are passed, what the child's environment is, and whether the
JSON stream or the exit code is treated as the authority. None of that needs a
real OpenTofu, and none of it should skip when one is absent.

The fake records its argv and environment and writes whatever stream the test
asks for, so a test can produce a diagnostic, a change summary, a truncated file
or a non-zero exit without arranging for OpenTofu to be unhappy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orchestrator import tofu

FAKE = """#!/usr/bin/env python3
import json, os, sys

argv = sys.argv[1:]
with open(os.environ["FAKE_TOFU_LOG"], "a") as fh:
    fh.write(json.dumps({
        "argv": argv,
        "env": {k: os.environ.get(k) for k in (
            "CHECKPOINT_DISABLE", "TF_IN_AUTOMATION", "TF_CLI_CONFIG_FILE"
        )},
    }) + "\\n")

for arg in argv:
    if arg.startswith("-json-into="):
        with open(arg.split("=", 1)[1], "w") as fh:
            fh.write(os.environ.get("FAKE_TOFU_STREAM", ""))

sys.stdout.write(os.environ.get("FAKE_TOFU_STDOUT", ""))
sys.exit(int(os.environ.get("FAKE_TOFU_EXIT", "0")))
"""

DIAGNOSTIC = json.dumps(
    {
        "@level": "error",
        "type": "diagnostic",
        "diagnostic": {
            "severity": "error",
            "summary": "Volume Creation Failed",
            "detail": "storage volume 'app01.qcow2' exists already",
            "address": 'libvirt_volume.overlay["app01"]',
        },
    }
)

CHANGES = json.dumps(
    {
        "@level": "info",
        "type": "change_summary",
        "changes": {"add": 3, "change": 0, "remove": 0, "operation": "plan"},
    }
)


@pytest.fixture
def fake_tofu(tmp_path, monkeypatch):
    """A `tofu` on PATH that does what the test tells it to."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "tofu"
    binary.write_text(FAKE)
    binary.chmod(0o755)

    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOFU_LOG", str(log))
    return log


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return d


# -- what is on the command line -------------------------------------------


def test_every_command_chdirs_and_refuses_to_prompt(fake_tofu, workdir):
    """A prompt at an air-gapped site is a hang, not a question."""
    tofu.init(workdir)
    tofu.plan(workdir, workdir / "plan.bin")
    tofu.apply(workdir, workdir / "plan.bin")

    for call in calls(fake_tofu):
        assert f"-chdir={workdir}" in call["argv"]
        assert "-input=false" in call["argv"]


def test_the_machine_stream_goes_to_a_file_beside_the_state(fake_tofu, workdir):
    """`-json-into` rather than `-json`: the operator keeps stdout."""
    tofu.plan(workdir, workdir / "plan.bin")
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert f"-json-into={workdir / 'plan.json'}" in argv


def test_plan_writes_a_plan_file(fake_tofu, workdir):
    tofu.plan(workdir, workdir / "plan.bin")
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert "-out" in argv and str(workdir / "plan.bin") in argv


def test_apply_takes_the_saved_plan_and_never_auto_approves(fake_tofu, workdir):
    """A saved plan needs no approval, and asking for one anyway would let a
    future refactor drop the plan file without the flag noticing."""
    tofu.apply(workdir, workdir / "plan.bin")
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert str(workdir / "plan.bin") in argv
    assert "-auto-approve" not in argv


def test_no_color_when_stdout_is_not_a_terminal(fake_tofu, workdir):
    """1.12.6 ignores NO_COLOR and writes escapes even into a file, so this flag
    is the only thing between a piped log and a screenful of ANSI."""
    tofu.init(workdir)
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert "-no-color" in argv


def test_colour_is_kept_when_stdout_is_a_terminal(fake_tofu, workdir, monkeypatch):
    """The other half: an operator watching a multi-GB upload should see tofu
    exactly as they would running it by hand."""

    class Terminal:
        def isatty(self):
            return True

        def write(self, _text):
            return 0

        def flush(self):
            pass

    monkeypatch.setattr("sys.stdout", Terminal())
    tofu.init(workdir)
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert "-no-color" not in argv


def test_the_child_environment_is_offline_and_non_interactive(fake_tofu, workdir):
    tofu.init(workdir)
    (env,) = [c["env"] for c in calls(fake_tofu)]
    assert env["CHECKPOINT_DISABLE"] == "1"
    assert env["TF_IN_AUTOMATION"] == "1"


def test_the_cli_config_is_passed_through_untouched(fake_tofu, workdir, monkeypatch):
    """TF_CLI_CONFIG_FILE is what points OpenTofu at the mirror (R6). The driver
    must never invent one: the image sets it, and so do the tests."""
    monkeypatch.setenv("TF_CLI_CONFIG_FILE", "/opt/tofu/tofurc")
    tofu.init(workdir)
    (env,) = [c["env"] for c in calls(fake_tofu)]
    assert env["TF_CLI_CONFIG_FILE"] == "/opt/tofu/tofurc"


# -- what comes back --------------------------------------------------------


def test_diagnostics_are_read_from_the_stream(fake_tofu, workdir, monkeypatch):
    """Never off stderr: the human rendering is boxed, wrapped and coloured, and
    upstream is free to reword it in any release."""
    monkeypatch.setenv("FAKE_TOFU_STREAM", DIAGNOSTIC + "\n")
    monkeypatch.setenv("FAKE_TOFU_EXIT", "1")

    with pytest.raises(tofu.TofuError) as caught:
        tofu.apply(workdir, workdir / "plan.bin")

    assert "Volume Creation Failed" in str(caught.value)
    assert caught.value.result is not None
    (error,) = caught.value.result.errors
    assert error.address == 'libvirt_volume.overlay["app01"]'
    assert "exists already" in error.detail


def test_the_change_summary_is_parsed(fake_tofu, workdir, monkeypatch):
    monkeypatch.setenv("FAKE_TOFU_STREAM", CHANGES + "\n")
    assert tofu.plan(workdir, workdir / "plan.bin").changes["add"] == 3


def test_a_truncated_stream_does_not_turn_success_into_failure(
    fake_tofu, workdir, monkeypatch
):
    """tofu killed mid-write, or a full disk. The exit code is the authority on
    whether the apply worked; the stream is how we describe it."""
    monkeypatch.setenv("FAKE_TOFU_STREAM", CHANGES + '\n{"@level": "inf')
    result = tofu.apply(workdir, workdir / "plan.bin")
    assert result.returncode == 0
    assert result.changes["add"] == 3


def test_a_failure_with_no_diagnostics_still_raises(fake_tofu, workdir, monkeypatch):
    monkeypatch.setenv("FAKE_TOFU_EXIT", "2")
    with pytest.raises(tofu.TofuError, match="exit 2"):
        tofu.apply(workdir, workdir / "plan.bin")


def test_warnings_do_not_fail_a_run(fake_tofu, workdir, monkeypatch):
    warning = json.dumps(
        {
            "@level": "warn",
            "type": "diagnostic",
            "diagnostic": {
                "severity": "warning",
                "summary": "Deprecated",
                "detail": "",
            },
        }
    )
    monkeypatch.setenv("FAKE_TOFU_STREAM", warning + "\n")
    result = tofu.apply(workdir, workdir / "plan.bin")
    assert result.errors == ()
    assert len(result.diagnostics) == 1


def test_outputs_are_captured_rather_than_inherited(fake_tofu, workdir, monkeypatch):
    """The one place we want the bytes rather than the view: it is the handoff."""
    monkeypatch.setenv(
        "FAKE_TOFU_STDOUT", json.dumps({"vms": {"value": {"app01": {"name": "app01"}}}})
    )
    assert tofu.outputs(workdir)["vms"]["value"]["app01"]["name"] == "app01"


def test_version_reports_what_actually_ran(fake_tofu, workdir, monkeypatch):
    monkeypatch.setenv(
        "FAKE_TOFU_STDOUT", json.dumps({"terraform_version": "1.12.6", "platform": "x"})
    )
    assert tofu.version()["terraform_version"] == "1.12.6"


# -- Ctrl-C ------------------------------------------------------------------


class Stubborn:
    """A child that raises KeyboardInterrupt from `wait` a given number of times.

    Faked rather than signalled for real: the behaviour under test is which of
    the two waits runs and whether `kill` follows, and driving that with actual
    SIGINTs would make it a test of process-group delivery.
    """

    def __init__(self, interrupts: int):
        self.remaining = interrupts
        self.killed = False
        self.returncode = 0
        #: Every `timeout=` the driver passed, in order. Recorded rather than
        #: ignored because the value is the thing under test below.
        self.timeouts: list[float | None] = []

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if self.remaining:
            self.remaining -= 1
            raise KeyboardInterrupt
        return self.returncode

    def kill(self):
        self.killed = True


def test_one_ctrl_c_waits_for_tofu_instead_of_killing_it(
    fake_tofu, workdir, monkeypatch
):
    """`subprocess.run` sleeps 0.25 s and then SIGKILLs, which lands in the middle
    of an apply. tofu's own handler releases the state lock and stops between
    resources, and that shutdown is worth waiting for."""
    child = Stubborn(interrupts=1)
    monkeypatch.setattr(tofu.subprocess, "Popen", lambda *a, **k: child)

    with pytest.raises(KeyboardInterrupt):
        tofu.apply(workdir, workdir / "plan.bin")
    assert not child.killed


def test_a_second_ctrl_c_is_the_operator_meaning_it(fake_tofu, workdir, monkeypatch):
    child = Stubborn(interrupts=2)
    monkeypatch.setattr(tofu.subprocess, "Popen", lambda *a, **k: child)

    with pytest.raises(KeyboardInterrupt):
        tofu.apply(workdir, workdir / "plan.bin")
    assert child.killed


def test_init_runs_on_a_clock_and_apply_does_not(fake_tofu, workdir, monkeypatch):
    """D42, and the one property of `_run` nothing else pins.

    `SHORT_TIMEOUT` exists for `init`, where a hang is a name lookup or a stuck
    registry and never work. `apply` streams the whole golden image through the
    SSH tunnel with `vol-upload`, which has no resume, so a timeout that fires
    kills a live upload. Making the timeout unconditional passed the entire
    suite before this test existed.
    """
    child = Stubborn(interrupts=0)
    monkeypatch.setattr(tofu.subprocess, "Popen", lambda *a, **k: child)

    tofu.init(workdir)
    assert child.timeouts == [tofu.SHORT_TIMEOUT]

    child.timeouts.clear()
    tofu.apply(workdir, workdir / "plan.bin")
    assert child.timeouts == [None], "a clock on apply kills a resumeless upload"


def test_output_and_version_run_on_the_short_clock(fake_tofu, workdir, monkeypatch):
    """The other half of the same fact, and the half #17 did not pin.

    `SHORT_TIMEOUT`'s docstring scopes it to "init/output/version only". The
    test above covers `init`, which goes through `_run` and `Popen`. `outputs`
    and `version` both reach the same constant through `_capture`, which uses
    `subprocess.run` -- so the recording goes on the call, not on a fake
    process. Replacing that timeout with `None` passed the whole suite.
    """
    seen: list[float | None] = []
    real = tofu.subprocess.run

    def recording(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    monkeypatch.setattr(tofu.subprocess, "run", recording)
    monkeypatch.setenv("FAKE_TOFU_STDOUT", json.dumps({"terraform_version": "1.12.6"}))
    tofu.version()
    monkeypatch.setenv("FAKE_TOFU_STDOUT", json.dumps({"vms": {"value": {}}}))
    tofu.outputs(workdir)

    assert seen == [tofu.SHORT_TIMEOUT, tofu.SHORT_TIMEOUT], (
        "a capture with no clock is a CLI that hangs and never writes its record"
    )


def test_a_missing_binary_says_so(workdir, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(tofu.TofuError, match="not on PATH"):
        tofu.init(workdir)


def test_warnings_are_the_half_that_did_not_stop_the_run():
    """Nothing read them before: `errors` is consulted on the failure path and the
    rest of the stream was dropped, so the run directory recorded a clean apply
    that OpenTofu had warned about."""
    result = tofu.Result(
        0,
        (
            tofu.Diagnostic(
                "warning", "deprecated attribute", address="libvirt_domain"
            ),
            tofu.Diagnostic("error", "nope"),
        ),
    )
    assert [d.summary for d in result.warnings] == ["deprecated attribute"]
    assert [d.summary for d in result.errors] == ["nope"]


# -- the stream's own defaults ------------------------------------------------
#
# `_read_stream` reads every field through a fallback chain, because OpenTofu's
# stream is a contract we do not own: `diagnostic` is optional on a message that
# declares itself one, and `severity` and `summary` are optional inside it. Each
# link in those chains was reachable and unasserted, so a fallback could be
# removed, renamed or pointed at the wrong key and every test still passed.


def stream(tmp_path, *messages) -> Path:
    path = tmp_path / "read.json"
    path.write_text("".join(json.dumps(m) + "\n" for m in messages))
    return path


def test_a_diagnostic_body_with_nothing_in_it_falls_back_to_the_envelope(tmp_path):
    """`@level` and `@message` sit on the message, `severity` and `summary`
    inside `diagnostic`, and the inner pair is optional."""
    (d,), changes = tofu._read_stream(
        stream(
            tmp_path,
            {
                "@level": "warn",
                "@message": "envelope headline",
                "type": "diagnostic",
                "diagnostic": {},
            },
        )
    )
    assert (d.severity, d.summary) == ("warn", "envelope headline")
    # Empty strings rather than None: `Diagnostic` is frozen and typed `str`, and
    # `__str__` and `_log_diagnostic` both branch on their truthiness.
    assert (d.detail, d.address) == ("", "")
    # A stream carrying no `change_summary` has no counts, not no dictionary.
    assert changes == {}


def test_a_diagnostic_that_names_no_severity_anywhere_is_an_error(tmp_path):
    """The end of the chain. Treating an unlabelled diagnostic as an error is
    the safe direction: it is reported and it fails the run rather than being
    filed as a warning nothing acts on."""
    (d,), _ = tofu._read_stream(stream(tmp_path, {"type": "diagnostic"}))
    assert (d.severity, d.summary) == ("error", "")


def test_the_diagnostics_own_severity_beats_the_envelopes(tmp_path):
    """The first link, and the one every fixture hides: OpenTofu sets `@level`
    and `diagnostic.severity` to the same word, so a lookup on the wrong key
    falls through to a fallback that happens to agree."""
    (d,), _ = tofu._read_stream(
        stream(
            tmp_path,
            {
                "@level": "error",
                "type": "diagnostic",
                "diagnostic": {"severity": "warning", "summary": "s"},
            },
        )
    )
    assert d.severity == "warning"


def test_a_change_summary_with_no_counts_is_empty_rather_than_fatal(tmp_path):
    assert tofu._read_stream(stream(tmp_path, {"type": "change_summary"})) == ((), {})


# -- the captured commands ---------------------------------------------------


def test_a_capture_that_fails_raises_rather_than_returning_nothing(
    fake_tofu, workdir, monkeypatch
):
    """`_run` has `test_a_failure_with_no_diagnostics_still_raises`; `_capture` is
    the other half of the driver and had nothing. Left on `check=True` the failure
    arrives as `CalledProcessError`, which nothing above catches."""
    monkeypatch.setenv("FAKE_TOFU_EXIT", "1")
    with pytest.raises(tofu.TofuError, match="exit 1"):
        tofu.outputs(workdir)


def test_a_capture_that_prints_nothing_is_an_empty_document(
    fake_tofu, workdir, monkeypatch
):
    """`tofu output -json` in a directory with no outputs prints nothing at all,
    and `json.loads("")` is a decode error rather than an empty inventory."""
    monkeypatch.setenv("FAKE_TOFU_STDOUT", "")
    assert tofu.outputs(workdir) == {}


def test_the_captured_commands_get_the_same_child_environment(
    fake_tofu, workdir, monkeypatch
):
    """`_run`'s environment is asserted; `_capture` builds its own and nothing
    read it, so it could have inherited the caller's."""
    monkeypatch.setenv("FAKE_TOFU_STDOUT", "{}")
    tofu.outputs(workdir)
    (env,) = [c["env"] for c in calls(fake_tofu)]
    assert env["CHECKPOINT_DISABLE"] == "1"
    assert env["TF_IN_AUTOMATION"] == "1"


def test_version_asked_inside_a_directory_asks_that_directory(
    fake_tofu, workdir, monkeypatch
):
    """`provider_selections` is empty unless the question is asked inside an
    initialised directory, which is the only reason `version` takes one."""
    monkeypatch.setenv("FAKE_TOFU_STDOUT", "{}")
    tofu.version(workdir)
    (argv,) = [c["argv"] for c in calls(fake_tofu)]
    assert f"-chdir={workdir}" in argv
