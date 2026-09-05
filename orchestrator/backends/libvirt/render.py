"""Pure: config plus what ``prepare`` resolved, out to a values dict. No I/O.

Everything ``create`` needs that is not in its XML templates comes through here
as *values*, which is what keeps the whole config-to-values step testable with
no hypervisor: it is compared against a golden file byte for byte.
"""

from __future__ import annotations

from typing import Any

from ...cloudinit import mac_of, primary_index, seed_name
from ...marker import Marker
from .schema import FIRMWARE_DEFAULT, MACHINE_DEFAULT

# Names are the logical name, undecorated. Maximally predictable for
# hand-debugging at a site, where an operator has the config and `virsh list` and
# nothing else.


def overlay_name(vm_name: str) -> str:
    return f"{vm_name}.qcow2"


def render(cfg: dict, prepared: dict[str, Any]) -> dict[str, Any]:
    target = cfg["target"]["libvirt"]
    base = prepared["base_volume"]
    seeds = prepared["seed_isos"]

    return {
        "pool": target["pool"],
        "base_volume": {
            "name": base["name"],
            # False once the image is already on this host. `create` only ever
            # creates, so without this it would try to create an existing volume
            # on every deploy after the first.
            "create": base["create"],
            # Empty when creating; the pool's own path for it when not.
            "path": base["path"],
            "source": cfg["image"]["source_qcow2"] if base["create"] else "",
        },
        "vms": {vm["name"]: _vm(vm, cfg, seeds[vm["name"]]) for vm in cfg["vms"]},
    }


def _vm(vm: dict, cfg: dict, seed_iso: str) -> dict[str, Any]:
    name = vm["name"]
    firmware = vm.get("firmware", FIRMWARE_DEFAULT)
    primary = vm["nics"][primary_index(vm)]
    return {
        # The logical name itself, undecorated. `decide`'s name-clash refusal
        # compares `Existing.name` against the config's logical name, and that
        # comparison only means anything while these two are the same string.
        "domain_name": name,
        "overlay_name": overlay_name(name),
        "seed_name": seed_name(name),
        "marker_xml": Marker.for_vm(name, cfg["deployment"]).to_xml(),
        "vcpus": vm["vcpus"],
        "memory_mib": vm["memory_mib"],
        # Capacity belongs on the overlay and nowhere else: vol-upload writes the
        # golden image's own header from offset 0 and silently discards whatever
        # capacity the base volume declared.
        "disk_bytes": vm["disk_gb"] * 1024**3,
        "seed_iso": seed_iso,
        "firmware": firmware,
        "machine": vm.get("machine", MACHINE_DEFAULT),
        "loader": vm.get("loader"),
        "loader_format": vm.get("loader_format"),
        "nvram_template": vm.get("nvram_template"),
        # The tool never asks libvirt for an address; this is what the config
        # said and what cloud-init was told to configure. The name is the
        # inventory's, and it carries that distinction the whole way through.
        "configured_address": primary["ip_cidr"].split("/")[0],
        "nics": [_nic(vm, i, cfg["deployment"]) for i in range(len(vm["nics"]))],
    }


def _nic(vm: dict, index: int, deployment: str) -> dict[str, Any]:
    nic = vm["nics"][index]
    return {
        "mac": mac_of(vm, index, deployment),
        "model": nic.get("model", "virtio"),
        # The config names either a `network` or a `bridge`; the choice is made
        # here, and `create.domain_xml` spells both into the same two slots.
        "kind": "network" if nic.get("network") else "bridge",
        "source": nic.get("network") or nic.get("bridge"),
    }
