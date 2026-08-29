#!/bin/bash
# A3 -- does the golden image carry what the overlay design depends on?
#
# Run on the hypervisor. Strictly read-only; the image is never booted.
# Re-run this against the REAL golden qcow2 -- D3 records the stand-in result as
# unverified against the shipping artifact.
set -euo pipefail

IMG=${1:-/var/lib/libvirt/images/Rocky-9-GenericCloud-Base.latest.x86_64.qcow2}
export LIBGUESTFS_BACKEND=${LIBGUESTFS_BACKEND:-direct}

echo "### image: $IMG"
guestfish --ro -a "$IMG" run : list-filesystems

# Root is assumed to be the last xfs partition; override with ROOTDEV.
ROOTDEV=${ROOTDEV:-/dev/sda4}

echo
echo "### binaries the growpart path needs ###"
guestfish --ro -a "$IMG" <<EOF
run
mount $ROOTDEV /
is-file /usr/bin/cloud-init
is-file /usr/bin/growpart
is-file /usr/sbin/sgdisk
is-file /usr/sbin/sfdisk
is-file /usr/sbin/xfs_growfs
is-file /usr/bin/qemu-ga
EOF

echo
echo "### cloud_init_modules + datasource_list (read the module list yourself) ###"
echo "### 'growpart' MUST appear, and MUST precede 'resizefs'                 ###"
guestfish --ro -a "$IMG" run : mount $ROOTDEV / : cat /etc/cloud/cloud.cfg

echo
echo "### drop-ins that could override the above ###"
guestfish --ro -a "$IMG" run : mount $ROOTDEV / : ls /etc/cloud/cloud.cfg.d
