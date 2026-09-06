"""The ``target.vsphere`` block and the per-VM shape.

Written against what vSphere does, not copied from the Proxmox schema with words
changed. Four things differ, and each one is the seam earning its keep:

* **A NIC names no network.** vSphere attaches a NIC to a port group, and the
  session resolves exactly one -- ``target.vsphere.network`` -- so there is
  nothing per-NIC to attach and no union to check. ``bridge`` and ``network`` on
  a NIC are rejected by ``additionalProperties``, which is the intended message
  for a config carried over from another backend.
* **No ``vlan_id``.** The port group carries the VLAN on vSphere; a tag on the
  NIC would be a value nothing could send anywhere.
* **The credential is a user and a password, and nothing else.** vCenter has no
  API-token form to offer, so both are simply required and there is no shape
  check to make. What the password buys is a SOAP session, and its cookie is
  what authorises the datastore uploads as well.
* **Where a VM lands is a cluster or a host, exactly one.** vCenter resolves a
  clone's placement from one of them, and picking either silently when both or
  neither are given is the mistake worth refusing.

The second rule of this backend's own is the **linked-clone disk size**: a
linked clone's delta disk cannot be extended, so ``disk_gb`` above the image's
virtual size cannot be honoured. It is an error rather than a silent shrink, and
it applies only under the default ``clone: linked``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from ... import qcow2
from ...cloudinit import (
    check_addressing,
    check_vm_structure,
    nic_checks_are_safe,
)
from ...imagecheck import check_disk_capacity, check_image_digest
from ...limits import MAX_DISK_GB, MAX_MEMORY_MIB, MAX_VCPUS
from ...problems import Problem

#: vSphere itself validates a VM name barely at all -- 80 characters, spaces
#: included -- so this rule is ours rather than the hypervisor's. The name
#: becomes the guest's cloud-init ``local-hostname`` and the datastore folder
#: holding its seed ISO, and a space or a slash in either is a defect looking for
#: somewhere to happen. ``\Z`` rather than ``$``, for the reason the libvirt
#: schema spells out: Python's ``$`` also matches before a trailing newline.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}\Z"

MAC_PATTERN = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\Z"

#: ``ca_cert`` carries the certificate itself, so this asks whether it opens like
#: one -- ``api.connect`` builds an ``ssl`` context out of the text and a body
#: that is not a certificate raises there, naming no config field. It also
#: catches the mistake worth catching: a *private* key pasted where the public
#: half belongs, a credential put into a config for nothing.
CA_CERT_PATTERN = r"^-----BEGIN CERTIFICATE-----"

#: An absolute path with no whitespace. Matched only to reject it: ``ca_cert``
#: carries the PEM itself and nothing is mounted for a path.
PATH_PATTERN = re.compile(r"^/[^\s]*\Z")

#: How the golden image reaches the datastore. ``ovf`` uploads a
#: streamOptimized VMDK through an ``ImportVApp`` lease; ``datastore`` PUTs a
#: monolithicFlat descriptor and its extent. Both are first-contact knobs, so
#: they are config rather than a constant: a delivered bundle switches paths
#: without a rebuild.
IMPORT_DEFAULT = "ovf"

#: ``linked`` clones from the template's snapshot and moves no bytes; ``full``
#: copies the disk and can then be grown. The disk rule below applies to the
#: default only.
CLONE_DEFAULT = "linked"

NIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # No attachment key: the port group is named once, under
    # `target.vsphere.network`, because the session resolves one network.
    "required": ["ip_cidr", "gateway"],
    "properties": {
        "ip_cidr": {"type": "string", "minLength": 1},
        "gateway": {"type": "string", "minLength": 1},
        "nameservers": {"type": "array", "items": {"type": "string"}},
        "mac": {"type": "string", "pattern": MAC_PATTERN},
        # The three adapters vSphere offers for a Linux guest. `virtio` and
        # `rtl8139` are Proxmox's and are refused here rather than accepted and
        # dropped on the floor.
        "model": {"enum": ["vmxnet3", "e1000", "e1000e"]},
        "primary": {"type": "boolean"},
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
        # No `machine` and no `os_type`: q35 is a QEMU machine type and `l26` is
        # Proxmox's vocabulary, and a clone takes its hardware version and its
        # guest id from the template. `additionalProperties: False` refuses both,
        # which is a better answer than accepting a value nothing sends.
        "firmware": {"enum": ["efi", "bios"]},
        "user_data": {"type": "string"},
        "nics": {"type": "array", "minItems": 1, "items": NIC_SCHEMA},
    },
}

TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["endpoint", "user", "password", "datacenter", "datastore", "network"],
    "properties": {
        # The vCenter base URL, https only. `api.connect` takes the host and the
        # port out of it and appends the SDK path itself.
        "endpoint": {"type": "string", "minLength": 1},
        # An SSO login -- `vcows@vsphere.local` or a domain account -- and its
        # password. vCenter offers no token form, so both are required and there
        # is no credential shape to check.
        "user": {"type": "string", "minLength": 1},
        "password": {"type": "string", "minLength": 1},
        # Which datacenter every other name below is resolved inside.
        "datacenter": {"type": "string", "minLength": 1},
        # Where the template's disk, the clones and the seed ISOs land.
        "datastore": {"type": "string", "minLength": 1},
        # The port group every NIC attaches to. It carries the VLAN, which is
        # why no NIC has a `vlan_id`.
        "network": {"type": "string", "minLength": 1},
        # Exactly one of these -- checked in `_check_placement` rather than as a
        # jsonschema `oneOf`, the way the libvirt backend checks its NIC union in
        # code rather than in its schema.
        "cluster": {"type": "string", "minLength": 1},
        "host": {"type": "string", "minLength": 1},
        # Optional placement. Without them the clone lands in the datacenter's
        # VM folder and in the cluster's or host's root resource pool.
        "folder": {"type": "string", "minLength": 1},
        "resource_pool": {"type": "string", "minLength": 1},
        # The CA certificate for a vCenter certificate signed by a private CA --
        # the ordinary case -- as PEM. `api.connect` builds an SSL context from
        # the text; unlike the Proxmox backend it writes no file, because
        # `ssl` takes the certificate itself.
        "ca_cert": {"type": "string", "pattern": CA_CERT_PATTERN},
        "insecure": {"type": "boolean"},
        # The two first-contact knobs. Defaults are applied where they are read,
        # not here: jsonschema's `default` fills nothing in.
        "import": {"enum": ["ovf", "datastore"]},
        "clone": {"enum": ["linked", "full"]},
    },
}


def validate(cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
    """Offline checks. No connection, no I/O against the target.

    Returns every problem rather than the first, matching ``config.load``.
    ``verify_digest`` is false only for ``destroy``; see ``Backend.validate``.
    """
    problems: list[Problem] = []
    problems += _check_target(cfg["target"]["vsphere"])
    problems += _check_placement(cfg["target"]["vsphere"])

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
    problems += _check_linked_clone_disk(cfg)
    return problems


def _check_placement(target: dict) -> list[Problem]:
    """A clone lands in a cluster or on a host, and vcows will not choose.

    Filed against the block rather than a field, for the reason the Proxmox
    backend files a missing credential there: which of the two the operator meant
    is exactly what is missing, so there is no one field to go and fix.
    """
    if bool(target.get("cluster")) == bool(target.get("host")):
        return [
            Problem.error(
                "exactly one of `cluster` or `host` is required. A cluster is a "
                "DRS cluster whose root resource pool the clone lands in; a host "
                "is one ESXi host managed by this vCenter.",
                where="target.vsphere",
            )
        ]
    return []


def _check_target(target: dict) -> list[Problem]:
    """The endpoint is ours to build the SDK URL from, not the operator's to
    decorate."""
    where = "target.vsphere.endpoint"
    endpoint = target["endpoint"]
    try:
        parts = urlsplit(endpoint)
    except ValueError as exc:
        # Same early return as the other two backends': every check below reads
        # `parts`, and an unhandled ValueError here would unwind past
        # `config.load`'s every-problem contract.
        return [
            Problem.error(
                f"{endpoint!r} is not a URL ({exc}); vcows takes the vCenter host "
                f"and port from this field and cannot parse it",
                where=where,
            )
        ]

    problems: list[Problem] = []
    if parts.scheme != "https":
        problems.append(
            Problem.error(
                f"scheme must be 'https', got {parts.scheme or '<none>'!r}. The "
                f"password is sent to vCenter in the login call and the session "
                f"cookie authorises every upload after it; plaintext http would "
                f"put both on the wire.",
                where=where,
            )
        )
    if not parts.hostname:
        problems.append(Problem.error(f"no host in {endpoint!r}", where=where))
    if parts.path not in ("", "/"):
        problems.append(
            Problem.error(
                f"path must be empty or '/', got {parts.path!r}. This is the "
                f"vCenter base URL; vcows appends '/sdk' and the datastore paths "
                f"itself.",
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
        # Same refusal, and the same reason, as the other two backends'.
        problems.append(
            Problem.error(
                "endpoint must carry no credentials. Authentication is the user "
                "and password under target.vsphere; anything here would be "
                "written to the run directory in plaintext.",
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
                where="target.vsphere.ca_cert",
            )
        )
    # An error rather than a warning, for the same reason the libvirt backend
    # errors on a path: nothing is mounted for it.
    if isinstance(ca_cert, str) and PATH_PATTERN.match(ca_cert):
        problems.append(
            Problem.error(
                "ca_cert is the certificate itself, not a path to it. Paste the "
                "PEM in -- nothing is mounted for it.",
                where="target.vsphere.ca_cert",
            )
        )

    if target.get("insecure"):
        problems.append(
            Problem.warning(
                "certificate verification is disabled. The password under "
                "target.vsphere is sent to whatever answers at this endpoint.",
                where="target.vsphere.insecure",
            )
        )
    return problems


def _check_linked_clone_disk(cfg: dict) -> list[Problem]:
    """A linked clone's delta disk cannot be extended, so ``disk_gb`` must match.

    Only the half ``imagecheck.check_disk_capacity`` does not already cover: it
    errors on a ``disk_gb`` *below* the image's virtual size for every backend,
    and two refusals for one typo is the round trip ``config.load``'s
    every-problem contract exists to avoid.

    Silent when the image cannot be read or is not a qcow2, for the same reason:
    ``check_disk_capacity`` reports both, and this rule has nothing of its own to
    add about them.
    """
    if cfg["target"]["vsphere"].get("clone", CLONE_DEFAULT) != "linked":
        return []
    try:
        virtual = qcow2.virtual_size(cfg["image"]["source_qcow2"])
    except (OSError, qcow2.NotAQcow2):
        return []

    problems = []
    for i, vm in enumerate(cfg["vms"]):
        want = vm.get("disk_gb")
        if isinstance(want, int) and want * 1024**3 > virtual:
            problems.append(
                Problem.error(
                    f"disk_gb is {want}, and a linked clone's disk is the "
                    f"template's: {cfg['image']['source_qcow2']} has a virtual "
                    f"size of {virtual / 1024**3:.1f} GiB and a delta disk cannot "
                    f"be extended. Set disk_gb to match, or set "
                    f"`clone: full` under target.vsphere to grow it.",
                    where=f"vms[{i}].disk_gb",
                )
            )
    return problems
