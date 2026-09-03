"""Preflight against a real hypervisor.

Skipped with an explicit reason when ``VCOWS_RIG_URI`` is unset -- never
silently. A gate that quietly passes because it did not run is worse than no
gate.

Read-only apart from ``pool.refresh()``, which is a directory rescan. Nothing here
defines, starts, stops or undefines anything: the two probe domains are fixtures on
the rig, and destroy is not exercised against a real VM until the acceptance run
has created one to tear down. ``tests/test_libvirt_boot.py`` is the file that
does define, start and undefine one, under this same ``rig`` gate.

The pair of probes is deliberate. ``vcows-probe02`` carries a current
``urn:vcows:1`` marker and is the positive case; ``vcows-spike-probe01`` still
carries spike A2's ``https://example.invalid/vcows`` and is therefore the
*unmarked* case, for free.
"""

from __future__ import annotations

import os

import pytest

from orchestrator.backends.base import Action, decide
from orchestrator.backends.libvirt import preflight
from orchestrator.config import vm_names
from orchestrator.problems import Severity
from tests.conftest import gate

RIG = os.environ.get("VCOWS_RIG_URI")

needs_rig = gate(
    "rig",
    RIG is not None,
    "set VCOWS_RIG_URI to a reachable libvirt URI to run the rig gate",
)

pytestmark = needs_rig

MARKED_PROBE = "vcows-probe02"
UNMARKED_PROBE = "vcows-spike-probe01"


@pytest.fixture
def rig_cfg(cfg):
    assert RIG is not None  # every test here is behind needs_rig
    cfg["target"]["libvirt"]["uri"] = RIG
    cfg["target"]["libvirt"].pop("ssh_keyfile", None)
    cfg["target"]["libvirt"].pop("known_hosts", None)
    return cfg


@pytest.fixture
def session(rig_cfg):
    with preflight.connect(rig_cfg) as conn:
        yield conn


def test_the_connection_closes_on_the_way_out(rig_cfg):
    import libvirt

    with preflight.connect(rig_cfg) as conn:
        assert conn.isAlive()
    with pytest.raises(libvirt.libvirtError):
        conn.getLibVersion()


def test_the_daemon_version_is_not_the_client_version(session):
    """The undefine mask gates on the daemon's. Reading the client's would gate on
    the wrong machine entirely -- and on the rig the two genuinely differ."""
    import libvirt

    assert session.getLibVersion() != libvirt.getVersion()


# -- discovery -------------------------------------------------------------


def test_the_marked_probe_is_found_by_marker(rig_cfg, session):
    discovered = preflight.preflight(rig_cfg, session)
    probe = next(e for e in discovered.vms if e.name == MARKED_PROBE)
    assert probe.marker is not None
    assert probe.marker.name == "probe02"
    assert probe.marker.deployment == "spike"
    # Identity is the marker, not the name: the hypervisor name and the logical
    # name differ here on purpose.
    assert probe.marker.name != probe.name


def test_the_superseded_probe_reads_as_unmarked(rig_cfg, session):
    discovered = preflight.preflight(rig_cfg, session)
    probe = next(e for e in discovered.vms if e.name == UNMARKED_PROBE)
    assert probe.marker is None


def test_an_unmarked_domain_whose_name_we_want_is_refused(rig_cfg, session):
    """vcows will not adopt or overwrite something it did not create."""
    rig_cfg["vms"][0]["name"] = UNMARKED_PROBE
    discovered = preflight.preflight(rig_cfg, session)
    decisions, _ = decide(vm_names(rig_cfg), discovered.vms, rig_cfg["deployment"])
    assert decisions[0].action is Action.REFUSE
    assert "will not adopt or overwrite" in decisions[0].reason


def test_a_running_domains_disks_resolve_after_the_refresh(rig_cfg, session):
    """D35, against the real cache. Three of the rig's four running domains have
    disks written out of band, which do not resolve until the pool is refreshed.
    """
    import libvirt

    discovered = preflight.preflight(rig_cfg, session)
    paths = [p for e in discovered.vms for p in e.disks]
    assert paths, "the rig has running domains with disks"
    for path in paths:
        try:
            session.storageVolLookupByPath(path)
        except libvirt.libvirtError as exc:
            pytest.fail(f"{path} did not resolve after refresh: {exc}")


# -- the pool --------------------------------------------------------------


def test_the_configured_pool_opens(session):
    pool, problems = preflight.open_pool(session, "images")
    assert pool is not None
    assert problems == []


def test_a_pool_that_does_not_exist_refuses(session):
    pool, problems = preflight.open_pool(session, "nosuchpool")
    assert pool is None
    assert [p.severity for p in problems] == [Severity.ERROR]


# -- the base image (D30) --------------------------------------------------

BASE_ON_RIG = "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2"


def sparse(path, size: int):
    with open(path, "wb") as handle:
        handle.truncate(size)
    return str(path)


@pytest.fixture
def rig_volumes(session):
    pool, problems = preflight.open_pool(session, "images")
    assert pool is not None, problems
    volumes, _ = preflight.walk(pool)
    return volumes


def test_the_walk_survives_the_directory_entry_in_the_real_pool(rig_volumes):
    assert "_cloud-images" in rig_volumes
    assert rig_volumes["_cloud-images"]["physical"] is None
    assert rig_volumes[BASE_ON_RIG]["physical"] > 0


def test_a_matching_base_image_is_reported_present_with_its_path(
    rig_cfg, rig_volumes, tmp_path
):
    on_rig = rig_volumes[BASE_ON_RIG]["physical"]
    rig_cfg["image"]["base_volume_name"] = BASE_ON_RIG
    rig_cfg["image"]["source_qcow2"] = sparse(tmp_path / "golden.qcow2", on_rig)

    base, problems = preflight.base_volume(rig_cfg, rig_volumes)
    assert base["create"] is False
    assert base["path"].endswith(BASE_ON_RIG)
    assert problems == []


def test_a_truncated_local_image_refuses(rig_cfg, rig_volumes, tmp_path):
    """Truncating a copy rather than interrupting a real upload: the failure being
    modelled is a size disagreement, and how the disagreement arose is irrelevant
    to the check. It also catches a *different* image under the same name."""
    on_rig = rig_volumes[BASE_ON_RIG]["physical"]
    rig_cfg["image"]["base_volume_name"] = BASE_ON_RIG
    rig_cfg["image"]["source_qcow2"] = sparse(tmp_path / "golden.qcow2", on_rig - 4096)

    _, problems = preflight.base_volume(rig_cfg, rig_volumes)
    assert [p.severity for p in problems] == [Severity.ERROR]
    assert "truncated upload or a different image" in problems[0].message


# -- addressing ------------------------------------------------------------


def conflicts(cfg, session):
    _, by_mac, _ = preflight._domains(session)
    return preflight.address_conflicts(session, cfg, by_mac)


def test_a_free_address_in_the_confirmed_range_passes(rig_cfg, session):
    assert conflicts(rig_cfg, session) == []


def test_an_address_with_a_live_lease_refuses(rig_cfg, session):
    """192.168.122.82 is this dev box, leased on the rig's default network."""
    rig_cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.82/24"
    problems = conflicts(rig_cfg, session)
    assert any("192.168.122.82 is already" in p.message for p in problems)


def test_an_address_with_a_dhcp_reservation_refuses(rig_cfg, session):
    """The rig reserves .101-.105 for rocky8-cto-01..05."""
    rig_cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.101/24"
    problems = conflicts(rig_cfg, session)
    assert any("a DHCP reservation" in p.message for p in problems)


def test_a_mac_already_on_the_rig_refuses(rig_cfg, session):
    """vcows-probe02's MAC, claimed by a VM with a different logical name -- so it
    is somebody else's, not ours."""
    rig_cfg["vms"][0]["nics"][0]["mac"] = "52:54:00:c0:ff:ee"
    problems = conflicts(rig_cfg, session)
    assert any(
        f"already configured on domain '{MARKED_PROBE}'" in p.message for p in problems
    )
