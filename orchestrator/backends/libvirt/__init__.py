"""The libvirt backend: six methods, bound together.

Two of them delegate to the free functions in ``schema.py``, which imports
nothing hypervisor-specific. The four that hold a connection live in
``preflight.py``, ``destroy.py`` and ``create.py``. There is no ``prepare``
here: it built the seed ISOs through core's ``cloudinit`` and carried
``preflight``'s ``base_volume`` through to ``create``, which is what the
inherited ``Backend.prepare`` now does for every backend.

**No libvirt import at module level, here or in any module this one imports at
import time.** ``orchestrator/backends/__init__.py`` names this class, so importing
the registry drags this file in on every run -- including runs on a machine with no
libvirt at all. ``tests/test_seam.py`` breaks the import and checks exactly that.
The hypervisor import lives inside the methods that need a connection.

The class arrives here rather than in a submodule for the reason findings.md §3
wants an ABC in the first place (D28): the registry names one object, and every
method core calls is on it.
"""

from __future__ import annotations

from typing import Any

from ...problems import Problem
from ..base import (
    Backend,
    Discovered,
    Existing,
    Outcome,
)
from . import create as _create
from . import destroy as _destroy
from . import preflight as _preflight
from . import render as _render
from . import schema as _schema


class LibvirtBackend(Backend):
    # -- offline ---------------------------------------------------------

    def config_schema(self) -> dict:
        return _schema.TARGET_SCHEMA

    def validate(self, cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
        return _schema.validate(cfg, verify_digest=verify_digest)

    # -- connected -------------------------------------------------------

    def connect(self, cfg: dict) -> Any:
        return _preflight.connect(cfg)

    def preflight(self, cfg: dict, session: Any) -> Discovered:
        return _preflight.preflight(cfg, session)

    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
        return _destroy.destroy(cfg, session, targets)

    # -- apply -----------------------------------------------------------

    def create(self, cfg: dict, session: Any, prepared: dict[str, Any]) -> dict:
        """Render the values, then make the objects they describe.

        ``render`` stays a step of its own now that nothing consumes its output
        but this line: it is the pure config-to-values half, golden-file tested
        byte for byte, and keeping it separate is what lets ``create`` be tested
        against a dict rather than against a config.
        """
        return _create.create(session, _render.render(cfg, prepared))
