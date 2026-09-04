"""Preflight, against XML recorded from the rig.

Every fixture in ``tests/fixtures/libvirt/`` is a verbatim ``XMLDesc`` from the
Fedora 44 rig, not something hand-written to suit the parser. That matters for two
of them in particular: ``volume-dir-entry.xml`` is a real ``<volume type='dir'>``
with no ``<physical>`` at all, and ``domain-unmarked-running.xml`` has an empty
cdrom tray with no ``<source>``. Both were found by inspecting the rig rather than
by imagining failure modes, and both would have crashed a naive parser.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import libvirt
import pytest

from orchestrator import cloudinit
from orchestrator.backends.libvirt import preflight
from orchestrator.marker import MARKER_XMLNS
from orchestrator.problems import Severity
from tests.conftest import KNOWN_HOSTS, SSH_KEY, wheres
from tests.fake_libvirt import FakeConnection, FakeDomain, FakePool, lv_error

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


def test_a_device_that_yields_nothing_does_not_end_the_disk_scan():
    """Both skips pass over one device; neither ends the walk. A domain whose
    first device is a floppy, or whose empty cdrom tray comes before its disk,
    would otherwise report no disks at all -- and destroy tears down what
    preflight found, so an unreported disk is a leaked one."""
    from xml.etree import ElementTree as ET

    xml = """<domain><devices>
      <disk type='file' device='floppy'><source file='/pool/ignored.img'/></disk>
      <disk type='file' device='cdrom'/>
      <disk type='file' device='disk'><source file='/pool/app01.qcow2'/></disk>
    </devices></domain>"""
    assert preflight.disks_of(ET.fromstring(xml)) == ("/pool/app01.qcow2",)


def test_macs_come_out_of_the_same_document():
    assert preflight.macs_of(parsed("domain-marked.xml")) == ("52:54:00:c0:ff:ee",)


# -- volumes ---------------------------------------------------------------


def test_base_image_volume_reports_physical():
    facts = preflight.volume_facts(fixture("volume-base-image.xml"))
    assert facts["format"] == "qcow2"
    assert facts["physical"] == 645988352
    assert facts["path"].endswith("Rocky-9-GenericCloud-Base.latest.x86_64.qcow2")


def test_an_overlay_reports_what_it_backs_onto():
    """The size-mismatch refusal counts these, so a message about replacing the
    golden image can say how many VMs would break with it."""
    facts = preflight.volume_facts(fixture("volume-overlay.xml"))
    assert facts["backing"].endswith("Rocky-9-GenericCloud-Base.latest.x86_64.qcow2")
    assert preflight.volume_facts(fixture("volume-base-image.xml"))["backing"] is None


def test_a_volume_with_no_name_reads_as_the_empty_name():
    """`walk` keys its result on this, so the fallback is a dictionary key and
    not a display string."""
    assert preflight.volume_facts("<volume/>")["name"] == ""


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
    assert wheres(problems) == ["image.base_volume_name"]


def test_missing_physical_warns_rather_than_refusing(cfg, tmp_path):
    """Optional in libvirt's RNG and meaningless for a non-file pool."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": None}}
    base, problems = preflight.base_volume(cfg, volumes)
    assert base["create"] is False
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert wheres(problems) == ["image.base_volume_name"]


def test_unreadable_local_image_warns(cfg):
    """`validate` is the offline phase; preflight must not turn a missing local
    file into a refusal to talk to a healthy host."""
    cfg["image"]["source_qcow2"] = "/nonexistent/golden.qcow2"
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 64}}
    _, problems = preflight.base_volume(cfg, volumes)
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert wheres(problems) == ["image.source_qcow2"], "the local file, not the host's"


def test_a_base_volume_that_reports_no_path_refuses(cfg, tmp_path):
    """Every overlay is created backing onto this path. A volume libvirt will not
    give one for cannot be backed onto, and `create: False` with an empty path
    would reach the module as a golden image nothing can find."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": None, "physical": 64}}
    base, problems = preflight.base_volume(cfg, volumes)
    assert base["create"] is False
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert wheres(problems) == ["image.base_volume_name"]


def test_size_mismatch_names_the_non_destructive_procedure(cfg, tmp_path):
    """2.4. The old message ended "delete it on the hypervisor and re-run",
    addressed to an operator whose golden image is backing every overlay on the
    host -- and it prints during a destroy as well as a deploy."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {
        "golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 32},
        "app01.qcow2": {"backing": "/pool/golden.qcow2"},
        "app02.qcow2": {"backing": "/pool/golden.qcow2"},
        "other.qcow2": {"backing": "/pool/somebody-else.qcow2"},
    }
    _, problems = preflight.base_volume(cfg, volumes)
    message = problems[0].message
    assert "delete" not in message.lower()
    assert "base_volume_name" in message
    assert "2 volume(s)" in message


def test_size_mismatch_stays_honest_with_nothing_backing_onto_it(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    volumes = {"golden.qcow2": {"path": "/pool/golden.qcow2", "physical": 32}}
    _, problems = preflight.base_volume(cfg, volumes)
    assert "0 volume(s)" in problems[0].message


# -- orphan volumes --------------------------------------------------------


def test_volume_with_no_owning_domain_refuses(cfg):
    """findings.md §2. The operator deletes one file; vcows builds no recovery
    machinery for it, which is where the last version started sprawling."""
    volumes = {
        "app01.qcow2": {"path": "/pool/app01.qcow2"},
        "app02-seed.iso": {"path": "/pool/app02-seed.iso"},
    }
    problems = preflight.orphan_volumes(cfg, volumes, claimed=set())
    assert len(problems) == 2
    assert all(p.severity is Severity.ERROR for p in problems)
    assert "app01.qcow2" in problems[0].message
    assert wheres(problems) == ["app01", "app02"]


def test_orphan_message_admits_it_may_be_another_deployments(cfg):
    """2.11. Volume names are undecorated logical names in one flat pool (D16),
    so on a shared pool this refusal can be raised against `lab-b`'s deploy,
    blamed on `lab-b`'s VM, and tell its operator to delete `lab-a`'s data."""
    volumes = {"app01.qcow2": {"path": "/pool/app01.qcow2"}}
    problems = preflight.orphan_volumes(cfg, volumes, claimed=set())
    message = problems[0].message
    assert "delete" not in message.lower()
    assert "another deployment" in message
    assert "may" in message


def test_volume_claimed_by_a_domain_is_not_an_orphan(cfg):
    volumes = {"app01.qcow2": {"path": "/pool/app01.qcow2"}}
    claimed = {"/pool/app01.qcow2"}
    assert preflight.orphan_volumes(cfg, volumes, claimed=claimed) == []


def test_a_volume_that_reports_no_path_cannot_be_vouched_for(cfg):
    """Nothing can be matched against a volume with no path, so it stays refused.
    The message says that is the reason rather than asserting it is an orphan."""
    volumes = {"app01.qcow2": {"path": None}}
    claimed = {"/pool/app01.qcow2"}
    problems = preflight.orphan_volumes(cfg, volumes, claimed=claimed)
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "reports no path" in problems[0].message
    assert wheres(problems) == ["app01"]


# -- the pool --------------------------------------------------------------


def test_missing_pool_refuses_and_says_vcows_will_not_create_one():
    """D29. Creating a pool is a host-level mutation on somebody else's hypervisor
    and would create a destroy obligation we do not want."""
    conn = FakeConnection(pools=[])
    pool, problems = preflight.open_pool(conn, "images")
    assert pool is None
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "never creates a pool" in problems[0].message
    assert wheres(problems) == ["target.libvirt.pool"]


def test_inactive_pool_refuses_by_name_rather_than_as_a_missing_volume():
    """A lookup against an inactive pool returns NO_STORAGE_VOL, which names the
    volume and never the pool -- so the real cause would never surface."""
    conn = FakeConnection(pools=[FakePool("images", {}, active=False)])
    pool, problems = preflight.open_pool(conn, "images")
    assert pool is None
    assert "not active" in problems[0].message
    assert wheres(problems) == ["target.libvirt.pool"]


def test_a_pool_lookup_that_is_not_absence_is_not_read_as_absence():
    """The refusal above tells an operator to create a pool. A reset connection, a
    policy refusal and an internal error all reach this line, and none of them says
    anything about whether the pool exists. Raising is reported by `_guard` as a
    failed run; the alternative is a confident wrong instruction."""
    conn = FakeConnection(pools=[FakePool("images", {})])
    conn.pool_lookup_error = lv_error(1, "internal error")
    with pytest.raises(libvirt.libvirtError):
        preflight.open_pool(conn, "images")


def test_a_pool_that_cannot_be_refreshed_refuses_the_deploy():
    """D35's refresh is required for correctness. Without it a golden image copied
    in out of band is invisible, preflight says "not present", the module sets
    `create = true`, and the apply dies on "storage volume exists already".

    Fatal on deploy and advisory on destroy, which needs no branch here: `cmd_deploy`
    treats `Discovered.problems` as fatal and `cmd_destroy` prints them and carries
    on. Destroy's own refresh, in `destroy._refresh_pools`, keeps its WARNING.
    """
    pool = FakePool("images", {"golden.qcow2": ""})
    pool.refresh_error = lv_error(1, "failed to read directory")
    opened, problems = preflight.open_pool(FakeConnection(pools=[pool]), "images")
    # Still returned: one pass reports every problem it can, and the walk is where
    # the rest of them come from.
    assert opened is pool
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "golden image" in problems[0].message
    assert wheres(problems) == ["target.libvirt.pool"]


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
    volumes, problems = preflight.walk(pool)
    assert set(volumes) == {
        "_cloud-images",
        "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2",
    }
    assert volumes["_cloud-images"]["physical"] is None
    assert problems == []


def test_a_volume_that_will_not_parse_is_reported_rather_than_dropped():
    """The walk answers three questions -- the orphan refusal, whether the golden
    image is here, and D30's size comparison -- so a volume it silently drops is a
    volume none of the three saw. It still must not abandon the walk."""
    pool = FakePool(
        "images",
        {"golden.qcow2": fixture("volume-base-image.xml"), "broken.qcow2": "<vol"},
    )
    pool.refresh(0)
    volumes, problems = preflight.walk(pool)
    assert "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2" in volumes
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert "broken.qcow2" in problems[0].message
    assert wheres(problems) == ["target.libvirt.pool"]


def test_a_volume_that_vanished_between_listing_and_reading_is_reported():
    """Listing then describing is two calls, and a volume can go between them."""
    pool = FakePool("images", {"golden.qcow2": ""})
    pool.refresh(0)
    pool.volume_xml_error = lv_error(50, "no storage vol with matching path")
    volumes, problems = preflight.walk(pool)
    assert volumes == {}
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert wheres(problems) == ["target.libvirt.pool"]


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
    assert wheres(problems) == ["nics[].network=default"], (
        "one refusal per missing network, not one per NIC that names it"
    )


def test_a_network_lookup_that_is_not_absence_is_not_read_as_absence(cfg):
    """Same shape as the pool lookup: "does not exist on this host" is one code,
    not every code."""
    conn = conn_with_network()
    conn.network_lookup_error = lv_error(1, "internal error")
    with pytest.raises(libvirt.libvirtError):
        preflight.address_conflicts(conn, cfg, {})


def test_leases_that_could_not_be_read_are_not_read_as_no_leases(cfg):
    """The bare `pass` this replaces made every DHCPLeases failure mean "no DHCP
    here", and an empty claim set is what `address_conflicts` then declares each
    address free against. The reservations in the network XML still read fine, so
    the check is not abandoned -- it is reported as partial."""
    conn = conn_with_network()
    conn.lease_error = lv_error(1, "internal error")
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.101/24"
    problems = preflight.address_conflicts(conn, cfg, {})
    assert [p.severity for p in problems] == [Severity.WARNING, Severity.ERROR]
    assert "leases" in problems[0].message
    assert "a DHCP reservation" in problems[1].message
    assert wheres(problems) == ["nics[].network=default", "app01.nics[0].ip_cidr"]


@pytest.mark.parametrize("code", [3, 55])  # NO_SUPPORT, OPERATION_INVALID
def test_a_network_with_no_dhcp_warns_about_nothing(cfg, code):
    """The normal case for an isolated or routed network. Printing it would train
    an operator to ignore the line that matters."""
    conn = conn_with_network()
    conn.lease_error = lv_error(code, "this function is not supported")
    assert preflight.address_conflicts(conn, cfg, {}) == []


def test_a_free_address_passes(cfg):
    assert preflight.address_conflicts(conn_with_network(), cfg, {}) == []


def test_an_address_with_a_dhcp_reservation_refuses(cfg):
    """The rig reserves .101-.105 in the network XML."""
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.101/24"
    problems = preflight.address_conflicts(conn_with_network(), cfg, {})
    assert len(problems) == 1
    assert "a DHCP reservation" in problems[0].message
    assert wheres(problems) == ["app01.nics[0].ip_cidr"]


def test_an_address_with_an_active_lease_refuses(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.82/24"
    leases = [{"ipaddr": "192.168.122.82", "mac": "52:54:00:10:a6:42"}]
    problems = preflight.address_conflicts(conn_with_network(leases), cfg, {})
    assert len(problems) == 1
    assert "an active DHCP lease" in problems[0].message
    assert wheres(problems) == ["app01.nics[0].ip_cidr"]


def test_the_deployment_reaches_the_mac_derivation(cfg):
    """`mac_of` derives from the deployment name, and this is the only route it
    takes on the preflight path. Dropped or replaced by a constant, the derived
    MAC is consistent and wrong: it collides with nothing on the host, so the
    collision this check exists to find is never reported."""
    derived = cloudinit.derive_mac("app01", 0, cfg["deployment"])
    by_mac = {derived: "somebody-elses-vm"}
    problems = preflight.address_conflicts(conn_with_network(), cfg, by_mac)
    assert wheres(problems) == ["app01.nics[0]"]


def test_a_mac_already_on_another_domain_refuses(cfg):
    """Free: it comes out of the same XMLDesc already parsed for the marker and the
    disks, which is why this check survived D32's cut of the ICMP probe."""
    cfg["vms"][1]["nics"][0]["mac"] = "52:54:00:10:a6:42"
    by_mac = {"52:54:00:10:a6:42": "rocky-runner"}
    problems = preflight.address_conflicts(conn_with_network(), cfg, by_mac)
    assert len(problems) == 1
    assert "already configured on domain 'rocky-runner'" in problems[0].message
    assert wheres(problems) == ["app02.nics[0]"], (
        "the NIC, which has no ip_cidr to blame"
    )


# -- libvirt's own error handler -------------------------------------------


def test_the_error_handler_takes_the_message_out_of_the_error_tuple(caplog):
    """It runs inside libvirt's callback, where an `IndexError` surfaces as
    something far stranger than a missing log line. Element 2 of the 9-tuple is
    the message; anything shorter, or not a tuple at all, is logged whole rather
    than indexed into.

    Asserted on the argument rather than the rendered line: what the handler
    chose is the behaviour, and the wording around it is not.
    """
    with caplog.at_level(logging.DEBUG, logger=preflight.log.name):
        preflight._chatter(None, (9, 0, "the message", 2, "", "", "", -1, -1))
        # Three is the shortest tuple that carries one, and the boundary the
        # index is guarded by: element 2 exists here and does not above.
        preflight._chatter(None, (9, 0, "the short one"))
        preflight._chatter(None, (9, 0))
        preflight._chatter(None, "not a tuple at all")

    assert [record.args[0] for record in caplog.records] == [
        "the message",
        "the short one",
        (9, 0),
        "not a tuple at all",
    ]


# -- the domain walk -------------------------------------------------------


@pytest.mark.parametrize(
    "break_it",
    [
        lambda dom: setattr(dom, "xml_error", lv_error(1, "internal error")),
        lambda dom: setattr(dom, "_xml", "<domain"),
    ],
    ids=["libvirt refuses", "the document will not parse"],
)
def test_one_unreadable_domain_does_not_abort_the_walk(break_it):
    """Uncaught, one broken domain on a shared host aborts preflight and no deploy
    on that host can run again. Caught silently, its MACs and disks are simply
    absent -- and this walk is where a MAC collision and a name clash are found, so
    absent reads as free. Reported and skipped is the only honest one."""
    doms = [
        FakeDomain("first", "u1", fixture("domain-marked.xml")),
        FakeDomain("broken", "u2", fixture("domain-marked.xml")),
        FakeDomain("last", "u3", fixture("domain-unmarked-running.xml")),
    ]
    break_it(doms[1])
    found, by_mac, problems = preflight._domains(FakeConnection(domains=doms))

    assert [e.name for e in found] == ["first", "last"]
    # `Existing.id` is what destroy looks a domain up by, and the two readable
    # domains keep theirs -- the skip drops a whole record, never a field of one.
    assert [e.id for e in found] == ["u1", "u3"]
    assert [p.severity for p in problems] == [Severity.WARNING]
    assert "broken" in problems[0].message
    assert wheres(problems) == ["target.libvirt"], (
        "the host, not a VM: the domain is somebody else's and this config may "
        "not name it at all"
    )
    # MAC -> the domain that configures it. `address_conflicts` reads both
    # halves: the key is what a config's MAC is looked up by, and the value is
    # the domain named in the refusal.
    assert by_mac["52:54:00:c0:ff:ee"] == "first"


def test_an_all_readable_host_warns_about_nothing():
    doms = [FakeDomain("first", "u1", fixture("domain-marked.xml"))]
    _, _, problems = preflight._domains(FakeConnection(domains=doms))
    assert problems == []


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
    assert discovered.problems == ()


def test_preflight_carries_the_disks_it_found(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    discovered = preflight.preflight(cfg, rig_connection(cfg))
    running = next(e for e in discovered.vms if e.name == "rocky9-box")
    assert running.disks == ("/var/lib/libvirt/images/rocky9-box.2026-08-28T23:55",)


def test_preflight_refuses_an_orphaned_overlay(cfg, tmp_path):
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    orphan = (
        "<volume><name>app01.qcow2</name>"
        "<target><path>/pool/app01.qcow2</path></target></volume>"
    )
    conn = rig_connection(cfg, volumes={"app01.qcow2": orphan})
    discovered = preflight.preflight(cfg, conn)
    assert [p.severity for p in discovered.problems] == [Severity.ERROR]
    assert "no domain on this host references it" in discovered.problems[0].message
    assert wheres(discovered.problems) == ["app01"]


def test_our_own_macs_are_not_reported_as_somebody_elses(cfg, tmp_path):
    """A marked domain for a name in this config is a SKIP. Its MACs are ours by
    construction and must not refuse the deploy that owns them."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    cfg["vms"][0]["name"] = "probe02"
    cfg["vms"][0]["nics"][0]["mac"] = "52:54:00:c0:ff:ee"
    discovered = preflight.preflight(cfg, rig_connection(cfg))
    assert discovered.problems == ()


def test_a_domain_the_walk_could_not_read_reaches_the_operator(cfg, tmp_path):
    """`Discovered.problems` is printed by every connected verb, so the warning
    needs no plumbing of its own -- but it does need to be put there."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    conn = rig_connection(cfg)
    conn.domains[0].xml_error = lv_error(1, "internal error")
    discovered = preflight.preflight(cfg, conn)
    assert [p.severity for p in discovered.problems] == [Severity.WARNING]
    assert "vcows-probe02" in discovered.problems[0].message
    assert wheres(discovered.problems) == ["target.libvirt"]


def test_a_disk_of_the_same_name_elsewhere_does_not_clear_the_refusal(cfg, tmp_path):
    """`claimed` is what the host's domains name, and a domain's disks are wherever
    they are -- while the volume being judged is in one specific pool. Compared by
    basename, any domain anywhere naming `app01.qcow2` vouches for this pool's
    orphan and the refusal never fires."""
    cfg["image"]["source_qcow2"] = str(golden(tmp_path, 64))
    orphan = (
        "<volume><name>app01.qcow2</name>"
        "<target><path>/pool/app01.qcow2</path></target></volume>"
    )
    conn = rig_connection(cfg, volumes={"app01.qcow2": orphan})
    conn.domains.append(
        FakeDomain(
            "elsewhere",
            "u4",
            "<domain><name>elsewhere</name><devices>"
            "<disk type='file' device='disk'>"
            "<source file='/elsewhere/app01.qcow2'/></disk></devices></domain>",
        )
    )
    discovered = preflight.preflight(cfg, conn)
    assert [p.severity for p in discovered.problems] == [Severity.ERROR]
    assert "no domain on this host references it" in discovered.problems[0].message


# -- the connection --------------------------------------------------------


def dial(cfg, monkeypatch, opened=None):
    """Run `connect` against a fake `libvirt.open` and return what it saw
    *while the session was open*: the URI, its parameters, and the files."""
    seen: dict = {}
    conn = FakeConnection()

    def fake_open(uri):
        params = dict(parse_qsl(urlsplit(uri).query))
        seen["uri"] = uri
        seen["params"] = params
        seen["files"] = {k: Path(v) for k, v in params.items()}
        seen["text"] = {k: p.read_text() for k, p in seen["files"].items()}
        seen["modes"] = {k: p.stat().st_mode & 0o777 for k, p in seen["files"].items()}
        if seen["files"]:
            here = next(iter(seen["files"].values())).parent
            seen["dir"] = here
            seen["dir_mode"] = here.stat().st_mode & 0o777
            seen["known_hosts"] = (here / "known_hosts").read_text()
        if opened is not None:
            raise opened
        return conn

    monkeypatch.setattr(libvirt, "open", fake_open)
    return seen, conn


def test_without_credentials_the_uri_is_the_operators_with_no_query(cfg, monkeypatch):
    """The rig tests pop both fields and rely on the dev box's `~/.ssh/config`
    alias, so a config carrying neither has to dial exactly this. `sshcmd` is
    what the acceptance run found libvirt's client does not recognise."""
    cfg["target"]["libvirt"].pop("ssh_key")
    cfg["target"]["libvirt"].pop("known_hosts")
    seen, conn = dial(cfg, monkeypatch)
    with preflight.connect(cfg) as session:
        assert session is conn
    assert seen["uri"] == "qemu+ssh://vcows@vcows/system"
    assert conn.closed


def test_inline_credentials_reach_ssh_as_files_that_live_for_the_session(
    cfg, monkeypatch
):
    """What is dialled, and what the files looked like while it was. libvirt
    honours `keyfile=`; `known_hosts=` it does not (#247), so the copy is
    named inside the `command=` wrapper with host key checking kept on."""
    seen, _ = dial(cfg, monkeypatch)
    with preflight.connect(cfg):
        pass
    assert seen["uri"].startswith("qemu+ssh://vcows@vcows/system?")
    assert set(seen["params"]) == {"keyfile", "command"}
    assert seen["text"]["keyfile"] == SSH_KEY
    assert seen["known_hosts"] == KNOWN_HOSTS
    # ssh refuses a group-readable key, and the wrapper has to be runnable.
    assert seen["modes"] == {"keyfile": 0o600, "command": 0o700}
    assert seen["dir_mode"] == 0o700
    wrapper = seen["text"]["command"]
    assert wrapper.startswith("#!/bin/sh\nexec ssh ")
    assert f"-o UserKnownHostsFile={seen['dir'] / 'known_hosts'}" in wrapper
    assert "-o StrictHostKeyChecking=yes" in wrapper
    assert "-o BatchMode=yes" in wrapper
    assert wrapper.rstrip().endswith('"$@"')
    # The image gate's `ls /tmp/vcows-ssh-*/` depends on the prefix.
    assert seen["dir"].name.startswith("vcows-ssh-")
    # Gone with the session: the key was only ever in a directory of our own.
    assert not seen["dir"].exists()


def test_a_file_already_there_is_refused_rather_than_truncated(cfg, tmp_path):
    """`O_EXCL`: the directory is ours and fresh, so anything already at the
    path is a bug, and overwriting a key in place is the wrong way to find it."""
    (tmp_path / "key").write_text("theirs\n")
    with pytest.raises(FileExistsError):
        preflight.ssh_files(cfg["target"]["libvirt"], tmp_path)


def test_the_files_are_removed_when_the_dial_fails(cfg, monkeypatch):
    seen, _ = dial(cfg, monkeypatch, opened=lv_error(1, "Cannot recv data"))
    with pytest.raises(libvirt.libvirtError), preflight.connect(cfg):
        pass
    assert not seen["dir"].exists()


def test_a_chomped_key_gains_the_newline_openssh_wants(cfg, monkeypatch):
    """`ssh_key: |-` strips it, and OpenSSH refuses a key whose final line has
    no terminator -- naming neither the config nor the chomping indicator."""
    cfg["target"]["libvirt"]["ssh_key"] = SSH_KEY.rstrip("\n")
    seen, _ = dial(cfg, monkeypatch)
    with preflight.connect(cfg):
        pass
    assert seen["text"]["keyfile"] == SSH_KEY


def test_a_temp_dir_with_a_space_is_quoted_in_the_wrapper(cfg, tmp_path):
    """The path is baked into a shell script, so `TMPDIR=/tmp/a b` would
    otherwise split it. Called directly: `TemporaryDirectory` honours a cached
    `tempfile.tempdir`, which is not worth fighting for one assertion."""
    into = tmp_path / "with space"
    into.mkdir()
    params = preflight.ssh_files(cfg["target"]["libvirt"], into)
    assert (
        f"UserKnownHostsFile='{into / 'known_hosts'}'"
        in Path(params["command"]).read_text()
    )
