"""vcows-deploy -- deploy pre-built golden qcow2 images as VMs to KVM/libvirt.

The version is four-digit Major.Minor.Patch.Hotfix and this is its only
definition. Five things consume it, and ``tests/test_version.py`` asserts they
agree so they cannot drift:

  * ``--version`` output
  * the ownership marker's ``v`` field
  * the container image tag
  * the ``org.opencontainers.image.version`` label
  * the build manifest copied into every run directory
"""

VERSION = "0.1.0.0"

__all__ = ["VERSION"]
