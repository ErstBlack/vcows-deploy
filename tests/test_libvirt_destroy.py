"""Teardown: the flag mask, the ordering, and the accounting.

No real VM is torn down here. There is nothing vcows created to tear down until
the acceptance run, and destroying one of the rig's four working guests to test
this is not a trade worth making. What is testable without one is everything that
has actually been got wrong in this design: the order of the two calls, which flag
bits may be shed, whether the pool is refreshed before a path is resolved, and
whether a partial failure is reported or swallowed.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from orchestrator.backends.base import Existing, Severity
from orchestrator.backends.libvirt import destroy as d
from orchestrator.backends.libvirt.preflight import disks_of, marker_of
from orchestrator.marker import Marker
from tests.fake_libvirt import FakeConnection, FakeDomain, FakePool, lv_error

FULL = d.FLOOR | d.UNDEFINE_CHECKPOINTS_METADATA | d.UNDEFINE_TPM


# -- the constants ---------------------------------------------------------


def test_flag_values_match_the_installed_binding():
    """They are written as literals so the mask builder is pure and testable with
    no libvirt. That only stays safe if something pins them to the real ABI."""
    libvirt = pytest.importorskip("libvirt")
    assert d.UNDEFINE_MANAGED_SAVE == libvirt.VIR_DOMAIN_UNDEFINE_MANAGED_SAVE
    assert (
        d.UNDEFINE_SNAPSHOTS_METADATA == libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
    )
    assert d.UNDEFINE_NVRAM == libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
    assert (
        d.UNDEFINE_CHECKPOINTS_METADATA
        == libvirt.VIR_DOMAIN_UNDEFINE_CHECKPOINTS_METADATA
    )
    assert d.UNDEFINE_TPM == libvirt.VIR_DOMAIN_UNDEFINE_TPM


def test_error_codes_match_the_installed_binding():
    libvirt = pytest.importorskip("libvirt")
    assert d.ERR_INVALID_ARG == libvirt.VIR_ERR_INVALID_ARG
    assert d.ERR_OPERATION_INVALID == libvirt.VIR_ERR_OPERATION_INVALID
    assert d.ERR_NO_STORAGE_VOL == libvirt.VIR_ERR_NO_STORAGE_VOL
    assert d.ERR_NO_DOMAIN == libvirt.VIR_ERR_NO_DOMAIN


# -- the mask --------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (8000000, d.FLOOR | d.UNDEFINE_CHECKPOINTS_METADATA),  # Rocky 9.0 EUS
        (8005000, d.FLOOR | d.UNDEFINE_CHECKPOINTS_METADATA),  # Rocky 9.1 EUS
        (8009000, FULL),  # first release with TPM
        (10010000, FULL),  # Rocky 9.6 / 10.0
        (11010000, FULL),  # Rocky 9.8 and 10.2 both
        (12000000, FULL),  # the rig
    ],
)
def test_mask_is_gated_on_the_daemon_version(version, expected):
    assert d.undefine_mask(version) == expected


def test_floor_always_survives_the_gate():
    """MANAGED_SAVE, SNAPSHOTS_METADATA and NVRAM all predate libvirt 1.2.9, so no
    supported target rejects them."""
    assert d.undefine_mask(0) == d.FLOOR
    assert d.FLOOR & d.UNDEFINE_NVRAM


# -- ordering --------------------------------------------------------------


def domain_xml(marker, disks=()):
    """A domain document with the two things destroy re-reads: marker and disks.

    Real XML rather than `<domain/>` because destroy now parses this rather than
    trusting the snapshot preflight took, and a fixture that returns nothing
    would prove only that a target carrying nothing is skipped.
    """
    devices = "".join(
        f'<disk type="file" device="disk"><source file="{p}"/></disk>' for p in disks
    )
    return (
        f"<domain type='kvm'><metadata>{marker.to_xml()}</metadata>"
        f"<devices>{devices}</devices></domain>"
    )


def domain(name="app01", active=True, disks=(), deployment="lab-a"):
    marker = Marker.for_vm(name, deployment)
    return FakeDomain(name, marker.id, domain_xml(marker, disks), active=active)


def target(dom, disks=None):
    """What preflight would have produced, parsed the way preflight parses it.

    `disks` overrides the parse, for the tests whose whole point is that the
    snapshot and the live document disagree.
    """
    root = ET.fromstring(dom.XMLDesc(0))
    return Existing(
        name=dom.name(),
        id=dom.UUIDString(),
        marker=marker_of(root),
        disks=disks_of(root) if disks is None else tuple(disks),
    )


def test_destroy_precedes_undefine():
    """Reversed, `virDomainDeleteConfig` unlinks the persistent XML -- and the
    marker with it -- before flipping the domain transient, leaving a running VM
    nobody owns and no future preflight can see."""
    dom = domain(active=True)
    conn = FakeConnection(domains=[dom])
    d.destroy({}, conn, [target(dom)])
    assert dom.log == ["destroy", f"undefine:{FULL}"]


def test_a_domain_already_off_is_not_destroyed_again():
    """`destroyFlags` on a stopped domain raises OPERATION_INVALID. Checking
    `isActive` first keeps that out of the error path entirely."""
    dom = domain(active=False)
    conn = FakeConnection(domains=[dom])
    d.destroy({}, conn, [target(dom)])
    assert dom.log == [f"undefine:{FULL}"]


def test_a_domain_stopped_by_someone_else_mid_run_is_not_a_failure():
    """The race between isActive and destroyFlags. Treated as success only because
    the domain really is inactive afterwards."""
    dom = domain(active=True)
    dom.stop_error = lv_error(d.ERR_OPERATION_INVALID, "domain is not running")
    dom.active = False
    conn = FakeConnection(domains=[dom])
    d.destroy({}, conn, [target(dom)])
    assert dom.log[-1] == f"undefine:{FULL}"


def test_a_real_stop_failure_aborts_that_domain_and_is_fatal():
    dom = domain(active=True)
    dom.stop_error = lv_error(1, "internal error")
    conn = FakeConnection(domains=[dom])
    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [target(dom)])
    assert "could not stop" in str(caught.value)
    assert "undefine" not in " ".join(dom.log)


def test_a_domain_already_gone_still_has_its_disks_collected():
    """The crash window between destroy and undefine, resumed. Also the case where
    somebody undefined it by hand and left the qcow2 behind."""
    pool = FakePool("images", {"app01.qcow2": ""})
    conn = FakeConnection(domains=[], pools=[pool])
    ghost = Existing(
        name="app01",
        id="00000000-0000-0000-0000-000000000000",
        marker=Marker.for_vm("app01", "lab-a"),
        disks=("/pool/app01.qcow2",),
    )
    d.destroy({}, conn, [ghost])
    assert pool.deleted == ["app01.qcow2"]


def test_a_lookup_that_failed_for_any_other_reason_is_fatal_and_touches_no_disk():
    """`no domain with matching uuid` is one error code out of dozens. A dropped
    connection or a policy refusal says nothing about whether that domain still
    exists, and deleting its recorded disks on that basis is the one action a
    failed lookup must never authorise."""
    pool = FakePool("images", {"app01.qcow2": ""})
    conn = FakeConnection(domains=[], pools=[pool])
    conn.lookup_error = lv_error(38, "Cannot recv data: Connection reset by peer")
    ghost = Existing(
        name="app01",
        id="00000000-0000-0000-0000-000000000000",
        marker=Marker.for_vm("app01", "lab-a"),
        disks=("/pool/app01.qcow2",),
    )
    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [ghost])
    assert "app01" in str(caught.value)
    assert pool.deleted == []


# -- the flag floor --------------------------------------------------------


def test_rejected_flags_are_shed_down_to_the_floor_in_one_retry():
    """`virCheckFlags` reports every offending bit at once, so a bit-at-a-time loop
    buys nothing but round trips -- and risks reaching NVRAM."""
    dom = domain(active=False)
    dom.rejects = d.UNDEFINE_TPM
    conn = FakeConnection(domains=[dom])
    d.destroy({}, conn, [target(dom)])
    assert dom.log == [f"undefine:{FULL}", f"undefine:{d.FLOOR}"]


def test_the_retry_never_shed_nvram():
    """Dropping NVRAM does not degrade gracefully: an EFI domain then refuses to
    undefine at all, turning a diagnosable flag error into an undiagnosable one."""
    dom = domain(active=False)
    dom.rejects = d.UNDEFINE_TPM
    conn = FakeConnection(domains=[dom])
    d.destroy({}, conn, [target(dom)])
    for call in dom.log:
        assert int(call.split(":")[1]) & d.UNDEFINE_NVRAM


def test_a_floor_rejection_is_fatal_rather_than_retried_forever():
    dom = domain(active=False)
    dom.rejects = d.UNDEFINE_NVRAM
    conn = FakeConnection(domains=[dom])
    with pytest.raises(d.DestroyError):
        d.destroy({}, conn, [target(dom)])


def test_a_non_flag_undefine_failure_is_not_retried():
    """OPERATION_INVALID -- a managed save, a snapshot, an NVRAM varstore -- is a
    real refusal. Retrying it would just fail twice and log a misleading warning."""
    dom = domain(active=False)

    def refuse(_flags):
        dom.log.append("undefine")
        raise lv_error(d.ERR_OPERATION_INVALID, "Refusing to undefine")

    dom.undefineFlags = refuse
    conn = FakeConnection(domains=[dom])
    with pytest.raises(d.DestroyError):
        d.destroy({}, conn, [target(dom)])
    assert dom.log == ["undefine"]


# -- storage ---------------------------------------------------------------


def test_pools_are_refreshed_before_any_path_is_resolved():
    """D35, and the reason it is mandatory: on the rig, three of four running
    domains' disks are real files in an active pool's own directory and do not
    resolve until this happens. Without it, `report and skip what does not resolve`
    silently leaks every overlay."""
    pool = FakePool("images", {"app01.qcow2": "", "app01-seed.iso": ""})
    assert pool.visible == set(), "starts cold, as libvirt's cache does"
    dom = domain(active=False, disks=["/pool/app01.qcow2", "/pool/app01-seed.iso"])
    conn = FakeConnection(domains=[dom], pools=[pool])
    d.destroy({}, conn, [target(dom)])
    assert pool.refreshed == 1
    assert sorted(pool.deleted) == ["app01-seed.iso", "app01.qcow2"]


def test_an_inactive_pool_is_not_refreshed():
    pool = FakePool("images", {}, active=False)
    conn = FakeConnection(domains=[], pools=[pool])
    d.destroy({}, conn, [])
    assert pool.refreshed == 0


def test_an_inactive_pool_holding_a_targets_disk_is_fatal():
    """The 2.3 reproduction. An inactive pool cannot be refreshed, so every disk
    in it resolves as NO_STORAGE_VOL -- "already gone" -- while both files sit on
    disk with the domain's marker undefined. Silence here is how a teardown
    reports success and leaks every volume it was asked to remove."""
    pool = FakePool("images", {"app01.qcow2": ""}, active=False)
    dom = domain(active=False, disks=["/pool/app01.qcow2"])
    conn = FakeConnection(domains=[dom], pools=[pool])

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [target(dom)])

    outcome = caught.value.outcome
    assert any(
        "images" in p.message and "app01.qcow2" in p.message
        for p in outcome.problems
        if p.fatal
    ), "the pool and the disk it holds are both named"
    assert "undefine" in " ".join(dom.log), "the domain is still torn down"
    assert "/pool/app01.qcow2" in outcome.skipped
    assert pool.deleted == []


def test_an_inactive_pool_holding_nothing_of_ours_is_left_alone():
    """Every pool is refreshed because a domain's disks are wherever they are.
    That does not make every idle pool on the host this teardown's problem."""
    pool = FakePool("elsewhere", {}, active=False, path="/other")
    dom = domain(active=False, disks=["/pool/app01.qcow2"])
    conn = FakeConnection(
        domains=[dom], pools=[pool, FakePool("images", {"app01.qcow2": ""})]
    )

    outcome = d.destroy({}, conn, [target(dom)])
    assert outcome.problems == []


def test_a_path_that_will_not_resolve_is_skipped_not_unlinked():
    """After a refresh, NO_STORAGE_VOL genuinely means gone. Reaching past the pool
    with os.unlink is what would turn a teardown into arbitrary file deletion on
    somebody else's hypervisor."""
    pool = FakePool("images", {})
    dom = domain(active=False, disks=["/pool/app01.qcow2"])
    conn = FakeConnection(domains=[dom], pools=[pool])
    d.destroy({}, conn, [target(dom)])
    assert pool.deleted == []


# -- the window between preflight and the operator's answer -----------------


def test_the_disks_deleted_are_the_ones_the_domain_names_now():
    """`cmd_destroy` waits on a human at a terminal between the two reads, and
    the wait is unbounded. findings.md:87 claimed these paths were read
    immediately before undefining; until now they were not read again at all."""
    dom = domain(active=False, disks=["/pool/app01.qcow2"])
    stale = target(dom, ["/pool/app01-seed.iso"])
    pool = FakePool("images", {"app01.qcow2": "", "app01-seed.iso": ""})
    conn = FakeConnection(domains=[dom], pools=[pool])

    d.destroy({}, conn, [stale])

    assert pool.deleted == ["app01.qcow2"], "the snapshot won, not the live document"


def test_a_domain_whose_marker_changed_since_preflight_is_left_alone():
    """Someone re-stamped it for another deployment while the prompt was open.
    Tearing it down anyway would destroy a VM this run does not own."""
    dom = domain(active=False, disks=["/pool/app01.qcow2"])
    stale = target(dom)
    dom.redefine(domain_xml(Marker.for_vm("app01", "lab-b"), ["/pool/app01.qcow2"]))
    pool = FakePool("images", {"app01.qcow2": ""})
    conn = FakeConnection(domains=[dom], pools=[pool])

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [stale])

    assert "marker changed" in str(caught.value)
    assert dom.log == [], "neither stopped nor undefined"
    assert pool.deleted == []


def test_a_vanished_targets_disk_claimed_by_another_domain_is_left_alone():
    """The vanished case has no live document to compare against, so its recorded
    paths are all there is. `vol.delete` will happily unlink a running VM's disk,
    which makes this the only thing standing between the two."""
    squatter = domain("app99", active=True, disks=["/pool/app01.qcow2"])
    pool = FakePool("images", {"app01.qcow2": ""})
    conn = FakeConnection(domains=[squatter], pools=[pool])
    ghost = Existing(
        name="app01",
        id="00000000-0000-0000-0000-000000000000",
        marker=Marker.for_vm("app01", "lab-a"),
        disks=("/pool/app01.qcow2",),
    )

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [ghost])

    assert "claimed by another domain" in str(caught.value)
    assert pool.deleted == []


def test_a_recorded_path_outside_this_vms_two_names_is_not_deleted():
    """`disks_of` collects every file-backed source a domain names, which is the
    right width for discovery and too wide for deletion. A domain we own that has
    been given the shared golden image must not take it down with it."""
    dom = domain(active=False, disks=["/pool/golden.qcow2"])
    pool = FakePool("images", {"golden.qcow2": ""})
    conn = FakeConnection(domains=[dom], pools=[pool])

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [target(dom)])

    assert "not one of the names this VM owns" in str(caught.value)
    assert pool.deleted == []


# -- accounting ------------------------------------------------------------


def test_one_failure_does_not_stop_the_others_and_is_still_fatal():
    """Twenty objects can fail independently. Silent partial success is exactly
    what findings.md §1 rejects `tofu destroy` for; reintroducing it here would be
    the worst possible outcome."""
    ok = domain("app01", active=False)
    bad = domain("app02", active=True)
    bad.stop_error = lv_error(1, "internal error")
    conn = FakeConnection(domains=[ok, bad])

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [target(ok), target(bad)])

    outcome = caught.value.outcome
    assert "app01" in outcome.destroyed
    assert "app02" not in outcome.destroyed
    assert [p.severity for p in outcome.problems] == [Severity.ERROR]


def test_a_clean_run_raises_nothing_and_says_what_it_removed():
    dom = domain(active=True, disks=["/pool/app01.qcow2"])
    pool = FakePool("images", {"app01.qcow2": ""})
    conn = FakeConnection(domains=[dom], pools=[pool])

    outcome = d.destroy({}, conn, [target(dom)])

    # Objects, not VMs: the domain and its disk are two things that could have
    # failed separately, and the caller is the only thing that can report either.
    assert outcome.destroyed == ["app01", "/pool/app01.qcow2"]
    assert outcome.skipped == []
    assert outcome.problems == []


def test_the_error_names_everything_left_behind_not_only_what_failed():
    """`cmd_destroy` never sees the Outcome on this path -- it gets an exception
    instead -- so a message carrying only the fatal problems drops every leaked
    volume beside them."""
    ok = domain("app01", active=False, disks=["/pool/app01.qcow2"])
    bad = domain("app02", active=True)
    bad.stop_error = lv_error(1, "internal error")
    conn = FakeConnection(domains=[ok, bad], pools=[FakePool("images", {})])

    with pytest.raises(d.DestroyError) as caught:
        d.destroy({}, conn, [target(ok), target(bad)])

    message = str(caught.value)
    assert "internal error" in message
    assert "/pool/app01.qcow2" in message, "the volume that would not resolve"
