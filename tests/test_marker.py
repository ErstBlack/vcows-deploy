"""Marker serialisation, identity derivation, and forward compatibility."""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET

import pytest

from orchestrator import VERSION
from orchestrator.marker import (
    MARKER_ELEMENT,
    MARKER_XMLNS,
    VCOWS_NS,
    Marker,
    MarkerError,
    derive_id,
)


def test_namespace_is_pinned_and_re_derivable():
    """VCOWS_NS is permanent: changing it orphans every VM ever stamped.

    Pinned two ways -- the literal value, and the derivation it came from -- so
    an accidental edit fails here rather than silently in the field years later.
    """
    assert VCOWS_NS == uuid.UUID("43a00ff6-89be-57a1-8596-246f665e9f4b")
    assert VCOWS_NS == uuid.uuid5(uuid.NAMESPACE_DNS, "vcows-deploy")


def test_id_is_deterministic_across_processes():
    """The whole reason destroy works with the state file deleted."""
    assert derive_id("app01", "lab-a") == "2647c9f3-9d71-531a-b874-98a578d6c7aa"
    assert derive_id("app01", "lab-a") == derive_id("app01", "lab-a")
    assert derive_id("app01", "lab-a") != derive_id("app02", "lab-a")


def test_id_carries_the_deployment():
    """Two deployments each containing `app01` must not derive one identity.

    The id is the seed ISO's `instance-id`, so without this the two
    deployments' seeds are byte-identical.
    """
    assert derive_id("app01", "lab-a") != derive_id("app01", "lab-b")


def test_round_trip():
    m = Marker.for_vm("app01", "lab-a")
    assert Marker.from_json(m.to_json()) == m
    assert m.v == VERSION


def test_parser_ignores_unknown_keys():
    """Forward compatibility has to be real, not theoretical: a newer version
    adding a field must not make its VMs unreadable by this one."""
    raw = json.dumps(
        {
            "v": "0.9.9.9",
            "deployment": "lab-b",
            "name": "app01",
            "id": derive_id("app01", "lab-b"),
            "future_field": {"nested": [1, 2, 3]},
        }
    )
    m = Marker.from_json(raw)
    assert m.name == "app01"
    assert m.deployment == "lab-b"
    assert m.v == "0.9.9.9"


def test_marker_without_deployment_parses_to_empty_string():
    """Markers written before `deployment` existed must still parse, and must
    never hand callers a None to forget to check."""
    raw = json.dumps(
        {"v": "0.1.0.0", "name": "app01", "id": derive_id("app01", "lab-a")}
    )
    assert Marker.from_json(raw).deployment == ""


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ("not json at all", "not valid JSON"),
        ("[1,2,3]", "expected object"),
        ('{"v":"0.1.0.0"}', "missing required key"),
    ],
)
def test_malformed_markers_raise(raw, fragment):
    with pytest.raises(MarkerError) as exc:
        Marker.from_json(raw)
    assert fragment in str(exc.value)


def test_xml_form_is_a_single_namespaced_element():
    """libvirt requires <metadata> to have at least one element child, and the
    payload must survive as text. Verified against a real hypervisor in
    docs/spikes.md A2; this pins the shape that produced that result."""
    m = Marker.for_vm("app01", "lab-a")
    node = ET.fromstring(m.to_xml())
    assert node.tag == f"{{{MARKER_XMLNS}}}{MARKER_ELEMENT}"
    assert node.text == m.to_json()
    assert Marker.from_json(node.text) == m


def test_xml_payload_needs_no_escaping():
    """If the JSON ever needed XML-escaping, the byte-identical round trip that
    A2 verified would stop holding."""
    payload = Marker.for_vm("app01", "lab-a").to_json()
    assert not (set("<>&") & set(payload))


def test_text_field_form_is_findable_and_removable():
    m = Marker.for_vm("app01", "lab-a")
    line = m.to_text_field()
    assert line.startswith("vcows-managed: ")
    assert "\n" not in line
    assert Marker.from_json(line.removeprefix("vcows-managed: ")) == m
