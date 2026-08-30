"""Read a qcow2 header. Replaces ``qemu-img info`` for the one thing we need it for.

Not in findings.md §3's layout, which enumerated the significant modules rather
than forbidding others. It is its own file because it is one pure function with
one purpose, independently testable, and putting it in ``config.py`` would mix
schema composition with binary parsing.

Why not shell out to ``qemu-img``: it is 14.2 MB and GPL-2.0-only -- the most
constrained licence that would be in the bundle, with no upgrade path. The only
in-container use is the "disk_gb >= golden image virtual size" check, because
every volume operation (create, upload, overlay via ``backing_store``) happens on
the *hypervisor* through libvirt. Verified byte-for-byte against
``qemu-img info --output=json``; see docs/spikes.md.
"""

from __future__ import annotations

import struct
from pathlib import Path

QCOW_MAGIC = b"QFI\xfb"

# qcow2 header, v2 and v3 alike:
#    0  magic[4]              4  version[4]
#    8  backing_file_offset[8]
#   16  backing_file_size[4] 20  cluster_bits[4]
#   24  size[8]  <- virtual size, big-endian u64
_HEADER_LEN = 32
_SIZE_OFFSET = 24


class NotAQcow2(ValueError):
    """The file is not a qcow2 image we can read."""


def virtual_size(path: str | Path) -> int:
    """Virtual size in bytes, as ``qemu-img info`` reports it."""
    with open(path, "rb") as fh:
        header = fh.read(_HEADER_LEN)
    if len(header) < _HEADER_LEN:
        raise NotAQcow2(f"{path}: too short to be a qcow2 ({len(header)} bytes)")
    if header[:4] != QCOW_MAGIC:
        raise NotAQcow2(f"{path}: bad magic {header[:4]!r}, expected {QCOW_MAGIC!r}")
    (version,) = struct.unpack(">I", header[4:8])
    if version not in (2, 3):
        raise NotAQcow2(f"{path}: qcow2 version {version} is not supported")
    (size,) = struct.unpack(">Q", header[_SIZE_OFFSET : _SIZE_OFFSET + 8])
    return size
