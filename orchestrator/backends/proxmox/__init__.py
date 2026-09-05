"""The Proxmox VE backend: six methods, bound together.

Two delegate to free functions in ``schema.py``, which imports nothing
hypervisor-specific. The four that hold a session live in ``api.py``,
``preflight.py``, ``create.py`` and ``destroy.py``.

**No ``proxmoxer`` import at module level, here or in any module this one imports
at import time.** ``orchestrator/backends/__init__.py`` names this class, so
importing the registry drags this file in on every run -- including runs that
only ever talk to libvirt. ``api.py`` imports ``proxmoxer`` inside the functions
that need it; ``tests/test_seam.py`` is the gate.

**Why there is no ``prepare`` here, in any form.** The seed ISOs are built by
``cloudinit.build_all`` and the ``image`` this backend needs is carried through
from ``preflight``, so the inherited ``Backend.prepare`` is the whole of it. The
Proxmox research predicted this backend would be the one that had to hold a
socket open while the image was pulled. **It is not.** Serving the image over
HTTP for PVE to pull was the ``download_file`` design; what shipped uploads over
the same API token, so nothing is held open.
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
        """Render the values, then make the objects they describe.

        ``render`` stays a step of its own now that nothing consumes its output
        but this line: it is the pure config-to-values half, golden-file tested
        byte for byte, and keeping it separate is what lets ``create`` be tested
        against a dict rather than against a config.
        """
        return _create.create(session, _render.render(cfg, prepared))
