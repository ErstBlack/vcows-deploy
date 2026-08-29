"""Shared fixtures, and the gates every OpenTofu-backed test shares.

`needs_tofu`, `needs_tofu_binary` and `tofu_env` live here rather than in one test
file because three of them now drive the binary: the module gate, the driver gate
and the CLI gate.

**A gate that quietly passes because it did not run is worse than no gate**, and in
aggregate that is what a bare `pytest -q` does: 25 skips, exit 0, with nothing
saying the module was never looked at. `VCOWS_GATES` is the opt-in that turns a
named gate's skip into a failure -- `VCOWS_GATES=tofu`, or `all` for every one of
them. Named rather than only `all` because the gates differ in what they need: a
missing `tofu` is a fixable local omission, while `rig` and `image` need hardware
and a build, and a run proving the module was checked should not have to fail on
those too.

The config below is the canonical one: two VMs covering both firmware branches
(libvirt-selected and explicitly pinned) and both MAC branches (derived and
overridden), on the `default` network with statics from the .60-.70 range that is
confirmed free on the rig.
"""

from __future__ import annotations

import copy
import os
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MIRROR = REPO / ".tools" / "tofu-mirror"

TOFU = shutil.which("tofu")

#: Gates the operator demanded. Comma-separated names, or `all`.
GATES = {g for g in os.environ.get("VCOWS_GATES", "").split(",") if g}


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


#: For tests that apply the *libvirt* module, which needs the pinned provider.
NEEDS_TOFU = (
    "needs `tofu` on PATH and a provider mirror at .tools/tofu-mirror; "
    "see the Stage 2 prerequisites in the plan"
)
needs_tofu = gate("tofu", TOFU is not None and MIRROR.is_dir(), NEEDS_TOFU)

#: For tests whose module uses only builtin providers, where `init` installs
#: nothing and contacts nothing -- verified against 1.12.6 with an empty mirror.
needs_tofu_binary = gate("tofu", TOFU is not None, "needs `tofu` on PATH")


#: The CLI config the image ships, and the mirror path baked into it.
SHIPPED_TOFURC = REPO / "container" / "tofurc"
IMAGE_MIRROR = "/opt/tofu-mirror"


def tofu_env(workdir: Path, mirror: Path = MIRROR) -> dict:
    """A CLI config pointing at a filesystem mirror only.

    `/etc/tofurc` is not a path OpenTofu reads, and under a rootless container a
    UID absent from /etc/passwd gets HOME=/, so even a correct ~/.tofurc is
    missed. TF_CLI_CONFIG_FILE is the only reliable lever (findings.md R6).

    **This is the shipped file with one path substituted, not a second config.**
    The two had opposite fallback behaviour while they were separate: the test one
    carried a `direct` block, so an unmirrored provider was fetched from the
    registry and the suite went green, while the image has no `direct` block by
    design and fails immediately at a site. Reading the real file means the
    default suite exercises the air-gap config, and there is one fewer document to
    keep true.
    """
    shipped = SHIPPED_TOFURC.read_text()
    # Without this the substitution silently does nothing and every gate below
    # points at a mirror that is not there, which reads as a provider problem.
    assert IMAGE_MIRROR in shipped, f"{SHIPPED_TOFURC} no longer names {IMAGE_MIRROR}"
    rc = workdir / "tofurc"
    rc.write_text(shipped.replace(IMAGE_MIRROR, str(mirror)))
    return {
        **os.environ,
        "TF_CLI_CONFIG_FILE": str(rc),
        "CHECKPOINT_DISABLE": "1",
        # Residual egress should fail fast rather than hang at a site.
        "no_proxy": "*",
        # Diagnostics are matched as text. NO_COLOR is *not* honoured by 1.12.6
        # -- colour is written even to a file -- so the callers that need clean
        # text pass `-no-color` themselves.
        "NO_COLOR": "1",
    }


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


@pytest.fixture
def cfg() -> dict:
    """A fresh deep copy, so a test that mutates it cannot poison the next."""
    return copy.deepcopy(CONFIG)
