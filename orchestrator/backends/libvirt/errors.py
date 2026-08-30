"""libvirt error codes, as numbers.

**Matched numerically, never by message.** The NVRAM refusal has been reworded
three times upstream, and the rig and the RHEL targets agree on the wording only
until the next Fedora update. A code is ABI.

They are literals rather than ``libvirt.VIR_ERR_*`` attribute lookups so the
functions that match on them stay importable and testable with no libvirt
installed -- the same reason ``destroy``'s undefine flags are literals.
``tests/test_libvirt_errors.py`` pins every one against the installed binding.

This module exists rather than a constant beside each caller because
``preflight`` and ``destroy`` both need ``OPERATION_INVALID`` and ``destroy``
already imports ``preflight``, so the other direction is a cycle. Two copies in
two files is how the two copies eventually disagree.
"""

from __future__ import annotations

ERR_NO_SUPPORT = 3
ERR_INVALID_ARG = 8

#: The *only* code that means "already gone" for a domain. Every other failure of
#: ``lookupByUUIDString`` -- a reset connection, a policy refusal, an internal
#: error -- says nothing about whether that domain still exists, so it must not
#: be read as one that does not.
ERR_NO_DOMAIN = 42

ERR_NO_NETWORK = 43
ERR_NO_STORAGE_POOL = 49
ERR_NO_STORAGE_VOL = 50
ERR_OPERATION_INVALID = 55
