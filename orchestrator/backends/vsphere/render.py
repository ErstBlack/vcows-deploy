"""Pure: config plus what ``prepare`` resolved, out to a values dict. No I/O.

The same treatment the other two backends' renders get, and for the same reason:
everything ``create`` needs that is not in an SDK call comes through here as
*values*, so the whole config-to-values step is compared against a golden file
byte for byte with no vCenter anywhere.

**No credential is rendered**, and no managed object can be. ``api.connect``
reads the user and the password out of ``target.vsphere`` itself and nothing from
that block is copied here; ``preflight`` puts names and booleans into
``Discovered.artifacts`` and never an object, which is what leaves this function
able to be pure at all.

Two values come from ``prepare`` rather than from the config, and both are empty
when the template is already on the vCenter: ``vmdk`` is the file ``prepare``
converted, and ``capacity`` is the golden image's virtual size, read there
because reading it here would be the one piece of I/O this module refuses. They
are what the import needs, so they exist exactly when the import does.

``firmware`` is not translated. vSphere's own vocabulary for it is ``efi`` and
``bios``, which is what the config already says -- unlike Proxmox, whose
``ovmf``/``seabios`` appear only in that backend's render.
"""

from __future__ import annotations

from typing import Any

from ...cloudinit import mac_of, primary_index, seed_name
from ...marker import Marker
from .schema import (
    CLONE_DEFAULT,
    FIRMWARE_DEFAULT,
    IMPORT_DEFAULT,
    NIC_MODEL_DEFAULT,
)


def render(cfg: dict, prepared: dict[str, Any]) -> dict[str, Any]:
    target = cfg["target"]["vsphere"]
    image = prepared["image"]
    seeds = prepared["seed_isos"]

    return {
        "image": {
            # False once a template of ours is on this vCenter. A linked clone of
            # it moves no bytes, so without this every deploy after the first
            # would convert and upload a multi-GB image again.
            "create": image["create"],
            # The template VM's name either way: what the import marks as a
            # template, or what the clones come from.
            "template": image["template"],
            # The converted file in the run directory, and its virtual size in
            # bytes for the OVF descriptor. Empty and 0 when nothing is imported.
            "vmdk": prepared.get("vmdk", ""),
            "capacity": prepared.get("capacity", 0),
            "import": target.get("import", IMPORT_DEFAULT),
        },
        "vms": {vm["name"]: _vm(vm, cfg, seeds[vm["name"]]) for vm in cfg["vms"]},
    }


def _vm(vm: dict, cfg: dict, seed_iso: str) -> dict[str, Any]:
    name = vm["name"]
    target = cfg["target"]["vsphere"]
    primary = vm["nics"][primary_index(vm)]
    return {
        # The logical name, undecorated -- no folder path and no prefix. What
        # `base.Existing` requires: `decide`'s name-clash refusal compares the
        # name vCenter reports against the config's logical name, and a
        # transformed name there compares two different strings.
        "vm_name": name,
        # The durable record of what vcows created, in the field vCenter shows as
        # a VM's notes. `destroy` discovers by this, not by the state file.
        "annotation": Marker.for_vm(name, cfg["deployment"]).to_description(),
        "vcpus": vm["vcpus"],
        "memory_mib": vm["memory_mib"],
        "disk_gb": vm["disk_gb"],
        "firmware": vm.get("firmware", FIRMWARE_DEFAULT),
        # A target knob, rendered per VM because that is where it is used: one
        # clone call reads one shape.
        "clone": target.get("clone", CLONE_DEFAULT),
        # Local path to this VM's cidata ISO, built by orchestrator/cloudinit.py,
        # and the name it is uploaded under: `[ds] vcows/<vm>/<seed_name>`.
        "seed_iso": seed_iso,
        "seed_name": seed_name(name),
        # What the config said, for the inventory. The tool never asks vCenter
        # what address a guest came up on, and the name carries that distinction.
        "configured_address": primary["ip_cidr"].split("/")[0],
        "nics": [_nic(vm, i, cfg) for i in range(len(vm["nics"]))],
    }


def _nic(vm: dict, index: int, cfg: dict) -> dict[str, Any]:
    """The port group is the target's, once, for every NIC.

    A NIC names no network on this backend -- the schema refuses the key -- so
    the one under ``target.vsphere`` is rendered onto each of them rather than
    read from the config a second time in ``create``.
    """
    nic = vm["nics"][index]
    return {
        "network": cfg["target"]["vsphere"]["network"],
        "mac": mac_of(vm, index, cfg["deployment"]),
        "model": nic.get("model", NIC_MODEL_DEFAULT),
    }
