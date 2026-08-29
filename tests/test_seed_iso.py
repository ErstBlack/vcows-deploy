"""The NoCloud seed ISO.

Cross-read with the *other* toolchain than the one that built it, as spike A1 did:
pycdlib writes, xorriso reads back. A builder that can only be verified by its own
reader proves nothing about whether cloud-init will find the files.

Not `isoinfo` -- findings.md R4 names it, but it ships with genisoimage/cdrkit and
is absent from Rocky 10.2. See docs/spikes.md.
"""

from __future__ import annotations

import io
import subprocess

import pytest
import yaml

from orchestrator.backends.libvirt import prepare
from orchestrator.backends.libvirt.schema import derive_mac

pycdlib = pytest.importorskip("pycdlib")


@pytest.fixture
def iso(cfg, tmp_path):
    files = prepare.seed_files(cfg["vms"][0], cfg)
    return prepare.build_seed_iso(files, tmp_path / "app01-seed.iso")


def read_via_pycdlib(path, name: str) -> bytes:
    r = pycdlib.PyCdlib()
    r.open(str(path))
    try:
        buf = io.BytesIO()
        r.get_file_from_iso_fp(buf, joliet_path=f"/{name}")
        rr = io.BytesIO()
        r.get_file_from_iso_fp(rr, rr_path=f"/{name}")
        assert buf.getvalue() == rr.getvalue(), "Joliet and Rock Ridge disagree"
        return buf.getvalue()
    finally:
        r.close()


# -- structure --------------------------------------------------------------


def test_volume_label_is_cidata(iso):
    """Both cloud-init and libvirt find a NoCloud datasource by this label."""
    pvd = iso.read_bytes()[32768 : 32768 + 190]
    assert pvd[40:72].decode("ascii").strip() == "cidata"


def test_all_three_files_are_present_to_xorriso(iso):
    """The cross-read: a different implementation walks the tree pycdlib wrote."""
    out = subprocess.run(
        ["xorriso", "-indev", str(iso), "-find", "/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for name in ("user-data", "meta-data", "network-config"):
        assert f"'/{name}'" in out, out


def test_iso9660_identifiers_conform(iso):
    assert prepare.iso_path("network-config") == "/NETWORK_CONFIG.;1"
    r = pycdlib.PyCdlib()
    r.open(str(iso))
    try:
        names = [c.file_identifier().decode() for c in r.list_children(iso_path="/")]
    finally:
        r.close()
    assert "NETWORK_CONFIG.;1" in names


def test_the_build_is_reproducible(cfg, tmp_path):
    """Same inputs, same bytes -- so a run directory kept for debugging can be
    compared against a rebuild."""
    files = prepare.seed_files(cfg["vms"][0], cfg)
    a = prepare.build_seed_iso(files, tmp_path / "a.iso")
    b = prepare.build_seed_iso(files, tmp_path / "b.iso")
    assert a.read_bytes() == b.read_bytes()


# -- content ----------------------------------------------------------------


def test_meta_data_carries_the_marker_id(iso, cfg):
    from orchestrator.marker import derive_id

    meta = yaml.safe_load(read_via_pycdlib(iso, "meta-data"))
    assert meta == {
        "instance-id": derive_id("app01", cfg["deployment"]),
        "local-hostname": "app01",
    }


def test_two_deployments_do_not_share_one_seed(cfg, tmp_path):
    """Two configs differing only in `deployment` must not produce identical
    media. Both the instance-id and the MAC carry the deployment, so a guest
    cannot be handed another deployment's identity."""
    a = prepare.seed_files(cfg["vms"][0], cfg)
    cfg["deployment"] = "lab-b"
    b = prepare.seed_files(cfg["vms"][0], cfg)
    assert a["meta-data"] != b["meta-data"]
    assert a["network-config"] != b["network-config"]
    assert (
        prepare.build_seed_iso(a, tmp_path / "a.iso").read_bytes()
        != prepare.build_seed_iso(b, tmp_path / "b.iso").read_bytes()
    )


def test_network_config_matches_by_mac(iso, cfg):
    net = yaml.safe_load(read_via_pycdlib(iso, "network-config"))
    assert net["version"] == 2
    nic = net["ethernets"]["nic0"]
    assert nic["match"]["macaddress"] == derive_mac("app01", 0, cfg["deployment"])
    assert nic["dhcp4"] is False
    assert nic["addresses"] == ["192.168.122.60/24"]
    # A CIDR, not netplan's `default`: cloud-init 24.4 throws
    # "Address default is not a valid ip address" out of its v2-to-v1 route
    # normaliser, then falls back to DHCP and boots healthy on the wrong
    # address. Measured in the acceptance run.
    assert nic["routes"] == [{"to": "0.0.0.0/0", "via": "192.168.122.1"}]
    assert nic["nameservers"] == {"addresses": ["192.168.122.1"]}


def test_a_mac_is_quoted_not_read_as_a_number(iso):
    """YAML 1.1 reads 52:54:00 as sexagesimal. Round-tripping through the parser
    is what proves it did not."""
    raw = read_via_pycdlib(iso, "network-config").decode()
    assert "52:54:00" in raw
    net = yaml.safe_load(raw)
    assert isinstance(net["ethernets"]["nic0"]["match"]["macaddress"], str)


def test_user_data_is_passed_through_verbatim(cfg, tmp_path):
    """vcows owns meta-data and network-config; this half is not interpreted."""
    files = prepare.seed_files(cfg["vms"][1], cfg)
    assert files["user-data"] == cfg["vms"][1]["user_data"].encode()
    iso = prepare.build_seed_iso(files, tmp_path / "app02-seed.iso")
    assert read_via_pycdlib(iso, "user-data") == cfg["vms"][1]["user_data"].encode()


def test_user_data_defaults_to_just_the_hostname(cfg):
    files = prepare.seed_files(cfg["vms"][0], cfg)
    assert files["user-data"] == b"#cloud-config\nhostname: app01\n"


def test_nameservers_are_omitted_when_unset(cfg):
    del cfg["vms"][0]["nics"][0]["nameservers"]
    net = yaml.safe_load(prepare.seed_files(cfg["vms"][0], cfg)["network-config"])
    assert "nameservers" not in net["ethernets"]["nic0"]


def test_multiple_nics_each_get_their_own_match(cfg):
    second = dict(cfg["vms"][0]["nics"][0])
    second["ip_cidr"] = "192.168.122.70/24"
    cfg["vms"][0]["nics"].append(second)
    net = yaml.safe_load(prepare.seed_files(cfg["vms"][0], cfg)["network-config"])
    assert set(net["ethernets"]) == {"nic0", "nic1"}
    macs = {k: v["match"]["macaddress"] for k, v in net["ethernets"].items()}
    assert macs["nic0"] != macs["nic1"]


def test_build_all_names_one_iso_per_vm(cfg, tmp_path):
    built = prepare.build_all(cfg, tmp_path)
    assert set(built) == {"app01", "app02"}
    for name, path in built.items():
        assert path.endswith(f"{name}-seed.iso")
        assert (tmp_path / f"{name}-seed.iso").exists()
