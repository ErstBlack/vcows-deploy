# vcsim as an offline vSphere: measured 2026-09-06

Issue #310 (epic #308, chunk C2) asked which of the epic's create-and-teardown
calls the govmomi simulator answers, so that the C9 smoke gate asserts only what
a simulator can decide. Every row below came from
`docs/spikes/vsphere_vcsim.py` against one `vcsim` process; the script is a
record, not a library, and nothing collects or runs it.

- **vcsim** from govmomi **v0.56.0**, pinned in `scripts/install-tools.sh`
  (sha256 from that release's own `checksums.txt`).
- It identifies itself as `VMware vCenter Server 6.5.0 build-5973321 (govmomi
  simulator)`, `apiType VirtualCenter`, **`apiVersion 6.5`**.
- Client: `pyVmomi` 9.1.1.0 from the dev venv, `requests` 2.34.2.
- Started as `vcsim -l 127.0.0.1:8989`, default model: 1 datacenter `DC0`,
  1 datastore `LocalDS_0`, 1 cluster `DC0_C0`, 4 hosts, 4 VMs.

## 1. Verdict

**41 calls walked: 38 answered, 3 faulted, none unreachable.** The whole
sequence in the epic's Design section runs end to end against the simulator,
including the OVF import path A8 left unmeasured. The three faults are one
deliberate probe, one lifecycle difference, and one client-side deserialisation
failure that changes how the product must read *every* property.

The consequential result is not in the table's verdict column. It is that
vcsim's `apiVersion` is 6.5 while the target is vCenter 7, and that vcsim
validates almost nothing: it accepted a 33-byte "streamOptimized VMDK", a
`52:54:00` manual MAC, `firmware=efi`, and a linked-clone spec it then ignored.
A green smoke gate proves the call shapes and the ordering, and proves nothing
about acceptance. Section 4 is the list of assertions C9 must not make.

## 2. The measured table

| # | call | vcsim implements | what it returned |
|---|---|---|---|
| 1 | `SmartConnect` + `RetrieveContent` | yes | VMware vCenter Server 6.5.0 build-5973321 (govmomi simulator), apiType `VirtualCenter`, apiVersion `6.5` |
| 2 | `CreateContainerView` over `VirtualMachine` | yes | 4 VMs in vcsim's default model |
| 3 | `PropertyCollector.RetrieveContents`, all six paths in one call | yes | 4 `ObjectContent` |
| 4 | property `name` | yes | set on 4 of 4; first is `DC0_H0_VM0` |
| 5 | property `config.template` | yes | set on 4 of 4; first is `False` |
| 6 | property `config.annotation` | yes | answered with no `missingSet`, but **unset on all 4** VMs in vcsim's default model |
| 7 | property `config.hardware.device` | yes | set on 4 of 4; first carries `vim.vm.device.ParaVirtualSCSIController`, `vim.vm.device.VirtualCdrom`, `vim.vm.device.VirtualDisk`, `vim.vm.device.VirtualE1000`, `vim.vm.device.VirtualIDEController`, `vim.vm.device.VirtualKeyboard`, `vim.vm.device.VirtualPCIController`, `vim.vm.device.VirtualPS2Controller`, `vim.vm.device.VirtualPointingDevice`, `vim.vm.device.VirtualSIOController`, `vim.vm.device.VirtualVMCIDevice`, `vim.vm.device.VirtualVideoCard` |
| 8 | property `summary.config.uuid` | yes | set on 4 of 4; first is `63d40cc5-9cba-5cc1-884a-f7f19070ecea` |
| 9 | property `runtime.powerState` | yes | set on 4 of 4; first is `poweredOn` |
| 10 | resolve datacenter by name (`vim.Datacenter`) | yes | 1 present, resolved `DC0` |
| 11 | resolve datastore by name (`vim.Datastore`) | yes | 1 present, resolved `LocalDS_0` |
| 12 | resolve cluster by name (`vim.ClusterComputeResource`) | yes | 1 present, resolved `DC0_C0` |
| 13 | resolve host by name (`vim.HostSystem`) | yes | 4 present, resolved `DC0_H0` |
| 14 | resolve network by name (`vim.Network`) | yes | 3 present, resolved `VM Network` |
| 15 | resolve folder by name (`vim.Folder`) | yes | 4 present, resolved `vm` |
| 16 | resolve resource pool by name (`vim.ResourcePool`) | yes | 2 present, resolved `Resources` |
| 17 | datastore `PUT /folder/...?dcPath=&dsName=`, session cookie | yes | HTTP 200, cookie `vmware_soap_session`, wrote `[LocalDS_0] vcows/spike/x.iso` |
| 18 | read the uploaded file back with `GET /folder/...` | yes | HTTP 200, 37 bytes, identical |
| 19 | `OvfManager.CreateImportSpec`, one streamOptimized disk | yes | 1 `fileItem`, first `disk.vmdk` create=False, no warnings, importSpec `vim.vm.VmImportSpec` |
| 20 | `ResourcePool.ImportVApp` returning an `HttpNfcLease` | yes | lease state `ready` |
| 21 | `HttpNfcLease.info.deviceUrl`, does it carry `*` as host | yes | 1 URL(s), first `https://127.0.0.1:8989/nfc/session%5Bc8ccbe69-4288-4b7b-b25e-de81e10b7d3a%5Da8cd1ede-f0bd-4460-a9c4-531cdba41b76/disk-0.vmdk`, host `*` **no** |
| 22 | `POST` the disk file to the lease device URL | yes | substituted host, HTTP 200 |
| 23 | `HttpNfcLeaseProgress` | yes | accepted, lease state `ready`, `info.entity` `vcows-spike-tmpl-import` |
| 24 | `HttpNfcLeaseComplete` | yes | returned |
| 25 | read the lease back after `HttpNfcLeaseComplete` | faulted | `vmodl.fault.ManagedObjectNotFound`: The object has already been deleted or has not been completely created |
| 26 | the imported VM is in the inventory after the lease completes | yes | `vcows-spike-tmpl-import`, 11 devices, powerState `poweredOff` |
| 27 | `PUT` a VMDK to `[ds] vcows/spike/`, the `datastore` knob | yes | HTTP 200, wrote `[LocalDS_0] vcows/spike/vcows-spike-tmpl.vmdk` |
| 28 | `CreateVM_Task` attaching a disk file not on the datastore | faulted | `vim.fault.FileNotFound`: *types.FileNotFound |
| 29 | `CreateVM_Task` attaching an existing disk file | yes | `vcows-spike-tmpl`, uuid `47de1637-4012-5860-801a-c89611f8b09f`, powerState `poweredOff` |
| 30 | `vm.runtime` as a whole-object fetch, not through a collector | faulted | `AttributeError`: runtime |
| 31 | `ReconfigVM_Task` setting annotation and `firmware=efi` | yes | config.firmware `efi`, annotation round-trips True, efiSecureBootEnabled `False` |
| 32 | `CreateSnapshot_Task` | yes | `vm.snapshot.currentSnapshot` set, 1 root snapshot(s) |
| 33 | `MarkAsTemplate` | yes | `config.template` is `True` |
| 34 | re-read `config.annotation` and `config.template` on our own VM | yes | marker round-trips byte-identical, `config.template` `True` |
| 35 | `CloneVM_Task`, linked (`createNewChildDiskBacking` + snapshot) | yes | `vcows-spike-linked`; `vim.vm.device.VirtualDisk.FlatVer2BackingInfo` fileName `[LocalDS_0] vcows-spike-linked/vcows-spike-linked.vmdk`, parent **None**, diskMode `persistent` |
| 36 | `CloneVM_Task`, full (no `diskMoveType`, no snapshot) | yes | `vcows-spike-full`; `vim.vm.device.VirtualDisk.FlatVer2BackingInfo` fileName `[LocalDS_0] vcows-spike-full/vcows-spike-full.vmdk`, parent **None**, diskMode `persistent` |
| 37 | cdrom `IsoBackingInfo` + NIC with a manual MAC, on the clone | yes | MACs ['52:54:00:12:34:56'], cdrom iso ['[LocalDS_0] vcows/spike/x.iso'] |
| 38 | `PowerOnVM_Task` | yes | powerState `poweredOn` |
| 39 | `PowerOffVM_Task` | yes | powerState `poweredOff` |
| 40 | `Destroy_Task` | yes | VM named `vcows-spike-linked` gone from the inventory: True |
| 41 | `FileManager.DeleteDatastoreFile_Task` | yes | deleted `[LocalDS_0] vcows/spike/x.iso` |

`yes` in the third column means vcsim returned without a fault; it does not mean
vcsim did what vCenter would do. Rows 25, 28 and 30 are the three faults, and
row 28's fault is the answer the step was looking for.

## 3. Six surprises

**The whole-object property fetch does not work at all.** `vm.runtime`,
`vm.summary` and `task.info` each raise `AttributeError` in pyVmomi 9 against
vcsim. The name in the `AttributeError` is the property being read, because a
descriptor that raises `AttributeError` falls through to `__getattr__`; the real
failure is inside the SOAP deserialiser, `AttributeError: type object
'vim.VirtualMachine.FaultToleranceState' has no attribute ''`. vcsim emits an
empty `faultToleranceState` element inside `VirtualMachineRuntimeInfo` and
pyVmomi cannot map an empty string onto that enum. The same leaves read through
the PropertyCollector -- `runtime.powerState`, `summary.config.uuid`,
`info.state`, `info.error`, `info.result` -- all come back fine. This is why the
spike has a `props` helper and why its task waiter polls `info.state` through
the collector rather than touching `task.info`. **C3's `api.wait` and preflight
must both do the same**, or the smoke gate fails on the first task it waits for
and the failure will read as a vcsim gap rather than a client one.

**The lease device URL carries no `*`.** A4 assumed the host in
`HttpNfcLease.info.deviceUrl` is `*`, substituted by the SDK. vcsim returns
`https://127.0.0.1:8989/nfc/session%5B<uuid>%5D<uuid>/disk-0.vmdk` -- its own
listen address, already resolved. So the substitution code is exercised as a
no-op here and A4 stays a first-contact item: the simulator cannot tell us
whether vCenter emits `*` or whether the resulting URL is reachable. Write the
replacement so it is harmless when there is no `*`, and do not let C9 assert on
the URL's shape.

**`HttpNfcLeaseComplete` deletes the lease.** Reading `lease.state` after the
call raises `ManagedObjectNotFound` rather than returning `done`. `info.entity`
has to be captured *before* `HttpNfcLeaseComplete`, which is the opposite of
what the obvious sequence does. The imported VM itself is in the inventory
afterwards, with 11 devices and `poweredOff`, so the import genuinely lands.

**Both clones produce the same disk backing.** A8 said vcsim ignores
`diskMoveType` and `snapshot`, and it is right: the linked clone
(`createNewChildDiskBacking` plus the template's snapshot) and the full clone
both come back as `FlatVer2BackingInfo` with `parent` **None** and `diskMode`
`persistent`, differing only in `fileName`. Nothing about linked cloning is
observable here. C9 can assert that the `CloneSpec` we build carries the
snapshot and the disk move type, and that the call is accepted -- nothing more.

**An unset optional property is omitted, not faulted.** `config.annotation` came
back on none of the four default VMs, with an empty `missingSet`. That is
correct vCenter behaviour for an unset property and the same shape a real
vCenter gives, so preflight has to read "absent from `propSet`" as `None` rather
than as an error. The marker itself round-trips: our own VM, created with
`annotation` in the `ConfigSpec` and re-read through the collector, gives the
payload back byte-identical, and `config.template` flips to `True` after
`MarkAsTemplate`.

**vcsim does check one thing: the disk backing file.** `CreateVM_Task` with a
`FlatVer2BackingInfo` naming a file that is not on the datastore throws
`FileNotFound` (row 28). PUT the file to `[LocalDS_0] vcows/spike/` through the
`/folder` endpoint first and the same `ConfigSpec` succeeds (rows 27 and 29).
That makes the epic's `datastore` import knob -- PUT the VMDK, then
`CreateVM_Task` attaching it -- testable end to end offline, which the `ovf`
default is not.

## 4. What the smoke gate cannot assert

For C9 (#317). Each of these ran green against vcsim and proves nothing about
vCenter, so the smoke test must either skip the assertion by name or assert only
the call shape.

| | assertion | why vcsim cannot decide it |
|---|---|---|
| N1 | that a linked clone is a delta disk | the clone's backing is `FlatVer2BackingInfo` with `parent` `None` for both linked and full specs; `diskMoveType` and `snapshot` are accepted and discarded (A8) |
| N2 | that a linked clone's disk cannot be grown | follows from N1: there is no delta disk to refuse the growth, so the `disk_gb` == image virtual size rule in `schema.validate` has no offline counter-example |
| N3 | that vCenter accepts qemu-img's streamOptimized VMDK | the lease `POST` took 33 bytes of ASCII with `Content-Type: application/x-vnd.vmware-streamVmdk` and returned 200. No header is parsed |
| N4 | that the lease device URL is reachable through vCenter (A4) | vcsim returns its own listen address, never `*`, so the substitution is a no-op and the reachability question never arises |
| N5 | that a `52:54:00` manual MAC is accepted (A6) | accepted, and `ethernet0.checkMACAddress=FALSE` accepted, with no validation of either behind them |
| N6 | anything about hardware version or secure boot (A10) | `firmware=efi` and `efiSecureBootEnabled=False` are stored and echoed back; vcsim has no vmx-version ceiling and no VBS |
| N7 | that a template's snapshot must be its only one (A5) | one snapshot was taken and `MarkAsTemplate` succeeded; vcsim imposes no rule that a second snapshot would violate |
| N8 | that the lease ends in state `done` | vcsim deletes the lease object on `HttpNfcLeaseComplete`; asserting a terminal state raises `ManagedObjectNotFound` |
| N9 | anything version-gated on vCenter 7 or 8 | vcsim negotiates `urn:vim25/6.5` |
| N10 | that `CopyVirtualDisk_Task` with a format-changing `destSpec` fails (A2) | not exercised: the design removed the call, and a simulator that accepts everything could not have shown the `NotImplemented` anyway |

What C9 *can* assert, and what the spike therefore fixes as the smoke gate's
scope: the ordering of the sequence, that every call is accepted with the
argument shapes the product builds, that the marker round-trips through
`config.annotation`, that `MarkAsTemplate` sets `config.template`, that the
`/folder` PUT and `GET` move bytes intact, that `Destroy_Task` removes the VM
from the inventory, and that `DeleteDatastoreFile_Task` removes the seed ISO.

## 5. Consequences already taken

- Every property read in the vSphere backend goes through the PropertyCollector
  with leaf paths, including the task waiter. Not a style choice: `task.info`
  raises against vcsim.
- `HttpNfcLease.info.entity` is read before `HttpNfcLeaseComplete`, not after.
- The `*`-to-host substitution on the lease URL must tolerate a URL with no `*`.
- vcsim is pinned in `scripts/install-tools.sh` alongside trivy and the rest,
  with a row in `lib.sh`'s `TOOL_INSTALLER`. It has **no version flag** --
  `vcsim --version` prints its usage and exits 2 -- so `install_one`'s
  PATH-detection arm refuses a system copy rather than reporting it. The pinned
  download is the only supported path.
