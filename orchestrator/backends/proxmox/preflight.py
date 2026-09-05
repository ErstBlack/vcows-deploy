"""What exists on the cluster, in one pass, while a session is open.

The one place this backend reads the target during a deploy, so everything the
pure half needs has to be learned here and carried out in ``Discovered``. Four
questions, in the order their answers are needed:

1. **Which VMs exist, and which are ours.** Cluster-wide, because a VM migrated
   after vcows created it is still ours -- see ``api.cluster_vms``.
2. **Is the golden image already uploaded.** Without this a deploy re-uploads a
   multi-GB image every time. Exactly the role libvirt's ``base_volume`` plays.
3. **Do the two storages allow the content types this backend needs.** The
   ``import`` type in particular is *not* enabled by default on a PVE storage,
   and the failure without this check lands mid-apply.
4. **Is a seed ISO already sitting there for a VM we are about to create.** The
   residue of a half-finished earlier run, and it would collide.
"""

from __future__ import annotations

import logging
import os

from ...cloudinit import seed_name
from ...marker import Marker, MarkerError, from_description
from ...problems import Problem
from ..base import Discovered, Existing
from . import api

log = logging.getLogger(__name__)

#: What each configured storage has to be willing to hold.
NEEDED = {
    "import_datastore": ("import", "iso"),
    "datastore": ("images",),
}


def preflight(cfg: dict, session: api.Session) -> Discovered:
    problems: list[Problem] = []
    vms = _existing(session, problems)
    problems += _check_storages(cfg, session)
    image = _image(cfg, session, problems)
    problems += _orphan_seeds(cfg, session, vms)
    return Discovered(
        vms=tuple(vms), artifacts={"image": image}, problems=tuple(problems)
    )


def _existing(session: api.Session, problems: list[Problem]) -> list[Existing]:
    """Every VM the token can see, with its marker if it carries one."""
    found: list[Existing] = []
    for res in api.cluster_vms(session):
        node = str(res.get("node", ""))
        vmid = res.get("vmid")
        if vmid is None:
            # /cluster/resources is the authority on what exists, so an entry
            # with no vmid is a shape this code does not understand rather than
            # a VM to reason about. Reported, because silently dropping a VM is
            # how `decide` ends up planning a create over something live.
            problems.append(
                Problem.warning(
                    f"a VM on {node!r} was listed with no vmid; vcows cannot "
                    f"identify it and is ignoring it",
                    where=str(res.get("name") or "<unnamed>"),
                )
            )
            continue
        vmid = str(vmid)
        try:
            config = api.vm_config(session, node, vmid)
        except Exception as exc:
            # Reported rather than raised: a VM the token cannot read is a
            # permissions gap on that one VM, and refusing the whole run over it
            # would make a deploy impossible on a cluster with unrelated guests.
            problems.append(
                Problem.warning(
                    f"could not read the config of VM {vmid} on {node} ({exc}); "
                    f"vcows cannot tell whether it is one of ours",
                    where=str(res.get("name") or vmid),
                )
            )
            continue
        found.append(
            Existing(
                name=str(res.get("name") or ""),
                # Node and vmid together, because `destroy` is handed `Existing`
                # and nothing else -- and a vmid alone does not say which node to
                # send the delete to on a multi-node cluster.
                id=f"{node}/{vmid}",
                marker=_marker_of(config, node, vmid),
                disks=_media(config, session),
            )
        )
    return found


def _marker_of(config: dict, node: str, vmid: str) -> Marker | None:
    """The marker out of ``description``, or None. Unparseable reads as unmarked.

    Same call as the libvirt backend makes on a domain whose ``<metadata>`` will
    not parse, and for the same reason: refusing to run because somebody typed
    into a VM's notes would be worse than declining to claim it. Logged at INFO
    so the reason is recoverable.
    """
    try:
        return from_description(config.get("description"))
    except MarkerError as exc:
        log.info("VM %s on %s carries an unreadable marker: %s", vmid, node, exc)
        return None


def _media(config: dict, session: api.Session) -> tuple[str, ...]:
    """Volume ids of CD-ROM media this VM holds in the import datastore.

    Only the seed ISO can match: ``delete_vm`` with ``purge`` already removes the
    VM's own disks, so what is left to collect is the media vcows uploaded
    separately. Restricted to the configured datastore so a teardown can never
    reach an installer ISO somebody else parked on a different store.
    """
    found = []
    for key, value in config.items():
        if not key.startswith(("ide", "sata", "scsi", "virtio")) or not isinstance(
            value, str
        ):
            continue
        if "media=cdrom" not in value:
            continue
        volid = value.split(",")[0]
        if volid.startswith(f"{session.import_datastore}:"):
            found.append(volid)
    return tuple(found)


def _check_storages(cfg: dict, session: api.Session) -> list[Problem]:
    """Both storages exist on the node and allow what this backend puts in them.

    **The ``import`` content type is the one that bites.** It is not enabled by
    default on a PVE storage; it is added under Datacenter -> Storage. Without
    this check the run gets as far as uploading and fails mid-apply with a PVE
    error rather than an instruction.
    """
    target = cfg["target"]["proxmox"]
    problems: list[Problem] = []
    for field, needed in NEEDED.items():
        name = target[field]
        where = f"target.proxmox.{field}"
        entry = api.storage(session, name)
        if entry is None:
            problems.append(
                Problem.error(
                    f"node {session.node!r} has no storage named {name!r}. vcows "
                    f"never creates a storage.",
                    where=where,
                )
            )
            continue
        allowed = set(str(entry.get("content", "")).split(","))
        missing = [c for c in needed if c not in allowed]
        if missing:
            problems.append(
                Problem.error(
                    f"storage {name!r} does not allow content type(s) "
                    f"{', '.join(missing)}; it allows "
                    f"{', '.join(sorted(allowed)) or '<none>'}. Add them under "
                    f"Datacenter -> Storage -> {name} -> Content.",
                    where=where,
                )
            )
    return problems


def _image(cfg: dict, session: api.Session, problems: list[Problem]) -> dict:
    """Whether the golden image is already in the import datastore.

    ``create`` false once it is there. The volume id is what ``create_vm``'s
    ``import-from`` needs when nothing is uploaded, and it is PVE's own string
    for the file rather than one built here.

    A name match is not enough on its own, which is what ``_verified`` is for.
    """
    wanted = cfg["image"]["base_volume_name"]
    try:
        content = api.storage_content(session, session.import_datastore, "import")
    except Exception as exc:
        # A storage the token cannot list is reported rather than assumed empty:
        # assuming empty means planning an upload that then collides.
        problems.append(
            Problem.error(
                f"could not list the 'import' content of "
                f"{session.import_datastore!r} ({exc}); vcows cannot tell whether "
                f"the golden image is already there",
                where="target.proxmox.import_datastore",
            )
        )
        return {"create": False, "volid": ""}

    for item in content:
        volid = str(item.get("volid", ""))
        if volid.rsplit("/", 1)[-1] == wanted:
            log.info("golden image already present as %s", volid)
            problems += _verified(cfg, session, item)
            # `create: False` and the volid whatever the size says: the file is
            # there, so there is nothing to upload over it, and a fatal problem
            # is what stops the deploy rather than a plan to re-upload.
            return {"create": False, "volid": volid}
    return {"create": True, "volid": f"{session.import_datastore}:import/{wanted}"}


def _verified(cfg: dict, session: api.Session, item: dict) -> list[Problem]:
    """A present image is verified, not trusted.

    The twin of libvirt's ``base_volume``, which compares a present base
    volume's physical size with the local file. An interrupted upload leaves a
    truncated qcow2 whose header still declares the full virtual size, so
    matching the listing by name alone reuses it -- and every disk imported from
    it is a copy of a broken image. The comparison catches a different image
    under the same name as well.
    """
    name = cfg["image"]["base_volume_name"]
    source = cfg["image"]["source_qcow2"]
    try:
        local = os.stat(source).st_size
    except OSError as exc:
        return [
            Problem.warning(
                f"golden image {source!r} is not readable ({exc.strerror}), so the "
                f"copy already on the host cannot be verified against it.",
                where="image.source_qcow2",
            )
        ]

    size = item.get("size")
    if size is None:
        return [
            Problem.warning(
                f"volume {name!r} reports no size, so it cannot be checked "
                f"against {source!r}.",
                where="image.base_volume_name",
            )
        ]
    if size != local:
        # The procedure offered is the non-destructive one, as it is in
        # `base_volume`: a name this datastore does not hold uploads alongside
        # the existing file, and nothing already imported from it is touched.
        return [
            Problem.error(
                f"volume {name!r} is {size} bytes in "
                f"{session.import_datastore!r} but {local} bytes locally. That is "
                f"either a truncated upload or a different image under the same "
                f"name; either way every disk imported from it would be a copy of "
                f"it. Set image.base_volume_name to a name this datastore does not "
                f"hold and re-run: the new image uploads alongside the old one.",
                where="image.base_volume_name",
            )
        ]
    return []


def _orphan_seeds(
    cfg: dict, session: api.Session, existing: list[Existing]
) -> list[Problem]:
    """A seed ISO already there for a VM that is not.

    findings.md §2's orphan-volume refusal, in this backend's terms: the residue
    of a run that uploaded a seed and then failed before defining its VM. Left
    alone it collides with the upload this run is about to make, mid-apply.

    Only for VMs that do not already exist -- a seed belonging to a live VM of
    ours is not an orphan, it is in use.
    """
    live = {e.name for e in existing}
    try:
        content = api.storage_content(session, session.import_datastore, "iso")
    except Exception as exc:
        return [
            Problem.warning(
                f"could not list the 'iso' content of "
                f"{session.import_datastore!r} ({exc}); a leftover seed ISO would "
                f"not have been noticed",
                where="target.proxmox.import_datastore",
            )
        ]

    present = {str(i.get("volid", "")).rsplit("/", 1)[-1] for i in content}
    return [
        Problem.error(
            f"{seed_name(vm['name'])!r} is already in "
            f"{session.import_datastore!r}, but no VM named {vm['name']!r} "
            f"exists. It is the residue of an earlier run; remove it, or destroy "
            f"that deployment, before deploying again.",
            where=f"vms[{i}].name",
        )
        for i, vm in enumerate(cfg["vms"])
        if vm["name"] not in live and seed_name(vm["name"]) in present
    ]
