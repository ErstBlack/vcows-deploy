"""Shared fixtures, and the gates the suite shares.

**A gate that quietly passes because it did not run is worse than no gate**, and in
aggregate that is what a bare `pytest -q` does: skips, exit 0, with nothing
saying what was never looked at. `VCOWS_GATES` is the opt-in that turns a
named gate's skip into a failure -- `VCOWS_GATES=rig`, or `all` for every one of
them. Named rather than only `all` because the gates differ in what they need: a
missing `pycdlib` is a fixable local omission, while `rig` and `image` need
hardware and a build, and a run proving one gate was checked should not have to
fail on the others too.

The config below is the canonical one: two VMs covering both firmware branches
(libvirt-selected and explicitly pinned) and both MAC branches (derived and
overridden), on the `default` network with statics from the .60-.70 range that is
confirmed free on the rig.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _rev_parse(*args: str) -> str:
    """One `git rev-parse` answer, or empty when git will not give one.

    Empty covers all three failures the same way -- a non-zero status, no git on
    PATH, a tree that is not a repo -- because every caller below treats "no
    answer" as "not a worktree".
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _worktree() -> str:
    """The linked worktree's branch name, sanitised, or empty.

    `worktree_tag` (`scripts/lib.sh`) is the same rule for the shell side: empty
    in the main checkout, in CI and outside a repo, because a linked worktree is
    the only case where `--git-dir` and `--git-common-dir` disagree. Anything
    this suite creates that outlives the process -- an image tag, a rig test's
    `deployment` -- appends it when it is non-empty. `VCOWS_WORKTREE` overrides.

    `CONFIG` and `PROXMOX_CONFIG` deliberately keep their literal `lab-a`: they
    are unit fixtures asserted verbatim across the suite and nothing deploys
    them.
    """
    name = os.environ.get("VCOWS_WORKTREE", "")
    if not name:
        common = _rev_parse("--git-common-dir")
        if not common or common == _rev_parse("--git-dir"):
            return ""
        name = _rev_parse("--abbrev-ref", "HEAD")
    return re.sub(r"[^a-z0-9._-]", "-", name.lower())


#: This worktree's name, or empty in the main checkout. See `_worktree`.
WORKTREE = _worktree()


def _parse(raw: str) -> set[str]:
    """Comma-separated names, no stripping. `rig, image` demands `rig` only."""
    return {g for g in raw.split(",") if g}


#: Gates the operator demanded. Comma-separated names, or `all`.
GATES = _parse(os.environ.get("VCOWS_GATES", ""))


def demanded(name: str) -> bool:
    return bool(GATES & {name, "all"})


def gate(name: str, available: bool, reason: str):
    """A skip, or -- when this gate was demanded -- a failure carrying the reason.

    The failure is raised from `pytest_runtest_setup` rather than by letting the
    test run into whatever error the missing dependency produces: a rig test
    without `VCOWS_RIG_URI` would otherwise fail somewhere inside libvirt, which
    says nothing about the gate.
    """
    if available:
        return pytest.mark.skipif(False, reason=reason)
    return (
        pytest.mark.gate_missing(reason)
        if demanded(name)
        else pytest.mark.skip(reason=reason)
    )


def require(name: str, available: bool, reason: str) -> None:
    """`gate` for the places a mark cannot reach: a fixture body, a module import."""
    if available:
        return
    if demanded(name):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "gate_missing(reason): a demanded gate whose dependency is absent"
    )


def pytest_runtest_setup(item) -> None:
    for mark in item.iter_markers("gate_missing"):
        pytest.fail(mark.args[0], pytrace=False)


#: For tests that talk to a real Proxmox VE cluster. Both halves are needed: an
#: endpoint says where, and the token is the only credential this backend has.
PVE_ENDPOINT = os.environ.get("VCOWS_PVE_ENDPOINT")
needs_proxmox = gate(
    "proxmox",
    bool(PVE_ENDPOINT) and bool(os.environ.get("PROXMOX_VE_API_TOKEN")),
    "needs VCOWS_PVE_ENDPOINT and PROXMOX_VE_API_TOKEN to run against a cluster",
)


CONFIG: dict = {
    "schema_version": 1,
    "deployment": "lab-a",
    "backend": "libvirt",
    "target": {
        "libvirt": {
            "uri": "qemu+ssh://vcows@vcows/system",
            "pool": "images",
            "ssh_keyfile": "/run/secrets/id_ed25519",
            "known_hosts": "/run/secrets/known_hosts",
        }
    },
    "image": {
        "source_qcow2": "/images/golden.qcow2",
        "base_volume_name": "golden.qcow2",
    },
    "vms": [
        {
            "name": "app01",
            "vcpus": 2,
            "memory_mib": 4096,
            "disk_gb": 40,
            "nics": [
                {
                    "network": "default",
                    "ip_cidr": "192.168.122.60/24",
                    "gateway": "192.168.122.1",
                    "nameservers": ["192.168.122.1"],
                }
            ],
        },
        {
            "name": "app02",
            "vcpus": 4,
            "memory_mib": 8192,
            "disk_gb": 60,
            "firmware": "efi",
            # The rig's paths. RHEL ships a raw .fd at a different path, which is
            # exactly why these are config and not constants.
            "loader": "/usr/share/edk2/ovmf/OVMF_CODE_4M.qcow2",
            "loader_format": "qcow2",
            "nvram_template": "/usr/share/edk2/ovmf/OVMF_VARS_4M.qcow2",
            "machine": "q35",
            "user_data": "#cloud-config\npackages:\n  - tmux\n",
            "nics": [
                {
                    "network": "default",
                    "ip_cidr": "192.168.122.61/24",
                    "gateway": "192.168.122.1",
                    "nameservers": ["192.168.122.1", "192.168.122.1"],
                    "mac": "52:54:00:aa:bb:cc",
                }
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def _root_logger():
    """Put the root logger back after every test.

    `orchestrator` configures it at package import and `container.entrypoint`
    configures it again from `main()`, with a different format -- and both
    *replace* the root handler list rather than adding to it. So a test that runs
    either one changes what every later test reads off stderr.

    In declaration order this stayed invisible: `test_logging.py`'s own tests call
    `configure_logging()` before the ones that assert on a line, repairing it by
    luck. Under a shuffled suite it surfaces as
    `test_every_line_carries_a_level_and_a_logger` failing because the line
    carries the entrypoint's `orchestrator.cli:` rather than `_Short`'s `cli`.

    Same argument as `_umask` below, and the same remedy: global process state a
    test mutates has to be handed back.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture(autouse=True)
def _umask():
    """Put the process umask back after every test.

    `cli.main` sets 0o077, and the tests call it in-process -- so without this the
    first CLI test quietly changes the mode of every file every later test writes,
    which is the kind of ordering dependency that shows up as one unexplained
    failure months later.
    """
    before = os.umask(0o022)
    os.umask(before)
    yield
    os.umask(before)


@pytest.fixture
def cfg() -> dict:
    """A fresh deep copy, so a test that mutates it cannot poison the next."""
    return copy.deepcopy(CONFIG)


#: The Proxmox counterpart of CONFIG above, deliberately exercising what differs
#: rather than mirroring it: a NIC attaches to a bridge and only a bridge, one VM
#: carries a VLAN tag, and firmware is a choice with no host paths beside it.
PROXMOX_CONFIG: dict = {
    "schema_version": 1,
    "deployment": "lab-a",
    "backend": "proxmox",
    "target": {
        "proxmox": {
            "endpoint": "https://pve.example.com:8006",
            "node": "pve1",
            "datastore": "local-lvm",
            "import_datastore": "local",
        }
    },
    "image": {
        "source_qcow2": "/images/golden.qcow2",
        "base_volume_name": "golden.qcow2",
    },
    "vms": [
        {
            "name": "app01",
            "vcpus": 2,
            "memory_mib": 4096,
            "disk_gb": 40,
            "nics": [
                {
                    "bridge": "vmbr0",
                    "ip_cidr": "192.168.122.60/24",
                    "gateway": "192.168.122.1",
                    "nameservers": ["192.168.122.1"],
                }
            ],
        },
        {
            "name": "app02",
            "vcpus": 4,
            "memory_mib": 8192,
            "disk_gb": 60,
            "firmware": "efi",
            "machine": "q35",
            "user_data": "#cloud-config\npackages:\n  - tmux\n",
            "nics": [
                {
                    "bridge": "vmbr0",
                    "vlan_id": 42,
                    "ip_cidr": "192.168.122.61/24",
                    "gateway": "192.168.122.1",
                    "nameservers": ["192.168.122.1"],
                    "mac": "52:54:00:aa:bb:cc",
                }
            ],
        },
    ],
}


@pytest.fixture
def pve_cfg() -> dict:
    """A fresh deep copy, for the same reason `cfg` is one."""
    return copy.deepcopy(PROXMOX_CONFIG)


@pytest.fixture
def pve_token(monkeypatch) -> str:
    """A syntactically valid token in the environment.

    Every Proxmox verb reads it, so a test that does not set it is testing the
    unset-token refusal whether it meant to or not. Not a real credential and
    never sent anywhere: `FakeProxmox` does not authenticate.
    """

    # A fixed, obviously-fake UUID for a fake that never authenticates.
    token = "vcows@pve!deploy=00000000-0000-4000-8000-000000000000"  # noqa: S105
    monkeypatch.setenv("PROXMOX_VE_API_TOKEN", token)
    return token
