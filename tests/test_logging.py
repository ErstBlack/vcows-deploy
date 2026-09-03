"""The log, and the line it draws against the printout.

#136's split: the printout carries the headline, the log carries the detail. The
assertions here are about *what would otherwise be destroyed* -- a libvirt error
message, a damaged marker. What they deliberately do not assert is any
`Problem`, `Decision` or `Outcome` being
logged: each of those is already printed where it arrives and recorded in
`run.json`, and repeating one here would make the logger the fifth result carrier
findings.md §3 refuses.

Nothing in this file needs a hypervisor or a gate.
"""

from __future__ import annotations

import io
import logging
import re
import sys
import time
from xml.etree import ElementTree as ET

import pytest

import orchestrator
from orchestrator import cli, limits
from orchestrator.backends.libvirt import destroy, preflight
from tests.conftest import REPO

# -- the streams and markers that go missing quietly -----------------------


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
    cannot be re-run to recover what was not captured the first time."""
    monkeypatch.delenv("VCOWS_LOG_LEVEL", raising=False)
    assert orchestrator._log_level() == ("INFO", None)


@pytest.mark.parametrize("given, wanted", [("debug", "DEBUG"), ("WARNING", "WARNING")])
def test_the_level_is_taken_from_the_environment(monkeypatch, given, wanted):
    monkeypatch.setenv("VCOWS_LOG_LEVEL", given)
    assert orchestrator._log_level() == (wanted, None)


def test_an_unusable_level_falls_back_without_raising(monkeypatch):
    """`basicConfig` raises ValueError on an unknown level, which would turn a
    typo in an environment variable into a run that does not start.

    The second half of the tuple is the rejected value, carried back out so the
    variable is read and checked once rather than twice. `configure_logging`
    returns it, which is what the module-level warning at import reports.
    """
    monkeypatch.setenv("VCOWS_LOG_LEVEL", "chatty")
    assert orchestrator._log_level() == ("INFO", "chatty")
    assert orchestrator.configure_logging() == "chatty"  # must not raise


def test_quiet_mode_drops_the_report_and_keeps_the_problems(monkeypatch, capsys):
    """`VCOWS_LOG_LEVEL=WARNING` is the quiet mode that falls out of putting the
    report at INFO. It is the one setting at which output is not identical."""
    monkeypatch.setenv("VCOWS_LOG_LEVEL", "WARNING")
    orchestrator.configure_logging()
    try:
        logging.getLogger("orchestrator.cli").info("a report row")
        logging.getLogger("orchestrator.cli").warning("a problem")
        err = capsys.readouterr().err
        assert "a report row" not in err
        assert "a problem" in err
    finally:
        monkeypatch.delenv("VCOWS_LOG_LEVEL")
        orchestrator.configure_logging()


# -- the shape of a line ---------------------------------------------------


def test_every_line_carries_a_level_and_a_logger(capsys, monkeypatch):
    """The whole point of the migration: one channel, and the level is what
    tells the operator which kind of line they are looking at.

    Emitted from `limits._ceiling` rather than from this file, because
    `%(module)s` names the file the call came from: a line this module logged
    through a package logger would read `test_logging`, which is true and tells
    nothing about the shape a real run writes.
    """
    monkeypatch.setenv("VCOWS_MAX_VCPUS", "0")
    limits._ceiling("VCOWS_MAX_VCPUS", 512)
    line = capsys.readouterr().err.strip()
    assert "WARNING" in line
    # The module, not the dotted path: `orchestrator.` on every line is shared
    # prefix that says nothing, and being variable width it moves the message
    # column around and defeats the padding.
    assert "limits" in line and "orchestrator.limits" not in line
    assert line.endswith("Using 512.")
    # ISO-8601 UTC with milliseconds -- a preflight puts four lines in one second.
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z ", line), line


def test_the_timestamp_is_utc():
    """`asctime` is localtime unless the converter says so, and a site in
    another timezone would read a stamp that disagrees with its run directory."""
    orchestrator.configure_logging()
    assert logging.Formatter.converter is time.gmtime


def test_levels_line_up_in_the_gutter(capsys):
    """`%(levelname)-7s` is padded so the message column starts at the same
    offset regardless of level -- which is what keeps `_row`'s table aligned."""
    logging.getLogger("orchestrator.cli").info("first")
    logging.getLogger("orchestrator.cli").warning("second")
    a, b = capsys.readouterr().err.splitlines()
    assert a.index("first") == b.index("second"), (a, b)


def test_the_handler_follows_a_replaced_stream(monkeypatch):
    """The handler resolves `sys.stderr` when it writes, not when it is built.

    Deliberately does **not** call `configure_logging` after replacing the
    stream: doing so rebuilds the handler against the new one and the test would
    pass whether or not the property exists. Configured at package import, a
    bound handler holds the real stderr and writes straight past `capsys` --
    measured at 39 failing tests before this property existed.
    """
    handler = orchestrator._Stderr()  # built against the current sys.stderr
    replacement = io.StringIO()
    monkeypatch.setattr(sys, "stderr", replacement)

    handler.emit(
        logging.LogRecord("t", logging.WARNING, __file__, 0, "after the swap", (), None)
    )
    assert "after the swap" in replacement.getvalue()


def test_configuring_twice_does_not_double_a_line(capsys):
    """Handlers are replaced, not appended. Left to accumulate, every line would
    be emitted once per call."""
    orchestrator.configure_logging()
    orchestrator.configure_logging()
    capsys.readouterr()
    logging.getLogger("orchestrator.cli").warning("once")
    assert capsys.readouterr().err.count("once") == 1


def test_configure_logging_installs_the_format_rather_than_inheriting_one(
    capsys, monkeypatch
):
    """The whole line shape, asserted after an explicit call.

    Every other assertion in this file reads a line produced by the handler the
    *package import* installed, so nothing covered `configure_logging` putting the
    formatter on. Replace it with the default one and the timestamp, the level and
    the module column all vanish while the rest of the file still passes.
    """
    orchestrator.configure_logging()
    capsys.readouterr()
    monkeypatch.setenv("VCOWS_MAX_VCPUS", "0")
    limits._ceiling("VCOWS_MAX_VCPUS", 512)
    line = capsys.readouterr().err.strip()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z WARNING\s+limits\s+"
        r"ignoring VCOWS_MAX_VCPUS='0': not a positive integer\. Using 512\.",
        line,
    ), line


# -- the one thing that is not a log line ----------------------------------


def test_the_confirm_prompt_is_bare_and_on_stdout(monkeypatch, capsys):
    """`input()` writes it with no trailing newline so the cursor stays where
    the operator types. Being the only unprefixed output vcows produces, it is
    trivially separable from the log -- which is why it is the exception."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        "builtins.input", lambda prompt: (print(prompt, end=""), "no")[1]
    )

    assert cli._confirm(2, "lab-a", yes=False) is False
    captured = capsys.readouterr()
    assert "type 'yes'" in captured.out
    assert "INFO" not in captured.out and "WARNING" not in captured.out
    assert captured.err == ""


def test_the_non_tty_refusal_is_a_log_line(monkeypatch, capsys):
    """`_confirm`'s other write is not interactive, so it is not an exception."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli._confirm(2, "lab-a", yes=False) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err and "pass --yes" in captured.err


# -- the import-time write that forced the __init__ move -------------------


def test_the_import_time_ceiling_warning_is_a_proper_log_line(monkeypatch, capsys):
    """`limits._ceiling` runs while `VM_SCHEMA` is being built, before `main()`.
    This is the assertion that fails if logging configuration moves back out of
    `orchestrator/__init__.py`: the record would reach `logging.lastResort`,
    which writes to stderr unprefixed and ignores VCOWS_LOG_LEVEL."""
    import importlib

    monkeypatch.setenv("VCOWS_MAX_VCPUS", "lots")
    try:
        importlib.reload(limits)
        err = capsys.readouterr().err
        assert "VCOWS_MAX_VCPUS='lots'" in err
        assert "WARNING" in err, "reached lastResort instead of our handler"
        assert "limits" in err
    finally:
        monkeypatch.delenv("VCOWS_MAX_VCPUS")
        importlib.reload(limits)


# -- the gate that keeps it that way ---------------------------------------


def test_nothing_prints(capsys):
    """No `print(` anywhere in the shipped code.

    With `_confirm`'s `input()` being the only sanctioned non-log output, and
    `input()` not being a `print`, this gate is exact -- there is no allow-list
    to drift. It replaces #143's `test_nothing_is_logged_above_info`, which
    forbade `log.warning`/`log.error`; those are now required everywhere, and
    the reason that gate existed (WARNING+ escaping `capsys` through
    `logging.lastResort`) is answered by configuring at package import.
    """
    import ast

    sources = [*(REPO / "orchestrator").rglob("*.py"), REPO / "container/entrypoint.py"]
    offenders = []
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
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


# -- what the messages themselves say --------------------------------------


def test_a_problem_without_a_where_renders_as_just_its_message(caplog):
    """Most problems are about the run rather than about a field. The absent
    `where` must be omitted, not rendered -- the first version bracketed it and
    produced a leading `" : "`.

    Asserted on the record's exact message rather than on a substring of the
    line: the first version of this test checked for `":  "` and passed against
    the very defect it was written for, which renders `" : "`.
    """
    from orchestrator.problems import Problem, Severity

    with caplog.at_level(logging.WARNING, logger="orchestrator.cli"):
        cli._problem(Problem(Severity.WARNING, "the pool went away"))
    (record,) = caplog.records
    assert record.getMessage() == "the pool went away"


def test_a_problem_with_a_where_names_it_first(caplog):
    from orchestrator.problems import Problem, Severity

    with caplog.at_level(logging.WARNING, logger="orchestrator.cli"):
        cli._problem(Problem(Severity.ERROR, "duplicate IP", "vms[0].nics[0]"))
    (record,) = caplog.records
    assert record.getMessage() == "[vms[0].nics[0]] duplicate IP"


def test_a_report_row_carries_no_trailing_padding(caplog):
    """`_row` pads to fixed columns; with an empty detail that padding would
    hang off the end of the record."""
    with caplog.at_level(logging.INFO, logger="orchestrator.cli"):
        logging.getLogger("orchestrator.cli").info(
            "%s", cli._row("app01", "destroy", "")
        )
    (record,) = caplog.records
    assert record.getMessage() == "app01                destroy"
