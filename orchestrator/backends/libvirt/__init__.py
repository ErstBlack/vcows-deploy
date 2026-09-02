"""The libvirt backend: seven methods, bound together.

Three of them delegate to the free functions in ``schema.py``, which imports
nothing hypervisor-specific, and to ``orchestrator/cloudinit.py``, which is core
because nothing in the seed ISO is hypervisor-specific. The three that hold a
connection live in ``preflight.py`` and ``destroy.py``. ``create`` is the
seventh and is a stub: it lands in the commit that replaces the tofu module.

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

from pathlib import Path
from typing import Any

from ... import cloudinit as _cloudinit
from ...problems import Problem
from ..base import (
    Backend,
    Discovered,
    Existing,
    Outcome,
    Prepared,
)
from . import destroy as _destroy
from . import preflight as _preflight
from . import schema as _schema


class LibvirtBackend(Backend):
    # -- offline ---------------------------------------------------------

    def config_schema(self) -> dict:
        return _schema.TARGET_SCHEMA

    def validate(self, cfg: dict) -> list[Problem]:
        return _schema.validate(cfg)

    # -- connected -------------------------------------------------------

    def connect(self, cfg: dict) -> Any:
        return _preflight.connect(cfg)

    def preflight(self, cfg: dict, session: Any) -> Discovered:
        return _preflight.preflight(cfg, session)

    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
        return _destroy.destroy(cfg, session, targets)

    # -- apply -----------------------------------------------------------

    def prepare(self, cfg: dict, workdir: Path, discovered: Discovered) -> Prepared:
        """Build the seed ISOs and carry preflight's findings through to ``create``.

        Nothing is torn down afterwards -- the run directory keeps the ISOs so a
        VM that will not boot can be debugged by inspecting the one it was given.
        """
        return Prepared(
            artifacts={
                "seed_isos": _cloudinit.build_all(cfg, workdir),
                # Discovered while connected, because nothing downstream can find
                # it out: the pool's directory belongs to somebody else's pool
                # definition, and `create` is handed data rather than a lookup.
                "base_volume": discovered.artifacts["base_volume"],
            },
        )

    def create(self, cfg: dict, session: Any, prepared: Prepared) -> dict:
        raise NotImplementedError("create lands in the next commit")
