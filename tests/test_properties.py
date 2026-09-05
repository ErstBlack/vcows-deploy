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

import re
import struct

from hypothesis import given
from hypothesis import strategies as st

from orchestrator import qcow2
from orchestrator.backends.libvirt.schema import NAME_PATTERN
from orchestrator.cloudinit import _parse_interface
from orchestrator.config import DEPLOYMENT_PATTERN
from orchestrator.marker import Marker
from orchestrator.problems import Problem

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


#: What a validated config can actually hold. Unbounded `st.text` makes the
#: assertion below false rather than strong: `derive_id` joins the two halves
#: with a bare '/', so ("b/c", "a") and ("c", "a/b") produce one id from two
#: inputs -- and Hypothesis draws the two pairs independently, so it never builds
#: that pair and the test passes while claiming something untrue.
#:
#: Two strategies rather than one, because these are two constants in two
#: modules that are currently identical and need not stay so.
VM_NAME = st.from_regex(NAME_PATTERN, fullmatch=True)
DEPLOYMENT = st.from_regex(DEPLOYMENT_PATTERN, fullmatch=True)


@given(
    a=st.tuples(VM_NAME, DEPLOYMENT),
    b=st.tuples(VM_NAME, DEPLOYMENT),
)
def test_derived_ids_separate_deployments(a, b):
    """`deployment` is folded into the uuid5 input so two deployments with the
    same VM name do not collide. That is a claim about every pair of validated
    identifiers, not about the pair someone happened to write down."""
    # Injectivity holds because the separator cannot occur in either half, not
    # because uuid5 is injective. This is the assertion that fails if either
    # pattern is ever widened to admit '/', which the property test above
    # cannot catch on its own.
    assert re.match(NAME_PATTERN, "a/b") is None
    assert re.match(DEPLOYMENT_PATTERN, "a/b") is None

    first, second = Marker.for_vm(*a), Marker.for_vm(*b)
    assert (first.id == second.id) == (a == b)


@given(
    spec=st.one_of(
        st.tuples(st.ip_addresses(v=4), st.integers(min_value=0, max_value=32)),
        st.tuples(st.ip_addresses(v=6), st.integers(min_value=0, max_value=128)),
    )
)
def test_parse_interface_accepts_what_ipaddress_produces(spec):
    """Anything the stdlib will render, the parser will read back identically.

    The address and the prefix length are drawn independently because
    `_parse_interface` is `ip_interface`, which takes a host address and not
    only a network one -- and a host address is what an operator writes. Mapping
    through `IPv4Network(addr).supernet(24)` instead widens a /32 by 24 bits:
    every case a /8, every v6 case a /120, and never once a host address.
    """
    address, prefixlen = spec
    problems: list[Problem] = []
    text = f"{address}/{prefixlen}"
    parsed = _parse_interface(text, "where", problems)
    assert problems == []
    assert parsed is not None
    # The whole interface, not just its prefix: the docstring says "read back
    # identically", and `prefixlen` alone is not a round trip.
    assert str(parsed) == text


@given(raw=st.text(max_size=40).filter(lambda s: "/" not in s))
def test_parse_interface_always_demands_a_prefix(raw):
    """A bare address is the mistake this exists to catch, and it must be caught
    for every bare address rather than for the ones a test author thought of."""
    problems: list[Problem] = []
    assert _parse_interface(raw, "target.libvirt.nics[0].ip_cidr", problems) is None
    assert len(problems) == 1
    assert "prefix length" in problems[0].message
