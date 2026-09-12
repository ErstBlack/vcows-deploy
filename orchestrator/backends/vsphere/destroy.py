"""Teardown, through the vCenter API.

Same shape and the same guarantees as the other two backends': every object is
accounted for in an ``Outcome`` whether the run succeeds or not, the marker is
re-read immediately before anything is removed, and a partial teardown raises
while still carrying its record.

**Order matters and is not negotiable.** Power off, then destroy the VM, then
delete the seed ISO. vCenter refuses ``Destroy_Task`` on a running VM, and
deleting the ISO first would strand it if the destroy then failed -- the VM
would still have a CD-ROM backed by a file that is gone.

**The template is never a target.** ``preflight._existing`` already drops every
VM carrying ``config.template``, and this checks again on the object it
re-resolved: the golden image carries a marker of ours too, and destroying it
would take every other deployment's linked clones with it. Removing it is #165's
job, not this one's.

**Nothing here holds a managed object from preflight.** ``api.Session`` says
why: a target is a uuid, and the VM behind it is looked up again through the
property collector -- which is the same read that answers what its annotation
and its power state are *now*.
"""

from __future__ import annotations

import logging
from typing import Any

from ...cloudinit import seed_name
from ...marker import MarkerError, from_description
from ...problems import Problem
from ..base import Existing, Outcome, carrying
from . import api

log = logging.getLogger(__name__)

#: What ``runtime.powerState`` reads as for a VM that is running. Spelt out
#: rather than reached through ``vim``, because pyvmomi's enum members are these
#: strings and importing the SDK here would be a module-scope import of it.
POWERED_ON = "poweredOn"


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


def _fail(out: Outcome, name: str, what: str, exc: object) -> None:
    out.problems.append(Problem.error(f"{what}: {exc}", where=name))


def _find(session: api.Session, target: Existing) -> dict | None:
    """The VM this target names, as vCenter describes it right now, or None.

    By uuid, because that is what ``Existing.id`` holds and a rename does not
    change ownership. None means it is no longer there.

    One inventory walk per target rather than one per teardown, and deliberately:
    the answer carries the annotation the re-verify below reads, so "the marker
    was read immediately before this VM was destroyed" is true of every target
    rather than of the first one. A teardown is a handful of VMs.
    """
    return next(
        (
            props
            for props in api.vms(session.content)
            if str(props.get("summary.config.uuid") or "") == target.id
        ),
        None,
    )


def _reverify(props: dict, target: Existing) -> bool:
    """Read the marker again, immediately before removing anything.

    Preflight ran earlier and an operator may have edited the VM since. This is
    the last point at which refusing costs nothing, and the check is against the
    marker rather than the name because a rename does not change ownership.

    No round trip of its own: ``config.annotation`` came back with the walk that
    resolved the VM.
    """
    try:
        now = from_description(props.get("config.annotation"))
    except MarkerError:
        # Damaged rather than absent, and not ours either way.
        return False
    # The `is not None` is what makes this safe, and the equality alone would
    # not be: an unmarked VM and an unmarked target both read as None, and None
    # equals None, so the two would compare as a match and the VM would be
    # destroyed on the strength of neither of them carrying a marker.
    return now is not None and now == target.marker


def destroy(cfg: dict, session: api.Session, targets: list[Existing]) -> Outcome:
    from pyVmomi import vim

    out = Outcome()
    # `DestroyError` below is the only other route `out` takes out of here.
    with carrying(outcome=out):
        # Resolved again rather than carried from preflight, and needed because
        # vCenter cannot resolve a `[ds] path` without the datacenter it is in.
        # A name that stopped resolving between the two is not refused here: the
        # VMs are still destroyable, and the seed that could not be deleted is
        # reported as the leak it is.
        # Preflight refuses a datacenter name that resolves twice, so what
        # comes back here is one datacenter or none.
        found = api.find_by_name(
            session.content, vim.Datacenter, cfg["target"]["vsphere"]["datacenter"]
        )
        datacenter = found[0] if found else None
        for target in targets:
            _one(session, datacenter, target, out)
    if out.failed:
        raise DestroyError(out)
    return out


def _one(session: api.Session, datacenter: Any, target: Existing, out: Outcome) -> None:
    name = target.name or target.id

    try:
        props = _find(session, target)
        if props is None:
            # Gone between preflight and teardown. Nothing to power off or
            # destroy, but its seed ISO is still worth collecting -- the same
            # branch the other two backends have.
            log.info("VM %s (%s) is no longer on this vCenter", name, target.id)
            out.skipped.append(name)
            _delete_seed(session, datacenter, target, out)
            return

        if props.get("config.template"):
            out.skipped.append(name)
            out.problems.append(
                Problem.error(
                    f"VM {target.id} is a template now; refusing to destroy it, "
                    f"because every other deployment's clones are overlays on it",
                    where=name,
                )
            )
            return

        if not _reverify(props, target):
            out.skipped.append(name)
            out.problems.append(
                Problem.error(
                    f"the marker on VM {target.id} changed between preflight and "
                    f"teardown; refusing to destroy it",
                    where=name,
                )
            )
            return

        vm = props["obj"]
        if props.get("runtime.powerState") == POWERED_ON:
            api.wait(vm.PowerOffVM_Task(), f"power off {name}")
        api.wait(vm.Destroy_Task(), f"destroy {name}")
        out.destroyed.append(name)
        _delete_seed(session, datacenter, target, out)
    except api.VsphereApiError as exc:
        _fail(out, name, f"could not destroy VM {target.id}", exc)


def _delete_seed(
    session: api.Session, datacenter: Any, target: Existing, out: Outcome
) -> None:
    """Remove this VM's seed ISO, and nothing else.

    Guarded twice. ``preflight._media`` only ever records CD-ROM media on the
    configured datastore, and this checks the file name against the one
    ``cloudinit.seed_name`` derives for the marker's *logical* name -- so an
    installer ISO an operator attached by hand is not a candidate, whatever
    datastore it is on.
    """
    if target.marker is None:
        return
    wanted = seed_name(target.marker.name)
    for path in target.disks:
        # `rpartition` rather than `rsplit("/", 1)[-1]`: same answer for a path
        # with a folder in it and for one at the datastore root, and it carries
        # neither of the two numbers, which are equivalent-mutant knobs --
        # `preflight._orphan_seeds` is spelt this way for that reason.
        if path.rpartition("/")[-1] != wanted:
            log.debug("leaving %s attached to %s alone", path, target.name)
            continue
        try:
            api.wait(
                session.content.fileManager.DeleteDatastoreFile_Task(
                    name=path, datacenter=datacenter
                ),
                f"delete {path}",
            )
            out.destroyed.append(path)
        except Exception as exc:
            # A skip rather than a stop: the VM is already gone, and the rest of
            # the targets are still worth attempting. It still makes the exit
            # code non-zero, because something vcows was asked to remove is there.
            out.skipped.append(path)
            _fail(out, path, "could not delete the seed ISO", exc)
