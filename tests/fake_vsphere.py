"""A vCenter-shaped stand-in for pyvmomi's service instance.

This is not the fake *backend* -- that one proves the seam by having no
hypervisor semantics at all. This one has vSphere semantics deliberately, and it
imports ``pyVmomi`` at module scope the way ``tests/fake_libvirt.py`` imports
``libvirt``: the content object is vCenter's own ``ServiceInstanceContent``, so a
test cannot assert against a shape the SDK does not have.

``connect`` needs content and the stub the SOAP session cookie hangs off;
``preflight`` needs an inventory to walk and ``destroy`` needs to change it, and
that is the rest of this file.

**Two kinds of stand-in, and the split is pyvmomi's.** Data objects --
``TaskInfo``, a device, a search result, an ``ObjectContent`` -- are constructed
for real, because they are plain records the SDK builds anywhere. Managed
objects are not: their properties are read-only on a live vCenter, so
``vim.Datastore(...).name`` has no setter and cannot be filled in here. ``mo``
subclasses the real type and shadows exactly the fields the fake fills, which
keeps ``isinstance`` honest -- ``find_by_name`` filters by the type it was
handed, and a plain stand-in would make that filter untestable.

Every argument is recorded rather than ignored: a fake whose method drops what it
was handed leaves the caller's wiring unchecked, and the test then proves only
that a call happened. ``smart_connect`` takes keywords only, which is also how
``api.connect`` calls the real one -- a positional call is a ``TypeError`` here
rather than a silently accepted one.
"""

from __future__ import annotations

from collections.abc import Sequence
from fnmatch import fnmatch
from typing import Any

from pyVmomi import vim, vmodl

#: What vCenter's login sets, in the shape pyvmomi keeps on the stub. Arbitrary,
#: and the only thing asserted about it is that `connect` carries it across.
COOKIE = 'vmware_soap_session="52ab-not-a-session"; Path=/; HttpOnly; Secure;'


class _Stub:
    """The half of a service instance the session cookie hangs off."""

    def __init__(self, cookie: str):
        self.cookie = cookie


class FakeServiceInstance:
    """What ``SmartConnect`` returns, for the parts of a run that hold one."""

    def __init__(self, content: Any = None, cookie: str = COOKIE):
        self.content = content if content is not None else vim.ServiceInstanceContent()
        self._stub = _Stub(cookie)
        self.disconnected = False

    def RetrieveContent(self) -> Any:
        return self.content


def smart_connect(
    recorded: dict[str, Any],
    si: FakeServiceInstance | None = None,
    error: Exception | None = None,
):
    """A ``SmartConnect`` stand-in, recording how it was called.

    ``recorded`` is filled in on the call rather than returned, so a test that
    asserts on the connection arguments reads one dict whether the connect
    succeeded or raised. ``error`` is what vCenter refused the login with, in the
    one convention this file shares with ``tests/fake_proxmox.py``: the caller
    sets the failure and the fake raises it where the real call would.
    """
    instance = si if si is not None else FakeServiceInstance()

    def factory(**kw: Any) -> FakeServiceInstance:
        recorded.update(kw)
        if error is not None:
            raise error
        return instance

    return factory


def disconnect(si: FakeServiceInstance) -> None:
    """``Disconnect``'s stand-in. The session is closed exactly once per run."""
    si.disconnected = True


# -- the inventory -------------------------------------------------------


def mo(kind: Any, moid: str, **attrs: Any) -> Any:
    """One managed object of a real SDK type, carrying the fields a fake fills.

    The subclass exists because pyvmomi makes a managed object's properties
    read-only -- on a live vCenter each one is a round trip -- so ``name`` on a
    ``vim.Datastore`` has no setter. Shadowing exactly the fields named here
    leaves every other property as the SDK has it, and leaves ``isinstance``
    telling the truth.
    """
    shadow = type(f"Fake{kind.__name__}", (kind,), dict.fromkeys(attrs))
    obj = shadow(moid, stub=None)
    for field, value in attrs.items():
        setattr(obj, field, value)
    return obj


def array(kind: Any, items: Sequence) -> Any:
    """A VMOMI array of ``kind``, which is what a ``DynamicProperty.val`` and a
    ``TaskInfo.result`` have to hold.

    Both fields are typed ``anyType`` and pyvmomi refuses a bare Python list in
    either, so a fake handing one over would be answering in a shape no vCenter
    can. ``kind`` is untyped because the SDK builds ``Array`` at import time out
    of the WSDL and no type checker can see it.
    """
    return kind.Array(list(items))


def cdrom(path: str) -> Any:
    """A CD-ROM backed by an ISO on a datastore, as a seed ISO is attached."""
    return vim.vm.device.VirtualCdrom(
        backing=vim.vm.device.VirtualCdrom.IsoBackingInfo(fileName=path)
    )


def disk(
    path: str, parent: str | None = None, key: int = 0, capacity_kb: int = 1024
) -> Any:
    """A virtual disk, optionally an overlay on a parent.

    ``parent`` is what a linked clone's disk carries: the template's own disk,
    which every other deployment's clones are overlays on too. It is here so a
    test can prove that ``Existing.disks`` does not follow it.

    ``key`` is what a ``deviceChange`` of operation ``edit`` names the device
    by, so a disk a test intends to be grown needs one of its own: every device
    a fake builds otherwise has key 0 and an edit would match all of them.
    """
    backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(fileName=path)
    if parent is not None:
        backing.parent = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(fileName=parent)
    return vim.vm.device.VirtualDisk(key=key, capacityInKB=capacity_kb, backing=backing)


class FakeVm:
    """One VM as ``api.vms`` reads it: the object later phases hold, and the
    properties one ``RetrieveContents`` call answers with.

    ``props`` is the *answer*, not the VM, so a test models a property vCenter
    did not return by deleting its key -- the shape a VM being created right now
    has, with a name and no ``config`` at all.

    The two tasks a teardown starts hang off the managed object, because that is
    where the code holding it calls them: ``props["obj"]``, straight off the
    property collector's answer. ``power_off_error`` and ``destroy_error`` follow
    ``FakeBrowser``'s convention with the constructor's half left out: the caller
    decides what vCenter refused with and the fake ends the task with it, and
    setting them on the instance keeps a signature every other test would pass
    nothing to.
    """

    def __init__(
        self,
        name: str,
        *,
        uuid: str | None = None,
        annotation: str = "",
        template: bool = False,
        devices: Sequence = (),
        # pyvmomi's `VirtualMachinePowerState` members *are* these strings --
        # its enums subclass `str` -- and spelling it out keeps the SDK's
        # WSDL-built namespace, which no type checker can see into, out of a
        # fake that never reads the value back.
        power_state: str = "poweredOn",
        moid: str | None = None,
    ):
        #: Every task this VM was asked to start. `FakeContent` replaces it with
        #: its own call log, so one list orders a power off against a datastore
        #: delete made through the file manager -- which is the ordering a
        #: teardown has to get right and no per-object list can check.
        self.log: list[tuple] = []
        self.destroyed = False
        self.power_off_error: Any = None
        self.power_on_error: Any = None
        self.destroy_error: Any = None
        self.clone_error: Any = None
        #: Every `ConfigSpec` this VM was reconfigured with, and every snapshot
        #: taken of it, so a test reads what `make_template` asked for rather
        #: than that it asked.
        self.reconfigured: list[Any] = []
        self.snapshots: list[Any] = []
        #: Every clone made of this VM: the folder, the name, the spec, and the
        #: `FakeVm` the clone became -- so a test reads the devices and the
        #: annotation the clone ended up with, and not only what was asked for.
        self.clones: list[tuple] = []
        #: The vCenter this VM is on, set by `FakeContent.add`. A clone has to
        #: join it, or the property collector cannot answer for a VM the run
        #: just made.
        self.world: Any = None
        self.mo = mo(
            vim.VirtualMachine,
            moid or f"vm-{name}",
            # Shadowed as well as held in `props`, because the two are read by
            # different roads: `find_by_name` compares the managed object's own
            # `name`, and the property collector answers for the path.
            name=name,
            PowerOffVM_Task=self._power_off,
            PowerOnVM_Task=self._power_on,
            Destroy_Task=self._destroy,
            ReconfigVM_Task=self._reconfigure,
            CreateSnapshot_Task=self._snapshot,
            MarkAsTemplate=self._mark_as_template,
            CloneVM_Task=self._clone,
            # What a linked clone is an overlay on, and None until something
            # takes one. `create` reads `template.snapshot.currentSnapshot`.
            snapshot=None,
        )
        self.props: dict[str, Any] = {
            "name": name,
            "config.template": template,
            "config.annotation": annotation,
            "config.hardware.device": array(vim.vm.device.VirtualDevice, devices),
            "summary.config.uuid": uuid if uuid is not None else f"uuid-{name}",
            "runtime.powerState": power_state,
        }

    def _power_off(self) -> FakeTask:
        self.log.append(("PowerOffVM_Task", self.props["name"]))
        if self.power_off_error is not None:
            return FakeTask(error=self.power_off_error)
        self.props["runtime.powerState"] = "poweredOff"
        return FakeTask()

    def _power_on(self) -> FakeTask:
        self.log.append(("PowerOnVM_Task", self.props["name"]))
        if self.power_on_error is not None:
            return FakeTask(error=self.power_on_error)
        self.props["runtime.powerState"] = "poweredOn"
        return FakeTask()

    def _destroy(self) -> FakeTask:
        self.log.append(("Destroy_Task", self.props["name"]))
        if self.destroy_error is not None:
            return FakeTask(error=self.destroy_error)
        if self.props["runtime.powerState"] == "poweredOn":
            # vCenter refuses to destroy a running VM. Modelled, so a teardown
            # that stopped ordering the two fails loudly here rather than
            # passing against a fake that would delete anything.
            return FakeTask(
                error=vim.fault.InvalidPowerState(msg="the VM is powered on")
            )
        self.destroyed = True
        return FakeTask()

    def _reconfigure(self, spec: Any) -> FakeTask:
        self.log.append(("ReconfigVM_Task", self.props["name"]))
        if self.props["config.template"]:
            # A template's config is read-only until it is turned back into a
            # VM. Modelled for the reason the destroy of a running VM is: a
            # `make_template` that marked before it reconfigured would otherwise
            # pass here and fail on a vCenter.
            return FakeTask(
                error=vmodl.fault.NotSupported(msg="this VM is a template now")
            )
        # Recorded here rather than in `_apply`, so that `reconfigured` means
        # what a `ReconfigVM_Task` carried and not what a clone was made with.
        self.reconfigured.append(spec)
        self._apply(spec)
        return FakeTask()

    def _apply(self, spec: Any) -> None:
        """One ``ConfigSpec`` onto this VM's properties.

        Shared by the reconfigure and the clone, because a ``CloneSpec`` carries
        one too and a fake that applied it on only one path would let a clone
        that reconfigured afterwards pass -- which is the ordering the product
        deliberately does not use.

        ``edit`` matches on the device key, which is how vCenter finds the
        device an edit means; anything but ``add`` and ``edit`` is an assertion
        rather than a fault, because nothing here builds one and a fake that
        quietly ignored it would hide it.
        """
        if spec is None:
            return
        if spec.annotation is not None:
            self.props["config.annotation"] = spec.annotation
        devices = list(self.props["config.hardware.device"])
        for change in spec.deviceChange or ():
            if change.operation == "add":
                devices.append(change.device)
            elif change.operation == "edit":
                devices = [
                    change.device if held.key == change.device.key else held
                    for held in devices
                ]
            else:
                raise AssertionError(
                    f"this fake does not model a {change.operation!r} device change"
                )
        self.props["config.hardware.device"] = array(
            vim.vm.device.VirtualDevice, devices
        )

    def _clone(self, folder: Any, name: str, spec: Any) -> FakeTask:
        """``CloneVM_Task``: a second VM on the same vCenter, with the spec's own
        config already applied to it.

        **A linked spec is refused unless it names this VM's current snapshot.**
        vCenter has nothing to overlay a delta disk on otherwise, and this is
        where a ``make_template`` that marked before it snapshotted shows up --
        the template it left has none, so the clone the next run makes is the
        failure and not the template. A8 says vcsim accepts the spec and
        discards it, so the simulator cannot ask this question at all.
        """
        self.log.append(("CloneVM_Task", self.props["name"], name))
        if self.clone_error is not None:
            return FakeTask(error=self.clone_error)
        if spec.location.diskMoveType is not None:
            current = (
                None if self.mo.snapshot is None else self.mo.snapshot.currentSnapshot
            )
            if current is None or spec.snapshot is not current:
                return FakeTask(
                    error=vmodl.fault.InvalidArgument(
                        msg="a linked clone is an overlay on a snapshot of its source"
                    )
                )
        clone = FakeVm(
            name,
            annotation=self.props["config.annotation"],
            devices=list(self.props["config.hardware.device"]),
            power_state="poweredOff",
        )
        if self.world is not None:
            self.world.add(clone)
        clone._apply(spec.config)
        self.clones.append((folder, name, spec, clone))
        return FakeTask(result=clone.mo)

    def _snapshot(
        self, name: str, description: str, memory: bool, quiesce: bool
    ) -> FakeTask:
        self.log.append(("CreateSnapshot_Task", self.props["name"], name))
        if self.props["config.template"]:
            # Same rule as the reconfigure above: a template cannot be
            # snapshotted, so the snapshot a linked clone needs has to be taken
            # while it is still a VM.
            return FakeTask(
                error=vmodl.fault.NotSupported(msg="this VM is a template now")
            )
        self.snapshots.append((name, description, memory, quiesce))
        self.mo.snapshot = vim.vm.SnapshotInfo(
            currentSnapshot=mo(vim.vm.Snapshot, f"snapshot-{name}")
        )
        return FakeTask()

    def _mark_as_template(self) -> None:
        """Not a task: `MarkAsTemplate` returns nothing and is done when it
        returns, which is what the caller must not wait on."""
        self.log.append(("MarkAsTemplate", self.props["name"]))
        self.props["config.template"] = True


#: How long this fake plays along with a task that never finishes. Small,
#: because the only caller that reaches it has had its ceiling cut first.
MAX_POLLS = 50


class FakeTask:
    """A vCenter task, as ``api.wait`` polls it.

    ``running`` is how many reads report the task still going before it reaches
    its final state, which is what a wait that polls once and believes the
    answer gets wrong. ``never_finishes`` never reaches one: the fake stops
    answering after ``MAX_POLLS`` so a wait carrying no ceiling fails the test
    rather than running until something else kills it.
    """

    def __init__(
        self,
        result: Any = None,
        error: Any = None,
        running: int = 0,
        never_finishes: bool = False,
    ):
        self.running = running
        self.never_finishes = never_finishes
        self.polls = 0
        self._final: Any = (
            vim.TaskInfo.State.error
            if error is not None
            else vim.TaskInfo.State.success
        )
        self._info = vim.TaskInfo(
            state=(
                vim.TaskInfo.State.running if running or never_finishes else self._final
            ),
            result=result,
            error=error,
        )

    @property
    def info(self) -> Any:
        self.polls += 1
        if self.polls > MAX_POLLS:
            raise AssertionError(
                f"the task has been read {self.polls} times; the caller is "
                f"waiting on it without a ceiling"
            )
        if not self.never_finishes and self.polls > self.running:
            self._info.state = self._final
        return self._info


class FakeBrowser:
    """A datastore's ``HostDatastoreBrowser``, for the one search preflight makes.

    ``files`` are datastore paths as vCenter writes them --
    ``[ds-a] vcows/app01/app01-seed.iso`` -- and the answer is grouped by folder
    and matched on the file name, which is the shape
    ``SearchDatastoreSubFolders_Task`` returns. ``error`` is the fault the task
    ends with; a folder that is not there is the ordinary first-deploy answer,
    so it is a fault rather than an empty result.
    """

    def __init__(self, files: Sequence = (), error: Any = None):
        self.files = list(files)
        self.error = error
        #: Every search made: the path and the patterns it was given.
        self.searches: list[tuple[str, tuple[str, ...]]] = []

    def SearchDatastoreSubFolders_Task(
        self, datastorePath: str, searchSpec: Any
    ) -> FakeTask:
        patterns = tuple(searchSpec.matchPattern)
        self.searches.append((datastorePath, patterns))
        if self.error is not None:
            return FakeTask(error=self.error)
        folders: dict[str, list[str]] = {}
        for path in self.files:
            folder, _, found = path.rpartition("/")
            if path.startswith(f"{datastorePath}/") and any(
                fnmatch(found, pattern) for pattern in patterns
            ):
                folders.setdefault(folder, []).append(found)
        return FakeTask(
            result=array(
                vim.host.DatastoreBrowser.SearchResults,
                [
                    vim.host.DatastoreBrowser.SearchResults(
                        # Trailing slash, as vCenter writes it.
                        folderPath=f"{folder}/",
                        file=[
                            vim.host.DatastoreBrowser.FileInfo(path=found)
                            for found in names
                        ],
                    )
                    for folder, names in folders.items()
                ],
            )
        )


class FakeFileManager:
    """vCenter's ``FileManager``, for the one delete a teardown makes.

    ``files`` are datastore paths as vCenter writes them --
    ``[ds-a] vcows/app01/app01-seed.iso`` -- and a delete of a path this
    datastore does not hold ends its task with ``FileNotFound``, the way vCenter
    answers one. ``error`` is a fault to end every delete with instead, for the
    read-only datastore a seed cannot be removed from.

    ``deleted`` records the datacenter each delete named as well as the path,
    because vCenter cannot resolve ``[ds] path`` without one and a teardown that
    passed none would fail against a vCenter and pass against a fake that
    ignored it.
    """

    def __init__(self, log: list | None = None, files: Sequence = ()):
        self.files = list(files)
        self.error: Any = None
        self.log = [] if log is None else log
        self.deleted: list[tuple[str, str]] = []

    def DeleteDatastoreFile_Task(self, name: str, datacenter: Any) -> FakeTask:
        self.log.append(("DeleteDatastoreFile_Task", name))
        if datacenter is None:
            # What vCenter answers a datastore path it has no datacenter to
            # resolve in. A fault rather than an assertion, because a datacenter
            # that stopped resolving between preflight and teardown is a real
            # run and the seed it leaves is a leak to report.
            return FakeTask(
                error=vmodl.fault.InvalidArgument(
                    msg="a datastore path needs a datacenter"
                )
            )
        if self.error is not None:
            return FakeTask(error=self.error)
        if name not in self.files:
            return FakeTask(error=vim.fault.FileNotFound(msg=f"{name} is not there"))
        self.files.remove(name)
        self.deleted.append((name, datacenter.name))
        return FakeTask()


class _ViewManager:
    def __init__(self, world: FakeContent):
        self.world = world

    def CreateContainerView(self, container: Any, type: Any, recursive: bool) -> Any:
        # `type` is pyvmomi's own parameter name, and `api.find_by_name` passes
        # it by keyword, so a tidier spelling here would be a TypeError there.
        return self.world.container_view(container, type, recursive)


class _PropertyCollector:
    def __init__(self, world: FakeContent):
        self.world = world

    def RetrieveContents(self, specSet: list) -> list:
        return self.world.retrieve(specSet)


class FakeContent:
    """A vCenter's inventory, as ``RetrieveContent()`` hands it over.

    ``objects`` are the named things ``target.vsphere`` resolves -- built with
    ``mo`` so each one has the SDK type it really has -- and each carries the
    ``container`` it sits in, so a lookup rooted at one datacenter cannot find
    another's datastore. ``vms`` are ``FakeVm``, visible from the root folder
    the way a ContainerView over it sees every VM on the vCenter.

    ``calls`` is every call reached, in order: the fake has no API path to
    dispatch on the way ``tests/fake_proxmox.py`` does, so this is what stands
    in for one. The VMs and the file manager append to it too, which is what
    lets a test order a power off, a destroy and a datastore delete against each
    other.

    A VM a teardown destroyed stops being visible here, so the walk a second
    target makes sees the vCenter the first one left.
    """

    def __init__(self, objects: Sequence = (), vms: Sequence = ()):
        self.rootFolder = mo(vim.Folder, "group-d1", name="Datacenters")
        self.viewManager = _ViewManager(self)
        self.propertyCollector = _PropertyCollector(self)
        self.objects = list(objects)
        self.vms: list[Any] = []
        self.calls: list[tuple] = []
        self.fileManager = FakeFileManager(self.calls)
        self.ovfManager = FakeOvfManager(self.calls)
        for vm in vms:
            self.add(vm)
        #: Views handed out, and the ones destroyed. vCenter holds a view until
        #: it is destroyed or the session ends, so a run that makes one per
        #: configured name and destroys none leaks them for half an hour.
        self.views: list[Any] = []
        self.destroyed: list[Any] = []

        self.view_error: Exception | None = None
        self.retrieve_error: Exception | None = None

    def add(self, vm: Any) -> Any:
        """Put a VM on this vCenter, which is what ``CloneVM_Task`` does with
        what it made: visible to a container view and answerable by the property
        collector, and appending to the one call log everything else orders
        itself against."""
        vm.log = self.calls
        vm.world = self
        self.vms.append(vm)
        return vm

    def container_view(self, container: Any, types: list, recursive: bool) -> Any:
        self.calls.append(
            (
                "CreateContainerView",
                getattr(container, "name", ""),
                tuple(kind.__name__ for kind in types),
            )
        )
        if self.view_error is not None:
            raise self.view_error
        if container is None:
            # vCenter answers a null container with InvalidArgument. Modelled,
            # because a caller that lost its root would otherwise get a view of
            # the whole inventory here and nothing would say so.
            raise AssertionError("a container view needs a container to walk")
        if not recursive:
            raise AssertionError(
                "a non-recursive view sees only the container's own children; "
                "vcows resolves names anywhere under the one it names"
            )
        found = [
            obj
            for obj in [
                *self.objects,
                *(vm.mo for vm in self.vms if not vm.destroyed),
            ]
            if isinstance(obj, tuple(types)) and self._visible(obj, container)
        ]
        view = mo(vim.view.ContainerView, f"view-{len(self.views)}", view=found)
        view.Destroy = lambda: self.destroyed.append(view)
        self.views.append(view)
        return view

    def retrieve(self, specSet: list) -> list:
        [spec] = specSet
        paths = tuple(spec.propSet[0].pathSet)
        self.calls.append(("RetrieveContents", spec.propSet[0].type.__name__, paths))
        if self.retrieve_error is not None:
            raise self.retrieve_error
        if spec.propSet[0].type is not vim.VirtualMachine:
            raise AssertionError(f"this fake answers for VMs, not {spec.propSet[0]}")
        wanted = [objectSpec.obj for objectSpec in spec.objectSet]
        return [
            vmodl.query.PropertyCollector.ObjectContent(
                obj=vm.mo,
                propSet=[
                    vmodl.DynamicProperty(name=path, val=vm.props[path])
                    for path in paths
                    if path in vm.props
                ],
            )
            for vm in self.vms
            if vm.mo in wanted
        ]

    def _visible(self, obj: Any, container: Any) -> bool:
        """The root folder sees everything; anything else sees what it holds."""
        return container is self.rootFolder or getattr(obj, "container", None) is (
            container
        )


# -- the import ----------------------------------------------------------


class FakeOvfManager:
    """vCenter's ``OvfManager``, for the one descriptor an import builds.

    ``error`` is what vCenter refused the descriptor with -- a list, because
    that is the shape ``CreateImportSpecResult`` carries and a caller reading
    only the first would pass here and hide the rest on a real one.

    The descriptor itself is kept rather than parsed: what a test asks is
    whether the capacity and the file size reached the XML, and reading them out
    of the string is the same question a vCenter's own parser answers.
    """

    def __init__(self, log: list | None = None, error: Sequence = ()):
        self.log = [] if log is None else log
        self.error = list(error)
        #: Every call: the descriptor, the pool, the datastore and the params.
        self.specs: list[tuple] = []

    def CreateImportSpec(
        self, ovfDescriptor: str, resourcePool: Any, datastore: Any, cisp: Any
    ) -> Any:
        self.log.append(("CreateImportSpec", cisp.entityName))
        self.specs.append((ovfDescriptor, resourcePool, datastore, cisp))
        return vim.OvfManager.CreateImportSpecResult(
            importSpec=None if self.error else vim.vm.VmImportSpec(),
            error=list(self.error),
            warning=[],
        )


#: What vCenter is expected to answer with (#308, A4): a URL whose host is a
#: ``*`` standing for "whichever address you reached me on". vcsim answers with
#: its own listen address instead, which is why a test uses each.
DEVICE_URL = "https://*/nfc/session/52ab-not-a-session/disk-0.vmdk"


class FakeLease:
    """An ``HttpNfcLease``, as an import holds one.

    ``initializing`` is how many reads report the lease not ready yet, which is
    what a caller that reads the state once and believes it gets wrong.

    **Every read after ``HttpNfcLeaseComplete`` faults**, because that is what
    vcsim does: the call deletes the lease object, and reading ``state`` or
    ``info`` off it afterwards raises ``ManagedObjectNotFound``. So a caller that
    reads ``info.entity`` after completing rather than before fails here rather
    than against a vCenter.
    """

    def __init__(
        self,
        entity: Any,
        urls: Sequence = (DEVICE_URL,),
        initializing: int = 0,
        error: Any = None,
        never_ready: bool = False,
        log: list | None = None,
    ):
        self.entity = entity
        self.urls = list(urls)
        self.initializing = initializing
        self.error = error
        self.never_ready = never_ready
        self.log = [] if log is None else log
        self.polls = 0
        self.completed = False
        #: Every percentage the lease was told, in order.
        self.progress: list[int] = []

    def _alive(self) -> None:
        if self.completed:
            raise vmodl.fault.ManagedObjectNotFound(
                msg="The object has already been deleted or has not been "
                "completely created"
            )

    @property
    def state(self) -> Any:
        self._alive()
        self.polls += 1
        if self.polls > MAX_POLLS:
            raise AssertionError(
                f"the lease has been read {self.polls} times; the caller is "
                f"waiting on it without a ceiling"
            )
        if self.error is not None:
            return vim.HttpNfcLease.State.error
        if self.never_ready or self.polls <= self.initializing:
            return vim.HttpNfcLease.State.initializing
        return vim.HttpNfcLease.State.ready

    @property
    def info(self) -> Any:
        self._alive()
        return vim.HttpNfcLease.Info(
            entity=self.entity,
            deviceUrl=[vim.HttpNfcLease.DeviceUrl(url=url) for url in self.urls],
        )

    def HttpNfcLeaseProgress(self, percent: int) -> None:
        self._alive()
        self.progress.append(percent)

    def HttpNfcLeaseComplete(self) -> None:
        self._alive()
        self.log.append(("HttpNfcLeaseComplete", getattr(self.entity, "name", "")))
        self.completed = True


class FakePool:
    """A resource pool, for the one ``ImportVApp`` an import makes.

    ``mo`` is what a lookup finds and what a cluster's ``resourcePool`` points
    at; the lease it hands out is the caller's to set, so a test that wants a
    lease that never becomes ready builds one and passes it here.
    """

    def __init__(
        self,
        lease: Any = None,
        name: str = "Resources",
        container: Any = None,
        log: list | None = None,
        error: Exception | None = None,
    ):
        self.lease = lease
        self.error = error
        self.log = [] if log is None else log
        #: Every import: the spec, the folder and the host it was given.
        self.imports: list[tuple] = []
        self.mo = mo(
            vim.ResourcePool,
            "resgroup-1",
            name=name,
            container=container,
            ImportVApp=self._import,
        )

    def _import(self, spec: Any, folder: Any, host: Any) -> Any:
        self.log.append(("ImportVApp", getattr(folder, "name", "")))
        self.imports.append((spec, folder, host))
        if self.error is not None:
            raise self.error
        return self.lease


class FakeFolder:
    """A VM folder, for the ``CreateVM_Task`` the datastore import path makes."""

    def __init__(
        self,
        created: Any = None,
        name: str = "vm",
        container: Any = None,
        log: list | None = None,
        error: Any = None,
    ):
        self.created = created
        self.error = error
        self.log = [] if log is None else log
        #: Every create: the config spec, the pool and the host.
        self.creates: list[tuple] = []
        self.mo = mo(
            vim.Folder,
            "group-v1",
            name=name,
            container=container,
            CreateVM_Task=self._create,
        )

    def _create(self, config: Any, pool: Any, host: Any) -> FakeTask:
        self.log.append(("CreateVM_Task", config.name))
        self.creates.append((config, pool, host))
        if self.error is not None:
            return FakeTask(error=self.error)
        return FakeTask(result=self.created)


#: What urllib3 reads a request body in. The recorder below reads the same way,
#: so a body that reports its own progress reports it as often here as it would
#: against a vCenter.
CHUNK = 8192


class FakeResponse:
    """The two fields ``create`` reads off a ``requests`` answer."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class FakeHttp:
    """vCenter's two HTTP endpoints: the ``/folder`` PUT and the lease POST.

    Installed over ``requests`` itself rather than over the module under test,
    because ``create`` calls ``requests.put`` by attribute -- which is what makes
    a stand-in possible without the product knowing about one.

    The body is read in ``CHUNK``-sized pieces the way urllib3 reads one, and
    both what came out of it and what it declared as its length are recorded: a
    body with no length would be sent chunked, which the NFC endpoint refuses.
    """

    def __init__(self, status_code: int = 200, text: str = "", chunk: int = CHUNK):
        self.status_code = status_code
        self.text = text
        #: How much of the body is read at a time. urllib3's own size unless a
        #: test wants the reads finer than that.
        self.chunk = chunk
        #: One dict per request, in order.
        self.calls: list[dict] = []

    def install(self, monkeypatch: Any) -> FakeHttp:
        import requests

        monkeypatch.setattr(requests, "put", self.put)
        monkeypatch.setattr(requests, "post", self.post)
        return self

    def put(self, url: str, **kw: Any) -> FakeResponse:
        return self._record("PUT", url, kw)

    def post(self, url: str, **kw: Any) -> FakeResponse:
        return self._record("POST", url, kw)

    def _record(self, method: str, url: str, kw: dict) -> FakeResponse:
        body = kw.pop("data")
        read = b""
        while chunk := body.read(self.chunk):
            read += chunk
        self.calls.append(
            {
                "method": method,
                "url": url,
                "body": read,
                "length": len(body) if hasattr(body, "__len__") else None,
                **kw,
            }
        )
        return FakeResponse(self.status_code, self.text)
