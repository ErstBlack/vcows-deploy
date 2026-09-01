"""The Proxmox VE backend: eight methods, bound together.

Four delegate to free functions in ``schema.py`` and ``render.py``, which import
nothing hypervisor-specific, and to ``orchestrator/cloudinit.py``, which is core
because this backend and the libvirt one build the identical seed ISO. The three
that hold a session live in ``api.py``, ``preflight.py`` and ``destroy.py``.

**No ``proxmoxer`` import at module level, here or in any module this one imports
at import time.** ``orchestrator/backends/__init__.py`` names this class, so
importing the registry drags this file in on every run -- including runs that
only ever talk to libvirt. ``api.py`` imports ``proxmoxer`` inside the functions
that need it; ``tests/test_seam.py`` is the gate.

**Why there is no ``prepare.py`` here.** The seed ISOs are built by
``cloudinit.build_all``, and this backend adds nothing to that -- unlike the
libvirt backend, which also has to carry its base-volume lookup through. The
context manager still yields immediately: ``Backend.prepare``'s shape exists for
a backend that must hold a socket open for the apply's life, and the Proxmox
research predicted this backend would be that one. **It is not.** Serving the
image over HTTP for PVE to pull was the ``download_file`` design; what shipped
uploads through the provider over the same API token, so nothing is held open.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ... import cloudinit as _cloudinit
from ...problems import Problem
from ..base import Backend, Discovered, Existing, Inventory, Outcome, Prepared
from . import destroy as _destroy
from . import preflight as _preflight
from . import render as _render
from . import schema as _schema


class ProxmoxBackend(Backend):
    name = "proxmox"

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

    @contextmanager
    def prepare(
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> Iterator[Prepared]:
        """Build the seed ISOs and carry preflight's findings through to ``render``.

        Nothing is torn down on the way out -- the run directory keeps the ISOs so
        a VM that will not boot can be debugged by inspecting the one it was given.
        """
        yield Prepared(
            workdir=workdir,
            artifacts={
                "seed_isos": _cloudinit.build_all(cfg, workdir),
                # Discovered while connected, because nothing downstream can find
                # it out: the module has no data source that lists a storage's
                # import content, and the apply runs against an empty state.
                "image": discovered.artifacts["image"],
            },
        )

    def render(self, cfg: dict, prepared: Prepared) -> dict:
        return _render.render(cfg, prepared)

    def parse_outputs(self, raw: dict) -> Inventory:
        """``tofu output -json`` to the inventory contract.

        This exists so the module's ``output`` block is not the public API --
        rename an output and only this method changes. A missing one is a broken
        module and is raised as such, rather than read as an empty inventory and
        reported as ``created 0 VM(s)`` under ``outcome: ok``.
        """
        if "vms" not in raw:
            raise ValueError(
                "the tofu module declared no `vms` output. Its outputs were: "
                f"{', '.join(sorted(raw)) or '<none>'}"
            )
        return Inventory(vms=raw["vms"].get("value", {}))
