"""Preflight against a real Proxmox VE cluster. Read-only, creates nothing.

This is the gate for tomorrow: everything else in this backend's suite runs
against `tests/fake_proxmox.py`, which asserts the API *paths* but cannot assert
that a real PVE answers them the way the code expects. The questions it settles
are the ones listed as open in the plan -- whether the token's privileges are
enough to list and read, and whether the target storage really does allow the
`import` content type.

    export VCOWS_PVE_ENDPOINT=https://pve.example.com:8006
    export VCOWS_PVE_TOKEN='vcows@pve!deploy=...'
    VCOWS_GATES=proxmox just test -k proxmox_rig

**Nothing here creates, modifies or deletes anything.** A cluster with unrelated
production guests on it is a safe target.
"""

from __future__ import annotations

import copy
import os

import pytest

from orchestrator.backends.proxmox import api, preflight, schema
from orchestrator.problems import Severity
from tests.conftest import PROXMOX_CONFIG, needs_proxmox, require

pytestmark = needs_proxmox


@pytest.fixture
def rig_cfg() -> dict:
    """The canonical config, re-pointed at whatever cluster was named.

    The VMs stay as they are: nothing is created, so they exist only to give
    `validate` and the orphan-seed check something to reason about.

    The real token is composed into the config from `VCOWS_PVE_TOKEN` here. A
    harness building a config out of its environment is not the product reading
    a credential from one -- `api.connect` still reads `target.proxmox` only.
    """
    cfg = copy.deepcopy(PROXMOX_CONFIG)
    cfg["target"]["proxmox"] = {
        "endpoint": os.environ["VCOWS_PVE_ENDPOINT"],
        "node": os.environ.get("VCOWS_PVE_NODE", "pve"),
        "datastore": os.environ.get("VCOWS_PVE_DATASTORE", "local-lvm"),
        "import_datastore": os.environ.get("VCOWS_PVE_IMPORT_DATASTORE", "local"),
        "token": os.environ["VCOWS_PVE_TOKEN"],
    }
    if os.environ.get("VCOWS_PVE_INSECURE"):
        cfg["target"]["proxmox"]["insecure"] = True
    return cfg


@pytest.fixture
def session(rig_cfg):
    with api.connect(rig_cfg) as s:
        yield s


def test_the_token_is_accepted(session):
    """The first thing that fails against a new cluster, and the one `validate`
    cannot check: it can say the token is well formed, not that it is real."""
    assert api.cluster_vms(session) is not None


def test_the_token_can_read_a_vms_config(session):
    """Discovery needs one config read per VM -- it is the only place
    `description` appears, and so the only place a marker can be found. A token
    that can list but not read finds no markers and reports every VM as
    unmarked, which reads as "not ours" and refuses rather than corrupting."""
    found = api.cluster_vms(session)
    # Through `require`, not a bare skip: a demanded gate that quietly passes
    # because the cluster was empty is worse than no gate. If you asked for the
    # proxmox gate, an empty cluster means it could not answer the question.
    require(
        "proxmox",
        bool(found),
        "the cluster has no VMs, so there is no config read to exercise",
    )
    first = found[0]
    config = api.vm_config(session, first["node"], first["vmid"])
    assert isinstance(config, dict)


def test_the_configured_storages_exist_and_allow_what_we_put_in_them(rig_cfg, session):
    """The `import` content type is not enabled by default. This is the check
    that turns that from a mid-apply provider error into an instruction."""
    problems = preflight._check_storages(rig_cfg, session)
    assert [p for p in problems if p.severity is Severity.ERROR] == [], "\n".join(
        str(p) for p in problems
    )


def test_a_full_preflight_completes_and_creates_nothing(rig_cfg, session):
    before = {(v["node"], v["vmid"]) for v in api.cluster_vms(session)}
    discovered = preflight.preflight(rig_cfg, session)
    after = {(v["node"], v["vmid"]) for v in api.cluster_vms(session)}
    assert before == after, "preflight must not create or remove anything"
    for existing in discovered.vms:
        node, _, vmid = existing.id.partition("/")
        assert node and vmid, f"malformed id {existing.id!r}"


def test_validate_agrees_with_the_cluster(rig_cfg):
    """Offline validation against a config that names a real cluster. Any error
    here is a config problem rather than a cluster one, and it is what an
    operator would hit first."""
    problems = [p for p in schema.validate(rig_cfg) if p.severity is Severity.ERROR]
    # The golden image is not on this machine, so the image checks warn rather
    # than error -- that is the offline-phase contract, tested elsewhere.
    assert problems == [], "\n".join(str(p) for p in problems)
