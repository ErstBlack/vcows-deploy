#!/usr/bin/env python3
"""B2 -- create through proxmoxer instead of the provider, for #198.

The shape of `b1_libvirt_create.py`: the shipped code up to `render`, then
the dict `render` hands OpenTofu consumed directly. Every task goes through
the `api.wait` the destroy path already uses. The shipped `vcows destroy`
tears the result down, which is the compatibility check.

Usage:
    b2_proxmox_create.py CONFIG --run-dir DIR [--dry-run]

`--dry-run` connects to nothing: it renders as if the image were not yet on
the cluster and prints the exact API calls that would be made, which is the
desk check on a box that cannot reach a cluster.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orchestrator.backends import REGISTRY
from orchestrator.backends.base import Action, Discovered, decide
from orchestrator.backends.proxmox import api
from orchestrator.config import load, vm_names

# ---- What the provider does today ------------------------------------------
# One function per resource in orchestrator/backends/proxmox/tofu/main.tf.

Wait = Callable[[Any, str, str], None]


def upload(
    session: Any, content: str, path: str, file_name: str, checksum: str, wait: Wait
) -> str:
    """`proxmox_virtual_environment_file`: one multipart POST to the storage's
    upload endpoint, then the task wait. PVE names the file after the multipart
    part, and proxmoxer takes that from the file object's `name`, so a FileIO
    is opened and renamed rather than the file copied."""
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
    wait(session, upid, f"upload {file_name}")
    return f"{session.import_datastore}:{content}/{file_name}"


def create_vm(
    session: Any, vmid: str, vm: dict, image_id: str, seed_id: str, wait: Wait
) -> None:
    """`proxmox_virtual_environment_vm`: one POST carrying what the module's
    blocks carry, then resize to `disk_gb` (import gives the image's own size)
    and start."""
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
    wait(session, qemu.post(**params), f"create {vm['vm_name']}")

    imported = _size_gb(qemu(vmid).config.get()["scsi0"])
    if vm["disk_gb"] > imported:
        done = qemu(vmid).resize.put(disk="scsi0", size=f"{vm['disk_gb']}G")
        if isinstance(done, str) and done.startswith("UPID"):
            wait(session, done, f"resize {vm['vm_name']}")
    elif vm["disk_gb"] < imported:
        raise api.ProxmoxApiError(
            f"{vm['vm_name']}: disk_gb {vm['disk_gb']} is below the imported "
            f"image's {imported} GiB and PVE cannot shrink it"
        )
    wait(session, qemu(vmid).status.start.post(), f"start {vm['vm_name']}")


def _size_gb(disk: str) -> int:
    """`size=10G` out of a PVE disk string. Only G is expected off an import."""
    size = dict(kv.split("=", 1) for kv in disk.split(",")[1:] if "=" in kv)["size"]
    units = {"G": 1, "T": 1024, "M": 1 / 1024}
    return int(float(size[:-1]) * units[size[-1]])


def create(session: Any, tfvars: dict, wait: Wait = api.wait) -> dict:
    """The apply. Returns what `outputs.tf` returns, keyed by logical name.

    Order matches the module's edges: image, then per VM seed, VM. A failure
    raises out with the resource named; nothing is rolled back, which is also
    what the provider does. The marker plus `preflight._orphan_seeds` is what
    lets vcows see the leftovers.
    """
    image = tfvars["image"]
    image_id = image["volid"]
    if image["create"]:
        with timed(f"image {image['file_name']}"):
            image_id = upload(
                session,
                "import",
                image["source"],
                image["file_name"],
                image["checksum"],
                wait,
            )
    vms: dict[str, dict] = {}
    for key, vm in tfvars["vms"].items():
        with timed(f"seed {vm['seed_name']}"):
            seed_id = upload(session, "iso", vm["seed_iso"], vm["seed_name"], "", wait)
        vmid = str(session.prox.cluster.nextid.get())
        with timed(f"vm {vm['vm_name']} ({vmid})"):
            create_vm(session, vmid, vm, image_id, seed_id, wait)
        vms[key] = {
            "name": vm["vm_name"],
            "vmid": int(vmid),
            "node": session.node,
            "configured_address": vm["configured_address"],
            "disks": [seed_id],
        }
    return vms


@contextmanager
def timed(what: str) -> Iterator[None]:
    t0 = time.monotonic()
    try:
        yield
    except Exception as exc:
        print(
            f"  FAIL {what} after {time.monotonic() - t0:.1f}s: {exc}", file=sys.stderr
        )
        raise
    print(f"  ok   {what}  {time.monotonic() - t0:.1f}s")


# ---- Dry run: the calls, printed, against nothing --------------------------


class Echo:
    """proxmoxer's chaining shape, printing each request instead of sending it."""

    def __init__(self, parts: tuple[str, ...] = ()):
        self.parts = parts

    def __getattr__(self, name: str) -> Echo:
        return Echo((*self.parts, name))

    def __call__(self, arg: Any) -> Echo:
        return Echo((*self.parts, str(arg)))

    def get(self, **kw: Any) -> Any:
        self._show("GET", kw)
        return (
            "100"
            if self.parts[-1] == "nextid"
            else {"scsi0": "x:vm-100-disk-0,size=10G"}
        )

    def post(self, **kw: Any) -> str:
        return self._show("POST", kw)

    def put(self, **kw: Any) -> str:
        return self._show("PUT", kw)

    def _show(self, verb: str, kw: dict) -> str:
        shown = {k: getattr(v, "name", v) for k, v in kw.items()}
        print(f"  {verb} /{'/'.join(self.parts)} {json.dumps(shown)}")
        return "UPID:dry"


# ---- Harness: the shipped code up to the apply, as `cli._deploy` orders it ---


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg, problems = load(args.config, REGISTRY)
    backend = REGISTRY[cfg["backend"]]
    target = cfg["target"]["proxmox"]
    if args.dry_run:
        volid = (
            f"{target['import_datastore']}:import/{cfg['image']['base_volume_name']}"
        )
        discovered = Discovered(
            vms=(), artifacts={"image": {"create": True, "volid": volid}}, problems=()
        )
        session = api.Session(
            Echo(), target["node"], target["datastore"], target["import_datastore"]
        )
        wait: Wait = lambda *_: None  # noqa: E731
        create_cfg = cfg
    else:
        session_cm = backend.connect(cfg)
        session = session_cm.__enter__()
        discovered = backend.preflight(cfg, session)
        decisions, policy = decide(vm_names(cfg), discovered.vms, cfg["deployment"])
        problems += list(discovered.problems) + policy
        for d in decisions:
            print(f"{d.vm_name}: {d.action.value} ({d.reason})")
        for p in problems:
            print(str(p))
        if any(d.action is Action.REFUSE for d in decisions) or any(
            p.fatal for p in problems
        ):
            return 1
        creating = {d.vm_name for d in decisions if d.action is Action.CREATE}
        if not creating:
            print("nothing to create")
            return 0
        create_cfg = {**cfg, "vms": [vm for vm in cfg["vms"] if vm["name"] in creating]}
        wait = api.wait

    seed = args.run_dir / "seed"
    seed.mkdir(parents=True)
    with backend.prepare(create_cfg, seed, discovered) as prepared:
        tfvars = backend.render(create_cfg, prepared)
        t0 = time.monotonic()
        try:
            vms = create(session, tfvars, wait)
        finally:
            if not args.dry_run:
                session_cm.__exit__(None, None, None)
        print(f"created {len(vms)} VM(s) in {time.monotonic() - t0:.1f}s")
    (args.run_dir / "inventory.json").write_text(json.dumps({"vms": vms}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
