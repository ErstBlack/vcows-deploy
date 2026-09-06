"""Preflight against a vCenter-shaped fake built out of the SDK's own types.

`tests/fake_vsphere.py` answers with real `ObjectContent`, real devices and real
managed-object types, so a test here fails if the code reads a property vCenter
does not carry or filters on a type it does not have. That is the half a
dict-shaped fake cannot check, and most runs never reach a real vCenter.

Two of these tests are about `orchestrator/backends/base.py`'s two traps rather
than about vSphere: `Existing.name` must be vCenter's own name or `decide()`'s
name-clash refusal compares two different things, and `Existing.disks` must
never carry a disk backing or a teardown takes the shared template with it. Both
are named in full below.
"""

from __future__ import annotations

import pytest
from pyVmomi import vim

from orchestrator.backends.vsphere import api, preflight
from orchestrator.marker import Marker
from orchestrator.problems import Severity
from tests.conftest import messages, wheres
from tests.fake_vsphere import (
    COOKIE,
    FakeBrowser,
    FakeContent,
    FakeServiceInstance,
    FakeVm,
    cdrom,
    disk,
    mo,
)

pytestmark = pytest.mark.usefixtures("_no_vsphere_polling_delay")

#: Every name `VSPHERE_CONFIG` carries under `target.vsphere`, and the SDK type
#: each one resolves as. The config is the constant here and the vCenter is what
#: varies, which is why the failures below are set up by dropping an object
#: rather than by editing the config.
NAMED = (
    ("datacenter", vim.Datacenter, "dc-a"),
    ("datastore", vim.Datastore, "ds-a"),
    ("cluster", vim.ClusterComputeResource, "cluster-a"),
    ("network", vim.Network, "pg-vcows"),
)

#: The seed ISO of a VM `VSPHERE_CONFIG` names, where `create` would upload it.
APP01_SEED = "[ds-a] vcows/app01/app01-seed.iso"
APP02_SEED = "[ds-a] vcows/app02/app02-seed.iso"


def world(vms=(), files=(), error=None, missing=(), extra=lambda dc: ()) -> FakeContent:
    """The vCenter `VSPHERE_CONFIG` names, holding every object it names.

    `missing` drops one by field name, `files` is what the datastore holds,
    `error` is the fault its browser answers a search with, and `extra` is
    whatever else a test needs, built around the datacenter this made -- which
    is the container everything but the datacenter is looked for inside.
    """
    datacenter = mo(vim.Datacenter, "datacenter-1", name="dc-a")
    objects = [datacenter] if "datacenter" not in missing else []
    for field, kind, name in NAMED[1:]:
        if field in missing:
            continue
        browser = {"browser": FakeBrowser(files=files, error=error)}
        objects.append(
            mo(
                kind,
                f"{field}-1",
                name=name,
                container=datacenter,
                **(browser if field == "datastore" else {}),
            )
        )
    return FakeContent(objects=[*objects, *extra(datacenter)], vms=vms)


def session(content: FakeContent) -> api.Session:
    """A `Session` onto a fake vCenter, as `connect` would have yielded it."""
    si = FakeServiceInstance(content)
    return api.Session(si=si, content=content, cookie=COOKIE)


def annotation(name: str, deployment: str = "lab-a") -> str:
    return Marker.for_vm(name, deployment).to_description()


def marked(name: str, deployment: str = "lab-a", **kw) -> FakeVm:
    return FakeVm(name, annotation=annotation(name, deployment), **kw)


def errors(d):
    return [p for p in d.problems if p.severity is Severity.ERROR]


def browser(content: FakeContent) -> FakeBrowser:
    return next(o for o in content.objects if isinstance(o, vim.Datastore)).browser


# -- discovery ---------------------------------------------------------------


def test_an_empty_vcenter_discovers_nothing_and_refuses_nothing(vsphere_cfg):
    d = preflight.preflight(vsphere_cfg, session(world()))
    assert d.vms == ()
    assert errors(d) == []


def test_a_marked_vm_is_discovered_with_its_marker(vsphere_cfg):
    d = preflight.preflight(vsphere_cfg, session(world(vms=[marked("app01")])))
    assert len(d.vms) == 1
    assert d.vms[0].marker is not None
    assert d.vms[0].marker.name == "app01"


def test_an_unmarked_vm_is_discovered_without_one(vsphere_cfg):
    w = world(vms=[FakeVm("someone-elses", annotation="prod db")])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].marker is None


def test_an_unparseable_marker_reads_as_unmarked(vsphere_cfg):
    """Refusing to run because somebody typed into a VM's notes would be worse
    than declining to claim it. Same call the other two backends make."""
    w = world(vms=[FakeVm("app01", annotation="vcows: {oops")])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].marker is None


def test_operator_notes_above_the_marker_do_not_break_it(vsphere_cfg):
    w = world(
        vms=[FakeVm("app01", annotation=f"rebooted -- ops\n{annotation('app01')}")]
    )
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].marker is not None


def test_the_id_is_the_vcenter_uuid_because_destroy_gets_nothing_else(vsphere_cfg):
    """`destroy` is handed `Existing` and nothing else, and re-verifies by uuid.
    A moid would be vCenter's own handle rather than the VM's identity."""
    w = world(vms=[marked("app01", uuid="4213-not-a-uuid")])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].id == "4213-not-a-uuid"


def test_the_name_is_the_one_vcenter_reports_not_the_logical_one(vsphere_cfg):
    """**`base.Existing`'s first trap.** `decide()` compares this against the
    config's logical name to refuse a clash, so a backend that returned the
    marker's name -- or a folder path, or a prefix -- would be comparing two
    different things and the refusal would never fire."""
    w = world(vms=[FakeVm("esx-app01", annotation=annotation("app01"))])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].name == "esx-app01"
    assert d.vms[0].marker is not None
    assert d.vms[0].marker.name == "app01"


def test_a_template_is_never_a_target(vsphere_cfg):
    """The golden image carries our marker too. Reported through `artifacts`,
    never as a VM: `decide()` would otherwise see a marked VM it did not want
    and `destroy` would take it, and every other deployment's linked clones are
    overlays on its disk."""
    w = world(vms=[marked("golden.qcow2", template=True), marked("app01")])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert [e.name for e in d.vms] == ["app01"]
    assert d.artifacts["image"]["create"] is False


def test_a_vm_with_no_uuid_is_reported_rather_than_dropped(vsphere_cfg):
    """Silently dropping a VM is how `decide` ends up planning a create over
    something live. One with no name to report under is filed as unnamed."""
    nameless = marked("app01")
    del nameless.props["summary.config.uuid"]
    del nameless.props["name"]
    w = world(vms=[nameless])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms == ()
    assert wheres(d.problems) == ["<unnamed>"]
    assert "cannot identify it" in messages(d.problems)
    assert errors(d) == []


def test_a_vm_vcenter_lists_without_a_name_is_discovered_with_an_empty_one(
    vsphere_cfg,
):
    """Identity is the marker, so a nameless VM is still discoverable. `decide`
    compares the empty name and refuses nothing over it."""
    nameless = marked("app01")
    del nameless.props["name"]
    d = preflight.preflight(vsphere_cfg, session(world(vms=[nameless])))
    assert d.vms[0].name == ""
    assert d.vms[0].marker is not None


def test_a_vm_with_no_config_at_all_is_still_discovered(vsphere_cfg):
    """A VM being created right now answers with a name and no `config`. Every
    read here goes through `get` for that reason."""
    half = FakeVm("app01")
    for absent in ("config.template", "config.annotation", "config.hardware.device"):
        del half.props[absent]
    d = preflight.preflight(vsphere_cfg, session(world(vms=[half])))
    assert (d.vms[0].name, d.vms[0].marker, d.vms[0].disks) == ("app01", None, ())


def test_every_property_is_asked_for_in_one_call(vsphere_cfg):
    """`vm.config.annotation` on a managed object is a round trip apiece, so a
    hundred-VM vCenter is six hundred of them the moment this is done by
    attribute access."""
    w = world(vms=[marked("app01"), marked("app02")])
    preflight.preflight(vsphere_cfg, session(w))
    retrieved = [c for c in w.calls if c[0] == "RetrieveContents"]
    assert retrieved == [("RetrieveContents", "vim.VirtualMachine", api.VM_PROPERTIES)]


def test_every_view_is_destroyed(vsphere_cfg):
    """vCenter holds a container view until it is destroyed or the session ends,
    and preflight makes one per configured name plus one for the VMs."""
    w = world(vms=[marked("app01")])
    preflight.preflight(vsphere_cfg, session(w))
    assert w.views and w.destroyed == w.views


# -- the media a teardown collects -------------------------------------------


def test_the_seed_iso_is_recorded_for_teardown(vsphere_cfg):
    w = world(vms=[marked("app01", devices=(cdrom(APP01_SEED),))])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].disks == (APP01_SEED,)


def test_a_disk_is_never_collected_however_it_is_backed(vsphere_cfg):
    """**`base.Existing`'s second trap.** Every VM here is a linked clone whose
    disk is an overlay on the template's, and the backing chain names that
    template disk -- which every other deployment's clones are overlays on too.
    Collecting it would destroy the shared image on the first teardown."""
    w = world(
        vms=[
            marked(
                "app01",
                devices=(
                    disk("[ds-a] app01/app01.vmdk", parent="[ds-a] golden/golden.vmdk"),
                    cdrom(APP01_SEED),
                ),
            )
        ]
    )
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].disks == (APP01_SEED,)


def test_a_cdrom_that_is_not_backed_by_an_iso_is_not_collected(vsphere_cfg):
    """A drive pointed at the client's own device has no file to delete, and its
    backing carries no `fileName` at all."""
    client = vim.vm.device.VirtualCdrom(
        backing=vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo()
    )
    w = world(vms=[marked("app01", devices=(client,))])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].disks == ()


def test_media_on_another_datastore_is_not_collected(vsphere_cfg):
    """So a teardown can never reach an installer ISO somebody parked elsewhere."""
    w = world(vms=[marked("app01", devices=(cdrom("[iso-store] rocky10.iso"),))])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.vms[0].disks == ()


# -- the target --------------------------------------------------------------


def test_a_missing_datacenter_stops_the_walk_after_one_problem(vsphere_cfg):
    """Every other name is resolved inside it, so five further misses would all
    mean this one."""
    d = preflight.preflight(vsphere_cfg, session(world(missing=("datacenter",))))
    assert wheres(errors(d)) == ["target.vsphere.datacenter"]
    assert "vcows never creates one" in messages(d.problems)


def test_a_missing_datastore_is_refused(vsphere_cfg):
    d = preflight.preflight(vsphere_cfg, session(world(missing=("datastore",))))
    assert wheres(errors(d)) == ["target.vsphere.datastore"]
    assert "has no datastore named 'ds-a'" in messages(d.problems)


def test_every_name_is_looked_at_rather_than_the_first_miss_ending_the_walk(
    vsphere_cfg,
):
    """An operator at an air-gapped site should not round-trip once per fault."""
    w = world(missing=("cluster", "network"))
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["target.vsphere.cluster", "target.vsphere.network"]


def test_a_host_resolves_where_a_cluster_would_have(vsphere_cfg):
    """`schema._check_placement` allows exactly one of the two, so the other is
    absent here rather than missing, and an absent one is not a problem."""
    del vsphere_cfg["target"]["vsphere"]["cluster"]
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    w = world(
        missing=("cluster",),
        extra=lambda dc: [
            mo(vim.HostSystem, "host-1", name="esx1.example.com", container=dc)
        ],
    )
    assert preflight.preflight(vsphere_cfg, session(w)).problems == ()

    vsphere_cfg["target"]["vsphere"]["host"] = "esx2.example.com"
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["target.vsphere.host"]


def test_an_optional_placement_is_only_checked_when_it_is_named(vsphere_cfg):
    """Without `folder` and `resource_pool` the clone lands in the datacenter's
    VM folder and the root pool, which is not a fault to report."""
    d = preflight.preflight(vsphere_cfg, session(world()))
    assert d.problems == ()

    vsphere_cfg["target"]["vsphere"]["folder"] = "vcows"
    vsphere_cfg["target"]["vsphere"]["resource_pool"] = "vcows-pool"
    d = preflight.preflight(vsphere_cfg, session(world()))
    assert wheres(errors(d)) == [
        "target.vsphere.folder",
        "target.vsphere.resource_pool",
    ]


def test_an_object_in_another_datacenter_does_not_resolve(vsphere_cfg):
    """Two datacenters on one vCenter may each hold a datastore of the same
    name, which is why everything below the datacenter is looked for inside it
    rather than from the root folder."""

    def in_another_datacenter(_dc):
        other = mo(vim.Datacenter, "datacenter-2", name="dc-b")
        return [other, mo(vim.Datastore, "ds-2", name="ds-a", container=other)]

    w = world(missing=("datastore",), extra=in_another_datacenter)
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["target.vsphere.datastore"]


# -- the golden image --------------------------------------------------------


def test_an_absent_image_is_planned_for_creation(vsphere_cfg):
    d = preflight.preflight(vsphere_cfg, session(world()))
    assert d.artifacts["image"] == {"create": True, "template": "golden.qcow2"}


def test_a_marked_template_is_not_made_again(vsphere_cfg):
    """The bytes moved on the run that made it, and a linked clone moves none."""
    w = world(vms=[marked("golden.qcow2", template=True)])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.artifacts["image"] == {"create": False, "template": "golden.qcow2"}
    assert d.problems == ()


def test_a_template_of_ours_from_another_deployment_is_still_ours(vsphere_cfg):
    """One template serves every deployment on the vCenter -- that is the point
    of cloning from it -- so the marker's deployment is not a claim to check."""
    w = world(vms=[marked("golden.qcow2", deployment="lab-b", template=True)])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.artifacts["image"]["create"] is False
    assert d.problems == ()


def test_a_vm_holding_the_image_name_is_refused_rather_than_cloned(vsphere_cfg):
    w = world(vms=[marked("golden.qcow2")])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["image.base_volume_name"]
    assert "rather than a template" in messages(d.problems)
    # Present is present: there is nothing to create over it, and the fatal
    # problem is what stops the deploy.
    assert d.artifacts["image"]["create"] is False


def test_an_unmarked_template_is_not_adopted(vsphere_cfg):
    """The never-adopt rule, in this backend's terms: a template somebody else
    built and named the same thing is not ours to clone from or replace."""
    w = world(vms=[FakeVm("golden.qcow2", template=True)])
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["image.base_volume_name"]
    assert "carries no vcows marker" in messages(d.problems)
    assert "will not adopt or overwrite it" in messages(d.problems)


# -- orphan seeds ------------------------------------------------------------


def test_a_leftover_seed_for_a_vm_that_does_not_exist_is_refused(vsphere_cfg):
    """findings.md section 2's orphan-volume refusal, in this backend's terms:
    the residue of a run that uploaded a seed then failed before cloning its VM.
    Left alone it collides with this run's upload, mid-apply."""
    d = preflight.preflight(vsphere_cfg, session(world(files=(APP01_SEED,))))
    assert len(errors(d)) == 1
    assert "residue of an earlier run" in messages(d.problems)
    assert APP01_SEED in messages(d.problems), "the path an operator has to delete"
    # The VM the seed belongs to, by index, because that is what the operator
    # edits or destroys.
    assert wheres(errors(d)) == ["vms[0].name"]


def test_a_seed_belonging_to_a_live_vm_is_not_an_orphan(vsphere_cfg):
    """app01 exists and app02 does not, so the same two files under the same
    folder produce exactly one refusal."""
    w = world(vms=[marked("app01")], files=(APP01_SEED, APP02_SEED))
    d = preflight.preflight(vsphere_cfg, session(w))
    assert wheres(errors(d)) == ["vms[1].name"]


def test_the_search_names_the_vcows_folder_and_the_seed_pattern(vsphere_cfg):
    """One search of the folder every seed goes under, rather than one call per
    configured VM."""
    w = world(files=(APP01_SEED,))
    preflight.preflight(vsphere_cfg, session(w))
    assert browser(w).searches == [("[ds-a] vcows", ("*-seed.iso",))]


def test_a_datastore_with_no_vcows_folder_yet_is_not_a_problem(vsphere_cfg):
    """The first deploy against a datastore runs before anything has created it,
    so the fault that says so is the ordinary answer."""
    w = world(error=vim.fault.FileNotFound(msg="[ds-a] vcows was not found"))
    d = preflight.preflight(vsphere_cfg, session(w))
    assert d.problems == ()


def test_a_search_that_fails_otherwise_warns_rather_than_refusing(vsphere_cfg):
    """Nothing can be concluded from a search that did not happen, and refusing
    on one would stop a deploy over a permission on a folder."""
    w = world(error=vim.fault.NoPermission(msg="Permission to perform this operation"))
    d = preflight.preflight(vsphere_cfg, session(w))
    assert [p.severity for p in d.problems] == [Severity.WARNING]
    assert wheres(d.problems) == ["target.vsphere.datastore"]
    assert "would not have been noticed" in messages(d.problems)


def test_an_unresolvable_datastore_is_not_reported_twice(vsphere_cfg):
    """`_check_target` already said the datastore is not there; a second problem
    saying the search did not happen names the same fault again."""
    d = preflight.preflight(vsphere_cfg, session(world(missing=("datastore",))))
    assert wheres(d.problems) == ["target.vsphere.datastore"]


# -- failures that are not facts about the target ----------------------------


@pytest.mark.parametrize("attr", ["view_error", "retrieve_error"])
def test_a_failure_reaching_the_vcenter_is_not_swallowed(vsphere_cfg, attr):
    """Unlike a name that does not resolve, a vCenter that will not answer at all
    is not a fact to report -- preflight has nothing to say about the target."""
    w = world(vms=[marked("app01")])
    setattr(w, attr, RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        preflight.preflight(vsphere_cfg, session(w))
