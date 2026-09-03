"""Pure: config plus what ``prepare`` resolved, out to a values dict. No I/O.

Everything ``create`` needs that is not in its request bodies comes through here
as *values*, which is what keeps the whole config-to-values step testable with
no cluster: it is compared against a golden file byte for byte.

**No credential is rendered.** ``api.connect`` reads ``PROXMOX_VE_API_TOKEN``
from the environment itself, so nothing in this dict is a secret and it can be
read by whoever is debugging the run.

Two shapes are dictated by PVE rather than by taste:

* ``bios`` is PVE's vocabulary (``ovmf``/``seabios``), translated here from the
  config's ``efi``/``bios``, so one operator reads both backends' configs.
* ``vlan_id`` is emitted as ``null`` rather than omitted, because a map of
  objects in HCL must have a uniform shape -- the same reason the libvirt
  backend emits both halves of its NIC union.
"""

from __future__ import annotations

from typing import Any

from ...cloudinit import mac_of, primary_index, seed_name
from ...marker import Marker
from ..base import Prepared
from .schema import BIOS, FIRMWARE_DEFAULT, MACHINE_DEFAULT, OS_TYPE_DEFAULT


def render(cfg: dict, prepared: Prepared) -> dict[str, Any]:
    target = cfg["target"]["proxmox"]
    image = prepared.artifacts["image"]
    seeds = prepared.artifacts["seed_isos"]

    return {
        "endpoint": target["endpoint"],
        "insecure": bool(target.get("insecure", False)),
        "node": target["node"],
        "datastore": target["datastore"],
        "import_datastore": target["import_datastore"],
        "image": {
            "file_name": cfg["image"]["base_volume_name"],
            # False once the image is on this cluster. The apply runs against a
            # fresh state every time, so without this it would re-upload a
            # multi-GB image on every deploy after the first.
            "create": image["create"],
            # PVE's own id for the file. Used as `import_from` when not creating,
            # and as the expected id when creating.
            "volid": image["volid"],
            "source": cfg["image"]["source_qcow2"] if image["create"] else "",
            # The provider verifies this after upload when it is set. Optional in
            # the config, so empty means "not declared" rather than "no checksum".
            "checksum": cfg["image"].get("sha256", ""),
        },
        "vms": {vm["name"]: _vm(vm, cfg, seeds[vm["name"]]) for vm in cfg["vms"]},
    }


def _vm(vm: dict, cfg: dict, seed_iso: str) -> dict[str, Any]:
    name = vm["name"]
    primary = vm["nics"][primary_index(vm)]
    return {
        # The logical name, undecorated -- the same rule the libvirt backend
        # follows (D16), and for the same reason: maximally predictable for
        # hand-debugging at a site, where an operator has the config and the PVE
        # UI.
        "vm_name": name,
        "seed_name": seed_name(name),
        # The durable record of what vcows created, in the only per-VM free-text
        # field PVE has. `destroy` discovers by this, not by the state file.
        "description": Marker.for_vm(name, cfg["deployment"]).to_description(),
        "vcpus": vm["vcpus"],
        "memory_mib": vm["memory_mib"],
        "disk_gb": vm["disk_gb"],
        "bios": BIOS[vm.get("firmware", FIRMWARE_DEFAULT)],
        "machine": vm.get("machine", MACHINE_DEFAULT),
        "os_type": vm.get("os_type", OS_TYPE_DEFAULT),
        # Local path to this VM's cidata ISO, built by orchestrator/cloudinit.py.
        "seed_iso": seed_iso,
        # What the config said, for the inventory. The tool never asks PVE what
        # address a guest came up on, and the name carries that distinction.
        "configured_address": primary["ip_cidr"].split("/")[0],
        "nics": [_nic(vm, i, cfg["deployment"]) for i in range(len(vm["nics"]))],
    }


def _nic(vm: dict, index: int, deployment: str) -> dict[str, Any]:
    nic = vm["nics"][index]
    return {
        "mac": mac_of(vm, index, deployment),
        "model": nic.get("model", "virtio"),
        "bridge": nic["bridge"],
        "vlan_id": nic.get("vlan_id"),
    }
