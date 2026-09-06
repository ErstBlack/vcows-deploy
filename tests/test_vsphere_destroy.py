"""Teardown: ordering, accounting, and the refusals.

The questions here are the ones the other two backends' destroy tests ask,
because they are the ones that matter regardless of hypervisor: is the VM
powered off before it is destroyed, is the marker re-read immediately before
anything is removed, and does a partial teardown still report everything it did.

Two are this backend's own. A target is a uuid and nothing else -- the session
carries no managed object -- so the VM is looked up again, and a rename between
preflight and teardown must not lose it. And a target that is a template now is
refused: the golden image carries a marker of ours, and destroying it would take
every other deployment's linked clones with it.
"""

from __future__ import annotations

from typing import Any

import pytest
from pyVmomi import vim, vmodl

from orchestrator.backends.base import Existing
from orchestrator.backends.vsphere import VsphereBackend, api, destroy
from orchestrator.marker import Marker
from orchestrator.problems import Severity
from tests.fake_vsphere import (
    COOKIE,
    FakeContent,
    FakeServiceInstance,
    FakeTask,
    FakeVm,
    mo,
)

#: Every test here waits on a fake task. See `conftest._no_vsphere_polling_delay`.
pytestmark = pytest.mark.usefixtures("_no_vsphere_polling_delay")

#: Where `create` uploads a seed ISO, and so what `Existing.disks` carries.
APP01_SEED = "[ds-a] vcows/app01/app01-seed.iso"


def world(vms=(), files=(), datacenter: str = "dc-a") -> FakeContent:
    """The vCenter `VSPHERE_CONFIG` names, holding the VMs and files a test gives
    it. Only the datacenter is resolved by a teardown -- it is what vCenter needs
    to resolve a `[ds] path` -- so nothing else is built here."""
    content = FakeContent(
        objects=[mo(vim.Datacenter, "datacenter-1", name=datacenter)], vms=vms
    )
    content.fileManager.files = list(files)
    return content


def session(content: FakeContent) -> api.Session:
    """A `Session` onto a fake vCenter, as `connect` would have yielded it."""
    return api.Session(si=FakeServiceInstance(content), content=content, cookie=COOKIE)


def marker(name: str, deployment: str = "lab-a") -> Marker:
    return Marker.for_vm(name, deployment)


def vm(name: str, deployment: str = "lab-a", **kw) -> FakeVm:
    return FakeVm(name, annotation=marker(name, deployment).to_description(), **kw)


def target(name: str, uuid: str | None = None, disks=()) -> Existing:
    return Existing(
        name=name, id=uuid or f"uuid-{name}", marker=marker(name), disks=disks
    )


def verbs(content: FakeContent) -> list[tuple]:
    """The calls that changed something, in the order they were made."""
    return [
        call
        for call in content.calls
        if call[0] in ("PowerOffVM_Task", "Destroy_Task", "DeleteDatastoreFile_Task")
    ]


def test_a_stopped_vm_is_destroyed_and_accounted_for(vsphere_cfg):
    w = world(vms=[vm("app01", power_state="poweredOff")])
    out = destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
    assert out.problems == []
    assert w.vms[0].destroyed


def test_a_running_vm_is_powered_off_before_it_is_destroyed(vsphere_cfg):
    """vCenter refuses to destroy a running VM, and the fake models that -- so
    this fails loudly if the order is ever reversed, rather than silently."""
    w = world(vms=[vm("app01")])
    out = destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
    assert verbs(w) == [("PowerOffVM_Task", "app01"), ("Destroy_Task", "app01")]


def test_a_stopped_vm_is_not_asked_to_power_off_again(vsphere_cfg):
    w = world(vms=[vm("app01", power_state="poweredOff")])
    destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert verbs(w) == [("Destroy_Task", "app01")]


def test_the_vm_is_found_by_uuid_because_a_rename_does_not_change_ownership(
    vsphere_cfg,
):
    """`Existing.id` is the vCenter uuid and identity is the marker, so a VM an
    operator renamed between preflight and teardown is still this target's."""
    w = world(vms=[vm("renamed-by-hand", power_state="poweredOff", uuid="uuid-app01")])
    w.vms[0].props["config.annotation"] = marker("app01").to_description()
    out = destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
    assert w.vms[0].destroyed


def test_the_marker_is_re_read_immediately_before_destroying(vsphere_cfg):
    """Preflight ran earlier and an operator may have edited the VM since. This
    is the last point at which refusing costs nothing, and the read is per
    target: the walk before the second destroy sees what the first one left."""
    w = world(
        vms=[
            vm("app01", power_state="poweredOff"),
            vm("app02", power_state="poweredOff"),
        ]
    )
    destroy.destroy(vsphere_cfg, session(w), [target("app01"), target("app02")])
    reads = [i for i, call in enumerate(w.calls) if call[0] == "RetrieveContents"]
    destroys = [i for i, call in enumerate(w.calls) if call[0] == "Destroy_Task"]
    assert len(reads) == 2
    assert reads[0] < destroys[0] < reads[1] < destroys[1]


def test_a_changed_marker_refuses_that_vm_and_keeps_going(vsphere_cfg):
    w = world(
        vms=[
            vm("app01", "somebody-else", power_state="poweredOff"),
            vm("app02", power_state="poweredOff"),
        ]
    )
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01"), target("app02")])
    out = caught.value.outcome
    assert out.skipped == ["app01"]
    assert out.destroyed == ["app02"]
    assert not w.vms[0].destroyed
    assert "marker on VM uuid-app01 changed" in str(caught.value)
    # Filed under the VM it is about, so `run.json` names what to go and look at.
    assert [p.where for p in out.problems] == ["app01"]


def test_an_unmarked_vm_at_that_uuid_is_refused(vsphere_cfg):
    """The annotation an operator overwrote reads as unmarked, and unmarked is
    never ours -- the same refusal as a marker that changed."""
    w = world(vms=[FakeVm("app01", power_state="poweredOff", annotation="prod db")])
    with pytest.raises(destroy.DestroyError):
        destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert not w.vms[0].destroyed


def test_a_marker_that_will_not_parse_refuses_rather_than_destroying(vsphere_cfg):
    """Damaged rather than absent, and the answer is the same: the last point at
    which refusing costs nothing is this one."""
    w = world(
        vms=[FakeVm("app01", power_state="poweredOff", annotation="vcows: {oops")]
    )
    with pytest.raises(destroy.DestroyError):
        destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert not w.vms[0].destroyed


def test_an_unmarked_target_names_no_seed_to_delete(vsphere_cfg):
    """`cli._destroy` only ever passes marked VMs, so this is the guard rather
    than a path -- and without it the seed name would be derived off None."""
    w = world(files=[APP01_SEED])
    out = destroy.destroy(
        vsphere_cfg,
        session(w),
        [Existing(name="app01", id="uuid-app01", marker=None, disks=(APP01_SEED,))],
    )
    assert out.skipped == ["app01"]
    assert out.destroyed == []
    assert w.fileManager.files == [APP01_SEED]


def test_two_unmarked_records_are_not_a_match(vsphere_cfg):
    """The trap the `is not None` in `_reverify` exists for: an unmarked VM and
    an unmarked target both read as None, and None equals None."""
    w = world(vms=[FakeVm("app01", power_state="poweredOff")])
    with pytest.raises(destroy.DestroyError):
        destroy.destroy(
            vsphere_cfg,
            session(w),
            [Existing(name="app01", id="uuid-app01", marker=None)],
        )
    assert not w.vms[0].destroyed


def test_a_target_that_is_a_template_now_is_left_alone(vsphere_cfg):
    """The golden image carries a marker of ours, and every other deployment's
    linked clones are overlays on its disk. #165 is where removing it would
    live; a teardown refuses."""
    w = world(vms=[vm("app01", power_state="poweredOff", template=True)])
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    out = caught.value.outcome
    assert out.skipped == ["app01"]
    assert out.destroyed == []
    assert not w.vms[0].destroyed
    assert "is a template now" in str(caught.value)
    # Filed under the VM, like the marker refusal: `run.json` names what to look at.
    assert [p.where for p in out.problems] == ["app01"]


def test_a_vm_that_vanished_is_skipped_not_failed(vsphere_cfg):
    """A VM that disappeared between preflight and teardown. Its seed ISO is
    still worth collecting -- the same branch the other two backends have."""
    w = world(files=[APP01_SEED])
    out = destroy.destroy(
        vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))]
    )
    assert out.skipped == ["app01"]
    assert out.destroyed == [APP01_SEED]
    assert out.problems == []


def test_the_seed_iso_is_deleted_with_the_vm_and_after_it(vsphere_cfg):
    """Deleting the ISO first would strand it if the destroy then failed: the VM
    would still have a CD-ROM backed by a file that is gone."""
    w = world(vms=[vm("app01", power_state="poweredOff")], files=[APP01_SEED])
    out = destroy.destroy(
        vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))]
    )
    assert out.destroyed == ["app01", APP01_SEED]
    assert verbs(w) == [
        ("Destroy_Task", "app01"),
        ("DeleteDatastoreFile_Task", APP01_SEED),
    ]
    assert w.fileManager.files == []


def test_the_delete_names_the_datacenter_the_path_is_resolved_in(vsphere_cfg):
    """vCenter cannot resolve `[ds-a] vcows/...` without one, and the session
    carries no managed object, so the name is resolved again here."""
    w = world(vms=[vm("app01", power_state="poweredOff")], files=[APP01_SEED])
    destroy.destroy(vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))])
    assert w.fileManager.deleted == [(APP01_SEED, "dc-a")]


def test_a_datacenter_that_stopped_resolving_leaks_the_seed_rather_than_the_vm(
    vsphere_cfg,
):
    """Renamed between preflight and teardown. The VM is still destroyable by
    uuid; the file that could not be deleted is reported as what is left."""
    w = world(vms=[vm("app01", power_state="poweredOff")], datacenter="renamed")
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))])
    out = caught.value.outcome
    assert out.destroyed == ["app01"]
    assert out.skipped == [APP01_SEED]
    assert [p.where for p in out.problems] == [APP01_SEED]


def test_media_that_is_not_this_vms_seed_is_left_alone(vsphere_cfg):
    """Guarded on the file name `cloudinit.seed_name` derives for the marker's
    logical name, so an installer ISO attached by hand is not a candidate -- and
    the seed beside it is still collected."""
    other = "[ds-a] isos/rocky10-dvd.iso"
    w = world(vms=[vm("app01", power_state="poweredOff")], files=[other, APP01_SEED])
    out = destroy.destroy(
        vsphere_cfg, session(w), [target("app01", disks=(other, APP01_SEED))]
    )
    assert out.destroyed == ["app01", APP01_SEED]
    assert w.fileManager.files == [other]


def test_media_at_the_datastore_root_is_left_alone_rather_than_read_past_its_end(
    vsphere_cfg,
):
    """An ISO attached from the top of a datastore is `[ds-a] rocky10.iso`, with
    no `/` in it at all. It is not this VM's seed, and reading it must not end
    the teardown with an IndexError instead of an Outcome."""
    root = "[ds-a] rocky10.iso"
    w = world(vms=[vm("app01", power_state="poweredOff")], files=[root])
    out = destroy.destroy(vsphere_cfg, session(w), [target("app01", disks=(root,))])
    assert out.destroyed == ["app01"]
    assert out.problems == []
    assert w.fileManager.files == [root]


def test_a_seed_that_will_not_delete_is_a_skip_not_a_stop(vsphere_cfg):
    """The VM is already gone and the other targets are still worth attempting.
    It still makes the exit code non-zero, because something vcows was asked to
    remove is still there."""
    w = world(vms=[vm("app01", power_state="poweredOff")], files=[APP01_SEED])
    w.fileManager.error = vim.fault.CannotAccessFile(msg="the datastore is read-only")
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))])
    out = caught.value.outcome
    assert out.destroyed == ["app01"]
    assert out.skipped == [APP01_SEED]
    # The file, not the VM: the VM is gone and the ISO is what is still there.
    assert [p.where for p in out.problems] == [APP01_SEED]


def test_a_failed_task_is_a_failure_not_a_success(vsphere_cfg):
    """`info.state` goes to `error` as readily as to `success`, and a teardown
    that only waited for the task to stop reports a destroy that never
    happened."""
    w = world(vms=[vm("app01", power_state="poweredOff")])
    w.vms[0].destroy_error = vmodl.fault.SystemError(msg="the host is unreachable")
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert "the host is unreachable" in str(caught.value)
    assert caught.value.outcome.destroyed == []
    assert [p.where for p in caught.value.outcome.problems] == ["app01"]


def test_a_power_off_that_fails_stops_that_vm_being_destroyed(vsphere_cfg):
    """The seed ISO is not collected either: the VM is still there and still
    booted from it."""
    w = world(vms=[vm("app01")], files=[APP01_SEED])
    w.vms[0].power_off_error = vmodl.fault.SystemError(msg="no response")
    with pytest.raises(destroy.DestroyError):
        destroy.destroy(vsphere_cfg, session(w), [target("app01", disks=(APP01_SEED,))])
    assert not w.vms[0].destroyed
    assert w.fileManager.files == [APP01_SEED]


def test_a_task_that_never_finishes_times_out_rather_than_hanging(
    vsphere_cfg, monkeypatch
):
    monkeypatch.setattr(api, "TASK_TIMEOUT", 0)
    w = world(vms=[vm("app01", power_state="poweredOff")])
    monkeypatch.setattr(
        w.vms[0].mo, "Destroy_Task", lambda: FakeTask(never_finishes=True)
    )
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert "had not finished" in str(caught.value)


def test_an_interrupt_still_carries_what_was_already_destroyed(
    vsphere_cfg, monkeypatch
):
    """A Ctrl-C mid-teardown. `cli._destroy` reads this back with getattr rather
    than importing this module, so the attribute has to survive the raise."""
    w = world(
        vms=[
            vm("app01", power_state="poweredOff"),
            vm("app02", power_state="poweredOff"),
        ]
    )
    calls = {"n": 0}
    real = api.wait

    def interrupt(task, what):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(task, what)

    monkeypatch.setattr(api, "wait", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01"), target("app02")])
    # Read exactly as `cli._destroy` reads it: BaseException has no `outcome`,
    # and core deliberately never imports this backend to learn otherwise.
    carrier: Any = caught.value
    assert carrier.outcome.destroyed == ["app01"]


def test_nothing_to_destroy_is_an_empty_outcome(vsphere_cfg):
    out = destroy.destroy(vsphere_cfg, session(world()), [])
    assert out.destroyed == [] and out.skipped == [] and out.problems == []


def test_the_error_carries_every_problem_not_just_the_first(vsphere_cfg):
    w = world(
        vms=[
            vm("app01", "other", power_state="poweredOff"),
            vm("app02", "other", power_state="poweredOff"),
        ]
    )
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(vsphere_cfg, session(w), [target("app01"), target("app02")])
    fatal = [p for p in caught.value.outcome.problems if p.severity is Severity.ERROR]
    assert len(fatal) == 2


def test_every_view_the_teardown_opened_is_destroyed(vsphere_cfg):
    """vCenter holds a view until it is destroyed or the session ends, and a
    teardown makes one per target on top of the lookup."""
    w = world(vms=[vm("app01", power_state="poweredOff")])
    destroy.destroy(vsphere_cfg, session(w), [target("app01")])
    assert len(w.views) == len(w.destroyed) > 0


def test_the_backend_class_reaches_this_module(vsphere_cfg):
    """Through the class the registry will hold: a `destroy` that stayed a stub
    would delete nothing and exit successfully."""
    w = world(vms=[vm("app01", power_state="poweredOff")])
    out = VsphereBackend().destroy(vsphere_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
