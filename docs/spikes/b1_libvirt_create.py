#!/usr/bin/env python3
"""B1 -- create through libvirt-python instead of the provider, for #198.

Everything before the create is the shipped code, called the way `cli._deploy`
calls it: load, connect, preflight, decide, prepare, render. The dict `render`
hands OpenTofu is what this script consumes, so the only new code is the part
between "What the provider does today" and "Harness". The shipped `vcows
destroy` tears the result down, which is the compatibility check.

Usage:
    b1_libvirt_create.py CONFIG --run-dir DIR [--dry-run] [--sparse]

`--dry-run` stops after render and prints the domain XML that would be defined.
`--sparse` uploads the base image with VIR_STORAGE_VOL_UPLOAD_SPARSE_STREAM.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orchestrator.backends import REGISTRY
from orchestrator.backends.base import Action, decide
from orchestrator.backends.libvirt.schema import connection_uri
from orchestrator.config import load, vm_names

# ---- What the provider does today ------------------------------------------
# One function per resource in orchestrator/backends/libvirt/tofu/main.tf, in
# the order the module's dependency edges impose.

VOLUME_XML = """<volume type='file'>
  <name>{name}</name>
  <capacity unit='bytes'>{capacity}</capacity>
  <target><format type='{fmt}'/></target>
  {backing}
</volume>"""

BACKING_XML = "<backingStore><path>{path}</path><format type='qcow2'/></backingStore>"

DOMAIN_XML = """<domain type='kvm'>
  <name>{domain_name}</name>
  <memory unit='MiB'>{memory_mib}</memory>
  <vcpu>{vcpus}</vcpu>
  <metadata>{marker_xml}</metadata>
  <os{firmware_attr}>
    <type arch='x86_64' machine='{machine}'>hvm</type>
{loader_xml}    <boot dev='hd'/>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='{overlay}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
{interfaces}    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
</domain>
"""

INTERFACE_XML = """    <interface type='{kind}'>
      <mac address='{mac}'/>
      <source {kind}='{source}'/>
      <model type='{model}'/>
    </interface>
"""

NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"


def firmware_xml(vm: dict) -> tuple[str, str]:
    """The `os` block's two halves, with the same exclusivity `main.tf` keeps:
    autoselect only when nothing is pinned, and the pin as the whole config."""
    if vm["loader"] is None:
        return (" firmware='efi'" if vm["firmware"] == "efi" else "", "")
    fmt = vm["loader_format"]
    lines = (
        f"    <loader readonly='yes' type='pflash' format='{fmt}'>"
        f"{vm['loader']}</loader>\n"
    )
    if vm["nvram_template"] is not None:
        ext = "qcow2" if fmt == "qcow2" else "fd"
        nv_fmt = f" format='{fmt}'" if fmt != "raw" else ""
        lines += (
            f"    <nvram template='{vm['nvram_template']}'{nv_fmt}>"
            f"{NVRAM_DIR}/{vm['domain_name']}_VARS.{ext}</nvram>\n"
        )
    return "", lines


def domain_xml(vm: dict, overlay: str, seed: str) -> str:
    firmware, loader = firmware_xml(vm)
    interfaces = "".join(
        INTERFACE_XML.format(
            kind="network" if n["network"] else "bridge",
            source=n["network"] or n["bridge"],
            mac=n["mac"],
            model=n["model"],
        )
        for n in vm["nics"]
    )
    return DOMAIN_XML.format(
        **vm,
        firmware_attr=firmware,
        loader_xml=loader,
        overlay=overlay,
        seed=seed,
        interfaces=interfaces,
    )


def upload(conn: Any, pool: Any, name: str, fmt: str, source: str, sparse: bool) -> Any:
    """`libvirt_volume` with `create.content.url`: create at the file's size and
    stream the bytes in. Capacity is the file's size on purpose (spike A4)."""
    import libvirt

    size = os.path.getsize(source)
    vol = pool.createXML(
        VOLUME_XML.format(name=name, capacity=size, fmt=fmt, backing=""), 0
    )
    stream = conn.newStream(0)
    with open(source, "rb", buffering=0) as fh:
        fd = fh.fileno()
        if sparse:
            vol.upload(stream, 0, size, libvirt.VIR_STORAGE_VOL_UPLOAD_SPARSE_STREAM)
            stream.sparseSendAll(
                lambda st, n, _: os.read(fd, n),
                lambda st, _: _section(fd, size),
                lambda st, n, _: os.lseek(fd, n, os.SEEK_CUR) and 0,
                None,
            )
        else:
            vol.upload(stream, 0, size, 0)
            stream.sendAll(lambda st, n, _: os.read(fd, n), None)
    stream.finish()
    return vol


def _section(fd: int, size: int) -> list:
    """`[in_data, length]` of the section at the current offset, for the sparse
    stream. SEEK_DATA/SEEK_HOLE move the offset, so it is put back. At the end
    `[True, 0]` makes the read handler return nothing, which ends the loop."""
    here = os.lseek(fd, 0, os.SEEK_CUR)
    if here >= size:
        return [True, 0]
    try:
        data = os.lseek(fd, here, os.SEEK_DATA)
    except OSError:  # ENXIO: only a hole remains
        data = size
    end = os.lseek(fd, here, os.SEEK_HOLE) if data == here else data
    os.lseek(fd, here, os.SEEK_SET)
    return [data == here, end - here]


def overlay(pool: Any, vm: dict, base_path: str) -> Any:
    """`libvirt_volume.overlay`: capacity here and only here (spike A4)."""
    return pool.createXML(
        VOLUME_XML.format(
            name=vm["overlay_name"],
            capacity=vm["disk_bytes"],
            fmt="qcow2",
            backing=BACKING_XML.format(path=base_path),
        ),
        0,
    )


def create(conn: Any, tfvars: dict, sparse: bool) -> dict:
    """The apply. Returns what `outputs.tf` returns, keyed by logical name, and
    prints one timed line per resource so the run is its own evidence.

    Order matches the module's edges: base, then per VM seed, overlay, domain.
    A failure raises out with the resource named; nothing is rolled back, which
    is also what the provider does -- state is what lets tofu resume, and the
    marker plus `preflight.orphan_volumes` is what lets vcows see the leftovers.
    """
    pool = conn.storagePoolLookupByName(tfvars["pool"])
    base = tfvars["base_volume"]
    base_path = base["path"]
    if base["create"]:
        with timed(f"base {base['name']}"):
            base_path = upload(
                conn, pool, base["name"], "qcow2", base["source"], sparse
            ).path()
    vms: dict[str, dict] = {}
    for key, vm in tfvars["vms"].items():
        with timed(f"seed {vm['seed_name']}"):
            seed = upload(conn, pool, vm["seed_name"], "iso", vm["seed_iso"], False)
        with timed(f"overlay {vm['overlay_name']}"):
            disk = overlay(pool, vm, base_path)
        with timed(f"define {vm['domain_name']}"):
            dom = conn.defineXML(domain_xml(vm, disk.path(), seed.path()))
        dom.setAutostart(1)
        with timed(f"start {vm['domain_name']}"):
            dom.create()
        vms[key] = {
            "name": dom.name(),
            "uuid": dom.UUIDString(),
            "configured_address": vm["configured_address"],
            "disks": [disk.path(), seed.path()],
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


# ---- Harness: the shipped code up to the apply, as `cli._deploy` orders it ---


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("config")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sparse", action="store_true")
    args = ap.parse_args(argv)

    cfg, problems = load(args.config, REGISTRY)
    backend = REGISTRY[cfg["backend"]]
    with backend.connect(cfg) as session:
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
    seed = args.run_dir / "seed"
    seed.mkdir(parents=True)
    with backend.prepare(create_cfg, seed, discovered) as prepared:
        tfvars = backend.render(create_cfg, prepared)
        if args.dry_run:
            for vm in tfvars["vms"].values():
                print(domain_xml(vm, "<overlay>", "<seed>"))
            return 0
        import libvirt

        # The C client's scheme, not the provider's `sshcmd`: one connection
        # kind for the whole run, which the provider split in two.
        t0 = time.monotonic()
        conn = libvirt.open(connection_uri(cfg["target"]["libvirt"]))
        try:
            vms = create(conn, tfvars, args.sparse)
        finally:
            conn.close()
        print(f"created {len(vms)} VM(s) in {time.monotonic() - t0:.1f}s")
    (args.run_dir / "inventory.json").write_text(json.dumps({"vms": vms}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
