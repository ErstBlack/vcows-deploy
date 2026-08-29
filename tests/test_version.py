"""Version coherence.

The four-digit Major.Minor.Patch.Hotfix format is non-negotiable, and several
artefacts carry it. They are asserted to agree here so they cannot drift apart
silently -- a marker claiming one version while the image tag claims another is
the kind of thing nobody notices until an upgrade goes wrong.

The consumers that do not exist yet (image tag, OCI label, build manifest) are
picked up as their stages land.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from orchestrator import VERSION
from orchestrator.marker import Marker

ROOT = Path(__file__).resolve().parent.parent

FOUR_DIGIT = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def test_format_is_four_digit():
    assert FOUR_DIGIT.match(VERSION), f"{VERSION!r} is not Major.Minor.Patch.Hotfix"


def test_marker_carries_the_version():
    assert Marker.for_vm("app01", "lab-a").v == VERSION


def test_pyproject_agrees():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["version"] == VERSION
