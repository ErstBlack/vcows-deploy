"""The ownership policy. This is the dangerous logic, so it gets the most tests."""

from __future__ import annotations

from orchestrator.backends.base import Action, Existing, Severity, decide
from orchestrator.marker import Marker


def ours(name, deployment="lab-a", hv_name=None):
    m = Marker.for_vm(name, deployment)
    return Existing(name=hv_name or name, id=m.id, marker=m)


def unmarked(name):
    return Existing(name=name, id=f"uuid-of-{name}", marker=None)


def test_absent_creates():
    decisions, problems = decide(["app01"], [], "lab-a")
    assert [d.action for d in decisions] == [Action.CREATE]
    assert problems == []


def test_ours_skips_without_comparing():
    """'exists (not compared)' is the point: libvirt rewrites domain XML on
    define, so any naive diff produces permanent false drift."""
    decisions, _ = decide(["app01"], [ours("app01")], "lab-a")
    assert decisions[0].action is Action.SKIP
    assert "not compared" in decisions[0].reason


def test_unmarked_name_collision_refuses():
    """A VM we did not create must never be adopted or overwritten."""
    decisions, _ = decide(["app01"], [unmarked("app01")], "lab-a")
    assert decisions[0].action is Action.REFUSE
    assert "will not adopt or overwrite" in decisions[0].reason


def test_other_deployment_refuses():
    decisions, _ = decide(["app01"], [ours("app01", deployment="lab-b")], "lab-a")
    assert decisions[0].action is Action.REFUSE
    assert "lab-b" in decisions[0].reason


def test_identity_is_the_marker_not_the_name():
    """A renamed VM is still ours. This is the whole reason the marker exists,
    and renaming a VM is a plausible accident where editing a marker is not."""
    renamed = ours("app01", hv_name="somebody-renamed-me")
    decisions, problems = decide(["app01"], [renamed], "lab-a")
    assert decisions[0].action is Action.SKIP
    assert "somebody-renamed-me" in decisions[0].reason
    assert problems == []


def test_marked_vm_absent_from_config_is_reported_never_touched():
    """Removing a VM from the config does not delete it. The config is not
    declarative in the way people expect, so this must be visible."""
    decisions, problems = decide(["app01"], [ours("app01"), ours("app03")], "lab-a")
    assert [d.action for d in decisions] == [Action.SKIP]
    assert len(problems) == 1
    assert problems[0].severity is Severity.WARNING
    assert "app03" in problems[0].message
    assert not problems[0].fatal


def test_unmarked_vm_absent_from_config_is_not_even_mentioned():
    """Unmarked VMs we do not want are simply none of our business."""
    _, problems = decide(["app01"], [unmarked("someone-elses-vm")], "lab-a")
    assert problems == []


def test_mixed_world_decides_each_independently():
    world = [ours("app01"), unmarked("app02"), ours("app04", deployment="lab-b")]
    decisions, problems = decide(["app01", "app02", "app03", "app04"], world, "lab-a")
    assert {d.vm_name: d.action for d in decisions} == {
        "app01": Action.SKIP,  # ours
        "app02": Action.REFUSE,  # unmarked collision
        "app03": Action.CREATE,  # absent
        "app04": Action.REFUSE,  # other deployment
    }
    assert problems == []  # every marked VM was wanted
