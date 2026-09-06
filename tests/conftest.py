"""Shared fixtures, and the gates the suite shares.

**A gate that quietly passes because it did not run is worse than no gate**, and
that is what a bare `pytest -q` does in aggregate: skips, exit 0, nothing saying
what was never looked at. `VCOWS_GATES` turns a named gate's skip into a failure
-- `VCOWS_GATES=rig`, or `all` for every one. Named rather than only `all`
because the gates differ in what they need: a missing `pycdlib` is a fixable
local omission, while `rig` and `image` need hardware and a build, and a run
proving one gate was checked should not fail on the others.

The config below is the canonical one: two VMs covering both firmware branches
(libvirt-selected and explicitly pinned) and both MAC branches (derived and
overridden), on the `default` network with statics from the .60-.70 range that is
confirmed free on the rig.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import pytest

from orchestrator.backends.proxmox import api
from orchestrator.backends.vsphere import api as vsphere_api
from tests.fake_proxmox import FakeProxmox

REPO = Path(__file__).resolve().parent.parent


def _rev_parse(*args: str) -> str:
    """One `git rev-parse` answer, or empty when git will not give one.

    Empty covers all three failures the same way -- non-zero status, no git on
    PATH, a tree that is not a repo -- because every caller treats "no answer" as
    "not a worktree".
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
    `deployment` -- appends it when non-empty. `VCOWS_WORKTREE` overrides.

    `CONFIG` and `PROXMOX_CONFIG` keep their literal `lab-a`: they are unit
    fixtures asserted verbatim across the suite and nothing deploys them.
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

    Raised from `pytest_runtest_setup` rather than letting the test run into
    whatever error the missing dependency produces: a rig test without
    `VCOWS_RIG_URI` would otherwise fail inside libvirt, which says nothing about
    the gate.
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


def wheres(problems) -> list[str]:
    """What each problem is filed against, in order.

    `where` is the only field of a problem anything downstream reads: the CLI
    prints it beside the message and `run.json` records it. It is the half a
    message cannot carry -- "not present" against `image.base_volume_name` and
    against `target.libvirt.pool` are different instructions to the operator.
    """
    return [p.where for p in problems]


def messages(problems) -> str:
    return "\n".join(str(p) for p in problems)


def errors(problems) -> list:
    """The fatal half of a problem list."""
    return [p for p in problems if p.fatal]


def dumped(tfvars: dict) -> str:
    """A rendered values dict as its golden file holds it."""
    return json.dumps(tfvars, indent=2, sort_keys=True) + "\n"


def session(w: FakeProxmox) -> api.Session:
    """A `Session` onto a `FakeProxmox`, with `PROXMOX_CONFIG`'s node and stores."""
    return api.Session(
        prox=w, node="pve1", datastore="local-lvm", import_datastore="local"
    )


@pytest.fixture
def _no_polling_delay(monkeypatch):
    """proxmoxer's task poller sleeps once per wait. Fine against a cluster,
    pure latency here.

    Opt-in rather than autouse: `test_proxmox_backend.py` reads `POLL_INTERVAL`
    to assert `wait` passes it through, so zeroing it suite-wide would make that
    gate agree with itself whatever the value is. The two modules that wait on
    fake tasks name it in their `pytestmark`.
    """
    monkeypatch.setattr(api, "POLL_INTERVAL", 0)


@pytest.fixture
def _no_vsphere_polling_delay(monkeypatch):
    """`vsphere/api.wait` sleeps between two reads of a task. Fine against a
    vCenter, pure latency here.

    Opt-in for the reason the Proxmox one is: `test_vsphere_backend.py` reads
    `POLL_INTERVAL` to assert `wait` says what it is about to do, so zeroing it
    suite-wide would make that gate agree with itself whatever the value is.
    """
    monkeypatch.setattr(vsphere_api, "POLL_INTERVAL", 0)


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers", "gate_missing(reason): a demanded gate whose dependency is absent"
    )


def pytest_runtest_setup(item) -> None:
    for mark in item.iter_markers("gate_missing"):
        pytest.fail(mark.args[0], pytrace=False)


#: For tests that talk to a real Proxmox VE cluster. Both halves are needed: an
#: endpoint says where, and a token to reach it with. Read from the environment
#: because the rig test *composes* the config it deploys -- a harness building a
#: config out of its environment is not the product reading a credential from
#: one, and `target.proxmox` is the product's only source.
PVE_ENDPOINT = os.environ.get("VCOWS_PVE_ENDPOINT")
needs_proxmox = gate(
    "proxmox",
    bool(PVE_ENDPOINT) and bool(os.environ.get("VCOWS_PVE_TOKEN")),
    "needs VCOWS_PVE_ENDPOINT and VCOWS_PVE_TOKEN to run against a cluster",
)

#: For tests that talk to a real vCenter. All three halves are needed, for the
#: reason the two above are: vCenter has no token form, so an endpoint alone
#: names a server nothing can log in to. Read from the environment because the
#: rig test *composes* the config it deploys, and `target.vsphere` is the
#: product's only source.
VCENTER_ENDPOINT = os.environ.get("VCOWS_VSPHERE_ENDPOINT")
needs_vsphere = gate(
    "vsphere",
    bool(VCENTER_ENDPOINT)
    and bool(os.environ.get("VCOWS_VSPHERE_USER"))
    and bool(os.environ.get("VCOWS_VSPHERE_PASSWORD")),
    "needs VCOWS_VSPHERE_ENDPOINT, VCOWS_VSPHERE_USER and VCOWS_VSPHERE_PASSWORD "
    "to run against a vCenter",
)


#: Half a PEM header, kept in a name of its own so the other half can never join
#: it at compile time. Adjacent literals and `"a" + "b"` are both constant-folded
#: into the `.pyc`, and `gitleaks dir` walks `__pycache__` too: a whole header no
#: `.py` file holds gives three findings there. Concatenating through a name is
#: not folded, so nothing on disk carries it.
_BEGIN = "-----BEGIN "

#: The two libvirt credentials as a config carries them: the file's contents, not
#: a path to it. The suite's only key -- `tests/test_libvirt_preflight.py` and
#: `tests/test_image.py` import this one rather than writing another.
SSH_KEY = (
    _BEGIN + "OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)
KNOWN_HOSTS = "vcows ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleNotAKey\n"

#: A CA certificate as `target.proxmox.ca_cert` carries one. Written whole,
#: unlike SSH_KEY above: a certificate is the public half, and gitleaks' rules
#: are about private keys.
CA_CERT = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBkTCB+wIJAOExampleNotACertificateJustEnoughToLookLikeOneAAAAAA\n"
    "-----END CERTIFICATE-----\n"
)

#: A CA certificate as `target.vsphere.ca_cert` carries one, and a real
#: self-signed one rather than the decorative body above: `vsphere/api.connect`
#: builds an `ssl` context out of the text, and `ssl` raises on anything it
#: cannot parse. Generated once with `openssl req -x509`, valid to 2056, and its
#: private key was never kept -- it signs nothing and verifies nothing.
VSPHERE_CA_CERT = """\
-----BEGIN CERTIFICATE-----
MIIDEzCCAfugAwIBAgIUe5emU+CCji0LDK5ODbEqU4rV95IwDQYJKoZIhvcNAQEL
BQAwGDEWMBQGA1UEAwwNdmNvd3MgdGVzdCBDQTAgFw0yNjA5MDYxOTIwNDhaGA8y
MDU2MDgyOTE5MjA0OFowGDEWMBQGA1UEAwwNdmNvd3MgdGVzdCBDQTCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBALK0auf5vTnNRtn5STtt9eHFeMwQiMAF
feOT1nOnCPJjo04dek/uIESzBZj2HR4Y9yrzFnEdPlPrr2RswB+vJd0VN5DPUpCl
STR5yac6/iQom5sl0FEQ34xvypV7fpgoANEd1v5aJfiOEVTjV5Y4CXfY5QZ/K5Tb
ouoAZ4AXmGiT07MYN3MuhtlZZTj+eYU5ijgk50bIEauz9fQriTaagCsr+yzpEu7g
q23rnLHJpjHpGOdXuq1RM6OEthhsd71fWtHwhf5xlrMPtSKQ7jL0t6BYWgaH2XZ+
vO8fLELNXjvg2155NWQxq87/QgBg7A2qYr3i+oynSvXMs1ghVQsTVR8CAwEAAaNT
MFEwHQYDVR0OBBYEFKhNXeHt95YUpRLcFA33dB4xJJJrMB8GA1UdIwQYMBaAFKhN
XeHt95YUpRLcFA33dB4xJJJrMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQEL
BQADggEBADnEovRSIlfjA75vQ3USwhUm25gK2G+4xv1tbfpLkjFOXlAw++aMwgJY
o7QHswF8weTsBGmQvjD5+DnGid3v4B3UjCGrCE3uVK5tL7CZGqGxCa+U7d7zzv19
DMvJ/P9nTmxXL/GfP7+kk02JatwZVOl+2lz4RpzigAFL7AJRSQXpkgc8IqlHmKJ8
Dk+vpvjd4ZiPeamS/AUfDk+qB2ROcUPTw6vRRTi+KyYPqHNXP/jcxZQGgoX+nAsq
3GbSkVJP52Y9jZ0CP46GLd2zr8BK+/6Ci5hd+OJUivNQrfnmkle6sNT3DTINWwx8
Ft03sKaJD2cXbdVktuRKMs28My4v9e8=
-----END CERTIFICATE-----
"""

CONFIG: dict = {
    "schema_version": 1,
    "deployment": "lab-a",
    "backend": "libvirt",
    "target": {
        "libvirt": {
            "uri": "qemu+ssh://vcows@vcows/system",
            "pool": "images",
            "ssh_key": SSH_KEY,
            "known_hosts": KNOWN_HOSTS,
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

    `orchestrator` configures it at package import and `test_logging.py` calls
    `configure_logging()` again, and both *replace* the root handler list rather
    than adding to it, so a test that runs either changes what every later test
    reads off stderr.

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

    `cli.main` sets 0o077 and the tests call it in-process, so without this the
    first CLI test changes the mode of every file every later test writes.
    """
    before = os.umask(0o022)
    os.umask(before)
    yield
    os.umask(before)


@pytest.fixture
def cfg() -> dict:
    """A fresh deep copy, so a test that mutates it cannot poison the next."""
    return copy.deepcopy(CONFIG)


#: The Proxmox counterpart of CONFIG above, exercising what differs rather than
#: mirroring it: a NIC attaches to a bridge and only a bridge, one VM carries a
#: VLAN tag, and firmware is a choice with no host paths beside it.
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
            # A fixed, obviously-fake UUID for a fake that never authenticates.
            "token": "vcows@pve!deploy=00000000-0000-4000-8000-000000000000",
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
def pve_token() -> str:
    """The token `PROXMOX_CONFIG` carries, for the tests that assert on its value.

    Sets nothing: it hands back the one string a test asserts is absent from a
    log line or a rendered values dict.
    """
    return PROXMOX_CONFIG["target"]["proxmox"]["token"]


#: The vSphere counterpart of the two configs above, exercising what differs
#: rather than mirroring them: the placement is a cluster and not a host, a NIC
#: names no network because the port group is named once under the target, and
#: there is no VLAN tag anywhere -- the port group carries it.
VSPHERE_CONFIG: dict = {
    "schema_version": 1,
    "deployment": "lab-a",
    "backend": "vsphere",
    "target": {
        "vsphere": {
            "endpoint": "https://vcenter.example.com",
            "user": "vcows@vsphere.local",
            # An obviously-fake password for a fake that never logs in.
            "password": "not-a-password",
            "datacenter": "dc-a",
            "datastore": "ds-a",
            "network": "pg-vcows",
            "cluster": "cluster-a",
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
            "user_data": "#cloud-config\npackages:\n  - tmux\n",
            "nics": [
                {
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
def vsphere_cfg() -> dict:
    """A fresh deep copy, for the same reason `cfg` is one."""
    return copy.deepcopy(VSPHERE_CONFIG)
