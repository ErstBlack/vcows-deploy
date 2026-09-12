"""Backend registry: an explicit dict, and deliberately nothing cleverer.

No plugin discovery, no entry points, no capability negotiation. Adding a
backend adds one line here and a package beside this file.

Core never reads this module directly -- every entry point takes a ``registry``
argument -- so tests inject their own and the real registry is not a global
dependency.

Three rules hold for every backend package, and are stated here rather than
once per package:

* **No hypervisor library import at module level**, in a backend's ``__init__``
  or in any module it imports at import time. Naming the class here drags that
  file in on every run, including runs on a machine with no such library at all.
  The import belongs inside the methods that need a connection, which is what
  lets ``validate`` and ``render`` run anywhere; ``tests/test_seam.py`` is the
  gate.
* **``render`` is a step of its own** even though ``create`` is its only
  consumer: it is the pure config-to-values half, golden-file tested byte for
  byte, and keeping it separate lets ``create`` be tested against a dict rather
  than against a config.
* **No ``prepare`` in a backend package** unless it has work the inherited one
  does not do. ``Backend.prepare`` builds the seed ISOs through core's
  ``cloudinit`` and forwards what ``preflight`` found; vSphere is the one
  override, for the qcow2-to-VMDK conversion.

Two things a fourth backend would otherwise have to read three packages to
learn. ``Discovered.artifacts`` keys are per-backend and ``prepare`` may index
them unguarded: libvirt sets ``base_volume`` only when a pool was found, while
Proxmox and vSphere always set ``image``. And ``connect`` lives in ``api.py``
for Proxmox and vSphere, and in ``preflight.py`` for libvirt.
"""

from __future__ import annotations

from .base import Backend
from .libvirt import LibvirtBackend
from .proxmox import ProxmoxBackend
from .vsphere import VsphereBackend

REGISTRY: dict[str, Backend] = {
    "libvirt": LibvirtBackend(),
    "proxmox": ProxmoxBackend(),
    "vsphere": VsphereBackend(),
}

__all__ = ["REGISTRY", "Backend"]
