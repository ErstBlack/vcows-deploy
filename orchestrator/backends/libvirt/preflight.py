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

Nothing this file cannot read is skipped in silence. Every check here decides on
what it found, so an absent domain reads as no MAC collision, an absent volume as
no orphan, and an unreadable lease list as a free address. A skip is therefore
always a ``Problem`` naming the object and what the skip cost, and every
``libvirtError`` is matched against a code from ``errors`` rather than assumed to
be the benign one.

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
from ..base import Discovered, Existing, Problem
from .errors import (
    ERR_NO_NETWORK,
    ERR_NO_STORAGE_POOL,
    ERR_NO_SUPPORT,
    ERR_OPERATION_INVALID,
)
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
    """Every **file-backed** source path this domain owns, for teardown.

    Both ``disk`` and ``cdrom`` (D17) -- without the cdrom, every per-VM seed ISO
    is orphaned on teardown. Never a ``<backingStore>``, and never a device with no
    ``<source>``: an empty cdrom tray is normal (every domain on the rig has one)
    and must yield nothing rather than a ``None`` that destroy later tries to
    delete.

    **``source/@file`` only.** A network, block or volume-pool disk has a
    ``<source>`` with a different attribute and yields nothing here, so it is
    never reported and never deleted. Correct for what vcows creates -- every
    disk it makes is a file in a dir/fs pool -- and the safe direction for
    anything else, since destroy deletes only what this returns. But it means
    ``preflight``'s disk report is silent about a disk somebody attached by
    hand, so a VM torn down by vcows can leave one behind with no line saying so.
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


def _domains(conn: Any) -> tuple[list[Existing], dict[str, str], list[Problem]]:
    """Every domain on the host, a MAC -> domain-name index, and what it missed.

    A domain that will not read is skipped rather than fatal: one broken foreign
    domain would otherwise stop every deploy on a shared host, and it is not this
    deployment's to fix. Skipped *and reported*, because this walk is where a MAC
    collision and an unmarked name clash are found -- a domain nobody could read
    contributes neither, and absent reads as free.
    """
    import libvirt

    found: list[Existing] = []
    by_mac: dict[str, str] = {}
    problems: list[Problem] = []
    # The twin of destroy._claimed_elsewhere. Deliberately not shared: that one
    # skips its own targets before the read, and its warning names a different
    # cost -- which this module's docstring requires it to. #42 measured the
    # merge and rejected it; the nine identical lines are all boilerplate.
    for dom in conn.listAllDomains(0):
        name = "<unnamed>"
        try:
            name = dom.name()
            uuid = dom.UUIDString()
            root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))  # noqa: S314  libvirt's own XMLDesc output; D13, see preflight's module docstring
        except (libvirt.libvirtError, ET.ParseError) as exc:
            problems.append(
                Problem.warning(
                    f"domain {name!r} could not be read ({exc}), so its MACs and "
                    f"its disks were not checked against this config.",
                    where="target.libvirt",
                )
            )
            continue
        found.append(
            Existing(
                name=name,
                id=uuid,
                marker=marker_of(root),
                disks=disks_of(root),
            )
        )
        for mac in macs_of(root):
            by_mac.setdefault(mac, name)
    return found, by_mac, problems


# -- storage ---------------------------------------------------------------


def volume_facts(xml: str) -> dict[str, Any]:
    """One volume, reduced to what preflight decides on.

    ``physical`` is ``None`` when the element is absent. It is optional in
    libvirt's RNG and meaningless for non-file pools, and the rig's
    ``_cloud-images`` directory entry has none -- so its absence is a fact to carry,
    not a parse failure. ``backing`` is ``None`` for everything that is not an
    overlay, which is how ``base_volume`` counts what a replacement would break.
    """
    root = ET.fromstring(xml)  # noqa: S314  libvirt's own XMLDesc output; D13, see preflight's module docstring
    fmt = root.find("target/format")
    path = root.find("target/path")
    physical = root.find("physical")
    return {
        "name": (root.findtext("name") or ""),
        "path": (path.text if path is not None else None),
        "format": (fmt.get("type") if fmt is not None else None),
        "physical": _int_or_none(physical),
        "backing": root.findtext("backingStore/path"),
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
    except libvirt.libvirtError as exc:
        if exc.get_error_code() != ERR_NO_STORAGE_POOL:
            # A reset connection, a policy refusal, an internal error: none of them
            # says whether this pool exists, and the refusal below instructs an
            # operator to go and create one. Raising is reported by `_guard` as a
            # failed run; a confident wrong instruction is not reported at all.
            raise
        return None, [
            Problem.error(
                f"storage pool {name!r} does not exist on this host. vcows never "
                f"creates a pool -- create it on the hypervisor and re-run.",
                where="target.libvirt.pool",
            )
        ]

    if not pool.isActive():
        return None, [
            Problem.error(
                f"storage pool {name!r} exists but is not active.",
                where="target.libvirt.pool",
            )
        ]

    try:
        pool.refresh(0)
    except libvirt.libvirtError as exc:
        # Fatal for a deploy and advisory for a destroy, with no branch here:
        # `cmd_deploy` refuses on any fatal `Discovered.problems` and `cmd_destroy`
        # prints them and carries on. Destroy's own refresh, in
        # `destroy._refresh_pools`, is a WARNING for the same reason -- a teardown
        # that cannot refresh still reports every path it could not account for.
        # The pool is returned regardless: one pass reports every problem it can.
        return pool, [
            Problem.error(
                f"could not refresh pool {name!r} ({exc.get_error_message()}). "
                f"Volumes written out of band may be invisible, which can make a "
                f"present golden image look absent.",
                where="target.libvirt.pool",
            )
        ]
    return pool, []


def walk(pool: Any) -> tuple[dict[str, dict[str, Any]], list[Problem]]:
    """Every volume in the pool, keyed by name, and what could not be read.

    One walk answers three separate questions -- the orphan-volume refusal, whether
    the golden image is here and where, and D30's size comparison -- which is why
    ``prepare`` needs no session. A volume dropped here is therefore a volume none
    of the three saw, which is why the skip is reported rather than silent.
    """
    import libvirt

    facts = {}
    problems: list[Problem] = []
    for vol in pool.listAllVolumes(0):
        name = "<unnamed>"
        try:
            name = vol.name()
            entry = volume_facts(vol.XMLDesc(0))
        except (libvirt.libvirtError, ET.ParseError) as exc:
            # A volume that vanished between listing and describing, or whose XML
            # we cannot read, is not a reason to abandon the walk.
            problems.append(
                Problem.warning(
                    f"volume {name!r} could not be read ({exc}), so it was not "
                    f"considered as the golden image and is not counted as an "
                    f"orphan.",
                    where="target.libvirt.pool",
                )
            )
            continue
        facts[entry["name"]] = entry
    return facts, problems


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
            Problem.error(
                f"volume {name!r} is in pool {cfg['target']['libvirt']['pool']!r} "
                f"but reports no path, so overlays cannot back onto it.",
                where="image.base_volume_name",
            )
        ]

    try:
        local = os.stat(source).st_size
    except OSError as exc:
        return resolved, [
            Problem.warning(
                f"golden image {source!r} is not readable ({exc.strerror}), so the "
                f"copy already on the host cannot be verified against it.",
                where="image.source_qcow2",
            )
        ]

    physical = found["physical"]
    if physical is None:
        return resolved, [
            Problem.warning(
                f"volume {name!r} reports no physical size, so it cannot be checked "
                f"against {source!r}. Expected for a non-file pool.",
                where="image.base_volume_name",
            )
        ]
    if physical != local:
        # Named, not counted in passing: this volume is shared across every
        # deployment on the host, and the operator reading this is being asked
        # to act on it. The procedure offered is the non-destructive one --
        # `base_volume` returns `create: True` for a name it does not find, so
        # a new name uploads alongside and nothing running is touched.
        overlays = sum(
            1 for v in volumes.values() if v.get("backing") == resolved["path"]
        )
        return resolved, [
            Problem.error(
                f"volume {name!r} is {physical} bytes on the host but {local} bytes "
                f"locally. That is either a truncated upload or a different image "
                f"under the same name; either way every overlay would back onto it. "
                f"Set image.base_volume_name to a name this pool does not hold and "
                f"re-run: the new image uploads alongside the old one. "
                f"{overlays} volume(s) in this pool back onto {name!r} and would "
                f"stop working without it.",
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

    **Bounded to this config's VMs.** It asks about the two names each configured
    VM is entitled to and nothing else, so an orphan left by a VM since removed
    from the config is invisible to it -- and removing a VM from the config is
    the one thing this tool tells operators does not delete anything. Widening it
    to every volume in the pool would mean deciding about volumes on a shared
    pool that were never vcows's, which is what ``orphan_volumes`` exists to
    avoid asserting.
    """
    problems = []
    for vm in cfg["vms"]:
        for volume in (overlay_name(vm["name"]), seed_name(vm["name"])):
            found = volumes.get(volume)
            if found is None:
                continue
            # Whole paths. `claimed` is what the host's domains name, and a domain
            # can name a disk in any pool -- so by basename an unrelated
            # `/elsewhere/app01.qcow2` vouches for this pool's orphan.
            path = found.get("path")
            if path is None:
                problems.append(
                    Problem.error(
                        f"volume {volume!r} exists but reports no path, so no "
                        f"domain on this host can be matched against it and vcows "
                        f"cannot tell whether anything still uses it. Establish "
                        f"what it is before removing it.",
                        where=vm["name"],
                    )
                )
            elif path not in claimed:
                problems.append(
                    Problem.error(
                        f"volume {volume!r} exists but no domain on this host "
                        f"references it. A create interrupted before its domain "
                        f"was defined leaves exactly this, and so may a volume "
                        f"belonging to another deployment sharing this pool: "
                        f"names are the undecorated logical name (D16), so vcows "
                        f"cannot tell the two apart. Establish which it is before "
                        f"removing it.",
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
    except libvirt.libvirtError as exc:
        if exc.get_error_code() != ERR_NO_NETWORK:
            # As with the pool: absence is one code, not every code.
            raise
        return {}, [
            Problem.error(
                f"network {name!r} does not exist on this host.",
                where=f"nics[].network={name}",
            )
        ]

    claims: dict[str, str] = {}
    problems: list[Problem] = []
    root = ET.fromstring(net.XMLDesc(0))  # noqa: S314  libvirt's own XMLDesc output; D13, see preflight's module docstring
    for host in root.findall("ip/dhcp/host"):
        if ip := host.get("ip"):
            claims[ip] = f"a DHCP reservation on network {name!r}"
    try:
        for lease in net.DHCPLeases():
            if ip := lease.get("ipaddr"):
                claims.setdefault(ip, f"an active DHCP lease on network {name!r}")
    except libvirt.libvirtError as exc:
        # NO_SUPPORT and OPERATION_INVALID are "this network has no DHCP" -- an
        # ordinary configuration, and a line an operator would learn to ignore.
        # Anything else means the leases exist and could not be read, and an empty
        # claim set is exactly what `address_conflicts` calls free.
        if exc.get_error_code() not in (ERR_NO_SUPPORT, ERR_OPERATION_INVALID):
            problems.append(
                Problem.warning(
                    f"the DHCP leases on network {name!r} could not be read "
                    f"({exc.get_error_message()}), so an address a live lease "
                    f"holds may be reported as free. Reservations were read.",
                    where=f"nics[].network={name}",
                )
            )
    return claims, problems


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
                        Problem.error(
                            f"{address} is already {reason}.",
                            where=f"{vm['name']}.nics[{index}].ip_cidr",
                        )
                    )

            mac = mac_of(vm, index, cfg["deployment"]).lower()
            if owner := by_mac.get(mac):
                problems.append(
                    Problem.error(
                        f"MAC {mac} is already configured on domain {owner!r}.",
                        where=f"{vm['name']}.nics[{index}]",
                    )
                )
    return problems


# -- the walk --------------------------------------------------------------


def preflight(cfg: dict, session: Any) -> Discovered:
    """One pass over the target. Everything downstream runs on what this returns."""
    wanted = {vm["name"] for vm in cfg["vms"]}
    vms, by_mac, problems = _domains(session)

    # A domain that is ours, for a name in this config, is a SKIP -- its MACs are
    # ours by construction and must not be reported as somebody else's.
    ours = {e.name for e in vms if e.marker is not None and e.marker.name in wanted}
    by_mac = {mac: owner for mac, owner in by_mac.items() if owner not in ours}

    pool, pool_problems = open_pool(session, cfg["target"]["libvirt"]["pool"])
    problems += pool_problems
    artifacts: dict[str, Any] = {}

    if pool is not None:
        volumes, walk_problems = walk(pool)
        problems += walk_problems
        claimed = {path for e in vms for path in e.disks}
        base, base_problems = base_volume(cfg, volumes)
        artifacts["base_volume"] = base
        problems += base_problems
        problems += orphan_volumes(cfg, volumes, claimed)

    problems += address_conflicts(session, cfg, by_mac)
    return Discovered(vms=tuple(vms), artifacts=artifacts, problems=tuple(problems))
