"""The backend class, and the one function that reads the credential.

`connect` gets its own tests because it is where the API token is read, where TLS
verification is decided, and the only place `proxmoxer` is constructed. None of
that is reachable from the preflight or destroy tests, which are handed a session
that already exists.
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

from orchestrator.backends import REGISTRY
from orchestrator.backends.base import Backend, Discovered, Existing
from orchestrator.backends.proxmox import api
from orchestrator.marker import Marker
from tests.fake_proxmox import FakeProxmox


@pytest.fixture
def backend() -> Backend:
    return REGISTRY["proxmox"]


@pytest.fixture
def fake_proxmoxer(monkeypatch):
    """Stand in for `proxmoxer.ProxmoxAPI`, recording how it was constructed."""
    built = {}

    def factory(host, **kw):
        built["host"] = host
        built.update(kw)
        return FakeProxmox()

    import proxmoxer

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", factory)
    return built


# -- the class -----------------------------------------------------------


def test_it_is_registered_under_its_own_name(backend):
    assert isinstance(backend, Backend)
    assert backend.name == "proxmox"
    assert REGISTRY["proxmox"] is backend


def test_the_module_lives_beside_the_class(backend):
    """`cli.module_dir` resolves `<package>/tofu/` by convention, not by method,
    and only while the class is defined in the package's own __init__."""
    from orchestrator.cli import module_dir

    where = module_dir(backend)
    assert where.name == "tofu"
    assert (where / "main.tf").is_file()
    assert (where / "variables.tf").is_file()
    assert (where / "outputs.tf").is_file()


def test_the_module_stages_cleanly(backend, tmp_path):
    """`_stage_module` refuses anything that is not *.tf or the lock file, so a
    stray file in the module directory fails the deploy rather than being
    silently left behind."""
    from orchestrator.cli import _stage_module, module_dir

    _stage_module(module_dir(backend), tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {
        "main.tf",
        "variables.tf",
        "outputs.tf",
    }


def test_proxmoxer_is_not_imported_at_module_scope(monkeypatch):
    """The registry names ProxmoxBackend, so this package is imported on every
    run -- including runs that only ever talk to libvirt. Same rule the libvirt
    backend follows for `import libvirt`, and the same reason.

    `proxmoxer` *is* installed here, so this actively breaks the import rather
    than relying on its absence -- exactly as tests/test_seam.py does.
    """
    for name in [m for m in sys.modules if m.split(".")[0] == "proxmoxer"]:
        monkeypatch.delitem(sys.modules, name)
    for name in [m for m in sys.modules if "backends.proxmox" in m]:
        monkeypatch.delitem(sys.modules, name)

    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.split(".")[0] == "proxmoxer":
            raise ImportError(f"{name} is blocked by the seam test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)

    with pytest.raises(ImportError):
        __import__("proxmoxer")

    import orchestrator.backends.proxmox as pkg

    assert pkg.ProxmoxBackend().name == "proxmox"


def test_parse_outputs_raises_on_a_module_with_no_vms_output(backend):
    """Read as an empty inventory it would be reported as `created 0 VM(s)`
    under `outcome: ok`, beside an inventory.json that says otherwise."""
    with pytest.raises(ValueError, match="declared no `vms` output"):
        backend.parse_outputs({"something_else": {"value": {}}})
    assert backend.parse_outputs({"vms": {"value": {"app01": {}}}}).vms == {"app01": {}}
    # A declared output with nothing in it is an empty inventory, not a None that
    # `cli._deploy` would then compare against the set it asked for.
    assert backend.parse_outputs({"vms": {}}).vms == {}


def test_prepare_builds_a_seed_per_vm_and_carries_the_image(
    backend, pve_cfg, tmp_path, pve_token
):
    discovered = Discovered(
        vms=(), artifacts={"image": {"create": True, "volid": "local:import/g.qcow2"}}
    )
    with backend.prepare(pve_cfg, tmp_path, discovered) as prepared:
        assert set(prepared.artifacts["seed_isos"]) == {"app01", "app02"}
        assert prepared.artifacts["image"]["create"] is True
    assert (tmp_path / "app01-seed.iso").is_file()
    assert (tmp_path / "app02-seed.iso").is_file()


def test_prepare_holds_nothing_open(backend, pve_cfg, tmp_path):
    """The research predicted this backend would be the one that serves the image
    over HTTP for the duration of the apply. It is not: the provider uploads over
    the same API token, so there is no socket to hold."""
    discovered = Discovered(
        vms=(), artifacts={"image": {"create": False, "volid": "x"}}
    )
    with backend.prepare(pve_cfg, tmp_path, discovered) as prepared:
        first = Path(prepared.artifacts["seed_isos"]["app01"])
    assert first.is_file(), "prepare must not clean up after itself"


# -- connect -------------------------------------------------------------


def test_connect_reads_the_token_and_splits_it(pve_cfg, fake_proxmoxer, pve_token):
    with api.connect(pve_cfg) as session:
        assert session.node == "pve1"
        assert session.datastore == "local-lvm"
        assert session.import_datastore == "local"
    assert fake_proxmoxer["user"] == "vcows@pve"
    assert fake_proxmoxer["token_name"] == "deploy"  # noqa: S105  a token id
    assert fake_proxmoxer["token_value"].startswith("00000000-")


def test_connect_refuses_without_a_token(pve_cfg, fake_proxmoxer, monkeypatch):
    monkeypatch.delenv("PROXMOX_VE_API_TOKEN", raising=False)
    with (
        pytest.raises(api.ProxmoxApiError, match="unset or malformed"),
        api.connect(pve_cfg),
    ):
        pass


def test_connect_verifies_tls_by_default(pve_cfg, fake_proxmoxer, pve_token):
    with api.connect(pve_cfg):
        pass
    assert fake_proxmoxer["verify_ssl"] is True


def test_insecure_is_the_only_way_to_turn_verification_off(
    pve_cfg, fake_proxmoxer, pve_token
):
    """There is no ca_file: bpg/proxmox 0.111.1 has no CA-bundle option, so one
    would be honoured here and ignored by the apply."""
    pve_cfg["target"]["proxmox"]["insecure"] = True
    with api.connect(pve_cfg):
        pass
    assert fake_proxmoxer["verify_ssl"] is False


@pytest.mark.parametrize(
    ("endpoint", "expected"),
    [
        ("https://pve.example.com:8006", "pve.example.com:8006"),
        ("https://pve.example.com", "pve.example.com"),
        ("https://pve.example.com/", "pve.example.com"),
    ],
)
def test_the_host_is_taken_from_the_endpoint(endpoint, expected):
    assert api._endpoint_host(endpoint) == expected


def test_the_token_never_reaches_the_log(pve_cfg, fake_proxmoxer, pve_token, caplog):
    """The connect line names the endpoint and the token's user, because that is
    what an operator debugging a 401 needs. The secret is not either of those."""
    import logging

    with caplog.at_level(logging.DEBUG), api.connect(pve_cfg):
        pass
    assert "vcows@pve" in caplog.text
    assert pve_token.split("=", 1)[1] not in caplog.text


def test_a_rejected_token_is_re_raised_as_our_own_error(
    pve_cfg, monkeypatch, pve_token
):
    """`cli` never imports this backend to catch its errors, the same way it
    never imports the libvirt backend's DestroyError. So proxmoxer's exceptions
    must not escape the session."""
    import proxmoxer
    from proxmoxer.core import AuthenticationError

    class Boom(FakeProxmox):
        def dispatch(self, *a, **kw):
            raise AuthenticationError("401 no ticket")

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", lambda host, **kw: Boom())
    with (
        pytest.raises(api.ProxmoxApiError, match="rejected the PROXMOX_VE_API_TOKEN"),
        api.connect(pve_cfg) as session,
    ):
        api.cluster_vms(session)


def test_an_api_failure_is_re_raised_as_our_own_error(pve_cfg, monkeypatch, pve_token):
    """Named by host, because a 500 from the wrong cluster is the confusing one.
    proxmoxer's own type does not escape the session."""
    import proxmoxer
    from proxmoxer.core import ResourceException as RealResourceException

    class Boom(FakeProxmox):
        def dispatch(self, *a, **kw):
            raise RealResourceException(500, "Internal Server Error", "boom")

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", lambda host, **kw: Boom())
    with (
        pytest.raises(api.ProxmoxApiError, match=r"pve\.example\.com:8006"),
        api.connect(pve_cfg) as session,
    ):
        api.cluster_vms(session)


def test_the_session_is_closed_even_when_the_body_raises(
    pve_cfg, monkeypatch, pve_token
):
    closed = {"n": 0}

    class Closable(FakeProxmox):
        @property
        def _backend(self):
            class B:
                @staticmethod
                def get_session():
                    class S:
                        @staticmethod
                        def close():
                            closed["n"] += 1

                    return S()

            return B()

    import proxmoxer

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", lambda host, **kw: Closable())
    with pytest.raises(RuntimeError), api.connect(pve_cfg):
        raise RuntimeError("something in the body")
    assert closed["n"] == 1


def test_a_client_with_no_closable_session_is_not_fatal(
    pve_cfg, fake_proxmoxer, pve_token
):
    """`_close` reaches through a private attribute, so it is guarded: proxmoxer
    is free to move it and a failed close must never fail a run.

    `FakeProxmox` has no `_backend`, so the guarded call raises inside `_close`
    on the way out of the block below -- the session still has to arrive, and
    leaving the block still has to be uneventful.
    """
    with api.connect(pve_cfg) as session:
        assert isinstance(session, api.Session)
    assert fake_proxmoxer["verify_ssl"] is True


def test_the_task_wait_carries_our_ceiling_rather_than_proxmoxers(
    pve_cfg, pve_token, monkeypatch
):
    """A stop on a wedged guest is the slow one, and proxmoxer's own default
    would abandon a teardown that was going to finish. The poll interval is a
    fixed tax per wait, so it is ours to set too."""
    from proxmoxer.tools import Tasks

    seen = {}

    def record(prox, task_id, **kw):
        seen.update(kw)
        return {"status": "stopped", "exitstatus": "OK"}

    monkeypatch.setattr(Tasks, "blocking_status", record)
    w = FakeProxmox(vms={("pve1", "100"): {"name": "app01"}})
    session = api.Session(
        prox=w, node="pve1", datastore="local-lvm", import_datastore="local"
    )
    api.delete_vm(session, "pve1", "100")
    assert seen == {
        "timeout": api.TASK_TIMEOUT,
        "polling_interval": api.POLL_INTERVAL,
    }


def test_every_abstract_method_is_reachable_through_the_class(
    backend, pve_cfg, pve_token, monkeypatch, tmp_path
):
    """The eight the ABC declares, driven through the instance the registry
    holds rather than through the free functions they delegate to."""
    import proxmoxer

    # proxmoxer's task poller sleeps once per wait; the teardown below is the
    # only thing here that waits on one.
    monkeypatch.setattr(api, "POLL_INTERVAL", 0)
    w = FakeProxmox(
        storages=[
            {"storage": "local", "content": "import,iso"},
            {"storage": "local-lvm", "content": "images"},
        ],
        content={"local": {"import": [], "iso": []}},
    )
    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", lambda host, **kw: w)

    assert backend.config_schema()["required"]
    # Warnings only: the golden image is not on this machine, which the
    # image checks say so about. No errors is the assertion.
    assert [p for p in backend.validate(pve_cfg) if p.fatal] == []
    with backend.connect(pve_cfg) as session:
        discovered = backend.preflight(pve_cfg, session)
        assert discovered.vms == ()
        with backend.prepare(pve_cfg, tmp_path, discovered) as prepared:
            tfvars = backend.render(pve_cfg, prepared)
        assert set(tfvars["vms"]) == {"app01", "app02"}
        # With a target rather than none, so `destroy` reaches the session it is
        # handed rather than returning on an empty list.
        w.vms[("pve1", "100")] = {
            "name": "app01",
            "description": Marker.for_vm("app01", "lab-a").to_description(),
        }
        target = Existing(
            name="app01", id="pve1/100", marker=Marker.for_vm("app01", "lab-a")
        )
        assert backend.destroy(pve_cfg, session, [target]).destroyed == ["app01"]
    assert backend.parse_outputs({"vms": {"value": {}}}).vms == {}
