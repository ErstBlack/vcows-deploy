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

from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from ..base import Existing, Problem, Severity
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

#: libvirt error codes. Matched numerically, never by message: the NVRAM refusal
#: has been reworded three times, and the rig and the RHEL targets agree on the
#: wording only until the next Fedora update.
ERR_OPERATION_INVALID = 55
ERR_NO_STORAGE_VOL = 50
ERR_INVALID_ARG = 8
#: The *only* code that means "already gone". Every other failure of
#: ``lookupByUUIDString`` -- a reset connection, a policy refusal, an internal
#: error -- says nothing about whether that domain still exists, so it must not
#: be read as one that does not.
ERR_NO_DOMAIN = 42


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


@dataclass
class Outcome:
    """What actually happened, per object. The point of the exercise."""

    destroyed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(p.fatal for p in self.problems)


class DestroyError(Exception):
    """At least one object could not be torn down."""

    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        super().__init__("\n".join(str(p) for p in outcome.problems if p.fatal))


def _stop(dom: Any, name: str, out: Outcome) -> bool:
    """Force the domain off. True if it is now safe to undefine."""
    import libvirt

    if not dom.isActive():
        return True
    try:
        dom.destroyFlags(0)
    except libvirt.libvirtError as exc:
        # Another operator shut it down between the check and the call. Anything
        # else -- OPERATION_FAILED, INTERNAL_ERROR -- is a real failure.
        if exc.get_error_code() == ERR_OPERATION_INVALID and not dom.isActive():
            return True
        out.problems.append(
            Problem(Severity.ERROR, f"could not stop: {exc.get_error_message()}", name)
        )
        return False
    return True


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
    delete a running VM's disk without complaint. The ``<backingStore>`` exclusion in
    ``preflight.disks_of`` is the only thing between this call and the shared golden
    image.

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
            # After a refresh this genuinely means gone, which is success for a
            # teardown. Without one it would mean "invisible", which is why the
            # refresh in `_refresh_pools` is not optional.
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


def _refresh_pools(conn: Any, out: Outcome) -> None:
    """Rescan every active pool before resolving any path (D35).

    ``storageVolLookupByPath`` reads libvirt's in-memory pool cache. On the rig,
    three of four running domains' disks -- real files inside an active pool's own
    directory -- do not resolve without this. Skipping it would turn "report and
    skip what does not resolve" into "silently leak every overlay", which is the
    opposite of what that rule is for.

    Every pool, not just the configured one: a domain's disks are wherever they are,
    and destroy tears down what preflight found rather than what the config says.
    """
    import libvirt

    for pool in conn.listAllStoragePools(0):
        if not pool.isActive():
            continue
        try:
            pool.refresh(0)
        except libvirt.libvirtError as exc:
            out.problems.append(
                Problem(
                    Severity.WARNING,
                    f"could not refresh pool {pool.name()!r} "
                    f"({exc.get_error_message()}); disks in it may not resolve.",
                    "storage",
                )
            )


def _claimed_elsewhere(conn: Any, targets: list[Existing]) -> set[str] | None:
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
        try:
            if dom.UUIDString() in ours:
                continue
            root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
        except (libvirt.libvirtError, ET.ParseError):
            # A domain that vanished mid-scan claims nothing. One that will not
            # parse is `walk`'s problem, not this loop's.
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


def destroy(cfg: dict, session: Any, targets: list[Existing]) -> None:
    """Tear down exactly the set preflight discovered. Raises on any failure."""
    import libvirt

    out = Outcome()
    mask = undefine_mask(session.getLibVersion())
    _refresh_pools(session, out)

    claimed = _claimed_elsewhere(session, targets)
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
