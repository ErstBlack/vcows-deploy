"""Teardown, through the API.

Same shape and the same guarantees as the libvirt backend's: every object is
accounted for in an ``Outcome`` whether the run succeeds or not, the marker is
re-read immediately before anything is removed, and a partial teardown raises
while still carrying its record.

**Order matters and is not negotiable.** Stop, then delete the VM, then delete
the seed ISO. PVE refuses to delete a running VM, and deleting the ISO first
would strand it if the VM delete then failed -- the VM would still reference a
file that no longer exists.
"""

from __future__ import annotations

import logging
from typing import Any

from ...cloudinit import seed_name
from ...problems import Problem
from ..base import Existing, Outcome
from . import api

log = logging.getLogger(__name__)


class DestroyError(Exception):
    """A teardown failed. Carries what it managed to do first.

    ``cli._destroy`` mines this with ``getattr(exc, "outcome", None)`` rather
    than importing this class, which is what keeps core from importing a backend.
    """

    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        super().__init__(
            "; ".join(str(p) for p in outcome.problems) or "destroy failed"
        )


def _split(target: Existing) -> tuple[str, str]:
    """``node/vmid`` back into its halves. Built by ``preflight._existing``."""
    node, _, vmid = target.id.partition("/")
    return node, vmid


def _fail(out: Outcome, name: str, what: str, exc: object) -> None:
    out.problems.append(Problem.error(f"{what}: {exc}", where=name))


def _reverify(session: api.Session, target: Existing) -> bool:
    """Read the marker again, immediately before removing anything.

    Preflight ran earlier and an operator may have edited the VM since. This is
    the last point at which refusing costs nothing, and the check is against the
    marker rather than the name because a rename does not change ownership.
    """
    from ...marker import MarkerError, from_description

    node, vmid = _split(target)
    config = api.vm_config(session, node, vmid)
    try:
        now = from_description(config.get("description"))
    except MarkerError:
        now = None
    return now is not None and target.marker is not None and now == target.marker


def destroy(cfg: dict, session: api.Session, targets: list[Existing]) -> Outcome:
    out = Outcome()
    try:
        for target in targets:
            _one(session, target, out)
    except BaseException as exc:
        # A Ctrl-C mid-teardown still reports what was already removed. Same
        # carrier the libvirt backend uses, annotated the same way and for the
        # same reason -- `BaseException` has no `outcome`, and `cli._destroy`
        # reads it back with `getattr` rather than importing this module.
        carrier: Any = exc
        carrier.outcome = out
        raise
    if out.failed:
        raise DestroyError(out)
    return out


def _one(session: api.Session, target: Existing, out: Outcome) -> None:
    node, vmid = _split(target)
    name = target.name or vmid

    try:
        vanished = False
        try:
            same = _reverify(session, target)
        except Exception as exc:
            # PVE answers a missing vmid with a 500 carrying "does not exist";
            # rather than matching on that text -- which upstream is free to
            # reword -- the absence is inferred from the config read failing and
            # confirmed by the delete below, which is idempotent enough to skip.
            log.info("VM %s on %s could not be read (%s)", vmid, node, exc)
            vanished, same = True, False

        if vanished:
            # Nothing to stop or delete, but its seed ISO is still worth
            # collecting -- the same branch libvirt's destroy has.
            out.skipped.append(name)
            _delete_seed(session, target, out)
            return

        if not same:
            out.skipped.append(name)
            out.problems.append(
                Problem.error(
                    f"the marker on VM {vmid} changed between preflight and "
                    f"teardown; refusing to delete it",
                    where=name,
                )
            )
            return

        if api.is_running(session, node, vmid):
            api.stop_vm(session, node, vmid)
        api.delete_vm(session, node, vmid)
        out.destroyed.append(name)
        _delete_seed(session, target, out)
    except api.ProxmoxApiError as exc:
        _fail(out, name, f"could not destroy VM {vmid}", exc)


def _delete_seed(session: api.Session, target: Existing, out: Outcome) -> None:
    """Remove this VM's seed ISO, and nothing else.

    Guarded twice. ``preflight._media`` only ever records CD-ROM media in the
    configured import datastore, and this checks the basename against the one
    ``cloudinit.seed_name`` derives for the marker's *logical* name -- so an
    installer ISO an operator attached by hand is not a candidate, whatever
    storage it is on.
    """
    if target.marker is None:
        return
    wanted = seed_name(target.marker.name)
    for volid in target.disks:
        if volid.rsplit("/", 1)[-1] != wanted:
            log.debug("leaving %s attached to %s alone", volid, target.name)
            continue
        try:
            api.delete_volume(session, volid)
            out.destroyed.append(volid)
        except Exception as exc:
            # A skip rather than a stop: the VM is already gone, and the rest of
            # the targets are still worth attempting. It still makes the exit
            # code non-zero, because something vcows was asked to remove is there.
            out.skipped.append(volid)
            _fail(out, volid, "could not delete the seed ISO", exc)
