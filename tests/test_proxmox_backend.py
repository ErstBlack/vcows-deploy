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
    assert REGISTRY["proxmox"] is backend


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

    assert isinstance(pkg.ProxmoxBackend(), Backend)


def test_prepare_builds_a_seed_per_vm_and_carries_the_image(
    backend, pve_cfg, tmp_path, pve_token
):
    discovered = Discovered(
        vms=(), artifacts={"image": {"create": True, "volid": "local:import/g.qcow2"}}
    )
    prepared = backend.prepare(pve_cfg, tmp_path, discovered)
    assert set(prepared.artifacts["seed_isos"]) == {"app01", "app02"}
    assert prepared.artifacts["image"]["create"] is True
    assert (tmp_path / "app01-seed.iso").is_file()
    assert (tmp_path / "app02-seed.iso").is_file()


def test_prepare_cleans_up_after_nothing(backend, pve_cfg, tmp_path):
    """The seed ISOs outlive `prepare` deliberately: the run directory keeps them
    so a VM that will not boot can be debugged from the media it was given."""
    discovered = Discovered(
        vms=(), artifacts={"image": {"create": False, "volid": "x"}}
    )
    prepared = backend.prepare(pve_cfg, tmp_path, discovered)
    assert Path(prepared.artifacts["seed_isos"]["app01"]).is_file()


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
    """The seven the ABC declares, driven through the instance the registry
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
        # The golden image is already there, so `create` below has no local
        # file to upload -- only the seed ISOs `prepare` just wrote.
        content={"local": {"import": ["local:import/golden.qcow2"], "iso": []}},
    )
    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", lambda host, **kw: w)

    assert backend.config_schema()["required"]
    # Warnings only: the golden image is not on this machine, which the
    # image checks say so about. No errors is the assertion.
    assert [p for p in backend.validate(pve_cfg) if p.fatal] == []
    with backend.connect(pve_cfg) as session:
        discovered = backend.preflight(pve_cfg, session)
        assert discovered.vms == ()
        prepared = backend.prepare(pve_cfg, tmp_path, discovered)
        assert set(prepared.artifacts["seed_isos"]) == {"app01", "app02"}
        assert set(backend.create(pve_cfg, session, prepared)) == {"app01", "app02"}
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
