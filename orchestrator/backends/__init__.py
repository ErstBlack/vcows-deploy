"""Backend registry: an explicit dict, and deliberately nothing cleverer.

No plugin discovery, no entry points, no capability negotiation. Adding a
backend adds one line here and a package beside this file.

Core never reads this module directly -- every entry point takes a ``registry``
argument -- so tests inject their own and the real registry is not a global
dependency.

A backend package's ``__init__`` must not import its hypervisor library at module
level; that import belongs inside the methods that need a connection. It is what
lets ``validate`` and ``render`` run on a machine with no libvirt installed at
all, and it is what the fake-backend seam test checks.
"""

from __future__ import annotations

from .base import Backend
from .libvirt import LibvirtBackend
from .proxmox import ProxmoxBackend

REGISTRY: dict[str, Backend] = {
    "libvirt": LibvirtBackend(),
    "proxmox": ProxmoxBackend(),
}

__all__ = ["REGISTRY", "Backend"]
