"""The ``target.proxmox`` block and the per-VM shape.

Deliberately *not* a copy of the libvirt schema with words changed. Three things
differ because Proxmox differs, and each one is the seam earning its keep:

* **A NIC attaches to a bridge, and only a bridge.** Proxmox has no equivalent of
  a libvirt network, so the ``bridge``/``network`` union does not exist here and
  ``bridge`` is simply required. There is no union to check, so this backend has
  no attachment check of its own: ``NIC_SCHEMA`` carries the rule and
  ``validate`` calls the shared ``cloudinit.check_addressing`` directly.
* **Firmware is a choice, not a set of host paths.** libvirt needs ``loader``,
  ``loader_format`` and ``nvram_template`` because the operator has to name OVMF
  files that differ per distribution. Proxmox owns its own OVMF and allocates the
  EFI vars disk itself, so ``firmware: efi`` is the whole of it and the three
  libvirt keys are rejected by ``additionalProperties``.
* **A name must be a DNS name.** PVE validates ``name`` as one, so ``_`` is legal
  in a libvirt domain name and illegal here.

**Credentials live in ``target.proxmox``**: an API token, or a user and a
password, and exactly one of the two forms. Nothing carries either onward -- not
``run.json``, not the log. ``validate`` reports the *shape* only: which form is
missing or doubled, or that a token is not in the form a token takes, never any
part of a value -- the shape is enough to say what is wrong.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ...cloudinit import (
    check_addressing,
    check_vm_structure,
    nic_checks_are_safe,
)
from ...imagecheck import check_disk_capacity, check_image_digest
from ...limits import MAX_DISK_GB, MAX_MEMORY_MIB, MAX_VCPUS
from ...problems import Problem

#: PVE validates a VM name as a DNS name, so no underscore -- which libvirt does
#: allow. ``\Z`` rather than ``$`` for the reason the libvirt schema spells out:
#: Python's ``$`` also matches before a trailing newline.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}\Z"

MAC_PATTERN = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\Z"

#: ``ca_cert`` carries the certificate itself, so this asks whether it opens like
#: one. It also catches the mistake worth catching: a *private* key pasted where
#: the public half belongs, which `requests` would reject and which is a
#: credential put into a config for nothing.
CA_CERT_PATTERN = r"^-----BEGIN CERTIFICATE-----"

#: An absolute path with no whitespace -- what ``ca_file`` held at v0.1. Matched
#: only to say the field changed shape. A literal rather than an import of the
#: libvirt backend's: the two fields reach different libraries, and neither
#: backend's rule is the other's to widen.
PATH_PATTERN = re.compile(r"^/[^\s]*\Z")

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
        # The API base URL, https only. The credential is a field of its own
        # below and is never part of this URL.
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
        # Exactly one of `token`, or `user` and `password` -- checked in
        # `_check_auth` rather than as a jsonschema `oneOf`, the way the libvirt
        # backend checks its NIC union in code rather than in its schema.
        "token": {"type": "string", "minLength": 1},
        "user": {"type": "string", "minLength": 1},
        "password": {"type": "string", "minLength": 1},
        # The CA certificate for a PVE certificate signed by a private CA, as
        # PEM. `api.connect` writes it to a file, because proxmoxer hands
        # `verify_ssl` to requests' `verify=`, which wants a path.
        "ca_cert": {"type": "string", "pattern": CA_CERT_PATTERN},
        "insecure": {"type": "boolean"},
    },
}


def token_parts(raw: str) -> re.Match[str] | None:
    """The three fields proxmoxer wants, or None. Never logged, never reported."""
    return TOKEN_PATTERN.match(raw.strip())


def validate(cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
    """Offline checks. No connection, no I/O against the target.

    Returns every problem rather than the first, matching ``config.load``.
    ``verify_digest`` is false only for ``destroy``; see ``Backend.validate``.
    """
    problems: list[Problem] = []
    problems += _check_target(cfg["target"]["proxmox"])
    problems += _check_auth(cfg["target"]["proxmox"])

    seen_ips: dict[str, str] = {}
    seen_macs: dict[str, str] = {}
    for i, vm in enumerate(cfg["vms"]):
        where = f"vms[{i}]"
        structural = check_vm_structure(vm, where, VM_SCHEMA)
        problems += structural
        if structural and not nic_checks_are_safe(vm, structural):
            continue
        problems += check_addressing(vm, where, seen_ips, seen_macs, cfg["deployment"])

    problems += check_disk_capacity(cfg)
    if verify_digest:
        problems += check_image_digest(cfg)
    problems += _check_image_name(cfg)
    return problems


def _check_auth(target: dict) -> list[Problem]:
    """One credential form, and shaped like one. **No value ever appears.**

    An offline check because it is one: this reads three fields and matches a
    regex, and getting a token's shape wrong is the single most likely reason a
    first run against a new cluster fails. Catching it in `vcows validate` costs
    nothing and saves a round trip to a site.

    Whether the credential is *valid*, and whether it carries the privileges to
    upload, create and delete, is a question only the cluster can answer --
    `preflight` asks it.
    """
    where = "target.proxmox"
    token = target.get("token")
    user = target.get("user")
    password = target.get("password")
    if bool(token) == bool(user or password):
        # Neither form, or both of them. Filed against the block rather than a
        # field, because there is no one field to go and fix.
        return [
            Problem.error(
                "exactly one of `token`, or `user` and `password`, is required. "
                "A token is 'user@realm!tokenid=<secret>'; a user is a PVE login "
                "such as 'root@pam', with its password beside it.",
                where=where,
            )
        ]
    if token:
        if token_parts(token) is None:
            return [
                Problem.error(
                    "not in the form 'user@realm!tokenid=<secret>'. It must "
                    "carry the realm (for example 'vcows@pve'), then '!', the "
                    "token id, then '=' and the secret. The value is not shown "
                    "here.",
                    where=f"{where}.token",
                )
            ]
        return []
    if not password:
        return [
            Problem.error(
                "`user` is set, so the password that goes with it is required.",
                where=f"{where}.password",
            )
        ]
    if not user:
        return [
            Problem.error(
                "`password` is set, so the user it belongs to is required -- a "
                "PVE login such as 'root@pam'.",
                where=f"{where}.user",
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
                "endpoint must carry no credentials. Authentication is the "
                "token, or the user and password, under target.proxmox; "
                "anything here would be written to the run directory in "
                "plaintext.",
                where=where,
            )
        )

    ca_cert = target.get("ca_cert")
    if ca_cert is not None and target.get("insecure"):
        problems.append(
            Problem.error(
                "ca_cert and insecure: true contradict each other. One is the CA "
                "that must have signed the certificate, the other checks no "
                "certificate at all. Drop whichever was not meant.",
                where="target.proxmox.ca_cert",
            )
        )
    # The v0.1 shape, `ca_file: /run/secrets/pve-ca.pem`. An error rather than a
    # warning, for the same reason the libvirt backend errors on one: there is no
    # compatibility path and nothing is mounted for it any more.
    if isinstance(ca_cert, str) and PATH_PATTERN.match(ca_cert):
        problems.append(
            Problem.error(
                "ca_cert is the certificate itself now, not a path to it. Paste "
                "the PEM in -- nothing is mounted for it.",
                where="target.proxmox.ca_cert",
            )
        )

    if target.get("insecure"):
        problems.append(
            Problem.warning(
                "certificate verification is disabled. The credential under "
                "target.proxmox is sent to whatever answers at this endpoint.",
                where="target.proxmox.insecure",
            )
        )
    return problems


def _check_image_name(cfg: dict) -> list[Problem]:
    """The uploaded image's filename.

    A warning, because it is a claim about how a remote PVE validates an upload,
    which has not been measured against a live one.
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
    return problems
