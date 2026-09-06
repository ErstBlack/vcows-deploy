"""What the simulator holds after a deploy, and after the teardown.

The other half of `scripts/smoke-vsphere.sh`, and the half that is assertions
rather than setup. That script starts vcsim, writes the config and runs the
shipped `vcows deploy` and `vcows destroy` against it; this file says what the
result has to look like. It is a gate rather than a test file for the reason
`tests/test_libvirt_smoke.py` is: it needs something no bare `pytest` run has,
and a gate that quietly passes because it did not run is worse than no gate.

Every read below is a **leaf path through the PropertyCollector**, never
attribute access on a managed object. `docs/research/vsphere-vcsim-2026-09.md`
section 3 measured why: `vm.runtime`, `vm.summary` and any other whole-property
fetch raise `AttributeError` under pyVmomi 9 against vcsim, because vcsim emits
an empty `faultToleranceState` the deserialiser will not take. The backend reads
this way for that reason, and a gate that read any other way would fail as the
backend's defect rather than as its own.

## What this gate cannot assert

Section 4 of that document, row by row. Each ran green against vcsim and proves
nothing about vCenter, so nothing here asserts it; each names the first-contact
item -- the epic's "Not until first contact" list, settled in #318 -- that owns
the question instead.

**N1, that a linked clone is a delta disk.** vcsim reports
`FlatVer2BackingInfo` with `parent` None for a linked spec and a full one alike,
and discards `diskMoveType` and `snapshot` (A8). First contact: whether a delta
disk is what vCenter makes of the same spec.

**N2, that a linked clone's disk cannot be grown.** Follows from N1, and it is
what `schema._check_linked_clone_disk` rests on. First contact: "a linked-clone
delta disk cannot be grown".

**N3, that vCenter accepts qemu-img's streamOptimized VMDK.** The lease POST
parses no header. First contact: "vCenter accepts qemu-img's streamOptimized
VMDK header".

**N4, that the lease device URL is reachable through vCenter.** vcsim answers
with its own listen address and never `*`, so the substitution `create` makes is
a no-op here and the question never arises. First contact: A4.

**N5, that a `52:54:00` manual MAC is accepted.** vcsim stores it, and
`ethernet0.checkMACAddress=FALSE` beside it, with no validation behind either.
First contact: A6 on vCenter 7. The MAC assertion below says the derived value
reached the adapter, never that a hypervisor would keep it.

**N6, anything about hardware version or secure boot.** vcsim echoes `firmware`
and `efiSecureBootEnabled` back and has no vmx-version ceiling and no VBS. First
contact: "a clone inherits the template's hardware version".

**N7, that a template's snapshot must be its only one.** vcsim imposes no such
rule, so a second snapshot would violate nothing. First contact: A5.

**N8, that the lease ends in state `done`.** `HttpNfcLeaseComplete` deletes the
lease object, so asserting a terminal state raises `ManagedObjectNotFound`. No
epic row: only a vCenter that keeps the object can be asked, which makes it
#318's to record.

**N9, anything version-gated on vCenter 7 or 8.** vcsim negotiates
`urn:vim25/6.5`. First contact: the whole of #318.

**N10, that `CopyVirtualDisk_Task` with a format-changing `destSpec` fails.**
Not exercised at all: A2 removed the call from the design, and a simulator that
accepts everything could not have shown the `NotImplemented` anyway.

**N11, that a clone carries the marker its `CloneSpec` gave it.** Not in section
4, because the spike measured the annotation round trip on the template alone.
Measured here on vcsim 0.56.0: `CloneVM_Task` accepts a `CloneSpec` whose
`config` carries an annotation, applies `numCPUs` and `memoryMB` off that same
`ConfigSpec`, and drops the annotation -- absent afterwards whether or not the
source carried one, and `ReconfigVM_Task` then sets it. That reconfigure is what
`scripts/smoke-vsphere.sh` does between the two phases, so the teardown has a
subject at all. First contact: whether vCenter applies it, which is what
`create.clone_vm` is written for and what `tests/test_vsphere_create.py` pins the
spec of.

What is left is what the spike fixed as this gate's scope: the ordering of the
sequence, that every call is accepted with the argument shapes the product
builds, that the marker round-trips through `config.annotation`, that
`MarkAsTemplate` sets `config.template`, that the `/folder` PUT and GET move
bytes intact, that `Destroy_Task` removes the VM, and that
`DeleteDatastoreFile_Task` removes the seed ISO.

Run it through `just smoke-vsphere`. Invoking pytest here directly does nothing:
the constants below come from the script, which is also what starts the
simulator they name.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
import requests
from pyVmomi import vim

from orchestrator.backends.vsphere import api, create
from tests.conftest import gate


def _fact(name: str) -> str:
    """One constant from `scripts/smoke-vsphere.sh`, or "" when it did not run.

    The script stays the single source of truth for all of them: it writes the
    config these describe and it drives the two verbs, so a second copy here
    would be one fixture maintained in two languages -- and the `""` default is
    what lets this module import cleanly when the gate is not available.
    """
    return os.environ.get(f"VCOWS_VSMOKE_{name}", "")


ENDPOINT = _fact("ENDPOINT")
DATACENTER = _fact("DATACENTER")
DATASTORE = _fact("DATASTORE")
CLUSTER = _fact("CLUSTER")
NETWORK = _fact("NETWORK")
DEPLOYMENT = _fact("DEPLOYMENT")
VM = _fact("VM")
TEMPLATE = _fact("TEMPLATE")
TEMPLATE_MARKER_ID = _fact("TEMPLATE_MARKER_ID")
MAC = _fact("MAC")
#: `[datastore] vcows/<vm>/<vm>-seed.iso`, the spelling every SDK call wants.
SEED_PATH = _fact("SEED_PATH")
#: The same file on this machine, as `prepare` wrote it into the run directory.
SEED_LOCAL = _fact("SEED_LOCAL")
SNAPSHOT = _fact("SNAPSHOT")
VCPUS = _fact("VCPUS")
MEMORY_MIB = _fact("MEMORY_MIB")

pytestmark = gate(
    "vcsim",
    all(
        (
            ENDPOINT,
            DATACENTER,
            DATASTORE,
            CLUSTER,
            NETWORK,
            DEPLOYMENT,
            VM,
            TEMPLATE,
            TEMPLATE_MARKER_ID,
            MAC,
            SEED_PATH,
            SEED_LOCAL,
            SNAPSHOT,
            VCPUS,
            MEMORY_MIB,
        )
    ),
    "run `just smoke-vsphere` rather than pytest: the script exports every "
    "VCOWS_VSMOKE_* constant this file reads, starts the simulator it asserts "
    "against, and deploys the VM they describe",
)

#: The config `api.connect` is handed. Not the one the deploy ran -- that one is
#: on disk under the script's work directory and carries a password. This is the
#: same target, composed from the constants, which is what `target.vsphere`
#: needs and no more. `insecure` because vcsim's certificate is self-signed and
#: generated per run, so there is no CA to verify it against.
CONNECT: dict = {
    "target": {
        "vsphere": {
            "endpoint": ENDPOINT,
            "user": "vcows@vsphere.local",
            "password": "not-a-password",
            "datacenter": DATACENTER,
            "datastore": DATASTORE,
            "network": NETWORK,
            "cluster": CLUSTER,
            "insecure": True,
        }
    }
}


@pytest.fixture(scope="module")
def session():
    """The session the assertions read through -- a second one, opened after
    both verbs have closed theirs."""
    with api.connect(CONNECT) as opened:
        yield opened


@pytest.fixture(scope="module")
def found(session) -> dict[str, dict]:
    """Every VM the simulator holds, by name, as `api.vms` describes it.

    The same single `RetrieveContents` call `preflight` makes, so the properties
    here are the ones the product decides on.
    """
    return {str(props.get("name")): props for props in api.vms(session.content)}


def devices(props: dict, kind: type) -> list:
    """One VM's devices of one type, off the `config.hardware.device` leaf."""
    return [d for d in props.get("config.hardware.device") or () if isinstance(d, kind)]


def seed_over_http(session) -> requests.Response:
    """GET the seed ISO back through the `/folder` endpoint `create.upload` PUT
    it to.

    The endpoint is plain HTTPS authorised by the SOAP session cookie, so this
    is the only way to ask a datastore for a file's contents at all -- and
    reading it back is what makes the upload assertion about bytes that moved
    rather than about a call that returned. `create._cookie` builds the one
    header pair, rather than a second implementation of the split that pyvmomi's
    stub value needs.
    """
    relative = SEED_PATH.partition("] ")[2]
    url = f"{ENDPOINT}/folder/{quote(relative)}?" + urlencode(
        {"dcPath": DATACENTER, "dsName": DATASTORE}
    )
    return requests.get(
        url,
        cookies=create._cookie(session),
        verify=session.verify,
        timeout=30,
    )


class TestApplied:
    """After `vcows deploy`, and while the VM is running."""

    # -- the template --------------------------------------------------------

    def test_the_import_left_a_template_behind(self, found):
        """The whole of the import path in one property: the OVF descriptor, the
        `CreateImportSpec`, the lease, the POST and `HttpNfcLeaseComplete` end
        with a VM in the inventory that `MarkAsTemplate` flipped.
        """
        assert TEMPLATE in found, sorted(found)
        assert found[TEMPLATE].get("config.template") is True

    def test_the_template_carries_the_marker(self, found):
        """`preflight._image` refuses to clone from a template carrying none, so
        a run that imported one without this would refuse its own work on the
        next deploy. The id alone, not the whole payload: the marker carries the
        tool's version and a bump must not fail this gate.
        """
        assert TEMPLATE_MARKER_ID in (found[TEMPLATE].get("config.annotation") or "")

    def test_the_template_has_the_snapshot_a_linked_clone_names(self, session, found):
        """`clone_vm` reads `template.snapshot.currentSnapshot` and a template
        with none reaches vCenter as a linked spec naming nothing.

        Read through the collector like everything else here, so the name comes
        off `snapshot.rootSnapshotList` rather than off a managed object.
        """
        props = api.properties(
            session.content, found[TEMPLATE]["obj"], ("snapshot.rootSnapshotList",)
        )
        assert [t.name for t in props.get("snapshot.rootSnapshotList") or ()] == [
            SNAPSHOT
        ]

    # -- the VM --------------------------------------------------------------

    def test_the_clone_exists_and_is_not_a_template(self, found):
        assert VM in found, sorted(found)
        assert not found[VM].get("config.template")

    # **No assertion about the clone's own marker here.** N11: vcsim drops the
    # annotation off a `CloneSpec`, so the property is absent whatever the
    # product sent, and an assertion either way would be about the simulator.
    # `scripts/smoke-vsphere.sh` puts the marker on after this phase, which is
    # what gives the teardown below a target.

    def test_the_clone_has_a_uuid(self, found):
        """The only identity `destroy` is handed. An inventory carrying none
        names a VM nothing could remove."""
        assert found[VM].get("summary.config.uuid")

    def test_the_clone_is_powered_on(self, found):
        assert found[VM].get("runtime.powerState") == "poweredOn"

    def test_the_clone_took_the_config_specs_cpu_and_memory(self, session, found):
        """Not the template's. It is imported as a one-CPU, 512 MiB shell, and
        every clone overrides both from its own config -- so these prove the
        `ConfigSpec` on the `CloneSpec` was applied rather than inherited.
        """
        props = api.properties(
            session.content,
            found[VM]["obj"],
            ("config.hardware.numCPU", "config.hardware.memoryMB"),
        )
        assert props.get("config.hardware.numCPU") == int(VCPUS)
        assert props.get("config.hardware.memoryMB") == int(MEMORY_MIB)

    def test_the_cdrom_is_backed_by_the_seed_iso_on_the_datastore(self, found):
        """The path `preflight._media` reads back and `destroy` deletes, so a
        clone whose cdrom names anything else is a seed ISO nothing would ever
        collect."""
        cdroms = devices(found[VM], vim.vm.device.VirtualCdrom)
        assert [str(d.backing.fileName) for d in cdroms] == [SEED_PATH]

    def test_the_nic_carries_the_derived_mac_as_a_manual_address(self, found):
        """cloud-init matches an interface by MAC to apply the static address, so
        the derived value has to reach the adapter and `addressType` has to say
        it was ours rather than generated. N5: that a hypervisor *keeps* a
        `52:54:00` manual MAC is a first-contact question, not this one.
        """
        nics = devices(found[VM], vim.vm.device.VirtualEthernetCard)
        assert [(n.macAddress, n.addressType) for n in nics] == [(MAC, "manual")]

    def test_the_clones_disk_landed_on_the_configured_datastore(self, found):
        """Placement, and deliberately nothing about the backing's shape.

        N1: vcsim answers `parent` None and `FlatVer2BackingInfo` for a linked
        spec and a full one alike, so there is no observable difference to assert
        on. What a linked clone's `RelocateSpec` carries is pinned offline in
        `tests/test_vsphere_create.py`; what vCenter makes of it is #318's.
        """
        disks = devices(found[VM], vim.vm.device.VirtualDisk)
        assert disks, "the clone has no disk at all"
        assert all(
            str(d.backing.fileName).startswith(f"[{DATASTORE}]") for d in disks
        ), [str(d.backing.fileName) for d in disks]

    # -- the datastore -------------------------------------------------------

    def test_the_seed_iso_reads_back_byte_identical(self, session):
        """The `/folder` PUT, asserted on the bytes rather than on its status.

        `tests/fake_vsphere.py` records the body it was handed and hands it back,
        which cannot tell a transfer from an echo. This is a second process that
        stored the file and a third request that fetched it.
        """
        answer = seed_over_http(session)
        assert answer.status_code == 200, answer.text[:200]
        assert answer.content == Path(SEED_LOCAL).read_bytes()


class TestDestroyed:
    """After `vcows destroy --yes`, and before the script's own cleanup.

    The subject is the teardown the shipped verb runs. vcsim's inventory dies
    with the process, so running this after the script's trap would assert
    against nothing at all.
    """

    def test_destroy_removed_the_vm(self, found):
        assert VM not in found, sorted(found)

    def test_destroy_removed_the_seed_iso(self, session):
        """`DeleteDatastoreFile_Task`, asserted through the same endpoint the
        upload used: a file the datastore no longer holds is a 404 rather than a
        shorter body."""
        assert seed_over_http(session).status_code == 404

    def test_destroy_left_the_template_alone(self, found):
        """The golden image is shared, and every other deployment's clones are
        overlays on it.

        `preflight._existing` skips a template outright, so it is never a target
        -- and `destroy` refuses one that reaches it anyway. A teardown that took
        this with it would take every other deployment on the vCenter.
        """
        assert TEMPLATE in found
        assert found[TEMPLATE].get("config.template") is True
