"""The vSphere backend: six methods, bound together.

Two delegate to free functions in ``schema.py``, which imports nothing
hypervisor-specific. ``connect`` and the lookups live in ``api.py``, the one
module that reaches vCenter, and ``preflight``, ``create`` and ``destroy`` drive
their phases through them. ``prepare`` is the seventh and is overridden rather
than inherited, the only one of the three backends to do so: the conversion in
``convert.py`` is what the inherited body does not do.

**This package is deliberately not in ``orchestrator/backends/__init__.py``'s
``REGISTRY``** until the register chunk lands, so no config can name a backend
that is half built. Core takes a registry argument everywhere -- the tests build
their own dict and compose the core schema from it, which is the whole of what
registration would add.

**No ``pyVmomi`` import at module level, here or in any module this one imports
at import time.** The same rule the Proxmox backend follows for ``proxmoxer``
and the libvirt backend for ``libvirt``, and for the same reason: once the
registry names this class, importing the registry drags this file in on every
run, including runs that will never speak to a vCenter. ``api.py`` imports
``pyVim.connect`` inside the function that needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ... import qcow2
from ...problems import Problem
from ..base import Backend, Discovered, Existing, Outcome
from . import api as _api
from . import convert as _convert
from . import create as _create
from . import destroy as _destroy
from . import preflight as _preflight
from . import render as _render
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

    def prepare(
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> dict[str, Any]:
        """The inherited seed ISOs, plus the golden image as a VMDK.

        **The first backend to override ``prepare``**, which ``base.Backend``
        allows for exactly this: the seed ISOs are core's work either way, and
        what is added here is the format conversion vSphere needs and the other
        two backends do not.

        It happens here rather than in ``create`` because this is the phase that
        may touch the local filesystem and cannot reach the target, and because
        the file it writes belongs to the run: ``workdir`` is the run directory,
        and nothing tears the VMDK down.

        Skipped entirely once ``preflight`` has found the template already on the
        vCenter. Converting a multi-GB image to import nothing is the cost this
        branch exists to avoid, and it is the same reason the other two backends
        carry an ``image``/``base_volume`` ``create`` flag at all.

        ``capacity`` is read here rather than in ``render`` for the one reason
        ``render`` gives: it is a read of the golden image, and ``render`` does
        no I/O. Both keys are absent when nothing was converted, which is what
        ``render`` renders as empty.
        """
        prepared = super().prepare(cfg, workdir, discovered)
        if not discovered.artifacts["image"]["create"]:
            return prepared
        source = cfg["image"]["source_qcow2"]
        subformat = _convert.SUBFORMAT[
            cfg["target"]["vsphere"].get("import", _schema.IMPORT_DEFAULT)
        ]
        # Named after the template it becomes, in the run directory beside the
        # seed ISOs: a `monolithicFlat` conversion writes its `-flat` extent
        # alongside, so the stem has to be predictable.
        dest = workdir / f"{Path(cfg['image']['base_volume_name']).stem}.vmdk"
        prepared["vmdk"] = str(_convert.to_vmdk(source, dest, subformat))
        prepared["capacity"] = qcow2.virtual_size(source)
        return prepared

    def create(self, cfg: dict, session: Any, prepared: dict[str, Any]) -> dict:
        """Render the values, then make the objects they describe.

        ``render`` is a step of its own even though this line is its only
        consumer: it is the pure config-to-values half, golden-file tested byte
        for byte, and keeping it separate lets ``create`` be tested against a
        dict rather than against a config.
        """
        return _create.create(session, _render.render(cfg, prepared))
