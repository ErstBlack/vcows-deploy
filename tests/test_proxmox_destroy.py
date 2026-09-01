"""Teardown: ordering, accounting, and the refusals.

The questions here are the ones the libvirt destroy tests ask, because they are
the ones that matter regardless of hypervisor: is the VM stopped before it is
deleted, is the marker re-read immediately before anything is removed, and does a
partial teardown still report everything it did.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.backends.base import Existing
from orchestrator.backends.proxmox import api, destroy
from orchestrator.marker import Marker
from orchestrator.problems import Severity
from tests.fake_proxmox import FakeProxmox, ResourceException, upid


@pytest.fixture(autouse=True)
def _no_polling_delay(monkeypatch):
    """proxmoxer's task poller sleeps once per wait. Fine against a cluster,
    pure latency here."""
    monkeypatch.setattr(api, "POLL_INTERVAL", 0)


def marker(name: str, deployment: str = "lab-a") -> Marker:
    return Marker.for_vm(name, deployment)


def vm(name: str, deployment: str = "lab-a", **extra) -> dict:
    out = {"name": name, "description": marker(name, deployment).to_description()}
    out.update(extra)
    return out


def target(name: str, node: str = "pve1", vmid: str = "100", disks=()) -> Existing:
    return Existing(name=name, id=f"{node}/{vmid}", marker=marker(name), disks=disks)


def session(w: FakeProxmox) -> api.Session:
    return api.Session(
        prox=w, node="pve1", datastore="local-lvm", import_datastore="local"
    )


def verbs(w):
    return [(v, "/".join(p)) for v, p in w.calls]


def test_a_stopped_vm_is_deleted_and_accounted_for(pve_cfg):
    w = FakeProxmox(vms={("pve1", "100"): vm("app01")})
    out = destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
    assert out.problems == []
    assert w.vms == {}


def test_a_running_vm_is_stopped_before_it_is_deleted(pve_cfg):
    """PVE refuses to delete a running VM, and the fake models that -- so this
    fails loudly if the order is ever reversed, rather than silently."""
    w = FakeProxmox(vms={("pve1", "100"): vm("app01", status="running")})
    out = destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert out.destroyed == ["app01"]
    order = [path for verb, path in verbs(w) if verb in ("post", "delete")]
    assert order.index("nodes/pve1/qemu/100/status/stop") < order.index(
        "nodes/pve1/qemu/100"
    )


def test_a_stopped_vm_is_not_asked_to_stop_again(pve_cfg):
    w = FakeProxmox(vms={("pve1", "100"): vm("app01")})
    destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert "nodes/pve1/qemu/100/status/stop" not in [p for _v, p in verbs(w)]


def test_the_delete_purges_so_no_backup_job_outlives_the_vm(pve_cfg):
    w = FakeProxmox(vms={("pve1", "100"): vm("app01")})
    destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert w.vms == {}


def test_the_marker_is_re_read_immediately_before_deleting(pve_cfg):
    """Preflight ran earlier and an operator may have edited the VM since. This
    is the last point at which refusing costs nothing."""
    w = FakeProxmox(vms={("pve1", "100"): vm("app01")})
    destroy.destroy(pve_cfg, session(w), [target("app01")])
    read = [p for v, p in verbs(w) if v == "get"]
    assert read[0] == "nodes/pve1/qemu/100/config"


def test_a_changed_marker_refuses_that_vm_and_keeps_going(pve_cfg):
    w = FakeProxmox(
        vms={
            ("pve1", "100"): vm("app01", deployment="somebody-else"),
            ("pve1", "101"): vm("app02"),
        }
    )
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(
            pve_cfg, session(w), [target("app01"), target("app02", vmid="101")]
        )
    out = caught.value.outcome
    assert out.skipped == ["app01"]
    assert out.destroyed == ["app02"]
    assert ("pve1", "100") in w.vms
    assert "marker on VM 100 changed" in str(caught.value)


def test_a_vm_that_vanished_is_skipped_not_failed(pve_cfg):
    """A VM that disappeared between preflight and teardown. Its seed ISO is
    still worth collecting -- the same branch libvirt's destroy has."""
    w = FakeProxmox(vms={}, content={"local": {"iso": ["local:iso/app01-seed.iso"]}})
    out = destroy.destroy(
        pve_cfg,
        session(w),
        [target("app01", disks=("local:iso/app01-seed.iso",))],
    )
    assert out.skipped == ["app01"]
    assert "local:iso/app01-seed.iso" in out.destroyed
    assert out.problems == []


def test_the_seed_iso_is_deleted_with_the_vm(pve_cfg):
    w = FakeProxmox(
        vms={("pve1", "100"): vm("app01")},
        content={"local": {"iso": ["local:iso/app01-seed.iso"]}},
    )
    out = destroy.destroy(
        pve_cfg, session(w), [target("app01", disks=("local:iso/app01-seed.iso",))]
    )
    assert out.destroyed == ["app01", "local:iso/app01-seed.iso"]
    assert w.content["local"]["iso"] == []


def test_media_that_is_not_this_vms_seed_is_left_alone(pve_cfg):
    """Guarded on the basename `cloudinit.seed_name` derives for the marker's
    logical name, so an installer ISO attached by hand is not a candidate."""
    w = FakeProxmox(
        vms={("pve1", "100"): vm("app01")},
        content={"local": {"iso": ["local:iso/rocky10-dvd.iso"]}},
    )
    out = destroy.destroy(
        pve_cfg, session(w), [target("app01", disks=("local:iso/rocky10-dvd.iso",))]
    )
    assert out.destroyed == ["app01"]
    assert w.content["local"]["iso"] == ["local:iso/rocky10-dvd.iso"]


def test_a_seed_that_will_not_delete_is_a_skip_not_a_stop(pve_cfg):
    """The VM is already gone and the other targets are still worth attempting.
    It still makes the exit code non-zero, because something vcows was asked to
    remove is still there."""
    w = FakeProxmox(
        vms={("pve1", "100"): vm("app01")},
        content={"local": {"iso": ["local:iso/app01-seed.iso"]}},
    )
    w.volume_delete_error = ResourceException("storage is read-only")
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(
            pve_cfg, session(w), [target("app01", disks=("local:iso/app01-seed.iso",))]
        )
    out = caught.value.outcome
    assert out.destroyed == ["app01"]
    assert out.skipped == ["local:iso/app01-seed.iso"]


def test_a_failed_task_is_a_failure_not_a_success(pve_cfg):
    """`Tasks.blocking_status` returns when a task stops, and a failed delete
    stops too. Telling them apart is `exitstatus`, and taking the first as
    success is exactly the silent partial teardown Outcome exists to prevent."""
    w = FakeProxmox(vms={("pve1", "100"): vm("app01")})
    w.task_fails = {upid("pve1", "qmdestroy", "100")}
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert "task failed somehow" in str(caught.value)
    assert caught.value.outcome.destroyed == []


def test_a_task_that_never_finishes_times_out_rather_than_hanging(pve_cfg, monkeypatch):
    monkeypatch.setattr(api, "TASK_TIMEOUT", 0)
    w = FakeProxmox(vms={("pve1", "100"): vm("app01", status="running")})
    w.task_never_finishes = True
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(pve_cfg, session(w), [target("app01")])
    assert "had not finished" in str(caught.value)


def test_an_interrupt_still_carries_what_was_already_destroyed(pve_cfg, monkeypatch):
    """A Ctrl-C mid-teardown. `cli._destroy` reads this back with getattr rather
    than importing this module, so the attribute has to survive the raise."""
    w = FakeProxmox(vms={("pve1", "100"): vm("app01"), ("pve1", "101"): vm("app02")})
    calls = {"n": 0}
    real = api.delete_vm

    def interrupt(session_, node, vmid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(session_, node, vmid)

    monkeypatch.setattr(api, "delete_vm", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        destroy.destroy(
            pve_cfg, session(w), [target("app01"), target("app02", vmid="101")]
        )
    # Read exactly as `cli._destroy` reads it: BaseException has no `outcome`,
    # and core deliberately never imports this backend to learn otherwise.
    carrier: Any = caught.value
    assert carrier.outcome.destroyed == ["app01"]


def test_nothing_to_destroy_is_an_empty_outcome(pve_cfg):
    out = destroy.destroy(pve_cfg, session(FakeProxmox()), [])
    assert out.destroyed == [] and out.skipped == [] and out.problems == []


def test_the_error_carries_every_problem_not_just_the_first(pve_cfg):
    w = FakeProxmox(
        vms={
            ("pve1", "100"): vm("app01", deployment="other"),
            ("pve1", "101"): vm("app02", deployment="other"),
        }
    )
    with pytest.raises(destroy.DestroyError) as caught:
        destroy.destroy(
            pve_cfg, session(w), [target("app01"), target("app02", vmid="101")]
        )
    fatal = [p for p in caught.value.outcome.problems if p.severity is Severity.ERROR]
    assert len(fatal) == 2
