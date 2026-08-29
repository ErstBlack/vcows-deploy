"""What already exists on the hypervisor. The only place a deploy reads the target.

Everything the pure half of the pipeline needs to know about the world is learned
here and carried out in ``Discovered`` (findings.md §3, D24). ``prepare`` and
``render`` get data; they cannot go and look.

Three things about this file are load-bearing and easy to get wrong:

* **One ``XMLDesc`` per domain, parsed for three things.** The marker, the disk
  sources and the interface MACs all come out of the same document. Spike A2
  confirmed both marker read paths; this one is chosen because it yields the disks
  too, and ``dom.metadata()`` does not.
* **``pool.refresh(0)`` before any volume is looked at** (D35). ``listAllVolumes``
  and ``storageVolLookupByPath`` read libvirt's in-memory pool cache, not the
  filesystem. On the rig, three of four running domains' disks -- real files inside
  the pool's own directory, on an active pool -- do not resolve without it.
* **``<backingStore>`` is never followed.** Per-VM disks are overlays on the shared
  golden image. Collecting a backing path would hand destroy the volume every other
  deployment on that host depends on, and ``vol.delete()`` would not stop it.

``ElementTree`` rather than ``defusedxml`` is D13: identical API, and stdlib is what
runs when someone copies one of these functions onto a hypervisor to debug it. The
input is libvirt's own re-serialisation of XML it already accepted, ElementTree does
not resolve external entities, and the remaining attacks need someone who can
already define domains here.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from xml.etree import ElementTree as ET

from ...marker import MARKER_ELEMENT, MARKER_XMLNS, Marker, MarkerError
from ..base import Discovered, Existing, Problem, Severity
from .render import overlay_name, seed_name
from .schema import connection_uri, mac_of

#: The marker element as ``XMLDesc`` reports it. ``dom.metadata()`` strips the
#: xmlns and would need a different tag; spike A2 caught the disagreement.
MARKER_TAG = f"{{{MARKER_XMLNS}}}{MARKER_ELEMENT}"


@contextmanager
def connect(cfg: dict) -> Iterator[Any]:
    """Open a session and close it on the way out.

    The backend owns this rather than core: somebody has to build the connection,
    and if that were core then core would import libvirt and the seam would be
    decorative -- which is exactly what the fake-backend test exists to catch.

    ``registerErrorHandler`` silences libvirt's unconditional stderr chatter.
    Lookup misses are the normal case here, not errors, and every one of them would
    otherwise print a traceback-looking line an operator has to learn to ignore.
    """
    import libvirt

    libvirt.registerErrorHandler(lambda _ctx, _err: None, None)
    conn = libvirt.open(connection_uri(cfg["target"]["libvirt"]))
    try:
        yield conn
    finally:
        conn.close()


# -- parsing one domain ----------------------------------------------------


def marker_of(root: ET.Element) -> Marker | None:
    """The ownership marker, or ``None`` if this domain is not ours.

    **Unparseable is unmarked** (D12). Reading a damaged marker as *ours* would let
    destroy delete something we do not understand; reading it as *absent* is caught
    by the name-collision refusal instead, which is the safe direction.
    """
    element = root.find(f"metadata/{MARKER_TAG}")
    if element is None or not element.text:
        return None
    try:
        return Marker.from_json(element.text)
    except MarkerError:
        return None


def disks_of(root: ET.Element) -> tuple[str, ...]:
    """Every source path this domain owns, for teardown.

    Both ``disk`` and ``cdrom`` (D17) -- without the cdrom, every per-VM seed ISO
    is orphaned on teardown. Never a ``<backingStore>``, and never a device with no
    ``<source>``: an empty cdrom tray is normal (every domain on the rig has one)
    and must yield nothing rather than a ``None`` that destroy later tries to
    delete.
    """
    paths = []
    for disk in root.findall("devices/disk"):
        if disk.get("device") not in ("disk", "cdrom"):
            continue
        source = disk.find("source")
        if source is None:
            continue
        if path := source.get("file"):
            paths.append(path)
    return tuple(paths)


def macs_of(root: ET.Element) -> tuple[str, ...]:
    """Configured interface MACs, lowercased.

    Free: this is the same document already parsed for the marker and the disks,
    which is the whole reason the MAC collision check survived D32's cut of the
    ICMP probe.
    """
    return tuple(
        mac.lower()
        for nic in root.findall("devices/interface")
        if (element := nic.find("mac")) is not None
        and (mac := element.get("address")) is not None
    )


def _domains(conn: Any) -> tuple[list[Existing], dict[str, str]]:
    """Every domain on the host, and a MAC -> domain-name index."""
    import libvirt

    found: list[Existing] = []
    by_mac: dict[str, str] = {}
    for dom in conn.listAllDomains(0):
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        name = dom.name()
        found.append(
            Existing(
                name=name,
                id=dom.UUIDString(),
                marker=marker_of(root),
                disks=disks_of(root),
            )
        )
        for mac in macs_of(root):
            by_mac.setdefault(mac, name)
    return found, by_mac


# -- storage ---------------------------------------------------------------


def volume_facts(xml: str) -> dict[str, Any]:
    """One volume, reduced to what preflight decides on.

    ``physical`` is ``None`` when the element is absent. It is optional in
    libvirt's RNG and meaningless for non-file pools, and the rig's
    ``_cloud-images`` directory entry has none -- so its absence is a fact to carry,
    not a parse failure.
    """
    root = ET.fromstring(xml)
    fmt = root.find("target/format")
    path = root.find("target/path")
    physical = root.find("physical")
    return {
        "name": (root.findtext("name") or ""),
        "path": (path.text if path is not None else None),
        "format": (fmt.get("type") if fmt is not None else None),
        "physical": _int_or_none(physical),
    }


def _int_or_none(element: ET.Element | None) -> int | None:
    return int(element.text) if element is not None and element.text else None


def open_pool(conn: Any, name: str) -> tuple[Any | None, list[Problem]]:
    """Look the pool up, insist it is active, and refresh it.

    vcows never creates a pool (D29): that is a host-level mutation on someone
    else's hypervisor, and it would create a destroy obligation we do not want.
    Inactive is checked *explicitly* because a volume lookup against an inactive
    pool returns ``NO_STORAGE_VOL`` rather than anything naming the pool -- the real
    cause would never surface.

    The refresh is D35 and is required for correctness, not defensive. A golden
    image copied into the pool directory out of band is invisible until it happens,
    so preflight would report "not present", the module would set ``create = true``,
    and the apply would die on "storage volume exists already" for a reason nobody
    could diagnose.
    """
    import libvirt

    try:
        pool = conn.storagePoolLookupByName(name)
    except libvirt.libvirtError:
        return None, [
            Problem(
                Severity.ERROR,
                f"storage pool {name!r} does not exist on this host. vcows never "
                f"creates a pool -- create it on the hypervisor and re-run.",
                where="target.libvirt.pool",
            )
        ]

    if not pool.isActive():
        return None, [
            Problem(
                Severity.ERROR,
                f"storage pool {name!r} exists but is not active.",
                where="target.libvirt.pool",
            )
        ]

    try:
        pool.refresh(0)
    except libvirt.libvirtError as exc:
        return pool, [
            Problem(
                Severity.WARNING,
                f"could not refresh pool {name!r} ({exc.get_error_message()}). "
                f"Volumes written out of band may be invisible, which can make a "
                f"present golden image look absent.",
                where="target.libvirt.pool",
            )
        ]
    return pool, []


def walk(pool: Any) -> dict[str, dict[str, Any]]:
    """Every volume in the pool, keyed by name.

    One walk answers three separate questions -- the orphan-volume refusal, whether
    the golden image is here and where, and D30's size comparison -- which is why
    ``prepare`` needs no session.
    """
    import libvirt

    facts = {}
    for vol in pool.listAllVolumes(0):
        try:
            entry = volume_facts(vol.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError):
            # A volume that vanished between listing and describing, or whose XML
            # we cannot read, is not a reason to abandon the walk.
            continue
        facts[entry["name"]] = entry
    return facts


def base_volume(cfg: dict, volumes: dict[str, dict]) -> tuple[dict, list[Problem]]:
    """Resolve the shared golden image: present or not, and where.

    This is the fact ``prepare`` and ``render`` cannot discover for themselves --
    nothing in HCL can read a pool, ``tofu import`` probes by the path we are
    looking for, and the pool's target directory is a property of somebody else's
    pool. It travels in ``Discovered.artifacts`` and lands in ``var.base_volume``.

    **D30: a present base volume is verified, not trusted.** An interrupted upload
    leaves a truncated qcow2 whose header still declares the full virtual size, so
    capacity cannot catch it; every overlay would then back onto a broken image and
    VMs would fail at random points in boot on a host the tool called healthy.
    Comparing ``<physical>`` against the local image's size catches that, and
    catches a *different* image under the same name as well.
    """
    name = cfg["image"]["base_volume_name"]
    source = cfg["image"]["source_qcow2"]
    found = volumes.get(name)

    if found is None:
        return {"name": name, "create": True, "path": ""}, []

    resolved = {"name": name, "create": False, "path": found["path"] or ""}
    if not resolved["path"]:
        return resolved, [
            Problem(
                Severity.ERROR,
                f"volume {name!r} is in pool {cfg['target']['libvirt']['pool']!r} "
                f"but reports no path, so overlays cannot back onto it.",
                where="image.base_volume_name",
            )
        ]

    try:
        local = os.stat(source).st_size
    except OSError as exc:
        return resolved, [
            Problem(
                Severity.WARNING,
                f"golden image {source!r} is not readable ({exc.strerror}), so the "
                f"copy already on the host cannot be verified against it.",
                where="image.source_qcow2",
            )
        ]

    physical = found["physical"]
    if physical is None:
        return resolved, [
            Problem(
                Severity.WARNING,
                f"volume {name!r} reports no physical size, so it cannot be checked "
                f"against {source!r}. Expected for a non-file pool.",
                where="image.base_volume_name",
            )
        ]
    if physical != local:
        return resolved, [
            Problem(
                Severity.ERROR,
                f"volume {name!r} is {physical} bytes on the host but {local} bytes "
                f"locally. That is either a truncated upload or a different image "
                f"under the same name; either way every overlay would back onto it. "
                f"Delete it on the hypervisor and re-run.",
                where="image.base_volume_name",
            )
        ]
    return resolved, []


def orphan_volumes(
    cfg: dict, volumes: dict[str, dict], claimed: set[str]
) -> list[Problem]:
    """Refuse a per-VM volume that no domain owns (findings.md §2).

    Volumes cannot carry markers, so a create that died between the volume and the
    domain leaves a qcow2 with no marker and no owner. The next run would hit a name
    collision mid-apply and fail with a raw libvirt error. Naming volumes
    deterministically from the logical name is what makes this detectable at all,
    and the fix is deliberately manual: the operator deletes one file. Building
    recovery machinery here is how the last version started sprawling.
    """
    problems = []
    for vm in cfg["vms"]:
        for volume in (overlay_name(vm["name"]), seed_name(vm["name"])):
            if volume in volumes and volume not in claimed:
                problems.append(
                    Problem(
                        Severity.ERROR,
                        f"volume {volume!r} exists but no domain references it. "
                        f"A previous create was interrupted; delete it on the "
                        f"hypervisor and re-run.",
                        where=vm["name"],
                    )
                )
    return problems


# -- addressing ------------------------------------------------------------


def _network_claims(conn: Any, name: str) -> tuple[dict[str, str], list[Problem]]:
    """Addresses libvirt has already handed out or promised on one network.

    Leases and ``<host>`` reservations are the only address facts a hypervisor
    connection can state authoritatively. An ICMP probe was considered and cut
    (D32): the container has no reason to share a segment with the target network,
    so silence would be meaningless and a reply could be an unrelated host.

    The lookup pays for itself twice -- a NIC naming a network that does not exist
    fails at define time otherwise, deep inside an apply.
    """
    import libvirt

    try:
        net = conn.networkLookupByName(name)
    except libvirt.libvirtError:
        return {}, [
            Problem(
                Severity.ERROR,
                f"network {name!r} does not exist on this host.",
                where=f"nics[].network={name}",
            )
        ]

    claims: dict[str, str] = {}
    root = ET.fromstring(net.XMLDesc(0))
    for host in root.findall("ip/dhcp/host"):
        if ip := host.get("ip"):
            claims[ip] = f"a DHCP reservation on network {name!r}"
    try:
        for lease in net.DHCPLeases():
            if ip := lease.get("ipaddr"):
                claims.setdefault(ip, f"an active DHCP lease on network {name!r}")
    except libvirt.libvirtError:
        # No DHCP configured, or the network is not running. Reservations still
        # read fine from the XML above.
        pass
    return claims, []


def address_conflicts(conn: Any, cfg: dict, by_mac: dict[str, str]) -> list[Problem]:
    """Refuse an address or MAC libvirt already knows about.

    Scoped to what the connection affords. Whether an address is otherwise free is
    the operator's business -- including whether a static sits inside a DHCP range,
    which is the stock libvirt layout and not something to warn about (D33).
    """
    problems: list[Problem] = []
    claims: dict[str, dict[str, str]] = {}

    for vm in cfg["vms"]:
        for index, nic in enumerate(vm["nics"]):
            network = nic.get("network")
            if network is not None:
                if network not in claims:
                    found, issues = _network_claims(conn, network)
                    claims[network] = found
                    problems.extend(issues)
                address = nic["ip_cidr"].split("/")[0]
                if reason := claims[network].get(address):
                    problems.append(
                        Problem(
                            Severity.ERROR,
                            f"{address} is already {reason}.",
                            where=f"{vm['name']}.nics[{index}].ip_cidr",
                        )
                    )

            mac = mac_of(vm, index).lower()
            if owner := by_mac.get(mac):
                problems.append(
                    Problem(
                        Severity.ERROR,
                        f"MAC {mac} is already configured on domain {owner!r}.",
                        where=f"{vm['name']}.nics[{index}]",
                    )
                )
    return problems


# -- the walk --------------------------------------------------------------


def preflight(cfg: dict, session: Any) -> Discovered:
    """One pass over the target. Everything downstream runs on what this returns."""
    wanted = {vm["name"] for vm in cfg["vms"]}
    vms, by_mac = _domains(session)

    # A domain that is ours, for a name in this config, is a SKIP -- its MACs are
    # ours by construction and must not be reported as somebody else's.
    ours = {e.name for e in vms if e.marker is not None and e.marker.name in wanted}
    by_mac = {mac: owner for mac, owner in by_mac.items() if owner not in ours}

    pool, problems = open_pool(session, cfg["target"]["libvirt"]["pool"])
    artifacts: dict[str, Any] = {}

    if pool is not None:
        volumes = walk(pool)
        claimed = {os.path.basename(path) for e in vms for path in e.disks}
        base, base_problems = base_volume(cfg, volumes)
        artifacts["base_volume"] = base
        problems += base_problems
        problems += orphan_volumes(cfg, volumes, claimed)

    problems += address_conflicts(session, cfg, by_mac)
    return Discovered(vms=vms, artifacts=artifacts, problems=problems)
