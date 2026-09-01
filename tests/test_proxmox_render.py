"""`render` is pure, so it is golden-file tested byte for byte.

Same treatment as the libvirt backend's, and for the same reason: every value the
apply sees comes through here, and a diff in the golden file is the cheapest
possible review of what changed about a deploy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.backends.base import Prepared
from orchestrator.backends.proxmox.render import render
from orchestrator.marker import from_description

GOLDEN = Path(__file__).parent / "golden" / "proxmox.tfvars.json"


@pytest.fixture
def prepared(tmp_path):
    """What `prepare` resolves: the seed ISOs it built, and preflight's answer
    about whether the golden image is already on the cluster."""
    return Prepared(
        workdir=tmp_path,
        artifacts={
            "seed_isos": {
                "app01": "/runs/lab-a/seed/app01-seed.iso",
                "app02": "/runs/lab-a/seed/app02-seed.iso",
            },
            "image": {"create": True, "volid": "local:import/golden.qcow2"},
        },
    )


def dumped(tfvars: dict) -> str:
    return json.dumps(tfvars, indent=2, sort_keys=True) + "\n"


def test_matches_the_golden_file(pve_cfg, prepared):
    assert dumped(render(pve_cfg, prepared)) == GOLDEN.read_text()


def test_render_does_no_io(pve_cfg, prepared, monkeypatch):
    """The one property that makes a golden file meaningful."""

    def refuse(*args, **kwargs):
        raise AssertionError("render touched the filesystem")

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    render(pve_cfg, prepared)


def test_no_credential_is_rendered(pve_cfg, prepared, pve_token):
    """The provider reads PROXMOX_VE_API_TOKEN from its own environment. If the
    token ever reached the tfvars it would sit in the run directory in plaintext,
    which is the thing this design exists to avoid."""
    assert pve_token not in dumped(render(pve_cfg, prepared))
    assert "api_token" not in dumped(render(pve_cfg, prepared))


def test_the_marker_round_trips_per_vm(pve_cfg, prepared):
    for name, vm in render(pve_cfg, prepared)["vms"].items():
        marker = from_description(vm["description"])
        assert marker is not None
        assert marker.name == name
        assert marker.deployment == "lab-a"


def test_firmware_is_translated_into_pve_vocabulary(pve_cfg, prepared):
    """The config keeps libvirt's efi/bios so one operator reads both backends'
    configs; PVE's own words appear only here."""
    vms = render(pve_cfg, prepared)["vms"]
    assert vms["app02"]["bios"] == "ovmf"
    pve_cfg["vms"][0]["firmware"] = "bios"
    assert render(pve_cfg, prepared)["vms"]["app01"]["bios"] == "seabios"


def test_efi_is_the_default_when_firmware_is_unset(pve_cfg, prepared):
    assert "firmware" not in pve_cfg["vms"][0]
    assert render(pve_cfg, prepared)["vms"]["app01"]["bios"] == "ovmf"


def test_a_nic_carries_a_null_vlan_rather_than_omitting_it(pve_cfg, prepared):
    """A map of objects in HCL must have a uniform shape."""
    nics = render(pve_cfg, prepared)["vms"]
    assert nics["app01"]["nics"][0]["vlan_id"] is None
    assert nics["app02"]["nics"][0]["vlan_id"] == 42


def test_a_configured_mac_wins_over_the_derived_one(pve_cfg, prepared):
    nics = render(pve_cfg, prepared)["vms"]
    assert nics["app02"]["nics"][0]["mac"] == "52:54:00:aa:bb:cc"
    assert nics["app01"]["nics"][0]["mac"].startswith("52:54:00:")


def test_configured_address_is_the_primary_nics(pve_cfg, prepared):
    pve_cfg["vms"][0]["nics"].append(
        {
            "bridge": "vmbr1",
            "ip_cidr": "10.0.0.5/24",
            "gateway": "10.0.0.1",
            "primary": True,
        }
    )
    assert render(pve_cfg, prepared)["vms"]["app01"]["configured_address"] == "10.0.0.5"


def test_the_image_is_not_re_uploaded_once_it_is_there(pve_cfg, tmp_path):
    """The apply runs against a fresh state every time, so without `create` it
    would push a multi-GB image on every deploy after the first."""
    prepared = Prepared(
        workdir=tmp_path,
        artifacts={
            "seed_isos": {"app01": "/s/app01.iso", "app02": "/s/app02.iso"},
            "image": {"create": False, "volid": "local:import/golden.qcow2"},
        },
    )
    image = render(pve_cfg, prepared)["image"]
    assert image["create"] is False
    assert image["volid"] == "local:import/golden.qcow2"
    # Nothing to upload, so nothing names the local file.
    assert image["source"] == ""


def test_only_the_vms_it_is_given_are_rendered(pve_cfg, prepared):
    """`cli._deploy` narrows the config before calling this, so the module only
    ever creates."""
    pve_cfg["vms"] = [pve_cfg["vms"][0]]
    assert set(render(pve_cfg, prepared)["vms"]) == {"app01"}


def test_the_seed_name_is_not_one_proxmox_claims(pve_cfg, prepared):
    """Proxmox pattern-matches `vm-<vmid>-cloudinit.iso` and fails the VM's start
    task trying to regenerate it."""
    import re

    for vm in render(pve_cfg, prepared)["vms"].values():
        assert not re.match(r"^vm-\d+-cloudinit\.iso$", vm["seed_name"])
