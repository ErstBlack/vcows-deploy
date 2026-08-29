"""Core config loading and schema composition from the registry."""

from __future__ import annotations

import textwrap

import pytest

from orchestrator.config import ConfigError, core_schema, load, validate, vm_names
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
    assert vm_names(cfg) == ["app01", "app02"]


def test_load_hands_back_the_warnings_it_computed(tmp_path, registry):
    """Every verb validates on the way in and every verb but `validate` threw the
    non-fatal half away -- so `validate` recovered it by running the whole of
    validation a second time, and the other three never mentioned it at all."""
    from orchestrator.backends.base import Severity

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
    with pytest.raises(ConfigError, match="duplicate VM name"):
        load(write(tmp_path, text), registry)


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
    with pytest.raises(ConfigError) as exc:
        load(tmp_path / "nope.yaml", registry)
    assert not isinstance(exc.value.__cause__, KeyError)


def test_non_mapping_config_is_a_clean_error(tmp_path, registry):
    with pytest.raises(ConfigError, match="must be a mapping"):
        load(write(tmp_path, "- just\n- a\n- list\n"), registry)
