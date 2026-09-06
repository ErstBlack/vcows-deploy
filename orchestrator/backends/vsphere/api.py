"""The only module in this package that reaches vCenter.

Kept to one file for the reason ``tests/test_seam.py`` exists for the other two
backends: once the registry names ``VsphereBackend``, importing the registry
drags this package in on every run, including runs on a machine that will never
talk to a vCenter. Nothing here is imported at module scope; ``connect`` imports
``pyVim.connect`` inside its own body, exactly as the Proxmox backend imports
``proxmoxer`` and the libvirt backend imports ``libvirt`` inside the functions
that hold a connection.

The lookups, the task wait and the phases themselves land in the chunks that
write them; this file is ``connect`` and the record it yields.
"""

from __future__ import annotations

import logging
import ssl
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

#: vCenter's HTTPS port unless the endpoint names another one.
DEFAULT_PORT = 443


@dataclass(frozen=True)
class Session:
    """pyvmomi's service instance, and what the rest of a run needs off it.

    Opaque to core, which passes it from ``preflight`` to ``destroy`` without
    reading it -- the same contract libvirt's ``virConnect`` has.
    """

    si: Any
    content: Any
    """What ``RetrieveContent()`` returned: the root folder and every manager."""

    cookie: str
    """The SOAP session cookie.

    Held because the datastore uploads are plain HTTP against vCenter's
    ``/folder`` endpoint rather than SDK calls, and that cookie is the only thing
    that authorises them. Taken off the stub here so nothing later has to reach
    into pyvmomi's internals a second time.
    """


@contextmanager
def connect(cfg: dict):
    """Open a vCenter session against the configured endpoint.

    **The credential is read here and nowhere else.** ``target.vsphere`` carries
    a user and a password; neither is returned and only the user is logged,
    because that is what an operator debugging a failed login needs and it is not
    the credential.

    TLS follows the Proxmox backend: ``insecure`` outranks ``ca_cert``, so a
    config that got past ``validate`` with both gets no verification rather than
    a certificate that reads as one thing and behaves as another. ``ca_cert``
    becomes an ``ssl`` context directly -- ``ssl`` takes the certificate itself,
    so unlike the Proxmox backend nothing is written to a file.
    """
    from pyVim.connect import Disconnect, SmartConnect

    target = cfg["target"]["vsphere"]
    parts = urlsplit(target["endpoint"])
    # `_check_target` has already refused anything with credentials, a query or a
    # path, so what reaches here is a bare origin.
    host = parts.hostname
    tls: dict[str, Any] = {}
    if target.get("insecure"):
        tls["disableSslCertValidation"] = True
    elif target.get("ca_cert") is not None:
        tls["sslContext"] = ssl.create_default_context(cadata=target["ca_cert"])

    log.info("connecting to %s as %s", host, target["user"])
    si = SmartConnect(
        host=host,
        port=parts.port or DEFAULT_PORT,
        user=target["user"],
        pwd=target["password"],
        **tls,
    )
    try:
        yield Session(si=si, content=si.RetrieveContent(), cookie=si._stub.cookie)
    finally:
        # vCenter keeps an idle session for half an hour, and a run that raised
        # is exactly the one an operator retries at once.
        Disconnect(si)
