"""The ``target.libvirt`` block and the per-VM shape -- findings.md F11.

**This is the one-way door.** Other groups author these configs by hand and keep
them in their own version control, so the shape settled here is the shape we live
with. Everything below is either in F11's list or is a check F11 implies.

Two things F11 left open, settled here:

* ``nics`` is a list but the inventory carries one address, so **the first NIC is
  primary** unless one carries ``primary: true``. Primary means two things: its
  address is the one the inventory reports, and its gateway is the one that
  becomes the guest's default route.
* A per-VM value **replaces**, never merges. There is no ``defaults`` block at
  v0.1 so nothing exercises it yet, but the rule is invisible until the first
  nested field and by then configs exist.

The split with core is D11: core's ``vms`` schema requires only ``name``, and
everything about a VM's shape -- especially NICs, whose valid forms are entirely
backend-specific -- is checked here. That keeps core backend-agnostic and produces
better messages: a jsonschema ``oneOf`` failure on the bridge/network union is
close to unreadable, where a Python check names both fields the operator set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jsonschema

from ...cloudinit import check_addressing, seed_name
from ...imagecheck import check_disk_capacity, check_image_digest
from ...limits import MAX_DISK_GB, MAX_MEMORY_MIB, MAX_VCPUS
from ...problems import Problem, problems_from

#: Same shape as a deployment name: it becomes a libvirt domain name and the stem
#: of two volume names. ``\Z``, not ``$``, for the reason SSH_PATH_PATTERN spells
#: out below: Python's ``$`` also matches before a trailing newline, and a name
#: carrying one reaches libvirt as a domain name.
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z"

MAC_PATTERN = r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}\Z"

#: An absolute path with no whitespace in it. Both credential paths are
#: interpolated verbatim into ``~/.ssh/config`` by the container entrypoint, one
#: per line, so a value carrying a newline appends directives of its own --
#: ``ProxyCommand`` reaches command execution, and ``StrictHostKeyChecking no``
#: undoes R-D from the side the URI check cannot see. ``\Z``, not ``$``: Python's
#: ``$`` also matches before a trailing newline, which is precisely the character
#: this is here to refuse.
SSH_PATH_PATTERN = r"^/[^\s]*\Z"

#: Field-level defaults, not a ``defaults`` block. Each is one value used when a
#: VM omits the key -- there is no resolution step and no merge semantics.
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
        # Not merely non-empty: these two reach ~/.ssh/config verbatim.
        "ssh_keyfile": {"type": "string", "pattern": SSH_PATH_PATTERN},
        "known_hosts": {"type": "string", "pattern": SSH_PATH_PATTERN},
    },
}


def connection_uri(target: dict) -> str:
    """The URI vcows dials. One scheme, for every client this tool has left.

    It used to build two. The second was the go-libvirt provider's
    ``qemu+sshcmd``, and with the provider gone the same ``qemu+ssh`` serves
    preflight, create and destroy -- all three are libvirt's own C client, which
    does not recognise ``sshcmd`` at all (``remote_open: transport in URL not
    recognised``) and reaches a modern split-daemon host through
    ``virt-ssh-helper``.

    **No query string, deliberately.** libvirt's ``qemu+ssh`` ignores
    ``known_hosts`` -- it is libssh/libssh2 only -- so no spelling of the
    credential parameters does anything here. Both ends run ``ssh``, so the
    credentials reach it through ``~/.ssh/config``, which the container's
    entrypoint writes from ``ssh_keyfile`` and ``known_hosts``. R-D's refusal of
    an operator-supplied query string still matters: it is what keeps
    ``no_verify=1`` off the connection. **The netloc, by contrast, travels
    verbatim** -- only the scheme and the query are replaced here -- which is why
    a password is refused in ``_check_target`` rather than stripped here.
    """
    parts = urlsplit(target["uri"])
    return urlunsplit(parts._replace(scheme="qemu+ssh", query=""))


def validate(cfg: dict) -> list[Problem]:
    """Offline checks. No connection, no I/O against the target.

    Returns every problem rather than the first, matching ``config.load``.
    """
    problems: list[Problem] = []
    problems += _check_target(cfg["target"]["libvirt"])

    seen_ips: dict[str, str] = {}
    seen_macs: dict[str, str] = {}
    for i, vm in enumerate(cfg["vms"]):
        where = f"vms[{i}]"
        structural = _check_vm_structure(vm, where)
        problems += structural
        if structural and not _nic_checks_are_safe(vm, structural):
            continue
        problems += _check_firmware(vm, where)
        problems += _check_nics(vm, where, seen_ips, seen_macs, cfg["deployment"])

    problems += check_disk_capacity(cfg)
    problems += check_image_digest(cfg)
    problems += _check_volume_names(cfg)
    return problems


def _nic_checks_are_safe(vm: object, structural: list[Problem]) -> bool:
    """Whether `_check_firmware` and `_check_nics` can read this VM unguarded.

    Normally `_check_vm_structure` passing is what makes that safe. When it did
    not pass, the question is narrower: are the fields *these* checks index still
    the right shape. `_check_nics` reads `vm["nics"]` and, through `mac_of`,
    `vm["name"]`; `_check_firmware` uses `.get` throughout. A `vcpus` out of range
    or an unexpected key says nothing about any of them, and skipping anyway
    costs the operator the edit round trip `config.load`'s every-problem
    contract rules out.

    **The container's shape is only half the question, and asking only it was a
    regression.** A nic that is a mapping with one wrongly-typed *field* passes
    every clause below: `ip_cidr:` left blank in YAML is `None`, `nics` is still
    a list of dicts, and `_check_nics` then reached `"/" not in raw` in
    `_parse_interface` with `None` and raised an uncaught `TypeError` that lost
    every other problem in the document -- the same class of unwind `_check_target`
    wraps `urlsplit` against, added by the same commit that added this guard
    (#112). So the schema's own verdict is consulted first, and `problems_from`
    has already computed it: it puts the failing path in `where`
    (`vms[0].nics[0].ip_cidr`) and `structural` is one VM's problems, so a `.nics`
    anywhere in it places the failure inside the data these checks index, and the
    skip is the only answer that does not crash. A `vcpus` out of range is
    `vms[0].vcpus`, which names no nic, so the case this guard was written for
    still runs the checks and still reports a duplicate address alongside it.

    ``object`` and not ``dict``: the first clause is the one that matters when a
    VM is not a mapping at all, and annotating the parameter as the thing it is
    testing for would make that clause unreachable by declaration.
    """
    if any(".nics" in p.where for p in structural):
        return False
    return (
        isinstance(vm, dict)
        and isinstance(vm.get("name"), str)
        and isinstance(vm.get("nics"), list)
        and all(isinstance(nic, dict) for nic in vm["nics"])
    )


def _check_volume_names(cfg: dict) -> list[Problem]:
    """The golden image and a per-VM volume must not want the same name.

    One flat pool, undecorated names (D16), so a golden image called
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
    """R-D: the URI is ours to assemble, not the operator's to decorate."""
    where = "target.libvirt.uri"
    uri = target["uri"]
    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        # `_check_target` is the first thing `validate` runs, so an unhandled
        # `ValueError` here unwound past every other check and past
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
        # key checking, and neither `keyfile=` nor `known_hosts=` is a parameter
        # vcows would otherwise be setting: `connection_uri` replaces the scheme
        # and clears the query, and the credentials reach ssh through
        # `~/.ssh/config` instead. So an operator query string is not overriding
        # vcows -- it is the only thing on the connection nothing else checks.
        problems.append(
            Problem.error(
                f"URI must carry no query string, got {parts.query!r}. Neither "
                f"client reads credentials from it -- both run ssh, so "
                f"ssh_keyfile and known_hosts travel via ~/.ssh/config, which "
                f"the container entrypoint writes. Setting it here can only "
                f"weaken the connection: no_verify=1 disables host key "
                f"verification.",
                where=where,
            )
        )
    if parts.password is not None:
        # The query string is not the only way credentials reach the URI, and
        # this one survives further: `connection_uri` replaces the scheme and
        # clears the query but leaves the netloc alone, so a password reaches
        # `preflight.connect`'s "connecting to %s" line whole. It used to reach
        # the rendered tfvars too, and sit in the run directory in plaintext;
        # `render` no longer emits a URI, so the log is where it lands now.
        problems.append(
            Problem.error(
                "URI must carry no password. Neither client would use it -- both "
                "run ssh, so credentials travel via ~/.ssh/config, which the "
                "container entrypoint writes from ssh_keyfile and known_hosts. "
                "It would be logged in plaintext with the connection.",
                where=where,
            )
        )
    if parts.fragment:
        problems.append(
            Problem.error(f"unexpected fragment {parts.fragment!r}", where=where)
        )

    for field in ("ssh_keyfile", "known_hosts"):
        path = target.get(field)
        # A warning, not an error: `validate` is the offline phase and runs
        # anywhere, while these are paths on whichever machine runs the deploy --
        # normally the container, where they are bind-mounted at run time.
        if path is not None and not Path(path).is_file():
            problems.append(
                Problem.warning(
                    f"{path} does not exist here. It is read on the machine "
                    f"running the deploy, not on the target, so this matters "
                    f"only if that is this one.",
                    where=f"target.libvirt.{field}",
                )
            )
    return problems


def _check_vm_structure(vm: dict, where: str) -> list[Problem]:
    validator = jsonschema.Draft202012Validator(VM_SCHEMA)
    return problems_from(validator.iter_errors(vm), at=where)


def _check_firmware(vm: dict, where: str) -> list[Problem]:
    """R-G. None of this is changeable after a domain is created."""
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
        # The module does not treat an absent format as "unknown", it treats it
        # as raw: main.tf builds the varstore path with an `.fd` suffix and
        # passes `format = null`. A qcow2 loader then gets an `.fd` varstore,
        # which is the mismatch the first acceptance run already paid for.
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
