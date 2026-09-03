"""The ``target.proxmox`` block and the per-VM shape.

Deliberately *not* a copy of the libvirt schema with words changed. Three things
differ because Proxmox differs, and each one is the seam earning its keep:

* **A NIC attaches to a bridge, and only a bridge.** Proxmox has no equivalent of
  a libvirt network, so the ``bridge``/``network`` union does not exist here and
  ``bridge`` is simply required. That is why ``cloudinit.check_addressing`` holds
  the addressing checks and each backend keeps its own attachment rule.
* **Firmware is a choice, not a set of host paths.** libvirt needs ``loader``,
  ``loader_format`` and ``nvram_template`` because the operator has to name OVMF
  files that differ per distribution. Proxmox owns its own OVMF and allocates the
  EFI vars disk itself, so ``firmware: efi`` is the whole of it and the three
  libvirt keys are rejected by ``additionalProperties``.
* **A name must be a DNS name.** PVE validates ``name`` as one, so ``_`` is legal
  in a libvirt domain name and illegal here.

**Credentials are not in this file's schema and never in the config.** The API
token arrives in ``PROXMOX_VE_API_TOKEN`` and nothing writes it anywhere: not the
config, not ``run.json``, not the log. ``validate`` checks that it is *present
and well formed* and reports neither its value nor any part of it -- the shape
of a token is enough to say what is wrong with it.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

from ...cloudinit import (
    check_addressing,
    check_vm_structure,
    nic_checks_are_safe,
    seed_name,
)
from ...imagecheck import check_disk_capacity, check_image_digest
from ...limits import MAX_DISK_GB, MAX_MEMORY_MIB, MAX_VCPUS
from ...problems import Problem

#: PVE validates a VM name as a DNS name, so no underscore -- which libvirt does
#: allow. ``\Z`` rather than ``$`` for the reason the libvirt schema spells out:
#: Python's ``$`` also matches before a trailing newline.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}\Z"

MAC_PATTERN = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\Z"

#: **There is deliberately no ``ca_file``.** A private CA goes in the environment
#: instead, where proxmoxer already looks: ``REQUESTS_CA_BUNDLE`` (requests). One
#: mechanism, and the container can set it once.

#: **The one place the token's variable name is written.**
#: S105 is a false positive here: this is the *name* of an environment
#: variable, not a credential. The value it names is never assigned in this repo.
TOKEN_ENV = "PROXMOX_VE_API_TOKEN"  # noqa: S105

#: ``user@realm!tokenid=secret``. The secret half is matched but never captured
#: into a message.
TOKEN_PATTERN = re.compile(
    r"^(?P<user>[^\s@!=]+@[^\s@!=]+)!(?P<name>[^\s!=]+)=(?P<secret>\S+)\Z"
)

#: PVE stores an imported disk under the ``import`` content type, which accepts
#: disk images by extension. Not an error when it is something else: this is a
#: claim about a remote PVE's validation that has not been measured against one,
#: and `validate` is the offline phase.
IMPORT_SUFFIXES = (".qcow2", ".raw", ".vmdk")

FIRMWARE_DEFAULT = "efi"
MACHINE_DEFAULT = "q35"
OS_TYPE_DEFAULT = "l26"

#: What PVE calls the two firmwares. The config keeps libvirt's vocabulary so one
#: operator reads both backends' configs; the translation happens in `render`.
BIOS = {"efi": "ovmf", "bios": "seabios"}

NIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # `bridge` is required rather than being half of a union: Proxmox attaches a
    # NIC to a Linux or OVS bridge and has nothing else to attach it to.
    "required": ["bridge", "ip_cidr", "gateway"],
    "properties": {
        "bridge": {"type": "string", "minLength": 1},
        "ip_cidr": {"type": "string", "minLength": 1},
        "gateway": {"type": "string", "minLength": 1},
        "nameservers": {"type": "array", "items": {"type": "string"}},
        "mac": {"type": "string", "pattern": MAC_PATTERN},
        "model": {"enum": ["virtio", "e1000", "rtl8139", "vmxnet3"]},
        "primary": {"type": "boolean"},
        "vlan_id": {"type": "integer", "minimum": 1, "maximum": 4094},
    },
}

VM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "vcpus", "memory_mib", "disk_gb", "nics"],
    "properties": {
        "name": {"type": "string", "pattern": NAME_PATTERN},
        "vcpus": {"type": "integer", "minimum": 1, "maximum": MAX_VCPUS},
        "memory_mib": {"type": "integer", "minimum": 256, "maximum": MAX_MEMORY_MIB},
        "disk_gb": {"type": "integer", "minimum": 1, "maximum": MAX_DISK_GB},
        # No loader/loader_format/nvram_template: `additionalProperties: False`
        # rejects them, which is the intended message. Proxmox ships its own OVMF
        # and allocates the vars disk itself.
        "firmware": {"enum": ["efi", "bios"]},
        "machine": {"type": "string", "minLength": 1},
        "os_type": {"type": "string", "minLength": 1},
        "user_data": {"type": "string"},
        "nics": {"type": "array", "minItems": 1, "items": NIC_SCHEMA},
    },
}

TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["endpoint", "node", "datastore", "import_datastore"],
    "properties": {
        # The API base URL, https only. No credentials in it -- see TOKEN_ENV.
        "endpoint": {"type": "string", "minLength": 1},
        # Which node to create on. Single-node at v0.1: a cluster-wide scheduler
        # is a decision nobody has made, and picking one silently is worse than
        # naming it.
        "node": {"type": "string", "minLength": 1},
        # Where VM disks land -- typically an LVM-thin or ZFS store.
        "datastore": {"type": "string", "minLength": 1},
        # Where the golden image and the seed ISOs are uploaded. Must allow both
        # the `import` and `iso` content types; preflight checks and says so.
        "import_datastore": {"type": "string", "minLength": 1},
        "insecure": {"type": "boolean"},
    },
}


def token_parts(raw: str) -> re.Match[str] | None:
    """The three fields proxmoxer wants, or None. Never logged, never reported."""
    return TOKEN_PATTERN.match(raw.strip())


def validate(cfg: dict) -> list[Problem]:
    """Offline checks. No connection, no I/O against the target.

    Returns every problem rather than the first, matching ``config.load``.
    """
    problems: list[Problem] = []
    problems += _check_target(cfg["target"]["proxmox"])
    problems += _check_token()

    seen_ips: dict[str, str] = {}
    seen_macs: dict[str, str] = {}
    for i, vm in enumerate(cfg["vms"]):
        where = f"vms[{i}]"
        structural = check_vm_structure(vm, where, VM_SCHEMA)
        problems += structural
        if structural and not nic_checks_are_safe(vm, structural):
            continue
        problems += _check_nics(vm, where, seen_ips, seen_macs, cfg["deployment"])

    problems += check_disk_capacity(cfg)
    problems += check_image_digest(cfg)
    problems += _check_image_name(cfg)
    return problems


def _check_token() -> list[Problem]:
    """The token is present and shaped like a token. **Its value never appears.**

    An offline check because it is one: this reads an environment variable and
    matches a regex, and getting it wrong is the single most likely reason a
    first run against a new cluster fails. Catching it in `vcows validate` costs
    nothing and saves a round trip to a site.

    Whether the token is *valid*, and whether it carries the privileges to
    upload, create and delete, is a question only the cluster can answer --
    `preflight` asks it.
    """
    raw = os.environ.get(TOKEN_ENV)
    where = TOKEN_ENV
    if not raw:
        return [
            Problem.error(
                f"{TOKEN_ENV} is unset. The Proxmox backend authenticates with an "
                f"API token and reads it from this variable only -- it is never "
                f"written in the config. Export it as "
                f"'user@realm!tokenid=<secret>'.",
                where=where,
            )
        ]
    if token_parts(raw) is None:
        return [
            Problem.error(
                f"{TOKEN_ENV} is set but is not in the form "
                f"'user@realm!tokenid=<secret>'. It must carry the realm (for "
                f"example 'vcows@pve'), then '!', the token id, then '=' and the "
                f"secret. The value is not shown here.",
                where=where,
            )
        ]
    return []


def _check_target(target: dict) -> list[Problem]:
    """The endpoint is ours to use as a base URL, not the operator's to decorate."""
    where = "target.proxmox.endpoint"
    endpoint = target["endpoint"]
    try:
        parts = urlsplit(endpoint)
    except ValueError as exc:
        # Same early return as the libvirt backend's: every check below reads
        # `parts`, and an unhandled ValueError here would unwind past
        # `config.load`'s every-problem contract.
        return [
            Problem.error(
                f"{endpoint!r} is not a URL ({exc}); vcows builds the API base "
                f"URL from this field and cannot parse it",
                where=where,
            )
        ]

    problems: list[Problem] = []
    if parts.scheme != "https":
        problems.append(
            Problem.error(
                f"scheme must be 'https', got {parts.scheme or '<none>'!r}. The "
                f"API token is a bearer credential and travels in a header; "
                f"plaintext http would put it on the wire.",
                where=where,
            )
        )
    if not parts.hostname:
        problems.append(Problem.error(f"no host in {endpoint!r}", where=where))
    if parts.path not in ("", "/", "/api2/json", "/api2/json/"):
        problems.append(
            Problem.error(
                f"path must be empty or '/', got {parts.path!r}. This is the API "
                f"base URL; vcows appends the API path itself.",
                where=where,
            )
        )
    if parts.query:
        problems.append(
            Problem.error(
                f"endpoint must carry no query string, got {parts.query!r}",
                where=where,
            )
        )
    if parts.username is not None or parts.password is not None:
        # Same refusal, and the same reason, as the libvirt backend's password
        # check.
        problems.append(
            Problem.error(
                f"endpoint must carry no credentials. Authentication is the "
                f"{TOKEN_ENV} API token; anything here would be written to the "
                f"run directory in plaintext.",
                where=where,
            )
        )

    if target.get("insecure"):
        problems.append(
            Problem.warning(
                "certificate verification is disabled. The API token is sent to "
                "whatever answers at this endpoint.",
                where="target.proxmox.insecure",
            )
        )
    return problems


def _check_nics(
    vm: dict,
    where: str,
    seen_ips: dict[str, str],
    seen_macs: dict[str, str],
    deployment: str,
) -> list[Problem]:
    """The attachment rule is the schema's job here, so this is the shared half.

    `bridge` is `required` in NIC_SCHEMA, so a missing one is already a
    structural problem with a readable message. There is no union to check, which
    is the whole difference from the libvirt backend.
    """
    return check_addressing(vm, where, seen_ips, seen_macs, deployment)


def _check_image_name(cfg: dict) -> list[Problem]:
    """The uploaded image's filename, and the one name a seed ISO must not have.

    Both are warnings. The first is a claim about how a remote PVE validates an
    upload, which has not been measured against a live one; the second cannot
    currently fire, and says so, because it is the check that would catch it if
    `cloudinit.seed_name` ever changed.
    """
    problems: list[Problem] = []
    name = cfg["image"]["base_volume_name"]
    if not name.endswith(IMPORT_SUFFIXES):
        problems.append(
            Problem.warning(
                f"{name!r} does not end in one of {', '.join(IMPORT_SUFFIXES)}. "
                f"PVE stores this under the 'import' content type, which "
                f"recognises a disk image by extension, and may refuse the "
                f"upload.",
                where="image.base_volume_name",
            )
        )
    for i, vm in enumerate(cfg["vms"]):
        if not isinstance(vm, dict) or not isinstance(vm.get("name"), str):
            continue
        if re.match(r"^vm-\d+-cloudinit\.iso\Z", seed_name(vm["name"])):
            problems.append(
                Problem.warning(
                    f"{vm['name']!r} derives a seed ISO named "
                    f"{seed_name(vm['name'])!r}. Proxmox pattern-matches that "
                    f"name, assumes it owns the file, and fails the VM's start "
                    f"task trying to regenerate it.",
                    where=f"vms[{i}].name",
                )
            )
    return problems
