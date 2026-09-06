"""The backend class, and the one function that reads the credential.

`connect` gets its own tests because it is where the credential is read, where
TLS verification is decided, and the only place pyvmomi is constructed. None of
that is reachable from anywhere else in the package yet: `preflight`, `create`
and `destroy` are stubs until the chunks that write them land, and one test
below pins that they say so rather than doing nothing.

The registry here is a dict this module builds. `orchestrator.backends.REGISTRY`
does not name this backend until the register chunk, so master never carries a
config that can select a half-built one -- and core takes a registry argument
everywhere, which is what makes that possible.
"""

from __future__ import annotations

import builtins
import logging
import ssl
import sys
from pathlib import Path

import pytest
import yaml

from orchestrator.backends.base import Backend
from orchestrator.backends.vsphere import VsphereBackend, api, schema
from orchestrator.config import core_schema, load
from tests.conftest import VSPHERE_CA_CERT, VSPHERE_CONFIG
from tests.fake_vsphere import COOKIE, FakeServiceInstance, disconnect, smart_connect

REGISTRY = {"vsphere": VsphereBackend()}


@pytest.fixture
def backend() -> Backend:
    return REGISTRY["vsphere"]


@pytest.fixture
def fake_vcenter(monkeypatch):
    """Stand in for `SmartConnect` and `Disconnect`, recording both.

    Patched on `pyVim.connect` rather than on `api`, because `api.connect`
    imports the two names inside its own body -- which is the property
    `test_pyvmomi_is_not_imported_at_module_scope` exists to keep.
    """
    import pyVim.connect

    built: dict = {}
    monkeypatch.setattr(pyVim.connect, "SmartConnect", smart_connect(built))
    monkeypatch.setattr(pyVim.connect, "Disconnect", disconnect)
    return built


# -- the class -----------------------------------------------------------


def test_it_is_registered_under_its_own_name(backend, tmp_path):
    """Through a registry this module built: `config.core_schema` and
    `config.load` both take one, so a backend is reachable end to end before it
    is in the shipped `REGISTRY`."""
    assert isinstance(backend, Backend)
    assert core_schema(REGISTRY)["properties"]["target"]["properties"]["vsphere"] is (
        schema.TARGET_SCHEMA
    )

    path = Path(tmp_path / "lab-a.yaml")
    path.write_text(yaml.safe_dump(VSPHERE_CONFIG))
    cfg, problems = load(path, REGISTRY)
    assert cfg["backend"] == "vsphere"
    # Warnings only: the golden image is not on this machine, which the image
    # checks say so about. No errors is what `load` raising would have said.
    assert [p for p in problems if p.fatal] == []


def test_pyvmomi_is_not_imported_at_module_scope(monkeypatch):
    """Once the registry names VsphereBackend, this package is imported on every
    run -- including runs that only ever talk to libvirt. Same rule the other two
    backends follow for their own bindings, and the same reason.

    pyvmomi *is* installed here, so this actively breaks the import rather than
    relying on its absence -- exactly as tests/test_seam.py does. Both top-level
    packages the SDK ships are blocked: `SmartConnect` lives in `pyVim`.
    """
    blocked = {"pyVmomi", "pyVim"}
    for name in [m for m in sys.modules if m.split(".")[0] in blocked]:
        monkeypatch.delitem(sys.modules, name)
    for name in [m for m in sys.modules if "backends.vsphere" in m]:
        monkeypatch.delitem(sys.modules, name)

    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 0 and name.split(".")[0] in blocked:
            raise ImportError(f"{name} is blocked by the seam test")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)

    with pytest.raises(ImportError):
        __import__("pyVmomi")
    with pytest.raises(ImportError):
        __import__("pyVim")

    import orchestrator.backends.vsphere as pkg

    assert isinstance(pkg.VsphereBackend(), Backend)


def test_the_backend_delegates_every_call_with_its_arguments_intact(
    backend, monkeypatch
):
    """The class is what a registry holds, not the modules behind it, so the
    wiring is the only path core ever takes. Every delegation is a single
    forwarding line, which is exactly the kind of line a rename breaks silently:
    the free function keeps its own tests and passes them while the method calls
    the wrong one, or drops an argument on the way.
    """
    calls = []
    delegations = [
        ("validate", schema, "validate", ("cfg",), {"verify_digest": True}),
        ("connect", api, "connect", ("cfg",), {}),
    ]
    for _, module, function, _, _ in delegations:
        monkeypatch.setattr(
            module,
            function,
            lambda *args, _f=function, **kwargs: (
                calls.append((_f, args, kwargs)) or f"{_f}() said so"
            ),
        )

    for method, _, function, args, _ in delegations:
        assert getattr(backend, method)(*args) == f"{function}() said so"

    assert calls == [(f, args, kwargs) for _, _, f, args, kwargs in delegations]
    assert backend.config_schema() is schema.TARGET_SCHEMA


def test_the_backend_forwards_the_digest_flag(backend, vsphere_cfg, monkeypatch):
    """A forwarding line that dropped `verify_digest` would make `destroy` hash
    the golden image again -- ~59 s for 10 GiB -- with nothing else failing."""
    seen: list[bool] = []

    def record(cfg, *, verify_digest=True):
        seen.append(verify_digest)
        return []

    monkeypatch.setattr(schema, "validate", record)
    assert backend.validate(vsphere_cfg) == []
    assert backend.validate(vsphere_cfg, verify_digest=False) == []
    assert seen == [True, False]


def test_the_three_unwritten_methods_refuse_rather_than_doing_nothing(
    backend, vsphere_cfg
):
    """The ABC's own argument, applied to a half-built backend: a `preflight`
    that returned an empty `Discovered` would skip the safety check, and a
    `destroy` that returned an empty `Outcome` would delete nothing and exit
    successfully."""
    for call in (
        lambda: backend.preflight(vsphere_cfg, "session"),
        lambda: backend.create(vsphere_cfg, "session", {}),
        lambda: backend.destroy(vsphere_cfg, "session", []),
    ):
        with pytest.raises(NotImplementedError, match="chunk has not landed"):
            call()


def test_prepare_builds_a_seed_per_vm(backend, vsphere_cfg, tmp_path):
    """The inherited `Backend.prepare`, reached through this backend: nothing in
    a seed ISO is hypervisor-specific, so there is nothing here to override yet.
    The conversion chunk is what overrides it."""
    from orchestrator.backends.base import Discovered

    prepared = backend.prepare(
        vsphere_cfg, tmp_path, Discovered(vms=(), artifacts={"image": {"create": True}})
    )
    assert set(prepared["seed_isos"]) == {"app01", "app02"}
    assert prepared["image"]["create"] is True
    assert (tmp_path / "app01-seed.iso").is_file()


# -- connect -------------------------------------------------------------


def test_connect_reads_the_credential_and_the_endpoint(vsphere_cfg, fake_vcenter):
    with api.connect(vsphere_cfg) as session:
        assert session.content is session.si.content
        assert session.cookie == COOKIE
    assert fake_vcenter["host"] == "vcenter.example.com"
    assert fake_vcenter["port"] == api.DEFAULT_PORT
    assert fake_vcenter["user"] == "vcows@vsphere.local"
    assert fake_vcenter["pwd"] == VSPHERE_CONFIG["target"]["vsphere"]["password"]


def test_a_port_in_the_endpoint_is_the_one_used(vsphere_cfg, fake_vcenter):
    """443 is a default, not an assumption: a vCenter behind a reverse proxy is
    the case that needs the endpoint's own port."""
    vsphere_cfg["target"]["vsphere"]["endpoint"] = "https://vcenter.example.com:8443"
    with api.connect(vsphere_cfg):
        pass
    assert fake_vcenter["port"] == 8443


def test_the_session_is_closed_on_the_way_out(vsphere_cfg, fake_vcenter):
    with api.connect(vsphere_cfg) as session:
        assert not session.si.disconnected
    assert session.si.disconnected


def test_the_session_is_closed_when_the_body_raises(vsphere_cfg, fake_vcenter):
    """A run that raised is exactly the one an operator retries at once, and
    vCenter holds an idle session for half an hour."""
    held = {}
    with pytest.raises(RuntimeError), api.connect(vsphere_cfg) as session:
        held["si"] = session.si
        raise RuntimeError("boom")
    assert held["si"].disconnected


def test_connect_verifies_tls_by_default(vsphere_cfg, fake_vcenter):
    """Neither knob set means pyvmomi's own default context, which verifies."""
    with api.connect(vsphere_cfg):
        pass
    assert "sslContext" not in fake_vcenter
    assert "disableSslCertValidation" not in fake_vcenter


def test_a_ca_certificate_becomes_the_ssl_context(vsphere_cfg, fake_vcenter):
    """The config carries the certificate and `ssl` takes the certificate, so --
    unlike the Proxmox backend, whose `requests` wants a path -- nothing is
    written to disk."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with api.connect(vsphere_cfg):
        pass
    context = fake_vcenter["sslContext"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert "vcows test CA" in str(context.get_ca_certs())


def test_insecure_turns_verification_off_and_outranks_a_ca_certificate(
    vsphere_cfg, fake_vcenter
):
    """`validate` refuses the two together, so this is what the code does with a
    config that got past it: no verification, rather than a certificate that
    reads as one thing and behaves as another. No context is built either."""
    vsphere_cfg["target"]["vsphere"]["insecure"] = True
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with api.connect(vsphere_cfg):
        pass
    assert fake_vcenter["disableSslCertValidation"] is True
    assert "sslContext" not in fake_vcenter


def test_the_cookie_is_taken_off_the_stub_once(vsphere_cfg, monkeypatch):
    """The datastore uploads are plain HTTP against vCenter's `/folder`
    endpoint, and this cookie is the only thing that authorises them. Kept on the
    session so nothing later reaches into pyvmomi's internals again."""
    import pyVim.connect

    si = FakeServiceInstance(cookie='vmware_soap_session="held"')
    monkeypatch.setattr(pyVim.connect, "SmartConnect", smart_connect({}, si))
    monkeypatch.setattr(pyVim.connect, "Disconnect", disconnect)
    with api.connect(vsphere_cfg) as session:
        assert session.cookie == 'vmware_soap_session="held"'


def test_the_password_never_reaches_the_log(vsphere_cfg, fake_vcenter, caplog):
    """The connect line names the endpoint and the user, because that is what an
    operator debugging a failed login needs. The password is not either of
    those."""
    vsphere_cfg["target"]["vsphere"]["password"] = "SUPERSECRETVALUE"  # noqa: S105
    with caplog.at_level(logging.DEBUG), api.connect(vsphere_cfg):
        pass
    assert "vcows@vsphere.local" in caplog.text
    assert "SUPERSECRETVALUE" not in caplog.text
