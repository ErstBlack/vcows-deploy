"""The NoCloud seed ISO, and the MAC derivation cloud-init matches interfaces by.

**Core, not the libvirt backend's, because nothing in the artifact is
hypervisor-specific.**
``docs/archive/orchestrator-architecture.md`` §6.4 chose this before there was a second
backend to prove it: Proxmox's own ``cicustom`` reads a snippet, packages it into
an ISO and attaches it as a CD-ROM at every VM start, so shipping the ISO
directly is doing that packaging here -- where libvirt already needed it -- and
is what would keep a Proxmox backend on an API token alone, with no SSH
credential for the snippet upload the API cannot do.

Split pure/impure on purpose: ``seed_files`` decides what cloud-init is told and
is testable without touching a filesystem, ``build_seed_iso`` only writes it out.

**vcows owns ``meta-data`` and ``network-config``; the operator owns ``user-data``
verbatim.** Identity and addressing are derived from fields vcows already has, and
the config's ``user_data`` string is passed through with no interpretation. That
is what keeps a ``users:``/``ssh_keys:``/``packages:`` schema -- and the merge
semantics that would come with it -- out of v0.1 entirely.

``libvirt_cloudinit_disk`` is deliberately unused: it stages the ISO in
``os.TempDir()`` and calls ``RemoveResource()`` when the file is missing, so a
container's empty ``/tmp`` makes the ISO, its volume, and the domain all look like
they need recreating on every run (findings.md F2, upstream issue #1368).
"""

from __future__ import annotations

import io
import ipaddress
import uuid
from pathlib import Path

import jsonschema
import yaml

from .marker import VCOWS_NS, derive_id
from .problems import Problem, problems_from

#: Both cloud-init and libvirt find a NoCloud datasource by this volume label.
VOLUME_LABEL = "cidata"

#: Exactly the settings spike A1 verified, cross-read against xorrisofs output.
ISO_ARGS = {
    "interchange_level": 3,
    "joliet": 3,
    "rock_ridge": "1.09",
    "vol_ident": VOLUME_LABEL,
}

#: QEMU's OUI. Locally administered, and what every libvirt-generated MAC uses.
#: Correct for Proxmox too, which is QEMU/KVM and assigns from the same range --
#: which is why this derivation moved here whole rather than being parameterised.
MAC_OUI = (0x52, 0x54, 0x00)


def seed_name(vm_name: str) -> str:
    """The seed ISO's filename, for every backend.

    **Must not match ``vm-<vmid>-cloudinit.iso``.** Proxmox pattern-matches that
    name, assumes it generated the file, and tries to regenerate it at VM start --
    which fails the start task with a ``genisoimage`` error. Any other name is
    passed through untouched. libvirt does not care either way, so one name serves
    both and the constraint lives in one place.
    """
    return f"{vm_name}-seed.iso"


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


def seed_files(vm: dict, cfg: dict) -> dict[str, bytes]:
    """The three files cloud-init reads off the ISO."""
    name = vm["name"]
    meta = {
        # The marker's own derived id. Stable with no state file, so cloud-init's
        # per-instance modules do not re-run on a reboot.
        "instance-id": derive_id(name, cfg["deployment"]),
        "local-hostname": name,
    }
    user_data = vm.get("user_data")
    if user_data is None:
        user_data = f"#cloud-config\nhostname: {name}\n"

    return {
        "meta-data": yaml.safe_dump(meta, sort_keys=False).encode(),
        "user-data": user_data.encode(),
        "network-config": yaml.safe_dump(
            _network_config(vm, cfg["deployment"]), sort_keys=False
        ).encode(),
    }


def _network_config(vm: dict, deployment: str) -> dict:
    """NoCloud network-config v2, matching each interface by MAC.

    Matching by MAC rather than by name is why the MAC has to be derived at render
    time: interface names are assigned by the guest kernel and are not knowable
    from here.

    The default route is written as ``0.0.0.0/0``, **not** netplan's ``default``.
    That distinction cost the first acceptance run: cloud-init 24.4 accepts the
    document, reads it, logs "Applying network configuration from ds", and then
    throws ``ValueError: Address default is not a valid ip address`` out of its
    own v2-to-v1 route normaliser. `default` is a netplan idiom that cloud-init's
    parser does not implement, and the failure is the worst shape available: the
    guest boots, falls back to DHCP, and comes up healthy on an address nobody
    asked for. ``gateway4`` would also work and is deprecated; a CIDR is neither.

    **Only the primary NIC gets it.** One default route per NIC leaves a
    multi-NIC guest choosing its egress by metric, which is the same shape of
    failure: it boots, it routes, and it routes somewhere nobody chose.
    ``gateway`` stays required on every NIC even so -- it is what the address is
    checked against, and making a required field optional later is the
    backward-compatible direction.

    **The v6 half is not configured.** ``dhcp6`` is off and the default route is
    hardcoded to ``0.0.0.0/0``, so this emits a v4 document and nothing else. The
    schema is wider than that: ``_parse_interface`` takes both families and the
    network/broadcast check reasons about ``/127`` and ``/128``, so a v6
    ``ip_cidr`` validates cleanly, reaches here, and produces a guest with an
    address and no route. That is a gap, not a rejection -- closing it means
    deciding what a dual-stack primary means for ``configured_address`` and for
    ``address_conflicts``, which is more than a second route literal.

    **The keys are identifiers, not device names.** ``nic0``, ``nic1`` name the
    entries; the interface each one is applied to is found by MAC and keeps
    whatever name the image gives it -- ``eth0``, ``ens3``, ``ens18``, whatever
    the kernel and udev produce. cloud-init renames a matched interface only
    when the entry carries ``set-name`` (``extract_physdevs`` in
    ``cloudinit/net/__init__.py``: "only rename if configured to do so"), and
    that is deliberately not written, so a golden image keyed to its own kernel
    names keeps working. The rename would run from
    ``stages.py::apply_network_config`` in init-local, ahead of every renderer,
    so this holds for NetworkManager, sysconfig and netplan alike.
    """
    default_route = primary_index(vm)
    ethernets = {}
    for i, nic in enumerate(vm["nics"]):
        entry: dict = {
            "match": {"macaddress": mac_of(vm, i, deployment)},
            "dhcp4": False,
            "dhcp6": False,
            "addresses": [nic["ip_cidr"]],
        }
        if i == default_route:
            entry["routes"] = [{"to": "0.0.0.0/0", "via": nic["gateway"]}]
        if nic.get("nameservers"):
            entry["nameservers"] = {"addresses": list(nic["nameservers"])}
        ethernets[f"nic{i}"] = entry
    return {"version": 2, "ethernets": ethernets}


def build_seed_iso(files: dict[str, bytes], out: Path) -> Path:
    """Write one ``cidata`` ISO. Overwrites; the run directory is ours.

    **The bytes are not reproducible, the content is.** pycdlib stamps
    ``time.time()`` into the volume descriptors and into every directory record,
    and ``new()`` takes no date argument, so pinning them would mean freezing a
    process-global clock for the duration of the write. Two builds of one input
    therefore carry identical files and differ in a few dozen bytes, which is what
    ``tests/test_seed_iso.py`` asserts.
    """
    import pycdlib

    out.unlink(missing_ok=True)
    iso = pycdlib.PyCdlib()
    iso.new(**ISO_ARGS)
    try:
        for name in sorted(files):
            blob = files[name]
            iso.add_fp(
                io.BytesIO(blob),
                len(blob),
                iso_path(name),
                rr_name=name,
                joliet_path=f"/{name}",
            )
        iso.write(str(out))
    finally:
        iso.close()
    return out


def iso_path(name: str) -> str:
    """The ISO 9660 identifier for a cloud-init filename.

    cloud-init reads the Joliet or Rock Ridge name, never this one, but every file
    needs a conforming 9660 identifier underneath.
    """
    return f"/{name.upper().replace('-', '_')}.;1"


def build_all(cfg: dict, workdir: Path) -> dict[str, str]:
    """One ISO per VM in ``cfg``, named for the VM. Returns name -> path.

    The caller passes a config whose ``vms`` is already narrowed to what will be
    created, so this never builds a seed for a VM that already exists.

    The filename comes from ``seed_name`` rather than being spelled again here.
    It is the same string each backend's module uploads or creates as a volume,
    and the same one both ``destroy`` paths check a candidate against before
    unlinking it, so a second copy of the rule is a second place for it to drift
    -- and the drift would be silent on this side, since a seed built under one
    name and looked for under another is just a file nothing claims. Carried over
    from #147, which made the same change while this function still lived in the
    libvirt backend.
    """
    return {
        vm["name"]: str(
            build_seed_iso(seed_files(vm, cfg), workdir / seed_name(vm["name"]))
        )
        for vm in cfg["vms"]
    }


def check_vm_structure(vm: object, where: str, vm_schema: dict) -> list[Problem]:
    """One VM entry against the backend's own ``VM_SCHEMA``, as Problems at ``where``.

    Shared because the call is identical in every backend and only the schema
    differs; the schema stays the backend's, since shape is where they disagree.
    """
    validator = jsonschema.Draft202012Validator(vm_schema)
    return problems_from(validator.iter_errors(vm), at=where)


def nic_checks_are_safe(vm: object, structural: list[Problem]) -> bool:
    """Whether a backend's NIC checks can read this VM unguarded.

    Normally `check_vm_structure` passing is what makes that safe. When it did
    not pass, the question is narrower: are the fields *these* checks index still
    the right shape. `check_addressing` reads `vm["nics"]` and, through `mac_of`,
    `vm["name"]`. A `vcpus` out of range or an unexpected key says nothing about
    any of them, and skipping anyway costs the operator the edit round trip
    `config.load`'s every-problem contract rules out.

    **The container's shape is only half the question, and asking only it was a
    regression.** A nic that is a mapping with one wrongly-typed *field* passes
    every clause below: `ip_cidr:` left blank in YAML is `None`, `nics` is still
    a list of dicts, and `check_addressing` then reached `"/" not in raw` in
    `_parse_interface` with `None` and raised an uncaught `TypeError` that lost
    every other problem in the document -- the same class of unwind the libvirt
    `_check_target` wraps `urlsplit` against, added by the same commit that added
    this guard (#112). So the schema's own verdict is consulted first, and
    `problems_from` has already computed it: it puts the failing path in `where`
    (`vms[0].nics[0].ip_cidr`) and `structural` is one VM's problems, so a
    `.nics` anywhere in it places the failure inside the data these checks
    index, and the skip is the only answer that does not crash. A `vcpus` out of
    range is `vms[0].vcpus`, which names no nic, so the case this guard was
    written for still runs the checks and still reports a duplicate address
    alongside it.

    ``object`` and not ``dict``: the first clause is the one that matters when a
    VM is not a mapping at all, and annotating the parameter as the thing it is
    testing for would make that clause unreachable by declaration.

    One copy for every backend (#179): the guard exists because of #112, and a
    fix to it was being made twice.
    """
    if any(".nics" in p.where for p in structural):
        return False
    return (
        isinstance(vm, dict)
        and isinstance(vm.get("name"), str)
        and isinstance(vm.get("nics"), list)
        and all(isinstance(nic, dict) for nic in vm["nics"])
    )


def check_addressing(
    vm: dict,
    where: str,
    seen_ips: dict[str, str],
    seen_macs: dict[str, str],
    deployment: str,
) -> list[Problem]:
    """Everything about a NIC that is true for every backend.

    Split out of the libvirt backend ahead of a second one: these are
    checks on the values ``_network_config`` above consumes -- the address, its
    prefix, the gateway, the nameservers, the MAC -- so they belong beside it and
    not in either backend. What is *not* here is how a NIC attaches to a network,
    which is the half that genuinely differs: libvirt takes a network or a
    bridge, Proxmox only ever a bridge.

    ``seen_ips`` and ``seen_macs`` are threaded across every VM in one config, so
    a duplicate is reported against the second use and names the first.
    """
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
            if iface.version == 6:
                # `_network_config` writes `dhcp6: false` and the one default
                # route as `0.0.0.0/0`, so the address is configured and nothing
                # routes it. A warning and not an error: only the primary NIC
                # gets a route at all, so a secondary v6 NIC is no worse off
                # than a secondary v4 one, and refusing the config would refuse
                # that too. README's "IPv4 only, in practice" is the long form.
                problems.append(
                    Problem.warning(
                        "the generated network-config sets dhcp6: false and "
                        "routes only 0.0.0.0/0, so this NIC gets the address "
                        "and no route",
                        where=f"{at}.ip_cidr",
                    )
                )
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
