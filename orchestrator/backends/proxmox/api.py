"""The only module in this package that imports ``proxmoxer``.

Kept to one file for the reason ``tests/test_seam.py`` exists: the package's
``__init__`` names ``ProxmoxBackend``, so importing the registry drags this
backend in on every run -- including runs on a machine that will never talk to a
Proxmox cluster. Nothing here is imported at module scope; ``connect`` imports
``proxmoxer`` inside its own body, exactly as the libvirt backend imports
``libvirt`` inside the methods that hold a connection.

**This module is the calls, not the phases.** ``preflight.py``, ``create.py`` and
``destroy.py`` each drive one phase and each reaches PVE through the ``Session``
and the helpers here, which is what makes ``wait`` a single implementation: every
task any phase starts is checked for its ``exitstatus`` the same way. The uploads
and the VM creation are the exception and stay in ``create.py`` against
``Session.prox`` -- one endpoint apiece, and a wrapper here would only rename
them.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .schema import token_parts

log = logging.getLogger(__name__)

#: Ceiling on one PVE task. The tasks waited on are a stop and a delete on the
#: teardown, and an image upload, a VM create, a resize and a start on the create
#: path. Generous because a stop on a wedged guest is the slow one and a limit
#: short enough to be tidy would abandon a teardown that was going to succeed --
#: 600 was chosen for that stop, and has not been measured against an image
#: import, which is the other candidate for the slowest task here.
TASK_TIMEOUT = 600

#: How often ``Tasks.blocking_status`` asks. Its loop sleeps once more after the
#: task reports stopped, so this is a fixed tax on every wait -- the upload, the
#: create, the resize and the start of a create, and the stop and delete of a
#: teardown. One second is small enough not to matter against any of them.
POLL_INTERVAL = 1


class ProxmoxApiError(Exception):
    """A call to the PVE API failed, or a task it started did."""


@dataclass(frozen=True)
class Session:
    """proxmoxer's client, plus the target it is bound to.

    Opaque to core, which passes it from ``preflight`` to ``destroy`` without
    reading it -- the same contract libvirt's ``virConnect`` has.
    """

    prox: Any
    node: str
    datastore: str
    import_datastore: str


def _endpoint_host(endpoint: str) -> str:
    """``host`` or ``host:port`` for proxmoxer, out of the configured URL.

    proxmoxer splits a port off the host itself and defaults to 8006, so the
    port is passed through in the string rather than as a separate argument.
    ``_check_target`` has already refused anything with credentials, a query or a
    path, so what reaches here is a bare origin.
    """
    parts = urlsplit(endpoint)
    return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)


@contextmanager
def connect(cfg: dict):
    """Open a PVE API session against the configured endpoint.

    **The credential is read here and nowhere else.** ``target.proxmox`` carries
    either a token, which ``schema.token_parts`` splits into the three fields
    proxmoxer wants, or a user and a password. Neither a token's secret half nor
    a password is logged or returned. The log line below names the endpoint and
    the *user*, because that is what an operator debugging a 401 needs and it is
    not the credential.
    """
    from proxmoxer import ProxmoxAPI
    from proxmoxer.core import AuthenticationError, ResourceException

    target = cfg["target"]["proxmox"]
    token = target.get("token")
    if token is not None:
        parts = token_parts(token)
        if parts is None:
            # `validate` reports this as a Problem and every verb runs it, so
            # reaching here means the config changed underneath the run.
            raise ProxmoxApiError(
                "target.proxmox.token is malformed; expected "
                "'user@realm!tokenid=<secret>'"
            )
        user = parts.group("user")
        auth = {
            "user": user,
            "token_name": parts.group("name"),
            "token_value": parts.group("secret"),
        }
    else:
        user = target["user"]
        auth = {"user": user, "password": target["password"]}

    # A bool or a path: proxmoxer hands `verify_ssl` to requests' `verify=`
    # unchanged, and requests takes a CA bundle path there as readily as True.
    verify = False if target.get("insecure") else target.get("ca_file", True)

    host = _endpoint_host(target["endpoint"])
    log.info("connecting to %s as %s", host, user)
    prox = None
    try:
        # Constructed inside the `try` because password auth fetches a ticket
        # here rather than on first use, so AuthenticationError can be raised by
        # the constructor itself.
        prox = ProxmoxAPI(host, verify_ssl=verify, timeout=30, **auth)
        yield Session(
            prox=prox,
            node=target["node"],
            datastore=target["datastore"],
            import_datastore=target["import_datastore"],
        )
    except AuthenticationError as exc:
        # proxmoxer raises this for a credential the cluster rejects. Re-raised
        # as our own type so callers do not import proxmoxer to catch it -- the
        # same reason `cli` never imports the libvirt backend's error type.
        raise ProxmoxApiError(
            f"{host} rejected the credentials in target.proxmox: {exc}"
        ) from exc
    except ResourceException as exc:
        raise ProxmoxApiError(f"{host}: {exc}") from exc
    finally:
        if prox is not None:
            _close(prox)


def _close(prox: Any) -> None:
    """Best effort. proxmoxer exposes no ``close``, and this is a short-lived CLI.

    Reached through the backend's private session rather than left undone,
    because a run that opens a connection and never releases its socket is the
    kind of thing that only shows up under a supervisor that reuses the process.
    Guarded, because it is private and may move.
    """
    try:
        prox._backend.get_session().close()
    except Exception as exc:
        log.debug("could not close the API session: %s", exc)


def wait(session: Session, upid: str, what: str) -> None:
    """Block until one PVE task finishes, and refuse anything but success.

    **``Tasks.blocking_status`` alone is not enough.** It returns when the task
    stops, which includes stopping badly: a failed delete and a successful one
    both leave ``status: stopped``, and the two are told apart by ``exitstatus``.
    Taking the first as success is exactly the silent partial teardown
    ``Outcome`` exists to prevent. It also returns ``None`` on timeout rather
    than raising, which reads as "no status" and would otherwise fall through.
    """
    from proxmoxer.tools import Tasks

    status = Tasks.blocking_status(
        session.prox, upid, timeout=TASK_TIMEOUT, polling_interval=POLL_INTERVAL
    )
    if status is None:
        raise ProxmoxApiError(
            f"{what}: task {upid} had not finished after {TASK_TIMEOUT}s. It may "
            f"still be running on the node; check it before retrying."
        )
    exit_status = status.get("exitstatus") or "<none>"
    if exit_status != "OK":
        raise ProxmoxApiError(f"{what}: task {upid} ended with {exit_status!r}")
    log.debug("%s: task %s ok", what, upid)


def cluster_vms(session: Session) -> list[dict]:
    """Every QEMU VM the token can see, on every node.

    Cluster-wide rather than node-scoped **on purpose**, even though vcows only
    ever creates on one node. A VM that was migrated after vcows created it is
    still ours, and a node-scoped list would not find it -- so ``decide`` would
    see the name as absent, plan a create, and PVE would happily make a second VM
    with the same name, because unlike libvirt it does not require names to be
    unique. Discovery is cluster-wide; creation stays on ``target.proxmox.node``.
    """
    found = session.prox.cluster.resources.get(type="vm")
    return [r for r in found if r.get("type") == "qemu"]


def vm_config(session: Session, node: str, vmid: int | str) -> dict:
    """One VM's config, which is the only place ``description`` is returned.

    ``/cluster/resources`` carries the name, the node and the status but not the
    notes, so discovering markers costs one call per VM. That is the shape of the
    API rather than a choice.
    """
    return session.prox.nodes(node).qemu(vmid).config.get()


def storage(session: Session, name: str) -> dict | None:
    """One storage's entry as this node sees it, or None if the node has no such."""
    for entry in session.prox.nodes(session.node).storage.get():
        if entry.get("storage") == name:
            return entry
    return None


def storage_content(session: Session, name: str, content: str) -> list[dict]:
    """What is already in a storage under one content type."""
    return session.prox.nodes(session.node).storage(name).content.get(content=content)


def is_running(session: Session, node: str, vmid: int | str) -> bool:
    return (
        session.prox.nodes(node).qemu(vmid).status.current.get().get("status")
        == "running"
    )


def stop_vm(session: Session, node: str, vmid: int | str) -> None:
    """Stop, not shutdown. The guest is about to be deleted.

    ``shutdown`` asks the guest politely and waits for an ACPI response that a
    hung or agent-less guest never sends, which turns a teardown into a hang.
    ``destroy`` is a teardown; there is nothing to flush.
    """
    upid = session.prox.nodes(node).qemu(vmid).status.stop.post()
    wait(session, upid, f"stop {vmid}")


def delete_vm(session: Session, node: str, vmid: int | str) -> None:
    """Delete the VM and the disks PVE knows belong to it.

    ``purge=1`` also removes it from any backup job and HA configuration, which
    is what stops a deleted VM from leaving a job that fails nightly.
    ``destroy-unreferenced-disks=1`` collects disks that name this vmid but are
    not in its config -- the residue of a half-finished earlier run.
    """
    upid = (
        session.prox.nodes(node)
        .qemu(vmid)
        .delete(purge=1, **{"destroy-unreferenced-disks": 1})
    )
    wait(session, upid, f"delete {vmid}")


def delete_volume(session: Session, volid: str) -> None:
    """Remove one file from a storage. Used for seed ISOs, never for the image."""
    session.prox.nodes(session.node).storage(session.import_datastore).content(
        volid
    ).delete()
