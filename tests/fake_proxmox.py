"""A PVE-shaped stand-in for proxmoxer's chaining client.

This is not the fake *backend* -- that one proves the seam by having no
hypervisor semantics at all. This one has PVE semantics deliberately, and in
particular it dispatches on the **API path**, so a test fails if
``orchestrator/backends/proxmox/api.py`` calls the wrong endpoint. That is the
half a wrapper-level fake cannot check, and the half most likely to be wrong
against a cluster nobody has run this against yet.

proxmoxer builds a request by attribute access and calls -- ``prox.nodes("pve1")
.qemu(100).config.get()`` -- so the fake mirrors that shape and records the
resolved path. ``FakeProxmox.calls`` is the list of every path reached, which is
what the endpoint assertions read.

Every call a caller might have to survive carries a settable ``*_error``
attribute, ``None`` by default and raised when the matching path is reached. One
convention rather than several, because the question these tests ask is always
the same one: what does the code do when *this* call is the one that fails.
"""

from __future__ import annotations

from typing import Any


def upid(node: str = "pve1", kind: str = "qmstop", vmid: str = "100") -> str:
    """A UPID proxmoxer's ``Tasks.decode_upid`` accepts.

    Nine colon-separated segments, three of them hex. Built here rather than
    hardcoded in each test because ``decode_upid`` raises ``AssertionError`` on a
    malformed one, which reads as a broken test rather than a broken fake.
    """
    return f"UPID:{node}:0000ABCD:00000000:6600ABCD:{kind}:{vmid}:vcows@pve!deploy:"


class ResourceException(Exception):
    """Stands in for proxmoxer's. Raised where the real client would raise."""


class _Path:
    """One partially-built API path. Every attribute or call extends it."""

    def __init__(self, world: FakeProxmox, parts: tuple[str, ...]):
        self._world = world
        self._parts = parts

    def __getattr__(self, name: str) -> _Path:
        if name.startswith("_"):
            raise AttributeError(name)
        return _Path(self._world, (*self._parts, name))

    def __call__(self, *args: Any, **_kw: Any) -> _Path:
        return _Path(self._world, (*self._parts, *(str(a) for a in args)))

    def get(self, **kw: Any) -> Any:
        return self._world.dispatch("get", self._parts, kw)

    def post(self, **kw: Any) -> Any:
        return self._world.dispatch("post", self._parts, kw)

    def delete(self, **kw: Any) -> Any:
        return self._world.dispatch("delete", self._parts, kw)


class FakeProxmox:
    """A cluster, as the endpoints ``api.py`` uses would report it.

    ``vms`` is keyed by ``(node, vmid)`` and each value is the VM's *config* --
    the mapping ``/nodes/{node}/qemu/{vmid}/config`` returns, which is the only
    place ``description`` appears. ``name`` and ``status`` are lifted out of it
    into the ``/cluster/resources`` answer, mirroring how PVE splits them.
    """

    def __init__(
        self,
        vms: dict[tuple[str, str], dict] | None = None,
        storages: list[dict] | None = None,
        content: dict[str, dict[str, list[str]]] | None = None,
    ):
        self.vms = dict(vms or {})
        #: What `/nodes/{node}/storage` returns. `content` is PVE's own
        #: comma-separated string, not a list -- parsing it is the code's job.
        self.storages = storages if storages is not None else []
        #: storage -> content type -> volids.
        self.content = content or {}
        #: Every resolved path, in order. The endpoint assertions read this.
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        #: Raw `/cluster/resources` rows, when a test needs a shape `vms` cannot
        #: express -- a row with no vmid, or an LXC container beside the VMs.
        self.resources: list[dict] | None = None
        #: Paths whose task should report failure rather than "OK".
        self.task_fails: set[str] = set()
        #: Never finish; `wait` must time out rather than hang forever.
        self.task_never_finishes = False

        self.resources_error: Exception | None = None
        self.config_error: Exception | None = None
        self.storage_error: Exception | None = None
        self.content_error: Exception | None = None
        self.status_error: Exception | None = None
        self.stop_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.volume_delete_error: Exception | None = None

    # -- the chaining entry point ----------------------------------------

    def __getattr__(self, name: str) -> _Path:
        if name.startswith("_"):
            raise AttributeError(name)
        return _Path(self, (name,))

    # -- dispatch ---------------------------------------------------------

    def dispatch(self, verb: str, parts: tuple[str, ...], kw: dict) -> Any:
        self.calls.append((verb, parts))

        if parts == ("cluster", "resources"):
            self._raise(self.resources_error)
            return self._resources()

        if parts[:1] == ("nodes",):
            return self._node(verb, parts[1], parts[2:], kw)

        raise ResourceException(f"fake has no route for {verb} {'/'.join(parts)}")

    def _node(self, verb: str, node: str, rest: tuple[str, ...], kw: dict) -> Any:
        # /nodes/{node}/storage
        if rest == ("storage",):
            self._raise(self.storage_error)
            return list(self.storages)

        # /nodes/{node}/storage/{name}/content
        if len(rest) == 3 and rest[0] == "storage" and rest[2] == "content":
            self._raise(self.content_error)
            wanted = kw.get("content")
            store = self.content.get(rest[1], {})
            return [{"volid": v} for v in store.get(wanted, [])]

        # /nodes/{node}/storage/{name}/content/{volid}
        if len(rest) == 4 and rest[0] == "storage" and rest[2] == "content":
            self._raise(self.volume_delete_error)
            store = self.content.get(rest[1], {})
            for kind, volids in store.items():
                if rest[3] in volids:
                    store[kind] = [v for v in volids if v != rest[3]]
                    return None
            raise ResourceException(f"no such volume {rest[3]}")

        # /nodes/{node}/tasks/{upid}/status
        if len(rest) == 3 and rest[0] == "tasks" and rest[2] == "status":
            if self.task_never_finishes:
                return {"status": "running"}
            failed = rest[1] in self.task_fails
            return {
                "status": "stopped",
                "exitstatus": "task failed somehow" if failed else "OK",
            }

        if rest[:1] == ("qemu",):
            return self._qemu(verb, node, rest[1], rest[2:], kw)

        raise ResourceException(
            f"fake has no route for {verb} nodes/{node}/{'/'.join(rest)}"
        )

    def _qemu(
        self, verb: str, node: str, vmid: str, rest: tuple[str, ...], kw: dict
    ) -> Any:
        key = (node, vmid)

        if rest == ("config",):
            self._raise(self.config_error)
            if key not in self.vms:
                raise ResourceException(f"VM {vmid} does not exist on {node}")
            return dict(self.vms[key])

        if rest == ("status", "current"):
            self._raise(self.status_error)
            if key not in self.vms:
                raise ResourceException(f"VM {vmid} does not exist on {node}")
            return {"status": self.vms[key].get("status", "stopped")}

        if rest == ("status", "stop"):
            self._raise(self.stop_error)
            self.vms[key]["status"] = "stopped"
            return upid(node, "qmstop", vmid)

        # DELETE /nodes/{node}/qemu/{vmid}
        if rest == () and verb == "delete":
            self._raise(self.delete_error)
            if key not in self.vms:
                raise ResourceException(f"VM {vmid} does not exist on {node}")
            # PVE refuses to delete a running VM. Modelled, because the ordering
            # guarantee -- stop before delete -- is the thing worth testing.
            if self.vms[key].get("status") == "running":
                raise ResourceException(f"VM {vmid} is running")
            del self.vms[key]
            return upid(node, "qmdestroy", vmid)

        raise ResourceException(
            f"fake has no route for {verb} qemu/{vmid}/{'/'.join(rest)}"
        )

    def _resources(self) -> list[dict]:
        if self.resources is not None:
            return self.resources
        return [
            {
                "type": "qemu",
                "node": node,
                "vmid": int(vmid),
                "name": config.get("name", ""),
                "status": config.get("status", "stopped"),
            }
            for (node, vmid), config in sorted(self.vms.items())
        ]

    @staticmethod
    def _raise(exc: Exception | None) -> None:
        if exc is not None:
            raise exc
