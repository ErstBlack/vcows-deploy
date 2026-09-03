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
def prepared():
    """What `prepare` resolves: the seed ISOs it built, and preflight's answer
    about whether the golden image is already on the cluster."""
    return Prepared(
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


def test_no_credential_and_no_operator_free_text_is_rendered(
    pve_cfg, prepared, pve_token
):
    """`api.connect` reads the credential out of `target.proxmox` and nothing
    copies that block here. If either form reached these values it would sit in
    the run directory in plaintext, which is the thing this design exists to
    avoid.

    The same goes for `user_data`, which is where an operator's own secrets
    actually end up: it is built into the seed ISO and named nowhere here.
    """
    pve_cfg["target"]["proxmox"]["password"] = "SUPERSECRETVALUE"  # noqa: S105
    pve_cfg["vms"][0]["user_data"] = "#cloud-config\npassword: hunter2\n"
    rendered = dumped(render(pve_cfg, prepared))
    assert pve_token not in rendered
    assert "SUPERSECRETVALUE" not in rendered
    assert "hunter2" not in rendered
    assert "user_data" not in rendered
    assert "api_token" not in rendered


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


def test_the_image_is_not_re_uploaded_once_it_is_there(pve_cfg):
    """The apply runs against a fresh state every time, so without `create` it
    would push a multi-GB image on every deploy after the first."""
    prepared = Prepared(
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
    task trying to regenerate it. Not even for the name that comes closest."""
    import re

    pve_cfg["vms"][0]["name"] = "vm-100-cloudinit"
    prepared.artifacts["seed_isos"]["vm-100-cloudinit"] = "/runs/lab-a/seed/x.iso"
    for name, vm in render(pve_cfg, prepared)["vms"].items():
        assert vm["seed_name"] == f"{name}-seed.iso"
        assert not re.match(r"^vm-\d+-cloudinit\.iso$", vm["seed_name"])


def test_the_per_vm_values_the_config_overrides_reach_the_tfvars(pve_cfg, prepared):
    """Each has a default the fixture happens to agree with, so a key read from
    the wrong name renders the right value anyway until something disagrees."""
    pve_cfg["vms"][0]["machine"] = "pc"
    pve_cfg["vms"][0]["os_type"] = "win11"
    pve_cfg["vms"][0]["nics"][0]["model"] = "e1000"
    app01 = render(pve_cfg, prepared)["vms"]["app01"]
    assert (app01["machine"], app01["os_type"]) == ("pc", "win11")
    assert app01["nics"][0]["model"] == "e1000"


def test_the_target_values_the_config_overrides_reach_the_provider(pve_cfg, prepared):
    """`insecure` decides whether the apply verifies the cluster's certificate,
    and the checksum is what the provider verifies the upload against."""
    pve_cfg["target"]["proxmox"]["insecure"] = True
    pve_cfg["image"]["sha256"] = "a" * 64
    tfvars = render(pve_cfg, prepared)
    assert tfvars["insecure"] is True
    assert tfvars["image"]["checksum"] == "a" * 64
