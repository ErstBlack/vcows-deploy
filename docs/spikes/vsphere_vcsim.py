#!/usr/bin/env python3
"""C2 -- which of the epic's vSphere calls vcsim actually answers.

Not a test. Nothing collects this (pytest's `testpaths` is `tests`), no gate runs
it, and it starts nothing itself. Start the simulator first, in another shell:

    .tools/bin/vcsim -l 127.0.0.1:8989

then, from the repo root:

    .venv/bin/python docs/spikes/vsphere_vcsim.py

and stop the simulator afterwards. vcsim's inventory lives in the process, so a
run leaves nothing behind and there is no cleanup here.

The script walks the create-then-teardown sequence from the epic (#308, "Design")
in order, records per call whether vcsim implements it and what it returned, and
prints a markdown table. Every step is wrapped: a fault is recorded with its type
and message and the walk carries on, because the point is the whole list rather
than the first fault.

vcsim accepts any username and password by default; `user`/`pass` are what the
epic's smoke gate would use. TLS is vcsim's own self-signed certificate, so
certificate validation is off here exactly as `insecure: true` would set it.
"""
import contextlib
import ssl
import sys
import time
import urllib.parse

import requests
import urllib3
from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim, vmodl

HOST = "127.0.0.1"
PORT = 8989
USER = "user"
PASSWORD = "pass"

# The six paths preflight reads off every VirtualMachine (#308, "Preflight").
PROPS = [
    "name",
    "config.template",
    "config.annotation",
    "config.hardware.device",
    "summary.config.uuid",
    "runtime.powerState",
]

MARKER = '{"v":"0.1.0.0","deployment":"spike","name":"tmpl","id":"vcsim"}'
TEMPLATE_VM = "vcows-spike-tmpl"
LINKED_VM = "vcows-spike-linked"
FULL_VM = "vcows-spike-full"
SEED_DIR = "vcows/spike"
SEED_FILE = "x.iso"
SEED_BYTES = b"vcows spike seed iso, not a real iso\n"
DISK_KB = 1024 * 1024  # 1 GiB, the smallest thing worth calling a disk

# One-disk OVF descriptor, the shape `govc import.vmdk` builds (#308, A3): one
# File, one Disk declared streamOptimized, and a VirtualSystem carrying cpu,
# memory, a SCSI controller and the disk. Capacity is a literal here; in the
# product it comes from `qcow2.virtual_size`.
OVF = """<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
 xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
 xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <References>
    <File ovf:href="disk.vmdk" ovf:id="file1" ovf:size="65536"/>
  </References>
  <DiskSection>
    <Info>Virtual Disk Information</Info>
    <Disk ovf:capacity="1073741824" ovf:capacityAllocationUnits="byte"
     ovf:diskId="vmdisk1" ovf:fileRef="file1"
     ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <VirtualSystem ovf:id="OVFNAME">
    <Info>A virtual machine</Info>
    <Name>OVFNAME</Name>
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
""".replace("OVFNAME", TEMPLATE_VM + "-import")

ROWS = []
STATE = {}
# Set once in main(). Every read below goes through it, and a flat script beats
# threading one more parameter through twenty steps.
CONTENT = None


class Missing(Exception):
    """A prerequisite an earlier step failed to produce. Not a vcsim answer."""


@contextlib.contextmanager
def step(call):
    """Run one call, record one row, never raise.

    `box["note"]` is what the step wants said about the answer; a step that sets
    nothing gets an empty note rather than a fabricated one.
    """
    box = {"note": ""}
    try:
        yield box
    except Missing as exc:
        ROWS.append((call, "not reached", str(exc)))
        print(f"  -- {call}: not reached ({exc})")
    except Exception as exc:  # noqa: BLE001 -- recording the fault is the point
        msg = getattr(exc, "msg", None) or str(exc) or repr(exc)
        ROWS.append((call, "faulted", f"`{type(exc).__name__}`: {msg}"))
        print(f"  !! {call}: {type(exc).__name__}: {msg}")
    else:
        ROWS.append((call, "yes", box["note"]))
        print(f"  ok {call}: {box['note']}")


def need(key):
    value = STATE.get(key)
    if value is None:
        raise Missing(f"no {key} from an earlier step")
    return value


def wait_task(task, timeout=60):
    """Poll to completion; raise the task's fault so `step` records it.

    Leaf paths through the PropertyCollector rather than `task.info`, for the
    same reason `prop` exists: vcsim answers `DeleteDatastoreFile_Task` with a
    `TaskInfo` whose `entity` is the `FileManager` itself, which is not a
    `ManagedEntity`, and pyVmomi refuses to deserialize the whole object. The
    three leaves come back fine.
    """
    deadline = time.monotonic() + timeout
    while True:
        got = props(task, ["info.state", "info.error", "info.result"])
        state = got.get("info.state")
        if state not in (vim.TaskInfo.State.queued, vim.TaskInfo.State.running):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"task still {state} after {timeout}s")
        time.sleep(0.05)
    if state == vim.TaskInfo.State.error:
        raise got["info.error"]
    return got.get("info.result")


def all_of(content, kind, root=None):
    view = content.viewManager.CreateContainerView(
        root or content.rootFolder, [kind], True
    )
    try:
        return list(view.view)
    finally:
        view.Destroy()


def props(obj, paths):
    """Named properties off one object, through the PropertyCollector.

    Not `getattr(obj, path)`. vcsim's answer to a *whole-object* fetch of
    `runtime`, `summary` or `task.info` does not deserialize in pyVmomi 9 -- see
    the `vm.runtime` row in the table -- while the same leaves read through the
    collector come back fine. Every read below goes this way for that reason.
    """
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=obj)],
        propSet=[
            vmodl.query.PropertyCollector.PropertySpec(type=type(obj), pathSet=paths)
        ],
    )
    return {
        got.name: got.val
        for oc in CONTENT.propertyCollector.RetrieveContents([spec])
        for got in oc.propSet
    }


def prop(obj, path):
    return props(obj, [path]).get(path)


def by_name(content, kind, name, root=None):
    """Resolve one named object the way preflight will: a view, then a compare."""
    for obj in all_of(content, kind, root):
        if obj.name == name:
            return obj
    raise vim.fault.NotFound(msg=f"no {kind.__name__} named {name!r}")


def folder_url(dc, ds, path):
    """vCenter's datastore HTTP endpoint for one file (A1)."""
    query = urllib.parse.urlencode({"dcPath": dc.name, "dsName": ds.name})
    return f"https://{HOST}:{PORT}/folder/{path}?{query}"


def cookie_pair(stub):
    """`vmware_soap_session=...` out of the stub's Set-Cookie-shaped string."""
    head = stub.cookie.split(";")[0]
    name, _, value = head.partition("=")
    return name.strip(), value.strip().strip('"')


def disk_of(vm):
    for dev in vm.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk):
            return dev
    return None


def describe_backing(vm):
    disk = disk_of(vm)
    if disk is None:
        return "no VirtualDisk on the clone"
    backing = disk.backing
    parent = getattr(backing, "parent", None)
    return (
        f"`{type(backing).__name__}` fileName `{backing.fileName}`, "
        f"parent {'`' + parent.fileName + '`' if parent else '**None**'}, "
        f"diskMode `{getattr(backing, 'diskMode', '?')}`"
    )


def main():
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    ctx = ssl._create_unverified_context()  # noqa: S323 -- vcsim's self-signed cert

    print(f"connecting to https://{HOST}:{PORT}/sdk")
    si = SmartConnect(
        host=HOST,
        port=PORT,
        user=USER,
        pwd=PASSWORD,
        sslContext=ctx,
        disableSslCertValidation=True,
    )
    global CONTENT
    content = CONTENT = si.RetrieveContent()
    about = content.about
    print(f"vcsim says: {about.fullName} (apiVersion {about.apiVersion})")
    ROWS.append(
        (
            "`SmartConnect` + `RetrieveContent`",
            "yes",
            f"{about.fullName}, apiType `{about.apiType}`, "
            f"apiVersion `{about.apiVersion}`",
        )
    )

    try:
        walk(si, content)
    finally:
        Disconnect(si)

    print()
    print("| # | call | vcsim implements | what it returned |")
    print("|---|---|---|---|")
    for i, (call, verdict, note) in enumerate(ROWS, 1):
        print(f"| {i} | {call} | {verdict} | {note} |")
    counts = {}
    for _, verdict, _ in ROWS:
        counts[verdict] = counts.get(verdict, 0) + 1
    print()
    print("counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))


def walk(si, content):
    # ---- preflight: one ContainerView, one PropertyCollector call -----------
    view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True
    )
    ROWS.append(
        (
            "`CreateContainerView` over `VirtualMachine`",
            "yes",
            f"{len(view.view)} VMs in vcsim's default model",
        )
    )
    objects = []
    with step("`PropertyCollector.RetrieveContents`, all six paths in one call") as box:
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[
                vmodl.query.PropertyCollector.ObjectSpec(
                    obj=view,
                    skip=True,
                    selectSet=[
                        vmodl.query.PropertyCollector.TraversalSpec(
                            type=vim.view.ContainerView, path="view", skip=False
                        )
                    ],
                )
            ],
            propSet=[
                vmodl.query.PropertyCollector.PropertySpec(
                    type=vim.VirtualMachine, pathSet=PROPS
                )
            ],
        )
        objects = content.propertyCollector.RetrieveContents([spec])
        box["note"] = f"{len(objects)} `ObjectContent`"
    view.Destroy()

    for path in PROPS:
        with step(f"property `{path}`") as box:
            got = [
                p.val for o in objects for p in o.propSet if p.name == path
            ]
            missing = [
                m for o in objects for m in (o.missingSet or []) if m.path == path
            ]
            if missing:
                raise vmodl.fault.ManagedObjectNotFound(
                    msg=f"`missingSet` on {len(missing)} of {len(objects)} objects"
                )
            # An optional property that is simply unset is omitted from propSet
            # with no missingSet entry, on vCenter as here. That is a value of
            # None rather than an unimplemented path, and preflight has to read
            # it that way -- so it is recorded as answered.
            if not got:
                box["note"] = (
                    f"answered with no `missingSet`, but **unset on all "
                    f"{len(objects)}** VMs in vcsim's default model"
                )
            elif path == "config.hardware.device":
                kinds = sorted({type(d).__name__ for d in got[0]})
                box["note"] = (
                    f"set on {len(got)} of {len(objects)}; first carries "
                    + ", ".join(f"`{k}`" for k in kinds)
                )
            else:
                box["note"] = (
                    f"set on {len(got)} of {len(objects)}; first is `{got[0]}`"
                )

    # ---- resolve every named target object ---------------------------------
    lookups = [
        ("datacenter", vim.Datacenter),
        ("datastore", vim.Datastore),
        ("cluster", vim.ClusterComputeResource),
        ("host", vim.HostSystem),
        ("network", vim.Network),
        ("folder", vim.Folder),
        ("resource pool", vim.ResourcePool),
    ]
    for label, kind in lookups:
        with step(f"resolve {label} by name (`{kind.__name__}`)") as box:
            names = [o.name for o in all_of(content, kind)]
            if not names:
                raise Missing(f"vcsim's model contains no {kind.__name__}")
            obj = by_name(content, kind, names[0])
            STATE[label] = obj
            box["note"] = f"{len(names)} present, resolved `{names[0]}`"

    dc = STATE.get("datacenter")
    if dc is not None:
        # The VM folder and the cluster's root pool, not merely the first of
        # each kind: those are what create actually hands CreateVM and clone.
        STATE["folder"] = dc.vmFolder
        cluster = STATE.get("cluster")
        if cluster is not None:
            STATE["resource pool"] = cluster.resourcePool

    # ---- datastore PUT through vCenter's /folder endpoint (A1) --------------
    with step("datastore `PUT /folder/...?dcPath=&dsName=`, session cookie") as box:
        ds = need("datastore")
        dc = need("datacenter")
        name, value = cookie_pair(si._stub)
        url = folder_url(dc, ds, f"{SEED_DIR}/{SEED_FILE}")
        resp = requests.put(
            url,
            data=SEED_BYTES,
            cookies={name: value},
            verify=False,  # noqa: S501 -- vcsim's self-signed cert, as above
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]!r}")
        STATE["seed"] = f"[{ds.name}] {SEED_DIR}/{SEED_FILE}"
        box["note"] = (
            f"HTTP {resp.status_code}, cookie `{name}`, "
            f"wrote `{STATE['seed']}`"
        )

    with step("read the uploaded file back with `GET /folder/...`") as box:
        ds = need("datastore")
        dc = need("datacenter")
        name, value = cookie_pair(si._stub)
        url = folder_url(dc, ds, f"{SEED_DIR}/{SEED_FILE}")
        resp = requests.get(
            url, cookies={name: value}, verify=False, timeout=30  # noqa: S501
        )
        if resp.content != SEED_BYTES:
            raise RuntimeError(
                f"HTTP {resp.status_code}, {len(resp.content)} bytes back, "
                f"{len(SEED_BYTES)} sent"
            )
        box["note"] = f"HTTP {resp.status_code}, {len(resp.content)} bytes, identical"

    # ---- OVF import: CreateImportSpec, ImportVApp, the lease ----------------
    with step("`OvfManager.CreateImportSpec`, one streamOptimized disk") as box:
        pool = need("resource pool")
        ds = need("datastore")
        params = vim.OvfManager.CreateImportSpecParams(
            entityName=TEMPLATE_VM + "-import",
            diskProvisioning="thin",
        )
        result = content.ovfManager.CreateImportSpec(OVF, pool, ds, params)
        if result.error:
            raise RuntimeError(
                "; ".join(f"{type(e).__name__}: {e.msg}" for e in result.error)
            )
        STATE["import_spec"] = result.importSpec
        warn = "; ".join(f"{type(w).__name__}" for w in (result.warning or []))
        box["note"] = (
            f"{len(result.fileItem or [])} `fileItem`"
            + (
                f", first `{result.fileItem[0].path}` "
                f"create={result.fileItem[0].create}"
                if result.fileItem
                else ""
            )
            + (f", warnings {warn}" if warn else ", no warnings")
            + (
                f", importSpec `{type(result.importSpec).__name__}`"
                if result.importSpec
                else ", **importSpec is None**"
            )
        )

    with step("`ResourcePool.ImportVApp` returning an `HttpNfcLease`") as box:
        pool = need("resource pool")
        spec = need("import_spec")
        folder = need("folder")
        host = STATE.get("host")
        lease = pool.ImportVApp(spec=spec, folder=folder, host=host)
        deadline = time.monotonic() + 30
        while lease.state == vim.HttpNfcLease.State.initializing:
            if time.monotonic() > deadline:
                raise TimeoutError("lease still initializing after 30s")
            time.sleep(0.05)
        if lease.state == vim.HttpNfcLease.State.error:
            raise lease.error
        STATE["lease"] = lease
        box["note"] = f"lease state `{lease.state}`"

    with step("`HttpNfcLease.info.deviceUrl`, does it carry `*` as host") as box:
        lease = need("lease")
        urls = [d.url for d in (lease.info.deviceUrl or [])]
        if not urls:
            raise Missing("lease.info.deviceUrl is empty")
        STATE["device_url"] = urls[0]
        star = "**yes**" if "://*" in urls[0] else "**no**"
        box["note"] = f"{len(urls)} URL(s), first `{urls[0]}`, host `*` {star}"

    with step("`POST` the disk file to the lease device URL") as box:
        raw = need("device_url")
        url = raw.replace("://*/", f"://{HOST}:{PORT}/").replace("://*:", f"://{HOST}:")
        resp = requests.post(
            url,
            data=b"# not a real streamOptimized vmdk\n",
            headers={"Content-Type": "application/x-vnd.vmware-streamVmdk"},
            verify=False,  # noqa: S501
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]!r}")
        box["note"] = f"substituted host, HTTP {resp.status_code}"

    with step("`HttpNfcLeaseProgress`") as box:
        lease = need("lease")
        lease.HttpNfcLeaseProgress(100)
        entity = lease.info.entity
        STATE["imported"] = entity
        box["note"] = (
            f"accepted, lease state `{lease.state}`, `info.entity` "
            + (f"`{entity.name}`" if entity else "**None**")
        )

    with step("`HttpNfcLeaseComplete`") as box:
        lease = need("lease")
        lease.HttpNfcLeaseComplete()
        box["note"] = "returned"

    with step("read the lease back after `HttpNfcLeaseComplete`") as box:
        lease = need("lease")
        box["note"] = f"lease state `{lease.state}`"

    with step("the imported VM is in the inventory after the lease completes") as box:
        entity = need("imported")
        found = by_name(content, vim.VirtualMachine, entity.name)
        box["note"] = (
            f"`{found.name}`, {len(found.config.hardware.device)} devices, "
            f"powerState `{prop(found, 'runtime.powerState')}`"
        )

    # ---- CreateVM_Task attaching an existing disk file (the `datastore` knob)
    with step("`PUT` a VMDK to `[ds] vcows/spike/`, the `datastore` knob") as box:
        ds = need("datastore")
        dc = need("datacenter")
        cookie, value = cookie_pair(si._stub)
        url = folder_url(dc, ds, f"{SEED_DIR}/{TEMPLATE_VM}.vmdk")
        resp = requests.put(
            url,
            data=b"# not a real monolithicFlat descriptor\n",
            cookies={cookie: value},
            verify=False,  # noqa: S501
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]!r}")
        STATE["vmdk"] = f"[{ds.name}] {SEED_DIR}/{TEMPLATE_VM}.vmdk"
        box["note"] = f"HTTP {resp.status_code}, wrote `{STATE['vmdk']}`"

    # Deliberately provoked: a fault here is the *right* answer, and says
    # vcsim checks the backing file rather than accepting any path.
    with step("`CreateVM_Task` attaching a disk file not on the datastore") as box:
        folder = need("folder")
        pool = need("resource pool")
        ds = need("datastore")
        wait_task(
            folder.CreateVM_Task(
                config=create_spec(
                    ds, f"[{ds.name}] {SEED_DIR}/does-not-exist.vmdk", "vcows-spike-x"
                ),
                pool=pool,
                host=STATE.get("host"),
            )
        )
        box["note"] = "**accepted**, so vcsim does not check the backing file"


    with step("`CreateVM_Task` attaching an existing disk file") as box:
        folder = need("folder")
        pool = need("resource pool")
        ds = need("datastore")
        host = STATE.get("host")
        spec = create_spec(ds, need("vmdk"), TEMPLATE_VM)
        vm = wait_task(folder.CreateVM_Task(config=spec, pool=pool, host=host))
        STATE["vm"] = vm
        box["note"] = (
            f"`{vm.name}`, uuid `{prop(vm, 'summary.config.uuid')}`, "
            f"powerState `{prop(vm, 'runtime.powerState')}`"
        )

    with step("`vm.runtime` as a whole-object fetch, not through a collector") as box:
        vm = need("vm")
        box["note"] = f"powerState `{vm.runtime.powerState}`"

    with step("`ReconfigVM_Task` setting annotation and `firmware=efi`") as box:
        vm = need("vm")
        wait_task(
            vm.ReconfigVM_Task(
                spec=vim.vm.ConfigSpec(
                    annotation=MARKER,
                    firmware="efi",
                    bootOptions=vim.vm.BootOptions(efiSecureBootEnabled=False),
                )
            )
        )
        box["note"] = (
            f"config.firmware `{vm.config.firmware}`, "
            f"annotation round-trips {vm.config.annotation == MARKER}, "
            f"efiSecureBootEnabled `{vm.config.bootOptions.efiSecureBootEnabled}`"
        )

    with step("`CreateSnapshot_Task`") as box:
        vm = need("vm")
        wait_task(
            vm.CreateSnapshot_Task(
                name="vcows-base", description="spike", memory=False, quiesce=False
            )
        )
        snap = vm.snapshot.currentSnapshot if vm.snapshot else None
        if snap is None:
            raise Missing("task returned but vm.snapshot is None")
        STATE["snapshot"] = snap
        box["note"] = (
            f"`vm.snapshot.currentSnapshot` set, "
            f"{len(vm.snapshot.rootSnapshotList)} root snapshot(s)"
        )

    with step("`MarkAsTemplate`") as box:
        vm = need("vm")
        vm.MarkAsTemplate()
        box["note"] = f"`config.template` is `{vm.config.template}`"

    with step("re-read `config.annotation` and `config.template` on our own VM") as box:
        vm = need("vm")
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=vm)],
            propSet=[
                vmodl.query.PropertyCollector.PropertySpec(
                    type=vim.VirtualMachine,
                    pathSet=["config.annotation", "config.template"],
                )
            ],
        )
        got = {
            prop.name: prop.val
            for obj in content.propertyCollector.RetrieveContents([spec])
            for prop in obj.propSet
        }
        if got.get("config.annotation") != MARKER:
            raise Missing(f"annotation came back as {got.get('config.annotation')!r}")
        box["note"] = (
            f"marker round-trips byte-identical, `config.template` "
            f"`{got.get('config.template')}`"
        )

    # ---- clone twice, and look at what the disk backing actually is ---------
    with step("`CloneVM_Task`, linked (`createNewChildDiskBacking` + snapshot)") as box:
        vm = need("vm")
        folder = need("folder")
        pool = need("resource pool")
        ds = need("datastore")
        snap = need("snapshot")
        loc = vim.vm.RelocateSpec(
            pool=pool, datastore=ds, diskMoveType="createNewChildDiskBacking"
        )
        spec = vim.vm.CloneSpec(
            location=loc,
            snapshot=snap,
            powerOn=False,
            template=False,
            config=vim.vm.ConfigSpec(annotation=MARKER, numCPUs=2, memoryMB=1024),
        )
        clone = wait_task(vm.CloneVM_Task(folder=folder, name=LINKED_VM, spec=spec))
        STATE["linked"] = clone
        box["note"] = f"`{clone.name}`; {describe_backing(clone)}"

    with step("`CloneVM_Task`, full (no `diskMoveType`, no snapshot)") as box:
        vm = need("vm")
        folder = need("folder")
        pool = need("resource pool")
        ds = need("datastore")
        spec = vim.vm.CloneSpec(
            location=vim.vm.RelocateSpec(pool=pool, datastore=ds),
            powerOn=False,
            template=False,
        )
        clone = wait_task(vm.CloneVM_Task(folder=folder, name=FULL_VM, spec=spec))
        STATE["full"] = clone
        box["note"] = f"`{clone.name}`; {describe_backing(clone)}"

    with step("cdrom `IsoBackingInfo` + NIC with a manual MAC, on the clone") as box:
        clone = need("linked")
        ide = next(
            (
                d
                for d in clone.config.hardware.device
                if isinstance(d, vim.vm.device.VirtualIDEController)
            ),
            None,
        )
        cdrom = vim.vm.device.VirtualCdrom(
            key=-201,
            controllerKey=ide.key if ide else 200,
            unitNumber=0,
            backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(
                fileName=need("seed")
            ),
            connectable=vim.vm.device.VirtualDevice.ConnectInfo(
                startConnected=True, connected=True, allowGuestControl=False
            ),
        )
        nic = vim.vm.device.VirtualVmxnet3(
            key=-202,
            addressType="manual",
            macAddress="52:54:00:12:34:56",
            backing=vim.vm.device.VirtualEthernetCard.NetworkBackingInfo(
                deviceName=need("network").name
            ),
            connectable=vim.vm.device.VirtualDevice.ConnectInfo(startConnected=True),
        )
        wait_task(
            clone.ReconfigVM_Task(
                spec=vim.vm.ConfigSpec(
                    deviceChange=[
                        vim.vm.device.VirtualDeviceSpec(operation="add", device=cdrom),
                        vim.vm.device.VirtualDeviceSpec(operation="add", device=nic),
                    ],
                    extraConfig=[
                        vim.option.OptionValue(
                            key="ethernet0.checkMACAddress", value="FALSE"
                        )
                    ],
                )
            )
        )
        macs = [
            d.macAddress
            for d in clone.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualEthernetCard)
        ]
        isos = [
            d.backing.fileName
            for d in clone.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualCdrom)
            and isinstance(d.backing, vim.vm.device.VirtualCdrom.IsoBackingInfo)
        ]
        box["note"] = f"MACs {macs}, cdrom iso {isos}"

    with step("`PowerOnVM_Task`") as box:
        clone = need("linked")
        wait_task(clone.PowerOnVM_Task())
        box["note"] = f"powerState `{prop(clone, 'runtime.powerState')}`"

    with step("`PowerOffVM_Task`") as box:
        clone = need("linked")
        wait_task(clone.PowerOffVM_Task())
        box["note"] = f"powerState `{prop(clone, 'runtime.powerState')}`"

    with step("`Destroy_Task`") as box:
        clone = need("linked")
        wait_task(clone.Destroy_Task())
        gone = by_name_gone(content, LINKED_VM)
        box["note"] = f"VM named `{LINKED_VM}` gone from the inventory: {gone}"

    with step("`FileManager.DeleteDatastoreFile_Task`") as box:
        dc = need("datacenter")
        # The call is fine under either spelling pyVmomi offers
        # (`DeleteDatastoreFile_Task` or `DeleteFile`). What is not fine is
        # reading `task.info` afterwards -- see `wait_task`.
        wait_task(
            content.fileManager.DeleteDatastoreFile_Task(
                name=need("seed"), datacenter=dc
            )
        )
        box["note"] = f"deleted `{STATE['seed']}`"


def create_spec(ds, disk_file, name):
    """A one-controller, one-disk ConfigSpec attaching `disk_file` as it is.

    No `fileOperation` on the disk device: attach what is already on the
    datastore rather than create it, which is what the `datastore` import knob
    does after PUTting the descriptor and its extent.
    """
    controller = vim.vm.device.VirtualLsiLogicController(
        key=-100, busNumber=0, sharedBus="noSharing"
    )
    disk = vim.vm.device.VirtualDisk(
        key=-101,
        unitNumber=0,
        controllerKey=-100,
        capacityInKB=DISK_KB,
        backing=vim.vm.device.VirtualDisk.FlatVer2BackingInfo(
            fileName=disk_file, diskMode="persistent", thinProvisioned=True
        ),
    )
    return vim.vm.ConfigSpec(
        name=name,
        memoryMB=512,
        numCPUs=1,
        guestId="otherGuest64",
        annotation=MARKER,
        files=vim.vm.FileInfo(vmPathName=f"[{ds.name}]"),
        deviceChange=[
            vim.vm.device.VirtualDeviceSpec(operation="add", device=controller),
            vim.vm.device.VirtualDeviceSpec(operation="add", device=disk),
        ],
    )


def by_name_gone(content, name):
    return all(vm.name != name for vm in all_of(content, vim.VirtualMachine))


if __name__ == "__main__":
    sys.exit(main())
