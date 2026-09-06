"""`render` is pure, so it is golden-file tested byte for byte.

Same treatment as the other two backends' renders, and for the same reason:
every value `create` sees comes through here, and a diff in the golden file is
the cheapest possible review of what changed about a deploy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.backends.vsphere.render import render
from orchestrator.marker import from_description
from tests.conftest import dumped

GOLDEN = Path(__file__).parent / "golden" / "vsphere.tfvars.json"


@pytest.fixture
def prepared():
    """What `prepare` returns: the seed ISOs it built, preflight's answer about
    whether the golden image is already a template here, and -- because that
    answer was "no" -- the VMDK it converted and the virtual size it read while
    it had the file open."""
    return {
        "seed_isos": {
            "app01": "/runs/lab-a/seed/app01-seed.iso",
            "app02": "/runs/lab-a/seed/app02-seed.iso",
        },
        "image": {"create": True, "template": "golden.qcow2"},
        "vmdk": "/runs/lab-a/golden.vmdk",
        "capacity": 21474836480,
    }


def test_matches_the_golden_file(vsphere_cfg, prepared):
    assert dumped(render(vsphere_cfg, prepared)) == GOLDEN.read_text()


def test_render_does_no_io(vsphere_cfg, prepared, monkeypatch):
    """The one property that makes a golden file meaningful, and the reason
    `capacity` is read in `prepare`: it is the golden image's virtual size, and
    reading it here would be a read of a multi-GB file from a pure function."""

    def refuse(*args, **kwargs):
        raise AssertionError("render touched the filesystem")

    monkeypatch.setattr(Path, "open", refuse)
    monkeypatch.setattr("builtins.open", refuse)
    render(vsphere_cfg, prepared)


def test_no_credential_and_no_operator_free_text_is_rendered(vsphere_cfg, prepared):
    """`api.connect` reads the user and the password out of `target.vsphere` and
    nothing copies that block here. If either reached these values it would sit
    in the run directory in plaintext, which is what this design exists to
    avoid.

    The same goes for `user_data`, which is where an operator's own secrets
    actually end up: it is built into the seed ISO and named nowhere here.
    """
    vsphere_cfg["target"]["vsphere"]["password"] = "SUPERSECRETVALUE"  # noqa: S105
    vsphere_cfg["vms"][0]["user_data"] = "#cloud-config\npassword: hunter2\n"
    rendered = dumped(render(vsphere_cfg, prepared))
    assert "SUPERSECRETVALUE" not in rendered
    assert "hunter2" not in rendered
    assert "user_data" not in rendered
    assert "password" not in rendered


def test_the_marker_round_trips_per_vm(vsphere_cfg, prepared):
    """The annotation is the whole of this backend's identity: a renamed VM is
    still ours because of what is in this field."""
    for name, vm in render(vsphere_cfg, prepared)["vms"].items():
        marker = from_description(vm["annotation"])
        assert marker is not None
        assert marker.name == name
        assert marker.deployment == "lab-a"


def test_firmware_is_the_configs_own_word(vsphere_cfg, prepared):
    """vSphere's `ConfigSpec` takes `efi` and `bios` unchanged, so unlike the
    Proxmox backend there is nothing to translate and nothing that could be
    translated wrongly."""
    assert render(vsphere_cfg, prepared)["vms"]["app02"]["firmware"] == "efi"
    vsphere_cfg["vms"][0]["firmware"] = "bios"
    assert render(vsphere_cfg, prepared)["vms"]["app01"]["firmware"] == "bios"


def test_efi_is_the_default_when_firmware_is_unset(vsphere_cfg, prepared):
    assert "firmware" not in vsphere_cfg["vms"][0]
    assert render(vsphere_cfg, prepared)["vms"]["app01"]["firmware"] == "efi"


def test_every_nic_carries_the_targets_one_port_group(vsphere_cfg, prepared):
    """A NIC names no network on this backend -- the schema refuses the key --
    so the value has to arrive from the target block, onto each of them."""
    vsphere_cfg["vms"][0]["nics"].append(
        {"ip_cidr": "10.0.0.5/24", "gateway": "10.0.0.1"}
    )
    nics = render(vsphere_cfg, prepared)["vms"]["app01"]["nics"]
    assert [nic["network"] for nic in nics] == ["pg-vcows", "pg-vcows"]


def test_a_configured_mac_wins_over_the_derived_one(vsphere_cfg, prepared):
    """cloud-init matches an interface by MAC, so the derivation is not a knob;
    a MAC the config states is still the one used."""
    nics = render(vsphere_cfg, prepared)["vms"]
    assert nics["app02"]["nics"][0]["mac"] == "52:54:00:aa:bb:cc"
    assert nics["app01"]["nics"][0]["mac"].startswith("52:54:00:")


def test_the_adapter_defaults_to_vmxnet3_and_the_config_overrides_it(
    vsphere_cfg, prepared
):
    """The schema offers three, so a value it accepts has to reach `create`."""
    assert (
        render(vsphere_cfg, prepared)["vms"]["app01"]["nics"][0]["model"] == "vmxnet3"
    )
    vsphere_cfg["vms"][0]["nics"][0]["model"] = "e1000e"
    assert render(vsphere_cfg, prepared)["vms"]["app01"]["nics"][0]["model"] == "e1000e"


def test_configured_address_is_the_primary_nics(vsphere_cfg, prepared):
    vsphere_cfg["vms"][0]["nics"].append(
        {"ip_cidr": "10.0.0.5/24", "gateway": "10.0.0.1", "primary": True}
    )
    address = render(vsphere_cfg, prepared)["vms"]["app01"]["configured_address"]
    assert address == "10.0.0.5"


def test_the_two_knobs_reach_the_values_they_steer(vsphere_cfg, prepared):
    """Both are first-contact knobs: an operator flips one in the config a
    delivered bundle already carries, and nothing else changes."""
    rendered = render(vsphere_cfg, prepared)
    assert rendered["image"]["import"] == "ovf"
    assert rendered["vms"]["app01"]["clone"] == "linked"

    vsphere_cfg["target"]["vsphere"]["import"] = "datastore"
    vsphere_cfg["target"]["vsphere"]["clone"] = "full"
    rendered = render(vsphere_cfg, prepared)
    assert rendered["image"]["import"] == "datastore"
    assert rendered["vms"]["app01"]["clone"] == "full"


def test_nothing_is_imported_once_the_template_is_there(vsphere_cfg):
    """`prepare` converts nothing in that case, so there is no VMDK to name and
    no capacity to declare -- and `create` reads the same two keys either way."""
    prepared = {
        "seed_isos": {"app01": "/s/app01.iso", "app02": "/s/app02.iso"},
        "image": {"create": False, "template": "golden.qcow2"},
    }
    image = render(vsphere_cfg, prepared)["image"]
    assert image["create"] is False
    assert image["template"] == "golden.qcow2"
    assert image["vmdk"] == ""
    assert image["capacity"] == 0


def test_only_the_vms_it_is_given_are_rendered(vsphere_cfg, prepared):
    """`cli._deploy` narrows the config before calling this, so the module only
    ever creates."""
    vsphere_cfg["vms"] = [vsphere_cfg["vms"][0]]
    assert set(render(vsphere_cfg, prepared)["vms"]) == {"app01"}
