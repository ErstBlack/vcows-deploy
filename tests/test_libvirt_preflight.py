"""Preflight, against XML recorded from the rig.

Every fixture in ``tests/fixtures/libvirt/`` is a verbatim ``XMLDesc`` from the
Fedora 44 rig, not something hand-written to suit the parser. That matters for two
of them in particular: ``volume-dir-entry.xml`` is a real ``<volume type='dir'>``
with no ``<physical>`` at all, and ``domain-unmarked-running.xml`` has an empty
cdrom tray with no ``<source>``. Both were found by inspecting the rig rather than
by imagining failure modes, and both would have crashed a naive parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.backends.base import Severity
from orchestrator.backends.libvirt import preflight
from orchestrator.marker import MARKER_XMLNS
from tests.fake_libvirt import FakeConnection, FakeDomain, FakePool

FIXTURES = Path(__file__).parent / "fixtures" / "libvirt"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def parsed(name: str):
    from xml.etree import ElementTree as ET

    return ET.fromstring(fixture(name))


# -- the marker ------------------------------------------------------------


def test_current_namespace_parses():
    marker = preflight.marker_of(parsed("domain-marked.xml"))
    assert marker is not None
    assert marker.name == "probe02"
    assert marker.deployment == "spike"


def test_superseded_namespace_reads_as_unmarked():
    """The A2 probe predates D14's `urn:vcows:1`.

    It is not ours and must not be treated as ours -- reading a foreign namespace
    as a marker is how destroy would delete somebody else's VM.
    """
    root = parsed("domain-old-namespace.xml")
    assert MARKER_XMLNS not in fixture("domain-old-namespace.xml")
    assert preflight.marker_of(root) is None


def test_no_metadata_at_all_is_unmarked():
    assert preflight.marker_of(parsed("domain-unmarked-running.xml")) is None


@pytest.mark.parametrize("payload", ["not json", "[]", '{"name":"x"}'])
def test_unparseable_marker_is_unmarked_not_ours(payload):
    """D12. Reading a damaged marker as ours risks destroying something we do not
    understand; reading it as absent is caught by the name-collision refusal."""
    from xml.etree import ElementTree as ET

    xml = (
        f"<domain><name>x</name><metadata>"
        f'<vcows xmlns="{MARKER_XMLNS}">{payload}</vcows>'
        f"</metadata></domain>"
    )
    assert preflight.marker_of(ET.fromstring(xml)) is None


# -- disks -----------------------------------------------------------------


def test_disks_skip_a_cdrom_with_no_source():
    """Every domain on the rig has an empty tray. A None path here would reach
    destroy and be handed to a volume lookup."""
    disks = preflight.disks_of(parsed("domain-unmarked-running.xml"))
    assert disks == ("/var/lib/libvirt/images/rocky9-box.2026-08-28T23:55",)
    assert None not in disks


def test_backing_store_is_never_collected():
    """The single invariant between destroy and the shared golden image.

    ``vol.delete()`` provides no protection -- ``in_use`` tracks the storage
    driver's own transient operations, not domain references -- so nothing but this
    stands between a teardown and every other deployment's base volume.
    """
    from xml.etree import ElementTree as ET

    xml = """<domain><devices>
      <disk type='file' device='disk'>
        <source file='/pool/app01.qcow2'/>
        <backingStore type='file'>
          <source file='/pool/golden.qcow2'/>
        </backingStore>
      </disk>
    </devices></domain>"""
    assert preflight.disks_of(ET.fromstring(xml)) == ("/pool/app01.qcow2",)


def test_cdrom_sources_are_collected_when_present():
    """D17: without this the per-VM seed ISO is orphaned on every teardown."""
    from xml.etree import ElementTree as ET

    xml = """<domain><devices>
      <disk type='file' device='disk'><source file='/pool/app01.qcow2'/></disk>
      <disk type='file' device='cdrom'><source file='/pool/app01-seed.iso'/></disk>
    </devices></domain>"""
    assert preflight.disks_of(ET.fromstring(xml)) == (
        "/pool/app01.qcow2",
        "/pool/app01-seed.iso",
    )


def test_macs_come_out_of_the_same_document():
    assert preflight.macs_of(parsed("domain-marked.xml")) == ("52:54:00:c0:ff:ee",)


# -- volumes ---------------------------------------------------------------


def test_base_image_volume_reports_physical():
    facts = preflight.volume_facts(fixture("volume-base-image.xml"))
    assert facts["format"] == "qcow2"
    assert facts["physical"] == 645988352
    assert facts["path"].endswith("Rocky-9-GenericCloud-Base.latest.x86_64.qcow2")


def test_directory_entry_in_a_pool_parses_with_no_physical():
    """The rig's `_cloud-images`. Not a candidate, and not an error either."""
    facts = preflight.volume_facts(fixture("volume-dir-entry.xml"))
    assert facts["name"] == "_cloud-images"
    assert facts["format"] == "dir"
    assert facts["physical"] is None


# -- the base volume decision ---------------------------------------------


def golden(tmp_path: Path, size: int) -> Path:
    image = tmp_path / "golden.qcow2"
    image.write_bytes(b"\0" * size)
    return image


def test_absent_base_image_means_create(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    base, problems = preflight.base_volume(cfg, {})
    assert base == {"name": "golden.qcow2", "create": True, "path": ""}
    assert problems == []


def test_present_and_matching_means_do_not_create(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 64}}
    base, problems = preflight.base_volume(cfg, volumes)
    assert base == {
        "name": "golden.qcow2",
        "create": False,
        "path": "/pool/golden.qcow2",
    }
    assert problems == []


def test_size_mismatch_refuses(cfg, tmp_path):
    """D30. A truncated upload still declares the full virtual size in its header,
    so capacity cannot catch it -- every overlay would back onto a broken image and
    VMs would fail at random points in boot on a host reported healthy."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 32}}
    _, problems = preflight.base_volume(cfg, volumes)
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "32 bytes on the host but 64 bytes locally" in problems[0].message


def test_missing_physical_warns_rather_than_refusing(cfg, tmp_path):
    """Optional in libvirt's RNG and meaningless for a non-file pool."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": None}}
    base, problems = preflight.base_volume(cfg, volumes)
    assert base["create"] is False
    assert [p.severity for p in problems] == [Severity.WARNING]


def test_unreadable_local_image_warns(cfg):
    """`validate` is the offline phase; preflight must not turn a missing local
    file into a refusal to talk to a healthy host."""
    cfg["image"]["source_qcow2"] = "/nonexistent/golden.qcow2"
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 64}}
    _, problems = preflight.base_volume(cfg, volumes)
    assert [p.severity for p in problems] == [Severity.WARNING]


# -- orphan volumes --------------------------------------------------------


def test_volume_with_no_owning_domain_refuses(cfg):
    """findings.md §2. The operator deletes one file; vcows builds no recovery
    machinery for it, which is where the last version started sprawling."""
    volumes = {"app01.qcow2": {}, "app02-seed.iso": {}}
    problems = preflight.orphan_volumes(cfg, volumes, claimed=set())
    assert len(problems) == 2
    assert all(p.severity is Severity.ERROR for p in problems)
    assert "app01.qcow2" in problems[0].message


def test_volume_claimed_by_a_domain_is_not_an_orphan(cfg):
    volumes = {"app01.qcow2": {}}
    assert preflight.orphan_volumes(cfg, volumes, claimed={"app01.qcow2"}) == []


# -- the pool --------------------------------------------------------------


def test_missing_pool_refuses_and_says_vcows_will_not_create_one():
    """D29. Creating a pool is a host-level mutation on somebody else's hypervisor
    and would create a destroy obligation we do not want."""
    conn = FakeConnection(pools=[])
    pool, problems = preflight.open_pool(conn, "images")
    assert pool is None
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "never creates a pool" in problems[0].message


def test_inactive_pool_refuses_by_name_rather_than_as_a_missing_volume():
    """A lookup against an inactive pool returns NO_STORAGE_VOL, which names the
    volume and never the pool -- so the real cause would never surface."""
    conn = FakeConnection(pools=[FakePool("images", {}, active=False)])
    pool, problems = preflight.open_pool(conn, "images")
    assert pool is None
    assert "not active" in problems[0].message


def test_opening_a_pool_refreshes_it():
    pool = FakePool("images", {"golden.qcow2": ""})
    opened, problems = preflight.open_pool(FakeConnection(pools=[pool]), "images")
    assert opened is pool
    assert pool.refreshed == 1
    assert problems == []


def test_the_walk_survives_a_directory_entry():
    """The rig's `_cloud-images`. It has no <physical> and format 'dir', and it
    must neither crash the walk nor register as a candidate."""
    pool = FakePool(
        "images",
        {
            "_cloud-images": fixture("volume-dir-entry.xml"),
            "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2": fixture(
                "volume-base-image.xml"
            ),
        },
    )
    pool.refresh(0)
    volumes = preflight.walk(pool)
    assert set(volumes) == {
        "_cloud-images",
        "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
    }
    assert volumes["_cloud-images"]["physical"] is None


# -- addressing ------------------------------------------------------------


def conn_with_network(leases=None, domains=None):
    return FakeConnection(
        domains=domains or [],
        networks={"default": fixture("network-default.xml")},
        leases={"default": leases or []},
    )


def test_a_network_that_does_not_exist_refuses(cfg):
    """Otherwise this fails at define time, deep inside an apply."""
    problems = preflight.address_conflicts(FakeConnection(), cfg, {})
    assert all(p.severity is Severity.ERROR for p in problems)
    assert any("does not exist on this host" in p.message for p in problems)


def test_a_free_address_passes(cfg):
    assert preflight.address_conflicts(conn_with_network(), cfg, {}) == []


def test_an_address_with_a_dhcp_reservation_refuses(cfg):
    """The rig reserves .101-.105 in the network XML."""
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.101/24"
    problems = preflight.address_conflicts(conn_with_network(), cfg, {})
    assert len(problems) == 1
    assert "a DHCP reservation" in problems[0].message


def test_an_address_with_an_active_lease_refuses(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.82/24"
    leases = [{"ipaddr": "192.168.122.82", "mac": "52:54:00:10:a6:42"}]
    problems = preflight.address_conflicts(conn_with_network(leases), cfg, {})
    assert len(problems) == 1
    assert "an active DHCP lease" in problems[0].message


def test_a_mac_already_on_another_domain_refuses(cfg):
    """Free: it comes out of the same XMLDesc already parsed for the marker and the
    disks, which is why this check survived D32's cut of the ICMP probe."""
    cfg["vms"][1]["nics"][0]["mac"] = "52:54:00:10:a6:42"
    by_mac = {"52:54:00:10:a6:42": "rocky-runner"}
    problems = preflight.address_conflicts(conn_with_network(), cfg, by_mac)
    assert len(problems) == 1
    assert "already configured on domain 'rocky-runner'" in problems[0].message


# -- the whole walk --------------------------------------------------------


def rig_connection(cfg, volumes=None):
    domains = [
        FakeDomain("vcows-probe02", "u1", fixture("domain-marked.xml")),
        FakeDomain("vcows-spike-probe01", "u2", fixture("domain-old-namespace.xml")),
        FakeDomain(
            "rocky9-box", "u3", fixture("domain-unmarked-running.xml"), active=True
        ),
    ]
    return FakeConnection(
        domains=domains,
        pools=[FakePool(cfg["target"]["libvirt"]["pool"], volumes or {})],
        networks={"default": fixture("network-default.xml")},
    )


def test_preflight_reports_ours_theirs_and_the_base_volume(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    conn = rig_connection(cfg)
    discovered = preflight.preflight(cfg, conn)

    by_name = {e.name: e for e in discovered.vms}
    assert by_name["vcows-probe02"].marker is not None
    assert by_name["vcows-probe02"].marker.name == "probe02"
    # Superseded namespace, and no metadata at all: both unmarked, neither ours.
    assert by_name["vcows-spike-probe01"].marker is None
    assert by_name["rocky9-box"].marker is None

    assert discovered.artifacts["base_volume"]["create"] is True
    assert discovered.problems == []


def test_preflight_carries_the_disks_it_found(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    discovered = preflight.preflight(cfg, rig_connection(cfg))
    running = next(e for e in discovered.vms if e.name == "rocky9-box")
    assert running.disks == ("/var/lib/libvirt/images/rocky9-box.2026-08-28T23:55",)


def test_preflight_refuses_an_orphaned_overlay(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    orphan = "<volume><name>app01.qcow2</name></volume>"
    conn = rig_connection(cfg, volumes={"app01.qcow2": orphan})
    discovered = preflight.preflight(cfg, conn)
    assert [p.severity for p in discovered.problems] == [Severity.ERROR]
    assert "no domain references it" in discovered.problems[0].message


def test_our_own_macs_are_not_reported_as_somebody_elses(cfg, tmp_path):
    """A marked domain for a name in this config is a SKIP. Its MACs are ours by
    construction and must not refuse the deploy that owns them."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    cfg["vms"][0]["name"] = "probe02"
    cfg["vms"][0]["nics"][0]["mac"] = "52:54:00:c0:ff:ee"
    discovered = preflight.preflight(cfg, rig_connection(cfg))
    assert discovered.problems == []
