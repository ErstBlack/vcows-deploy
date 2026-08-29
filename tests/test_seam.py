"""The seam test: core must run a whole deploy/destroy cycle with no libvirt.

This is the one test that checks the architecture rather than the code. If core
cannot complete a cycle against a backend with no hypervisor semantics, on a
machine where ``import libvirt`` fails, then the backend abstraction is decorative
regardless of how clean the signatures look.

libvirt *is* installed on the dev box, so the fixture actively breaks the import
rather than relying on its absence.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import textwrap

import pytest

from orchestrator.backends.base import Action, Existing, decide
from orchestrator.config import load, vm_names
from orchestrator.marker import Marker
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
def no_libvirt(monkeypatch):
    """Make `import libvirt` fail, however it is attempted."""
    for name in [m for m in sys.modules if m == "libvirt" or m.startswith("libvirt.")]:
        monkeypatch.delitem(sys.modules, name)

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "libvirt" or name.startswith("libvirt."):
            raise ImportError(f"{name} is blocked by the seam test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)

    with pytest.raises(ImportError):
        import libvirt  # noqa: F401
    yield


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "lab-a.yaml"
    p.write_text(textwrap.dedent(CONFIG))
    return p


def test_libvirt_is_actually_blocked(no_libvirt):
    """Guard the guard: if the fixture stopped working, every test below would
    pass for the wrong reason."""
    with pytest.raises(ImportError):
        __import__("libvirt")


def test_full_pipeline_without_libvirt(no_libvirt, cfg, tmp_path):
    """validate -> preflight -> prepare -> render -> outputs -> destroy."""
    backend = FakeBackend()
    registry = {"fake": backend}

    config = load(cfg, registry)

    # -- deploy ------------------------------------------------------------
    with backend.connect(config) as session:
        existing = backend.preflight(config, session)
        decisions, problems = decide(vm_names(config), existing, config["deployment"])
        assert [d.action for d in decisions] == [Action.CREATE, Action.CREATE]
        assert problems == []

        workdir = tmp_path / "work"
        workdir.mkdir()
        with backend.prepare(config, workdir) as prepared:
            tfvars = backend.render(config, prepared)

        assert set(tfvars["vms"]) == {"app01", "app02"}
        for name, vm in tfvars["vms"].items():
            assert (
                Marker.from_json(
                    vm["marker_xml"].split(">", 1)[1].rsplit("<", 1)[0]
                ).name
                == name
            )

        # Stand in for `tofu apply` + `tofu output -json`.
        session.world.extend(
            Existing(
                name=n,
                id=Marker.for_vm(n, "lab-a").id,
                marker=Marker.for_vm(n, "lab-a"),
            )
            for n in tfvars["vms"]
        )
        inventory = backend.parse_outputs(
            {"vms": {"value": {n: {"name": n} for n in tfvars["vms"]}}}
        )
        assert set(inventory.vms) == {"app01", "app02"}

    assert session.closed, "connect() must close its session on the way out"

    # -- second deploy is a no-op ------------------------------------------
    with backend.connect(config) as session:
        session.world = list(backend.sessions[0].world)
        decisions, _ = decide(
            vm_names(config), backend.preflight(config, session), config["deployment"]
        )
        assert [d.action for d in decisions] == [Action.SKIP, Action.SKIP]

    # -- destroy, with no config-derived state ------------------------------
    with backend.connect(config) as session:
        session.world = list(backend.sessions[0].world)
        targets = [
            e for e in backend.preflight(config, session) if e.marker is not None
        ]
        backend.destroy(config, session, targets)
        assert sorted(session.destroyed) == ["app01", "app02"]
        assert session.world == []


def test_core_modules_do_not_import_libvirt(no_libvirt):
    """Importing core from a cold start must not reach for libvirt."""
    for mod in list(sys.modules):
        if mod.startswith("orchestrator"):
            del sys.modules[mod]

    for mod in ("backends.base", "config", "marker", "qcow2"):
        importlib.import_module(f"orchestrator.{mod}")

    assert "libvirt" not in sys.modules


def test_registry_is_importable_without_libvirt(no_libvirt):
    """The registry is what a real run touches first. If importing it drags in a
    hypervisor library, nothing above matters."""
    for mod in [m for m in sys.modules if m.startswith("orchestrator")]:
        del sys.modules[mod]

    from orchestrator.backends import REGISTRY

    assert isinstance(REGISTRY, dict)
    assert "libvirt" not in sys.modules
