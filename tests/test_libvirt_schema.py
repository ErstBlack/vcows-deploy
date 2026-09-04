"""The `target.libvirt` block and the per-VM shape -- findings.md F11.

This is the one-way door, so every check gets a rejecting case *and* an accepting
one. A validator that rejects everything passes half a suite.
"""

from __future__ import annotations

import hashlib
import logging
import struct

import pytest

from orchestrator import cloudinit, imagecheck, limits
from orchestrator.backends.libvirt import schema
from orchestrator.config import core_schema
from orchestrator.config import validate as core_validate
from orchestrator.marker import VCOWS_NS
from orchestrator.problems import Problem
from tests.conftest import errors, messages, wheres
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


# -- the canonical config ---------------------------------------------------


def test_the_canonical_config_has_no_errors(cfg):
    """Only the unreadable golden image, which is a warning by design -- and,
    off a host with the secrets mounted, the two credential paths, for the same
    reason and with the same severity."""
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


# -- names, and the sizes ---------------------------------------------------


@pytest.mark.parametrize(
    "key, value, where",
    [
        ("name", "app01\n", "vms[0].name"),
        ("mac", "52:54:00:aa:bb:cc\n", "vms[0].nics[0].mac"),
    ],
)
def test_a_trailing_newline_is_rejected(cfg, key, value, where):
    """Python's `$` also matches before a trailing newline. A name carrying one
    becomes a libvirt domain name and the stem of two volume names."""
    target = cfg["vms"][0] if key == "name" else cfg["vms"][0]["nics"][0]
    target[key] = value
    assert where in [p.where for p in errors(schema.validate(cfg))]


@pytest.mark.parametrize("key", ["vcpus", "memory_mib", "disk_gb"])
def test_a_size_above_the_ceiling_is_rejected(cfg, key):
    """A fat-fingered zero, caught before the run creates volumes for a VM no
    host will start."""
    cfg["vms"][0][key] = getattr(schema, f"MAX_{key.upper()}", 512) + 1
    assert key in messages(errors(schema.validate(cfg)))


def test_the_ceilings_are_raisable_from_the_environment(cfg, monkeypatch):
    """A site on hardware we have not seen raises the bound from the outside.

    Both modules are reloaded, and the order is the point. `_ceiling` reads the
    environment in `limits`, and `schema` copies what it returns into `VM_SCHEMA`
    as a literal at import. Reloading `schema` alone re-runs
    `from ...limits import MAX_VCPUS` against an `orchestrator.limits` that is
    already imported, so the ceiling does not move and the assertion below holds
    for the default -- which is what it looked like before the constants became
    core, and is not a test of anything.
    """
    import importlib

    raised = limits.MAX_VCPUS + 8
    monkeypatch.setenv("VCOWS_MAX_VCPUS", str(raised))
    importlib.reload(limits)
    reloaded = importlib.reload(schema)
    try:
        assert reloaded.MAX_VCPUS == raised
        cfg["vms"][0]["vcpus"] = raised
        assert errors(reloaded.validate(cfg)) == []
    finally:
        monkeypatch.delenv("VCOWS_MAX_VCPUS")
        importlib.reload(limits)
        importlib.reload(schema)


def test_an_unusable_ceiling_is_reported_not_taken(monkeypatch, capsys):
    monkeypatch.setenv("VCOWS_MAX_VCPUS", "lots")
    monkeypatch.setenv("VCOWS_MAX_DISK_GB", "-1")
    import importlib

    try:
        reloaded = importlib.reload(limits)
        assert reloaded.MAX_VCPUS == 512 and reloaded.MAX_DISK_GB == 64 * 1024
        err = capsys.readouterr().err
        assert "VCOWS_MAX_VCPUS='lots'" in err and "VCOWS_MAX_DISK_GB='-1'" in err
    finally:
        monkeypatch.delenv("VCOWS_MAX_VCPUS")
        monkeypatch.delenv("VCOWS_MAX_DISK_GB")
        importlib.reload(limits)


def test_a_ceiling_of_one_is_taken(monkeypatch):
    """The floor is `< 1`. One is a positive integer, and a site that caps at a
    single vCPU gets that cap rather than the default."""
    monkeypatch.setenv("VCOWS_MAX_VCPUS", "1")
    assert limits._ceiling("VCOWS_MAX_VCPUS", 512) == 1


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
        # The query string is not the only way a credential reaches the URI, and
        # a password survives further: `connection_uri` clears the query but
        # leaves the netloc, so it reaches the log.
        ("qemu+ssh://vcows:hunter2@vcows/system", "no password"),
        ("qemu+ssh://vcows:@vcows/system", "no password"),
        # `urlsplit` raises on these rather than returning an unusable split, so
        # the ValueError unwound past every other problem in the document and
        # past `config.load`'s "every problem rather than the first".
        ("qemu+ssh://[2001:db8::1/system", "is not a URL"),
        ("qemu+ssh://2001:db8::1]/system", "is not a URL"),
        # Escaped rather than literal: ruff's RUF001 rejects a bare FULLWIDTH
        # NUMBER SIGN in source, and the point of the row is what `urlsplit`
        # does with it under NFKC, not how it reads here.
        ("qemu+ssh://h\uff03x/system", "is not a URL"),
        # A fragment is silently dropped by every client, so a URI carrying one
        # means something other than it looks like.
        ("qemu+ssh://vcows@vcows/system#frag", "fragment"),
    ],
)
def test_bad_uris_are_rejected(cfg, uri, expect):
    cfg["target"]["libvirt"]["uri"] = uri
    problems = errors(schema.validate(cfg))
    assert expect in messages(problems)
    # A set: one bad URI can break two clauses -- `qemu:///system` has neither
    # the scheme nor a host -- and both are filed against the key that carries it.
    assert set(wheres(problems)) == {"target.libvirt.uri"}


def test_a_uri_that_will_not_parse_loses_no_other_problem(cfg):
    """`config.load`: every problem rather than the first. `_check_target`
    is the first check `validate` runs, so a `ValueError` out of `urlsplit` took
    the whole document's report with it."""
    cfg["target"]["libvirt"]["uri"] = "qemu+ssh://[2001:db8::1/system"
    cfg["vms"][0]["vcpus"] = 0
    problems = schema.validate(cfg)
    assert any(p.where == "target.libvirt.uri" for p in problems), messages(problems)
    assert any(p.where == "vms[0].vcpus" for p in problems), messages(problems)


def test_a_good_uri_passes(cfg):
    cfg["target"]["libvirt"]["uri"] = "qemu+ssh://root@10.0.0.5:2222/system"
    assert not [p for p in errors(schema.validate(cfg)) if "uri" in p.where]


# -- credentials, which are contents rather than paths ----------------------


def test_the_credentials_themselves_pass_and_say_nothing(cfg):
    """The ordinary case, and the vacuity guard for the refusal below: a check
    that rejects a path has to accept what replaced it."""
    problems = schema.validate(cfg)
    assert errors(problems) == [], messages(problems)
    assert not [p for p in problems if p.where.startswith("target.libvirt.ssh")]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ssh_key", "/run/secrets/id_ed25519"),
        ("known_hosts", "/run/secrets/known_hosts"),
    ],
)
def test_a_path_where_the_credential_belongs_is_refused_by_name(cfg, field, value):
    """The one shape every config written against v0.1 has, and there is no
    compatibility for it. `known_hosts` carries no pattern of its own, so
    without this an absolute path reaches `ssh` as a known_hosts file holding a
    single nonsense line -- and fails at the host key check, naming neither."""
    cfg["target"]["libvirt"][field] = value
    problems = errors(schema.validate(cfg))
    assert wheres(problems) == [f"target.libvirt.{field}"], messages(problems)
    assert "not a path" in messages(problems)


# -- R-G: firmware settings are not changeable after creation ---------------


def test_loader_without_nvram_template_is_rejected(cfg):
    del cfg["vms"][1]["nvram_template"]
    problems = errors(schema.validate(cfg))
    assert "nvram_template" in messages(problems)
    assert wheres(problems) == ["vms[1].loader"], "the key that was set, not the gap"


def test_nvram_template_without_loader_is_rejected(cfg):
    del cfg["vms"][1]["loader"]
    del cfg["vms"][1]["loader_format"]
    problems = errors(schema.validate(cfg))
    assert "'loader'" in messages(problems)
    assert wheres(problems) == ["vms[1].nvram_template"]


def test_loader_format_without_loader_is_rejected(cfg):
    cfg["vms"][0]["loader_format"] = "raw"
    problems = errors(schema.validate(cfg))
    assert "loader_format" in messages(problems)
    assert wheres(problems) == ["vms[0].loader_format"]


def test_loader_without_loader_format_is_rejected(cfg):
    """It is not optional. The module builds the varstore path from it and takes
    an absent value as `raw`, so a qcow2 loader would get an `.fd` varstore --
    the mismatch the first acceptance run already paid for."""
    del cfg["vms"][1]["loader_format"]
    problems = errors(schema.validate(cfg))
    assert "without 'loader_format'" in messages(problems)
    assert wheres(problems) == ["vms[1].loader"]


def test_loader_with_its_format_passes(cfg):
    assert not [p for p in errors(schema.validate(cfg)) if "loader" in p.where]


def test_uefi_settings_with_bios_firmware_are_rejected(cfg):
    cfg["vms"][1]["firmware"] = "bios"
    problems = errors(schema.validate(cfg))
    assert "loader" in messages(problems) and "bios" in messages(problems)
    assert wheres(problems) == [
        "vms[1].loader",
        "vms[1].loader_format",
        "vms[1].nvram_template",
    ], "one per UEFI key present, so the operator can delete all three at once"


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


@pytest.mark.parametrize(
    "attach, expect",
    [({}, "neither"), ({"bridge": "br0", "network": "default"}, "both")],
    ids=["neither", "both"],
)
def test_a_nic_needs_exactly_one_attachment(cfg, attach, expect):
    """`render` reads whichever is present and the module builds one `<interface>`
    from it. Neither leaves a NIC attached to nothing; both leave the choice to
    whichever key the renderer happens to look at first."""
    nic = cfg["vms"][0]["nics"][0]
    nic.pop("network", None)
    nic.pop("bridge", None)
    nic.update(attach)
    problems = errors(schema.validate(cfg))
    assert expect in messages(problems)
    assert wheres(problems) == ["vms[0].nics[0]"], "the NIC, not either key"


def test_an_attachment_fault_and_an_addressing_fault_are_both_reported(cfg):
    """The two halves of `_check_nics` live in different modules now.

    The attachment rule is libvirt's and `cloudinit.check_addressing` is core, so
    the one function that used to emit both in a single pass emits its own and
    appends the other's. Drop the delegation, or put a `return` in front of it,
    and one whole class of problem disappears -- the operator fixes what they were
    told about, re-runs, and is told the rest. Order is not pinned here: the split
    does change it, and nothing depends on it.
    """
    nic = cfg["vms"][0]["nics"][0]
    nic.pop("network", None)
    nic["gateway"] = "10.0.0.1"
    out = messages(errors(schema.validate(cfg)))
    assert "neither" in out, "the attachment half"
    assert "gateway" in out, "the addressing half"


def test_ip_without_a_prefix_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.60"
    problems = errors(schema.validate(cfg))
    assert "prefix length" in messages(problems)
    assert wheres(problems) == ["vms[0].nics[0].ip_cidr"]


def test_unparseable_address_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "192.168.122.999/24"
    problems = errors(schema.validate(cfg))
    assert problems
    assert wheres(problems) == ["vms[0].nics[0].ip_cidr"]


def test_an_unparseable_nameserver_is_blamed_on_its_own_index(cfg):
    """`nameservers` is a list, so the index is the only thing that says which
    entry to fix -- and `_parse_address` is the one place that carries it."""
    cfg["vms"][0]["nics"][0]["nameservers"] = ["192.168.122.1", "not-an-ip"]
    problems = errors(schema.validate(cfg))
    assert wheres(problems) == ["vms[0].nics[0].nameservers[1]"]


@pytest.mark.parametrize(
    "ip_cidr, expect",
    [
        ("192.168.122.0/24", "the network address"),
        ("192.168.122.255/24", "the broadcast address"),
        ("192.168.122.64/26", "the network address"),
    ],
)
def test_an_address_that_is_not_a_host_address_is_rejected(cfg, ip_cidr, expect):
    cfg["vms"][0]["nics"][0]["ip_cidr"] = ip_cidr
    problems = errors(schema.validate(cfg))
    assert [p.where for p in problems if expect in p.message] == [
        "vms[0].nics[0].ip_cidr"
    ]
    # A /26 moves the subnet as well as the address, so the canonical gateway
    # falls outside it and is reported too. That is a second real problem, not
    # this one reported twice.
    assert set(wheres(problems)) <= {
        "vms[0].nics[0].ip_cidr",
        "vms[0].nics[0].gateway",
    }


@pytest.mark.parametrize("ip_cidr", ["192.168.122.60/31", "192.168.122.60/32"])
def test_a_point_to_point_block_has_no_reserved_addresses(cfg, ip_cidr):
    """Every address in a /31 or /32 is a host address, so the check is skipped
    rather than applied and got wrong."""
    cfg["vms"][0]["nics"][0]["ip_cidr"] = ip_cidr
    assert not [p for p in schema.validate(cfg) if "host address" in p.message]


def test_gateway_outside_the_subnet_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["gateway"] = "10.0.0.1"
    problems = errors(schema.validate(cfg))
    assert "outside" in messages(problems)
    assert wheres(problems) == ["vms[0].nics[0].gateway"]


def test_bad_nameserver_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["nameservers"] = ["not-an-ip"]
    assert "nameservers[0]" in messages(schema.validate(cfg))


def test_duplicate_ip_across_vms_is_rejected(cfg):
    cfg["vms"][1]["nics"][0]["ip_cidr"] = cfg["vms"][0]["nics"][0]["ip_cidr"]
    problems = errors(schema.validate(cfg))
    assert "already used by" in messages(problems)
    assert wheres(problems) == ["vms[1].nics[0].ip_cidr"], "the second one seen"


def test_a_duplicate_ip_is_reported_even_when_the_gateway_is_unparseable(cfg):
    """Registering the address needs only the address to have parsed.

    Nesting the registration under the gateway guard costs the operator a round
    trip -- they fix the gateway, re-run, and only then learn the address
    collides -- which is what `schema.validate`'s docstring rules out.
    """
    cfg["vms"][0]["nics"][0]["gateway"] = "not-an-ip"
    cfg["vms"][1]["nics"][0]["ip_cidr"] = cfg["vms"][0]["nics"][0]["ip_cidr"]
    out = messages(schema.validate(cfg))
    assert "gateway" in out
    assert "already used by" in out


@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda vm: vm.__setitem__("vcpus", 0), "is less than the minimum"),
        (lambda vm: vm.__setitem__("cpus", 2), "Additional properties"),
    ],
)
def test_a_structural_error_outside_nics_does_not_hide_a_duplicate_address(
    cfg, mutate, expect
):
    """The same round trip as the test above, one level up. A structural problem
    anywhere in a VM skipped that VM's nic checks entirely, so its addresses were
    never registered and a later VM reusing one went unreported -- for triggers
    (a `vcpus` out of range, an unexpected key) that say nothing about `nics`.
    """
    mutate(cfg["vms"][0])
    cfg["vms"][1]["nics"][0]["ip_cidr"] = cfg["vms"][0]["nics"][0]["ip_cidr"]
    out = messages(schema.validate(cfg))
    assert expect in out
    assert "already used by" in out


def test_a_vm_that_is_not_a_mapping_still_skips_the_nic_checks(cfg):
    """The `continue` has to survive shapes `_check_nics` and `_check_firmware`
    cannot read, and this is the one that is not reachable through `validate`:
    `config.load` returns the core schema's errors without ever asking the
    backend, and calling `schema.validate` with one anyway raises `TypeError` out
    of `_check_volume_names` -- on master too, for a reason this guard is nowhere
    near. So the predicate is pinned directly, one clause at a time."""
    safe = cloudinit.nic_checks_are_safe
    assert safe(cfg["vms"][0], []) is True
    assert safe("app01", []) is False
    assert safe({"name": "app01"}, []) is False
    assert safe({"nics": []}, []) is False
    assert safe({"name": "app01", "nics": ["eth0"]}, []) is False


def test_a_nic_that_is_not_a_mapping_still_skips_the_nic_checks(cfg):
    """Same guard, one level deeper -- `mac_of` indexes each nic -- and this one
    the core schema does pass through to the backend."""
    cfg["vms"][0]["nics"] = ["default"]
    cfg["vms"][1]["nics"][0]["ip_cidr"] = "192.168.122.60/24"
    problems = schema.validate(cfg)
    assert "vms[0].nics[0]]: 'default' is not of type 'object'" in messages(problems)
    # The consequence of the skip, and the reason it is the right trade here:
    # vms[0] registers no address, so vms[1] reusing one is not reported. That
    # is a round trip the operator pays -- and the alternative is a crash.
    assert "already used by" not in messages(problems)


def test_the_vm_that_skips_its_nic_checks_does_not_skip_the_vms_after_it(cfg):
    """The skip is one VM's, and `validate` returns every problem in the
    document. Ending the loop instead of passing over the VM would hide every
    fault in every VM below it behind one unreadable nic."""
    cfg["vms"][0]["nics"] = ["default"]
    cfg["vms"][1]["nics"][0]["gateway"] = "10.0.0.1"
    problems = errors(schema.validate(cfg))
    assert "vms[0].nics[0]]: 'default' is not of type 'object'" in messages(problems)
    assert "vms[1].nics[0].gateway" in wheres(problems), "the VM after the skip"


def test_the_guard_refuses_when_the_schema_failure_is_inside_a_nic(cfg):
    """The clause the container's shape cannot express (#112).

    `structural` is one VM's problems and `problems_from` puts the failing path
    in `where`, so a `.nics` in it is the schema saying the failure is inside the
    data `_check_nics` indexes. Anything else -- a `vcpus` out of range, an
    unexpected key -- names no nic and still runs the checks, which is the whole
    point of the guard and is pinned end to end by the parametrised test above.
    """
    vm = cfg["vms"][0]
    inside = Problem.error("not of type 'string'", where="vms[0].nics[0].ip_cidr")
    outside = Problem.error("less than the minimum", where="vms[0].vcpus")
    assert cloudinit.nic_checks_are_safe(vm, [outside]) is True
    assert cloudinit.nic_checks_are_safe(vm, [inside]) is False
    assert cloudinit.nic_checks_are_safe(vm, [outside, inside]) is False


@pytest.mark.parametrize(
    "field, value, expect",
    [
        ("ip_cidr", None, "None is not of type 'string'"),
        ("ip_cidr", 5, "5 is not of type 'string'"),
        ("ip_cidr", True, "True is not of type 'string'"),
        ("nameservers", None, "None is not of type 'array'"),
        ("nameservers", 5, "5 is not of type 'array'"),
        ("nameservers", True, "True is not of type 'array'"),
        ("mac", 5, "5 is not of type 'string'"),
        ("mac", True, "True is not of type 'string'"),
    ],
)
def test_a_wrongly_typed_nic_field_reports_the_schema_error_rather_than_crashing(
    cfg, registry, field, value, expect
):
    """#112. A nic that is a mapping whose *field* is wrong passes every clause
    of the container-shape guard, and `_check_nics` assumes the schema ran:
    `ip_cidr` reaches `"/" not in raw` (`TypeError`), `nameservers` reaches
    `enumerate` (`TypeError`), `mac` reaches `.lower()` (`AttributeError`). Each
    unwound past every other check and past `config.load`'s "every problem rather
    than the first", so the operator got a stack trace instead of a field name.

    A blank YAML value is the commonest way in -- `ip_cidr:` with nothing after
    it parses as `None` -- and every trigger here is already caught by the schema
    `check_vm_structure` just ran, so the named error is what has to reach the
    operator. Asserted against the whole fatal list, not a substring, because the
    defect was the *loss* of everything else: the composed path is included since
    `config.load` runs it for all four verbs, not only `validate`.
    """
    cfg["vms"][0]["nics"][0][field] = value
    expected = [(f"vms[0].nics[0].{field}", expect)]
    for problems in (schema.validate(cfg), core_validate(cfg, registry)):
        assert [(p.where, p.message) for p in errors(problems)] == expected


def test_the_deployment_reaches_the_derivation_through_the_schema(cfg):
    """The config's deployment name has to survive the trip to `derive_mac`.

    `_check_nics` forwards it to `cloudinit.check_addressing`, which forwards it
    to `mac_of`, and that is the only route it takes on the validate path. Pinning
    `derive_mac` directly does not cover either hop: drop the argument or replace
    it with a constant and every other test here still passes, because a
    consistent-but-wrong MAC collides with nothing and appears in no assertion.

    Setting app02's MAC to the value app01 *derives under this deployment* makes
    the duplicate report depend on the name actually arriving.
    """
    cfg["vms"][1]["nics"][0]["mac"] = cloudinit.derive_mac(
        cfg["vms"][0]["name"], 0, cfg["deployment"]
    )
    problems = errors(schema.validate(cfg))
    assert "already used by" in messages(problems)
    assert wheres(problems) == ["vms[1].nics[0]"]


def test_duplicate_mac_across_vms_is_rejected(cfg):
    cfg["vms"][0]["nics"][0]["mac"] = cfg["vms"][1]["nics"][0]["mac"]
    problems = errors(schema.validate(cfg))
    assert "already used by" in messages(problems)
    assert wheres(problems) == ["vms[1].nics[0]"]


def test_two_primaries_are_rejected(cfg):
    nic = dict(cfg["vms"][0]["nics"][0])
    nic["ip_cidr"] = "192.168.122.70/24"
    nic["primary"] = True
    cfg["vms"][0]["nics"][0]["primary"] = True
    cfg["vms"][0]["nics"].append(nic)
    problems = errors(schema.validate(cfg))
    assert "claim primary" in messages(problems)
    assert wheres(problems) == ["vms[0].nics"], "the list, not either nic in it"


def test_one_primary_is_not_a_conflict(cfg):
    """The rule is against two claims, not against the claim: `> 1`, not `>= 1`."""
    nic = dict(cfg["vms"][0]["nics"][0])
    nic["ip_cidr"] = "192.168.122.70/24"
    cfg["vms"][0]["nics"].append(nic)
    cfg["vms"][0]["nics"][1]["primary"] = True
    assert errors(schema.validate(cfg)) == []


def test_a_nic_without_nameservers_validates(cfg):
    """`nameservers` is optional and `check_addressing` iterates it, so absent
    has to read as an empty list. A `None` default is a TypeError out of
    `validate` for every config that leaves the key out."""
    del cfg["vms"][0]["nics"][0]["nameservers"]
    assert errors(schema.validate(cfg)) == []


def test_first_nic_is_primary_by_default(cfg):
    nic = dict(cfg["vms"][0]["nics"][0])
    nic["ip_cidr"] = "192.168.122.70/24"
    cfg["vms"][0]["nics"].append(nic)
    assert cloudinit.primary_index(cfg["vms"][0]) == 0
    cfg["vms"][0]["nics"][1]["primary"] = True
    assert cloudinit.primary_index(cfg["vms"][0]) == 1


# -- R-F: an overlay cannot be smaller than what it backs onto --------------


def qcow2_header(virtual_size: int) -> bytes:
    return (
        b"QFI\xfb" + struct.pack(">I", 3) + b"\0" * 16 + struct.pack(">Q", virtual_size)
    )


def test_disk_gb_below_the_image_virtual_size_is_rejected(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(50 * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    problems = errors(schema.validate(cfg))
    assert "virtual size" in messages(problems)  # app01 asks for 40
    assert wheres(problems) == ["vms[0].disk_gb"], (
        "the VM that asked for too little, not the image it backs onto"
    )


def test_disk_gb_at_or_above_it_passes(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    assert errors(schema.validate(cfg)) == []


def test_one_byte_over_disk_gb_is_rejected(cfg, tmp_path):
    """The comparison is in GiB exactly: an image one byte larger than the
    overlay asked for is one the overlay cannot back onto."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3 + 1))
    cfg["image"]["source_qcow2"] = str(img)
    assert wheres(errors(schema.validate(cfg))) == ["vms[0].disk_gb"]


def test_an_unreadable_image_warns_rather_than_failing(cfg):
    """`validate` is the offline phase and the image is bind-mounted at run time,
    so its absence must not block a config check."""
    problems = schema.validate(cfg)
    assert errors(problems) == []
    assert [p.where for p in problems if "cannot read" in p.message] == [
        "image.source_qcow2"
    ]


def test_a_readable_image_is_logged_at_debug(cfg, tmp_path, caplog):
    """#163: the success path returns no Problem, so the DEBUG line is the only
    evidence that `validate` opened the image and what it measured."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(40 * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    with caplog.at_level(logging.DEBUG, logger="orchestrator.imagecheck"):
        schema.validate(cfg)
    # Equality, not `in`: a substring match let mutmut's padded-string mutant
    # of the message survive, measured (476 -> 477 on the first CI run).
    mine = [r for r in caplog.records if r.name == "orchestrator.imagecheck"]
    assert [r.getMessage() for r in mine] == [f"{img}: virtual size 40.0 GiB"]


def test_a_non_qcow2_image_is_an_error(cfg, tmp_path):
    img = tmp_path / "golden.qcow2"
    img.write_bytes(b"not a qcow2 at all, not even close" + b"\0" * 32)
    cfg["image"]["source_qcow2"] = str(img)
    problems = errors(schema.validate(cfg))
    assert "bad magic" in messages(problems)
    assert wheres(problems) == ["image.source_qcow2"]


# -- #12: the declared digest, actually computed ----------------------------


def golden(tmp_path, cfg, virtual_gb: int = 40):
    """A real qcow2 on disk, wired into the config. Returns it and its digest."""
    img = tmp_path / "golden.qcow2"
    img.write_bytes(qcow2_header(virtual_gb * 1024**3))
    cfg["image"]["source_qcow2"] = str(img)
    return img, hashlib.sha256(img.read_bytes()).hexdigest()


def test_a_matching_sha256_passes(cfg, tmp_path):
    _, digest = golden(tmp_path, cfg)
    cfg["image"]["sha256"] = digest
    assert errors(schema.validate(cfg)) == []


def test_a_mismatched_sha256_is_an_error(cfg, tmp_path):
    """The field was schema-validated and never computed, so a substituted or
    corrupted golden image deployed with no signal at all (#12)."""
    _, digest = golden(tmp_path, cfg)
    cfg["image"]["sha256"] = "0" * 64
    problems = errors(schema.validate(cfg))
    assert wheres(problems) == ["image.sha256"]
    assert "not the image the config describes" in messages(problems)
    # The message names both digests, so an operator can tell a corruption from
    # a stale config without computing anything themselves.
    assert digest in messages(problems)


def test_an_uppercase_sha256_matches(cfg, tmp_path):
    """`config.py`'s `sha256` pattern admits [0-9a-fA-F]{64}, so the comparison
    must not be the thing that rejects a config the schema accepted."""
    _, digest = golden(tmp_path, cfg)
    cfg["image"]["sha256"] = digest.upper()
    assert errors(schema.validate(cfg)) == []


def test_no_sha256_reads_nothing(cfg, tmp_path, monkeypatch):
    """The field is optional and hashing the image is the expensive part, so a
    config that declares no digest must not pay for one."""
    golden(tmp_path, cfg)
    cfg["image"].pop("sha256", None)

    def refuse(*args, **kwargs):
        raise AssertionError("hashed the image for a config that declares no sha256")

    monkeypatch.setattr(imagecheck.hashlib, "file_digest", refuse)
    assert errors(schema.validate(cfg)) == []


def test_an_unreadable_image_warns_rather_than_failing_the_digest(cfg):
    """Same reason as the capacity check: `validate` is offline and the image is
    bind-mounted at run time. An absent image must not turn a digest the
    operator declared into a fatal problem."""
    cfg["image"]["sha256"] = "0" * 64
    problems = schema.validate(cfg)
    assert errors(problems) == []
    assert [p.where for p in problems if "was not verified" in p.message] == [
        "image.sha256"
    ], "the digest that could not be checked, not the image that could not be read"


def test_a_base_volume_named_like_a_per_vm_volume_is_refused(cfg):
    """One flat pool and undecorated names (D16), so a golden image called
    `app01.qcow2` collides with app01's own overlay. libvirt would refuse it
    mid-apply; this refuses it offline, naming the clash."""
    for name in ("app01.qcow2", "app02-seed.iso"):
        cfg["image"]["base_volume_name"] = name
        problems = errors(schema.validate(cfg))
        assert name in messages(problems)
        assert wheres(problems) == ["image.base_volume_name"]


# -- D25: the MAC derivation is permanent -----------------------------------


def test_derived_mac_is_pinned():
    """Changing this renames the interface every running VM's guest config is
    keyed to. Pinned for the same reason VCOWS_NS is."""
    assert cloudinit.derive_mac("app01", 0, "lab-a") == "52:54:00:be:a8:60"
    assert cloudinit.derive_mac("app01", 1, "lab-a") == "52:54:00:d3:8b:f5"
    assert cloudinit.derive_mac("app02", 0, "lab-a") == "52:54:00:22:01:10"


def test_derived_mac_carries_the_deployment():
    """Two deployments each containing `app01` on one L2: without this both
    guests boot, both apply their static address, and both report success on
    one MAC. `address_conflicts` only ever looks at one host, so nothing else
    catches it."""
    assert cloudinit.derive_mac("app01", 0, "lab-a") != cloudinit.derive_mac(
        "app01", 0, "lab-b"
    )


def test_derived_mac_matches_its_documented_formula():
    """Re-derive it independently, so the pin above cannot be 'whatever the code
    happens to produce'."""
    import uuid

    raw = uuid.uuid5(VCOWS_NS, "lab-a/app01#nic0").bytes
    assert cloudinit.derive_mac("app01", 0, "lab-a") == (
        f"52:54:00:{raw[0]:02x}:{raw[1]:02x}:{raw[2]:02x}"
    )


def test_an_explicit_mac_wins(cfg):
    """The override is the only escape from a derived MAC, so it has to hold
    regardless of deployment -- a site whose switch policy or DHCP
    reservations already own an address has nothing else to reach for."""
    deployment = cfg["deployment"]
    assert cloudinit.mac_of(cfg["vms"][1], 0, deployment) == "52:54:00:aa:bb:cc"
    assert cloudinit.mac_of(cfg["vms"][1], 0, "lab-b") == "52:54:00:aa:bb:cc"
    assert cloudinit.mac_of(cfg["vms"][0], 0, deployment) == cloudinit.derive_mac(
        "app01", 0, deployment
    )


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


def test_one_scheme_serves_every_client_this_tool_has():
    """It used to build two: the go-libvirt provider needed `qemu+sshcmd`, which
    libvirt's own client does not recognise at all (`transport in URL not
    recognised`). With the provider gone, preflight, create and destroy are all
    that dial, and all three are that client."""
    target = {"uri": "qemu+ssh://vcows@vcows/system"}
    assert schema.connection_uri(target) == "qemu+ssh://vcows@vcows/system"
    assert "sshcmd" not in schema.connection_uri(target)
    # The scheme is fixed here, not merely checked upstream: a config URI that
    # `_check_target` refused still dials as qemu+ssh if something else lets it through.
    assert (
        schema.connection_uri({"uri": "qemu+tcp://vcows@vcows/system"})
        == "qemu+ssh://vcows@vcows/system"
    )


def test_the_operators_query_is_replaced_never_merged():
    """R-D: the query is vcows's to assemble. Fed `no_verify=1`, the result
    carries only what `connect` handed over -- with files, their two
    parameters; without, nothing at all."""
    target = {"uri": "qemu+ssh://vcows@vcows/system?no_verify=1"}
    assert schema.connection_uri(target) == "qemu+ssh://vcows@vcows/system"
    uri = schema.connection_uri(target, {"keyfile": "/t/key", "command": "/t/ssh"})
    assert uri == "qemu+ssh://vcows@vcows/system?keyfile=/t/key&command=/t/ssh"
    assert "no_verify" not in uri
    assert schema.connection_uri(target, {"keyfile": "/t/a b/key"}) == (
        "qemu+ssh://vcows@vcows/system?keyfile=/t/a%20b/key"
    )


# -- the credentials, through the composed core schema ----------------------


#: What `ssh_key` is not. The last is the whole reason there is a pattern rather
#: than a `minLength`: a public key is what an operator reaches for by habit, it
#: is not a secret, and it authenticates nothing.
NOT_A_PRIVATE_KEY = [
    "/run/secrets/id_ed25519",
    "id_ed25519",
    "",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleNotAKey vcows@host",
]


@pytest.mark.parametrize("value", NOT_A_PRIVATE_KEY)
def test_something_that_is_not_a_private_key_is_rejected(cfg, registry, value):
    """`ssh_key` carries the key itself, so the check is that it opens like one.
    The entrypoint writes it to a file and hands the file to `ssh`, which would
    otherwise fail with `invalid format` and name no config field."""
    cfg["target"]["libvirt"]["ssh_key"] = value
    assert errors(core_validate(cfg, registry)), f"{value!r} was accepted"


def test_an_ordinary_key_and_known_hosts_pass(cfg, registry):
    """A validator that rejects everything passes half a suite."""
    assert errors(core_validate(cfg, registry)) == []


def test_an_empty_known_hosts_is_rejected(cfg, registry):
    """It has no pattern -- a host key line is `host algo base64` with any
    algorithm name -- so `minLength` is the whole of what can be said here."""
    cfg["target"]["libvirt"]["known_hosts"] = ""
    assert errors(core_validate(cfg, registry))


# -- defaults ---------------------------------------------------------------


def test_a_default_supplies_a_key_the_vm_omits(cfg, registry):
    """Through `core_validate`, because resolution is core's and the VM schema
    that judges the result is this backend's."""
    del cfg["vms"][0]["vcpus"]
    cfg["defaults"] = {"vcpus": 2}
    problems = errors(core_validate(cfg, registry))
    assert problems == [], messages(problems)


def test_a_bad_default_is_reported_once_at_the_default(cfg, registry):
    """Both VMs inherit it. Blaming each of them names a key the operator never
    wrote, twice, for one mistake."""
    for vm in cfg["vms"]:
        del vm["vcpus"]
    cfg["defaults"] = {"vcpus": 0}
    problems = errors(core_validate(cfg, registry))
    assert wheres(problems) == ["defaults.vcpus"], messages(problems)
    # Re-pointed, not rewritten: the schema's own words travel with it.
    assert "minimum" in messages(problems)


def test_a_vm_that_wrote_its_own_bad_value_is_blamed_for_it(cfg, registry):
    """The other half of the re-pointing: a good default beside a VM that set
    the key itself leaves the blame where the operator can act on it."""
    del cfg["vms"][1]["vcpus"]
    cfg["defaults"] = {"vcpus": 2}
    cfg["vms"][0]["vcpus"] = 0
    problems = errors(core_validate(cfg, registry))
    assert wheres(problems) == ["vms[0].vcpus"], messages(problems)


def test_an_explicit_null_replaces_the_default_and_then_fails(cfg, registry):
    """There is no null-means-unset rule. A per-VM value replaces, and `None` is
    a value -- so it lands on the type check, at the VM that wrote it."""
    cfg["defaults"] = {"vcpus": 2}
    cfg["vms"][0]["vcpus"] = None
    problems = errors(core_validate(cfg, registry))
    assert wheres(problems) == ["vms[0].vcpus"], messages(problems)
