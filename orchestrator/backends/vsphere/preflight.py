"""What exists on the vCenter, in one pass, while a session is open.

The one place this backend reads the target during a deploy, so everything the
pure half needs has to be learned here and carried out in ``Discovered``. Four
questions, in the order their answers are needed:

1. **Does every name in ``target.vsphere`` resolve.** A datastore or a port
   group that is not there fails a clone mid-apply otherwise, with a vCenter
   fault naming no config field.
2. **Which VMs exist, and which are ours.** One ``PropertyCollector`` call for
   the whole inventory -- ``api.vms`` -- rather than a round trip per property
   per VM.
3. **Is the golden image already here as a template.** Without this every
   deploy re-converts and re-uploads a multi-GB image. The role libvirt's
   ``base_volume`` and the Proxmox backend's uploaded image both play.
4. **Is a seed ISO already sitting under ``[ds] vcows/`` for a VM that is
   not.** The residue of a half-finished earlier run, and it would collide.

**No managed object leaves this module.** ``Discovered.artifacts`` carries names
and booleans, so ``prepare`` and ``render`` stay pure and golden-file testable;
``api.Session`` says why the resolved objects are not held there either.
"""

from __future__ import annotations

import logging
from typing import Any

from ...cloudinit import seed_name
from ...marker import Marker, MarkerError, from_description
from ...problems import Problem
from ..base import Discovered, Existing
from . import api

log = logging.getLogger(__name__)

#: The datastore folder every seed ISO is uploaded under, one folder per VM:
#: ``[ds] vcows/<vm>/<vm>-seed.iso``.
SEED_FOLDER = "vcows"


def preflight(cfg: dict, session: api.Session) -> Discovered:
    problems: list[Problem] = []
    datastore = _check_target(cfg, session, problems)
    found = api.vms(session.content)
    vms = _existing(cfg, found, problems)
    image = _image(cfg, found, problems)
    problems += _orphan_seeds(cfg, datastore, vms)
    return Discovered(
        vms=tuple(vms), artifacts={"image": image}, problems=tuple(problems)
    )


def _check_target(cfg: dict, session: api.Session, problems: list[Problem]) -> Any:
    """Every name under ``target.vsphere`` resolves to an object on this vCenter.

    Returns the datastore alone, because it is the only resolved object anything
    later in this pass needs -- ``_orphan_seeds`` searches through its browser.
    The other five are resolved to prove they resolve, and the phases that place
    a clone resolve them again by name; ``api.Session`` says why.

    **Each miss is a Problem, never an exception**, and every name is looked at
    rather than the first miss ending the walk, for the reason ``config.load``
    reports every problem at once: an operator at an air-gapped site should not
    round-trip once per fault.

    The datacenter is the exception, and only because it is the container the
    other five are looked for inside: without it there is nowhere to look, so
    the walk stops rather than reporting five misses that all mean one.
    """
    from pyVmomi import vim

    target = cfg["target"]["vsphere"]
    name = target["datacenter"]
    datacenter = api.find_by_name(session.content, vim.Datacenter, name)
    if datacenter is None:
        problems.append(
            Problem.error(
                f"this vCenter has no datacenter named {name!r}. vcows never "
                f"creates one, and every other name in target.vsphere is "
                f"resolved inside it.",
                where="target.vsphere.datacenter",
            )
        )
        return None

    resolved: dict[str, Any] = {}
    for field, kind in (
        ("datastore", vim.Datastore),
        # Exactly one of these is set: `schema._check_placement` refuses a config
        # carrying both or neither, so the other is absent here rather than
        # missing.
        ("cluster", vim.ClusterComputeResource),
        ("host", vim.HostSystem),
        # Both optional. Without them the clone lands in the datacenter's VM
        # folder and in the root resource pool, which is what the schema says.
        ("folder", vim.Folder),
        ("resource_pool", vim.ResourcePool),
        ("network", vim.Network),
    ):
        wanted = target.get(field)
        if wanted is None:
            continue
        resolved[field] = api.find_by_name(
            session.content, kind, wanted, root=datacenter
        )
        if resolved[field] is None:
            problems.append(
                Problem.error(
                    f"datacenter {name!r} has no {field.replace('_', ' ')} named "
                    f"{wanted!r}. vcows never creates one.",
                    where=f"target.vsphere.{field}",
                )
            )
    return resolved.get("datastore")


def _existing(cfg: dict, found: list[dict], problems: list[Problem]) -> list[Existing]:
    """Every VM on the vCenter that is not a template, with its marker.

    **A template is never a target.** ``config.template`` true is the golden
    image, which ``_image`` reports on and which no deploy and no teardown may
    treat as one of its VMs -- destroying it would take every other
    deployment's clones with it.

    ``Existing.name`` is the name vCenter reports and nothing else: no folder
    path, no prefix. ``base.Existing`` says why -- ``decide()`` compares it
    against the config's logical name, and a transformed name there compares two
    different things, so the name-clash refusal never fires.
    """
    datastore = cfg["target"]["vsphere"]["datastore"]
    vms: list[Existing] = []
    for props in found:
        if props.get("config.template"):
            continue
        uuid = props.get("summary.config.uuid")
        name = str(props.get("name") or "")
        if not uuid:
            # Reported rather than dropped: the uuid is the only identity
            # `destroy` is handed, and a VM missing from this list is one
            # `decide` will happily plan a create over.
            problems.append(
                Problem.warning(
                    "a VM was listed with no uuid; vcows cannot identify it and "
                    "is ignoring it",
                    where=name or "<unnamed>",
                )
            )
            continue
        vms.append(
            Existing(
                name=name,
                id=str(uuid),
                marker=_marker_of(props),
                disks=_media(props, datastore),
            )
        )
    return vms


def _marker_of(props: dict) -> Marker | None:
    """The marker out of ``config.annotation``, or None. Unparseable is unmarked.

    Same call the other two backends make on a VM whose ownership record will
    not parse, and for the same reason: refusing to run because somebody typed
    into a VM's notes would be worse than declining to claim it. Logged at INFO
    so the reason is recoverable.
    """
    try:
        return from_description(props.get("config.annotation"))
    except MarkerError as exc:
        log.info("VM %r carries an unreadable marker: %s", props.get("name"), exc)
        return None


def _media(props: dict, datastore: str) -> tuple[str, ...]:
    """The datastore paths of the ISOs this VM has in its CD-ROM drives.

    **CD-ROM backings only, and never a disk's.** A VM here is a linked clone
    whose disk is an overlay on the template's, and
    ``VirtualDisk.FlatVer2BackingInfo.parent`` names that template disk --
    every other deployment's clones are overlays on it too. ``base.Existing``
    calls following the chain the way to destroy the base image; not reading a
    disk backing at all is how this backend cannot.

    Restricted to the configured datastore, as the Proxmox backend restricts its
    media to the configured storage, so a teardown can never reach an installer
    ISO somebody parked somewhere else.
    """
    from pyVmomi import vim

    return tuple(
        str(device.backing.fileName)
        for device in props.get("config.hardware.device") or ()
        if isinstance(device, vim.vm.device.VirtualCdrom)
        and isinstance(device.backing, vim.vm.device.VirtualCdrom.IsoBackingInfo)
        and str(device.backing.fileName).startswith(f"[{datastore}]")
    )


def _image(cfg: dict, found: list[dict], problems: list[Problem]) -> dict:
    """Whether the golden image is already here as a template VM of ours.

    ``create`` is false once a template named ``base_volume_name`` carrying our
    marker exists: the bytes moved on the run that made it, and a linked clone
    of it moves none. The name is carried either way because it is what the
    create chunk clones from or makes.

    A VM of that name that is *not* a marked template stops the deploy rather
    than planning one over it -- the never-adopt rule the whole tool is built on,
    and creating over it would collide on the name anyway.
    """
    wanted = cfg["image"]["base_volume_name"]
    for props in found:
        if props.get("name") != wanted:
            continue
        if not props.get("config.template"):
            reason = "is a VM rather than a template, so nothing can be cloned from it"
        elif _marker_of(props) is None:
            reason = "carries no vcows marker, so it is not ours to clone or replace"
        else:
            log.info("golden image already present as template %r", wanted)
            return {"create": False, "template": wanted}
        problems.append(
            Problem.error(
                f"a VM named {wanted!r} already exists on this vCenter and {reason}. "
                f"vcows will not adopt or overwrite it: set image.base_volume_name "
                f"to a name this vCenter does not hold.",
                where="image.base_volume_name",
            )
        )
        return {"create": False, "template": wanted}
    return {"create": True, "template": wanted}


def _orphan_seeds(cfg: dict, datastore: Any, existing: list[Existing]) -> list[Problem]:
    """A seed ISO already under ``[ds] vcows/`` for a VM that is not there.

    findings.md section 2's orphan-volume refusal, in this backend's terms: the
    residue of a run that uploaded a seed and then failed before cloning its VM.
    Left alone it collides with the upload this run is about to make, mid-apply.

    Only for VMs that do not already exist -- a seed beside a live VM of ours is
    not an orphan, it is the media that VM booted from and is still holding.
    """
    if datastore is None:
        # Unresolvable, and already reported as such by `_check_target`. A
        # second problem saying the search did not happen would name the same
        # fault twice.
        return []
    live = {e.name for e in existing}
    path = f"[{cfg['target']['vsphere']['datastore']}] {SEED_FOLDER}"
    try:
        found = api.datastore_files(datastore, path, seed_name("*"))
    except Exception as exc:
        # A warning, not a refusal: the search failing says nothing about
        # whether a seed is there, and the Proxmox backend answers an
        # unlistable seed store the same way.
        return [
            Problem.warning(
                f"could not search {path!r} ({exc}); a leftover seed ISO would "
                f"not have been noticed",
                where="target.vsphere.datastore",
            )
        ]

    present = {path.rpartition("/")[-1]: path for path in found}
    return [
        Problem.error(
            f"{present[seed_name(vm['name'])]!r} is already on the datastore, but "
            f"no VM named {vm['name']!r} exists. It is the residue of an earlier "
            f"run; remove it, or destroy that deployment, before deploying again.",
            where=f"vms[{i}].name",
        )
        for i, vm in enumerate(cfg["vms"])
        if vm["name"] not in live and seed_name(vm["name"]) in present
    ]
