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

import hashlib
import ipaddress
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jsonschema

from ... import qcow2
from ...marker import VCOWS_NS
from ..base import Problem, problems_from

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

#: QEMU's OUI. Locally administered, and what every libvirt-generated MAC uses.
MAC_OUI = (0x52, 0x54, 0x00)

#: Field-level defaults, not a ``defaults`` block. Each is one value used when a
#: VM omits the key -- there is no resolution step and no merge semantics.
FIRMWARE_DEFAULT = "efi"
MACHINE_DEFAULT = "q35"


def _ceiling(name: str, default: int) -> int:
    """One size ceiling, overrideable from the environment.

    Same shape as ``cli.MANIFEST``: a constant with an environment override, so a
    site on hardware we have not seen raises the bound from the outside rather
    than editing a file inside the image. A value that will not parse, or is not
    positive, is reported and ignored -- taking it silently is the failure mode
    the reporting work existed to remove.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        print(
            f"vcows: ignoring {name}={raw!r}: not a positive integer. Using {default}.",
            file=sys.stderr,
        )
        return default
    return value


#: Sanity ceilings, **not** a supported-configuration claim. They exist to catch
#: a fat-fingered zero before a run creates volumes for a VM no host can start;
#: the hypervisor stays the authority on what it will actually serve. Each is
#: overrideable, and raising one is always safe.
MAX_VCPUS = _ceiling("VCOWS_MAX_VCPUS", 512)
MAX_MEMORY_MIB = _ceiling("VCOWS_MAX_MEMORY_MIB", 4 * 1024 * 1024)
MAX_DISK_GB = _ceiling("VCOWS_MAX_DISK_GB", 64 * 1024)

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


def derive_mac(name: str, index: int, deployment: str) -> str:
    """A deterministic MAC for one VM's Nth NIC.

    cloud-init's ``network-config`` matches an interface by MAC to apply the
    static address, so the MAC has to be known at render time. Deriving it keeps
    a single-NIC config to three lines, stays correct for multiple NICs, and
    regenerates identically with no state file -- the same property, for the same
    reason, as ``derive_id``.

    The deployment is in the input because two deployments each containing
    ``app01`` would otherwise derive one MAC. On two hosts bridged to one L2
    both guests boot, both apply their static address, and both report
    ``cloud-init status: done``; ``address_conflicts`` only ever looks at one
    host, so nothing else catches it. **This narrows the collision rather than
    closing it:** two hosts running the same deployment name still derive the
    same MAC, and a per-NIC ``mac:`` is the escape.

    **This derivation is permanent.** Changing it renames the interface every
    running VM's guest configuration is keyed to. ``tests/test_libvirt_schema.py``
    pins it.
    """
    raw = uuid.uuid5(VCOWS_NS, f"{deployment}/{name}#nic{index}").bytes
    return ":".join(f"{b:02x}" for b in (*MAC_OUI, raw[0], raw[1], raw[2]))


def mac_of(vm: dict, index: int, deployment: str) -> str:
    return vm["nics"][index].get("mac") or derive_mac(vm["name"], index, deployment)


def primary_index(vm: dict) -> int:
    """Which NIC's address represents this VM. First wins unless one says so."""
    for i, nic in enumerate(vm["nics"]):
        if nic.get("primary"):
            return i
    return 0


def connection_uri(target: dict, transport: str = "ssh") -> str:
    """The URI for the client that is about to use it. **The two differ.**

    Measured against the rig, in the container, because none of this is
    documented anywhere the two implementations agree:

    * ``preflight`` uses libvirt's own C client, which does **not** recognise
      ``sshcmd`` at all -- ``remote_open: transport in URL not recognised``. It
      needs ``qemu+ssh``, where it reaches a modern split-daemon host through
      ``virt-ssh-helper``.
    * The provider is go-libvirt, whose ``qemu+ssh`` dials a hardcoded
      ``/var/run/libvirt/libvirt-sock`` over an SSH socket forward. That socket
      does not exist on a split-daemon host, and even given ``socket=`` the
      forward is refused, because SELinux does not let ``sshd`` open a libvirt
      socket. Its ``qemu+sshcmd`` runs ``ssh`` itself and asks the remote end for
      ``virt-ssh-helper``, falling back to ``nc -U`` when that is absent -- the
      modern path with a monolithic fallback, already upstream.

    **No query string, deliberately.** Neither client honours the credential
    parameters the way the config implies: libvirt's ``qemu+ssh`` ignores
    ``known_hosts`` (it is libssh/libssh2 only), the provider's ``qemu+ssh``
    spells it ``knownhosts``, and ``qemu+sshcmd`` fails outright on either. Both
    run ``ssh``, so the credentials reach them through ``~/.ssh/config``, which
    the container's entrypoint writes from ``ssh_keyfile`` and ``known_hosts``.
    R-D's refusal of an operator-supplied query string still matters: it is what
    keeps ``no_verify=1`` off the connection. **The netloc, by contrast, travels
    verbatim** -- only the scheme and the query are replaced here -- which is why
    a password is refused in ``_check_target`` rather than stripped here. Left to
    this function it would reach the rendered tfvars and sit in the run directory
    in plaintext.
    """
    parts = urlsplit(target["uri"])
    return urlunsplit(parts._replace(scheme=f"qemu+{transport}", query=""))


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

    problems += _check_disk_capacity(cfg)
    problems += _check_image_digest(cfg)
    problems += _check_volume_names(cfg)
    return problems


def _nic_checks_are_safe(vm: object, structural: list[Problem]) -> bool:
    """Whether `_check_firmware` and `_check_nics` can read this VM unguarded.

    Normally `_check_vm_structure` passing is what makes that safe. When it did
    not pass, the question is narrower: are the fields *these* checks index still
    the right shape. `_check_nics` reads `vm["nics"]` and, through `mac_of`,
    `vm["name"]`; `_check_firmware` uses `.get` throughout. A `vcpus` out of range
    or an unexpected key says nothing about any of them, and skipping anyway costs
    the operator the edit round trip `config.py:117-119` rules out.

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


def _check_image_digest(cfg: dict) -> list[Problem]:
    """The declared ``image.sha256``, actually computed.

    The field is schema-validated (``config.py:57``, under
    ``additionalProperties: False``) and was never checked, so a corrupted or
    substituted golden image deployed with no signal. A pattern that only ever
    proved the string was 64 hex characters reads as enforcement and is not.

    Optional, and the cost is why it stays optional: this reads the whole image.
    Measured through this function -- 424 MiB in 2.46 s, ~172 MiB/s -- so roughly
    12 s for a 2 GiB golden image and 59 s for a 10 GiB one. CPU-bound, so a warm
    page cache does not help; with no ``sha256`` declared the call returns in
    8 microseconds. ``config.load`` runs the offline checks for every verb
    (``cli.py:295``, ``:325``, ``:336``, ``:531``), so ``destroy`` pays it even
    though it reads only ``cfg["backend"]`` and ``cfg["deployment"]`` and never
    touches ``cfg["image"]``.

    That waste is accepted rather than engineered around. An operator who sets
    the field has asked for the check, and the alternative -- verifying in
    ``preflight`` -- puts an offline check in the connected phase, so
    ``vcows validate`` would keep reporting a corrupt image as valid. That is the
    defect this closes, not a shape to preserve.

    Unreadable is a warning, for the same reason ``_check_disk_capacity`` says:
    ``validate`` is the offline phase and the golden image is bind-mounted at run
    time.
    """
    declared = cfg["image"].get("sha256")
    if declared is None:
        return []

    source = cfg["image"]["source_qcow2"]
    try:
        with open(source, "rb") as fh:
            # `file_digest` rather than a read loop: it chunks internally, so a
            # multi-GB image never lands in memory.
            actual = hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError as exc:
        return [
            Problem.warning(
                f"cannot read {source} to check its sha256 ({exc.strerror}); "
                f"the declared digest was not verified",
                where="image.sha256",
            )
        ]

    # The schema pattern admits either case, so both sides are compared in one.
    if actual != declared.lower():
        return [
            Problem.error(
                f"{source} has sha256 {actual}, but the config declares "
                f"{declared.lower()}; this is not the image the config describes",
                where="image.sha256",
            )
        ]
    return []


def _check_volume_names(cfg: dict) -> list[Problem]:
    """The golden image and a per-VM volume must not want the same name.

    One flat pool, undecorated names (D16), so a golden image called
    ``app01.qcow2`` collides with app01's own overlay. libvirt refuses the
    duplicate itself, but mid-apply, after the run has created other objects.

    ``render`` imports this module, so this import is function-local -- the
    same reason ``prepare.seed_files`` and ``preflight.walk`` import inside
    their functions.
    """
    from .render import overlay_name, seed_name

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
        # clears the query but leaves the netloc alone, so a password is
        # rendered into the tfvars and sits in the run directory in plaintext.
        problems.append(
            Problem.error(
                "URI must carry no password. Neither client would use it -- both "
                "run ssh, so credentials travel via ~/.ssh/config, which the "
                "container entrypoint writes from ssh_keyfile and known_hosts. "
                "It would be written to the run directory in plaintext.",
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
    problems: list[Problem] = []
    primaries = [i for i, n in enumerate(vm["nics"]) if n.get("primary")]
    if len(primaries) > 1:
        problems.append(
            Problem.error(
                f"{len(primaries)} NICs claim primary: true (indices {primaries}); "
                f"at most one may. Omit it entirely and the first NIC is primary.",
                where=f"{where}.nics",
            )
        )

    for i, nic in enumerate(vm["nics"]):
        at = f"{where}.nics[{i}]"
        attachments = [k for k in ("bridge", "network") if k in nic]
        if len(attachments) != 1:
            problems.append(
                Problem.error(
                    "exactly one of 'bridge' or 'network' is required, found "
                    + (
                        f"both ({', '.join(attachments)})" if attachments else "neither"
                    ),
                    where=at,
                )
            )

        iface = _parse_interface(nic.get("ip_cidr", ""), f"{at}.ip_cidr", problems)
        if iface is not None and iface.network.num_addresses > 2:
            # Skipped for /31 and /32 -- and /127 and /128 -- where every address
            # in the block is a host address. `num_addresses` says that in one
            # condition for both families.
            reserved = {
                iface.network.network_address: "the network address",
                iface.network.broadcast_address: "the broadcast address",
            }
            if iface.ip in reserved:
                problems.append(
                    Problem.error(
                        f"{iface.ip} is {reserved[iface.ip]} of "
                        f"{iface.network}, not a host address in it",
                        where=f"{at}.ip_cidr",
                    )
                )
        gateway = _parse_address(nic.get("gateway", ""), f"{at}.gateway", problems)
        # Only the gateway-outside-network check needs both values. Registering
        # the address needs `iface` alone: guarding it on the gateway too means a
        # NIC whose gateway did not parse never claims its address, so the next
        # VM to reuse that address is not reported until the operator has fixed
        # the gateway and re-run -- the round trip `validate` exists to avoid.
        if iface is not None:
            if gateway is not None and gateway not in iface.network:
                problems.append(
                    Problem.error(
                        f"gateway {gateway} is outside {iface.network}",
                        where=f"{at}.gateway",
                    )
                )
            owner = seen_ips.setdefault(str(iface.ip), at)
            if owner != at:
                problems.append(
                    Problem.error(
                        f"address {iface.ip} is already used by {owner}",
                        where=f"{at}.ip_cidr",
                    )
                )
        for j, ns in enumerate(nic.get("nameservers", [])):
            _parse_address(ns, f"{at}.nameservers[{j}]", problems)

        mac = mac_of(vm, i, deployment).lower()
        owner = seen_macs.setdefault(mac, at)
        if owner != at:
            problems.append(
                Problem.error(f"MAC {mac} is already used by {owner}", where=at)
            )
    return problems


def _parse_interface(
    raw: str, where: str, problems: list[Problem]
) -> ipaddress.IPv4Interface | ipaddress.IPv6Interface | None:
    if "/" not in raw:
        problems.append(
            Problem.error(
                f"{raw!r} needs a prefix length, e.g. '192.168.122.60/24'", where=where
            )
        )
        return None
    try:
        return ipaddress.ip_interface(raw)
    except ValueError as exc:
        problems.append(Problem.error(str(exc), where=where))
        return None


def _parse_address(
    raw: str, where: str, problems: list[Problem]
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(raw)
    except ValueError as exc:
        problems.append(Problem.error(str(exc), where=where))
        return None


def _check_disk_capacity(cfg: dict) -> list[Problem]:
    """R-F: an overlay smaller than its backing image cannot be created.

    Uses the qcow2 header read rather than ``qemu-img info`` -- see D18 and
    orchestrator/qcow2.py. Degrades to a warning rather than an error when the
    image cannot be read, because ``validate`` is the offline phase and the golden
    image is bind-mounted at run time.
    """
    source = cfg["image"]["source_qcow2"]
    try:
        virtual = qcow2.virtual_size(source)
    except OSError as exc:
        return [
            Problem.warning(
                f"cannot read {source} to check disk_gb against it ({exc.strerror}); "
                f"a VM whose disk_gb is below the image's virtual size will fail "
                f"at create time",
                where="image.source_qcow2",
            )
        ]
    except qcow2.NotAQcow2 as exc:
        return [Problem.error(str(exc), where="image.source_qcow2")]

    problems = []
    for i, vm in enumerate(cfg["vms"]):
        want = vm.get("disk_gb")
        if isinstance(want, int) and want * 1024**3 < virtual:
            problems.append(
                Problem.error(
                    f"disk_gb is {want} but {source} has a virtual size "
                    f"of {virtual / 1024**3:.1f} GiB; an overlay cannot be "
                    f"smaller than the image it backs onto",
                    where=f"vms[{i}].disk_gb",
                )
            )
    return problems
