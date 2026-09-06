"""The apply's shared half: the datastore upload, the two imports, the template.

The per-VM clone and the ``create`` that orders these calls are the next chunk's,
so every test here drives one function directly rather than going through
``VsphereBackend.create``.

Three of the questions come from the vcsim spike rather than from the API docs,
and they are the ones a fake is worth having for: the lease is read before it is
completed and never after, the ``*``-to-host substitution is harmless when there
is no ``*``, and the lease hears about the upload while the upload is happening.
``tests/fake_vsphere.py``'s ``FakeLease`` faults on any read after
``HttpNfcLeaseComplete``, which is what vcsim does, so the first of those fails
here rather than against a vCenter.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

import pytest
from pyVmomi import vim, vmodl

from orchestrator.backends.vsphere import api
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
        self.imported = entity if entity is not None else FakeVm(TEMPLATE)
        self.content = FakeContent()
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
