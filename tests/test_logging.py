"""The log, and the line it draws against the printout.

#136's split: the printout carries the headline, the log carries the detail. The
assertions here are about *what would otherwise be destroyed* -- a diagnostic's
multi-line ``detail``, a libvirt error message, the argv actually executed. What
they deliberately do not assert is any `Problem`, `Decision` or `Outcome` being
logged: each of those is already printed where it arrives and recorded in
`run.json`, and repeating one here would make the logger the fifth result carrier
findings.md §3 refuses.

Nothing in this file needs a hypervisor, OpenTofu, or a gate. The one test that
runs a tofu command uses `test_tofu_driver`'s fake binary, for the reason that
file's docstring gives.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from orchestrator import cli, tofu
from orchestrator.backends.libvirt import destroy, preflight

from .test_tofu_driver import DIAGNOSTIC, FAKE


@pytest.fixture
def fake_tofu(tmp_path, monkeypatch):
    """`test_tofu_driver`'s fake, without importing its fixture into this scope."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    binary = bindir / "tofu"
    binary.write_text(FAKE)
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_TOFU_LOG", str(tmp_path / "calls.jsonl"))
    return tmp_path


# -- #136: the detail that `__str__` drops ---------------------------------


def test_a_diagnostics_detail_reaches_the_log(fake_tofu, tmp_path, monkeypatch, caplog):
    """The filed case. `summary` is the headline; `detail` is the part that says
    why, and until now it was populated on every run and read by nothing."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setenv("FAKE_TOFU_STREAM", DIAGNOSTIC)
    with caplog.at_level(logging.INFO, logger="orchestrator.tofu"):
        tofu.plan(workdir, workdir / "plan.bin")

    logged = "\n".join(r.getMessage() for r in caplog.records)
    # The headline, which `str(Diagnostic)` already carried...
    assert "Volume Creation Failed" in logged
    # ...and the detail, which it did not.
    assert "storage volume 'app01.qcow2' exists already" in logged


def test_the_record_keeps_its_shape_while_the_log_carries_the_detail():
    """#89's RX-B6: a diagnostic message must not widen a typed record field.

    `_note_warnings` stays `list[str]` and `Diagnostic.__str__` stays one line.
    The detail goes to the log instead of into the record, which is what makes
    this fix additive rather than a type change.
    """
    d = tofu.Diagnostic(
        severity="warning",
        summary="headline",
        detail="line one\nline two",
        address="libvirt_domain.vm",
    )
    assert str(d) == "warning [libvirt_domain.vm]: headline"
    assert "\n" not in str(d)

    # `path` is never touched by `_note_warnings`; nothing is written here.
    run = cli._Run(path=Path("/nonexistent"), command="deploy", cfg={}, started="now")
    run.extra["tofu_warnings"] = []
    cli._note_warnings(run, tofu.Result(0, (d,)))
    assert run.extra["tofu_warnings"] == ["warning [libvirt_domain.vm]: headline"]
    assert all(isinstance(w, str) for w in run.extra["tofu_warnings"])


def test_the_argv_is_recorded(fake_tofu, tmp_path, caplog):
    """What was actually executed. The -no-color decision and the resolved
    -chdir are computed in `_run`, so the config does not say what tofu was given."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    with caplog.at_level(logging.INFO, logger="orchestrator.tofu"):
        tofu.init(workdir)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert f"-chdir={workdir}" in logged
    assert "-input=false" in logged


# -- the streams and markers that go missing quietly -----------------------


def test_an_unreadable_stream_is_logged_and_still_not_fatal(tmp_path, caplog):
    """The exit code stays the authority -- but "not fatal" and "not worth
    saying" are different, and every diagnostic for the step is gone."""
    missing = tmp_path / "never-written.json"
    with caplog.at_level(logging.INFO, logger="orchestrator.tofu"):
        assert tofu._read_stream(missing) == ((), {})
    assert "no diagnostics" in caplog.text
    assert str(missing) in caplog.text


def test_a_line_that_is_not_json_is_logged_at_debug(tmp_path, caplog):
    stream = tmp_path / "plan.json"
    stream.write_text("not json at all\n" + DIAGNOSTIC + "\n")
    with caplog.at_level(logging.DEBUG, logger="orchestrator.tofu"):
        diagnostics, _ = tofu._read_stream(stream)
    assert len(diagnostics) == 1
    assert "not JSON" in caplog.text


def test_a_damaged_marker_is_logged_and_still_read_as_unmarked(caplog):
    """D12 stands -- unparseable is unmarked, which is the safe direction. The
    log is what distinguishes it from a domain that carries no marker at all."""
    root = ET.fromstring(
        "<domain><metadata>"
        f'<vcows:vcows xmlns:vcows="{preflight.MARKER_XMLNS}">'
        "{not valid json"
        "</vcows:vcows>"
        "</metadata></domain>"
    )
    with caplog.at_level(logging.INFO, logger=preflight.__name__):
        assert preflight.marker_of(root) is None
    assert "damaged marker" in caplog.text


def test_an_absent_marker_says_nothing(caplog):
    """The other half of the pair: no marker is the ordinary case on any host
    with domains that are not ours, and it must not produce a line per domain."""
    root = ET.fromstring("<domain><metadata/></domain>")
    with caplog.at_level(logging.DEBUG, logger=preflight.__name__):
        assert preflight.marker_of(root) is None
    assert caplog.text == ""


# -- libvirt's chatter, routed rather than discarded -----------------------


def test_libvirt_errors_are_logged_rather_than_destroyed(caplog):
    """`registerErrorHandler` was `lambda _ctx, _err: None`. Keeping the chatter
    off stderr is the point; destroying it was incidental. Asserted against the
    handler directly, which needs no hypervisor."""
    err = (38, 7, "Cannot recv data", 2, "", "", "", -1, -1)
    with caplog.at_level(logging.DEBUG, logger=preflight.__name__):
        preflight._chatter(None, err)
    assert "Cannot recv data" in caplog.text


def test_the_error_handler_survives_a_shape_it_does_not_expect(caplog):
    """It runs inside libvirt's own callback, where an IndexError would surface
    as something far stranger than a missing log line."""
    with caplog.at_level(logging.DEBUG, logger=preflight.__name__):
        preflight._chatter(None, None)
        preflight._chatter(None, ("short",))
    assert caplog.text  # logged whole rather than guessed at, and did not raise


# -- the level, and the knob that sets it ----------------------------------


def test_the_default_is_info(monkeypatch):
    """Not WARNING. The purpose is traceability *after* delivery, and `destroy`
    cannot be re-run to recover what was not recorded the first time."""
    monkeypatch.delenv("VCOWS_LOG_LEVEL", raising=False)
    assert cli._log_level() == "INFO"


@pytest.mark.parametrize("given, wanted", [("debug", "DEBUG"), ("WARNING", "WARNING")])
def test_the_level_is_taken_from_the_environment(monkeypatch, given, wanted):
    monkeypatch.setenv("VCOWS_LOG_LEVEL", given)
    assert cli._log_level() == wanted


def test_an_unusable_level_is_reported_and_ignored(monkeypatch, capsys):
    """`basicConfig` raises ValueError on an unknown level, which would turn a
    typo in an environment variable into a run that does not start. Same shape
    and the same voice as `schema._ceiling`."""
    monkeypatch.setenv("VCOWS_LOG_LEVEL", "chatty")
    assert cli._log_level() == "INFO"
    assert "ignoring VCOWS_LOG_LEVEL='chatty'" in capsys.readouterr().err


def test_configuring_twice_rebinds_the_stream(monkeypatch, capsys):
    """`force=True` is not decorative: `basicConfig` is a no-op when the root
    logger already has handlers, so without it the first caller in a process
    binds the handler to a stream every later one keeps writing to."""
    monkeypatch.delenv("VCOWS_LOG_LEVEL", raising=False)
    cli._logging()
    cli._logging()
    capsys.readouterr()
    logging.getLogger("orchestrator.cli").info("after the second configure")
    assert "after the second configure" in capsys.readouterr().err


def test_the_timestamp_is_utc(monkeypatch):
    """The format ends in `Z`. `asctime` is localtime unless the converter says
    otherwise, and a site in another timezone would read a stamp that disagrees
    with the run directory's name."""
    monkeypatch.delenv("VCOWS_LOG_LEVEL", raising=False)
    cli._logging()
    import time

    assert logging.Formatter.converter is time.gmtime


def test_nothing_is_logged_above_info():
    """Some tests exercise these modules directly rather than through `main`,
    where no handler is configured and `logging.lastResort` writes WARNING+ to
    the real stderr, escaping capsys. Keeping every statement at INFO or below
    makes that unreachable -- this is the gate on that, not a style note."""
    import ast

    banned = {"warning", "warn", "error", "exception", "critical", "fatal"}
    offenders = []
    root = Path(__file__).resolve().parent.parent
    sources = [*(root / "orchestrator").rglob("*.py"), root / "container/entrypoint.py"]
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in banned
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "log"
            ):
                offenders.append(f"{path.relative_to(root)}: log.{node.func.attr}")
    assert offenders == []


def test_a_pool_that_answers_without_a_target_path_is_logged(caplog):
    """`_pool_holds` returns None for "could not tell", which is a different
    answer from the empty list and must not collapse into it. Both routes to that
    None say why, because the return value cannot."""

    class Pool:
        def XMLDesc(self, _flags):  # libvirt's own spelling
            return "<pool><target/></pool>"

    with caplog.at_level(logging.DEBUG, logger=destroy.__name__):
        assert destroy._pool_holds(Pool(), {"/pool/app01.qcow2"}) is None
    assert "no <target><path>" in caplog.text
