#!/bin/bash
# A4 -- does `vol-upload` silently discard the declared capacity? (findings.md F5)
#
# Run on the hypervisor. Creates and deletes one throwaway volume.
# Expected: capacity drops from 20 GiB to the source image's own virtual size.
set -euo pipefail

POOL=${POOL:-images}
VOL=${VOL:-captest.qcow2}
SRC=${SRC:-/var/lib/libvirt/images/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2}
C="virsh -c qemu:///system"

cleanup() { $C vol-delete --pool "$POOL" "$VOL" 2>/dev/null || true; }
trap cleanup EXIT

$C vol-create-as "$POOL" "$VOL" 20G --format qcow2

echo "### BEFORE UPLOAD (declared 20G) ###"
$C vol-info --pool "$POOL" "$VOL"

$C vol-upload --pool "$POOL" "$VOL" "$SRC"

echo "### AFTER UPLOAD ###"
$C vol-info --pool "$POOL" "$VOL"

echo
echo "Capacity < 20 GiB proves F5: the upload writes from offset 0 including the"
echo "source qcow2 header, so the declared capacity is silently discarded."
echo "Per-VM size must therefore be set on the overlay, not the base volume."
