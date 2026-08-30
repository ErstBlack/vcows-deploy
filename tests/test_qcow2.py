"""The qcow2 header read that replaces qemu-img."""

from __future__ import annotations

import struct

import pytest

from orchestrator.qcow2 import NotAQcow2, virtual_size


def make_qcow2(path, size_bytes, version=3, magic=b"QFI\xfb"):
    """A header-only qcow2. Enough for virtual_size, which reads 32 bytes."""
    header = bytearray(32)
    header[0:4] = magic
    header[4:8] = struct.pack(">I", version)
    header[24:32] = struct.pack(">Q", size_bytes)
    path.write_bytes(bytes(header) + b"\x00" * 512)
    return path


@pytest.mark.parametrize("gib", [1, 10, 20, 100])
def test_reads_virtual_size(tmp_path, gib):
    p = make_qcow2(tmp_path / "d.qcow2", gib * 2**30)
    assert virtual_size(p) == gib * 2**30


def test_matches_the_value_qemu_img_reported(tmp_path):
    """Pinned from docs/spikes.md, where this was checked against
    `qemu-img info --output=json` on a real 20 GiB image."""
    p = make_qcow2(tmp_path / "d.qcow2", 21474836480)
    assert virtual_size(p) == 21474836480


@pytest.mark.parametrize("version", [2, 3])
def test_accepts_both_qcow2_versions(tmp_path, version):
    p = make_qcow2(tmp_path / "d.qcow2", 2**30, version=version)
    assert virtual_size(p) == 2**30


def test_rejects_wrong_magic(tmp_path):
    p = make_qcow2(tmp_path / "d.raw", 2**30, magic=b"\x00\x00\x00\x00")
    with pytest.raises(NotAQcow2, match="bad magic"):
        virtual_size(p)


def test_rejects_qcow1(tmp_path):
    """qcow v1 has a different header layout; reading offset 24 would return
    garbage rather than fail, so the version check has to be explicit."""
    p = make_qcow2(tmp_path / "d.qcow", 2**30, version=1)
    with pytest.raises(NotAQcow2, match="version 1"):
        virtual_size(p)


def test_rejects_truncated_file(tmp_path):
    p = tmp_path / "short.qcow2"
    p.write_bytes(b"QFI\xfb" + b"\x00" * 4)
    with pytest.raises(NotAQcow2, match="too short"):
        virtual_size(p)
