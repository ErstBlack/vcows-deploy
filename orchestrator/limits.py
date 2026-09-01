"""Sanity ceilings on VM size, and the environment overrides for them.

**Not a supported-configuration claim.** They exist to catch a fat-fingered zero
before a run creates disks for a VM no host can start; the hypervisor stays the
authority on what it will actually serve. Each is overrideable, and raising one
is always safe.

Core rather than per-backend because the override contract is one contract: an
operator setting ``VCOWS_MAX_VCPUS`` means it for whatever they are deploying to,
and two backends each reading the same variable into their own constant is a way
for the two to disagree.
"""

from __future__ import annotations

import logging
import os

#: ``_ceiling`` is the only thing in this module that writes.
log = logging.getLogger(__name__)


def _ceiling(name: str, default: int) -> int:
    """One size ceiling, overrideable from the environment.

    Same shape as ``cli.MANIFEST``: a constant with an environment override, so a
    site on hardware we have not seen raises the bound from the outside rather
    than editing a file inside the image. A value that will not parse, or is not
    positive, is reported and ignored -- taking it silently is the failure mode
    the reporting work existed to remove.

    This runs at **import**, because the three constants it produces are consumed
    as literals inside each backend's ``VM_SCHEMA``. That is why ``orchestrator``
    configures logging in its own ``__init__`` rather than in ``cli.main`` -- a
    logger configured in ``main`` would not exist yet here, and this warning
    would fall through to ``logging.lastResort`` unprefixed.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        log.warning(
            "ignoring %s=%r: not a positive integer. Using %s.", name, raw, default
        )
        return default
    return value


#: Overrideable, and raising one is always safe. The reason they exist at all is
#: in this module's docstring.
MAX_VCPUS = _ceiling("VCOWS_MAX_VCPUS", 512)
MAX_MEMORY_MIB = _ceiling("VCOWS_MAX_MEMORY_MIB", 4 * 1024 * 1024)
MAX_DISK_GB = _ceiling("VCOWS_MAX_DISK_GB", 64 * 1024)
