"""The only module in this package that reaches vCenter.

Kept to one file for the reason ``tests/test_seam.py`` exists for the other two
backends: once the registry names ``VsphereBackend``, importing the registry
drags this package in on every run, including runs on a machine that will never
talk to a vCenter. Nothing here is imported at module scope; ``connect`` imports
``pyVim.connect`` inside its own body, exactly as the Proxmox backend imports
``proxmoxer`` and the libvirt backend imports ``libvirt`` inside the functions
that hold a connection.

The lookups and the task wait are below; the phases themselves live in
``preflight.py`` and the modules the later chunks add, each reaching vCenter
through the ``Session`` and the helpers here -- which is what makes ``wait`` a
single implementation: every task any phase starts is checked the same way.
"""

from __future__ import annotations

import logging
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: vCenter's HTTPS port unless the endpoint names another one.
DEFAULT_PORT = 443

#: Ceiling on one vCenter task. The tasks waited on are a datastore search on
#: the preflight, and the import, the clone, the reconfigure and the power
#: changes of a create or a teardown. Generous because the import moves the
#: whole image, and the same number the Proxmox backend chose for the same
#: reason: a limit short enough to be tidy would abandon a task that was going
#: to succeed.
TASK_TIMEOUT = 600

#: How long ``wait`` sleeps between two reads of ``task.info``. One pair of
#: numbers for every task, rather than a ceiling per call site: a wait that is
#: right for a clone is right for a search, and one place to change is what
#: stops the two drifting.
POLL_INTERVAL = 1


class VsphereApiError(Exception):
    """A call to vCenter failed, or a task it started did.

    Ours rather than pyvmomi's so nothing above this package imports ``vim`` to
    catch a fault -- the same reason the Proxmox backend re-raises proxmoxer's
    errors as its own, and what keeps ``cli.main``'s catch-all from having to
    know what a ``vim.fault`` is.
    """


@dataclass(frozen=True)
class Session:
    """pyvmomi's service instance, and what the rest of a run needs off it.

    Opaque to core, which passes it from ``preflight`` to ``destroy`` without
    reading it -- the same contract libvirt's ``virConnect`` has.

    **It carries no resolved managed objects, and that is a decision.** The
    epic's design has the datacenter, the datastore, the pool, the folder and
    the network resolved into this record. They are not: a name that does not
    resolve must become a ``Problem`` rather than an exception, only
    ``preflight`` produces Problems, and this record is frozen and built before
    ``preflight`` runs. So ``preflight`` resolves through ``find_by_name`` and
    the later phases resolve again through the same call. It costs a handful of
    lookups per run and it keeps managed objects out of ``Discovered.artifacts``
    entirely, which is what leaves ``render`` pure and golden-file testable.
    """

    si: Any
    content: Any
    """What ``RetrieveContent()`` returned: the root folder and every manager."""

    cookie: str
    """The SOAP session cookie.

    Held because the datastore uploads are plain HTTP against vCenter's
    ``/folder`` endpoint rather than SDK calls, and that cookie is the only thing
    that authorises them. Taken off the stub here so nothing later has to reach
    into pyvmomi's internals a second time.
    """


@contextmanager
def connect(cfg: dict):
    """Open a vCenter session against the configured endpoint.

    **The credential is read here and nowhere else.** ``target.vsphere`` carries
    a user and a password; neither is returned and only the user is logged,
    because that is what an operator debugging a failed login needs and it is not
    the credential.

    TLS follows the Proxmox backend: ``insecure`` outranks ``ca_cert``, so a
    config that got past ``validate`` with both gets no verification rather than
    a certificate that reads as one thing and behaves as another. ``ca_cert``
    becomes an ``ssl`` context directly -- ``ssl`` takes the certificate itself,
    so unlike the Proxmox backend nothing is written to a file.

    A login vCenter refuses comes back as ``VsphereApiError``, the way
    proxmoxer's ``AuthenticationError`` does on the other backend: a raw
    ``vim.fault`` reaching ``cli.main``'s catch-all would print pyvmomi's own
    repr and name no config field. Only the login is translated -- the yield is
    outside the ``try`` -- because an exception from the body carries what
    ``base.carrying`` attached to it, and rewrapping it would drop the record of
    what a half-finished create had already made.
    """
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim, vmodl

    target = cfg["target"]["vsphere"]
    parts = urlsplit(target["endpoint"])
    # `_check_target` has already refused anything with credentials, a query or a
    # path, so what reaches here is a bare origin.
    host = parts.hostname
    tls: dict[str, Any] = {}
    if target.get("insecure"):
        tls["disableSslCertValidation"] = True
    elif target.get("ca_cert") is not None:
        tls["sslContext"] = ssl.create_default_context(cadata=target["ca_cert"])

    log.info("connecting to %s as %s", host, target["user"])
    try:
        si = SmartConnect(
            host=host,
            port=parts.port or DEFAULT_PORT,
            user=target["user"],
            pwd=target["password"],
            **tls,
        )
    # `.msg` through `getattr` in both: pyvmomi builds its fault classes at
    # import time out of the WSDL, so no type checker can see the field.
    except vim.fault.InvalidLogin as exc:
        raise VsphereApiError(
            f"{host} rejected the credentials in target.vsphere: "
            f"{getattr(exc, 'msg', exc)}"
        ) from exc
    except vmodl.MethodFault as exc:
        # Every SOAP fault, not just `vim.fault.VimFault`: a locked account
        # comes back as `NotAuthenticated`, which descends from `RuntimeFault`
        # instead, and the two share only this base.
        raise VsphereApiError(f"{host}: {getattr(exc, 'msg', exc)}") from exc
    try:
        yield Session(si=si, content=si.RetrieveContent(), cookie=si._stub.cookie)
    finally:
        # vCenter keeps an idle session for half an hour, and a run that raised
        # is exactly the one an operator retries at once.
        Disconnect(si)


def wait(task: Any, what: str) -> Any:
    """Block until one vCenter task finishes, refuse anything but success, and
    hand back what it produced.

    **A task that stopped is not a task that worked.** ``info.state`` goes to
    ``error`` as readily as to ``success``, and a caller that only waited for it
    to stop reports a clone or a delete that never happened -- the silent
    partial teardown ``Outcome`` exists to prevent. The result comes back
    because vCenter returns the object it made through the task and there is no
    second call that would fetch it.

    The ceiling is ours rather than the SDK's, which has none: ``WaitForTask``
    blocks until vCenter answers or the connection dies, so a wedged task hangs
    the run instead of failing it.

    **Every read below is a leaf, and that is a measurement rather than a
    style.** The vcsim spike found that fetching a whole property object off a
    managed object -- ``task.info``, ``vm.runtime``, ``vm.summary`` -- raises
    ``AttributeError`` under pyVmomi 9, because vcsim emits an empty
    ``faultToleranceState`` the deserialiser will not take. Leaf reads are fine.
    So nothing here binds ``info`` to a local and reads fields off it, and the
    C9 smoke gate is what would catch a change that did.
    """
    from pyVmomi import vim

    # One line before the wait rather than one per poll, as on the Proxmox
    # backend: what it says is how long the silence can legitimately last.
    log.debug(
        "%s: waiting on a task, polling every %ss for up to %ss",
        what,
        POLL_INTERVAL,
        TASK_TIMEOUT,
    )
    deadline = time.monotonic() + TASK_TIMEOUT
    while task.info.state in (vim.TaskInfo.State.queued, vim.TaskInfo.State.running):
        if time.monotonic() >= deadline:
            raise VsphereApiError(
                f"{what}: the task had not finished after {TASK_TIMEOUT}s. It may "
                f"still be running on the vCenter; check it before retrying."
            )
        time.sleep(POLL_INTERVAL)
    if task.info.state != vim.TaskInfo.State.success:
        error = task.info.error
        raise VsphereApiError(
            f"{what}: the task ended as {task.info.state} "
            f"({getattr(error, 'msg', None) or error})"
        )
    log.debug("%s: task ok", what)
    return task.info.result


def find_by_name(content: Any, vim_type: Any, name: str, root: Any = None) -> Any:
    """The one object of ``vim_type`` called ``name`` under ``root``, or None.

    None rather than a raise: every caller is ``preflight`` deciding whether a
    configured name resolves, and a miss there is a ``Problem`` naming the field
    that holds the name. An exception would lose the field.

    ``root`` defaults to the root folder, which is the only container the
    datacenter itself can be found in. Everything else is looked for inside the
    datacenter, because two datacenters on one vCenter may each hold a datastore
    or a port group of the same name.
    """
    view = content.viewManager.CreateContainerView(
        container=content.rootFolder if root is None else root,
        type=[vim_type],
        recursive=True,
    )
    try:
        return next((obj for obj in view.view if obj.name == name), None)
    finally:
        # vCenter holds a view until it is destroyed or the session ends, and a
        # run makes one per configured name.
        view.Destroy()


#: What one preflight walk needs off every VM. Read in a single
#: ``RetrieveContents`` call: ``vm.config.annotation`` on a managed object is a
#: round trip apiece, so a hundred-VM vCenter is a hundred round trips per
#: property the moment this is done by attribute access.
VM_PROPERTIES = (
    "name",
    "config.template",
    "config.annotation",
    "config.hardware.device",
    "summary.config.uuid",
    "runtime.powerState",
)


def vms(content: Any) -> list[dict]:
    """Every VM this session can see, as ``VM_PROPERTIES`` describes it.

    One dict per VM: the property paths that came back, keyed by the path
    itself, plus ``obj`` -- the managed object the later phases power off,
    reconfigure and destroy. A property vCenter did not answer for is absent
    rather than None, which is the shape ``RetrieveContents`` returns and the
    reason every reader here uses ``get``: a VM being created right now has a
    ``name`` and no ``config`` at all.

    ``VM_PROPERTIES`` names leaves -- ``summary.config.uuid``, not ``summary``
    -- for the reason ``wait`` reads them: a whole property object off a vcsim
    VM will not deserialise under pyVmomi 9, and a PropertyCollector answer for
    a leaf path is what both it and vCenter return happily.
    """
    from pyVmomi import vim, vmodl

    view = content.viewManager.CreateContainerView(
        container=content.rootFolder, type=[vim.VirtualMachine], recursive=True
    )
    try:
        found = list(view.view)
    finally:
        view.Destroy()
    if not found:
        # pyvmomi refuses a filter with an empty object set, and a vCenter with
        # no VMs on it has nothing to be asked about anyway.
        return []
    spec = vmodl.query.PropertyCollector.FilterSpec(
        objectSet=[vmodl.query.PropertyCollector.ObjectSpec(obj=vm) for vm in found],
        propSet=[
            vmodl.query.PropertyCollector.PropertySpec(
                type=vim.VirtualMachine, pathSet=list(VM_PROPERTIES)
            )
        ],
    )
    return [
        {"obj": answer.obj, **{prop.name: prop.val for prop in answer.propSet}}
        for answer in content.propertyCollector.RetrieveContents([spec])
    ]


def datastore_files(datastore: Any, path: str, pattern: str) -> list[str]:
    """Every file under ``path`` matching ``pattern``, as datastore paths.

    ``path`` is vCenter's own spelling -- ``[datastore] folder`` -- and the
    search walks the folders under it, because a seed ISO lives one folder down
    beside the VM it belongs to.

    **A folder that is not there is not an error.** The first deploy against a
    datastore runs before anything has created ``vcows/``, so the fault that
    says so is the ordinary answer and comes back as no files.
    """
    from pyVmomi import vim

    task = datastore.browser.SearchDatastoreSubFolders_Task(
        datastorePath=path,
        searchSpec=vim.host.DatastoreBrowser.SearchSpec(matchPattern=[pattern]),
    )
    try:
        results = wait(task, f"search {path}")
    except VsphereApiError:
        if isinstance(task.info.error, vim.fault.FileNotFound):
            return []
        raise
    return [
        f"{result.folderPath.rstrip('/')}/{found.path}"
        for result in results or ()
        for found in result.file or ()
    ]
