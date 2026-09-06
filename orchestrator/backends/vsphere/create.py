"""The apply, through the vCenter API and vCenter's own HTTP endpoints.

What is here is the *shared image*: the golden VMDK becomes one marked template
VM, once, and every deploy after that clones it. The per-VM half -- the seed ISO,
the clone, the power on -- is the next chunk's, and so is the ``create`` that
orders these calls; this module is the calls themselves.

**Two import paths, and the ``import`` knob picks between them.** ``import_ovf``
is the default and moves a ``streamOptimized`` VMDK through an ``ImportVApp``
lease, the way ``govc import.vmdk`` does. ``import_flat`` is the fallback and
PUTs a ``monolithicFlat`` descriptor and its ``-flat`` extent to the datastore,
then attaches the file with ``CreateVM_Task``. Both end in a VM that
``make_template`` reconfigures, snapshots and marks. Neither converts anything:
``convert.to_vmdk`` already wrote the format the knob asked for, in ``prepare``.

**Nothing is rolled back**, the rule the other two backends' creates follow.
``_made`` names the resource on the exception and re-raises, and a lease that
was left holding a half-imported VM is vCenter's to expire -- aborting it would
be the rollback this tool deliberately does not do, and ``preflight`` is what
sees the leftovers on the next run.

**No ``pyVmomi`` import at module scope**, as everywhere else in this package.
``requests`` is not an SDK and is imported normally: the Proxmox backend already
depends on it, and nothing about a machine that will never speak to a vCenter
makes importing it a cost.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import requests

from . import api
from .preflight import SEED_FOLDER

log = logging.getLogger(__name__)

#: How long one HTTP call to vCenter may go quiet for. Not a ceiling on an
#: upload: ``requests`` applies this between two socket operations, so a
#: multi-GB PUT that keeps moving never reaches it and one that stalls does.
HTTP_TIMEOUT = 60

#: How far the upload has to get before the lease is told again. vCenter expires
#: a lease that goes quiet and a whole golden image is minutes of one POST, so
#: this is what keeps it alive; small enough to speak on any image worth
#: importing, large enough not to make a SOAP call per read.
PROGRESS_STEP = 5

#: The snapshot every linked clone is an overlay on. One name, because the clone
#: chunk has to find it again on a template an earlier run made.
SNAPSHOT_NAME = "vcows-base"

#: The shell the template is imported as: one CPU, 512 MiB and one disk. Every
#: clone overrides ``numCPUs`` and ``memoryMB`` from its own config, so these are
#: never what a VM runs with -- they are what the OVF has to declare to be a
#: valid OVF at all.
OVF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
 xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <References>
    <File ovf:href="disk.vmdk" ovf:id="file1" ovf:size="{size}"/>
  </References>
  <DiskSection>
    <Info>Virtual Disk Information</Info>
    <Disk ovf:capacity="{capacity}" ovf:capacityAllocationUnits="byte"
     ovf:diskId="vmdisk1" ovf:fileRef="file1"
     ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <VirtualSystem ovf:id="{name}">
    <Info>A virtual machine</Info>
    <Name>{name}</Name>
    <OperatingSystemSection ovf:id="107">
      <Info>The kind of installed guest operating system</Info>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual Hardware</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemType>vmx-13</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits>
        <rasd:Description>Number of Virtual CPUs</rasd:Description>
        <rasd:ElementName>1 virtual CPU(s)</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>1</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Description>Memory Size</rasd:Description>
        <rasd:ElementName>512MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>512</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Address>0</rasd:Address>
        <rasd:ElementName>SCSI Controller 0</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>lsilogic</rasd:ResourceSubType>
        <rasd:ResourceType>6</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:ElementName>Hard Disk 1</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/vmdisk1</rasd:HostResource>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


@contextmanager
def _made(what: str) -> Iterator[None]:
    """One line per created resource, naming it and what it cost.

    **The name rides on the exception, not only on the log line.** The log is
    stderr; ``run.json``'s ``error`` field is what an air-gapped site ships back,
    and ``cli._guard`` fills it from the exception's text. Without this a failed
    task reads as a vCenter fault and says nothing about which of a run's
    objects was being made. **This is the only thing that names the resource**,
    which is why the ``what`` handed to ``api.wait`` inside is the bare step.

    Copied from the Proxmox backend rather than lifted into ``base.py`` with the
    two of them, and the copy is the smaller change: all three bodies differ.
    This one reads ``.msg`` off the exception, the Proxmox one interpolates the
    exception itself, the libvirt one rewrites ``args`` in place instead of
    raising its own type -- so a shared version would take the type to raise
    *and* the way to render what it caught as parameters, and would edit two
    working backends to pass them.

    ``.msg`` when there is one, for the reason ``api.wait`` uses it: pyvmomi
    renders a fault as its whole field list, which buries the one sentence
    vCenter wrote. What else reaches here -- an ``OSError`` from opening the
    converted image -- already reads as a sentence and names its own file.
    """
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        message = f"could not create {what}: {getattr(exc, 'msg', exc)}"
        log.error("%s", message)
        raise api.VsphereApiError(message) from exc
    log.info("created %s in %.1fs", what, time.monotonic() - started)


# -- the datastore's HTTP endpoint ---------------------------------------


def upload(cfg: dict, session: api.Session, path: str | Path, ds_path: str) -> str:
    """PUT one local file onto the datastore, and answer with its vCenter path.

    Not an SDK call: vCenter serves ``/folder/<path>?dcPath=&dsName=`` over plain
    HTTPS and proxies the bytes to the datastore, and the SOAP session cookie is
    the only thing that authorises it. pyvmomi has no helper for it.

    ``ds_path`` is the path *inside* the datastore -- ``vcows/app01/x.iso`` --
    and what comes back is the ``[datastore] vcows/app01/x.iso`` spelling every
    later SDK call wants, so no caller builds that string itself.

    The file object goes to ``requests`` rather than its bytes: a golden image is
    multi-GB and reading one into memory to send it is how the Proxmox backend's
    upload used to raise ``OverflowError``.
    """
    target = cfg["target"]["vsphere"]
    url = f"{target['endpoint'].rstrip('/')}/folder/{quote(ds_path)}?" + urlencode(
        {"dcPath": target["datacenter"], "dsName": target["datastore"]}
    )
    # Before the PUT, not after: the request answers nothing until the last byte
    # is in, so this line and the size on it are all an operator has while a
    # multi-GB file goes over the wire.
    log.info("uploading %s (%s MiB)", ds_path, os.path.getsize(path) // 1024**2)
    with open(path, "rb") as fh:
        answer = requests.put(
            url,
            data=fh,
            cookies=_cookie(session),
            verify=session.verify,
            timeout=HTTP_TIMEOUT,
        )
    _refuse(answer, f"upload {ds_path}")
    return f"[{target['datastore']}] {ds_path}"


def _cookie(session: api.Session) -> dict[str, str]:
    """``vmware_soap_session`` out of the ``Set-Cookie``-shaped string pyvmomi
    keeps on the stub.

    Split rather than sent whole: the stub's copy carries ``Path``, ``HttpOnly``
    and quotes around the value, and what a request has to send is the one pair.
    """
    name, _, value = session.cookie.split(";")[0].partition("=")
    return {name.strip(): value.strip().strip('"')}


def _refuse(answer: Any, what: str) -> None:
    """A 4xx or 5xx from vCenter's HTTP endpoints, as this backend's error.

    ``raise_for_status`` raises ``requests``' own error, which names the status
    and nothing vCenter said. The body is where a datastore says *why* it
    refused a write, and it is truncated because a fault page is HTML.
    """
    if answer.status_code >= 400:
        raise api.VsphereApiError(
            f"{what}: vCenter answered HTTP {answer.status_code} "
            f"({answer.text[:200].strip()})"
        )


# -- the import ----------------------------------------------------------


def import_ovf(
    cfg: dict, session: api.Session, vmdk: str | Path, name: str, capacity: int
) -> Any:
    """Import the converted image as a VM, through an ``ImportVApp`` lease.

    The sequence ``govc import.vmdk`` uses, and the one the vcsim spike measured
    end to end: a one-disk OVF descriptor, ``CreateImportSpec``, ``ImportVApp``
    for a lease, the file POSTed to the lease's device URL, then
    ``HttpNfcLeaseComplete``.

    ``capacity`` is the golden image's virtual size, which ``prepare`` read off
    the qcow2 header; the file's own size is what the descriptor declares as the
    transfer length, and on a ``streamOptimized`` VMDK the two differ by however
    well the image compressed.

    **``info.entity`` is read before ``HttpNfcLeaseComplete``**, because that
    call deletes the lease: reading anything off it afterwards raises
    ``ManagedObjectNotFound``. Every read is a leaf -- ``lease.info.entity``,
    never ``lease.info`` bound to a local -- for the reason ``api.wait`` reads
    ``task.info.state`` that way.
    """
    from pyVmomi import vim

    datastore, folder, pool, host = _placement(cfg, session)
    size = os.stat(vmdk).st_size
    log.info("importing %s as %s (%s MiB)", vmdk, name, size // 1024**2)
    spec = session.content.ovfManager.CreateImportSpec(
        OVF_XML.format(name=name, capacity=capacity, size=size),
        pool,
        datastore,
        vim.OvfManager.CreateImportSpecParams(entityName=name, diskProvisioning="thin"),
    )
    if spec.error:
        raise api.VsphereApiError(
            f"vCenter refused the OVF descriptor for {name}: "
            + "; ".join(getattr(fault, "msg", str(fault)) for fault in spec.error)
        )
    lease = pool.ImportVApp(spec=spec.importSpec, folder=folder, host=host)
    _ready(lease, name)
    _post(_device_url(cfg, lease, name), vmdk, size, lease, session.verify)
    lease.HttpNfcLeaseProgress(100)
    imported = lease.info.entity
    lease.HttpNfcLeaseComplete()
    return imported


def _ready(lease: Any, name: str) -> None:
    """Block until the lease is ready to take bytes, and refuse anything else.

    ``ImportVApp`` answers before vCenter has the lease open, so the device URL
    is not there yet. The ceiling and the interval are ``api.wait``'s, because a
    wait that is right for a clone is right for this.
    """
    from pyVmomi import vim

    deadline = time.monotonic() + api.TASK_TIMEOUT
    while lease.state == vim.HttpNfcLease.State.initializing:
        if time.monotonic() >= deadline:
            raise api.VsphereApiError(
                f"import {name}: the lease was still initializing after "
                f"{api.TASK_TIMEOUT}s"
            )
        time.sleep(api.POLL_INTERVAL)
    if lease.state != vim.HttpNfcLease.State.ready:
        raise api.VsphereApiError(
            f"import {name}: the lease came back {lease.state} "
            f"({getattr(lease.error, 'msg', lease.error)})"
        )


def _device_url(cfg: dict, lease: Any, name: str) -> str:
    """Where the lease wants the disk POSTed, as a URL this side can reach.

    One disk in the descriptor means one device URL, so the first is it.

    **The substitution is written to be a no-op.** A4 says vCenter puts ``*``
    where the host belongs, meaning "whichever address you reached me on", and
    vcsim answers with its own listen address instead. Neither is asserted: what
    is here replaces a ``*`` if there is one and leaves the URL alone if there is
    not.
    """
    urls = lease.info.deviceUrl or ()
    if not urls:
        raise api.VsphereApiError(f"import {name}: the lease named no device URL")
    endpoint = urlsplit(cfg["target"]["vsphere"]["endpoint"])
    return (
        urls[0]
        .url.replace("://*/", f"://{endpoint.netloc}/")
        .replace("://*:", f"://{endpoint.hostname}:")
    )


def _post(
    url: str, vmdk: str | Path, size: int, lease: Any, verify: bool | str
) -> None:
    """POST the VMDK to the lease, telling the lease how far it has got."""
    with open(vmdk, "rb") as fh:
        answer = requests.post(
            url,
            data=_Reporting(fh, size, lease),
            headers={"Content-Type": "application/x-vnd.vmware-streamVmdk"},
            verify=verify,
            timeout=HTTP_TIMEOUT,
        )
    _refuse(answer, f"POST {os.path.basename(str(vmdk))} to the lease")


class _Reporting:
    """The VMDK, wrapped so that reading it tells the lease how it is going.

    ``HttpNfcLeaseProgress`` has to be called *during* the transfer -- vCenter
    expires a lease that goes quiet, and the whole image moves in one POST -- so
    there is nowhere else to call it from. ``__len__`` is what ``requests`` reads
    the ``Content-Length`` off; without it the body would go chunked, which the
    NFC endpoint does not take.
    """

    def __init__(self, fh: Any, size: int, lease: Any):
        self._fh = fh
        self._size = size
        self._lease = lease
        self._sent = 0
        self._told = 0

    def __len__(self) -> int:
        return self._size

    def read(self, amount: int = -1) -> bytes:
        chunk = self._fh.read(amount)
        self._sent += len(chunk)
        # `max` guards nothing but a zero-byte file, which is a conversion that
        # failed silently; the last percent is the completed POST's to report.
        percent = min(99, self._sent * 100 // max(self._size, 1))
        if percent - self._told >= PROGRESS_STEP:
            self._lease.HttpNfcLeaseProgress(percent)
            self._told = percent
        return chunk


def import_flat(
    cfg: dict, session: api.Session, vmdk: str | Path, name: str, capacity: int
) -> Any:
    """Import the converted image by putting it on the datastore, then attaching
    it. The ``import: datastore`` knob's path.

    A ``monolithicFlat`` conversion is a descriptor and a ``-flat`` extent beside
    it, and the descriptor names the extent by file name alone -- so both go into
    ``[ds] vcows/<name>/`` under the names ``qemu-img`` gave them, and the
    reference resolves on the far side.

    Slower than the lease by the whole compression ratio: this uploads the
    image's full virtual size. It is the fallback for a vCenter whose lease URL
    cannot be reached or which will not take qemu-img's ``streamOptimized``
    header, and both of those are first-contact questions.
    """
    _, folder, pool, host = _placement(cfg, session)
    descriptor = Path(vmdk)
    extent = descriptor.with_name(f"{descriptor.stem}-flat.vmdk")
    folder_path = f"{SEED_FOLDER}/{name}"
    disk_file = upload(cfg, session, descriptor, f"{folder_path}/{descriptor.name}")
    upload(cfg, session, extent, f"{folder_path}/{extent.name}")
    return api.wait(
        folder.CreateVM_Task(
            config=_config_spec(cfg, name, disk_file, capacity), pool=pool, host=host
        ),
        "create",
    )


def _config_spec(cfg: dict, name: str, disk_file: str, capacity: int) -> Any:
    """One controller and one disk, attaching a file that is already there.

    No ``fileOperation`` on the disk: ``create`` would have vCenter make a new
    empty disk over the one just uploaded. The shell matches what ``OVF_XML``
    declares, for the same reason -- every clone overrides it.
    """
    from pyVmomi import vim

    controller = vim.vm.device.VirtualLsiLogicController(
        key=-100, busNumber=0, sharedBus="noSharing"
    )
    disk = vim.vm.device.VirtualDisk(
        key=-101,
        unitNumber=0,
        controllerKey=-100,
        capacityInKB=capacity // 1024,
        backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
            fileName=disk_file, diskMode="persistent", thinProvisioned=True
        ),
    )
    return vim.vm.ConfigSpec(
        name=name,
        numCPUs=1,
        memoryMB=512,
        guestId="otherGuest64",
        files=vim.vm.FileInfo(vmPathName=f"[{cfg['target']['vsphere']['datastore']}]"),
        deviceChange=[
            vim.vm.device.VirtualDeviceSpec(operation="add", device=controller),
            vim.vm.device.VirtualDeviceSpec(operation="add", device=disk),
        ],
    )


def _placement(cfg: dict, session: api.Session) -> tuple[Any, Any, Any, Any]:
    """The datastore, folder, resource pool and host an import lands in.

    Resolved by name here rather than carried from ``preflight``, for the reason
    ``api.Session`` gives: a name that does not resolve has to be a ``Problem``
    there, and only ``preflight`` makes those. Reaching a miss *here* means the
    name stopped resolving between the two passes, which is an error rather than
    a problem to collect.

    ``folder`` and ``resource_pool`` are optional and the schema refuses both
    ``cluster`` and ``host`` together, so what is left is: the datacenter's own
    VM folder, and the pool belonging to whichever of the two was named.
    """
    from pyVmomi import vim

    target = cfg["target"]["vsphere"]
    datacenter = _resolve(session, vim.Datacenter, target["datacenter"], "datacenter")
    datastore = _resolve(
        session, vim.Datastore, target["datastore"], "datastore", datacenter
    )
    folder = (
        datacenter.vmFolder
        if target.get("folder") is None
        else _resolve(session, vim.Folder, target["folder"], "folder", datacenter)
    )
    host = (
        None
        if target.get("host") is None
        else _resolve(session, vim.HostSystem, target["host"], "host", datacenter)
    )
    if target.get("resource_pool") is not None:
        pool = _resolve(
            session,
            vim.ResourcePool,
            target["resource_pool"],
            "resource_pool",
            datacenter,
        )
    elif host is not None:
        # A host names its own compute resource's root pool. `parent` is the
        # ComputeResource, not the cluster, which is what an unclustered host
        # has and what `CreateVM_Task` wants.
        pool = host.parent.resourcePool
    else:
        pool = _resolve(
            session,
            vim.ClusterComputeResource,
            target["cluster"],
            "cluster",
            datacenter,
        ).resourcePool
    return datastore, folder, pool, host


def _resolve(
    session: api.Session, kind: Any, name: str, field: str, root: Any = None
) -> Any:
    what = api.find_by_name(session.content, kind, name, root=root)
    if what is None:
        raise api.VsphereApiError(
            f"target.vsphere.{field} names {name!r}, which this vCenter no "
            f"longer holds; it resolved when preflight ran"
        )
    return what


# -- the template --------------------------------------------------------


def make_template(vm: Any, marker: str) -> None:
    """Turn the imported VM into the template every clone comes from.

    Three calls and the order is the documented one: the marker and the firmware
    first, because a template cannot be reconfigured; then the snapshot, because
    a linked clone is an overlay on a snapshot and a template's has to exist
    before anything clones it; then ``MarkAsTemplate``, which is not a task and
    returns nothing.

    ``efiSecureBootEnabled`` is set to False rather than left out. Unset means
    off today, and a config that says ``firmware: efi`` is asking for EFI and not
    for secure boot -- the golden images are not signed for it.

    Takes no session: everything here is a call on the VM itself, and the
    resource this belongs to is named by the ``_made`` around the caller.
    """
    from pyVmomi import vim

    api.wait(
        vm.ReconfigVM_Task(
            spec=vim.vm.ConfigSpec(
                annotation=marker,
                firmware="efi",
                bootOptions=vim.vm.BootOptions(efiSecureBootEnabled=False),
            )
        ),
        "reconfigure",
    )
    api.wait(
        vm.CreateSnapshot_Task(
            name=SNAPSHOT_NAME,
            description="The point every linked clone is an overlay on.",
            memory=False,
            quiesce=False,
        ),
        "snapshot",
    )
    vm.MarkAsTemplate()
