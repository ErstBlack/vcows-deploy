"""The `target.vsphere` block and the per-VM shape.

Weighted towards what *differs* from the other two backends, because the shared
half -- addressing -- is tested once in `tests/test_seed_iso.py` and
`tests/test_libvirt_schema.py` against `cloudinit.check_addressing`, which every
backend calls. What is tested here is the placement rule, the NIC that names no
network, the keys carried over from the other backends that must be refused, and
the linked-clone disk size.
"""

from __future__ import annotations

import struct

import pytest

from orchestrator.backends.vsphere import VsphereBackend, schema
from orchestrator.config import validate as core_validate
from tests.conftest import CA_CERT, VSPHERE_CA_CERT, errors, messages, wheres

#: This backend is deliberately absent from `orchestrator.backends.REGISTRY`
#: until the register chunk, so the checks that go through the composed core
#: schema build their own.
REGISTRY = {"vsphere": VsphereBackend()}


def qcow2_header(virtual_size: int) -> bytes:
    """A header-only qcow2. Enough for `qcow2.virtual_size`, which reads 32 bytes."""
    return (
        b"QFI\xfb" + struct.pack(">I", 3) + b"\0" * 16 + struct.pack(">Q", virtual_size)
    )


def test_a_valid_config_has_no_errors(vsphere_cfg):
    assert errors(schema.validate(vsphere_cfg)) == []


# -- placement ---------------------------------------------------------------


def test_neither_a_cluster_nor_a_host_is_refused_at_the_block(vsphere_cfg):
    """Filed against `target.vsphere`, not a field: which of the two the
    operator meant is exactly what is missing."""
    vsphere_cfg["target"]["vsphere"].pop("cluster")
    problems = errors(schema.validate(vsphere_cfg))
    assert len(problems) == 1
    assert "exactly one of `cluster` or `host`" in problems[0].message
    assert problems[0].where == "target.vsphere"


def test_both_a_cluster_and_a_host_are_refused(vsphere_cfg):
    """Two answers to one placement question, and picking either silently is
    worse than saying so."""
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    problems = errors(schema.validate(vsphere_cfg))
    assert len(problems) == 1
    assert problems[0].where == "target.vsphere"


def test_a_host_is_the_other_accepted_placement(vsphere_cfg):
    vsphere_cfg["target"]["vsphere"].pop("cluster")
    vsphere_cfg["target"]["vsphere"]["host"] = "esx1.example.com"
    assert errors(schema.validate(vsphere_cfg)) == []


# -- the credential ----------------------------------------------------------


@pytest.mark.parametrize("field", ["user", "password"])
def test_both_halves_of_the_credential_are_required(vsphere_cfg, field):
    """vCenter has no token form, so there is no shape check and no union --
    `required` is the whole rule, enforced by the composed core schema."""
    vsphere_cfg["target"]["vsphere"].pop(field)
    assert field in messages(errors(core_validate(vsphere_cfg, REGISTRY)))


def test_a_password_is_never_echoed_by_a_refusal(vsphere_cfg):
    """Whatever else is wrong with the block, the value stays out of the report:
    it reaches the log, `run.json`, and whatever an operator pastes into a
    ticket."""
    vsphere_cfg["target"]["vsphere"]["password"] = "SUPERSECRETVALUE"  # noqa: S105
    vsphere_cfg["target"]["vsphere"].pop("cluster")
    assert "SUPERSECRETVALUE" not in messages(schema.validate(vsphere_cfg))


# -- the endpoint ------------------------------------------------------------


def test_http_is_refused_because_the_password_travels_on_it(vsphere_cfg):
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "http://vcenter.example.com"
    problems = errors(schema.validate(vsphere_cfg))
    assert "target.vsphere.endpoint" in wheres(problems)
    assert "must be 'https'" in messages(problems)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://administrator:hunter2@vcenter.example.com",
        # A username with no password is still a credential in the netloc, and
        # still travels into the run directory.
        "https://administrator@vcenter.example.com",
    ],
)
def test_credentials_in_the_endpoint_are_refused(vsphere_cfg, endpoint):
    vsphere_cfg["target"]["vsphere"]["endpoint"] = endpoint
    problems = errors(schema.validate(vsphere_cfg))
    assert "no credentials" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.endpoint"}


def test_a_query_string_is_refused(vsphere_cfg):
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://vcenter.example.com?x=1"
    problems = errors(schema.validate(vsphere_cfg))
    assert "no query string" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.endpoint"}


def test_the_sdk_path_is_refused_because_vcows_appends_it(vsphere_cfg):
    """`SmartConnect` is given the host and appends `/sdk` itself, so an
    endpoint carrying it would reach vCenter as `/sdk/sdk`."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://vcenter.example.com/sdk"
    problems = errors(schema.validate(vsphere_cfg))
    assert "path must be empty" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.endpoint"}


def test_an_endpoint_with_no_host_is_refused(vsphere_cfg):
    """`connect` would hand pyvmomi `None` as a host and fail against a server
    nobody wrote."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://"
    problems = errors(schema.validate(vsphere_cfg))
    assert "no host in" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.endpoint"}


def test_an_endpoint_that_is_not_a_url_is_reported_not_raised(vsphere_cfg):
    """`urlsplit` raises on this one. Every check below it reads the parts, so
    the refusal returns early -- an unhandled ValueError here would unwind past
    `config.load`'s every-problem contract."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://[vcenter.example.com:443"
    problems = errors(schema.validate(vsphere_cfg))
    assert "is not a URL" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.endpoint"}


def test_a_bare_origin_with_or_without_a_port_is_accepted(vsphere_cfg):
    for endpoint in (
        "https://vcenter.example.com",
        "https://vcenter.example.com/",
        "https://vcenter.example.com:8443",
    ):
        vsphere_cfg["target"]["vsphere"]["endpoint"] = endpoint
        assert errors(schema.validate(vsphere_cfg)) == [], endpoint


# -- TLS ---------------------------------------------------------------------


def test_insecure_warns_but_does_not_refuse(vsphere_cfg):
    """It is the operator's call, and a vCenter still carrying its self-signed
    certificate is common. It must not pass silently, because the password is
    sent to whatever answers at that endpoint."""
    vsphere_cfg["target"]["vsphere"]["insecure"] = True
    problems = schema.validate(vsphere_cfg)
    assert errors(problems) == []
    assert "verification is disabled" in messages(problems)
    assert "target.vsphere.insecure" in wheres(problems)


def test_a_ca_certificate_says_nothing(vsphere_cfg):
    """A private CA is the ordinary case in front of a vCenter, and pasting its
    certificate is not a weakening -- unlike `insecure`, which is why only one
    of them warns."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    problems = schema.validate(vsphere_cfg)
    assert errors(problems) == []
    assert "target.vsphere.ca_cert" not in wheres(problems)


def test_a_ca_certificate_beside_insecure_is_refused(vsphere_cfg):
    """Two contradictory answers about the certificate. Honouring either one
    silently is the failure mode: `insecure` wins in `api.connect`, so an
    operator who added a certificate would get no verification and no
    warning."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    vsphere_cfg["target"]["vsphere"]["insecure"] = True
    problems = errors(schema.validate(vsphere_cfg))
    assert "contradict each other" in messages(problems)
    assert set(wheres(problems)) == {"target.vsphere.ca_cert"}


def test_a_path_where_the_certificate_belongs_is_refused_by_name(vsphere_cfg):
    """`ca_cert: /run/secrets/vcenter-ca.pem` is refused, not accepted for
    compatibility: nothing is mounted for it."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = "/run/secrets/vcenter-ca.pem"
    problems = errors(schema.validate(vsphere_cfg))
    assert wheres(problems) == ["target.vsphere.ca_cert"], messages(problems)
    assert "not a path" in messages(problems)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("pki/ca.pem", id="relative-path"),
        pytest.param("-----BEGIN RSA PRIVATE KEY-----\n", id="a-private-key"),
    ],
)
def test_something_that_is_not_a_certificate_is_refused(vsphere_cfg, value):
    """Through `config.validate`, because the pattern on TARGET_SCHEMA is
    enforced by the composed core schema rather than by this backend's own
    checks. The last row is the one worth having: a key pasted where the public
    half belongs is a credential leaked into a config for nothing."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = value
    assert "ca_cert" in messages(errors(core_validate(vsphere_cfg, REGISTRY)))


def test_the_proxmox_fixture_certificate_is_accepted_by_the_pattern(vsphere_cfg):
    """The pattern asks how the value opens and nothing more. That the body is
    also parseable is `api.connect`'s problem, not this phase's -- `validate` is
    offline and does not build an SSL context."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = CA_CERT
    assert errors(schema.validate(vsphere_cfg)) == []


# -- the per-VM shape --------------------------------------------------------


@pytest.mark.parametrize("key", ["network", "bridge"])
def test_a_nic_names_no_network_of_its_own(vsphere_cfg, key):
    """The port group is named once, under `target.vsphere.network`, because the
    session resolves one. A config carried over from either other backend is told
    so rather than having the key ignored."""
    vsphere_cfg["vms"][0]["nics"][0][key] = "default"
    problems = errors(schema.validate(vsphere_cfg))
    assert key in messages(problems)
    assert "vms[0].nics[0]" in wheres(problems)


def test_a_vlan_tag_is_refused_because_the_port_group_carries_it(vsphere_cfg):
    vsphere_cfg["vms"][0]["nics"][0]["vlan_id"] = 42
    assert "vlan_id" in messages(errors(schema.validate(vsphere_cfg)))


@pytest.mark.parametrize(
    ("key", "value"),
    [("machine", "q35"), ("os_type", "l26"), ("loader", "/usr/share/edk2/x.fd")],
)
def test_keys_the_other_backends_have_are_refused(vsphere_cfg, key, value):
    """A clone takes its hardware version and its guest id from the template, and
    vCenter owns its own firmware images. An operator copying a config across
    must be told, not silently ignored."""
    vsphere_cfg["vms"][0][key] = value
    assert key in messages(errors(schema.validate(vsphere_cfg)))


def test_an_underscore_in_a_name_is_refused(vsphere_cfg):
    """The name becomes the guest's cloud-init hostname and the datastore folder
    holding its seed ISO."""
    vsphere_cfg["vms"][0]["name"] = "app_01"
    assert "vms[0].name" in wheres(errors(schema.validate(vsphere_cfg)))


def test_a_proxmox_nic_model_is_refused(vsphere_cfg):
    """`virtio` is a real model on the other two backends and no model at all
    here."""
    vsphere_cfg["vms"][0]["nics"][0]["model"] = "virtio"
    assert "vms[0].nics[0].model" in wheres(errors(schema.validate(vsphere_cfg)))


def test_vmxnet3_is_accepted(vsphere_cfg):
    vsphere_cfg["vms"][0]["nics"][0]["model"] = "vmxnet3"
    assert errors(schema.validate(vsphere_cfg)) == []


def test_duplicate_addresses_are_reported_across_vms(vsphere_cfg):
    """The shared half, reached through this backend -- proving
    `check_addressing` is actually wired in rather than only tested via
    libvirt."""
    nics = vsphere_cfg["vms"]
    nics[1]["nics"][0]["ip_cidr"] = nics[0]["nics"][0]["ip_cidr"]
    assert "already used by" in messages(errors(schema.validate(vsphere_cfg)))


def test_a_gateway_outside_the_subnet_is_reported(vsphere_cfg):
    vsphere_cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    assert "outside" in messages(errors(schema.validate(vsphere_cfg)))


def test_every_problem_is_reported_not_just_the_first(vsphere_cfg):
    """`config.load`'s contract: an operator at a site should not round-trip
    once per typo."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "http://vcenter.example.com"
    vsphere_cfg["vms"][0]["name"] = "app_01"
    vsphere_cfg["vms"][1]["nics"][0]["model"] = "virtio"
    problems = errors(schema.validate(vsphere_cfg))
    # Exactly these three: a fourth would mean one typo produced two refusals,
    # which is the round trip this contract exists to avoid.
    assert set(wheres(problems)) == {
        "target.vsphere.endpoint",
        "vms[0].name",
        "vms[1].nics[0].model",
    }


def test_a_bad_name_does_not_hide_this_vms_addressing_problem(vsphere_cfg):
    """The nic checks are skipped only when the *nics* are the unsafe part."""
    vsphere_cfg["vms"][0]["name"] = "app_01"
    vsphere_cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    problems = errors(schema.validate(vsphere_cfg))
    assert set(wheres(problems)) == {"vms[0].name", "vms[0].nics[0].gateway"}


def test_a_vm_that_is_not_a_mapping_is_reported_rather_than_crashing(vsphere_cfg):
    """`vms: [app01]` in YAML. The schema's verdict comes first, and the nic
    checks are not attempted on something with no fields to read."""
    vsphere_cfg["vms"][0] = "app01"
    assert "vms[0]" in wheres(errors(schema.validate(vsphere_cfg)))


def test_a_vm_with_no_nics_is_reported_rather_than_crashing(vsphere_cfg):
    """Missing entirely, so the schema files it against the VM rather than
    against a nic. `check_addressing` would reach for `vm["nics"]` and raise."""
    vsphere_cfg["vms"][0].pop("nics")
    problems = errors(schema.validate(vsphere_cfg))
    assert "vms[0]" in wheres(problems)
    assert "nics" in messages(problems)


def test_a_malformed_nic_does_not_lose_the_other_problems(vsphere_cfg):
    """The guard `nic_checks_are_safe` exists for this: a nic whose ip_cidr is
    blank in YAML arrives as None and would reach `ipaddress` and raise,
    unwinding past every other problem in the document."""
    vsphere_cfg["vms"][0]["nics"][0]["ip_cidr"] = None
    vsphere_cfg["vms"][1]["name"] = "app_02"
    assert "vms[1].name" in wheres(errors(schema.validate(vsphere_cfg)))


def test_a_hand_written_mac_that_collides_with_a_derived_one_is_refused(vsphere_cfg):
    """MACs are derived from the deployment and the VM, so a hand-set one can
    collide with a MAC no config file contains."""
    from orchestrator.cloudinit import mac_of

    vsphere_cfg["vms"][1]["nics"][0]["mac"] = mac_of(vsphere_cfg["vms"][0], 0, "lab-a")
    assert "already used by" in messages(errors(schema.validate(vsphere_cfg)))


# -- the linked-clone disk size ----------------------------------------------


def test_disk_gb_above_the_image_is_refused_for_a_linked_clone(vsphere_cfg, tmp_path):
    """A linked clone's disk *is* the template's, through a delta, and a delta
    disk cannot be extended. `linked` is the default, so this is what an operator
    who did not set `clone` gets."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    problems = errors(schema.validate(vsphere_cfg))
    # app01 asks for exactly 40, app02 for 60.
    assert wheres(problems) == ["vms[1].disk_gb"]
    assert "clone: full" in messages(problems)


def test_disk_gb_equal_to_the_image_passes(vsphere_cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    vsphere_cfg["vms"][1]["disk_gb"] = 40
    assert errors(schema.validate(vsphere_cfg)) == []


def test_a_full_clone_may_be_grown(vsphere_cfg, tmp_path):
    """The knob is one config key away, which is the point of it being a knob."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    vsphere_cfg["target"]["vsphere"]["clone"] = "full"
    assert errors(schema.validate(vsphere_cfg)) == []


def test_one_byte_over_the_image_is_still_over_it(vsphere_cfg, tmp_path):
    """The comparison is in bytes: 40 GiB of disk_gb against an image one byte
    smaller is still a delta disk asked to grow."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3 - 1))
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    vsphere_cfg["vms"][1]["disk_gb"] = 40
    assert wheres(errors(schema.validate(vsphere_cfg))) == [
        "vms[0].disk_gb",
        "vms[1].disk_gb",
    ]


def test_a_disk_below_the_image_is_reported_once_by_the_shared_check(
    vsphere_cfg, tmp_path
):
    """`imagecheck.check_disk_capacity` already refuses this for every backend.
    Reporting it a second time here would be two refusals for one typo."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(80 * 1024**3))
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    problems = errors(schema.validate(vsphere_cfg))
    assert wheres(problems) == ["vms[0].disk_gb", "vms[1].disk_gb"]
    assert "cannot be smaller" in messages(problems)


def test_an_unreadable_image_leaves_the_rule_silent(vsphere_cfg):
    """`check_disk_capacity` warns about the unreadable image, and this rule has
    nothing of its own to add about it. `validate` is the offline phase and the
    golden image is bind-mounted at run time."""
    problems = schema.validate(vsphere_cfg)
    assert errors(problems) == []
    assert [p.where for p in problems if "cannot read" in p.message] == [
        "image.source_qcow2"
    ]


def test_an_image_that_is_not_a_qcow2_is_reported_once(vsphere_cfg, tmp_path):
    """The same argument: `check_disk_capacity` errors on the header, and a
    second complaint from this rule would say nothing new."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(b"not a qcow2 header, but long enough to be read whole")
    vsphere_cfg["image"]["source_qcow2"] = str(img)
    problems = errors(schema.validate(vsphere_cfg))
    assert wheres(problems) == ["image.source_qcow2"]


# -- defaults ----------------------------------------------------------------


def test_a_key_this_backend_does_not_have_under_defaults_is_refused(vsphere_cfg):
    """Core resolves `defaults` before the backend sees a VM, so a key this
    backend does not have is caught by `VM_SCHEMA`'s `additionalProperties` --
    which files against the VM, not the key, so the message is what names it."""
    vsphere_cfg["defaults"] = {"machine": "q35"}
    problems = errors(core_validate(vsphere_cfg, REGISTRY))
    assert "machine" in messages(problems)
    assert wheres(problems) == ["vms[0]", "vms[1]"]


def test_verify_digest_false_skips_the_digest_check(vsphere_cfg, monkeypatch):
    """`destroy` never reads the golden image, so it loads the config without
    the hash. Patched at this module's own binding, because that is the name
    `validate` calls."""
    vsphere_cfg["image"]["sha256"] = "0" * 64

    def refuse(*args, **kwargs):
        raise AssertionError("hashed the image for a verb that does not read it")

    monkeypatch.setattr(schema, "check_image_digest", refuse)
    assert "image.sha256" not in wheres(
        schema.validate(vsphere_cfg, verify_digest=False)
    )
    with pytest.raises(AssertionError):
        schema.validate(vsphere_cfg)
