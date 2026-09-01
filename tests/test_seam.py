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
import inspect
import sys
import textwrap

import pytest

from orchestrator.backends.base import Action, Backend, Discovered, Existing, decide
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

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        # `level` matters: the backend package is *also* called libvirt, so
        # `from .libvirt import LibvirtBackend` arrives here as name="libvirt"
        # with level=1. Blocking that would prove nothing about the hypervisor
        # binding and would only stop the registry importing itself. Absolute
        # imports -- level 0 -- are the ones this fixture exists to catch.
        if level == 0 and (name == "libvirt" or name.startswith("libvirt.")):
            raise ImportError(f"{name} is blocked by the seam test")
        return real_import(name, globals, locals, fromlist, level)

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

    config, _ = load(cfg, registry)

    # -- deploy: everything that touches the target happens here ------------
    with backend.connect(config) as session:
        discovered = backend.preflight(config, session)
        decisions, problems = decide(
            vm_names(config), discovered.vms, config["deployment"]
        )
        assert [d.action for d in decisions] == [Action.CREATE, Action.CREATE]
        assert problems == []

    assert session.closed, "connect() must close its session on the way out"

    # -- and the apply runs with the connection already closed --------------
    # prepare gets what preflight found, not the ability to go and look again.
    workdir = tmp_path / "work"
    workdir.mkdir()
    with backend.prepare(config, workdir, discovered) as prepared:
        tfvars = backend.render(config, prepared)
    assert prepared.artifacts["existing_names"] == []

    assert set(tfvars["vms"]) == {"app01", "app02"}
    for name, vm in tfvars["vms"].items():
        assert (
            Marker.from_json(vm["marker_xml"].split(">", 1)[1].rsplit("<", 1)[0]).name
            == name
        )

    # Stand in for `tofu apply` + `tofu output -json`.
    backend.world.extend(
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

    # -- second deploy is a no-op ------------------------------------------
    with backend.connect(config) as session:
        decisions, _ = decide(
            vm_names(config),
            backend.preflight(config, session).vms,
            config["deployment"],
        )
        assert [d.action for d in decisions] == [Action.SKIP, Action.SKIP]

    # -- destroy, with no config-derived state ------------------------------
    with backend.connect(config) as session:
        targets = [
            e for e in backend.preflight(config, session).vms if e.marker is not None
        ]
        backend.destroy(config, session, targets)
        assert sorted(session.destroyed) == ["app01", "app02"]
        assert session.world == []


def test_prepare_is_handed_data_not_a_connection():
    """The guarantee, asserted rather than documented.

    An earlier version passed the live session here so the backend could ask
    whether the golden image was already on the host. It turned out preflight
    already walks the pool for §2's orphan-volume refusal, so that was a second
    lookup of a fact it was already holding -- and it let `prepare` reach the
    hypervisor for anything else too. A signature check is the only thing that
    notices if a session creeps back in, because the call site looks identical.
    """
    params = inspect.signature(Backend.prepare).parameters
    assert list(params) == ["self", "cfg", "workdir", "discovered"]


def test_prepare_and_render_work_from_data_alone(no_libvirt, cfg, tmp_path):
    """No session is constructed anywhere in this test, and the apply half still
    completes."""
    backend = FakeBackend()
    config, _ = load(cfg, {"fake": backend})

    with backend.prepare(
        config, tmp_path, Discovered(vms=(), artifacts={"existing_names": []})
    ) as prepared:
        tfvars = backend.render(config, prepared)

    assert set(tfvars["vms"]) == {"app01", "app02"}
    assert backend.sessions == [], "prepare must not have opened a connection"


def test_core_modules_do_not_import_libvirt(no_libvirt, monkeypatch):
    """Importing core from a cold start must not reach for libvirt.

    `delitem`, not `del`: the re-import below builds a *second* set of
    `orchestrator` classes, and a plain `del` leaves them in `sys.modules` for
    good. Every test module already imported holds the first set, so anything
    that imports `orchestrator` afterwards compares members of two classes that
    print identically -- `Severity.WARNING != Severity.WARNING`. Invisible under
    one `pytest` run, which imports each test module once and runs this file
    late; fatal under anything that runs the suite twice in one interpreter.
    `mutmut` does exactly that, three times, before it forks a single mutant.
    monkeypatch's teardown puts the originals back. The `no_libvirt` fixture
    above uses `delitem` for the same reason.
    """
    for mod in list(sys.modules):
        if mod.startswith("orchestrator"):
            monkeypatch.delitem(sys.modules, mod)

    for mod in (
        "backends.base",
        "cloudinit",
        "config",
        "imagecheck",
        "limits",
        "marker",
        "problems",
        "qcow2",
    ):
        importlib.import_module(f"orchestrator.{mod}")

    assert "libvirt" not in sys.modules


def test_registry_is_importable_without_libvirt(no_libvirt, monkeypatch):
    """The registry is what a real run touches first. If importing it drags in a
    hypervisor library, nothing above matters.

    `delitem` for the reason the test above gives.
    """
    for mod in [m for m in sys.modules if m.startswith("orchestrator")]:
        monkeypatch.delitem(sys.modules, mod)

    from orchestrator.backends import REGISTRY

    assert isinstance(REGISTRY, dict)
    assert "libvirt" not in sys.modules


# -- the class the registry holds -------------------------------------------


def test_the_backend_delegates_every_call_with_its_arguments_intact(monkeypatch):
    """`REGISTRY` holds this class, not the modules behind it, so the wiring is
    the only path core ever takes -- and five of its six delegating methods were
    reached by no test at all. Every one is a single forwarding line, which is
    exactly the kind of line a rename breaks silently: the free function keeps its
    own tests and passes them while the method calls the wrong one, or drops an
    argument on the way.
    """
    from orchestrator.backends.libvirt import LibvirtBackend
    from orchestrator.backends.libvirt import destroy as destroy_mod
    from orchestrator.backends.libvirt import preflight as preflight_mod
    from orchestrator.backends.libvirt import render as render_mod
    from orchestrator.backends.libvirt import schema as schema_mod

    backend = LibvirtBackend()
    calls = []

    delegations = [
        ("validate", schema_mod, "validate", ("cfg",)),
        ("connect", preflight_mod, "connect", ("cfg",)),
        ("preflight", preflight_mod, "preflight", ("cfg", "session")),
        ("destroy", destroy_mod, "destroy", ("cfg", "session", "targets")),
        ("render", render_mod, "render", ("cfg", "prepared")),
    ]
    for _, module, function, _ in delegations:
        monkeypatch.setattr(
            module,
            function,
            lambda *args, _f=function: calls.append((_f, args)) or f"{_f}() said so",
        )

    for method, _, function, args in delegations:
        assert getattr(backend, method)(*args) == f"{function}() said so"

    assert calls == [(function, args) for _, _, function, args in delegations]
    assert backend.config_schema() is schema_mod.TARGET_SCHEMA
