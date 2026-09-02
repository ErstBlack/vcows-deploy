"""`render` is pure, so it is golden-file tested byte for byte.

The golden file is the tfvars document OpenTofu actually consumes. Comparing it
whole rather than field by field is what makes an accidental rename or a dropped
key show up as a diff instead of as a passing test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.backends.base import Prepared
from orchestrator.backends.libvirt import render as render_mod
from orchestrator.backends.libvirt.render import render
from orchestrator.marker import Marker

GOLDEN = Path(__file__).parent / "golden" / "libvirt.tfvars.json"


@pytest.fixture
def prepared(tmp_path):
    """What `prepare` resolves against the session: the seed ISOs it built, and
    whether the golden image is already on this host."""
    return Prepared(
        workdir=tmp_path,
        artifacts={
            "seed_isos": {
                "app01": "/run/vcows/lab-a/app01-seed.iso",
                "app02": "/run/vcows/lab-a/app02-seed.iso",
            },
            "base_volume": {"name": "golden.qcow2", "create": True, "path": ""},
        },
    )


def dumped(tfvars: dict) -> str:
    return json.dumps(tfvars, indent=2, sort_keys=True) + "\n"


def test_matches_the_golden_file(cfg, prepared):
    assert dumped(render(cfg, prepared)) == GOLDEN.read_text()


def test_render_does_no_io(cfg, prepared, monkeypatch):
    """Pure means pure: the config names paths that do not exist here."""
    monkeypatch.setattr(
        "builtins.open", lambda *a, **k: pytest.fail("render() opened a file")
    )
    render(cfg, prepared)


# -- the parts worth naming -------------------------------------------------


def test_marker_xml_round_trips_per_vm(cfg, prepared):
    for name, vm in render(cfg, prepared)["vms"].items():
        payload = vm["marker_xml"].split(">", 1)[1].rsplit("<", 1)[0]
        marker = Marker.from_json(payload)
        assert (marker.name, marker.deployment) == (name, "lab-a")
        assert marker.id == Marker.for_vm(name, "lab-a").id


def test_names_are_undecorated(cfg, prepared):
    vm = render(cfg, prepared)["vms"]["app01"]
    assert vm["domain_name"] == "app01"
    assert vm["overlay_name"] == "app01.qcow2"
    assert vm["seed_name"] == "app01-seed.iso"


def test_capacity_is_in_bytes_on_the_overlay(cfg, prepared):
    """A4: the base volume's declared capacity is discarded by the upload, so the
    only number that survives is this one."""
    assert render(cfg, prepared)["vms"]["app01"]["disk_bytes"] == 40 * 1024**3


def test_a_nic_carries_both_union_keys_with_one_null(cfg, prepared):
    """A ternary between two differently-shaped objects does not type-check in
    HCL, so the shape stays uniform and the choice lives in the values."""
    nic = render(cfg, prepared)["vms"]["app01"]["nics"][0]
    assert nic["network"] == "default"
    assert nic["bridge"] is None

    cfg["vms"][0]["nics"][0] = {
        "bridge": "br0",
        "ip_cidr": "192.168.122.60/24",
        "gateway": "192.168.122.1",
    }
    nic = render(cfg, prepared)["vms"]["app01"]["nics"][0]
    assert (nic["network"], nic["bridge"]) == (None, "br0")


def test_firmware_defaults_and_overrides(cfg, prepared):
    vms = render(cfg, prepared)["vms"]
    assert vms["app01"]["firmware"] == "efi"
    assert vms["app01"]["loader"] is None
    assert vms["app01"]["machine"] == "q35"
    assert vms["app02"]["loader_format"] == "qcow2"


def test_the_per_vm_values_the_config_overrides_reach_the_tfvars(cfg, prepared):
    """Each has a default the fixture happens to agree with, so a key read from
    the wrong name renders the right value anyway until something disagrees."""
    cfg["vms"][0]["firmware"] = "bios"
    cfg["vms"][0]["machine"] = "pc"
    cfg["vms"][0]["nics"][0]["model"] = "e1000"
    app01 = render(cfg, prepared)["vms"]["app01"]
    assert (app01["firmware"], app01["machine"]) == ("bios", "pc")
    assert app01["nics"][0]["model"] == "e1000"


def test_configured_address_is_the_primary_nics(cfg, prepared):
    second = dict(cfg["vms"][0]["nics"][0])
    second["ip_cidr"] = "192.168.122.70/24"
    second["primary"] = True
    cfg["vms"][0]["nics"].append(second)
    vms = render(cfg, prepared)["vms"]
    assert vms["app01"]["configured_address"] == "192.168.122.70"


def test_base_volume_when_it_is_already_on_the_host(cfg, tmp_path):
    """Each apply runs against a fresh state, so without this the module would
    try to create an existing volume on every deploy after the first."""
    prepared = Prepared(
        workdir=tmp_path,
        artifacts={
            "seed_isos": {"app01": "/a.iso", "app02": "/b.iso"},
            "base_volume": {
                "name": "golden.qcow2",
                "create": False,
                "path": "/var/lib/libvirt/images/golden.qcow2",
            },
        },
    )
    base = render(cfg, prepared)["base_volume"]
    assert base == {
        "name": "golden.qcow2",
        "create": False,
        "path": "/var/lib/libvirt/images/golden.qcow2",
        "source": "",
    }


def test_only_the_vms_it_is_given_are_rendered(cfg, prepared):
    """The caller narrows `vms` to the CREATE set before this point."""
    cfg["vms"] = [cfg["vms"][0]]
    assert set(render(cfg, prepared)["vms"]) == {"app01"}


def test_module_helpers_agree_with_the_rendered_names():
    assert render_mod.overlay_name("app01") == "app01.qcow2"
    assert render_mod.seed_name("app01") == "app01-seed.iso"


def test_the_provider_is_given_the_transport_that_can_reach_the_host(cfg, prepared):
    """The two clients need different schemes, and getting it wrong fails only at
    apply time, after a multi-GB upload.

    libvirt's own client does not recognise `sshcmd` at all. The provider's `ssh`
    dials a hardcoded monolithic socket that a split-daemon host does not have,
    through a forward SELinux refuses. Both measured against the rig; the apply is
    the only thing that would have noticed.
    """
    from orchestrator.backends.libvirt.schema import connection_uri

    target = cfg["target"]["libvirt"]
    rendered = render(cfg, prepared)["uri"]
    assert rendered.startswith("qemu+sshcmd://")
    assert rendered == connection_uri(target, "sshcmd")
    assert rendered != connection_uri(target), "preflight and the apply differ"
    # Credentials travel through ~/.ssh/config, which the container's entrypoint
    # writes -- no spelling of the URI parameters reaches both clients.
    assert "?" not in rendered


# -- the other half of the module contract ---------------------------------


def test_a_missing_vms_output_is_a_broken_module_not_an_empty_inventory():
    """`parse_outputs` exists so the module's `output` block is not the public
    API. Reading a renamed output as `{}` spends that isolation on silence: the
    deploy records `created 0 VM(s)` and `outcome: ok` beside an `inventory.json`
    that contradicts both."""
    from orchestrator.backends.libvirt import LibvirtBackend

    backend = LibvirtBackend()
    assert backend.parse_outputs({"vms": {"value": {"app01": {}}}}).vms == {"app01": {}}
    with pytest.raises(ValueError, match="vms"):
        backend.parse_outputs({"something_else": {"value": "not an inventory"}})
