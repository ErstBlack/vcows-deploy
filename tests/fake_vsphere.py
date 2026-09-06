"""A vCenter-shaped stand-in for pyvmomi's service instance.

This is not the fake *backend* -- that one proves the seam by having no
hypervisor semantics at all. This one has vSphere semantics deliberately, and it
imports ``pyVmomi`` at module scope the way ``tests/fake_libvirt.py`` imports
``libvirt``: the content object is vCenter's own ``ServiceInstanceContent``, so a
test cannot assert against a shape the SDK does not have.

What ``connect`` needs is the whole of it for now -- content, and the stub the
SOAP session cookie hangs off. The chunks that add the lookups, the tasks and
the phases grow it.

Every argument is recorded rather than ignored: a fake whose method drops what it
was handed leaves the caller's wiring unchecked, and the test then proves only
that a call happened. ``smart_connect`` takes keywords only, which is also how
``api.connect`` calls the real one -- a positional call is a ``TypeError`` here
rather than a silently accepted one.
"""

from __future__ import annotations

from typing import Any

from pyVmomi import vim

#: What vCenter's login sets, in the shape pyvmomi keeps on the stub. Arbitrary,
#: and the only thing asserted about it is that `connect` carries it across.
COOKIE = 'vmware_soap_session="52ab-not-a-session"; Path=/; HttpOnly; Secure;'


class _Stub:
    """The half of a service instance the session cookie hangs off."""

    def __init__(self, cookie: str):
        self.cookie = cookie


class FakeServiceInstance:
    """What ``SmartConnect`` returns, for the parts of a run that hold one."""

    def __init__(self, content: Any = None, cookie: str = COOKIE):
        self.content = content if content is not None else vim.ServiceInstanceContent()
        self._stub = _Stub(cookie)
        self.disconnected = False

    def RetrieveContent(self) -> Any:
        return self.content


def smart_connect(recorded: dict[str, Any], si: FakeServiceInstance | None = None):
    """A ``SmartConnect`` stand-in, recording how it was called.

    ``recorded`` is filled in on the call rather than returned, so a test that
    asserts on the connection arguments reads one dict whether the connect
    succeeded or raised.
    """
    instance = si if si is not None else FakeServiceInstance()

    def factory(**kw: Any) -> FakeServiceInstance:
        recorded.update(kw)
        return instance

    return factory


def disconnect(si: FakeServiceInstance) -> None:
    """``Disconnect``'s stand-in. The session is closed exactly once per run."""
    si.disconnected = True
