"""The `target.proxmox` block and the per-VM shape.

Weighted towards what *differs* from the libvirt backend, because the shared half
-- addressing -- is tested once in `tests/test_seed_iso.py` and
`tests/test_libvirt_schema.py` against `cloudinit.check_addressing`, which both
backends call. What is tested here is the attachment rule, the firmware split,
the DNS-name rule, and the token, none of which the libvirt backend has.
"""

from __future__ import annotations

import pytest

from orchestrator.backends.proxmox import schema
from orchestrator.problems import Severity


def errors(problems):
    return [p for p in problems if p.severity is Severity.ERROR]


def messages(problems):
    return " | ".join(str(p) for p in problems)


def wheres(problems):
    return {p.where for p in problems}


@pytest.fixture(autouse=True)
def _token(pve_token):
    """Every test here wants a valid token unless it is testing the token."""


# -- the token ---------------------------------------------------------------


def test_a_missing_token_is_refused_and_says_where_to_put_it(pve_cfg, monkeypatch):
    monkeypatch.delenv("PROXMOX_VE_API_TOKEN")
    problems = errors(schema.validate(pve_cfg))
    assert len(problems) == 1
    assert "PROXMOX_VE_API_TOKEN is unset" in problems[0].message
    assert "user@realm!tokenid=<secret>" in problems[0].message
    # The variable, not a config path: there is nothing in the file to go and fix.
    assert problems[0].where == schema.TOKEN_ENV


def test_a_malformed_token_is_refused_without_echoing_it(pve_cfg, monkeypatch):
    """The refusal must not quote the value. A token pasted with a missing '!'
    is still most of a live credential, and a message naming it puts it in the
    log, in run.json, and in whatever the operator pastes into a ticket."""
    secret = "vcows@pve-deploy-SUPERSECRETVALUE"  # noqa: S105  not a real token
    monkeypatch.setenv("PROXMOX_VE_API_TOKEN", secret)
    problems = errors(schema.validate(pve_cfg))
    assert len(problems) == 1
    assert "SUPERSECRETVALUE" not in messages(problems)
    assert "not in the form" in problems[0].message
    assert problems[0].where == schema.TOKEN_ENV


def test_a_well_formed_token_splits_into_the_three_fields():
    m = schema.token_parts("vcows@pve!deploy=abc-123")
    assert m is not None
    assert (m.group("user"), m.group("name"), m.group("secret")) == (
        "vcows@pve",
        "deploy",
        "abc-123",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "vcows@pve!deploy",  # no secret
        "vcows!deploy=x",  # no realm
        "vcows@pve=x",  # no token id
        "",
        "   ",
    ],
)
def test_token_shapes_that_are_not_tokens(raw):
    assert schema.token_parts(raw) is None


def test_a_valid_config_has_no_errors(pve_cfg):
    assert errors(schema.validate(pve_cfg)) == []


# -- the endpoint ------------------------------------------------------------


def test_http_is_refused_because_the_token_is_a_bearer_credential(pve_cfg):
    pve_cfg["target"]["proxmox"]["endpoint"] = "http://pve.example.com:8006"
    problems = errors(schema.validate(pve_cfg))
    assert "target.proxmox.endpoint" in wheres(problems)
    assert "must be 'https'" in messages(problems)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://root:hunter2@pve.example.com",
        # A username with no password is still a credential in the netloc, and
        # still travels into the run directory.
        "https://root@pve.example.com",
    ],
)
def test_credentials_in_the_endpoint_are_refused(pve_cfg, endpoint):
    """Same refusal, and the same reason, as the libvirt backend's."""
    pve_cfg["target"]["proxmox"]["endpoint"] = endpoint
    problems = errors(schema.validate(pve_cfg))
    assert "no credentials" in messages(problems)
    assert wheres(problems) == {"target.proxmox.endpoint"}


def test_a_query_string_is_refused(pve_cfg):
    pve_cfg["target"]["proxmox"]["endpoint"] = "https://pve.example.com?verify=0"
    problems = errors(schema.validate(pve_cfg))
    assert "no query string" in messages(problems)
    assert wheres(problems) == {"target.proxmox.endpoint"}


def test_a_path_beyond_the_api_root_is_refused(pve_cfg):
    pve_cfg["target"]["proxmox"]["endpoint"] = "https://pve.example.com/nodes/pve1"
    problems = errors(schema.validate(pve_cfg))
    assert "path must be empty" in messages(problems)
    assert wheres(problems) == {"target.proxmox.endpoint"}


def test_an_endpoint_with_no_host_is_refused(pve_cfg):
    """`_endpoint_host` would hand proxmoxer the string 'None' and the connect
    would fail against a hostname nobody wrote."""
    pve_cfg["target"]["proxmox"]["endpoint"] = "https://"
    problems = errors(schema.validate(pve_cfg))
    assert "no host in" in messages(problems)
    assert wheres(problems) == {"target.proxmox.endpoint"}


def test_an_endpoint_that_is_not_a_url_is_reported_not_raised(pve_cfg):
    """`urlsplit` raises on this one. Every check below it reads the parts, so
    the refusal returns early -- an unhandled ValueError here would unwind past
    `config.load`'s every-problem contract."""
    pve_cfg["target"]["proxmox"]["endpoint"] = "https://[pve.example.com:8006"
    problems = errors(schema.validate(pve_cfg))
    assert "is not a URL" in messages(problems)
    assert wheres(problems) == {"target.proxmox.endpoint"}


def test_a_bare_origin_and_the_api_root_are_both_accepted(pve_cfg):
    for endpoint in (
        "https://pve.example.com",
        "https://pve.example.com/",
        "https://pve.example.com:8006",
        "https://pve.example.com/api2/json",
        # PVE's own UI links carry the trailing slash.
        "https://pve.example.com/api2/json/",
    ):
        pve_cfg["target"]["proxmox"]["endpoint"] = endpoint
        assert errors(schema.validate(pve_cfg)) == [], endpoint


def test_insecure_warns_but_does_not_refuse(pve_cfg):
    """It is the operator's call, and a self-signed PVE certificate is the
    default. It must not pass silently, because the token is sent to whatever
    answers at that endpoint."""
    pve_cfg["target"]["proxmox"]["insecure"] = True
    problems = schema.validate(pve_cfg)
    assert errors(problems) == []
    assert "verification is disabled" in messages(problems)
    # The field that turned it off, not the endpoint it applies to.
    assert "target.proxmox.insecure" in wheres(problems)


def test_there_is_no_ca_file_field(pve_cfg):
    """Measured against bpg/proxmox 0.111.1: the provider has no CA-bundle
    option, so a ca_file would be honoured by preflight and ignored by the
    apply. Rejected rather than half-implemented.

    Through `config.validate`, because `additionalProperties: False` on
    TARGET_SCHEMA is enforced by the composed core schema rather than by this
    backend's own checks.
    """
    from orchestrator.backends import REGISTRY
    from orchestrator.config import validate

    pve_cfg["target"]["proxmox"]["ca_file"] = "/etc/pki/ca.pem"
    assert "ca_file" in messages(errors(validate(pve_cfg, REGISTRY)))


# -- the per-VM shape --------------------------------------------------------


def test_a_nic_needs_a_bridge_and_takes_no_network(pve_cfg):
    """Proxmox has no equivalent of a libvirt network. This is the check the
    libvirt backend cannot share, and the reason each backend keeps its own."""
    pve_cfg["vms"][0]["nics"][0].pop("bridge")
    pve_cfg["vms"][0]["nics"][0]["network"] = "default"
    problems = errors(schema.validate(pve_cfg))
    assert "bridge" in messages(problems)
    assert "network" in messages(problems)


def test_libvirt_firmware_paths_are_rejected(pve_cfg):
    """Proxmox owns its OVMF and allocates the vars disk itself. An operator
    copying a libvirt config across must be told, not silently ignored."""
    pve_cfg["vms"][0]["loader"] = "/usr/share/edk2/ovmf/OVMF_CODE_4M.qcow2"
    pve_cfg["vms"][0]["nvram_template"] = "/usr/share/edk2/ovmf/OVMF_VARS_4M.qcow2"
    problems = errors(schema.validate(pve_cfg))
    assert "loader" in messages(problems)


def test_an_underscore_is_legal_on_libvirt_and_not_here(pve_cfg):
    """PVE validates a VM name as a DNS name."""
    pve_cfg["vms"][0]["name"] = "app_01"
    assert "vms[0].name" in wheres(errors(schema.validate(pve_cfg)))


def test_a_vlan_tag_outside_the_range_is_refused(pve_cfg):
    pve_cfg["vms"][1]["nics"][0]["vlan_id"] = 4095
    assert "vms[1].nics[0].vlan_id" in wheres(errors(schema.validate(pve_cfg)))


def test_an_unknown_nic_model_is_refused(pve_cfg):
    pve_cfg["vms"][0]["nics"][0]["model"] = "ne2k_pci"
    assert "vms[0].nics[0].model" in wheres(errors(schema.validate(pve_cfg)))


def test_duplicate_addresses_are_reported_across_vms(pve_cfg):
    """The shared half, reached through this backend -- proving `check_addressing`
    is actually wired in rather than only tested via libvirt."""
    pve_cfg["vms"][1]["nics"][0]["ip_cidr"] = pve_cfg["vms"][0]["nics"][0]["ip_cidr"]
    assert "already used by" in messages(errors(schema.validate(pve_cfg)))


def test_a_gateway_outside_the_subnet_is_reported(pve_cfg):
    pve_cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    assert "outside" in messages(errors(schema.validate(pve_cfg)))


def test_every_problem_is_reported_not_just_the_first(pve_cfg):
    """`config.load`'s contract: an operator at a site should not round-trip
    once per typo."""
    pve_cfg["target"]["proxmox"]["endpoint"] = "http://pve.example.com"
    pve_cfg["vms"][0]["name"] = "app_01"
    pve_cfg["vms"][1]["nics"][0]["vlan_id"] = 9999
    problems = errors(schema.validate(pve_cfg))
    # Exactly these three: a fourth would mean one typo produced two refusals,
    # which is the round trip this contract exists to avoid.
    assert wheres(problems) == {
        "target.proxmox.endpoint",
        "vms[0].name",
        "vms[1].nics[0].vlan_id",
    }


def test_a_bad_name_does_not_hide_this_vms_addressing_problem(pve_cfg):
    """The nic checks are skipped only when the *nics* are the unsafe part. A
    problem elsewhere in the same VM must not cost the operator a second run."""
    pve_cfg["vms"][0]["name"] = "app_01"
    pve_cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    problems = errors(schema.validate(pve_cfg))
    assert wheres(problems) == {"vms[0].name", "vms[0].nics[0].gateway"}


def test_a_vm_that_is_not_a_mapping_is_reported_rather_than_crashing(pve_cfg):
    """`vms: [app01]` in YAML. The schema's verdict comes first, and the nic
    checks are not attempted on something with no fields to read."""
    pve_cfg["vms"][0] = "app01"
    problems = errors(schema.validate(pve_cfg))
    assert "vms[0]" in wheres(problems)


def test_a_vm_with_no_nics_is_reported_rather_than_crashing(pve_cfg):
    """Missing entirely, so the schema files it against the VM rather than
    against a nic. `check_addressing` would reach for `vm["nics"]` and raise."""
    pve_cfg["vms"][0].pop("nics")
    problems = errors(schema.validate(pve_cfg))
    assert "vms[0]" in wheres(problems)
    assert "nics" in messages(problems)


def test_a_hand_written_mac_that_collides_with_a_derived_one_is_refused(pve_cfg):
    """MACs are derived from the deployment and the VM, so a hand-set one can
    collide with a MAC no config file contains. The deployment has to reach the
    derivation for this to be seen at all."""
    from orchestrator.cloudinit import mac_of

    pve_cfg["vms"][1]["nics"][0]["mac"] = mac_of(pve_cfg["vms"][0], 0, "lab-a")
    assert "already used by" in messages(errors(schema.validate(pve_cfg)))


def test_a_malformed_nic_does_not_lose_the_other_problems(pve_cfg):
    """The guard `nic_checks_are_safe` exists for: a nic whose ip_cidr is blank
    in YAML arrives as None and would reach `ipaddress` and raise, unwinding past
    every other problem in the document."""
    pve_cfg["vms"][0]["nics"][0]["ip_cidr"] = None
    pve_cfg["vms"][1]["name"] = "app_02"
    problems = errors(schema.validate(pve_cfg))
    assert "vms[1].name" in wheres(problems)


# -- the image ---------------------------------------------------------------


def test_an_image_name_pve_will_not_recognise_warns(pve_cfg):
    pve_cfg["image"]["base_volume_name"] = "golden"
    problems = schema.validate(pve_cfg)
    assert errors(problems) == []
    assert "recognises a disk image by extension" in messages(problems)
    assert "image.base_volume_name" in wheres(problems)


def test_a_recognised_image_name_says_nothing(pve_cfg):
    """Only about the name. The golden image does not exist on this machine, so
    the capacity and digest checks warn -- deliberately, and tested in
    tests/test_libvirt_schema.py where they used to live."""
    for name in ("golden.qcow2", "golden.raw", "golden.vmdk"):
        pve_cfg["image"]["base_volume_name"] = name
        said = messages(schema.validate(pve_cfg))
        assert "import" not in said, name
        assert errors(schema.validate(pve_cfg)) == [], name
