"""The Proxmox VE backend: six methods, bound together.

Two delegate to free functions in ``schema.py``, which imports nothing
hypervisor-specific. The four that hold a session live in ``api.py``,
``preflight.py``, ``create.py`` and ``destroy.py``. The three rules every
backend package follows are in ``orchestrator/backends/__init__.py``; the
inherited ``prepare`` carries ``preflight``'s ``image`` through to ``create``.

Nothing is held open while the image moves: it is uploaded over the same API
token rather than served over HTTP for PVE to pull.
"""

from __future__ import annotations

from typing import Any

from ...problems import Problem
from ..base import Backend, Discovered, Existing, Outcome
from . import api as _api
from . import create as _create
from . import destroy as _destroy
from . import preflight as _preflight
from . import render as _render
from . import schema as _schema


class ProxmoxBackend(Backend):
    # -- offline ---------------------------------------------------------

    def config_schema(self) -> dict:
        return _schema.TARGET_SCHEMA

    def validate(self, cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
        return _schema.validate(cfg, verify_digest=verify_digest)

    # -- connected -------------------------------------------------------

    def connect(self, cfg: dict) -> Any:
        return _api.connect(cfg)

    def preflight(self, cfg: dict, session: Any) -> Discovered:
        return _preflight.preflight(cfg, session)

    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
        return _destroy.destroy(cfg, session, targets)

    # -- apply -----------------------------------------------------------

    def create(self, cfg: dict, session: Any, prepared: dict[str, Any]) -> dict:
        return _create.create(session, _render.render(cfg, prepared))
