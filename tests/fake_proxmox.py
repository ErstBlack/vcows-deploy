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


def upid(node: str = "pve1", kind: str = "qmstop", ident: str = "100") -> str:
    """A UPID proxmoxer's ``Tasks.decode_upid`` accepts.

    Nine colon-separated segments, three of them hex. Built here rather than
    hardcoded in each test because ``decode_upid`` raises ``AssertionError`` on a
    malformed one, which reads as a broken test rather than a broken fake.

    ``ident`` is PVE's own id field: a vmid for a task on a VM, and the file name
    for an upload. It is what makes two UPIDs from one run distinguishable, which
    is how a test can tell that every task started was also waited on.
    """
    return f"UPID:{node}:0000ABCD:00000000:6600ABCD:{kind}:{ident}:vcows@pve!deploy:"


#: How long the fake plays along with a task that never finishes. Small, because
#: the only caller that reaches it has already had its ceiling cut to zero.
MAX_POLLS = 50

#: The `size` a content row reports for a volume no test has given one. The
#: value is arbitrary: a test that compares it against a local file sets both
#: ends, and every other test's golden image is not on this machine, so
#: preflight warns about the local file and never reaches the comparison.
VOLUME_SIZE = 4096


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

    def put(self, **kw: Any) -> Any:
        return self._world.dispatch("put", self._parts, kw)

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
        #: The nodes this cluster has. A path naming any other one errors at
        #: PVE rather than answering emptily, so the fake refuses it instead of
        #: serving `nodes/None/...` as if it were the configured node. Spelled
        #: `node_names` and not `nodes` because `prox.nodes(...)` resolves
        #: through `__getattr__`, which only fires for a missing attribute.
        self.node_names = {"pve1", *(node for node, _vmid in self.vms)}
        #: What `/nodes/{node}/storage` returns. `content` is PVE's own
        #: comma-separated string, not a list -- parsing it is the code's job.
        self.storages = storages if storages is not None else []
        #: storage -> content type -> volids.
        self.content = content or {}
        #: volid -> the `size` its content row carries, in bytes. Anything not
        #: named here reports VOLUME_SIZE. None means the row carries no size
        #: at all, which is the shape preflight warns about rather than reading
        #: as a match.
        self.sizes: dict[str, int | None] = {}
        #: Every resolved path, in order. The endpoint assertions read this.
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        #: One entry per upload: the storage, and every parameter the call was
        #: handed with the file handle rendered as its `name` -- which is the
        #: name PVE stores the file under, and the one thing a caller can get
        #: wrong without the request failing.
        self.uploads: list[dict] = []
        #: Every UPID this fake handed out, and every one it was asked the status
        #: of. A task started and not waited on is a create that reports success
        #: before PVE has finished, so the tests compare the two.
        self.upids: list[str] = []
        self.waited: list[str] = []
        #: What `import-from` gives a disk: the golden image's own size, in GiB,
        #: which is why `create_vm` has to resize afterwards.
        self.imported_gb = 10
        #: PVE 8 answers a resize with a UPID. Older ones answer with nothing,
        #: having already done the work, and a caller that waits on that answer
        #: turns a working cluster into a failed deploy.
        self.resize_returns_upid = True
        #: Raw `/cluster/resources` rows, when a test needs a shape `vms` cannot
        #: express -- a row with no vmid, or an LXC container beside the VMs.
        self.resources: list[dict] | None = None
        #: Paths whose task should report failure rather than "OK".
        self.task_fails: set[str] = set()
        #: Never finish; `wait` must time out rather than hang forever. The fake
        #: stops answering after MAX_POLLS so a wait that carries no ceiling
        #: fails the test rather than running until something else kills it.
        self.task_never_finishes = False
        self.polls = 0

        self.resources_error: Exception | None = None
        self.config_error: Exception | None = None
        self.storage_error: Exception | None = None
        self.content_error: Exception | None = None
        self.stop_error: Exception | None = None
        self.volume_delete_error: Exception | None = None
        self.nextid_error: Exception | None = None

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
            # `type` is a filter over every resource kind PVE knows -- storages
            # and nodes included -- so a call that drops it or spells it wrong
            # gets rows this fake has no shape for. Refused rather than answered
            # as though the filter had been sent.
            if kw.get("type") != "vm":
                raise ResourceException(f"unknown resource type {kw.get('type')!r}")
            return self._resources()

        # PVE's own answer is the lowest free id at or above 100.
        if parts == ("cluster", "nextid"):
            self._raise(self.nextid_error)
            taken = {vmid for _node, vmid in self.vms}
            return next(str(n) for n in range(100, 1000) if str(n) not in taken)

        if parts[:1] == ("nodes",):
            return self._node(verb, parts[1], parts[2:], kw)

        raise ResourceException(f"fake has no route for {verb} {'/'.join(parts)}")

    def _node(self, verb: str, node: str, rest: tuple[str, ...], kw: dict) -> Any:
        if node not in self.node_names:
            raise ResourceException(f"no such node {node!r}")

        # /nodes/{node}/storage
        if rest == ("storage",):
            self._raise(self.storage_error)
            return list(self.storages)

        # /nodes/{node}/storage/{name}/upload
        if len(rest) == 3 and rest[0] == "storage" and rest[2] == "upload":
            content = kw["content"]
            name = getattr(kw["filename"], "name", kw["filename"])
            self.uploads.append(
                {
                    "storage": rest[1],
                    **{k: getattr(v, "name", v) for k, v in kw.items()},
                }
            )
            self.content.setdefault(rest[1], {}).setdefault(content, []).append(
                f"{rest[1]}:{content}/{name}"
            )
            return self._upid(node, "imgcopy", str(name))

        # /nodes/{node}/storage/{name}/content
        if len(rest) == 3 and rest[0] == "storage" and rest[2] == "content":
            self._raise(self.content_error)
            wanted = kw.get("content")
            store = self.content.get(rest[1], {})
            return [self._content_row(v) for v in store.get(wanted, [])]

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
            self.waited.append(rest[1])
            if self.task_never_finishes:
                self.polls += 1
                if self.polls > MAX_POLLS:
                    raise ResourceException(
                        f"{rest[1]} has been polled {self.polls} times; the "
                        f"caller is waiting on it without a ceiling"
                    )
                return {"status": "running"}
            failed = rest[1] in self.task_fails
            return {
                "status": "stopped",
                "exitstatus": "task failed somehow" if failed else "OK",
            }

        # POST /nodes/{node}/qemu -- the create, which carries the vmid itself.
        if rest == ("qemu",) and verb == "post":
            vmid = str(kw["vmid"])
            if (node, vmid) in self.vms:
                raise ResourceException(f"VM {vmid} already exists on {node}")
            # `import-from` gives the disk the image's size and PVE rewrites the
            # config to record it, which is the number `create_vm` reads back.
            self.vms[(node, vmid)] = {
                **kw,
                "scsi0": f"{kw['scsi0']},size={self.imported_gb}G",
            }
            return self._upid(node, "qmcreate", vmid)

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
            if key not in self.vms:
                raise ResourceException(f"VM {vmid} does not exist on {node}")
            return {"status": self.vms[key].get("status", "stopped")}

        if rest == ("resize",) and verb == "put":
            disk, size = kw["disk"], kw["size"]
            head, *fields = self.vms[key][disk].split(",")
            kept = [f for f in fields if not f.startswith("size=")]
            self.vms[key][disk] = ",".join([head, *kept, f"size={size}"])
            return (
                self._upid(node, "qmresize", vmid) if self.resize_returns_upid else None
            )

        if rest == ("status", "start"):
            self.vms[key]["status"] = "running"
            return self._upid(node, "qmstart", vmid)

        if rest == ("status", "stop"):
            self._raise(self.stop_error)
            self.vms[key]["status"] = "stopped"
            return upid(node, "qmstop", vmid)

        # DELETE /nodes/{node}/qemu/{vmid}
        if rest == () and verb == "delete":
            # Both default to 0 at the API. A delete that omits either one leaves
            # the VM in its backup jobs, or leaves disks that name its vmid, so
            # the fake refuses it the way FakeVolume.delete refuses a wrong flag.
            if (kw.get("purge"), kw.get("destroy-unreferenced-disks")) != (1, 1):
                raise ResourceException(
                    f"delete of {vmid} must purge and collect unreferenced "
                    f"disks; got {kw!r}"
                )
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

    def _content_row(self, volid: str) -> dict:
        """One row of a content listing, as PVE reports it: a volid and a size."""
        size = self.sizes.get(volid, VOLUME_SIZE)
        return {"volid": volid} if size is None else {"volid": volid, "size": size}

    def _upid(self, node: str, kind: str, ident: str) -> str:
        """Hand out a UPID and remember that it was handed out."""
        made = upid(node, kind, ident)
        self.upids.append(made)
        return made

    @staticmethod
    def _raise(exc: Exception | None) -> None:
        if exc is not None:
            raise exc
