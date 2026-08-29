"""The codes are literals. That only stays safe if something pins them to the ABI.

The same argument as ``test_flag_values_match_the_installed_binding``: writing
them out is what keeps the matching functions importable with no libvirt, and a
literal nobody checks is a literal that can be wrong for a release.
"""

from __future__ import annotations

import pytest

from orchestrator.backends.libvirt import errors as e


def test_error_codes_match_the_installed_binding():
    libvirt = pytest.importorskip("libvirt")
    assert e.ERR_NO_SUPPORT == libvirt.VIR_ERR_NO_SUPPORT
    assert e.ERR_INVALID_ARG == libvirt.VIR_ERR_INVALID_ARG
    assert e.ERR_NO_DOMAIN == libvirt.VIR_ERR_NO_DOMAIN
    assert e.ERR_NO_NETWORK == libvirt.VIR_ERR_NO_NETWORK
    assert e.ERR_NO_STORAGE_POOL == libvirt.VIR_ERR_NO_STORAGE_POOL
    assert e.ERR_NO_STORAGE_VOL == libvirt.VIR_ERR_NO_STORAGE_VOL
    assert e.ERR_OPERATION_INVALID == libvirt.VIR_ERR_OPERATION_INVALID
