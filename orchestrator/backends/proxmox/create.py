"""The apply, through proxmoxer.

One function per resource, in dependency order: the golden image when preflight
found the cluster without it, then per VM a seed ISO and a VM. Ported from the
#198 spike, whose sequence PVE 8.4.0 accepted and whose VM the shipped
``vcows destroy`` removed (``docs/tofu-eval-2026-09-02.md`` M4) -- so the
parameters here are the parameters that were measured.

**Every task goes through ``api.wait``**, the same function ``stop_vm`` and
``delete_vm`` use, so a task that stops badly is a failure here too rather than
a create that reports success and leaves a VM that never started.

**Nothing is rolled back.** ``_made`` names the resource on the exception and
re-raises, and the marker plus ``preflight._orphan_seeds`` is what lets a later
run see the leftovers.

No ``proxmoxer`` import here, at module scope or anywhere else, for the reason
``__init__`` gives: importing the registry must not drag the client in. The
uploads reach it through the ``api.Session`` this module is handed.
"""

from __future__ import annotations

import io
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import api

log = logging.getLogger(__name__)


@contextmanager
def _made(what: str) -> Iterator[None]:
    """One line per created resource, naming it and what it cost.

    **The name rides on the exception, not only on the log line.** The log is
    stderr; ``run.json``'s ``error`` field is what an air-gapped site ships back,
    and ``cli._guard`` fills it from the exception's text. Without this a failed
    task reads as a UPID and an exit status and says nothing about which of a
    run's four-per-VM objects was being made. **This is the only thing that
    names the resource**, which is why the ``what`` handed to ``api.wait`` inside
    is the bare step -- ``upload``, ``create``, ``resize``, ``start`` -- rather
    than the name a second time.

    Re-raised as ``ProxmoxApiError`` rather than as whatever came out, because
    the only other thing that reaches here is proxmoxer's own
    ``ResourceException``, which ``api.connect`` already translates to this type
    at the boundary. Constructing a fresh one is safe here in a way it is not
    for ``libvirtError``: it is this project's class and it keeps the message it
    is handed. The original stays on ``__cause__``.
    """
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        message = f"could not create {what}: {exc}"
        log.error("%s", message)
        raise api.ProxmoxApiError(message) from exc
    log.info("created %s in %.1fs", what, time.monotonic() - started)


def upload(
    session: api.Session, content: str, path: str, file_name: str, checksum: str
) -> str:
    """``proxmox_virtual_environment_file``: one multipart POST to the storage's
    upload endpoint, then the task wait.

    PVE names the file after the multipart part, and proxmoxer takes that from
    the file object's ``name``, so a ``FileIO`` is opened and renamed rather
    than the file copied to the name it has to arrive under.

    proxmoxer streams this part only when ``requests_toolbelt`` is importable;
    without it the whole file is read into memory and anything over 2 GiB raises
    ``OverflowError``. It is not imported here -- proxmoxer finds it itself --
    which is why the Containerfile installs it and nothing in this package names
    it.
    """
    fh = io.FileIO(path)
    fh.name = file_name  # type: ignore[misc]
    params: dict[str, Any] = {"content": content, "filename": fh}
    if checksum:
        params |= {"checksum": checksum, "checksum-algorithm": "sha256"}
    with fh:
        upid = (
            session.prox.nodes(session.node)
            .storage(session.import_datastore)
            .upload.post(**params)
        )
    api.wait(session, upid, "upload")
    return f"{session.import_datastore}:{content}/{file_name}"


def create_vm(
    session: api.Session, vmid: str, vm: dict, image_id: str, seed_id: str
) -> None:
    """``proxmox_virtual_environment_vm``: one POST carrying what the module's
    blocks carried, then a resize and a start.

    The resize is not optional decoration. ``import-from`` gives the disk the
    golden image's own size, so ``disk_gb`` is only honoured by growing it
    afterwards -- and PVE cannot shrink a disk, so a config asking for less than
    the image is refused rather than silently deployed at the wrong size.
    """
    params: dict[str, Any] = {
        "vmid": vmid,
        "name": vm["vm_name"],
        "description": vm["description"],
        "bios": vm["bios"],
        "machine": vm["machine"],
        "onboot": 1,
        "cores": vm["vcpus"],
        "cpu": "host",
        "memory": vm["memory_mib"],
        "ostype": vm["os_type"],
        "scsihw": "virtio-scsi-pci",
        "scsi0": f"{session.datastore}:0,import-from={image_id},discard=on,ssd=1",
        "ide2": f"{seed_id},media=cdrom",
        "boot": "order=scsi0;ide2",
    }
    if vm["bios"] == "ovmf":
        params["efidisk0"] = f"{session.datastore}:1,efitype=4m"
    for i, n in enumerate(vm["nics"]):
        tag = f",tag={n['vlan_id']}" if n["vlan_id"] else ""
        params[f"net{i}"] = f"{n['model']}={n['mac']},bridge={n['bridge']}{tag}"
    qemu = session.prox.nodes(session.node).qemu
    api.wait(session, qemu.post(**params), "create")

    imported = _size_gb(qemu(vmid).config.get()["scsi0"])
    if vm["disk_gb"] > imported:
        done = qemu(vmid).resize.put(disk="scsi0", size=f"{vm['disk_gb']}G")
        # Current PVE answers a resize with a UPID; older ones answer with
        # nothing and have already done the work. Both are a success.
        if isinstance(done, str) and done.startswith("UPID"):
            api.wait(session, done, "resize")
    elif vm["disk_gb"] < imported:
        raise api.ProxmoxApiError(
            f"disk_gb {vm['disk_gb']} is below the imported image's "
            f"{imported} GiB and PVE cannot shrink it"
        )
    api.wait(session, qemu(vmid).status.start.post(), "start")


def _size_gb(disk: str) -> int:
    """``size=10G`` out of a PVE disk string. Only G is expected off an import."""
    size = dict(kv.split("=", 1) for kv in disk.split(",")[1:] if "=" in kv)["size"]
    units = {"G": 1, "T": 1024, "M": 1 / 1024}
    return int(float(size[:-1]) * units[size[-1]])


def create(session: api.Session, tfvars: dict) -> dict:
    """Create everything ``render`` described, and report it as the inventory.

    Keyed by the logical name, with the five fields ``inventory.json`` carries.
    """
    image = tfvars["image"]
    image_id = image["volid"]
    if image["create"]:
        with _made(f"image {image['file_name']}"):
            image_id = upload(
                session,
                "import",
                image["source"],
                image["file_name"],
                image["checksum"],
            )

    vms: dict[str, dict] = {}
    for key, vm in tfvars["vms"].items():
        with _made(f"seed {vm['seed_name']}"):
            seed_id = upload(session, "iso", vm["seed_iso"], vm["seed_name"], "")
        # Asked for per VM rather than once, because the VM created a moment ago
        # has taken the previous answer. Inside `_made` like every other call
        # here: a cluster that cannot answer must say which VM it was asked for.
        with _made(f"vmid for {vm['vm_name']}"):
            vmid = str(session.prox.cluster.nextid.get())
        with _made(f"vm {vm['vm_name']} ({vmid})"):
            create_vm(session, vmid, vm, image_id, seed_id)
        vms[key] = {
            "name": vm["vm_name"],
            "vmid": int(vmid),
            "node": session.node,
            "configured_address": vm["configured_address"],
            "disks": [seed_id],
        }
    return vms
