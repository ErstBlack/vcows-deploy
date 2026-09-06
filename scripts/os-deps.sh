#!/usr/bin/env bash
# The OS packages the test suite needs, for a CI runner or a fresh box.
#
# **`python3-libvirt` is not optional and not a rig-only dependency.**
# tests/fake_libvirt.py imports libvirt at module scope, to build genuine
# libvirt.libvirtError instances, so most of the default suite needs the binding
# present -- not just the gated rig tests. PyPI ships sdist only, which is why
# this is a distro package and why the venv is created with
# --system-site-packages.
#
# `xorriso` is likewise ungated: tests/test_seed_iso.py shells out to it to read
# back what pycdlib wrote, on the principle that a builder verified only by
# itself is not verified.
#
# `qemu-img` is Debian's `qemu-utils` and everyone else's `qemu-img`. The vSphere
# backend converts the golden image to VMDK with it, so a runner without it
# cannot run that conversion or the smoke gate over it.
#
# `shellcheck` is here rather than in install-tools.sh because it is a distro
# package on every platform this runs on, and it is a lint gate rather than a
# build input -- there is nothing about it that needs pinning to a digest.
#
# Kept in a script rather than inline in a workflow so both pipelines call the
# same thing and `.github/` holds no logic of its own.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

main() {
    local sudo=""
    [ "$(id -u || true)" -eq 0 ] || sudo=sudo
    if have apt-get; then
        $sudo apt-get update -qq
        $sudo apt-get install -y -qq python3-libvirt xorriso shellcheck jq curl unzip git qemu-utils
    elif have dnf; then
        $sudo dnf install -y -q python3-libvirt xorriso ShellCheck jq curl unzip git qemu-img
    else
        die "no apt-get or dnf -- install python3-libvirt, xorriso, shellcheck, jq and qemu-img by hand"
    fi
    log "os dependencies present"
}

main "$@"
