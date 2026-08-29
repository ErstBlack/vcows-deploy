"""The `target.libvirt` block and the per-VM shape -- findings.md F11.

This is the one-way door, so every check gets a rejecting case *and* an accepting
one. A validator that rejects everything passes half a suite.
"""

from __future__ import annotations

import struct

import pytest

from orchestrator.backends.libvirt import schema
from orchestrator.config import core_schema
from orchestrator.config import validate as core_validate
from orchestrator.marker import VCOWS_NS
from tests.fake_backend import FakeBackend


class LibvirtSchemaOnly(FakeBackend):
    """Stage-2 harness: the real schema behind the rest of the fake backend.

    The libvirt `Backend` subclass does not exist yet by design (D28), but the
    schema still has to be proven against the registry composition rather than
    only in isolation.
    """

    def __init__(self):
        super().__init__(name="libvirt")

    def config_schema(self) -> dict:
        return schema.TARGET_SCHEMA

    def validate(self, cfg: dict) -> list:
        return schema.validate(cfg)


@pytest.fixture
def registry():
    return {"libvirt": LibvirtSchemaOnly()}


def messages(problems) -> str:
    return "\n".join(str(p) for p in problems)


def errors(problems) -> list:
    return [p for p in problems if p.fatal]


# -- the canonical config ---------------------------------------------------


def test_the_canonical_config_has_no_errors(cfg):
    """Only the unreadable golden image, which is a warning by design."""
    problems = schema.validate(cfg)
    assert errors(problems) == [], messages(problems)


def test_it_composes_through_the_core_schema(cfg, registry):
    """F11's block has to survive `target` composition, not just validate alone."""
    assert errors(core_validate(cfg, registry)) == []
    assert set(core_schema(registry)["properties"]["target"]["properties"]) == {
        "libvirt"
    }


def test_unknown_key_in_target_is_rejected(cfg, registry):
    cfg["target"]["libvirt"]["no_verify"] = True
    assert errors(core_validate(cfg, registry))


def test_unknown_key_in_a_vm_is_rejected(cfg):
    """A typo'd key silently ignored is how a config means something other than
    what it looks like."""
    cfg["vms"][0]["memory_gb"] = 4
    assert "memory_gb" in messages(schema.validate(cfg))


# -- R-D: the URI is ours to assemble ---------------------------------------


@pytest.mark.parametrize(
    "uri, expect",
    [
        ("qemu+ssh://vcows@vcows/system?no_verify=1", "query string"),
        ("qemu+ssh://vcows@vcows/system?keyfile=/tmp/k", "query string"),
        ("qemu:///system", "qemu+ssh"),
        ("qemu+tcp://host/system", "qemu+ssh"),
        ("qemu+ssh:///system", "no host"),
        ("qemu+ssh://vcows@vcows/session", "/system"),
    ],
)
def test_bad_uris_are_rejected(cfg, uri, expect):
    cfg["target"]["libvirt"]["uri"] = uri
    assert expect in messages(schema.validate(cfg))


def test_a_good_uri_passes(cfg):
    cfg["target"]["libvirt"]["uri"] = "qemu+ssh://root@10.0.0.5:2222/system"
    assert not [p for p in errors(schema.validate(cfg)) if "uri" in p.where]


# -- R-G: firmware settings are not changeable after creation ---------------


def test_loader_without_nvram_template_is_rejected(cfg):
    del cfg["vms"][1]["nvram_template"]
    assert "nvram_template" in messages(schema.validate(cfg))


def test_nvram_template_without_loader_is_rejected(cfg):
    del cfg["vms"][1]["loader"]
    del cfg["vms"][1]["loader_format"]
    assert "'loader'" in messages(schema.validate(cfg))


def test_loader_format_without_loader_is_rejected(cfg):
    cfg["vms"][0]["loader_format"] = "raw"
    assert "loader_format" in messages(schema.validate(cfg))


def test_uefi_settings_with_bios_firmware_are_rejected(cfg):
    cfg["vms"][1]["firmware"] = "bios"
    problems = messages(schema.validate(cfg))
    assert "loader" in problems and "bios" in problems


def test_bios_alone_passes(cfg):
    for key in ("loader", "loader_format", "nvram_template"):
        del cfg["vms"][1][key]
    cfg["vms"][1]["firmware"] = "bios"
    assert errors(schema.validate(cfg)) == []


def test_neither_loader_nor_template_passes(cfg):
    """The default path: libvirt selects the firmware from the host's own
    descriptors, so a config that names no paths is portable across hosts."""
    for key in ("loader", "loader_format", "nvram_template"):
        del cfg["vms"][1][key]
    assert errors(schema.validate(cfg)) == []


# -- NICs -------------------------------------------------------------------


def test_both_bridge_and_network_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["bridge"] = "br0"
    assert "both (bridge, network)" in messages(schema.validate(cfg))


def test_neither_bridge_nor_network_is_rejected(cfg):
    del cfg["vms"][0]["nics"][0]["network"]
    assert "neither" in messages(schema.validate(cfg))


def test_a_bridge_nic_passes(cfg):
    nic = cfg["vms"][0]["nics"][0]
    del nic["network"]
    nic["bridge"] = "br0"
    assert errors(schema.validate(cfg)) == []


def test_ip_without_a_prefix_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.60"
    assert "prefix length" in messages(schema.validate(cfg))


def test_unparseable_address_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.999/24"
    assert errors(schema.validate(cfg))


def test_gateway_outside_the_subnet_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    assert "outside" in messages(schema.validate(cfg))


def test_bad_nameserver_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["nameservers"] = ["not-an-ip"]
    assert "nameservers[0]" in messages(schema.validate(cfg))


def test_duplicate_ip_across_vms_is_rejected(cfg):
    cfg["vms"][1]["nics"][0]["ip_cidr"] = cfg["vms"][0]["nics"][0]["ip_cidr"]
    assert "already used by" in messages(schema.validate(cfg))


def test_duplicate_mac_across_vms_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["mac"] = cfg["vms"][1]["nics"][0]["mac"]
    assert "already used by" in messages(schema.validate(cfg))


def test_two_primaries_are_rejected(cfg):
    nic = dict(cfg["vms"][0]["nics"][0])
    nic["ip_cidr"] = "192.168.122.70/24"
    nic["primary"] = True
    cfg["vms"][0]["nics"][0]["primary"] = True
    cfg["vms"][0]["nics"].append(nic)
    assert "claim primary" in messages(schema.validate(cfg))


def test_first_nic_is_primary_by_default(cfg):
    nic = dict(cfg["vms"][0]["nics"][0])
    nic["ip_cidr"] = "192.168.122.70/24"
    cfg["vms"][0]["nics"].append(nic)
    assert schema.primary_index(cfg["vms"][0]) == 0
    cfg["vms"][0]["nics"][1]["primary"] = True
    assert schema.primary_index(cfg["vms"][0]) == 1


# -- R-F: an overlay cannot be smaller than what it backs onto --------------


def qcow2_header(virtual_size: int) -> bytes:
    return (
        b"QFI\xfb" + struct.pack(">I", 3) + b"\0" * 16 + struct.pack(">Q", virtual_size)
    )


def test_disk_gb_below_the_image_virtual_size_is_rejected(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(50 * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    assert "virtual size" in messages(schema.validate(cfg))  # app01 asks for 40


def test_disk_gb_at_or_above_it_passes(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    assert errors(schema.validate(cfg)) == []


def test_an_unreadable_image_warns_rather_than_failing(cfg):
    """`validate` is the offline phase and the image is bind-mounted at run time,
    so its absence must not block a config check."""
    problems = schema.validate(cfg)
    assert errors(problems) == []
    assert any("cannot read" in p.message for p in problems)


def test_a_non_qcow2_image_is_an_error(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(b"not a qcow2 at all, not even close" + b"\0" * 32)
    cfg["image"]["source_qcow2"] = str(img)
    assert "bad magic" in messages(schema.validate(cfg))


# -- D25: the MAC derivation is permanent -----------------------------------


def test_derived_mac_is_pinned():
    """Changing this renames the interface every running VM's guest config is
    keyed to. Pinned for the same reason VCOWS_NS is."""
    assert schema.derive_mac("app01", 0) == "52:54:00:ee:77:63"
    assert schema.derive_mac("app01", 1) == "52:54:00:b1:b9:b9"
    assert schema.derive_mac("app02", 0) == "52:54:00:a5:b8:7c"


def test_derived_mac_matches_its_documented_formula():
    """Re-derive it independently, so the pin above cannot be 'whatever the code
    happens to produce'."""
    import uuid

    raw = uuid.uuid5(VCOWS_NS, "app01#nic0").bytes
    assert schema.derive_mac("app01", 0) == (
        f"52:54:00:{raw[0]:02x}:{raw[1]:02x}:{raw[2]:02x}"
    )


def test_an_explicit_mac_wins(cfg):
    assert schema.mac_of(cfg["vms"][1], 0) == "52:54:00:aa:bb:cc"
    assert schema.mac_of(cfg["vms"][0], 0) == schema.derive_mac("app01", 0)


def test_a_malformed_mac_is_rejected(cfg):
    cfg["vms"][1]["nics"][0]["mac"] = "52-54-00-aa-bb-cc"
    assert errors(schema.validate(cfg))


# -- everything at once -----------------------------------------------------


def test_every_problem_is_reported_not_just_the_first(cfg):
    cfg["target"]["libvirt"]["uri"] = "qemu:///system"
    cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    cfg["vms"][1]["loader_format"] = "bogus"
    assert len(errors(schema.validate(cfg))) >= 3


# -- the URI ---------------------------------------------------------------


def test_preflight_and_the_provider_are_given_different_transports():
    """Measured, not chosen. libvirt's own client does not recognise `sshcmd`
    (`transport in URL not recognised`), and the provider's `ssh` dials a
    monolithic socket a split-daemon host does not have, through a forward
    SELinux refuses. One config, two clients, two schemes."""
    target = {"uri": "qemu+ssh://vcows@vcows/system"}
    assert schema.connection_uri(target) == "qemu+ssh://vcows@vcows/system"
    assert schema.connection_uri(target, "sshcmd") == "qemu+sshcmd://vcows@vcows/system"


def test_credentials_never_reach_the_uri():
    """No spelling of the credential parameters works for both clients -- libvirt
    ignores `known_hosts`, the provider's ssh dialer spells it `knownhosts`, and
    `sshcmd` fails on either. They arrive through ~/.ssh/config instead, so a URI
    carrying them would be a silently ignored promise."""
    uri = schema.connection_uri(
        {
            "uri": "qemu+ssh://vcows@vcows/system",
            "ssh_keyfile": "/run/secrets/id_ed25519",
            "known_hosts": "/run/secrets/known_hosts",
        }
    )
    assert "?" not in uri
    assert "keyfile" not in uri and "known_hosts" not in uri
    # R-D still earns its keep: refusing an operator query string is what keeps
    # no_verify=1 off the connection.
    assert "no_verify" not in uri


# -- the credential paths ---------------------------------------------------


@pytest.mark.parametrize("key", ["ssh_keyfile", "known_hosts"])
@pytest.mark.parametrize(
    "value",
    [
        "/run/secrets/k\n  ProxyCommand /bin/sh -c 'curl evil'",
        "/run/secrets/k\n  StrictHostKeyChecking no",
        "/run/secrets/k\n",
        "/run/secrets/k\tHost *",
        "relative/path",
        "",
    ],
)
def test_a_credential_path_that_is_not_a_plain_path_is_rejected(
    cfg, registry, key, value
):
    """These two are interpolated verbatim into ~/.ssh/config by the container
    entrypoint. A newline appends directives: `ProxyCommand` reaches command
    execution, and `StrictHostKeyChecking no` undoes R-D from the other side."""
    cfg["target"]["libvirt"][key] = value
    assert errors(core_validate(cfg, registry)), f"{key}={value!r} was accepted"


@pytest.mark.parametrize("key", ["ssh_keyfile", "known_hosts"])
def test_an_ordinary_credential_path_passes(cfg, registry, key):
    """A validator that rejects everything passes half a suite."""
    cfg["target"]["libvirt"][key] = "/home/vcows/.ssh/id_ed25519-vcows.pub"
    assert errors(core_validate(cfg, registry)) == []
