"""The libvirt backend: seven methods, bound together.

Four of them delegate to the free functions in ``schema.py``, ``render.py`` and
``prepare.py``, which import nothing hypervisor-specific. The three that hold a
connection live in ``preflight.py`` and ``destroy.py``.

**No libvirt import at module level, here or in any module this one imports at
import time.** ``orchestrator/backends/__init__.py`` names this class, so importing
the registry drags this file in on every run -- including runs on a machine with no
libvirt at all. ``tests/test_seam.py`` breaks the import and checks exactly that.
The hypervisor import lives inside the methods that need a connection.

The class arrives now rather than in Stage 2 for the reason findings.md §3 wants an
ABC in the first place (D28): a class satisfying the interface with three methods
raising ``NotImplementedError`` would instantiate cleanly and look finished.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..base import Backend, Discovered, Existing, Inventory, Prepared, Problem
from . import destroy as _destroy
from . import preflight as _preflight
from . import prepare as _prepare
from . import render as _render
from . import schema as _schema


class LibvirtBackend(Backend):
    name = "libvirt"

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

    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> None:
        _destroy.destroy(cfg, session, targets)

    # -- apply -----------------------------------------------------------

    @contextmanager
    def prepare(
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> Iterator[Prepared]:
        """Build the seed ISOs and carry preflight's findings through to ``render``.

        Nothing is torn down on the way out -- the run directory keeps the ISOs so a
        VM that will not boot can be debugged by inspecting the one it was given.
        The context manager is the seam's shape rather than this backend's need: a
        future backend serving an image over HTTP holds a socket open for the
        apply's life, and retrofitting that later would mean restructuring.
        """
        yield Prepared(
            workdir=workdir,
            artifacts={
                "seed_isos": _prepare.build_all(cfg, workdir),
                # Discovered while connected, because nothing downstream can find
                # it out: no HCL data source reads a pool, `tofu import` probes by
                # the path we are looking for, and the pool's directory belongs to
                # somebody else's pool definition.
                "base_volume": discovered.artifacts["base_volume"],
            },
        )

    def render(self, cfg: dict, prepared: Prepared) -> dict:
        return _render.render(cfg, prepared)

    def parse_outputs(self, raw: dict) -> Inventory:
        """``tofu output -json`` to the inventory contract.

        This exists so the module's ``output`` block is not the public API. Rename
        an output and only this method changes, rather than every consumer of
        inventory.json.
        """
        return Inventory(vms=raw.get("vms", {}).get("value", {}))
