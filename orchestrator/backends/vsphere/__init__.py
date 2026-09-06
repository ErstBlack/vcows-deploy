"""The vSphere backend: six methods, one of them not written yet.

Two delegate to free functions in ``schema.py``, which imports nothing
hypervisor-specific. ``connect`` and the lookups live in ``api.py``, the one
module that reaches vCenter, and ``preflight`` and ``destroy`` drive their
phases through them. ``create`` raises ``NotImplementedError`` here and gains
its module in the chunk that writes it.

**This package is deliberately not in ``orchestrator/backends/__init__.py``'s
``REGISTRY``** until the last of those chunks lands, so no config can name a
backend that is half built. Core takes a registry argument everywhere -- the
tests build their own dict and compose the core schema from it, which is the
whole of what registration would add.

**No ``pyVmomi`` import at module level, here or in any module this one imports
at import time.** The same rule the Proxmox backend follows for ``proxmoxer``
and the libvirt backend for ``libvirt``, and for the same reason: once the
registry names this class, importing the registry drags this file in on every
run, including runs that will never speak to a vCenter. ``api.py`` imports
``pyVim.connect`` inside the function that needs it.
"""

from __future__ import annotations

from typing import Any

from ...problems import Problem
from ..base import Backend, Discovered, Existing, Outcome
from . import api as _api
from . import destroy as _destroy
from . import preflight as _preflight
from . import schema as _schema


class VsphereBackend(Backend):
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
        raise NotImplementedError("the vSphere create chunk has not landed")
