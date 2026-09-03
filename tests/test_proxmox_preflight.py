"""Preflight against a PVE-shaped fake that dispatches on the API path.

The endpoint assertions matter more here than in the libvirt backend: nothing in
this repo has yet run against a real cluster, so `FakeProxmox.calls` recording
the resolved path is what stands in for that until it does.
"""

from __future__ import annotations

import pytest

from orchestrator.backends.proxmox import preflight
from orchestrator.marker import Marker
from orchestrator.problems import Severity
from tests.conftest import messages, session, wheres
from tests.fake_proxmox import FakeProxmox, ResourceException

STORAGES = [
    {"storage": "local", "content": "import,iso,vztmpl"},
    {"storage": "local-lvm", "content": "images,rootdir"},
]


def world(**kw) -> FakeProxmox:
    kw.setdefault("storages", STORAGES)
    kw.setdefault("content", {"local": {"import": [], "iso": []}})
    return FakeProxmox(**kw)


def marked(name: str, deployment: str = "lab-a", **extra) -> dict:
    vm = {"name": name, "description": Marker.for_vm(name, deployment).to_description()}
    vm.update(extra)
    return vm


def errors(d):
    return [p for p in d.problems if p.severity is Severity.ERROR]


def paths(w):
    return {"/".join(parts) for _verb, parts in w.calls}


# -- discovery ---------------------------------------------------------------


def test_an_empty_cluster_discovers_nothing_and_refuses_nothing(pve_cfg):
    w = world()
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms == ()
    assert errors(d) == []


def test_a_marked_vm_is_discovered_with_its_marker(pve_cfg):
    w = world(vms={("pve1", "100"): marked("app01")})
    d = preflight.preflight(pve_cfg, session(w))
    assert len(d.vms) == 1
    assert d.vms[0].name == "app01"
    assert d.vms[0].marker is not None
    assert d.vms[0].marker.name == "app01"


def test_an_unmarked_vm_is_discovered_without_one(pve_cfg):
    w = world(
        vms={("pve1", "100"): {"name": "someone-elses", "description": "prod db"}}
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].marker is None


def test_the_description_is_read_from_the_config_endpoint(pve_cfg):
    """`/cluster/resources` carries the name and the node but not the notes, so
    the marker costs one call per VM. That is the shape of the API."""
    w = world(vms={("pve1", "100"): marked("app01")})
    preflight.preflight(pve_cfg, session(w))
    assert "cluster/resources" in paths(w)
    assert "nodes/pve1/qemu/100/config" in paths(w)


def test_discovery_is_cluster_wide_not_node_scoped(pve_cfg):
    """A VM migrated after vcows created it is still ours. Node-scoped discovery
    would miss it, `decide` would plan a create, and PVE would make a second VM
    with the same name -- it does not require names to be unique."""
    w = world(vms={("pve2", "100"): marked("app01")})
    d = preflight.preflight(pve_cfg, session(w))
    assert len(d.vms) == 1
    assert d.vms[0].id == "pve2/100"


def test_the_id_carries_the_node_because_destroy_gets_nothing_else(pve_cfg):
    w = world(vms={("pve2", "100"): marked("app01")})
    d = preflight.preflight(pve_cfg, session(w))
    node, _, vmid = d.vms[0].id.partition("/")
    assert (node, vmid) == ("pve2", "100")


def test_an_unreadable_vm_is_reported_and_the_run_continues(pve_cfg):
    """A VM the token cannot read is a permissions gap on that one VM. Refusing
    the whole run over it would make deploying impossible on a cluster that has
    unrelated guests -- so the second unreadable VM is reported too, and each
    report is filed under the VM it is about."""
    w = world(vms={("pve1", "100"): marked("app01")})
    w.resources = [
        {"type": "qemu", "node": "pve1", "vmid": 100, "name": "app01"},
        {"type": "qemu", "node": "pve1", "vmid": 101},
    ]
    w.config_error = ResourceException("403 Forbidden")
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms == ()
    assert len(d.problems) == 2
    assert "cannot tell whether it is one of ours" in messages(d.problems)
    # The name when PVE gave one, the vmid when it did not: either way something
    # an operator can go and look at.
    assert set(wheres(d.problems)) == {"app01", "101"}
    assert errors(d) == []


def test_a_vm_pve_lists_without_a_name_is_discovered_with_an_empty_one(pve_cfg):
    """Identity is the marker, so a nameless VM is still discoverable. `decide`
    compares the empty name and refuses nothing over it."""
    w = world(vms={("pve1", "100"): marked("app01")})
    w.resources = [{"type": "qemu", "node": "pve1", "vmid": 100}]
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].name == ""
    assert d.vms[0].marker is not None


def test_an_unparseable_marker_reads_as_unmarked(pve_cfg):
    """Refusing to run because somebody typed into a VM's notes would be worse
    than declining to claim it. Same call the libvirt backend makes."""
    w = world(vms={("pve1", "100"): {"name": "app01", "description": "vcows: {oops"}})
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].marker is None


def test_operator_notes_above_the_marker_do_not_break_it(pve_cfg):
    text = (
        "rebooted 2026-09-01 -- ops\n"
        + Marker.for_vm("app01", "lab-a").to_description()
    )
    w = world(vms={("pve1", "100"): {"name": "app01", "description": text}})
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].marker is not None


# -- the storages ------------------------------------------------------------


def test_a_storage_without_the_import_type_is_refused_with_the_fix(pve_cfg):
    """The one that bites: `import` is not enabled by default on a PVE storage.
    Without this the run gets as far as uploading and fails inside the apply."""
    w = world(storages=[{"storage": "local", "content": "iso,vztmpl"}, STORAGES[1]])
    d = preflight.preflight(pve_cfg, session(w))
    # Naming the type is the whole message: `Problem.__str__` prefixes every
    # problem with its location, and this one's location already says "import".
    assert "does not allow content type(s) import" in messages(d.problems)
    assert "Datacenter -> Storage" in messages(d.problems)
    assert set(wheres(d.problems)) == {"target.proxmox.import_datastore"}
    assert errors(d)


def test_a_second_missing_storage_is_reported_too(pve_cfg):
    """Both storages are checked, so an operator configuring a fresh cluster
    hears about both in one run rather than one per round trip."""
    d = preflight.preflight(pve_cfg, session(world(storages=[])))
    assert len(errors(d)) == 2
    assert set(wheres(d.problems)) == {
        "target.proxmox.import_datastore",
        "target.proxmox.datastore",
    }


def test_a_missing_storage_is_refused(pve_cfg):
    w = world(storages=[STORAGES[0]])
    d = preflight.preflight(pve_cfg, session(w))
    assert "has no storage named 'local-lvm'" in messages(d.problems)
    assert "vcows never creates a storage" in messages(d.problems)


def test_the_disk_datastore_must_hold_images(pve_cfg):
    w = world(storages=[STORAGES[0], {"storage": "local-lvm", "content": "rootdir"}])
    d = preflight.preflight(pve_cfg, session(w))
    assert "images" in messages(d.problems)


# -- the golden image --------------------------------------------------------


def test_an_absent_image_is_planned_for_upload(pve_cfg):
    d = preflight.preflight(pve_cfg, session(world()))
    assert d.artifacts["image"]["create"] is True
    assert d.artifacts["image"]["volid"] == "local:import/golden.qcow2"


def test_an_image_already_there_is_not_re_uploaded(pve_cfg):
    w = world(content={"local": {"import": ["local:import/golden.qcow2"], "iso": []}})
    d = preflight.preflight(pve_cfg, session(w))
    assert d.artifacts["image"]["create"] is False
    assert d.artifacts["image"]["volid"] == "local:import/golden.qcow2"


def test_a_storage_that_cannot_be_listed_is_refused_not_assumed_empty(pve_cfg):
    """Assuming empty means planning an upload that then collides.

    Both listings go through the same storage, so both report: the image one
    fatally, the seed one as a warning that a leftover would not have been seen.
    """
    w = world()
    w.content_error = ResourceException("403 Forbidden")
    d = preflight.preflight(pve_cfg, session(w))
    assert errors(d)
    assert "cannot tell whether the golden image is already there" in messages(
        d.problems
    )
    assert "a leftover seed ISO would not have been noticed" in messages(d.problems)
    assert set(wheres(d.problems)) == {"target.proxmox.import_datastore"}
    # No volid to name and nothing to upload against: `render` would otherwise
    # be handed a create with an empty id.
    assert d.artifacts["image"] == {"create": False, "volid": ""}


# -- orphan seeds ------------------------------------------------------------


def test_a_leftover_seed_for_a_vm_that_does_not_exist_is_refused(pve_cfg):
    """findings.md section 2's orphan-volume refusal, in this backend's terms:
    the residue of a run that uploaded a seed then failed before defining its VM.
    Left alone it collides with this run's upload, mid-apply."""
    w = world(content={"local": {"import": [], "iso": ["local:iso/app01-seed.iso"]}})
    d = preflight.preflight(pve_cfg, session(w))
    assert len(errors(d)) == 1
    assert "residue of an earlier run" in messages(d.problems)
    assert "'app01-seed.iso'" in messages(d.problems)
    # The VM the seed belongs to, by index, because that is what the operator
    # edits or destroys.
    assert set(wheres(d.problems)) == {"vms[0].name"}


def test_a_seed_belonging_to_a_live_vm_is_not_an_orphan(pve_cfg):
    """A seed beside a VM of ours is in use. app01 exists and app02 does not, so
    the same two files in the same storage produce exactly one refusal."""
    w = world(
        vms={("pve1", "100"): marked("app01")},
        content={
            "local": {
                "import": [],
                "iso": ["local:iso/app01-seed.iso", "local:iso/app02-seed.iso"],
            }
        },
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert len(errors(d)) == 1
    assert set(wheres(d.problems)) == {"vms[1].name"}


# -- media -------------------------------------------------------------------


@pytest.mark.parametrize("bus", ["ide2", "sata0", "scsi1", "virtio3"])
def test_the_seed_iso_is_recorded_for_teardown(pve_cfg, bus):
    """Whichever bus it was attached to. PVE puts a CD-ROM on any of the four,
    and the module's choice today is not the only one a cluster can report."""
    w = world(
        vms={
            ("pve1", "100"): marked(
                "app01", **{bus: "local:iso/app01-seed.iso,media=cdrom"}
            )
        }
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].disks == ("local:iso/app01-seed.iso",)


def test_a_disk_listed_before_the_cdrom_does_not_end_the_walk(pve_cfg):
    """Every drive is read, not just the ones up to the first hard disk."""
    w = world(
        vms={
            ("pve1", "100"): marked(
                "app01",
                scsi0="local-lvm:vm-100-disk-0,size=40G",
                ide2="local:iso/app01-seed.iso,media=cdrom",
            )
        }
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].disks == ("local:iso/app01-seed.iso",)


def test_notes_that_read_like_a_cdrom_line_are_not_collected(pve_cfg):
    """Only a drive key names a drive. `description` is free text an operator
    types into, and a teardown must not delete a file because somebody pasted
    the line describing it into the VM's notes."""
    w = world(
        vms={
            ("pve1", "100"): {
                "name": "app01",
                "description": "local:iso/rocky10-dvd.iso,media=cdrom",
            }
        }
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].disks == ()


def test_media_on_another_storage_is_not_collected(pve_cfg):
    """So a teardown can never reach an installer ISO somebody parked elsewhere."""
    w = world(
        vms={
            ("pve1", "100"): marked(
                "app01", ide2="bigstore:iso/rocky10.iso,media=cdrom"
            )
        }
    )
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].disks == ()


def test_a_hard_disk_is_not_collected_as_media(pve_cfg):
    """`delete_vm` with purge already removes the VM's own disks."""
    w = world(vms={("pve1", "100"): marked("app01", scsi0="local-lvm:vm-100-disk-0")})
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms[0].disks == ()


def test_a_vm_with_no_vmid_is_reported_rather_than_dropped(pve_cfg):
    """Silently dropping a VM is how `decide` ends up planning a create over
    something live. A second such row is reported too, and one with no name to
    report under is filed as unnamed rather than under nothing."""
    w = world()
    w.resources = [
        {"type": "qemu", "node": "pve1", "name": "odd"},
        {"type": "qemu", "node": "pve1"},
    ]
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms == ()
    assert len(d.problems) == 2
    assert "cannot identify it" in messages(d.problems)
    assert set(wheres(d.problems)) == {"odd", "<unnamed>"}


def test_non_qemu_resources_are_ignored(pve_cfg):
    """`/cluster/resources?type=vm` returns LXC containers too."""
    w = world()
    w.resources = [{"type": "lxc", "node": "pve1", "vmid": 200, "name": "a-container"}]
    d = preflight.preflight(pve_cfg, session(w))
    assert d.vms == ()


@pytest.mark.parametrize("attr", ["resources_error", "storage_error"])
def test_a_failure_reaching_the_cluster_is_not_swallowed(pve_cfg, attr):
    """Unlike a single unreadable VM, a cluster that will not answer at all is
    not a fact to report -- preflight has nothing to say about the target."""
    w = world()
    setattr(w, attr, ResourceException("boom"))
    with pytest.raises(ResourceException, match="boom"):
        preflight.preflight(pve_cfg, session(w))
