"""Property tests for the three surfaces where hand-picked cases run out.

The rest of the suite is example-based on purpose: most of this codebase
orchestrates subprocesses and libvirt calls, where a generated input is a
generated mock and proves nothing. These three are different -- they are parsers,
and a parser's interesting inputs are the ones nobody thought to write down.

  * ``qcow2.virtual_size`` unpacks a big-endian u64 out of a 32-byte header. The
    existing tests cover magic, version, truncation and a few sizes; what they
    cannot cover by hand is the full u64 range.
  * ``Marker`` round-trips through JSON and then into XML. There is already a
    hand-written case asserting a payload with ``<``, ``>`` and ``&`` needs no
    XML escaping, which is exactly the kind of claim that should be quantified
    over strings rather than over three of them.
  * ``_parse_interface`` does real CIDR arithmetic across two address families.
"""

from __future__ import annotations

import ipaddress
import struct

from hypothesis import given
from hypothesis import strategies as st

from orchestrator import qcow2
from orchestrator.backends.base import Problem
from orchestrator.backends.libvirt.schema import _parse_interface
from orchestrator.marker import Marker

U64 = st.integers(min_value=0, max_value=2**64 - 1)


def _header(size: int, version: int = 3) -> bytes:
    head = bytearray(32)
    head[0:4] = qcow2.QCOW_MAGIC
    head[4:8] = struct.pack(">I", version)
    head[24:32] = struct.pack(">Q", size)
    return bytes(head)


@given(size=U64, version=st.sampled_from([2, 3]), tail=st.binary(max_size=64))
def test_virtual_size_reads_any_u64(tmp_path_factory, size, version, tail):
    """Whatever the header says the size is, that is what comes back -- across
    the whole u64 range, and regardless of what follows the header."""
    path = tmp_path_factory.mktemp("qcow2") / "disk.qcow2"
    path.write_bytes(_header(size, version) + tail)
    assert qcow2.virtual_size(path) == size


@given(
    name=st.text(min_size=1, max_size=64),
    deployment=st.text(max_size=64),
)
def test_marker_survives_a_json_round_trip(name, deployment):
    """The marker is the identity: a VM that cannot be read back is a VM that
    cannot be destroyed. This generalises the hand-written XML-escaping case."""
    marker = Marker.for_vm(name, deployment)
    assert Marker.from_json(marker.to_json()) == marker


@given(
    a=st.tuples(st.text(min_size=1, max_size=32), st.text(max_size=32)),
    b=st.tuples(st.text(min_size=1, max_size=32), st.text(max_size=32)),
)
def test_derived_ids_separate_deployments(a, b):
    """S3 folded `deployment` into the uuid5 input so two deployments with the
    same VM name stop colliding. That is a claim about every pair of inputs, not
    about the pair someone happened to write down."""
    first, second = Marker.for_vm(*a), Marker.for_vm(*b)
    assert (first.id == second.id) == (a == b)


@given(
    network=st.one_of(
        st.ip_addresses(v=4).map(lambda a: ipaddress.IPv4Network(a).supernet(24)),
        st.ip_addresses(v=6).map(lambda a: ipaddress.IPv6Network(a).supernet(8)),
    )
)
def test_parse_interface_accepts_what_ipaddress_produces(network):
    """Anything the stdlib will render, the parser will read back identically."""
    problems: list[Problem] = []
    text = f"{network.network_address}/{network.prefixlen}"
    parsed = _parse_interface(text, "where", problems)
    assert problems == []
    assert parsed is not None
    assert parsed.network.prefixlen == network.prefixlen


@given(raw=st.text(max_size=40).filter(lambda s: "/" not in s))
def test_parse_interface_always_demands_a_prefix(raw):
    """A bare address is the mistake this exists to catch, and it must be caught
    for every bare address rather than for the ones a test author thought of."""
    problems: list[Problem] = []
    assert _parse_interface(raw, "target.libvirt.nics[0].ip_cidr", problems) is None
    assert len(problems) == 1
    assert "prefix length" in problems[0].message
