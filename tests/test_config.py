"""Core config loading and schema composition from the registry."""

from __future__ import annotations

import textwrap

import pytest

from orchestrator.config import ConfigError, core_schema, load, resolve, validate
from tests.fake_backend import FakeBackend

CONFIG = """\
schema_version: 1
deployment: lab-a
backend: fake
target:
  fake:
    endpoint: good://example
image:
  source_qcow2: /images/golden.qcow2
  base_volume_name: golden.qcow2
vms:
  - name: app01
  - name: app02
"""


@pytest.fixture
def registry():
    return {"fake": FakeBackend()}


def write(tmp_path, text, name="lab-a.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return p


def test_loads_a_valid_config(tmp_path, registry):
    cfg, _ = load(write(tmp_path, CONFIG), registry)
    assert cfg["backend"] == "fake"
    assert [vm["name"] for vm in cfg["vms"]] == ["app01", "app02"]


def test_load_hands_back_the_warnings_it_computed(tmp_path, registry):
    """Every verb validates on the way in and every verb but `validate` threw the
    non-fatal half away -- so `validate` recovered it by running the whole of
    validation a second time, and the other three never mentioned it at all."""
    from orchestrator.problems import Severity

    text = CONFIG.replace("good://example", "odd://example")
    cfg, problems = load(write(tmp_path, text), registry)
    assert cfg["backend"] == "fake"
    assert [p.severity for p in problems] == [Severity.WARNING]


def test_deployment_defaults_to_filename_stem(tmp_path, registry):
    """A config that never says `deployment` still stamps something meaningful
    into every marker."""
    text = CONFIG.replace("deployment: lab-a\n", "")
    cfg, _ = load(write(tmp_path, text, name="site-7.yaml"), registry)
    assert cfg["deployment"] == "site-7"


def test_a_bad_filename_stem_blames_the_file_not_the_key(tmp_path, registry):
    """The stem *became* the deployment name, so complaining about `deployment`
    names a key the operator never wrote."""
    text = CONFIG.replace("deployment: lab-a\n", "")
    config = write(tmp_path, text, name="9 bad name.yaml")
    with pytest.raises(ConfigError) as exc:
        load(config, registry)
    message = str(exc.value)
    assert "9 bad name" in message and "filename" in message
    assert "[deployment]" not in message
    # The rewrite has to *carry* a message. Both substrings above also occur in
    # `tmp_path`, which `where` renders, so this assertion passed for a Problem
    # whose message was None.
    assert isinstance(exc.value.problems[0].message, str)
    assert [p.where for p in exc.value.problems] == [str(config)]


def test_an_explicit_deployment_is_still_blamed_on_the_key(tmp_path, registry):
    """The rewrite is for the defaulted case only. A value the operator wrote is
    reported where they wrote it."""
    text = CONFIG.replace("deployment: lab-a", "deployment: 'bad name'")
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), registry)
    assert "[deployment]" in str(exc.value)
    assert [p.where for p in exc.value.problems] == ["deployment"]


@pytest.mark.parametrize(
    "value", ["images/golden.qcow2", "http://host/golden.qcow2", "./golden.qcow2"]
)
def test_source_qcow2_must_be_absolute(tmp_path, registry, value):
    """It is opened here and handed to the backend as a volume source. A relative
    path resolves against a working directory nothing here controls.

    **The `http://` case is the one with teeth.** The string reaches the
    provider's `create.content.url`, which really does resolve a URL, and
    measured on the rig it resolves it *client-side*: an http server bound to the
    client's own loopback -- unreachable from the hypervisor -- served the fetch.
    So without this anchor a config could send the container to the network for
    its base image, at a site whose whole premise is that there is no network.
    """
    text = CONFIG.replace("/images/golden.qcow2", value)
    with pytest.raises(ConfigError):
        load(write(tmp_path, text), registry)


@pytest.mark.parametrize(
    "before, after",
    [
        ("deployment: lab-a", 'deployment: "lab-a\\n"'),
        (
            "  base_volume_name: golden.qcow2",
            f'  base_volume_name: golden.qcow2\n  sha256: "{"a" * 64}\\n"',
        ),
    ],
)
def test_a_trailing_newline_is_rejected(tmp_path, registry, before, after):
    """Python's `$` also matches before a trailing newline, so every pattern in
    the tree anchors with `\\Z` instead."""
    with pytest.raises(ConfigError):
        load(write(tmp_path, CONFIG.replace(before, after)), registry)


def test_a_sha256_without_a_newline_passes(tmp_path, registry):
    text = CONFIG.replace(
        "  base_volume_name: golden.qcow2",
        f'  base_volume_name: golden.qcow2\n  sha256: "{"a" * 64}"',
    )
    cfg, _ = load(write(tmp_path, text), registry)
    assert cfg["image"]["sha256"] == "a" * 64


def test_explicit_deployment_wins(tmp_path, registry):
    cfg, _ = load(write(tmp_path, CONFIG, name="ignored.yaml"), registry)
    assert cfg["deployment"] == "lab-a"


def test_backend_validate_runs_after_structure_passes(tmp_path, registry):
    text = CONFIG.replace("good://example", "bad://example")
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), registry)
    assert "endpoint scheme is not supported" in str(exc.value)


def test_reports_every_problem_not_just_the_first(tmp_path, registry):
    """An operator editing a config at a site should not round-trip once per typo."""
    text = CONFIG.replace("schema_version: 1", "schema_version: 99").replace(
        "  - name: app01\n  - name: app02\n", "  - {}\n"
    )
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), registry)
    assert len(exc.value.problems) >= 2


def test_duplicate_vm_names_are_rejected(tmp_path, registry):
    text = CONFIG.replace("  - name: app02", "  - name: app01")
    with pytest.raises(ConfigError, match="duplicate VM name") as exc:
        load(write(tmp_path, text), registry)
    assert [p.where for p in exc.value.problems] == ["vms"]


def test_target_must_match_selected_backend(tmp_path, registry):
    """The whole of the composition: `backend: fake` requires `target.fake`."""
    registry["other"] = FakeBackend(name="other")
    text = CONFIG.replace("  fake:\n", "  other:\n")
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), registry)
    assert any("fake" in p.message for p in exc.value.problems)


def test_unknown_backend_is_rejected(tmp_path, registry):
    text = CONFIG.replace("backend: fake", "backend: nonesuch")
    with pytest.raises(ConfigError):
        load(write(tmp_path, text), registry)


def test_two_backends_compose(registry):
    """Adding a backend must not touch a core file -- so the schema has to be
    built from the registry, and both sub-schemas have to survive composition."""
    registry["other"] = FakeBackend(name="other")
    schema = core_schema(registry)

    assert schema["properties"]["backend"]["enum"] == ["fake", "other"]
    assert set(schema["properties"]["target"]["properties"]) == {"fake", "other"}
    # One if/then per registered backend, and nothing else in allOf.
    assert len(schema["allOf"]) == 2


def test_target_accepts_exactly_one_backend_block(registry):
    registry["other"] = FakeBackend(name="other")
    cfg = {
        "schema_version": 1,
        "deployment": "lab-a",
        "backend": "fake",
        "target": {"fake": {"endpoint": "good://x"}, "other": {"endpoint": "y"}},
        "image": {"source_qcow2": "/i.qcow2", "base_volume_name": "i.qcow2"},
        "vms": [{"name": "app01"}],
    }
    assert any("target" in p.where for p in validate(cfg, registry))


def test_unknown_top_level_key_is_rejected(tmp_path, registry):
    """A typo'd key silently ignored is how a config means something other than
    what it looks like."""
    text = CONFIG + "lifecycle: oneshot\n"
    with pytest.raises(ConfigError):
        load(write(tmp_path, text), registry)


def test_missing_file_is_a_clean_error(tmp_path, registry):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError) as exc:
        load(missing, registry)
    assert not isinstance(exc.value.__cause__, KeyError)
    assert [p.where for p in exc.value.problems] == [str(missing)]


@pytest.mark.parametrize(
    "text, match",
    [
        ("- just\n- a\n- list\n", "must be a mapping"),
        ("vms: [\n", "invalid YAML"),
    ],
)
def test_a_file_that_cannot_become_a_config_is_blamed_on_the_file(
    tmp_path, registry, text, match
):
    """`where` is the only part of a problem anything downstream reads -- the CLI
    prints it and `run.json` records it. A file that will not parse has no key to
    point at, so it has to point at itself; `None` there would be reported as a
    problem with nothing at all."""
    config = write(tmp_path, text)
    with pytest.raises(ConfigError, match=match) as exc:
        load(config, registry)
    assert [p.where for p in exc.value.problems] == [str(config)]


def test_a_problem_with_no_key_to_point_at_is_blamed_on_the_document(registry):
    """`<root>` is the `where` a document-level failure carries, and the CLI
    prints it and `run.json` records it exactly as it does any other. Nothing
    asserted it, so the sentinel could become the empty string -- a problem
    reported against nothing at all -- and every existing test still passed."""
    problems = validate({}, registry)
    # One per missing required key, and every one of them points at the document
    # rather than at a key -- there is no key to point at.
    assert len(problems) == 5
    assert {p.where for p in problems} == {"<root>"}


@pytest.mark.parametrize(
    "before, after",
    [
        # Each of these is one jsonschema keyword doing its job. A keyword whose
        # name is mistyped is not an error -- jsonschema ignores what it does not
        # recognise -- so the constraint simply stops applying, and every config
        # that was already valid stays valid.
        ("deployment: lab-a", "deployment:\n  not: a string"),
        ("target:\n  fake:\n    endpoint: good://example", "target:\n  - fake"),
        ("vms:\n  - name: app01\n  - name: app02", "vms:\n  app01: {}"),
        ("vms:\n  - name: app01\n  - name: app02", "vms: []"),
        ("vms:\n  - name: app01\n  - name: app02", "vms:\n  - app01"),
    ],
    ids=[
        "deployment-type",
        "target-type",
        "vms-type",
        "vms-empty",
        "vm-type",
    ],
)
def test_the_document_shape_is_checked_keyword_by_keyword(
    tmp_path, registry, before, after
):
    assert before in CONFIG
    with pytest.raises(ConfigError):
        load(write(tmp_path, CONFIG.replace(before, after)), registry)


def test_one_vm_is_enough(tmp_path, registry):
    """The other side of `minItems`. Every other config here carries two, so a
    floor of two would have been indistinguishable from a floor of one."""
    text = CONFIG.replace("  - name: app02\n", "")
    cfg, _ = load(write(tmp_path, text), registry)
    assert [vm["name"] for vm in cfg["vms"]] == ["app01"]


# -- defaults ---------------------------------------------------------------


def test_a_default_fills_what_a_vm_omits_and_never_replaces_what_it_sets(
    tmp_path, registry
):
    """`load` hands back a resolved config, so nothing downstream of it has to
    know the block exists."""
    text = CONFIG.replace(
        "vms:\n  - name: app01\n",
        "defaults:\n  vcpus: 2\nvms:\n  - name: app01\n    vcpus: 4\n",
    )
    cfg, _ = load(write(tmp_path, text), registry)
    assert [vm["vcpus"] for vm in cfg["vms"]] == [4, 2]


@pytest.mark.parametrize(
    "block, where",
    [
        # `name` is identity: every VM would share one, and the operator would
        # get a duplicate-name error against `vms`.
        ("defaults:\n  name: app\n", "defaults.name"),
        ("defaults:\n  nics: []\n", "defaults.nics"),
        # A mapping is the shape that would need a merge rule, and a per-VM
        # value replaces.
        ("defaults:\n  user_data:\n    packages: [tmux]\n", "defaults.user_data"),
        # The block itself has to be a mapping: `resolve` splats it into every VM.
        ("defaults:\n  - vcpus\n", "defaults"),
    ],
    ids=["name", "nics", "mapping", "not-a-mapping"],
)
def test_what_cannot_be_defaulted_is_refused_at_the_key(
    tmp_path, registry, block, where
):
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, block + CONFIG), registry)
    assert [p.where for p in exc.value.problems] == [where]


def test_resolve_changes_nothing_it_has_already_changed():
    """`load` and `validate` both resolve, and the two schema suites call
    `validate` directly, so the fold has to survive being applied twice."""
    bare = {"vms": [{"name": "app01"}]}
    assert resolve(bare) == bare

    once = resolve({"defaults": {"vcpus": 2}, "vms": [{"name": "app01"}]})
    assert once == {"defaults": {"vcpus": 2}, "vms": [{"name": "app01", "vcpus": 2}]}
    assert resolve(once) == once


# -- naming the VM ----------------------------------------------------------

#: Enough of a per-VM shape to file the two problems the operator actually sees
#: against a VM: a key that is not in it, and a value out of range.
VM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "disk_gb": {"type": "integer", "maximum": 64},
    },
}


class PerVmBackend(FakeBackend):
    """A backend that files problems against a VM, which `FakeBackend` never does.

    Both shipped backends do it through `cloudinit.check_vm_structure`, so this
    is the same call producing the same `vms[N]` wheres, without either
    backend's schema in a core test.
    """

    def validate(self, cfg):
        from orchestrator.cloudinit import check_vm_structure

        problems = []
        for i, vm in enumerate(cfg["vms"]):
            problems += check_vm_structure(vm, f"vms[{i}]", VM_SCHEMA)
        return problems


@pytest.fixture
def vm_registry():
    return {"fake": PerVmBackend()}


THREE = CONFIG.replace("  - name: app02\n", "  - name: app02\n  - name: app03\n")


@pytest.mark.parametrize(
    "text, where, expect",
    [
        (
            "    storage: fast\n",
            "vms[2]",
            "Additional properties are not allowed ('storage' was unexpected)",
        ),
        (
            "    disk_gb: 3000\n",
            "vms[2].disk_gb",
            "3000 is greater than the maximum of 64",
        ),
    ],
    ids=["unknown-key", "out-of-range"],
)
def test_a_problem_inside_a_vm_names_the_vm(tmp_path, vm_registry, text, where, expect):
    """`vms[2]` is the address of the entry; `app03` is what the operator called
    it and what every other tool on the box shows. The report needs both -- and
    `where` is the half nothing may move, so the name goes in the message."""
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, THREE + text), vm_registry)
    assert [(p.where, p.message) for p in exc.value.problems] == [
        (where, f"VM 'app03': {expect}")
    ]


def test_a_vm_with_no_name_is_left_unprefixed(tmp_path, vm_registry):
    """There is nothing to prefix with, and `name` is the missing key the
    problem is already about."""
    text = THREE.replace("  - name: app03\n", "  - vcpus: 2\n")
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), vm_registry)
    assert [p.where for p in exc.value.problems] == ["vms[2]"]
    assert not exc.value.problems[0].message.startswith("VM ")


def test_a_default_blamed_problem_is_left_unprefixed(tmp_path, vm_registry):
    """It is a complaint about the default, not about any one VM that inherited
    it -- so naming a VM would send the operator to the wrong key."""
    text = THREE.replace("vms:\n", "defaults:\n  disk_gb: 3000\nvms:\n")
    with pytest.raises(ConfigError) as exc:
        load(write(tmp_path, text), vm_registry)
    assert [p.where for p in exc.value.problems] == ["defaults.disk_gb"]
    assert not exc.value.problems[0].message.startswith("VM ")
