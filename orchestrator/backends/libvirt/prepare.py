"""The NoCloud seed ISO. One per VM, built with pycdlib.

Split pure/impure on purpose: ``seed_files`` decides what cloud-init is told and
is testable without touching a filesystem, ``build_seed_iso`` only writes it out.

**vcows owns ``meta-data`` and ``network-config``; the operator owns ``user-data``
verbatim.** Identity and addressing are derived from fields vcows already has, and
the config's ``user_data`` string is passed through with no interpretation. That
is what keeps a ``users:``/``ssh_keys:``/``packages:`` schema -- and the merge
semantics that would come with it -- out of v0.1 entirely.

``libvirt_cloudinit_disk`` is deliberately unused: it stages the ISO in
``os.TempDir()`` and calls ``RemoveResource()`` when the file is missing, so a
container's empty ``/tmp`` makes the ISO, its volume, and the domain all look like
they need recreating on every run (findings.md F2, upstream issue #1368).
"""

from __future__ import annotations

import io
from pathlib import Path

import yaml

from .schema import mac_of, primary_index

#: Both cloud-init and libvirt find a NoCloud datasource by this volume label.
VOLUME_LABEL = "cidata"

#: Exactly the settings spike A1 verified, cross-read against xorrisofs output.
ISO_ARGS = {
    "interchange_level": 3,
    "joliet": 3,
    "rock_ridge": "1.09",
    "vol_ident": VOLUME_LABEL,
}


def seed_files(vm: dict, cfg: dict) -> dict[str, bytes]:
    """The three files cloud-init reads off the ISO."""
    from ...marker import derive_id

    name = vm["name"]
    meta = {
        # The marker's own derived id. Stable with no state file, so cloud-init's
        # per-instance modules do not re-run on a reboot.
        "instance-id": derive_id(name, cfg["deployment"]),
        "local-hostname": name,
    }
    user_data = vm.get("user_data")
    if user_data is None:
        user_data = f"#cloud-config\nhostname: {name}\n"

    return {
        "meta-data": yaml.safe_dump(meta, sort_keys=False).encode(),
        "user-data": user_data.encode(),
        "network-config": yaml.safe_dump(
            _network_config(vm, cfg["deployment"]), sort_keys=False
        ).encode(),
    }


def _network_config(vm: dict, deployment: str) -> dict:
    """NoCloud network-config v2, matching each interface by MAC.

    Matching by MAC rather than by name is why the MAC has to be derived at render
    time: interface names are assigned by the guest kernel and are not knowable
    from here.

    The default route is written as ``0.0.0.0/0``, **not** netplan's ``default``.
    That distinction cost the first acceptance run: cloud-init 24.4 accepts the
    document, reads it, logs "Applying network configuration from ds", and then
    throws ``ValueError: Address default is not a valid ip address`` out of its
    own v2-to-v1 route normaliser. `default` is a netplan idiom that cloud-init's
    parser does not implement, and the failure is the worst shape available: the
    guest boots, falls back to DHCP, and comes up healthy on an address nobody
    asked for. ``gateway4`` would also work and is deprecated; a CIDR is neither.

    **Only the primary NIC gets it.** One default route per NIC leaves a
    multi-NIC guest choosing its egress by metric, which is the same shape of
    failure: it boots, it routes, and it routes somewhere nobody chose.
    ``gateway`` stays required on every NIC even so -- it is what the address is
    checked against, and making a required field optional later is the
    backward-compatible direction.

    **The v6 half is not configured.** ``dhcp6`` is off and the default route is
    hardcoded to ``0.0.0.0/0``, so this emits a v4 document and nothing else. The
    schema is wider than that: ``_parse_interface`` takes both families and the
    network/broadcast check reasons about ``/127`` and ``/128``, so a v6
    ``ip_cidr`` validates cleanly, reaches here, and produces a guest with an
    address and no route. That is a gap, not a rejection -- closing it means
    deciding what a dual-stack primary means for ``configured_address`` and for
    ``address_conflicts``, which is more than a second route literal.

    **The guest's interfaces are renamed.** The keys are ``nic0``, ``nic1``, and
    cloud-init renames each matched interface to its key, so nothing in the
    golden image can rely on ``eth0`` or a predictable kernel name.
    """
    default_route = primary_index(vm)
    ethernets = {}
    for i, nic in enumerate(vm["nics"]):
        entry: dict = {
            "match": {"macaddress": mac_of(vm, i, deployment)},
            "dhcp4": False,
            "dhcp6": False,
            "addresses": [nic["ip_cidr"]],
        }
        if i == default_route:
            entry["routes"] = [{"to": "0.0.0.0/0", "via": nic["gateway"]}]
        if nic.get("nameservers"):
            entry["nameservers"] = {"addresses": list(nic["nameservers"])}
        ethernets[f"nic{i}"] = entry
    return {"version": 2, "ethernets": ethernets}


def build_seed_iso(files: dict[str, bytes], out: Path) -> Path:
    """Write one ``cidata`` ISO. Overwrites; the run directory is ours.

    **The bytes are not reproducible, the content is.** pycdlib stamps
    ``time.time()`` into the volume descriptors and into every directory record,
    and ``new()`` takes no date argument, so pinning them would mean freezing a
    process-global clock for the duration of the write. Two builds of one input
    therefore carry identical files and differ in a few dozen bytes, which is what
    ``tests/test_seed_iso.py`` asserts.
    """
    import pycdlib

    out.unlink(missing_ok=True)
    iso = pycdlib.PyCdlib()
    iso.new(**ISO_ARGS)
    try:
        for name in sorted(files):
            blob = files[name]
            iso.add_fp(
                io.BytesIO(blob),
                len(blob),
                iso_path(name),
                rr_name=name,
                joliet_path=f"/{name}",
            )
        iso.write(str(out))
    finally:
        iso.close()
    return out


def iso_path(name: str) -> str:
    """The ISO 9660 identifier for a cloud-init filename.

    cloud-init reads the Joliet or Rock Ridge name, never this one, but every file
    needs a conforming 9660 identifier underneath.
    """
    return f"/{name.upper().replace('-', '_')}.;1"


def build_all(cfg: dict, workdir: Path) -> dict[str, str]:
    """One ISO per VM in ``cfg``, named for the VM. Returns name -> path.

    The caller passes a config whose ``vms`` is already narrowed to what will be
    created, so this never builds a seed for a VM that already exists.
    """
    return {
        vm["name"]: str(
            build_seed_iso(seed_files(vm, cfg), workdir / f"{vm['name']}-seed.iso")
        )
        for vm in cfg["vms"]
    }
