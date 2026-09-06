"""A vCenter-shaped stand-in for pyvmomi's service instance.

This is not the fake *backend* -- that one proves the seam by having no
hypervisor semantics at all. This one has vSphere semantics deliberately, and it
imports ``pyVmomi`` at module scope the way ``tests/fake_libvirt.py`` imports
``libvirt``: the content object is vCenter's own ``ServiceInstanceContent``, so a
test cannot assert against a shape the SDK does not have.

``connect`` needs content and the stub the SOAP session cookie hangs off;
``preflight`` needs an inventory to walk, and that is the rest of this file.

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


def disk(path: str, parent: str | None = None) -> Any:
    """A virtual disk, optionally an overlay on a parent.

    ``parent`` is what a linked clone's disk carries: the template's own disk,
    which every other deployment's clones are overlays on too. It is here so a
    test can prove that ``Existing.disks`` does not follow it.
    """
    backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(fileName=path)
    if parent is not None:
        backing.parent = vim.vm.device.VirtualDisk.FlatVer2BackingInfo(fileName=parent)
    return vim.vm.device.VirtualDisk(capacityInKB=1024, backing=backing)


class FakeVm:
    """One VM as ``api.vms`` reads it: the object later phases hold, and the
    properties one ``RetrieveContents`` call answers with.

    ``props`` is the *answer*, not the VM, so a test models a property vCenter
    did not return by deleting its key -- the shape a VM being created right now
    has, with a name and no ``config`` at all.
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
        self.mo = vim.VirtualMachine(moid or f"vm-{name}", stub=None)
        self.props: dict[str, Any] = {
            "name": name,
            "config.template": template,
            "config.annotation": annotation,
            "config.hardware.device": array(vim.vm.device.VirtualDevice, devices),
            "summary.config.uuid": uuid if uuid is not None else f"uuid-{name}",
            "runtime.powerState": power_state,
        }


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
    in for one.
    """

    def __init__(self, objects: Sequence = (), vms: Sequence = ()):
        self.rootFolder = mo(vim.Folder, "group-d1", name="Datacenters")
        self.viewManager = _ViewManager(self)
        self.propertyCollector = _PropertyCollector(self)
        self.objects = list(objects)
        self.vms = list(vms)
        self.calls: list[tuple] = []
        #: Views handed out, and the ones destroyed. vCenter holds a view until
        #: it is destroyed or the session ends, so a run that makes one per
        #: configured name and destroys none leaks them for half an hour.
        self.views: list[Any] = []
        self.destroyed: list[Any] = []

        self.view_error: Exception | None = None
        self.retrieve_error: Exception | None = None

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
        if not recursive:
            raise AssertionError(
                "a non-recursive view sees only the container's own children; "
                "vcows resolves names anywhere under the one it names"
            )
        found = [
            obj
            for obj in [*self.objects, *(vm.mo for vm in self.vms)]
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
