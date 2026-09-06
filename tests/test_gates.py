"""The gates, checked as a set rather than one at a time.

`conftest.py` makes a skip demandable: `VCOWS_GATES=<name>` turns it into a
failure carrying its reason. That only works for skips that go *through* the
mechanism. A bare `pytest.skip` or `pytest.importorskip` anywhere in the suite is
invisible to it, and no environment variable can ever make it fail -- so it
passes, quietly, forever: not a gate that is wrong, a gate that never ran.

These two tests are the thing that notices.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from tests import conftest
from tests.conftest import demanded, gate, pytest_runtest_setup, require

TESTS = Path(__file__).resolve().parent

#: Every gate name the suite is allowed to use. Adding one here is deliberate;
#: `demanded()` matches on these strings and silently ignores anything else, so
#: a typo in a `require()` call would otherwise create a gate nobody can demand.
KNOWN = {
    "image",
    "rig",
    "pycdlib",
    "libvirt",
    "smoke",
    "proxmox",
    "vsphere",
    "vcsim",
}

#: `conftest.py` is where the mechanism is implemented, so it is the one file
#: allowed to call pytest's skip machinery directly.
IMPLEMENTATION = {"conftest.py"}


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _references(tree: ast.AST) -> list[tuple[str, int]]:
    """Every dotted name used as a call, plus every dotted name used at all.

    Calls alone miss the *uncalled* spellings, and they are the ones that get
    written: `pytestmark = pytest.mark.skip` and `pytest.param(1,
    marks=pytest.mark.skip)` are both bare `ast.Attribute` nodes the call walk
    never sees. Adding `(reason=...)` to either turns it into an `ast.Call`, so a
    call-only scan catches one spelling of an edit and not the other.

    `from pytest import skip as _s` binds a name with no dotted path at all, so
    the import itself is collected under the imported name.
    """
    found = [(_dotted(c.func), c.lineno) for c in _calls(tree)]
    # Outermost only: `pytest.skip.Exception` is a legitimate reference to the
    # exception type, and its inner `pytest.skip` is not a call to it. Collecting
    # every Attribute flags this file's own two `pytest.skip.Exception`
    # references and nothing else.
    inner = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and id(node) not in inner:
            found.append((_dotted(node), node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            found += [(a.name, node.lineno) for a in node.names]
    return sorted(set(found))


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


#: `skipif` is here because it is the exact idiom `gate()` in `conftest.py`
#: itself returns, so it is the form a developer copying house style writes --
#: and one written by hand goes straight past the mechanism. `xfail` is here
#: because it is worse than a skip: the test runs, fails, and reports green.
#: Trailing attribute paths, not full dotted names: `import pytest as _pt` and
#: `raise unittest.SkipTest(...)` both reach the same behaviour under a spelling
#: no `pytest.`-prefixed literal matches.
BANNED = {
    "skip",
    "importorskip",
    "xfail",
    "mark.skip",
    "mark.skipif",
    "mark.xfail",
    "SkipTest",
}


def _is_banned(name: str) -> bool:
    """Match the trailing attribute path, so `import pytest as _pt` is no escape."""
    parts = name.split(".")
    return any(".".join(parts[i:]) in BANNED for i in range(len(parts)))


def test_every_skip_goes_through_the_gate_mechanism():
    """A skip conftest cannot see is a test that will never be made to run."""
    found = [
        f"{path.name}:{lineno}: {name}"
        for path in _sources()
        for name, lineno in _references(ast.parse(path.read_text()))
        if _is_banned(name)
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


def test_gates_is_parsed_without_whitespace_stripping():
    """Documented rather than fixed: `VCOWS_GATES` splits on `,` and does not
    strip, so `rig, image` demands `rig` and a gate named " image" that does
    not exist. Both CI files are written without spaces because of this.

    This performs the parse rather than monkeypatching `GATES` past it. Every
    other test in this file replaces `GATES` wholesale, so nothing else executes
    the env-var -> set step -- the one line every gate name in the suite travels
    through -- and a `GATES = set()` that stops reading the environment silences
    every name at once with the whole suite green.
    """
    assert conftest._parse("rig, image") == {"rig", " image"}
    assert conftest._parse("") == set()
    # The tie between the constant and the function that builds it. `GATES` is
    # read at import, so this is only load-bearing where VCOWS_GATES is actually
    # set -- CI's `all` job -- and that is exactly where a decoupled GATES does
    # harm. With nothing demanded there is nothing to see.
    assert conftest.GATES == conftest._parse(os.environ.get("VCOWS_GATES", ""))


# -- the mechanism itself ---------------------------------------------------
# Everything above scans the suite for skips that bypass the mechanism. These
# check the mechanism, which is a different thing: `gate()` and `require()` each
# have two branches and a green run only ever takes the "available" one. Mutating
# both to always skip leaves `369 passed, exit 0` under default and
# VCOWS_GATES=all alike, with `all` silently meaning nothing.
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
    mark = gate("rig", True, REASON).mark
    assert mark.name == "skipif"
    assert mark.args == (False,)


def test_a_demanded_gate_that_is_missing_carries_its_reason_to_the_hook(monkeypatch):
    """This is the branch VCOWS_GATES exists for, and the one nothing took."""
    monkeypatch.setattr("tests.conftest.GATES", {"rig"})
    mark = gate("rig", False, REASON).mark
    assert mark.name == "gate_missing"
    assert mark.args == (REASON,)


def test_an_undemanded_gate_that_is_missing_is_an_ordinary_skip(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", set())
    mark = gate("rig", False, REASON).mark
    assert mark.name == "skip"
    assert mark.kwargs["reason"] == REASON


def test_require_returns_when_the_dependency_is_there(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", {"rig"})
    assert require("rig", True, REASON) is None


def test_a_demanded_require_that_is_missing_fails(monkeypatch):
    """A `Skipped` raised inside `pytest.raises(pytest.fail.Exception)`
    propagates and skips the *enclosing* test, so with `require()`'s demanded
    branch gone this test would convert itself from a failure into a skip and the
    run would stay exit 0 -- the shape conftest.py's module docstring exists to
    prevent, in the file that enforces it."""
    monkeypatch.setattr("tests.conftest.GATES", {"rig"})
    try:
        with pytest.raises(pytest.fail.Exception, match=REASON):
            require("rig", False, REASON)
    except pytest.skip.Exception:  # pragma: no cover -- this is the assertion
        raise AssertionError(
            "require() skipped where it was demanded to fail"
        ) from None


def test_an_undemanded_require_that_is_missing_skips(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", set())
    with pytest.raises(pytest.skip.Exception, match=REASON):
        require("rig", False, REASON)


def test_the_hook_turns_a_gate_missing_mark_into_a_failure(monkeypatch):
    """`gate()` can only return a mark; this is the half that acts on it."""
    monkeypatch.setattr("tests.conftest.GATES", {"rig"})
    with pytest.raises(pytest.fail.Exception, match=REASON):
        pytest_runtest_setup(_Item(gate("rig", False, REASON).mark))


def test_the_hook_leaves_every_other_test_alone():
    assert pytest_runtest_setup(_Item()) is None
