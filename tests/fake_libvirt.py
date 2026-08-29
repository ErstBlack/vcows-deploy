"""A hypervisor-shaped stand-in, for the parts of preflight and destroy that hold
a connection.

This is not the fake *backend* -- that one proves the seam by having no hypervisor
semantics at all. This one has hypervisor semantics deliberately, because the
things worth testing here are exactly the libvirt-specific ones: that the pool is
refreshed before any path is resolved, that a domain is stopped before it is
undefined, and that a rejected flag mask sheds only the bits it is allowed to shed.

The errors are real ``libvirt.libvirtError`` instances with ``err`` filled in, so
``get_error_code()`` returns what the code under test matches on. Nothing here
matches on a message, and neither does the code -- the NVRAM refusal has been
reworded three times upstream.
"""

from __future__ import annotations

from typing import Any

import libvirt


def lv_error(code: int, message: str = "fake") -> libvirt.libvirtError:
    exc = libvirt.libvirtError(message)
    exc.err = (code, 0, message, 2, "", "", "", -1, -1)
    return exc


class FakeVolume:
    def __init__(self, pool: FakePool, name: str, xml: str = ""):
        self.pool = pool
        self._name = name
        self._xml = xml

    def name(self) -> str:
        return self._name

    def XMLDesc(self, _flags: int = 0) -> str:
        return self._xml

    def delete(self, flags: int = 0) -> None:
        assert flags == 0, "the dir/fs backend accepts no delete flags"
        self.pool.deleted.append(self._name)
        del self.pool.volumes[self._name]


class FakePool:
    def __init__(
        self,
        name: str,
        volumes: dict[str, str],
        active: bool = True,
        path: str = "/pool",
    ):
        self._name = name
        #: The pool's target directory. Readable on an inactive pool, which is the
        #: only way to tell whether one holds a disk this teardown needs.
        self._path = path
        #: name -> XMLDesc. Keys not in `visible` are on disk but not in libvirt's
        #: in-memory cache, which is the state D35 exists for.
        self.volumes = dict(volumes)
        self.visible: set[str] = set()
        self._active = active
        self.refreshed = 0
        self.deleted: list[str] = []

    def name(self) -> str:
        return self._name

    def isActive(self) -> bool:
        return self._active

    def XMLDesc(self, _flags: int = 0) -> str:
        return (
            f"<pool type='dir'><name>{self._name}</name>"
            f"<target><path>{self._path}</path></target></pool>"
        )

    def refresh(self, _flags: int = 0) -> None:
        self.refreshed += 1
        self.visible = set(self.volumes)

    def listAllVolumes(self, _flags: int = 0) -> list[FakeVolume]:
        return [FakeVolume(self, n, self.volumes[n]) for n in sorted(self.visible)]

    def storageVolLookupByName(self, name: str) -> FakeVolume:
        if name not in self.visible:
            raise lv_error(50, f"no storage vol with matching name '{name}'")
        return FakeVolume(self, name, self.volumes[name])


class FakeDomain:
    def __init__(self, name: str, uuid: str, xml: str, active: bool = False):
        self._name = name
        self._uuid = uuid
        self._xml = xml
        self.active = active
        self.log: list[str] = []
        #: Flag bits this daemon refuses, as `virCheckFlags` would.
        self.rejects = 0
        self.stop_error: libvirt.libvirtError | None = None

    def name(self) -> str:
        return self._name

    def UUIDString(self) -> str:
        return self._uuid

    def XMLDesc(self, _flags: int = 0) -> str:
        return self._xml

    def redefine(self, xml: str) -> None:
        """Another operator rewrote this domain while we were not looking.

        The window destroy has to survive: preflight read one document, an
        operator answered a prompt, and the domain is not the same afterwards.
        """
        self._xml = xml

    def isActive(self) -> bool:
        return self.active

    def destroyFlags(self, _flags: int = 0) -> None:
        self.log.append("destroy")
        if self.stop_error is not None:
            raise self.stop_error
        if not self.active:
            raise lv_error(55, "domain is not running")
        self.active = False

    def undefineFlags(self, flags: int) -> None:
        self.log.append(f"undefine:{flags}")
        if flags & self.rejects:
            raise lv_error(
                8, f"unsupported flags (0x{flags & self.rejects:x}) in function fake"
            )


class FakeConnection:
    def __init__(
        self,
        domains: list[FakeDomain] | None = None,
        pools: list[FakePool] | None = None,
        networks: dict[str, str] | None = None,
        leases: dict[str, list[dict]] | None = None,
        version: int = 12000000,
    ):
        self.domains = domains or []
        self.pools = pools or []
        self.networks = networks or {}
        self.leases = leases or {}
        self.version = version
        self.closed = False
        #: Raised by `lookupByUUIDString` instead of the NO_DOMAIN default, for
        #: the failures that are not "already gone". Mirrors `FakeDomain.stop_error`.
        self.lookup_error: libvirt.libvirtError | None = None

    # -- domains ---------------------------------------------------------

    def listAllDomains(self, _flags: int = 0) -> list[FakeDomain]:
        return list(self.domains)

    def lookupByUUIDString(self, uuid: str) -> FakeDomain:
        if self.lookup_error is not None:
            raise self.lookup_error
        for dom in self.domains:
            if dom.UUIDString() == uuid:
                return dom
        raise lv_error(42, f"no domain with matching uuid '{uuid}'")

    # -- storage ---------------------------------------------------------

    def listAllStoragePools(self, _flags: int = 0) -> list[FakePool]:
        return list(self.pools)

    def storagePoolLookupByName(self, name: str) -> FakePool:
        for pool in self.pools:
            if pool.name() == name:
                return pool
        raise lv_error(49, f"no storage pool with matching name '{name}'")

    def storageVolLookupByPath(self, path: str) -> FakeVolume:
        """Resolves out of the *cache*, exactly as libvirt does.

        A volume present in `pool.volumes` but absent from `pool.visible` is on disk
        and invisible -- the rig state that makes `pool.refresh()` mandatory rather
        than defensive.
        """
        for pool in self.pools:
            for name in pool.visible:
                if path.endswith("/" + name):
                    return FakeVolume(pool, name, pool.volumes[name])
        raise lv_error(50, f"no storage vol with matching path '{path}'")

    # -- networks --------------------------------------------------------

    def networkLookupByName(self, name: str) -> Any:
        if name not in self.networks:
            raise lv_error(43, f"no network with matching name '{name}'")
        return FakeNetwork(name, self.networks[name], self.leases.get(name, []))

    # -- misc ------------------------------------------------------------

    def getLibVersion(self) -> int:
        return self.version

    def close(self) -> None:
        self.closed = True


class FakeNetwork:
    def __init__(self, name: str, xml: str, leases: list[dict]):
        self._name = name
        self._xml = xml
        self._leases = leases

    def XMLDesc(self, _flags: int = 0) -> str:
        return self._xml

    def DHCPLeases(self) -> list[dict]:
        return list(self._leases)
