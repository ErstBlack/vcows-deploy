"""Preflight against a real vCenter. Read-only, creates nothing.

Everything else in this backend's suite runs against `tests/fake_vsphere.py`,
which is built out of the SDK's own types but cannot assert that a real vCenter
answers them the way the code expects. The questions this settles are whether
the login works, whether the account may read the inventory, and whether the
names in `target.vsphere` resolve on the cluster they were written for.

    export VCOWS_VSPHERE_ENDPOINT=https://vcenter.example.com
    export VCOWS_VSPHERE_USER=vcows@vsphere.local
    export VCOWS_VSPHERE_PASSWORD=...
    VCOWS_GATES=vsphere just test -k vsphere_rig

**Nothing here creates, modifies or deletes anything.** A vCenter with unrelated
production guests on it is a safe target.
"""

from __future__ import annotations

import copy
import os

import pytest

from orchestrator.backends.vsphere import api, preflight, schema
from orchestrator.problems import Severity
from tests.conftest import VSPHERE_CONFIG, WORKTREE, needs_vsphere, require

pytestmark = needs_vsphere


@pytest.fixture
def rig_cfg() -> dict:
    """The canonical config, re-pointed at whatever vCenter was named.

    The VMs stay as they are: nothing is created, so they exist only to give
    `validate` and the orphan-seed check something to reason about. The
    deployment carries this worktree's name, as everything the suite puts on
    real hardware does.

    The real credential is composed into the config from the environment here. A
    harness building a config out of its environment is not the product reading
    a credential from one -- `api.connect` still reads `target.vsphere` only.
    """
    cfg = copy.deepcopy(VSPHERE_CONFIG)
    if WORKTREE:
        cfg["deployment"] = f"{cfg['deployment']}-{WORKTREE}"
    cfg["target"]["vsphere"] = {
        "endpoint": os.environ["VCOWS_VSPHERE_ENDPOINT"],
        "user": os.environ["VCOWS_VSPHERE_USER"],
        "password": os.environ["VCOWS_VSPHERE_PASSWORD"],
        "datacenter": os.environ.get("VCOWS_VSPHERE_DATACENTER", "Datacenter"),
        "datastore": os.environ.get("VCOWS_VSPHERE_DATASTORE", "datastore1"),
        "network": os.environ.get("VCOWS_VSPHERE_NETWORK", "VM Network"),
        "cluster": os.environ.get("VCOWS_VSPHERE_CLUSTER", "cluster-a"),
    }
    if os.environ.get("VCOWS_VSPHERE_INSECURE"):
        cfg["target"]["vsphere"]["insecure"] = True
    return cfg


@pytest.fixture
def vc_session(rig_cfg):
    with api.connect(rig_cfg) as s:
        yield s


def test_the_credential_is_accepted(vc_session):
    """The first thing that fails against a new vCenter, and the one `validate`
    cannot check: it can say a user and a password are present, not that they
    are real."""
    assert vc_session.content is not None
    assert vc_session.cookie, "the datastore uploads have nothing else to go on"


def test_the_account_can_read_the_inventory(vc_session):
    """Discovery is one PropertyCollector call, and an account that may log in
    but not read answers it with nothing -- which reads as an empty vCenter and
    would have `decide` plan a create over every VM that is really there."""
    found = api.vms(vc_session.content)
    # Through `require`, not a bare skip: a demanded gate that quietly passes
    # because the vCenter was empty is worse than no gate.
    require(
        "vsphere",
        bool(found),
        "the vCenter has no VMs, so there is no property read to exercise",
    )
    assert all("name" in props for props in found)


def test_every_configured_name_resolves(rig_cfg, vc_session):
    """The check that turns a mid-clone vCenter fault naming no config field
    into an instruction naming one."""
    problems: list = []
    preflight._check_target(rig_cfg, vc_session, problems)
    assert [p for p in problems if p.fatal] == [], "\n".join(str(p) for p in problems)


def test_a_full_preflight_completes_and_creates_nothing(rig_cfg, vc_session):
    before = {props.get("summary.config.uuid") for props in api.vms(vc_session.content)}
    discovered = preflight.preflight(rig_cfg, vc_session)
    after = {props.get("summary.config.uuid") for props in api.vms(vc_session.content)}
    assert before == after, "preflight must not create or remove anything"
    for existing in discovered.vms:
        assert existing.id, f"a VM with no identity to destroy by: {existing.name!r}"


def test_validate_agrees_with_the_vcenter(rig_cfg):
    """Offline validation against a config that names a real vCenter. Any error
    here is a config problem rather than a cluster one, and it is what an
    operator would hit first."""
    problems = [p for p in schema.validate(rig_cfg) if p.severity is Severity.ERROR]
    # The golden image is not on this machine, so the image checks warn rather
    # than error -- that is the offline-phase contract, tested elsewhere.
    assert problems == [], "\n".join(str(p) for p in problems)
