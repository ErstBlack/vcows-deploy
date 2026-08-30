"""vcows-deploy -- deploy pre-built golden qcow2 images as VMs to KVM/libvirt.

The version is four-digit Major.Minor.Patch.Hotfix and this is its only
definition. Seven things consume it. Each is asserted against this constant, but
not all by the default suite -- two sit behind the image gate and are checked
only when ``VCOWS_IMAGE`` names a built image:

  * ``--version`` output -- ``test_cli.test_version_prints_the_single_definition``
  * the ownership marker's ``v`` -- ``test_marker.test_round_trip``
  * ``pyproject.toml``'s ``project.version`` -- ``test_version.test_pyproject_agrees``
  * ``run.json``'s ``vcows`` -- ``test_cli.test_deploy_runs_the_whole_pipeline``
  * ``ARG VCOWS_VERSION``, which names the image --
    ``test_version.test_the_image_tag_agrees``
  * the ``org.opencontainers.image.version`` label -- **image gate**,
    ``test_image.test_the_labels_are_ours_and_not_the_bases``
  * the build manifest copied into every run directory -- **image gate**,
    ``test_image.test_the_build_manifest_records_what_shipped``

What no test asserts is the tag a build was actually invoked with: ``podman build
-t`` takes it from the operator, and only ``ARG VCOWS_VERSION`` inside the file is
checked. A bump landed here and in the Containerfile can still ship under last
release's tag.
"""

VERSION = "0.1.0.0"

__all__ = ["VERSION"]
