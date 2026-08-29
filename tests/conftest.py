"""Shared fixtures, and the gates every OpenTofu-backed test shares.

`needs_tofu`, `needs_tofu_binary` and `tofu_env` live here rather than in one test
file because three of them now drive the binary: the module gate, the driver gate
and the CLI gate.

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

#: For tests that apply the *libvirt* module, which needs the pinned provider.
needs_tofu = pytest.mark.skipif(
    TOFU is None or not MIRROR.is_dir(),
    reason=(
        "needs `tofu` on PATH and a provider mirror at .tools/tofu-mirror; "
        "see the Stage 2 prerequisites in the plan"
    ),
)

#: For tests whose module uses only builtin providers, where `init` installs
#: nothing and contacts nothing -- verified against 1.12.6 with an empty mirror.
needs_tofu_binary = pytest.mark.skipif(TOFU is None, reason="needs `tofu` on PATH")


def tofu_env(workdir: Path, mirror: Path = MIRROR) -> dict:
    """A CLI config pointing at a filesystem mirror only.

    `/etc/tofurc` is not a path OpenTofu reads, and under a rootless container a
    UID absent from /etc/passwd gets HOME=/, so even a correct ~/.tofurc is
    missed. TF_CLI_CONFIG_FILE is the only reliable lever (findings.md R6).
    """
    rc = workdir / "tofurc"
    rc.write_text(
        f"provider_installation {{\n"
        f"  filesystem_mirror {{\n"
        f'    path    = "{mirror}"\n'
        f'    include = ["registry.opentofu.org/dmacvicar/libvirt"]\n'
        f"  }}\n"
        f"  direct {{\n"
        f'    exclude = ["registry.opentofu.org/dmacvicar/libvirt"]\n'
        f"  }}\n"
        f"}}\n"
    )
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
