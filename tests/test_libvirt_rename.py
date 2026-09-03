"""One VM deployed, renamed on the hypervisor, and destroyed under its new name.

`tests/test_policy.py` proves a renamed domain is still ours to `decide`. This
is the other half: that `destroy` then removes what the domain owns. The
destroy path re-reads the XML after the marker re-verify and takes the disks
from it (`preflight.disks_of`), so the name the domain carries should not
matter. kcli, evaluated in `docs/kcli-eval-2026-09-02.md`, reads its disks
from the XML too and then keeps only the paths matching the current name, so
a rename deletes the domain and leaks both volumes. That is the failure this
pins against. `#200`.

No guest is booted and nothing is read over SSH: the domain is powered off as
soon as it is defined, because `virDomain.rename` refuses a running domain.
Same gates and same rig facts as `tests/test_libvirt_boot.py`, and its own
deployment name and address so nothing here collides with that file or the
shared `CONFIG`.
"""

from __future__ import annotations

import copy

import pytest
import yaml

from orchestrator import cli
from orchestrator.backends.libvirt import preflight
from tests.conftest import CONFIG
from tests.test_libvirt_boot import _stand_in
from tests.test_libvirt_rig import RIG, needs_rig

pytestmark = needs_rig

DEPLOYMENT = "rename"
ADDRESS = "192.168.122.71"
GATEWAY = "192.168.122.1"
#: The name in the config, and the name the hypervisor is told afterwards.
NAME = "probe"
RENAMED = "somebody-renamed-me"

VM: dict = {
    "name": NAME,
    "vcpus": 1,
    "memory_mib": 1024,
    "disk_gb": 20,
    "nics": [
        {
            "network": "default",
            "ip_cidr": f"{ADDRESS}/24",
            "gateway": GATEWAY,
            "nameservers": [GATEWAY],
        }
    ],
}


def _volumes(cfg: dict) -> set[str]:
    """Every volume name in the rig's pool, right now."""
    with preflight.connect(cfg) as conn:
        pool, problems = preflight.open_pool(conn, cfg["target"]["libvirt"]["pool"])
        assert pool is not None, problems
        volumes, _ = preflight.walk(pool)
    return set(volumes)


def _domains(cfg: dict) -> set[str]:
    with preflight.connect(cfg) as conn:
        return {dom.name() for dom in conn.listAllDomains(0)}


def _rename(cfg: dict, hv_name: str, new: str) -> None:
    """Power the domain off and rename it, as an operator with `virsh` would."""
    with preflight.connect(cfg) as conn:
        dom = conn.lookupByName(hv_name)
        if dom.isActive():
            dom.destroy()
        assert dom.rename(new, 0) == 0


@pytest.fixture(scope="module")
def outcome(tmp_path_factory):
    """Deploy, rename, destroy; yield the pool and domain sets at each step.

    Destroy runs in `finally` under the renamed name so a failure between the
    two still tears down, and it is asserted on so a leftover is a failure here
    and not something the next run trips over.
    """
    assert RIG is not None  # every test here is behind needs_rig
    tmp = tmp_path_factory.mktemp("rename")

    cfg = copy.deepcopy(CONFIG)
    cfg["deployment"] = DEPLOYMENT
    cfg["target"]["libvirt"]["uri"] = RIG
    # The rig login comes from ~/.ssh/config, as in `tests/test_libvirt_rig.py`.
    cfg["target"]["libvirt"].pop("ssh_key", None)
    cfg["target"]["libvirt"].pop("known_hosts", None)
    cfg["vms"] = [copy.deepcopy(VM)]
    cfg["image"]["base_volume_name"] = "Rocky-9-GenericCloud-Base.latest.x86_64.qcow2"
    cfg["image"]["source_qcow2"] = str(_stand_in(cfg, tmp))

    path = tmp / f"{DEPLOYMENT}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    before = {"volumes": _volumes(cfg), "domains": _domains(cfg)}
    assert cli.main(["deploy", str(path), "--run-dir", str(tmp / "deploy")]) == 0
    try:
        deployed = {"volumes": _volumes(cfg), "domains": _domains(cfg)}
        hv_name = _hv_name(deployed["domains"] - before["domains"])
        _rename(cfg, hv_name, RENAMED)
        renamed = {"volumes": _volumes(cfg), "domains": _domains(cfg)}
    finally:
        assert (
            cli.main(["destroy", str(path), "--yes", "--run-dir", str(tmp / "destroy")])
            == 0
        )
    after = {"volumes": _volumes(cfg), "domains": _domains(cfg)}
    yield {
        "before": before,
        "deployed": deployed,
        "renamed": renamed,
        "after": after,
        "hv_name": hv_name,
    }


def _hv_name(new_domains: set[str]) -> str:
    assert len(new_domains) == 1, f"deploy defined {sorted(new_domains)}"
    return new_domains.pop()


def test_deploy_left_exactly_what_rename_then_destroy_must_remove(outcome):
    """The premise: one new domain and its volumes, and the rename took."""
    created = outcome["deployed"]["volumes"] - outcome["before"]["volumes"]
    assert created, "deploy created no volumes, so there is nothing to leak"
    assert outcome["renamed"]["domains"] - outcome["before"]["domains"] == {RENAMED}
    assert outcome["hv_name"] not in outcome["renamed"]["domains"]


def test_destroy_removes_the_renamed_domain(outcome):
    assert outcome["after"]["domains"] == outcome["before"]["domains"]


def test_destroy_removes_the_volumes_the_renamed_domain_owned(outcome):
    """kcli's failure: the domain is gone and the overlay and seed ISO stay."""
    leaked = outcome["after"]["volumes"] - outcome["before"]["volumes"]
    assert not leaked, f"left behind after destroy: {sorted(leaked)}"
    assert outcome["after"]["volumes"] == outcome["before"]["volumes"]
