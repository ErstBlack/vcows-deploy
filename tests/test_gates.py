"""The gates, checked as a set rather than one at a time.

`conftest.py` makes a skip demandable: `VCOWS_GATES=<name>` turns it into a
failure carrying its reason. That only works for skips that go *through* the
mechanism. A bare `pytest.skip` or `pytest.importorskip` anywhere in the suite is
invisible to it, and no environment variable can ever make it fail -- so it would
pass, quietly, forever. That is the shape of the finding already recorded as
F-TEETH-05, one level up: not a gate that is wrong, a gate that never ran.

These two tests are the thing that notices.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.conftest import demanded, gate, pytest_runtest_setup, require

TESTS = Path(__file__).resolve().parent

#: Every gate name the suite is allowed to use. Adding one here is deliberate;
#: `demanded()` matches on these strings and silently ignores anything else, so
#: a typo in a `require()` call would otherwise create a gate nobody can demand.
KNOWN = {"tofu", "image", "rig", "pycdlib", "libvirt"}

#: `conftest.py` is where the mechanism is implemented, so it is the one file
#: allowed to call pytest's skip machinery directly.
IMPLEMENTATION = {"conftest.py"}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _references(tree: ast.AST) -> list[tuple[str, int]]:
    """Every dotted name used as a call *or* as a bare decorator.

    Calls alone miss `@pytest.mark.skip`, which takes no arguments and so is an
    `ast.Attribute` the call walk never sees at all.
    """
    found = [(_dotted(c.func), c.lineno) for c in _calls(tree)]
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    for node in ast.walk(tree):
        if not isinstance(node, defs):
            continue
        found += [
            (_dotted(d), d.lineno)
            for d in node.decorator_list
            if not isinstance(d, ast.Call)
        ]
    return found


def _dotted(node: ast.expr) -> str:
    """`pytest.mark.skipif` from the AST, or "" for anything not a dotted name."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _sources() -> list[Path]:
    return sorted(p for p in TESTS.glob("*.py") if p.name not in IMPLEMENTATION)


#: `skipif` is here because it is the exact idiom `gate()` itself returns
#: (conftest.py:53), so it is the form a developer copying house style writes --
#: and one written by hand goes straight past the mechanism. `xfail` is here
#: because it is worse than a skip: the test runs, fails, and reports green.
BANNED = {
    "pytest.skip",
    "pytest.importorskip",
    "pytest.xfail",
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
}


def test_every_skip_goes_through_the_gate_mechanism():
    """A skip conftest cannot see is a test that will never be made to run."""
    found = [
        f"{path.name}:{lineno}: {name}"
        for path in _sources()
        for name, lineno in _references(ast.parse(path.read_text()))
        if name in BANNED
    ]
    assert not found, (
        "these skips bypass conftest's gate mechanism, so no VCOWS_GATES value "
        "can ever turn them into a failure -- route them through gate() or "
        "require() instead:\n  " + "\n  ".join(found)
    )


def test_gate_names_are_ones_that_can_be_demanded():
    """`demanded()` matches literal strings, so a typo makes an inert gate."""
    used = set()
    for path in [*_sources(), TESTS / "conftest.py"]:
        tree = ast.parse(path.read_text())
        for call in _calls(tree):
            if _dotted(call.func) not in {"gate", "require"}:
                continue
            first = call.args[0] if call.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                used.add(first.value)
    unknown = used - KNOWN
    assert not unknown, (
        f"gate name(s) {sorted(unknown)} are not in KNOWN, so nothing documents "
        "them and a reader cannot know what to set VCOWS_GATES to"
    )


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_every_known_gate_is_demandable(name: str, monkeypatch):
    """`all` covers every gate, and each name covers itself and nothing else."""
    monkeypatch.setattr("tests.conftest.GATES", {name})
    assert demanded(name)
    monkeypatch.setattr("tests.conftest.GATES", {"all"})
    assert demanded(name)
    monkeypatch.setattr("tests.conftest.GATES", {"some-other-gate"})
    assert not demanded(name)


def test_gates_is_parsed_without_whitespace_stripping(monkeypatch):
    """Documented rather than fixed: `VCOWS_GATES` splits on `,` and does not
    strip, so `tofu, image` demands `tofu` and a gate named " image" that does
    not exist. Both CI files are written without spaces because of this."""
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    assert demanded("tofu")
    assert not demanded(" tofu")
    # The other half of the same fact, from the parsing side: this is the set
    # `VCOWS_GATES="tofu, image"` produces, and `image` is not in it.
    monkeypatch.setattr("tests.conftest.GATES", {"tofu", " image"})
    assert not demanded("image")


# -- the mechanism itself ---------------------------------------------------
# Everything above scans the suite for skips that bypass the mechanism. These
# check the mechanism, which is a different thing and was the untested half:
# `gate()` and `require()` each have two branches and a green run only ever takes
# the "available" one, because `just test-tofu` sets VCOWS_GATES=tofu on a runner
# that has tofu. Mutating both to always skip left `369 passed, exit 0` under
# default, VCOWS_GATES=tofu and VCOWS_GATES=all alike -- `all` had silently
# stopped meaning anything, and nothing here noticed.
#
# `GATES` is read at import time and `gate()` calls `demanded()` when it builds
# the mark, so monkeypatching it reaches these direct calls and not the marks the
# suite already applied at its own import.

REASON = "needs a thing this runner does not have"


class _Item:
    """The one thing `pytest_runtest_setup` asks of the item it is handed."""

    def __init__(self, *marks):
        self._marks = marks

    def iter_markers(self, name: str) -> list:
        return [m for m in self._marks if m.name == name]


def test_an_available_gate_is_a_skipif_that_does_not_skip():
    mark = gate("tofu", True, REASON).mark
    assert mark.name == "skipif"
    assert mark.args == (False,)


def test_a_demanded_gate_that_is_missing_carries_its_reason_to_the_hook(monkeypatch):
    """This is the branch VCOWS_GATES exists for, and the one nothing took."""
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    mark = gate("tofu", False, REASON).mark
    assert mark.name == "gate_missing"
    assert mark.args == (REASON,)


def test_an_undemanded_gate_that_is_missing_is_an_ordinary_skip(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", set())
    mark = gate("tofu", False, REASON).mark
    assert mark.name == "skip"
    assert mark.kwargs["reason"] == REASON


def test_require_returns_when_the_dependency_is_there(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    assert require("tofu", True, REASON) is None


def test_a_demanded_require_that_is_missing_fails(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    with pytest.raises(pytest.fail.Exception, match=REASON):
        require("tofu", False, REASON)


def test_an_undemanded_require_that_is_missing_skips(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", set())
    with pytest.raises(pytest.skip.Exception, match=REASON):
        require("tofu", False, REASON)


def test_the_hook_turns_a_gate_missing_mark_into_a_failure(monkeypatch):
    """`gate()` can only return a mark; this is the half that acts on it."""
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    with pytest.raises(pytest.fail.Exception, match=REASON):
        pytest_runtest_setup(_Item(gate("tofu", False, REASON).mark))


def test_the_hook_leaves_every_other_test_alone():
    assert pytest_runtest_setup(_Item()) is None
