"""The apply: the datastore upload, the two imports, the template, the clones.

The shared half drives one function directly, because each is reachable on its
own. The per-VM half below it goes through ``VsphereBackend.create``, for the
reason ``tests/test_proxmox_create.py`` does: the wiring between ``render`` and
the spec vCenter is sent is exactly what a unit test of either half cannot see,
since a key renamed on one side and read on the other passes both.

Four of the questions come from the vcsim spike rather than from the API docs,
and they are the ones a fake is worth having for: the lease is read before it is
completed and never after, the ``*``-to-host substitution is harmless when there
is no ``*``, the lease hears about the upload while the upload is happening, and
a linked clone needs a snapshot that only exists if the template was snapshotted
before it was marked. ``tests/fake_vsphere.py``'s ``FakeLease`` faults on any
read after ``HttpNfcLeaseComplete`` and its ``CloneVM_Task`` refuses a linked
spec naming no snapshot -- which vcsim does not, because A8 says it accepts the
spec and discards it.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from pyVmomi import vim, vmodl

from orchestrator.backends.vsphere import VsphereBackend, api
from orchestrator.backends.vsphere import create as create_mod
from tests.fake_vsphere import (
    COOKIE,
    DEVICE_URL,
    FakeContent,
    FakeFolder,
    FakeHttp,
    FakeLease,
    FakePool,
    FakeServiceInstance,
    FakeVm,
    disk,
    mo,
)

#: The tests that wait on a lease or a task poll a fake. See
#: `conftest._no_vsphere_polling_delay`.
pytestmark = pytest.mark.usefixtures("_no_vsphere_polling_delay")

#: What `prepare` converted, in the run directory. Big enough to be read in more
#: than one chunk, which is what makes the progress calls observable.
VMDK_BYTES = b"# not a real streamOptimized vmdk\n" + b"\x00" * (64 * 1024 - 34)

#: The golden image's virtual size, as `prepare` read it off the qcow2 header.
#: Deliberately not the VMDK's own size: a streamOptimized disk is compressed,
#: and the descriptor has to declare both.
CAPACITY = 20 * 1024**3

#: `image.base_volume_name` in `VSPHERE_CONFIG`, which is the template's name.
TEMPLATE = "golden.qcow2"

#: The key vCenter gave the template's one disk. Fixed so that a `deviceChange`
#: of operation `edit` names something: every device a fake builds otherwise has
#: key 0, and an edit would match all of them at once.
TEMPLATE_DISK_KEY = 2000

OVF_NS = "{http://schemas.dmtf.org/ovf/envelope/1}"


class Vcenter:
    """The vCenter `VSPHERE_CONFIG` names, with the objects an import lands in.

    Built as one thing because every function under test resolves the same five
    names, and a test that cares about only one of them still needs the rest to
    resolve.
    """

    def __init__(
        self,
        entity: Any = None,
        urls=(DEVICE_URL,),
        initializing: int = 0,
        lease_error: Any = None,
        never_ready: bool = False,
        ovf_error=(),
        create_error: Any = None,
        decoys: bool = False,
    ):
        self.imported = (
            entity
            if entity is not None
            else FakeVm(
                TEMPLATE,
                devices=[
                    disk(
                        f"[ds-a] {TEMPLATE}/{TEMPLATE}.vmdk",
                        key=TEMPLATE_DISK_KEY,
                        capacity_kb=CAPACITY // 1024,
                    )
                ],
            )
        )
        #: On the vCenter, not beside it: a clone is answered for by the property
        #: collector, and the template has to be findable by name on the deploy
        #: that does not import one.
        self.content = FakeContent(vms=[self.imported])
        self.content.ovfManager.error = list(ovf_error)
        self.lease = FakeLease(
            self.imported.mo,
            urls=urls,
            initializing=initializing,
            error=lease_error,
            never_ready=never_ready,
            log=self.content.calls,
        )
        self.datacenter = mo(vim.Datacenter, "datacenter-1", name="dc-a", vmFolder=None)
        here = self.datacenter
        calls = self.content.calls
        #: The cluster's own root pool, which is where a cluster placement lands.
        self.pool = FakePool(self.lease, container=here, log=calls)
        #: What `target.vsphere.resource_pool` names when it is set.
        self.named_pool = FakePool(
            self.lease, name="vcows-pool", container=here, log=calls
        )
        #: The datacenter's own VM folder, and the one a config can name instead.
        self.folder = FakeFolder(
            self.imported.mo, container=here, log=calls, error=create_error
        )
        self.named_folder = FakeFolder(
            self.imported.mo,
            name="vcows",
            container=here,
            log=calls,
            error=create_error,
        )
        self.datacenter.vmFolder = self.folder.mo
        self.datastore = mo(
            vim.Datastore, "datastore-1", name="ds-a", container=self.datacenter
        )
        #: The port group every NIC of every VM is backed by, which `render`
        #: puts on each of them from `target.vsphere.network`.
        self.network = mo(
            vim.Network, "network-1", name="pg-vcows", container=self.datacenter
        )
        self.cluster = mo(
            vim.ClusterComputeResource,
            "domain-c1",
            name="cluster-a",
            container=self.datacenter,
            resourcePool=self.pool.mo,
        )
        #: A host placement's pool is its own compute resource's, not a
        #: cluster's, which is what an unclustered host has.
        self.host_pool = FakePool(self.lease, container=here, log=calls)
        self.host = mo(
            vim.HostSystem,
            "host-1",
            name="esx1.example.com",
            container=self.datacenter,
            parent=mo(vim.ComputeResource, "domain-s1", resourcePool=self.host_pool.mo),
        )
        self.content.objects = [
            self.datacenter,
            self.datastore,
            self.network,
            self.cluster,
            self.folder.mo,
            self.named_folder.mo,
            self.pool.mo,
            self.named_pool.mo,
            self.host,
        ]
        if decoys:
            # First, so that a lookup which lost its datacenter finds one of
            # these rather than the object it was asked for.
            self.content.objects[:0] = _decoys()

    @property
    def session(self) -> api.Session:
        return api.Session(
            si=FakeServiceInstance(self.content), content=self.content, cookie=COOKIE
        )


def _decoys() -> list:
    """A second datacenter holding one of everything, under the same names.

    vCenter allows it, which is why every lookup this module makes is rooted at
    the datacenter `target.vsphere` names. Nothing here can stand in silently:
    the folder makes no VM and the pools hand out no lease, so an import that
    resolved one of them fails rather than passing against the wrong object.
    """
    other = mo(vim.Datacenter, "datacenter-2", name="dc-b", vmFolder=None)
    stray = FakeFolder(name="vm", container=other)
    other.vmFolder = stray.mo
    return [
        other,
        mo(vim.Datastore, "datastore-2", name="ds-a", container=other),
        mo(vim.Network, "network-2", name="pg-vcows", container=other),
        mo(
            vim.ClusterComputeResource,
            "domain-c2",
            name="cluster-a",
            container=other,
            resourcePool=FakePool(name="Resources", container=other).mo,
        ),
        stray.mo,
        FakeFolder(name="vcows", container=other).mo,
        FakePool(name="vcows-pool", container=other).mo,
        mo(
            vim.HostSystem,
            "host-2",
            name="esx1.example.com",
            container=other,
            parent=mo(
                vim.ComputeResource,
                "domain-s2",
                resourcePool=FakePool(name="Resources", container=other).mo,
            ),
        ),
    ]


@pytest.fixture
def vcenter() -> Vcenter:
    return Vcenter()


@pytest.fixture
def http(monkeypatch) -> FakeHttp:
    return FakeHttp().install(monkeypatch)


@pytest.fixture
def vmdk(tmp_path):
    """The converted image, and the `-flat` extent a `monolithicFlat`
    conversion writes beside it."""
    path = tmp_path / "golden.vmdk"
    path.write_bytes(VMDK_BYTES)
    (tmp_path / "golden-flat.vmdk").write_bytes(b"flat extent" * 100)
    return path


# -- the datastore upload ------------------------------------------------


def test_the_url_names_the_datacenter_and_the_datastore(vsphere_cfg, vcenter, vmdk):
    """vCenter cannot resolve a datastore path without both, and the endpoint is
    the one `target.vsphere` names -- the upload goes through vCenter, never to
    an ESXi host."""
    http = FakeHttp()
    with pytest.MonkeyPatch.context() as patch:
        http.install(patch)
        stored = create_mod.upload(
            vsphere_cfg, vcenter.session, vmdk, "vcows/app01/app01-seed.iso"
        )
    assert http.calls[0]["method"] == "PUT"
    assert http.calls[0]["url"] == (
        "https://vcenter.example.com/folder/vcows/app01/app01-seed.iso"
        "?dcPath=dc-a&dsName=ds-a"
    )
    assert stored == "[ds-a] vcows/app01/app01-seed.iso"


def test_an_endpoint_with_a_trailing_slash_makes_the_same_url(
    vsphere_cfg, vcenter, vmdk, http
):
    """`https://vc/` and `https://vc` are the same vCenter, and `//folder/` is
    not the same path: vCenter answers it with a 404 that names nothing."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://vcenter.example.com/"
    create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["url"].startswith(
        "https://vcenter.example.com/folder/vcows/x/golden.vmdk?"
    )


def test_the_file_goes_over_the_wire_as_it_is(vsphere_cfg, vcenter, vmdk, http):
    create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["body"] == VMDK_BYTES


def test_the_session_cookie_is_what_authorises_the_upload(
    vsphere_cfg, vcenter, vmdk, http
):
    """One pair, out of the `Set-Cookie`-shaped string pyvmomi keeps on the
    stub: sending its `Path` and `HttpOnly` back is not a cookie."""
    create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["cookies"] == {"vmware_soap_session": "52ab-not-a-session"}


def test_the_upload_verifies_tls_the_way_the_session_resolved_it(
    vsphere_cfg, vcenter, vmdk, http
):
    """`connect` made the decision once, in the vocabulary `requests` speaks. An
    upload that decided again could verify over HTTP and not over SOAP."""
    session = api.Session(
        si=FakeServiceInstance(vcenter.content),
        content=vcenter.content,
        cookie=COOKIE,
        verify=False,
    )
    create_mod.upload(vsphere_cfg, session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["verify"] is False


def test_the_upload_carries_a_timeout(vsphere_cfg, vcenter, vmdk, http):
    """Between two socket operations rather than over the whole PUT, so a
    multi-GB image that keeps moving never reaches it and one that stalls does."""
    create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["timeout"] == create_mod.HTTP_TIMEOUT


def test_a_cookie_value_carrying_an_equals_sign_survives_the_split(
    vsphere_cfg, vcenter, vmdk, http
):
    """A session cookie is base64 and base64 pads with `=`, so the pair splits
    at the first one and not the last."""
    session = api.Session(
        si=FakeServiceInstance(vcenter.content),
        content=vcenter.content,
        cookie='vmware_soap_session="c2Vzc2lvbg=="; Path=/; HttpOnly; Secure;',
    )
    create_mod.upload(vsphere_cfg, session, vmdk, "vcows/x/golden.vmdk")
    assert http.calls[0]["cookies"] == {"vmware_soap_session": "c2Vzc2lvbg=="}


def test_a_refused_upload_says_what_vcenter_answered(vsphere_cfg, vcenter, vmdk):
    """The body is where vCenter says why a datastore refused a write, and a
    status code on its own names neither the file nor the reason."""
    with pytest.MonkeyPatch.context() as patch:
        FakeHttp(status_code=403, text="Permission to perform this was denied").install(
            patch
        )
        with pytest.raises(api.VsphereApiError) as bad:
            create_mod.upload(
                vsphere_cfg, vcenter.session, vmdk, "vcows/app01/app01-seed.iso"
            )
    assert str(bad.value) == (
        "upload vcows/app01/app01-seed.iso: vCenter answered HTTP 403 "
        "(Permission to perform this was denied)"
    )


def test_the_first_status_that_is_not_a_success_is_refused(vsphere_cfg, vcenter, vmdk):
    """400 itself, not one past it: vCenter answers a malformed datastore path
    with exactly that and a run that took it for a success would carry on to a
    `CreateVM_Task` naming a file that is not there."""
    with pytest.MonkeyPatch.context() as patch:
        FakeHttp(status_code=400, text="Bad Request").install(patch)
        with pytest.raises(api.VsphereApiError, match="HTTP 400"):
            create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/g.vmdk")


def test_a_fault_page_is_truncated_rather_than_printed_whole(
    vsphere_cfg, vcenter, vmdk
):
    """vCenter answers some refusals with an HTML page, and the run's `error`
    field is what an air-gapped site ships back."""
    with pytest.MonkeyPatch.context() as patch:
        FakeHttp(status_code=500, text="x" * 500).install(patch)
        with pytest.raises(api.VsphereApiError) as bad:
            create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/g.vmdk")
    assert str(bad.value).endswith(f"({'x' * 200})")


def test_the_size_is_logged_before_the_bytes_move(
    vsphere_cfg, vcenter, vmdk, http, caplog
):
    """The PUT answers nothing until the last byte is in, so this line is all an
    operator has while a multi-GB image goes over the wire."""
    with caplog.at_level(logging.INFO):
        create_mod.upload(vsphere_cfg, vcenter.session, vmdk, "vcows/x/golden.vmdk")
    assert "uploading vcows/x/golden.vmdk (0 MiB)" in caplog.text


def test_the_size_is_logged_in_mebibytes(vsphere_cfg, vcenter, tmp_path, http, caplog):
    """A golden image is measured in gibibytes and its seed ISO in kibibytes, so
    the unit is what makes the line worth printing at all."""
    big = tmp_path / "big.vmdk"
    big.write_bytes(b"\0" * (2 * 1024**2))
    with caplog.at_level(logging.INFO):
        create_mod.upload(vsphere_cfg, vcenter.session, big, "vcows/x/big.vmdk")
    assert "uploading vcows/x/big.vmdk (2 MiB)" in caplog.text


# -- the OVF lease import ------------------------------------------------


def imported(vsphere_cfg, vcenter) -> Any:
    return create_mod.import_ovf(
        vsphere_cfg, vcenter.session, VMDK_PATH[0], TEMPLATE, CAPACITY
    )


#: Filled in by the `vmdk` fixture through `ovf`, so the helper above reads one
#: path without every test threading it through.
VMDK_PATH: list = [None]


@pytest.fixture
def ovf(vmdk):
    VMDK_PATH[0] = vmdk
    return vmdk


def descriptor(vcenter) -> Any:
    """The OVF the import handed vCenter, parsed."""
    return ET.fromstring(vcenter.content.ovfManager.specs[0][0])


def test_the_import_says_what_it_is_moving_and_where_it_is_going(
    vsphere_cfg, vcenter, tmp_path, http, caplog
):
    """One line before a transfer that answers nothing until the last byte is
    in. The name is the template it becomes, which is not the file's."""
    big = tmp_path / "big.vmdk"
    big.write_bytes(b"\0" * (2 * 1024**2))
    with caplog.at_level(logging.INFO):
        create_mod.import_ovf(vsphere_cfg, vcenter.session, big, TEMPLATE, CAPACITY)
    assert f"importing {big} as {TEMPLATE} (2 MiB)" in caplog.text


def test_the_descriptor_declares_the_capacity_and_the_transfer_size(
    vsphere_cfg, vcenter, ovf, http
):
    """Two different numbers. The capacity is the golden image's virtual size,
    which is what the disk becomes; the file size is what actually moves, and on
    a streamOptimized VMDK the two differ by however well it compressed."""
    imported(vsphere_cfg, vcenter)
    root = descriptor(vcenter)
    disk = root.find(f"{OVF_NS}DiskSection/{OVF_NS}Disk")
    assert disk.get(f"{OVF_NS}capacity") == str(CAPACITY)
    assert disk.get(f"{OVF_NS}capacityAllocationUnits") == "byte"
    found = root.find(f"{OVF_NS}References/{OVF_NS}File")
    assert found.get(f"{OVF_NS}size") == str(len(VMDK_BYTES))


def test_the_descriptor_names_the_template_and_the_stream_optimized_format(
    vsphere_cfg, vcenter, ovf, http
):
    """The name is the VM the import makes, and the format is what an
    `ImportVApp` lease reads. A descriptor declaring anything else fails at the
    far end, mid-upload."""
    imported(vsphere_cfg, vcenter)
    root = descriptor(vcenter)
    assert root.find(f"{OVF_NS}VirtualSystem").get(f"{OVF_NS}id") == TEMPLATE
    params = vcenter.content.ovfManager.specs[0][3]
    assert params.entityName == TEMPLATE
    # Thin, because the golden image is mostly empty and the template is only
    # ever a clone source: nothing runs on it, so nothing writes to it.
    assert params.diskProvisioning == "thin"
    assert (
        root.find(f"{OVF_NS}DiskSection/{OVF_NS}Disk")
        .get(f"{OVF_NS}format")
        .endswith("#streamOptimized")
    )


def test_the_import_lands_in_the_configured_placement(vsphere_cfg, vcenter, ovf, http):
    """The pool and the datastore are what the spec is built against, and the
    folder is where the VM appears. A cluster placement means the cluster's own
    root pool, and no host."""
    imported(vsphere_cfg, vcenter)
    _, pool, datastore, _ = vcenter.content.ovfManager.specs[0]
    assert pool is vcenter.pool.mo
    assert datastore.name == "ds-a"
    spec, folder, host = vcenter.pool.imports[0]
    assert isinstance(spec, vim.vm.VmImportSpec)
    assert folder is vcenter.folder.mo
    assert host is None


def test_the_disk_is_posted_to_the_lease_url_with_the_endpoint_host(
    vsphere_cfg, vcenter, ovf, http
):
    """A4: vCenter is expected to answer with `*` where the host belongs,
    meaning whichever address you reached it on."""
    imported(vsphere_cfg, vcenter)
    assert http.calls[0]["method"] == "POST"
    assert http.calls[0]["url"] == (
        "https://vcenter.example.com/nfc/session/52ab-not-a-session/disk-0.vmdk"
    )
    assert http.calls[0]["headers"] == {
        "Content-Type": "application/x-vnd.vmware-streamVmdk"
    }
    assert http.calls[0]["body"] == VMDK_BYTES
    # The lease upload is the same HTTPS endpoint as the `/folder` PUT, so it
    # verifies the way the session resolved it and goes quiet for as long.
    assert http.calls[0]["verify"] is True
    assert http.calls[0]["timeout"] == create_mod.HTTP_TIMEOUT


def test_a_device_url_that_already_names_a_host_is_left_alone(vsphere_cfg, ovf, http):
    """What vcsim answers with -- its own listen address, already resolved -- and
    what a vCenter behind a proxy may. The substitution has to be a no-op, and
    nothing may assert that a `*` was there."""
    vcenter = Vcenter(urls=("https://127.0.0.1:8989/nfc/session/disk-0.vmdk",))
    imported(vsphere_cfg, vcenter)
    assert http.calls[0]["url"] == "https://127.0.0.1:8989/nfc/session/disk-0.vmdk"


def test_a_device_url_carrying_a_port_keeps_it(vsphere_cfg, ovf, http):
    """`https://*:443/...` is the other shape A4 allows, and only the host is
    the SDK's to replace."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://vcenter.example.com:8443"
    vcenter = Vcenter(urls=("https://*:443/nfc/session/disk-0.vmdk",))
    imported(vsphere_cfg, vcenter)
    assert http.calls[0]["url"] == (
        "https://vcenter.example.com:443/nfc/session/disk-0.vmdk"
    )


def test_the_upload_waits_for_the_lease_to_be_ready(vsphere_cfg, ovf, http):
    """`ImportVApp` answers before vCenter has the lease open, so the device URL
    is not there yet. A caller that read the state once would POST to nothing."""
    vcenter = Vcenter(initializing=3)
    imported(vsphere_cfg, vcenter)
    assert vcenter.lease.polls > 3
    assert len(http.calls) == 1


def test_a_lease_that_never_becomes_ready_times_out_rather_than_hanging(
    vsphere_cfg, ovf, http, monkeypatch
):
    """The clock is pinned so that reaching the deadline exactly is what is under
    test, as `test_vsphere_backend.py` pins it for `api.wait`."""
    monkeypatch.setattr(api, "TASK_TIMEOUT", 0)
    monkeypatch.setattr(create_mod.time, "monotonic", lambda: 1000.0)
    with pytest.raises(api.VsphereApiError, match="still initializing after 0s"):
        imported(vsphere_cfg, Vcenter(never_ready=True))
    assert http.calls == []


def test_a_lease_that_comes_back_in_error_is_refused(vsphere_cfg, ovf, http):
    """vCenter answers a lease it could not open with the fault on the lease
    itself, and nothing else says the import failed."""
    vcenter = Vcenter(lease_error=vim.fault.NoDiskSpace(msg="the datastore is full"))
    with pytest.raises(api.VsphereApiError) as bad:
        imported(vsphere_cfg, vcenter)
    # The sentence vCenter wrote and the template it was writing about. Compared
    # whole because pyvmomi renders a fault as its entire field list, which
    # carries the same sentence buried in it.
    assert str(bad.value) == (
        f"import {TEMPLATE}: the lease came back error (the datastore is full)"
    )
    assert http.calls == []


def test_a_lease_with_no_device_url_is_refused(vsphere_cfg, ovf, http):
    """Rather than an IndexError naming nothing."""
    with pytest.raises(api.VsphereApiError) as bad:
        imported(vsphere_cfg, Vcenter(urls=()))
    assert str(bad.value) == f"import {TEMPLATE}: the lease named no device URL"


def test_the_imported_vm_is_read_before_the_lease_is_completed(
    vsphere_cfg, vcenter, ovf, http
):
    """`HttpNfcLeaseComplete` deletes the lease: reading `info.entity` afterwards
    raises `ManagedObjectNotFound`, which is the opposite of what the obvious
    sequence does. The fake faults on any read after the completion, so this
    fails here rather than against a vCenter."""
    assert imported(vsphere_cfg, vcenter) is vcenter.imported.mo
    assert vcenter.lease.completed


def test_a_refused_descriptor_names_every_fault_vcenter_gave(vsphere_cfg, ovf, http):
    """A list, because that is the shape `CreateImportSpecResult` carries and a
    caller reading only the first would hide the rest."""
    vcenter = Vcenter(
        ovf_error=[
            vim.fault.OvfUnsupportedType(msg="the disk format is not supported"),
            vim.fault.OvfHardwareCheck(msg="vmx-13 is too old"),
        ]
    )
    with pytest.raises(api.VsphereApiError) as bad:
        imported(vsphere_cfg, vcenter)
    assert str(bad.value) == (
        f"vCenter refused the OVF descriptor for {TEMPLATE}: "
        "the disk format is not supported; vmx-13 is too old"
    )
    assert vcenter.pool.imports == []


def test_a_refused_lease_post_fails_the_import_and_names_the_file(
    vsphere_cfg, vcenter, ovf
):
    """The lease is the one call vCenter answers over HTTP rather than SOAP, so
    nothing else would say the import failed."""
    with pytest.MonkeyPatch.context() as patch:
        FakeHttp(status_code=500, text="the lease has expired").install(patch)
        with pytest.raises(api.VsphereApiError) as bad:
            imported(vsphere_cfg, vcenter)
    assert str(bad.value) == (
        "POST golden.vmdk to the lease: vCenter answered HTTP 500 "
        "(the lease has expired)"
    )
    assert not vcenter.lease.completed


def test_a_one_byte_disk_is_reported_rather_than_dividing_by_its_size(
    vsphere_cfg, vcenter, tmp_path, http
):
    """The percentage is a division by the file's size, and a conversion that
    wrote almost nothing must fail at vCenter rather than here."""
    tiny = tmp_path / "tiny.vmdk"
    tiny.write_bytes(b"\0")
    create_mod.import_ovf(vsphere_cfg, vcenter.session, tiny, TEMPLATE, CAPACITY)
    assert vcenter.lease.progress == [99, 100]


def test_an_empty_disk_reports_progress_rather_than_raising(
    vsphere_cfg, vcenter, tmp_path, http
):
    """Zero bytes is a conversion that failed silently, and dividing by it here
    would replace vCenter's answer with a ZeroDivisionError."""
    empty = tmp_path / "empty.vmdk"
    empty.write_bytes(b"")
    create_mod.import_ovf(vsphere_cfg, vcenter.session, empty, TEMPLATE, CAPACITY)
    assert vcenter.lease.progress == [100]


def test_the_lease_is_told_every_step_and_not_every_read(
    vsphere_cfg, vcenter, tmp_path, monkeypatch
):
    """Read a byte at a time, so that every percentage between 0 and 100 is
    reached: what the lease hears is one call per `PROGRESS_STEP` and not one
    per read, which would be a SOAP call per 8 KiB of a golden image."""
    http = FakeHttp(chunk=1).install(monkeypatch)
    hundred = tmp_path / "hundred.vmdk"
    hundred.write_bytes(b"\0" * 100)
    create_mod.import_ovf(vsphere_cfg, vcenter.session, hundred, TEMPLATE, CAPACITY)
    assert vcenter.lease.progress == [
        5,
        10,
        15,
        20,
        25,
        30,
        35,
        40,
        45,
        50,
        55,
        60,
        65,
        70,
        75,
        80,
        85,
        90,
        95,
        100,
    ]
    assert http.calls[0]["body"] == b"\0" * 100


def test_the_lease_hears_progress_while_the_upload_is_happening(
    vsphere_cfg, vcenter, ovf, http
):
    """vCenter expires a lease that goes quiet, and the whole image moves in one
    POST -- so there is nowhere else to call this from. The last percent is the
    completed POST's to report, which is why nothing before it reads 100."""
    imported(vsphere_cfg, vcenter)
    assert vcenter.lease.progress == [12, 25, 37, 50, 62, 75, 87, 99, 100]


def test_the_body_declares_its_length_so_the_post_is_not_chunked(
    vsphere_cfg, vcenter, ovf, http
):
    """Without a length `requests` sends the body chunked, which the NFC endpoint
    does not take."""
    imported(vsphere_cfg, vcenter)
    assert http.calls[0]["length"] == len(VMDK_BYTES)


# -- the datastore import, the `import: datastore` knob -------------------


def test_both_the_descriptor_and_its_extent_are_uploaded(
    vsphere_cfg, vcenter, vmdk, http
):
    """A `monolithicFlat` conversion is two files, and the descriptor names the
    extent by file name alone -- so both arrive in one folder under the names
    qemu-img gave them, or the reference does not resolve."""
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    assert [call["url"].split("/folder/")[1].split("?")[0] for call in http.calls] == [
        f"vcows/{TEMPLATE}/golden.vmdk",
        f"vcows/{TEMPLATE}/golden-flat.vmdk",
    ]
    assert http.calls[1]["body"] == b"flat extent" * 100


def test_the_vm_attaches_the_file_that_was_just_uploaded(
    vsphere_cfg, vcenter, vmdk, http
):
    """No `fileOperation` on the disk: `create` would have vCenter make a new
    empty disk over the one that just moved."""
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    spec, pool, host = vcenter.folder.creates[0]
    assert spec.name == TEMPLATE
    [controller, disk] = spec.deviceChange
    assert (controller.operation, disk.operation) == ("add", "add")
    assert disk.fileOperation is None
    assert disk.device.backing.fileName == f"[ds-a] vcows/{TEMPLATE}/golden.vmdk"
    assert disk.device.capacityInKB == CAPACITY // 1024
    assert disk.device.backing.diskMode == "persistent"
    assert disk.device.backing.thinProvisioned is True
    assert pool is vcenter.pool.mo
    assert host is None


def test_the_disk_hangs_off_the_controller_the_same_spec_adds(
    vsphere_cfg, vcenter, vmdk, http
):
    """One `CreateVM_Task` builds both, so the disk's `controllerKey` has to be
    the key the controller was given in the same call. vCenter numbers new
    devices itself and takes these only as references, which is why they are
    negative -- a positive key names a device that is already there."""
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    spec, _, _ = vcenter.folder.creates[0]
    [controller, disk] = spec.deviceChange
    assert isinstance(controller.device, vim.vm.device.VirtualLsiLogicController)
    assert controller.device.key < 0
    assert controller.device.busNumber == 0
    assert controller.device.sharedBus == "noSharing"
    assert disk.device.key < 0
    assert disk.device.key != controller.device.key
    assert disk.device.controllerKey == controller.device.key
    assert disk.device.unitNumber == 0


def test_the_shell_the_template_is_created_as_is_the_one_the_ovf_declares(
    vsphere_cfg, vcenter, vmdk, http
):
    """Every clone overrides the CPU and the memory from its own config, so
    these are never what a VM runs with. The datastore is named without a folder
    so that vCenter picks the VM's directory itself."""
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    spec, _, _ = vcenter.folder.creates[0]
    assert (spec.numCPUs, spec.memoryMB) == (1, 512)
    assert spec.guestId == "otherGuest64"
    assert spec.files.vmPathName == "[ds-a]"


def test_the_datastore_import_answers_with_the_vm_it_made(
    vsphere_cfg, vcenter, vmdk, http
):
    """Through `api.wait`, so a `CreateVM_Task` that ended badly is a failure
    here rather than a template that was never made."""
    made = create_mod.import_flat(
        vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY
    )
    assert made is vcenter.imported.mo


def test_a_create_that_fails_is_this_backend_s_error(vsphere_cfg, vmdk, http):
    vcenter = Vcenter(create_error=vim.fault.FileNotFound(msg="no such disk"))
    with pytest.raises(api.VsphereApiError, match="no such disk"):
        create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)


# -- the template --------------------------------------------------------


def test_the_marker_and_the_firmware_reach_the_reconfigure(vcenter):
    """The annotation is the durable record of what vcows made -- `destroy`
    discovers by it -- and the firmware is set on the template because a clone
    inherits it."""
    create_mod.make_template(vcenter.imported.mo, "the-marker")
    [spec] = vcenter.imported.reconfigured
    assert spec.annotation == "the-marker"
    assert spec.firmware == "efi"
    assert spec.bootOptions.efiSecureBootEnabled is False
    assert vcenter.imported.props["config.annotation"] == "the-marker"


def test_the_snapshot_is_taken_before_the_vm_is_marked_as_a_template(vcenter):
    """A linked clone is an overlay on a snapshot, and neither a reconfigure nor
    a snapshot is allowed once the VM is a template. The fake refuses both in
    that state, so a reordering fails here."""
    create_mod.make_template(vcenter.imported.mo, "the-marker")
    assert vcenter.imported.log == [
        ("ReconfigVM_Task", TEMPLATE),
        ("CreateSnapshot_Task", TEMPLATE, create_mod.SNAPSHOT_NAME),
        ("MarkAsTemplate", TEMPLATE),
    ]
    assert vcenter.imported.props["config.template"] is True


def test_the_snapshot_takes_no_memory_and_does_not_quiesce(vcenter):
    """The VM has never been powered on: there is no memory to capture and no
    guest agent to quiesce, and asking for either is a task that waits."""
    create_mod.make_template(vcenter.imported.mo, "the-marker")
    [(name, _, memory, quiesce)] = vcenter.imported.snapshots
    assert (name, memory, quiesce) == (create_mod.SNAPSHOT_NAME, False, False)


def test_a_reconfigure_that_fails_stops_before_anything_is_marked(vcenter):
    """Through `api.wait`: a task that stopped is not a task that worked, and a
    template marked over a failed reconfigure carries no marker."""
    vcenter.imported.props["config.template"] = True
    with pytest.raises(api.VsphereApiError, match="template now"):
        create_mod.make_template(vcenter.imported.mo, "the-marker")
    assert vcenter.imported.snapshots == []


# -- placement -----------------------------------------------------------


def test_a_named_folder_and_resource_pool_win_over_the_defaults(
    vsphere_cfg, vcenter, vmdk, http
):
    """Both are optional, and without them the import lands in the datacenter's
    own VM folder and the cluster's root pool."""
    vsphere_cfg["target"]["vsphere"]["folder"] = "vcows"
    vsphere_cfg["target"]["vsphere"]["resource_pool"] = "vcows-pool"
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    _, chosen_pool, _ = vcenter.named_folder.creates[0]
    assert chosen_pool is vcenter.named_pool.mo
    assert vcenter.folder.creates == []


def test_a_host_placement_uses_that_host_s_own_root_pool(
    vsphere_cfg, vcenter, vmdk, http
):
    """`parent` is the host's ComputeResource, not a cluster, which is what an
    unclustered host has and what `CreateVM_Task` wants."""
    del vsphere_cfg["target"]["vsphere"]["cluster"]
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    _, chosen_pool, host = vcenter.folder.creates[0]
    assert chosen_pool is vcenter.host_pool.mo
    assert host is vcenter.host


def test_the_host_a_config_names_is_the_one_the_import_lands_on(vsphere_cfg, ovf, http):
    """`ImportVApp` takes the host as well as the pool, and a lease opened on
    the wrong one puts the template where the clones cannot reach it."""
    vcenter = Vcenter()
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    imported(vsphere_cfg, vcenter)
    assert vcenter.host_pool.imports[0][2] is vcenter.host


def test_every_name_resolves_inside_the_configured_datacenter(vsphere_cfg, ovf, http):
    """Two datacenters on one vCenter may each hold a datastore, a folder or a
    cluster of the same name, so every lookup is rooted at the one
    `target.vsphere` names. The decoys are listed first, so a lookup that lost
    its root finds one of them."""
    vcenter = Vcenter(decoys=True)
    vsphere_cfg["target"]["vsphere"]["folder"] = "vcows"
    imported(vsphere_cfg, vcenter)
    _, pool, datastore, _ = vcenter.content.ovfManager.specs[0]
    assert datastore is vcenter.datastore
    assert pool is vcenter.pool.mo
    assert vcenter.pool.imports[0][1] is vcenter.named_folder.mo


def test_the_host_and_the_resource_pool_are_rooted_there_too(vsphere_cfg, ovf, http):
    """The other two optional names, which the branch above does not reach: a
    resource pool outranks both placements, and the host is resolved whichever
    of them the pool came from."""
    vcenter = Vcenter(decoys=True)
    vsphere_cfg["target"]["vsphere"]["resource_pool"] = "vcows-pool"
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    imported(vsphere_cfg, vcenter)
    assert vcenter.content.ovfManager.specs[0][1] is vcenter.named_pool.mo
    assert vcenter.named_pool.imports[0][2] is vcenter.host


def test_a_name_that_stopped_resolving_is_an_error_naming_the_field(
    vsphere_cfg, vcenter, vmdk, http
):
    """Preflight reported every miss as a Problem and resolved all six. Reaching
    one here means it went away between the two passes, which is an error rather
    than a problem to collect -- nothing above this is still gathering them."""
    vsphere_cfg["target"]["vsphere"]["datastore"] = "ds-gone"
    with pytest.raises(api.VsphereApiError) as bad:
        create_mod.import_flat(vsphere_cfg, vcenter.session, vmdk, TEMPLATE, CAPACITY)
    assert "target.vsphere.datastore names 'ds-gone'" in str(bad.value)
    assert http.calls == []


# -- naming what a failure was making -------------------------------------


def test_the_resource_name_rides_on_the_exception(caplog):
    """`run.json`'s `error` field is what an air-gapped site ships back, and
    `cli._guard` fills it from the exception's text. Without this a failed task
    reads as a vCenter fault and says nothing about what was being made."""
    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(api.VsphereApiError) as bad,
        create_mod._made("template golden.qcow2"),
    ):
        raise vmodl.fault.NotSupported(msg="vmx-13 is too old")
    assert str(bad.value) == (
        "could not create template golden.qcow2: vmx-13 is too old"
    )
    assert isinstance(bad.value.__cause__, vmodl.fault.NotSupported)
    assert "could not create template golden.qcow2" in caplog.text


def test_what_it_made_and_what_it_cost_are_logged(caplog):
    with caplog.at_level(logging.INFO), create_mod._made("template golden.qcow2"):
        pass
    assert "created template golden.qcow2 in" in caplog.text


# -- the whole apply, through the backend --------------------------------


@pytest.fixture
def prepared(tmp_path, vmdk):
    """What `prepare` handed on: the seed ISOs it built and the VMDK it wrote.

    Real files, because `render` is pure but `create` opens every path the
    values name -- and each is named something other than what it has to arrive
    on the datastore as, so an upload that used the local name would be visible.
    """
    seeds = {}
    for name in ("app01", "app02"):
        iso = tmp_path / f"{name}-cidata.iso"
        iso.write_bytes(f"{name} seed iso".encode())
        seeds[name] = str(iso)
    return {
        "seed_isos": seeds,
        "image": {"create": True, "template": TEMPLATE},
        "vmdk": str(vmdk),
        "capacity": CAPACITY,
    }


def deployed(cfg, vcenter, prepared) -> dict:
    return VsphereBackend().create(cfg, vcenter.session, prepared)


def already_there(vcenter, prepared) -> dict:
    """The template an earlier run left behind: marked, and snapshotted first.

    Built by calling the product's own `make_template`, so a test of the second
    deploy starts from the vCenter the first deploy actually leaves.
    """
    create_mod.make_template(vcenter.imported.mo, "the-template-marker")
    prepared["image"] = {"create": False, "template": TEMPLATE}
    del prepared["vmdk"], prepared["capacity"]
    return prepared


def cloned(vcenter, name: str = "app01") -> tuple:
    """The one clone made under `name`: its folder, its spec and the VM it
    became."""
    [(folder, _, spec, clone)] = [
        made for made in vcenter.imported.clones if made[1] == name
    ]
    return folder, spec, clone


def only_app01(cfg) -> dict:
    """One VM, for the tests whose question is about a single clone."""
    cfg["vms"] = [cfg["vms"][0]]
    return cfg


def full_clones(cfg) -> dict:
    cfg["target"]["vsphere"]["clone"] = "full"
    return cfg


# -- the template the clones come from ------------------------------------


def test_the_image_is_imported_and_marked_when_the_vcenter_has_none(
    vsphere_cfg, vcenter, prepared, http
):
    """The whole shared half, in one run: the lease import, the marker, the
    snapshot a linked clone needs, and the mark."""
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert vcenter.content.ovfManager.specs[0][3].entityName == TEMPLATE
    assert vcenter.imported.props["config.template"] is True
    assert '"name":"golden.qcow2"' in vcenter.imported.props["config.annotation"]
    assert vcenter.imported.snapshots[0][0] == create_mod.SNAPSHOT_NAME


def test_an_existing_template_is_cloned_from_rather_than_imported(
    vsphere_cfg, vcenter, prepared, http
):
    """The reason a second deploy against the same vCenter is cheap: no
    conversion, no lease, and no bytes but the seed ISOs."""
    deployed(only_app01(vsphere_cfg), vcenter, already_there(vcenter, prepared))
    assert vcenter.content.ovfManager.specs == []
    assert [call["url"].split("/folder/")[1].split("?")[0] for call in http.calls] == [
        "vcows/app01/app01-seed.iso"
    ]
    assert cloned(vcenter)[2].props["name"] == "app01"


def test_the_import_knob_picks_the_datastore_path(vsphere_cfg, vcenter, prepared, http):
    """The other half of #308's `import` knob, from the config an operator flips
    rather than from a rebuild."""
    vsphere_cfg["target"]["vsphere"]["import"] = "datastore"
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert vcenter.content.ovfManager.specs == []
    assert vcenter.folder.creates[0][0].name == TEMPLATE


def test_a_template_that_stopped_resolving_is_an_error_rather_than_an_import(
    vsphere_cfg, vcenter, prepared, http
):
    """`preflight` saw it, so `prepare` converted nothing and there is no VMDK on
    this machine to import instead."""
    prepared = already_there(vcenter, prepared)
    vcenter.imported.destroyed = True
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert str(bad.value) == (
        f"the template {TEMPLATE!r} is no longer on this vCenter; preflight "
        f"found it when this run started"
    )


def test_a_failed_import_names_the_template_it_was_making(
    vsphere_cfg, prepared, ovf, http
):
    """`run.json`'s `error` field is what an air-gapped site ships back, and a
    vCenter fault on its own says nothing about which object was being made."""
    vcenter = Vcenter(ovf_error=[vim.fault.OvfUnsupportedType(msg="not supported")])
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert str(bad.value).startswith(f"could not create template {TEMPLATE}: ")


# -- the seed ISO ---------------------------------------------------------


def test_each_seed_iso_is_uploaded_under_its_own_vm_s_folder(
    vsphere_cfg, vcenter, prepared, http
):
    """One folder per VM under `vcows/`, which is what `preflight._orphan_seeds`
    searches and what `destroy` deletes from."""
    deployed(vsphere_cfg, vcenter, already_there(vcenter, prepared))
    assert [call["url"].split("/folder/")[1].split("?")[0] for call in http.calls] == [
        "vcows/app01/app01-seed.iso",
        "vcows/app02/app02-seed.iso",
    ]
    assert http.calls[0]["body"] == b"app01 seed iso"


def test_a_failed_seed_upload_names_the_seed_and_carries_what_was_made(
    vsphere_cfg, vcenter, prepared
):
    """Nothing rolls back, so the record of the VMs already running is what the
    exception has to carry out -- `cli._deploy` reads it back with `getattr`."""
    prepared = already_there(vcenter, prepared)
    with pytest.MonkeyPatch.context() as patch:
        http = FakeHttp().install(patch)

        def refuse_the_second(url, **kw):
            if "app02" in url:
                return FakeHttp(status_code=403, text="denied").put(url, **kw)
            return http.put(url, **kw)

        patch.setattr(create_mod.requests, "put", refuse_the_second)
        with pytest.raises(api.VsphereApiError) as bad:
            deployed(vsphere_cfg, vcenter, prepared)
    assert str(bad.value).startswith("could not create seed app02-seed.iso: ")
    # `carrying` sets the attribute on whatever exception leaves the block, so
    # no type declares it -- the Proxmox test reads it through the same local.
    carrier: Any = bad.value
    assert list(carrier.created) == ["app01"]


# -- the clone ------------------------------------------------------------


def test_the_clone_is_a_delta_over_the_template_s_own_snapshot(
    vsphere_cfg, vcenter, prepared, http
):
    """A5, and the whole reason the bytes move once. The snapshot has to be the
    template's current one: the fake refuses a linked spec that names any
    other, because vCenter has nothing to overlay the delta disk on."""
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    assert spec.location.diskMoveType == "createNewChildDiskBacking"
    assert spec.snapshot is vcenter.imported.mo.snapshot.currentSnapshot
    # Powered on by a call of its own, so the disk can be grown in between.
    assert spec.powerOn is False
    assert spec.template is False


def test_a_full_clone_names_no_disk_move_type_and_no_snapshot(
    vsphere_cfg, vcenter, prepared, http
):
    """`clone: full` copies the disk. Naming the snapshot as well would make it
    a clone of that point in time rather than of the template as it is."""
    deployed(only_app01(full_clones(vsphere_cfg)), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    assert spec.location.diskMoveType is None
    assert spec.snapshot is None


def test_a_template_with_no_snapshot_fails_the_clone_rather_than_going_full(
    vsphere_cfg, vcenter, prepared, http
):
    """What a `make_template` that marked before it snapshotted leaves behind.
    Falling back to a full clone would silently copy a multi-GB disk per VM, so
    the refusal is vCenter's and this backend passes it on naming the VM."""
    prepared = already_there(vcenter, prepared)
    vcenter.imported.mo.snapshot = None
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert str(bad.value) == (
        "could not create vm app01: clone: the task ended as error "
        "(a linked clone is an overlay on a snapshot of its source)"
    )


def test_the_clone_lands_in_the_configured_placement(
    vsphere_cfg, vcenter, prepared, http
):
    """The same four names the import resolved. A cluster placement means the
    cluster's root pool and no host, which is vCenter's cue to place it."""
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    folder, spec, _ = cloned(vcenter)
    assert folder is vcenter.folder.mo
    assert spec.location.pool is vcenter.pool.mo
    assert spec.location.datastore is vcenter.datastore
    assert spec.location.host is None


def test_a_host_placement_puts_the_clone_on_that_host(
    vsphere_cfg, vcenter, prepared, http
):
    """The clone follows the import: a template on one host and clones on
    another is a delta disk reaching across a datastore boundary."""
    del vsphere_cfg["target"]["vsphere"]["cluster"]
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    deployed(only_app01(vsphere_cfg), vcenter, already_there(vcenter, prepared))
    _, spec, _ = cloned(vcenter)
    assert spec.location.host is vcenter.host
    assert spec.location.pool is vcenter.host_pool.mo


def test_the_clone_carries_its_own_marker_and_its_own_size(
    vsphere_cfg, vcenter, prepared, http
):
    """On the `CloneSpec` rather than reconfigured afterwards: a VM that
    appeared carrying the template's annotation is one another run's `preflight`
    reads as the golden image, and the window is as long as a disk copy."""
    deployed(vsphere_cfg, vcenter, already_there(vcenter, prepared))
    _, spec, clone = cloned(vcenter, "app02")
    assert (spec.config.numCPUs, spec.config.memoryMB) == (4, 8192)
    assert '"name":"app02"' in spec.config.annotation
    assert clone.props["config.annotation"] == spec.config.annotation


def test_the_firmware_the_config_named_is_the_clone_s(
    vsphere_cfg, vcenter, prepared, http
):
    """A clone inherits the template's, which `make_template` set to efi. So a
    config saying `firmware: bios` would otherwise be a key the schema accepts
    and nothing reads -- the other two backends both carry theirs per VM."""
    vsphere_cfg["vms"][0]["firmware"] = "bios"
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert cloned(vcenter)[1].config.firmware == "bios"


def test_the_seed_iso_is_in_a_drive_the_guest_finds_connected(
    vsphere_cfg, vcenter, prepared, http
):
    """A9. A CD-ROM vCenter attaches but leaves disconnected is a cloud-init
    that never runs, and the VM comes up with no addresses and no keys."""
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    [drive] = [
        change
        for change in spec.config.deviceChange
        if isinstance(change.device, vim.vm.device.VirtualCdrom)
    ]
    assert drive.operation == "add"
    assert drive.device.backing.fileName == "[ds-a] vcows/app01/app01-seed.iso"
    assert drive.device.connectable.startConnected is True
    # And now, because the VM is powered on in the same call chain: only
    # `startConnected` would leave that first boot without the drive.
    assert drive.device.connectable.connected is True
    # Not the guest's to eject, which pyvmomi's default already gives.
    assert drive.device.connectable.allowGuestControl is False
    # ide0 unit 0, which is where a VM without a CD-ROM has the free slot.
    assert drive.device.controllerKey == create_mod.IDE_CONTROLLER_KEY
    assert drive.device.unitNumber == 0
    assert drive.device.key == create_mod.CDROM_KEY


def test_every_nic_is_a_manual_mac_on_the_resolved_port_group(
    vsphere_cfg, vcenter, prepared, http
):
    """cloud-init matches an interface by MAC, so `addressType` is what makes
    the derived address mean anything: without it vCenter generates its own and
    the seed ISO configures nothing."""
    vsphere_cfg["vms"][0]["nics"].append(
        {"ip_cidr": "10.0.0.5/24", "gateway": "10.0.0.1"}
    )
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    nics = [
        change.device
        for change in spec.config.deviceChange
        if isinstance(change.device, vim.vm.device.VirtualEthernetCard)
    ]
    assert [nic.addressType for nic in nics] == ["manual", "manual"]
    assert [nic.macAddress for nic in nics] == [
        "52:54:00:be:a8:60",
        "52:54:00:d3:8b:f5",
    ]
    assert [nic.backing.deviceName for nic in nics] == ["pg-vcows", "pg-vcows"]
    # Connected at the first boot, like the drive: a NIC vCenter attaches but
    # leaves down is a guest cloud-init configures and cannot reach.
    assert [nic.connectable.startConnected for nic in nics] == [True, True]
    # Counting *down* from `NIC_KEY`, and negative: vCenter numbers the devices
    # itself and reads these only as references inside this one spec. Counting
    # up would put the second NIC on the CD-ROM's key.
    assert [nic.key for nic in nics] == [create_mod.NIC_KEY, create_mod.NIC_KEY - 1]
    assert create_mod.CDROM_KEY not in {nic.key for nic in nics}
    assert max(nic.key for nic in nics) < 0


@pytest.mark.parametrize(
    ("model", "device"),
    [
        ("vmxnet3", vim.vm.device.VirtualVmxnet3),
        ("e1000", vim.vm.device.VirtualE1000),
        ("e1000e", vim.vm.device.VirtualE1000e),
    ],
)
def test_the_adapter_class_is_the_one_the_config_named(
    vsphere_cfg, vcenter, prepared, http, model, device
):
    """All three the schema offers, because each is a separate device class and
    a name the table spells wrongly is a KeyError mid-apply rather than a config
    error -- vmxnet3 needs the guest driver the golden image ships, and e1000 is
    what an image without it has to fall back to."""
    vsphere_cfg["vms"][0]["nics"][0]["model"] = model
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    assert isinstance(spec.config.deviceChange[1].device, device)


def test_the_port_group_resolves_inside_the_configured_datacenter(
    vsphere_cfg, prepared, http
):
    """Two datacenters on one vCenter may each hold a port group of the same
    name, and a NIC on the wrong one is a VM with no route off its host. The
    decoys are listed first, so a lookup that lost its root finds one of them."""
    vcenter = Vcenter(decoys=True)
    deployed(only_app01(vsphere_cfg), vcenter, already_there(vcenter, prepared))
    _, spec, _ = cloned(vcenter)
    assert spec.config.deviceChange[1].device.backing.deviceName == "pg-vcows"
    assert [call for call in vcenter.content.calls if call[0] == "CreateContainerView"][
        -1
    ] == ("CreateContainerView", "dc-a", ("vim.Network",))


def test_each_nic_gets_its_own_check_mac_address_override(
    vsphere_cfg, vcenter, prepared, http
):
    """KB 423046, always set and never a knob: a `52:54:00` address is outside
    vCenter's own range and this is what stops it objecting."""
    vsphere_cfg["vms"][0]["nics"].append(
        {"ip_cidr": "10.0.0.5/24", "gateway": "10.0.0.1"}
    )
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    _, spec, _ = cloned(vcenter)
    assert [(o.key, o.value) for o in spec.config.extraConfig] == [
        ("ethernet0.checkMACAddress", "FALSE"),
        ("ethernet1.checkMACAddress", "FALSE"),
    ]


def test_a_port_group_that_stopped_resolving_is_an_error_naming_the_field(
    vsphere_cfg, vcenter, prepared, http
):
    """Preflight resolved it and reported a miss as a Problem. Reaching one here
    means it went away between the two passes, and nothing above is still
    gathering problems."""
    vsphere_cfg["target"]["vsphere"]["network"] = "pg-gone"
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(only_app01(vsphere_cfg), vcenter, already_there(vcenter, prepared))
    assert "target.vsphere.network names 'pg-gone'" in str(bad.value)
    assert vcenter.imported.clones == []


def test_every_vm_is_powered_on_once_it_is_whole(vsphere_cfg, vcenter, prepared, http):
    """After the clone and after any growth, not as part of the clone: a VM that
    booted at the template's size would have to be rebooted to see the rest."""
    deployed(vsphere_cfg, vcenter, already_there(vcenter, prepared))
    _, _, clone = cloned(vcenter, "app01")
    assert clone.props["runtime.powerState"] == "poweredOn"
    watched = ("CloneVM_Task", "PowerOnVM_Task")
    assert [call for call in vcenter.content.calls if call[0] in watched] == [
        ("CloneVM_Task", TEMPLATE, "app01"),
        ("PowerOnVM_Task", "app01"),
        ("CloneVM_Task", TEMPLATE, "app02"),
        ("PowerOnVM_Task", "app02"),
    ]


# -- growing a full clone's disk ------------------------------------------


def test_a_full_clone_s_disk_is_grown_to_the_configured_size(
    vsphere_cfg, vcenter, prepared, http
):
    """A clone comes back the size of the template, which is the golden image's
    virtual size, so `disk_gb` is only honoured by growing it afterwards."""
    deployed(only_app01(full_clones(vsphere_cfg)), vcenter, prepared)
    _, _, clone = cloned(vcenter)
    [spec] = clone.reconfigured
    [change] = spec.deviceChange
    assert change.operation == "edit"
    assert change.device.key == TEMPLATE_DISK_KEY
    assert change.device.capacityInKB == 40 * 1024**2


def test_a_disk_already_the_configured_size_is_left_alone(
    vsphere_cfg, vcenter, prepared, http
):
    """`imagecheck.check_disk_capacity` refuses a `disk_gb` below the image's
    virtual size, so equal is the ordinary case -- and vCenter refuses a resize
    that is not a growth."""
    vsphere_cfg["vms"][0]["disk_gb"] = CAPACITY // 1024**3
    deployed(only_app01(full_clones(vsphere_cfg)), vcenter, prepared)
    assert cloned(vcenter)[2].reconfigured == []


def test_a_linked_clone_is_never_grown(vsphere_cfg, vcenter, prepared, http):
    """A delta disk cannot be extended, which is why `schema` refuses a
    `disk_gb` above the image's virtual size on this path at load time. Asking
    anyway would fail mid-apply, with two VMs already running."""
    assert vsphere_cfg["vms"][0]["disk_gb"] == 40
    deployed(only_app01(vsphere_cfg), vcenter, prepared)
    assert cloned(vcenter)[2].reconfigured == []


def test_a_clone_with_no_disk_is_refused_rather_than_left_at_the_wrong_size(
    vsphere_cfg, prepared, http
):
    """Returning quietly would deploy every VM at the template's size with
    nothing saying so."""
    vcenter = Vcenter(entity=FakeVm(TEMPLATE))
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(only_app01(full_clones(vsphere_cfg)), vcenter, prepared)
    assert str(bad.value) == (
        "could not create vm app01: the clone has no virtual disk, so nothing "
        "could be grown to 40 GiB"
    )


# -- the inventory --------------------------------------------------------


def test_the_inventory_is_keyed_by_logical_name_with_the_seed_it_made(
    vsphere_cfg, vcenter, prepared, http
):
    """`disks` is what `destroy._delete_seed` walks, and the configured address
    is the config's rather than a lease -- the tool never asks vCenter what a
    guest came up on."""
    made = deployed(vsphere_cfg, vcenter, already_there(vcenter, prepared))
    assert made == {
        "app01": {
            "name": "app01",
            "uuid": "uuid-app01",
            "configured_address": "192.168.122.60",
            "disks": ["[ds-a] vcows/app01/app01-seed.iso"],
        },
        "app02": {
            "name": "app02",
            "uuid": "uuid-app02",
            "configured_address": "192.168.122.61",
            "disks": ["[ds-a] vcows/app02/app02-seed.iso"],
        },
    }


def test_a_failed_clone_carries_the_vms_already_made(
    vsphere_cfg, vcenter, prepared, http
):
    """Nothing rolls back. Without this a failure on the second VM loses every
    record of the first, which is running and which an operator cannot
    re-derive."""
    prepared = already_there(vcenter, prepared)
    vcenter.imported.clone_error = vim.fault.NoDiskSpace(msg="the datastore is full")
    with pytest.raises(api.VsphereApiError) as bad:
        deployed(vsphere_cfg, vcenter, prepared)
    assert str(bad.value).startswith("could not create vm app01: ")
    carrier: Any = bad.value
    assert carrier.created == {}


# -- the uuid, which is the only identity destroy is given ----------------


def uuid_of(vcenter, **props) -> str:
    """`_uuid` against one VM on this vCenter, with its uuid properties as a
    test set them. Driven directly because the fake's VMs all carry the summary
    path, and the question here is which of the two spellings is read."""
    vm = vcenter.content.add(FakeVm("app01"))
    vm.props.pop("summary.config.uuid")
    vm.props.update(props)
    return create_mod._uuid(vcenter.session, vm.mo, "app01")


def test_the_summary_path_is_what_the_uuid_is_read_from(vcenter):
    """Through the property collector, not `vm.summary.config.uuid`: fetching
    the whole `summary` first raises `AttributeError` under pyVmomi 9."""
    assert uuid_of(vcenter, **{"summary.config.uuid": "5001"}) == "5001"


def test_the_config_path_answers_when_the_summary_one_does_not(vcenter):
    """The same value by another name, and an unset property is absent from the
    answer rather than None -- which is what a vCenter returns."""
    assert uuid_of(vcenter, **{"config.uuid": "5002"}) == "5002"


def test_a_vm_with_no_uuid_at_all_is_refused(vcenter):
    """An inventory record carrying nothing here names a VM `vcows destroy`
    could never match, and it does not match on the name."""
    with pytest.raises(api.VsphereApiError) as bad:
        uuid_of(vcenter)
    assert str(bad.value) == (
        "vCenter gave app01 no uuid, so nothing could identify it again; "
        "`vcows destroy` matches a VM on it"
    )
