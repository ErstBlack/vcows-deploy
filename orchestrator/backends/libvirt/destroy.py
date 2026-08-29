"""Teardown, by marker, through python3-libvirt. Not `tofu destroy`.

The comparison was re-run against the provider source and a live OpenTofu 1.12.6
and it holds -- see findings.md §1. The short version: destroy is driven purely by
state, D23 throws state away every deploy, and a `tofu destroy` with empty state
reports "No objects need to be destroyed" and exits 0. Worse, on the first deploy to
a host ``libvirt_volume.base`` *is* written to state, and destroy ignores the
``count`` guard that protects it in config -- so the obvious implementation deletes
the shared golden image out from under every other deployment on that host. Nothing
here can make that mistake: volumes carry no marker, so destroy never sees the base.

Three things are load-bearing:

* **Destroy, then undefine. Never the reverse.** ``qemuDomainUndefineFlags`` unlinks
  ``/etc/libvirt/qemu/<name>.xml`` -- where the marker lives -- *before* flipping the
  domain transient, with no rollback. Undefining a running domain therefore leaves a
  VM running with no persistent config and no owner, invisible to every future
  preflight. The crash window this ordering does have is the safe one: between the
  two calls the domain is off, still defined, still marked, and a re-run finishes it.
* **``NVRAM`` is not droppable.** A retry loop that keeps shedding bits will
  eventually shed it, and that does not degrade gracefully -- undefining an EFI
  domain without it fails with ``OPERATION_INVALID`` instead, turning a diagnosable
  flag error into an undiagnosable undefine failure.
* **Every object's outcome is reported, and any failure is fatal.** Five domains with
  three objects each is twenty things that can fail independently. Silent partial
  success is the specific defect findings.md §1 rejects `tofu destroy` for.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from ..base import Existing, Outcome, Problem, Severity
from .errors import (
    ERR_INVALID_ARG,
    ERR_NO_DOMAIN,
    ERR_NO_STORAGE_VOL,
    ERR_OPERATION_INVALID,
)
from .preflight import disks_of, marker_of
from .render import overlay_name, seed_name

# Undefine flags, as ABI constants rather than attribute lookups, so the mask
# builder is a pure function testable with no libvirt installed.
# `tests/test_libvirt_destroy.py` pins each against the installed binding.
UNDEFINE_MANAGED_SAVE = 1  # since 0.9.4
UNDEFINE_SNAPSHOTS_METADATA = 2  # since 0.9.5
UNDEFINE_NVRAM = 4  # since 1.2.9
UNDEFINE_CHECKPOINTS_METADATA = 16  # since 5.6.0
UNDEFINE_TPM = 32  # since 8.9.0

#: Never dropped. All three predate libvirt 1.2.9, so no supported target rejects
#: them -- and dropping ``NVRAM`` makes an EFI domain refuse to undefine at all.
FLOOR = UNDEFINE_MANAGED_SAVE | UNDEFINE_SNAPSHOTS_METADATA | UNDEFINE_NVRAM

#: ``version`` here is libvirt's packed form: major * 1e6 + minor * 1e3 + release.
_GATED = ((5006000, UNDEFINE_CHECKPOINTS_METADATA), (8009000, UNDEFINE_TPM))


def undefine_mask(version: int) -> int:
    """The strongest mask this daemon accepts.

    Gate on ``conn.getLibVersion()`` -- the *daemon's* version. The client's is a
    different number entirely (on the rig: daemon 12000000, client 11010000), and
    flag validation happens server-side in ``qemuDomainUndefineFlags``.

    Every bit here has existed since libvirt 8.9.0, and RHEL 9.8 and RHEL 10.2 both
    ship 11.10.0, so in practice this only ever matters on 9.0/9.1 EUS. It is eight
    lines of insurance, not the central risk this file was scoped around.
    """
    mask = FLOOR
    for introduced, bit in _GATED:
        if version >= introduced:
            mask |= bit
    return mask


class DestroyError(Exception):
    """At least one object could not be torn down.

    Carries the whole ``Outcome``, and says the whole of it: this is the one path
    where core never sees the record, because ``cmd_destroy`` gets an exception
    instead of a return value. A message naming only the fatal problems would drop
    every leaked volume beside them.
    """

    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        lines = [str(p) for p in outcome.problems if p.fatal]
        lines += [f"  skipped: {name}" for name in outcome.skipped]
        lines += [f"  {p}" for p in outcome.problems if not p.fatal]
        super().__init__("\n".join(lines))


def _stop(dom: Any, name: str, out: Outcome) -> bool:
    """Force the domain off. True if it is now safe to undefine.

    ``isActive`` is a round trip like any other and belongs inside the ``try``. A
    raise from it left this function entirely, and with it ``destroy``'s loop: every
    target after this one went untouched and the operator got a traceback in place
    of the Outcome naming what was left behind.
    """
    import libvirt

    try:
        if not dom.isActive():
            return True
        dom.destroyFlags(0)
    except libvirt.libvirtError as exc:
        # Another operator shut it down between the check and the call. Anything
        # else -- OPERATION_FAILED, INTERNAL_ERROR -- is a real failure.
        if exc.get_error_code() == ERR_OPERATION_INVALID and _is_off(dom):
            return True
        out.problems.append(
            Problem(Severity.ERROR, f"could not stop: {exc.get_error_message()}", name)
        )
        return False
    return True


def _is_off(dom: Any) -> bool:
    """Confirmed inactive. A host that cannot answer is **not** confirmed.

    This is the second half of the race check above, and it decides whether a
    refusal to stop is reported at all. Reading an unanswerable question as "off"
    reports a finished teardown for a domain that may still be running.
    """
    import libvirt

    try:
        return not dom.isActive()
    except libvirt.libvirtError:
        return False


def _undefine(dom: Any, name: str, mask: int, out: Outcome) -> bool:
    """Undefine, shedding only droppable bits if the daemon rejects the mask.

    ``virCheckFlags`` reports every offending bit at once and always as
    ``VIR_ERR_INVALID_ARG``, so one retry down to ``FLOOR`` is enough -- no
    bit-at-a-time loop, and no risk of shedding ``NVRAM``. The catch cannot swallow
    a real refusal either: a transient domain, a managed save, or an NVRAM varstore
    all report ``OPERATION_INVALID`` instead.
    """
    import libvirt

    try:
        dom.undefineFlags(mask)
        return True
    except libvirt.libvirtError as exc:
        if exc.get_error_code() != ERR_INVALID_ARG or mask == FLOOR:
            out.problems.append(
                Problem(
                    Severity.ERROR,
                    f"could not undefine: {exc.get_error_message()}",
                    name,
                )
            )
            return False

    dropped = mask & ~FLOOR
    out.problems.append(
        Problem(
            Severity.WARNING,
            f"daemon rejected undefine flags 0x{dropped:x}; retrying without them.",
            name,
        )
    )
    try:
        dom.undefineFlags(FLOOR)
    except libvirt.libvirtError as exc:
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"could not undefine: {exc.get_error_message()}",
                name,
            )
        )
        return False
    return True


def _delete_volume(conn: Any, path: str, name: str, out: Outcome) -> None:
    """Delete one disk by path.

    ``vol.delete`` takes no flags at all -- the dir/fs backend declares
    ``virCheckFlags(0, -1)``. And it offers no protection whatsoever: ``in_use`` is
    only ever set by the storage driver's own transient operations, so libvirt will
    delete a running VM's disk without complaint. Three things stand between this
    call and the shared golden image, and every one of them is upstream of here:
    the ``<backingStore>`` exclusion in ``preflight.disks_of``, and ``_deletable``'s
    two guards.

    A path that will not resolve is reported and skipped. **Never an ``os.unlink``
    fallback** -- resolving through the pool is what bounds this to storage libvirt
    manages, and reaching past it is how a teardown becomes a way to delete
    arbitrary files on somebody else's hypervisor.
    """
    import libvirt

    try:
        conn.storageVolLookupByPath(path).delete(0)
    except libvirt.libvirtError as exc:
        if exc.get_error_code() == ERR_NO_STORAGE_VOL:
            # After a refresh this means gone, and a file already gone is one
            # this teardown does not have to remove -- but it is not one it
            # removed either, so it is recorded rather than counted as success.
            # Without the refresh it would mean "invisible", which is why the one
            # in `_refresh_pools` is not optional and why an inactive pool holding
            # any of these paths is fatal there.
            out.skipped.append(path)
            return
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"could not delete {path}: {exc.get_error_message()}",
                name,
            )
        )
        return
    out.destroyed.append(path)


def _pool_holds(pool: Any, wanted: set[str]) -> list[str] | None:
    """Which of ``wanted`` live in this pool's target directory.

    Read off ``<target><path>``, which an *inactive* pool still answers -- the
    volume list, which would be the direct question, is exactly what it will not
    give. Directory comparison rather than prefix matching: ``/pool2/x.qcow2``
    does not live in ``/pool``.

    ``None`` is "could not tell", which is not the same answer as the empty list
    and must not collapse into it: an inactive pool holding both of a target's
    volumes would then pass for somebody else's idle pool, and every disk in it
    resolves as already gone.
    """
    import libvirt

    try:
        path = ET.fromstring(pool.XMLDesc(0)).findtext("./target/path")
    except (libvirt.libvirtError, ET.ParseError):
        return None
    if not path:
        return None
    return sorted(p for p in wanted if str(PurePosixPath(p).parent) == path)


def _refresh_pools(conn: Any, out: Outcome, targets: list[Existing]) -> None:
    """Rescan every active pool before resolving any path (D35).

    ``storageVolLookupByPath`` reads libvirt's in-memory pool cache. On the rig,
    three of four running domains' disks -- real files inside an active pool's own
    directory -- do not resolve without this. Skipping it would turn "report and
    skip what does not resolve" into "silently leak every overlay", which is the
    opposite of what that rule is for.

    Every pool, not just the configured one: a domain's disks are wherever they are,
    and destroy tears down what preflight found rather than what the config says.

    An inactive pool cannot be refreshed and cannot be asked for its volumes, so
    every disk it holds will resolve as ``NO_STORAGE_VOL`` -- "already gone" --
    while the file sits there and the domain that named it loses its marker. If it
    holds nothing of ours it is somebody else's idle pool and no concern of this
    teardown; if it does, that is fatal, and fatal at the *end*, so the domains are
    still torn down and the operator is told exactly what was left. A pool that
    will not say which directory it holds counts with the second: unestablished,
    not empty.

    The paths tested are preflight's, since this runs before ``_reverify``. Right
    for a question about pools, which do not move; the per-disk decision is
    ``_deletable``'s and uses the fresh list.
    """
    import libvirt

    wanted = {path for t in targets for path in t.disks}
    try:
        pools = conn.listAllStoragePools(0)
    except libvirt.libvirtError as exc:
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"could not list this host's storage pools "
                f"({exc.get_error_message()}), so none of them was refreshed and a "
                f"disk that is still on this host may resolve as already gone. "
                f"Nothing below can be accounted for.",
                "storage",
            )
        )
        return

    for pool in pools:
        # `name` and `isActive` are calls, not attributes. A pool that cannot
        # answer either is one whose contents are unknown, which is fatal for the
        # same reason an inactive one holding our disks is -- but only for itself:
        # a target's disks are wherever they are, which is why every pool is
        # walked, and the remaining pools still have to be refreshed.
        name = "<unnamed>"
        try:
            name = pool.name()
            active = pool.isActive()
        except libvirt.libvirtError as exc:
            out.problems.append(
                Problem(
                    Severity.ERROR,
                    f"storage pool {name!r} could not be interrogated "
                    f"({exc.get_error_message()}), so whether it holds a disk of "
                    f"ours is unknown and it was not refreshed.",
                    "storage",
                )
            )
            continue

        if not active:
            held = _pool_holds(pool, wanted)
            if held is None:
                out.problems.append(
                    Problem(
                        Severity.ERROR,
                        f"storage pool {name!r} is not active and would not say "
                        f"which directory it holds, so whether a disk of ours is "
                        f"in it cannot be established. Start the pool and re-run.",
                        "storage",
                    )
                )
            elif held:
                out.problems.append(
                    Problem(
                        Severity.ERROR,
                        f"storage pool {name!r} is not active, so "
                        f"{', '.join(held)} cannot be resolved or deleted and will "
                        f"be left on this host. Start the pool and re-run.",
                        "storage",
                    )
                )
            continue
        try:
            pool.refresh(0)
        except libvirt.libvirtError as exc:
            out.problems.append(
                Problem(
                    Severity.WARNING,
                    f"could not refresh pool {name!r} "
                    f"({exc.get_error_message()}); disks in it may not resolve.",
                    "storage",
                )
            )


def _claimed_elsewhere(
    conn: Any, out: Outcome, targets: list[Existing]
) -> set[str] | None:
    """Every disk path some *other* domain on this host currently names.

    ``vol.delete`` offers no protection at all -- libvirt will delete a running
    VM's disk without complaint -- so this is the check that stands in for the one
    the storage driver does not do. It is also the only guard available for a
    target whose domain has already gone: there is no XML left to re-read, so its
    recorded paths are all we have, and a path recorded minutes ago may since have
    been handed to somebody else.

    ``None`` means the host could not be asked, which is fatal rather than
    ignorable: proceeding without this is exactly the case it exists to prevent.
    """
    import libvirt

    ours = {t.id for t in targets}
    claimed: set[str] = set()
    try:
        domains = conn.listAllDomains(0)
    except libvirt.libvirtError:
        return None
    for dom in domains:
        name = "<unnamed>"
        try:
            name = dom.name()
            if dom.UUIDString() in ours:
                continue
            root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except (libvirt.libvirtError, ET.ParseError) as exc:
            # A domain that vanished mid-scan claims nothing, and that is the
            # common case -- but one that could not be read may claim a disk this
            # teardown is about to delete, and this set is the only guard there is.
            # Reported rather than fatal: a broken foreign domain is not this
            # deployment's to fix, and refusing every teardown on the host over it
            # is worse than saying which guard was narrowed.
            out.problems.append(
                Problem(
                    Severity.WARNING,
                    f"domain {name!r} could not be read ({exc}), so any disk it "
                    f"claims was not checked against the ones being deleted.",
                    "storage",
                )
            )
            continue
        claimed.update(disks_of(root))
    return claimed


def _reverify(dom: Any, target: Existing, out: Outcome) -> Existing | None:
    """The target as the domain describes it **now**. ``None`` means leave it alone.

    ``preflight`` read the marker and the disk list, and ``cmd_destroy`` then
    waited on an operator at a terminal. That wait is unbounded and a host does
    not stop changing during it, so nothing below acts on the older document.
    """
    import libvirt

    try:
        root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    except (libvirt.libvirtError, ET.ParseError) as exc:
        out.problems.append(
            Problem(Severity.ERROR, f"could not re-read: {exc}", target.name)
        )
        return None

    marker = marker_of(root)
    if marker != target.marker:
        out.skipped.append(target.name)
        out.problems.append(
            Problem(
                Severity.ERROR,
                "its ownership marker changed between preflight and now "
                f"({target.marker} -> {marker}); refusing to tear down a domain "
                f"somebody else has taken over",
                target.name,
            )
        )
        return None
    return replace(target, disks=disks_of(root))


def _deletable(path: str, target: Existing, claimed: set[str], out: Outcome) -> bool:
    """Whether this teardown is allowed to delete this path.

    ``disks_of`` collects every file-backed source a domain names, which is the
    right width for discovery and too wide for deletion: a domain we own can
    still have been given a disk we do not, and the ``<backingStore>`` exclusion
    is the *only* other thing between this and the shared golden image.
    """
    if path in claimed:
        out.skipped.append(path)
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"{path} is claimed by another domain on this host; leaving it",
                target.name,
            )
        )
        return False

    owned = (
        {overlay_name(target.marker.name), seed_name(target.marker.name)}
        if target.marker is not None
        else set()
    )
    if PurePosixPath(path).name not in owned:
        out.skipped.append(path)
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"{path} is not one of the names this VM owns "
                f"({', '.join(sorted(owned)) or 'none -- it carries no marker'}); "
                f"leaving it",
                target.name,
            )
        )
        return False
    return True


def destroy(cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
    """Tear down exactly the set preflight discovered. Raises on any failure.

    Returns the record for the runs that did not fail but did not finish either:
    a domain already gone, a volume that would not resolve. Those are not errors
    and they are not nothing, and the caller is the only thing that can say so.
    """
    import libvirt

    out = Outcome()
    try:
        mask = undefine_mask(session.getLibVersion())
    except libvirt.libvirtError as exc:
        out.problems.append(
            Problem(
                Severity.ERROR,
                f"could not ask this host its libvirt version "
                f"({exc.get_error_message()}), so the undefine flags cannot be "
                f"chosen; nothing was torn down",
                "storage",
            )
        )
        raise DestroyError(out) from exc
    _refresh_pools(session, out, targets)

    claimed = _claimed_elsewhere(session, out, targets)
    if claimed is None:
        out.problems.append(
            Problem(
                Severity.ERROR,
                "could not list this host's domains, so a recorded disk path "
                "cannot be checked against what else claims it; nothing was "
                "torn down",
                "storage",
            )
        )
        raise DestroyError(out)

    for target in targets:
        try:
            dom = session.lookupByUUIDString(target.id)
        except libvirt.libvirtError as exc:
            if exc.get_error_code() != ERR_NO_DOMAIN:
                # We have been told nothing about this domain, so we know nothing
                # about what it owns. Deleting its recorded disks on the strength
                # of a failed lookup is the one thing this branch must not do.
                out.problems.append(
                    Problem(
                        Severity.ERROR,
                        f"could not look up: {exc.get_error_message()}",
                        target.name,
                    )
                )
                continue
            # Already gone. Its disks may not be, so they are still resolved
            # below -- from the preflight snapshot, which is why `_deletable`
            # rather than the snapshot decides what may go.
            out.skipped.append(target.name)
        else:
            fresh = _reverify(dom, target, out)
            if fresh is None:
                continue
            target = fresh
            if not _stop(dom, target.name, out):
                continue
            if not _undefine(dom, target.name, mask, out):
                continue
            out.destroyed.append(target.name)

        for path in target.disks:
            if _deletable(path, target, claimed, out):
                _delete_volume(session, path, target.name, out)

    if out.failed:
        raise DestroyError(out)
    return out
