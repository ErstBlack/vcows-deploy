"""Pure: config plus what ``prepare`` resolved, out to a tfvars dict. No I/O.

Everything OpenTofu needs that is not in the static module comes through here as
*values*. The module itself is hand-written and never generated -- that is what
makes ``tofu validate`` a real gate rather than a check that the generator agrees
with itself.

Two shapes here are dictated by HCL rather than by taste:

* A NIC emits **both** ``network`` and ``bridge`` with the unused one ``null``. A
  ternary between two differently-shaped objects does not type-check in HCL, so
  the shape stays uniform and the choice lives in the values.
* ``loader_readonly`` is the **string** ``"yes"``, not a boolean. The provider's
  generated docs say boolean; ``tofu providers schema -json`` says string, and
  the schema wins. That disagreement is the whole reason spike A6 pinned the
  ground truth (docs/provider-schema-0.9.8.json).
"""

from __future__ import annotations

from typing import Any

from ...marker import Marker
from ..base import Prepared
from .schema import FIRMWARE_DEFAULT, MACHINE_DEFAULT, mac_of, primary_index

# Names are the logical name, undecorated (D16). Maximally predictable for
# hand-debugging at a site, where an operator has the config and `virsh list` and
# nothing else.


def domain_name(vm_name: str) -> str:
    return vm_name


def overlay_name(vm_name: str) -> str:
    return f"{vm_name}.qcow2"


def seed_name(vm_name: str) -> str:
    return f"{vm_name}-seed.iso"


def render(cfg: dict, prepared: Prepared) -> dict[str, Any]:
    target = cfg["target"]["libvirt"]
    base = prepared.artifacts["base_volume"]
    seeds = prepared.artifacts["seed_isos"]

    return {
        "uri": target["uri"],
        "pool": target["pool"],
        "base_volume": {
            "name": base["name"],
            # False once the image is already on this host. The apply runs against
            # a fresh state every time, so without this it would try to create an
            # existing volume on every deploy after the first.
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
        "domain_name": domain_name(name),
        "overlay_name": overlay_name(name),
        "seed_name": seed_name(name),
        "marker_xml": Marker.for_vm(name, cfg["deployment"]).to_xml(),
        "vcpus": vm["vcpus"],
        "memory_mib": vm["memory_mib"],
        # Capacity belongs on the overlay and nowhere else: vol-upload writes the
        # golden image's own header from offset 0 and silently discards whatever
        # capacity the base volume declared. Confirmed in spike A4.
        "disk_bytes": vm["disk_gb"] * 1024**3,
        "seed_iso": seed_iso,
        "firmware": firmware,
        "machine": vm.get("machine", MACHINE_DEFAULT),
        "loader": vm.get("loader"),
        "loader_format": vm.get("loader_format"),
        "nvram_template": vm.get("nvram_template"),
        # The tool never asks libvirt for an address; this is what the config
        # said and what cloud-init was told to configure.
        "address": primary["ip_cidr"].split("/")[0],
        "nics": [_nic(vm, i) for i in range(len(vm["nics"]))],
    }


def _nic(vm: dict, index: int) -> dict[str, Any]:
    nic = vm["nics"][index]
    return {
        "mac": mac_of(vm, index),
        "model": nic.get("model", "virtio"),
        "network": nic.get("network"),
        "bridge": nic.get("bridge"),
    }
