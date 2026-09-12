"""The libvirt backend: six methods, bound together.

Two of them delegate to the free functions in ``schema.py``, which imports
nothing hypervisor-specific. The four that hold a connection live in
``preflight.py``, ``destroy.py`` and ``create.py``. The three rules every
backend package follows are in ``orchestrator/backends/__init__.py``; the
inherited ``prepare`` carries ``preflight``'s ``base_volume`` through to
``create``.

The class is here rather than in a submodule for the reason findings.md §3
wants an ABC at all: the registry names one object, and every method core calls
is on it.
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
        return _create.create(session, _render.render(cfg, prepared))
