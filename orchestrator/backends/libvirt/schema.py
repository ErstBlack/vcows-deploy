"""The ``target.libvirt`` block and the per-VM shape.

**This is the one-way door.** Other groups author these configs by hand and keep
them in their own version control, so the shape settled here is the shape we live
with.

Two rules the schema settles, because nothing else can:

* ``nics`` is a list but the inventory carries one address, so **the first NIC is
  primary** unless one carries ``primary: true``. Primary means two things: its
  address is the one the inventory reports, and its gateway is the one that
  becomes the guest's default route.
* A per-VM value **replaces**, never merges. The config's ``defaults`` block is
  flat for exactly that reason, and core resolves it before this module runs, so
  every VM reaching here already carries the values it will be judged against.

The split with core: core's ``vms`` schema requires only ``name``, and
everything about a VM's shape -- especially NICs, whose valid forms are entirely
backend-specific -- is checked here. That keeps core backend-agnostic and produces
better messages: a jsonschema ``oneOf`` failure on the bridge/network union is
close to unreadable, where a Python check names both fields the operator set.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from ...cloudinit import (
    check_addressing,
    check_vm_structure,
    nic_checks_are_safe,
    seed_name,
)
from ...imagecheck import check_disk_capacity, check_image_digest
from ...limits import MAX_DISK_GB, MAX_MEMORY_MIB, MAX_VCPUS
from ...problems import Problem

#: Same shape as a deployment name: it becomes a libvirt domain name and the stem
#: of two volume names. ``\Z``, not ``$``, for the reason PATH_PATTERN spells
#: out below: Python's ``$`` also matches before a trailing newline, and a name
#: carrying one reaches libvirt as a domain name.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z"

MAC_PATTERN = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\Z"

#: A PEM private key's opening line. ``ssh_key`` carries the key itself, so this
#: asks whether it opens like one -- ``preflight.connect`` writes it to a file
#: and hands the file to ``ssh``, which otherwise fails with ``invalid format``
#: and names no config field. It also catches the value an operator reaches for
#: by habit, a *public* key, which is not a secret and authenticates nothing.
#: Unanchored at the end: everything after the header is base64 and a footer.
SSH_KEY_PATTERN = r"^-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"

#: An absolute path with no whitespace in it. Matched only to reject it:
#: ``ssh_key`` and ``known_hosts`` carry contents, nothing is mounted for them,
#: and ``known_hosts`` has no pattern of its own, so an unrecognised path would
#: otherwise reach ``ssh`` as a known_hosts file of one nonsense line.
#: ``\Z``, not ``$``: Python's ``$`` also matches before a trailing newline.
PATH_PATTERN = re.compile(r"^/[^\s]*\Z")

#: Backend fallbacks, and not the config's ``defaults`` block -- core has already
#: resolved that one by the time this module runs. Each is the value used when a
#: VM, and any default it inherited, leave the key unset.
FIRMWARE_DEFAULT = "efi"
MACHINE_DEFAULT = "q35"


NIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ip_cidr", "gateway"],
    "properties": {
        # Exactly one of these two. Checked in Python, not as a jsonschema
        # `oneOf`, so the error can name what was actually set.
        "network": {"type": "string", "minLength": 1},
        "bridge": {"type": "string", "minLength": 1},
        "ip_cidr": {"type": "string", "minLength": 1},
        "gateway": {"type": "string", "minLength": 1},
        "nameservers": {"type": "array", "items": {"type": "string"}},
        "mac": {"type": "string", "pattern": MAC_PATTERN},
        "model": {"type": "string", "minLength": 1},
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
        # UEFI is not changeable after creation, which is why it is here rather
        # than being inferred.
        "firmware": {"enum": ["efi", "bios"]},
        # Host-specific, and the reason firmware autoselection is not assumed:
        # Fedora ships OVMF_CODE_4M.qcow2, RHEL ships a raw .fd, and an early
        # RHEL 9 may not carry the firmware descriptors autoselection needs.
        "loader": {"type": "string", "minLength": 1},
        "loader_format": {"enum": ["raw", "qcow2"]},
        "nvram_template": {"type": "string", "minLength": 1},
        "machine": {"type": "string", "minLength": 1},
        # Passed to cloud-init verbatim. vcows writes meta-data and
        # network-config; this is the operator's half and is not interpreted.
        "user_data": {"type": "string"},
        "nics": {"type": "array", "minItems": 1, "items": NIC_SCHEMA},
    },
}

TARGET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["uri", "pool"],
    "properties": {
        # qemu+ssh:// only. No local socket at v0.1.
        "uri": {"type": "string", "minLength": 1},
        # Must already exist. Creating a pool is a host-level mutation on someone
        # else's hypervisor; preflight refuses when it is missing or inactive.
        "pool": {"type": "string", "minLength": 1},
        # The credentials themselves, not paths to them. `preflight.connect`
        # writes each to a private temporary file for the length of the
        # connection.
        "ssh_key": {"type": "string", "pattern": SSH_KEY_PATTERN},
        # No pattern: a known_hosts line is `host algo base64` with any
        # algorithm name, so non-empty is the whole of what can be said.
        "known_hosts": {"type": "string", "minLength": 1},
    },
}


def connection_uri(target: dict, params: dict[str, str] | None = None) -> str:
    """The URI vcows dials: the operator's, scheme fixed and query replaced.

    ``params`` is what ``preflight.connect`` wrote for ``ssh`` -- ``keyfile=``
    and ``command=`` -- or nothing, in which case there is no query and ``ssh``
    reads the caller's own ``~/.ssh``. ``known_hosts=`` is a libssh parameter and
    not a ``qemu+ssh`` one, so the known_hosts copy travels inside the
    ``command=`` wrapper instead.

    ``_check_target``'s refusal of an operator-supplied query is what keeps
    ``no_verify=1`` off the connection, and the operator's query is *replaced*
    here, never merged. ``safe="/"`` keeps the paths readable in the log line,
    and ``quote_via=quote`` rather than ``urlencode``'s default ``quote_plus``
    is what makes a space travel as ``%20``: libvirt's URI parser unescapes
    ``%XX`` and does not read ``+`` as a space, so a ``TMPDIR`` with one in it
    would otherwise name a file that does not exist.
    **The netloc travels verbatim**, which is why a password is refused in
    ``_check_target`` rather than stripped here.
    """
    parts = urlsplit(target["uri"])
    query = urlencode(params or {}, safe="/", quote_via=quote)
    return urlunsplit(parts._replace(scheme="qemu+ssh", query=query))


def validate(cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
    """Offline checks. No connection, no I/O against the target.

    Returns every problem rather than the first, matching ``config.load``.
    ``verify_digest`` is false only for ``destroy``; see ``Backend.validate``.
    """
    problems: list[Problem] = []
    problems += _check_target(cfg["target"]["libvirt"])

    seen_ips: dict[str, str] = {}
    seen_macs: dict[str, str] = {}
    for i, vm in enumerate(cfg["vms"]):
        where = f"vms[{i}]"
        structural = check_vm_structure(vm, where, VM_SCHEMA)
        problems += structural
        if structural and not nic_checks_are_safe(vm, structural):
            continue
        problems += _check_firmware(vm, where)
        problems += _check_nics(vm, where, seen_ips, seen_macs, cfg["deployment"])

    problems += check_disk_capacity(cfg)
    if verify_digest:
        problems += check_image_digest(cfg)
    problems += _check_volume_names(cfg)
    return problems


def _check_volume_names(cfg: dict) -> list[Problem]:
    """The golden image and a per-VM volume must not want the same name.

    One flat pool, undecorated names, so a golden image called
    ``app01.qcow2`` collides with app01's own overlay. libvirt refuses the
    duplicate itself, but mid-apply, after the run has created other objects.

    ``render`` imports this module, so the ``overlay_name`` import is
    function-local -- the same reason ``preflight.walk`` imports inside its
    function. ``seed_name`` is core and needs no such dance.
    """
    from .render import overlay_name

    base = cfg["image"]["base_volume_name"]
    return [
        Problem.error(
            f"{base!r} is also the name vcows derives for {vm['name']}'s "
            f"{kind}, and both would be created in one pool.",
            where="image.base_volume_name",
        )
        for vm in cfg["vms"]
        for kind, derived in (
            ("overlay", overlay_name(vm["name"])),
            ("seed ISO", seed_name(vm["name"])),
        )
        if derived == base
    ]


def _check_target(target: dict) -> list[Problem]:
    """The URI is ours to assemble, not the operator's to decorate."""
    where = "target.libvirt.uri"
    uri = target["uri"]
    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        # `_check_target` is the first thing `validate` runs, so an unhandled
        # `ValueError` here would unwind past every other check and past
        # `config.load`'s "every problem rather than the first". Returning early
        # is right rather than merely convenient: every remaining check reads
        # `parts`, so there is nothing further to say about this URI.
        return [
            Problem.error(
                f"{uri!r} is not a URL ({exc}); vcows assembles the connection "
                f"URI from these fields and cannot parse this one",
                where=where,
            )
        ]

    problems: list[Problem] = []
    if parts.scheme != "qemu+ssh":
        problems.append(
            Problem.error(
                f"scheme must be 'qemu+ssh', got {parts.scheme or '<none>'!r}. "
                f"v0.1 connects over SSH only; a local socket is later work.",
                where=where,
            )
        )
    if not parts.hostname:
        problems.append(
            Problem.error(f"no host in {uri!r}", where=where),
        )
    if parts.path != "/system":
        problems.append(
            Problem.error(
                f"path must be '/system', got {parts.path or '<none>'!r}", where=where
            )
        )
    if parts.query:
        # The single most important check here. `no_verify=1` disables SSH host
        # key checking, and `connection_uri` replaces the operator's query with
        # the one `connect` builds from the credential files, so a query here is
        # not overriding vcows -- it is the only thing on the connection nothing
        # else checks.
        problems.append(
            Problem.error(
                f"URI must carry no query string, got {parts.query!r}. vcows "
                f"assembles the query itself from ssh_key and known_hosts; "
                f"setting it here can only weaken the connection: no_verify=1 "
                f"disables host key verification.",
                where=where,
            )
        )
    if parts.password is not None:
        # The query string is not the only way credentials reach the URI, and
        # this one survives further: `connection_uri` replaces the scheme and
        # the query but leaves the netloc alone, so a password reaches
        # `preflight.connect`'s "connecting to %s" line whole.
        problems.append(
            Problem.error(
                "URI must carry no password. ssh does not read one, and it would "
                "be logged in plaintext with the connection: connection_uri "
                "replaces the query but leaves the netloc alone. Use ssh_key.",
                where=where,
            )
        )
    if parts.fragment:
        problems.append(
            Problem.error(f"unexpected fragment {parts.fragment!r}", where=where)
        )

    # An error rather than a warning: nothing is mounted for these, so the
    # alternative is `ssh` failing on a key file holding one line of text that
    # happens to be a filename.
    for field in ("ssh_key", "known_hosts"):
        value = target.get(field)
        if isinstance(value, str) and PATH_PATTERN.match(value):
            problems.append(
                Problem.error(
                    f"{field} is the credential itself now, not a path to it. "
                    f"Paste the file's contents in -- nothing is mounted for it.",
                    where=f"target.libvirt.{field}",
                )
            )
    return problems


def _check_firmware(vm: dict, where: str) -> list[Problem]:
    """None of this is changeable after a domain is created."""
    firmware = vm.get("firmware", FIRMWARE_DEFAULT)
    loader = vm.get("loader")
    template = vm.get("nvram_template")

    problems: list[Problem] = []
    if firmware == "bios":
        for key in ("loader", "loader_format", "nvram_template"):
            if key in vm:
                problems.append(
                    Problem.error(
                        f"{key!r} is a UEFI setting and cannot appear with "
                        f"firmware: bios",
                        where=f"{where}.{key}",
                    )
                )
        return problems

    if (loader is None) != (template is None):
        present, missing = (
            ("loader", "nvram_template") if loader else ("nvram_template", "loader")
        )
        problems.append(
            Problem.error(
                f"{present!r} was set without {missing!r}. A UEFI domain needs "
                f"both, or neither -- with neither, libvirt selects the firmware "
                f"itself from the host's descriptors.",
                where=f"{where}.{present}",
            )
        )
    if "loader_format" in vm and loader is None:
        problems.append(
            Problem.error(
                "'loader_format' describes 'loader', which is not set",
                where=f"{where}.loader_format",
            )
        )
    if loader is not None and "loader_format" not in vm:
        # An absent format is taken as raw, not as unknown: `create.firmware_xml`
        # suffixes the varstore path `.fd` for anything that is not qcow2, so a
        # qcow2 loader would get an `.fd` varstore.
        problems.append(
            Problem.error(
                "'loader' was set without 'loader_format'. It is not optional: "
                "the varstore path is built from it, and an absent value is "
                "taken as 'raw'. Fedora's OVMF is qcow2, RHEL's is raw.",
                where=f"{where}.loader",
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
    """The attachment rule, then the addressing every backend shares.

    Only the first half is libvirt's. A NIC attaches to a libvirt network *or* a
    host bridge, exactly one; Proxmox has no network concept and always attaches
    to a bridge, so that check cannot be shared. Everything below it -- the
    address parses, the gateway is inside it, nothing is reused -- is identical
    for both and lives in ``cloudinit.check_addressing``.
    """
    problems: list[Problem] = []
    for i, nic in enumerate(vm["nics"]):
        attachments = [k for k in ("bridge", "network") if k in nic]
        if len(attachments) != 1:
            problems.append(
                Problem.error(
                    "exactly one of 'bridge' or 'network' is required, found "
                    + (
                        f"both ({', '.join(attachments)})" if attachments else "neither"
                    ),
                    where=f"{where}.nics[{i}]",
                )
            )
    return problems + check_addressing(vm, where, seen_ips, seen_macs, deployment)
