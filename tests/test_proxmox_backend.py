"""The backend class, and the one function that reads the credential.

`connect` gets its own tests because it is where the credential is read, where
TLS verification is decided, and the only place `proxmoxer` is constructed. None of
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
from tests.conftest import CA_CERT
from tests.fake_proxmox import FakeProxmox, upid


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
    assert set(prepared["seed_isos"]) == {"app01", "app02"}
    assert prepared["image"]["create"] is True
    assert (tmp_path / "app01-seed.iso").is_file()
    assert (tmp_path / "app02-seed.iso").is_file()


def test_prepare_cleans_up_after_nothing(backend, pve_cfg, tmp_path):
    """The seed ISOs outlive `prepare` deliberately: the run directory keeps them
    so a VM that will not boot can be debugged from the media it was given."""
    discovered = Discovered(
        vms=(), artifacts={"image": {"create": False, "volid": "x"}}
    )
    prepared = backend.prepare(pve_cfg, tmp_path, discovered)
    assert Path(prepared["seed_isos"]["app01"]).is_file()


# -- connect -------------------------------------------------------------


def test_connect_reads_the_token_and_splits_it(pve_cfg, fake_proxmoxer, pve_token):
    with api.connect(pve_cfg) as session:
        assert session.node == "pve1"
        assert session.datastore == "local-lvm"
        assert session.import_datastore == "local"
    assert fake_proxmoxer["user"] == "vcows@pve"
    assert fake_proxmoxer["token_name"] == "deploy"  # noqa: S105  a token id
    assert fake_proxmoxer["token_value"].startswith("00000000-")


def test_connect_reads_a_user_and_password_as_the_other_form(pve_cfg, fake_proxmoxer):
    """proxmoxer takes either shape; which one it gets is the config's call."""
    pve_cfg["target"]["proxmox"].pop("token")
    pve_cfg["target"]["proxmox"]["user"] = "root@pam"
    pve_cfg["target"]["proxmox"]["password"] = "hunter2"  # noqa: S105  not a password
    with api.connect(pve_cfg):
        pass
    assert fake_proxmoxer["user"] == "root@pam"
    assert fake_proxmoxer["password"] == "hunter2"  # noqa: S105
    assert "token_name" not in fake_proxmoxer


def test_connect_refuses_a_malformed_token_without_echoing_it(pve_cfg, fake_proxmoxer):
    """`validate` refuses this first and every verb runs it, so reaching here
    means the config changed underneath the run. The value stays out of the
    message for the same reason `validate`'s does."""
    pve_cfg["target"]["proxmox"]["token"] = "vcows@pve-deploy-SUPERSECRET"  # noqa: S105
    with pytest.raises(api.ProxmoxApiError) as caught, api.connect(pve_cfg):
        pass
    assert "target.proxmox.token is malformed" in str(caught.value)
    assert "SUPERSECRET" not in str(caught.value)


def test_connect_verifies_tls_by_default(pve_cfg, fake_proxmoxer, pve_token):
    with api.connect(pve_cfg):
        pass
    assert fake_proxmoxer["verify_ssl"] is True


def test_a_ca_certificate_reaches_proxmoxer_as_a_file_holding_it(
    pve_cfg, fake_proxmoxer, pve_token
):
    """The config carries the certificate and requests wants a path, so `connect`
    writes one. Measured in proxmoxer's https backend: `verify_ssl` is handed to
    requests' `verify=` unchanged, and requests takes a CA bundle path there.

    Read inside the session because that is the whole life of the file: it is
    written for proxmoxer to open by name, and removed on the way out.
    """
    pve_cfg["target"]["proxmox"]["ca_cert"] = CA_CERT
    with api.connect(pve_cfg):
        written = Path(fake_proxmoxer["verify_ssl"])
        assert written.read_text() == CA_CERT
        assert written.suffix == ".pem"
    assert not written.exists()


def test_the_ca_certificate_is_removed_when_the_credential_is_rejected(
    pve_cfg, monkeypatch, pve_token
):
    """A 401 is the likeliest way out of `connect`, and it leaves the session by
    the `except` rather than the normal exit -- so only a `finally` removes the
    file on that path."""
    import proxmoxer
    from proxmoxer.core import AuthenticationError

    built = {}

    def refuse(host, **kw):
        built["verify_ssl"] = kw["verify_ssl"]
        raise AuthenticationError("401 no ticket")

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", refuse)
    pve_cfg["target"]["proxmox"]["ca_cert"] = CA_CERT
    with pytest.raises(api.ProxmoxApiError), api.connect(pve_cfg):
        pass
    assert not Path(built["verify_ssl"]).exists()


def test_insecure_turns_verification_off_and_outranks_a_ca_certificate(
    pve_cfg, fake_proxmoxer, pve_token
):
    """`validate` refuses the two together, so this is what the code does with a
    config that got past it: no verification, rather than a certificate that
    reads as one thing and behaves as another. Nothing is written either."""
    pve_cfg["target"]["proxmox"]["insecure"] = True
    pve_cfg["target"]["proxmox"]["ca_cert"] = CA_CERT
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


def test_a_password_never_reaches_the_log_either(pve_cfg, fake_proxmoxer, caplog):
    """The other form, and the one an operator is likelier to reuse elsewhere."""
    import logging

    pve_cfg["target"]["proxmox"].pop("token")
    pve_cfg["target"]["proxmox"]["user"] = "root@pam"
    pve_cfg["target"]["proxmox"]["password"] = "SUPERSECRETVALUE"  # noqa: S105
    with caplog.at_level(logging.DEBUG), api.connect(pve_cfg):
        pass
    assert "root@pam" in caplog.text
    assert "SUPERSECRETVALUE" not in caplog.text


def test_a_rejected_credential_is_re_raised_as_our_own_error(
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
        pytest.raises(
            api.ProxmoxApiError, match=r"rejected the credentials in target\.proxmox"
        ),
        api.connect(pve_cfg) as session,
    ):
        api.cluster_vms(session)


def test_a_password_rejected_by_the_constructor_is_re_raised_too(pve_cfg, monkeypatch):
    """Password auth fetches a ticket inside proxmoxer's constructor, so this
    AuthenticationError arrives before there is a session to raise it on. A
    client built outside the `try` would let proxmoxer's own type escape."""
    import proxmoxer
    from proxmoxer.core import AuthenticationError

    def refuse(host, **kw):
        raise AuthenticationError("Couldn't authenticate user: root@pam")

    monkeypatch.setattr(proxmoxer, "ProxmoxAPI", refuse)
    pve_cfg["target"]["proxmox"].pop("token")
    pve_cfg["target"]["proxmox"]["user"] = "root@pam"
    pve_cfg["target"]["proxmox"]["password"] = "hunter2"  # noqa: S105  not a password
    with (
        pytest.raises(
            api.ProxmoxApiError, match=r"rejected the credentials in target\.proxmox"
        ),
        api.connect(pve_cfg),
    ):
        pass


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
    pve_cfg, pve_token, monkeypatch, caplog
):
    """A stop on a wedged guest is the slow one, and proxmoxer's own default
    would abandon a teardown that was going to finish. The poll interval is a
    fixed tax per wait, so it is ours to set too -- and both numbers are said
    once before the wait, because `blocking_status` polls silently and a run
    that has gone quiet is otherwise indistinguishable from one that has hung.
    """
    import logging

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
    caplog.set_level(logging.DEBUG, logger=api.log.name)
    api.delete_vm(session, "pve1", "100")
    assert seen == {
        "timeout": api.TASK_TIMEOUT,
        "polling_interval": api.POLL_INTERVAL,
    }
    task = upid("pve1", "qmdestroy", "100")
    assert [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"] == [
        f"delete 100: waiting on task {task}, polling every "
        f"{api.POLL_INTERVAL}s for up to {api.TASK_TIMEOUT}s",
        f"delete 100: task {task} ok",
    ]


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
        assert set(prepared["seed_isos"]) == {"app01", "app02"}
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
