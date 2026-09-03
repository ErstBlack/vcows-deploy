"""Version coherence.

The four-digit Major.Minor.Patch.Hotfix format is non-negotiable, and several
artefacts carry it. Each is asserted against ``orchestrator.VERSION``, because a
marker claiming one version while the image tag claims another is the kind of
thing nobody notices until an upgrade goes wrong.

Not all of them are asserted *here*. ``orchestrator/__init__.py`` lists all seven
consumers and where each is checked; this file covers the two that are read out
of files on disk rather than produced by running code, and the OCI label and the
build manifest are `test_image.py`'s, behind the image gate.
"""

from __future__ import annotations

import re
import tomllib

from orchestrator import VERSION
from orchestrator.marker import Marker
from tests.conftest import REPO

FOUR_DIGIT = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def test_format_is_four_digit():
    assert FOUR_DIGIT.match(VERSION), f"{VERSION!r} is not Major.Minor.Patch.Hotfix"


def test_marker_carries_the_version():
    assert Marker.for_vm("app01", "lab-a").v == VERSION


def test_pyproject_agrees():
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert data["project"]["version"] == VERSION


def test_the_image_tag_agrees():
    """The consumer the docstring above claims and nothing asserted.

    `ARG VCOWS_VERSION` is what names the image, what reaches the OCI version
    label, and what the build manifest records -- so a bump here that missed the
    Containerfile would ship an image whose tag, label and manifest all disagreed
    with the marker inside every VM it created.
    """
    text = (REPO / "Containerfile").read_text()
    found = re.search(r"^ARG VCOWS_VERSION=(\S+)$", text, re.MULTILINE)
    assert found is not None, "Containerfile no longer declares ARG VCOWS_VERSION"
    assert found.group(1) == VERSION
