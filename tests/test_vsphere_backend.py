"""The backend class, and the one function that reads the credential.

`connect` gets its own tests because it is where the credential is read, where
TLS verification is decided, and the only place pyvmomi is constructed. `wait`
gets its own for the reason the Proxmox backend's does: every task any phase
starts goes through it, so what it does with a task that fails or never
finishes is decided once. `create` is two calls rather than one forwarding
line, so one test below pins the order it makes them in.

The registry here is the shipped `orchestrator.backends.REGISTRY`: this backend
is registered, so the wiring test below is over the object a real run reaches
for rather than over a dict this module built.
"""

from __future__ import annotations

import builtins
import inspect
import logging
import ssl
import sys
import tempfile
from pathlib import Path

import pytest
import yaml
from pyVmomi import vim

from orchestrator.backends import REGISTRY
from orchestrator.backends.base import Backend, Discovered
from orchestrator.backends.vsphere import (
    api,
    convert,
    create,
    preflight,
    render,
    schema,
)
from orchestrator.config import core_schema, load
from tests.conftest import VSPHERE_CA_CERT, VSPHERE_CONFIG
from tests.fake_vsphere import (
    COOKIE,
    FakeServiceInstance,
    FakeTask,
    disconnect,
    smart_connect,
)
from tests.test_qcow2 import make_qcow2


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
    """Through the shipped registry: `backend: vsphere` in a config now resolves
    to this class, and `config.core_schema` composes its sub-schema in."""
    assert isinstance(backend, Backend)
    assert REGISTRY["vsphere"] is backend
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
        ("preflight", preflight, "preflight", ("cfg", "session"), {}),
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


def test_create_renders_first_and_hands_the_values_to_the_session(backend, monkeypatch):
    """The one delegation that is not a straight forwarding line: it calls two
    functions, and the argument order it calls the second one with is not the
    order it was called with. Both are what a rename or a swapped pair breaks
    while each half keeps passing its own tests -- the same gate
    `tests/test_seam.py` holds over the libvirt backend, and the one this
    backend has to pass now that `REGISTRY` names it.
    """
    monkeypatch.setattr(
        render, "render", lambda cfg, prepared: ("rendered", cfg, prepared)
    )
    monkeypatch.setattr(
        create, "create", lambda session, values: ("created", session, values)
    )

    cfg, prepared = {"deployment": "lab-a"}, {}
    assert backend.create(cfg, "session", prepared) == (
        "created",
        "session",
        ("rendered", cfg, prepared),
    )


# -- prepare -------------------------------------------------------------


@pytest.fixture
def converted(monkeypatch):
    """`convert.to_vmdk`, recording its arguments and writing nothing.

    What the real call does is `tests/test_vsphere_convert.py`'s subject and the
    smoke chunk's. What is asked here is whether `prepare` makes it at all, and
    with what.
    """
    calls: list[tuple] = []

    def fake(source, dest, subformat):
        calls.append((str(source), dest, subformat))
        return dest

    monkeypatch.setattr(convert, "to_vmdk", fake)
    return calls


@pytest.fixture
def golden_image(vsphere_cfg, tmp_path):
    """A header-only qcow2 where the config says the golden image is.

    `prepare` reads its virtual size, which is a real read of a real file: the
    conversion is faked above, the size is not.
    """
    source = make_qcow2(tmp_path / "golden.qcow2", 20 * 2**30)
    vsphere_cfg["image"]["source_qcow2"] = str(source)
    return source


def test_prepare_builds_a_seed_per_vm(backend, vsphere_cfg, tmp_path):
    """The inherited half, still inherited: nothing in a seed ISO is
    hypervisor-specific, and preflight's answer is forwarded whole rather than
    picked at."""
    prepared = backend.prepare(
        vsphere_cfg,
        tmp_path,
        Discovered(
            vms=(), artifacts={"image": {"create": False, "template": "golden.qcow2"}}
        ),
    )
    assert set(prepared["seed_isos"]) == {"app01", "app02"}
    assert prepared["image"]["template"] == "golden.qcow2"
    assert (tmp_path / "app01-seed.iso").is_file()


def test_prepare_converts_only_when_the_template_is_missing(
    backend, vsphere_cfg, tmp_path, converted, golden_image
):
    """Converting a multi-GB image to import nothing is what this branch avoids,
    and it is the only reason `preflight` reports on the template at all.

    The two keys are absent rather than empty when nothing was converted:
    `render` is what renders their absence, and a `prepare` that always set them
    would hand `create` the path of a file it never wrote.
    """
    already_there = backend.prepare(
        vsphere_cfg,
        tmp_path,
        Discovered(
            vms=(), artifacts={"image": {"create": False, "template": "golden.qcow2"}}
        ),
    )
    assert converted == []
    assert "vmdk" not in already_there
    assert "capacity" not in already_there

    prepared = backend.prepare(
        vsphere_cfg,
        tmp_path,
        Discovered(
            vms=(), artifacts={"image": {"create": True, "template": "golden.qcow2"}}
        ),
    )
    assert len(converted) == 1
    source, dest, _ = converted[0]
    assert source == str(golden_image)
    # Into the run directory, named after the template it becomes: a
    # `monolithicFlat` conversion writes its `-flat` extent beside it.
    assert dest == tmp_path / "golden.vmdk"
    assert prepared["vmdk"] == str(tmp_path / "golden.vmdk")
    # Read from the image itself, here rather than in `render`, which does no
    # I/O. The OVF descriptor the import chunk builds declares it.
    assert prepared["capacity"] == 20 * 2**30


@pytest.mark.parametrize(
    ("knob", "subformat"),
    [
        (None, "streamOptimized"),
        ("ovf", "streamOptimized"),
        ("datastore", "monolithicFlat"),
    ],
)
def test_the_import_knob_picks_the_subformat(
    backend, vsphere_cfg, tmp_path, converted, golden_image, knob, subformat
):
    """An `ImportVApp` lease reads `streamOptimized` and nothing else, and a
    datastore PUT wants the descriptor-plus-extent pair. Handing either path the
    other's format fails at the far end, mid-upload."""
    if knob is not None:
        vsphere_cfg["target"]["vsphere"]["import"] = knob
    backend.prepare(
        vsphere_cfg, tmp_path, Discovered(vms=(), artifacts={"image": {"create": True}})
    )
    assert converted[0][2] == subformat


def test_the_override_takes_what_the_base_takes(backend):
    """`tests/test_seam.py` asserts the signature on the ABC, and this is the
    only backend that overrides it: an override taking a session would leave
    that check green while reaching the target from the one phase that cannot.
    """
    assert inspect.signature(type(backend).prepare) == inspect.signature(
        Backend.prepare
    )


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
    """The config carries the certificate and `ssl` takes the certificate, so
    the SDK half of this backend needs no file. The datastore uploads are the
    other half and do; the test below is that one."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with api.connect(vsphere_cfg):
        pass
    context = fake_vcenter["sslContext"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert "vcows test CA" in str(context.get_ca_certs())


def test_a_ca_certificate_is_also_written_out_for_the_datastore_uploads(
    vsphere_cfg, fake_vcenter
):
    """`requests` takes a CA bundle by path and nothing else, and the uploads go
    through `requests`. Written once here rather than per upload, and removed on
    the way out: the run directory is the container's, but /tmp outlives it when
    the container is not `--rm`."""
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with api.connect(vsphere_cfg) as session:
        pem = Path(str(session.verify))
        assert pem.read_text() == VSPHERE_CA_CERT
    assert not pem.exists()


def test_the_pem_is_removed_even_when_the_login_fails(vsphere_cfg, monkeypatch):
    """The file is written before `SmartConnect` is called, so the failure path
    is the one that leaks it."""
    import pyVim.connect

    written: list[str] = []
    real = tempfile.NamedTemporaryFile

    def record(*args, **kw):
        handle = real(*args, **kw)
        written.append(handle.name)
        return handle

    monkeypatch.setattr(api.tempfile, "NamedTemporaryFile", record)
    monkeypatch.setattr(
        pyVim.connect,
        "SmartConnect",
        smart_connect({}, error=vim.fault.InvalidLogin(msg="Cannot complete login")),
    )
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with pytest.raises(api.VsphereApiError), api.connect(vsphere_cfg):
        pass
    assert written and not Path(written[0]).exists()


def test_neither_knob_leaves_the_uploads_verifying(vsphere_cfg, fake_vcenter):
    """`True` is what `requests` reads as its own trust store, which is what a
    vCenter with a publicly-trusted certificate needs and all a default is."""
    with api.connect(vsphere_cfg) as session:
        assert session.verify is True


def test_insecure_turns_verification_off_and_outranks_a_ca_certificate(
    vsphere_cfg, fake_vcenter
):
    """`validate` refuses the two together, so this is what the code does with a
    config that got past it: no verification, rather than a certificate that
    reads as one thing and behaves as another. No context is built either."""
    vsphere_cfg["target"]["vsphere"]["insecure"] = True
    vsphere_cfg["target"]["vsphere"]["ca_cert"] = VSPHERE_CA_CERT
    with api.connect(vsphere_cfg) as session:
        # Both halves, or a config could verify over SOAP and not over HTTP.
        assert session.verify is False
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


# -- tasks ---------------------------------------------------------------


def test_a_finished_task_hands_back_what_it_produced(vsphere_cfg):
    """vCenter returns the object a task made through the task itself, and there
    is no second call that would fetch it."""
    assert api.wait(FakeTask(result="a-new-vm"), "clone app01") == "a-new-vm"


def test_the_wait_polls_until_the_task_leaves_running(_no_vsphere_polling_delay):
    """A wait that reads `info.state` once and believes it reports a clone that
    has not happened yet."""
    task = FakeTask(result="a-new-vm", running=3)
    assert api.wait(task, "clone app01") == "a-new-vm"
    assert task.polls > 3


def test_a_task_that_ended_in_error_is_refused_with_the_fault(vsphere_cfg):
    """**A task that stopped is not a task that worked.** Taking `stopped` for
    success is exactly the silent partial teardown `Outcome` exists to
    prevent."""
    task = FakeTask(error=vim.fault.NoPermission(msg="Permission to perform this"))
    with pytest.raises(api.VsphereApiError) as bad:
        api.wait(task, "destroy app01")
    # The whole message: which task, what state it reached, and vCenter's own
    # sentence rather than pyvmomi's field dump of the fault around it.
    assert str(bad.value) == (
        "destroy app01: the task ended as error (Permission to perform this)"
    )


def test_a_task_that_never_finishes_times_out_rather_than_hanging(
    monkeypatch, _no_vsphere_polling_delay
):
    """The ceiling is ours: pyvmomi's own `WaitForTask` blocks until vCenter
    answers or the connection dies, so a wedged task hangs the run.

    The clock is pinned so that reaching the deadline exactly is what the wait
    is asked about: on a real clock the check is a fraction past it either way,
    and the boundary would never be the thing under test. The interval is
    zeroed with it, so a wait that missed the boundary runs into the fake's poll
    ceiling in milliseconds rather than sitting in `time.sleep`.
    """
    monkeypatch.setattr(api, "TASK_TIMEOUT", 0)
    monkeypatch.setattr(api.time, "monotonic", lambda: 1000.0)
    with pytest.raises(api.VsphereApiError, match="had not finished after 0s"):
        api.wait(FakeTask(never_finishes=True), "import golden.qcow2")


def test_the_wait_says_both_numbers_before_it_goes_quiet(caplog):
    """One line before the wait rather than one per poll: what it says is how
    long the silence can legitimately last."""
    with caplog.at_level(logging.DEBUG):
        api.wait(FakeTask(), "clone app01")
    assert (
        f"clone app01: waiting on a task, polling every {api.POLL_INTERVAL}s for "
        f"up to {api.TASK_TIMEOUT}s"
    ) in caplog.text


# -- a login vCenter refuses ---------------------------------------------


def test_a_rejected_credential_is_re_raised_as_our_own_error(vsphere_cfg, monkeypatch):
    """So nothing above this package imports `vim` to catch a fault. The message
    names the endpoint, the user and the config block -- and not the password,
    which pyvmomi's own fault does not carry either."""
    import pyVim.connect

    monkeypatch.setattr(
        pyVim.connect,
        "SmartConnect",
        smart_connect({}, error=vim.fault.InvalidLogin(msg="Cannot complete login")),
    )
    with (
        pytest.raises(api.VsphereApiError, match="rejected the credentials") as bad,
        api.connect(vsphere_cfg),
    ):
        pass
    assert "vcenter.example.com" in str(bad.value)
    assert VSPHERE_CONFIG["target"]["vsphere"]["password"] not in str(bad.value)


def test_any_other_login_fault_is_re_raised_too(vsphere_cfg, monkeypatch):
    """A vCenter that answers with anything else -- a locked account, a service
    that is still starting -- still must not reach `cli.main`'s catch-all as a
    pyvmomi repr."""
    import pyVim.connect

    monkeypatch.setattr(
        pyVim.connect,
        "SmartConnect",
        smart_connect({}, error=vim.fault.NotAuthenticated(msg="Not authenticated")),
    )
    with (
        pytest.raises(api.VsphereApiError, match="Not authenticated"),
        api.connect(vsphere_cfg),
    ):
        pass
