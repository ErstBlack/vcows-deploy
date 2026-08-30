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

from tests.conftest import GATES, demanded

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


def test_every_skip_goes_through_the_gate_mechanism():
    """A skip conftest cannot see is a test that will never be made to run."""
    banned = {"pytest.skip", "pytest.importorskip", "pytest.mark.skip"}
    found = []
    for path in _sources():
        tree = ast.parse(path.read_text())
        for call in _calls(tree):
            name = _dotted(call.func)
            if name in banned:
                found.append(f"{path.name}:{call.lineno}: {name}")
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
    strip, so `tofu, image` demands `tofu` and a gate named " image" that does
    not exist. Both CI files are written without spaces because of this."""
    assert isinstance(GATES, set)
