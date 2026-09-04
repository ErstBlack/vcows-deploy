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

from orchestrator.backends.base import (
    Action,
    Backend,
    Discovered,
    decide,
)
from orchestrator.config import load
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
def seam_cfg(tmp_path):
    p = tmp_path / "lab-a.yaml"
    p.write_text(textwrap.dedent(CONFIG))
    return p


def test_libvirt_is_actually_blocked(no_libvirt):
    """Guard the guard: if the fixture stopped working, every test below would
    pass for the wrong reason."""
    with pytest.raises(ImportError):
        __import__("libvirt")


def test_full_pipeline_without_libvirt(no_libvirt, seam_cfg, tmp_path):
    """validate -> preflight -> prepare -> create -> destroy."""
    backend = FakeBackend()
    registry = {"fake": backend}

    config, _ = load(seam_cfg, registry)

    # -- deploy: everything that touches the target happens here ------------
    with backend.connect(config) as session:
        discovered = backend.preflight(config, session)
        decisions, problems = decide(
            [vm["name"] for vm in config["vms"]],
            discovered.vms,
            config["deployment"],
        )
        assert [d.action for d in decisions] == [Action.CREATE, Action.CREATE]
        assert problems == []

    assert session.closed, "connect() must close its session on the way out"

    # -- and prepare runs with the connection already closed ----------------
    # prepare gets what preflight found, not the ability to go and look again.
    workdir = tmp_path / "work"
    workdir.mkdir()
    prepared = backend.prepare(config, workdir, discovered)
    assert prepared["existing_names"] == []

    # -- create, against a session of its own -------------------------------
    with backend.connect(config) as session:
        vms = backend.create(config, session, prepared)

    assert set(vms) == {"app01", "app02"}
    for name, record in vms.items():
        # The two keys `Backend.create` promises of every backend's record.
        assert record["name"] == name
        assert "configured_address" in record

    # -- second deploy is a no-op ------------------------------------------
    # Which is also the check that `create` marked what it made: an unmarked VM
    # of the same name would be a REFUSE here rather than a SKIP.
    with backend.connect(config) as session:
        decisions, _ = decide(
            [vm["name"] for vm in config["vms"]],
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


def test_prepare_works_from_data_alone(no_libvirt, seam_cfg, tmp_path):
    """No session is constructed anywhere in this test, and prepare still
    produces what `create` is handed."""
    backend = FakeBackend()
    config, _ = load(seam_cfg, {"fake": backend})

    prepared = backend.prepare(
        config, tmp_path, Discovered(vms=(), artifacts={"existing_names": []})
    )

    assert prepared["existing_names"] == []
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
    the only path core ever takes. Every delegation is a single forwarding line,
    which is exactly the kind of line a rename breaks silently: the free function
    keeps its own tests and passes them while the method calls the wrong one, or
    drops an argument on the way.
    """
    from orchestrator.backends.libvirt import LibvirtBackend
    from orchestrator.backends.libvirt import destroy as destroy_mod
    from orchestrator.backends.libvirt import preflight as preflight_mod
    from orchestrator.backends.libvirt import schema as schema_mod

    backend = LibvirtBackend()
    calls = []

    delegations = [
        ("validate", schema_mod, "validate", ("cfg",)),
        ("connect", preflight_mod, "connect", ("cfg",)),
        ("preflight", preflight_mod, "preflight", ("cfg", "session")),
        ("destroy", destroy_mod, "destroy", ("cfg", "session", "targets")),
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


def test_create_renders_first_and_hands_the_values_to_the_session(monkeypatch):
    """The one delegation that is not a straight forwarding line: it calls two
    functions, and the argument order it calls the second one with is not the
    order it was called with. Both are what a rename or a swapped pair breaks
    while each half keeps passing its own tests.
    """
    from orchestrator.backends.libvirt import LibvirtBackend
    from orchestrator.backends.libvirt import create as create_mod
    from orchestrator.backends.libvirt import render as render_mod

    monkeypatch.setattr(
        render_mod, "render", lambda cfg, prepared: ("rendered", cfg, prepared)
    )
    monkeypatch.setattr(
        create_mod, "create", lambda conn, values: ("created", conn, values)
    )

    cfg, prepared = {"deployment": "lab-a"}, {}
    assert LibvirtBackend().create(cfg, "session", prepared) == (
        "created",
        "session",
        ("rendered", cfg, prepared),
    )
