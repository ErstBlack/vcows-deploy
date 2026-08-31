#!/usr/bin/env bash
# One VM, end to end, against a real libvirtd -- and then **the address it
# actually came up on**.
#
# Everything else in this repository stops short of a running guest.
# `tests/libvirt-module.tftest.hcl` reaches every expression in the module with
# `mock_provider "libvirt" {}`, which is what makes it able to tell a path from a
# name; nothing is dialled, nothing is created, and no byte of the rendered XML is
# ever handed to libvirt. `tofu validate` and `tofu console` read less than that.
# This runs the whole path: the pinned provider against libvirtd,
# `virStorageVolUpload` streaming the 646 MB golden image and the seed ISO into a
# real pool, a third volume created as an overlay backed onto the first,
# `DomainDefineXML` accepting the rendered XML, a guest booting from it, and
# cloud-init consuming the seed ISO `prepare.py` built.
#
# **The last step is the reason this exists.** docs/acceptance.md defect 5 is
# `routes: [{to: default}]` -- a netplan idiom cloud-init does not implement. Both
# guests booted *healthy on the wrong addresses*, .205 and .253 instead of .60 and
# .61, reporting `cloud-init status: done`. No mock provider, no `.tftest.hcl`
# assertion and no `tofu plan` can observe that, because none of them boots
# anything. Asking the host what answers at the configured address is the only
# check that can, which is why the whole cost below is spent to reach one
# assertion.
#
# ## Three things the plan for this gate assumed that are not true
#
# **1. `TF_PROVIDER_LIBVIRT_DOMAIN_TYPE=qemu` does nothing.** The variable is real
# in terraform-provider-libvirt's own CI and it is also dead: measured against the
# v0.9.8 source tree, it appears in `README.md` and `.github/workflows/test.yml`
# and in **no `.go` file at all**. The only `os.Getenv` outside that project's
# test files is `LIBVIRT_DEFAULT_URI`. Every acceptance-test fixture in that
# release writes `type = "kvm"` literally, and they pass on `ubuntu-latest`
# because GitHub's hosted Linux runners expose `/dev/kvm`. So the variable cannot
# swap `kvm` for `qemu`, and TCG is not reachable by setting it.
#
# **2. TCG would require mutating the module under test, twice.** `main.tf` pins
# `type = "kvm"` and `cpu = { mode = "host-passthrough" }`, and libvirt refuses
# host-passthrough on a TCG domain outright. Reaching TCG therefore means editing
# two lines of the thing this gate exists to exercise -- and a gate that runs a
# patched module reports on the patch. `/dev/kvm` is required here instead, and
# the module is copied byte for byte.
#
# **3. GitLab.com's shared runners cannot host this job on any accelerator.**
# libvirt's `default` network is a bridge plus NAT rules, which needs `NET_ADMIN`
# in a container the SaaS Docker executor does not grant, and `libvirtd` is
# started here through systemd, which that executor does not run. TCG would not
# have changed either. The GitLab job is therefore tagged `libvirt`, for a runner
# that can host VMs, in the same spirit as the existing `podman` tag: stating the
# requirement is what stops an untagged job hanging pending forever.
#
# ## The guest, and why it is this one
#
# `Rocky-9-GenericCloud-Base`, 645988352 bytes -- **the same image, to the byte,
# that the acceptance run measured defect 5 on**. Two other candidates were
# rejected on what they carry rather than on taste:
#
# * Debian 13 genericcloud ships `netplan.io` and no `ifupdown`, so cloud-init
#   renders through netplan. netplan accepts `to: default` natively, so the defect
#   would be written straight through and the guest would come up on the *right*
#   address with the bug present. The gate would be green and worthless.
# * Alpine's cloud image is far smaller and far faster, and whether its cloud-init
#   performs the MAC-matched rename this project's seed depends on is not
#   something anyone here has measured.
#
# Rocky 9 renders through NetworkManager/sysconfig, which goes through
# cloud-init's v2-to-v1 normaliser -- `_normalize_net_keys`, the source of
# `ValueError: Address default is not a valid ip address`. The failure is
# reproducible here because it is the same image on the same code path.
#
# ## Proving it has teeth
#
# `VCOWS_SMOKE_INJECT_DEFECT5=1` rewrites the one route literal in the
# network-config `prepare.py` just produced, from `0.0.0.0/0` back to netplan's
# `default`, and changes nothing else. The gate must go **red** with it set, and
# does: run 33355953823 on ubuntu-latest, `smoke01 took a DHCP lease: cloud-init
# did not apply the static address`. This repository's position on a gate that
# cannot fail is already on record (`tests/conftest.py:7`), so this switch is how
# this one answers for itself.
#
# Not wired into `just lint` or `just check`: it needs root, packages, a
# hypervisor and minutes, and `.claude/hooks/static-gate.sh` runs `just lint` on
# every agent turn.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# -- pins --------------------------------------------------------------------
# A dated release rather than `.latest`, and checksummed, for the reason every
# other download in this repository is: an unpinned URL makes two runs of one
# commit test two different guests. Rocky moves point releases to
# vault.rockylinux.org eventually, so this 404s loudly one day rather than
# drifting; the fix is these three lines, from the `.CHECKSUM` published beside
# the image.
GUEST_NAME="Rocky-9-GenericCloud-Base-9.8-20260525.0.x86_64.qcow2"
GUEST_URL="https://dl.rockylinux.org/pub/rocky/9/images/x86_64/$GUEST_NAME"
GUEST_SHA256="92c206cc6f790c61583247eefe87890f8828420662c17cacf247cec78ab4eec8"

# One VM, not two. The second VM in `tests/conftest.py`'s canonical config exists
# to cover the second firmware and MAC branch, and `.tftest.hcl` reads both
# without booting either. Here a second guest would double the slowest thing in
# the repository to re-assert what the offline gate already asserts.
#
# Exported rather than passed, because the config below is read by the embedded
# renderer and thirteen positional arguments would be worse. The only children
# this script has are apt, virsh, curl, tofu and that renderer, and none of them
# reads a variable by any of these names.
export DEPLOYMENT="smoke"
export VM_NAME="smoke01"
export VM_ADDRESS="192.168.122.60"
export POOL="default"
export NETWORK="default"
export BASE_VOLUME="golden.qcow2"

# EFI with libvirt's own firmware autoselection -- `main.tf`'s app01 branch, and
# the default every config that names no `loader` gets. The pinned-loader branch
# was tried first and **cannot currently apply against a raw firmware build**:
#
#   Error: Provider produced inconsistent result after apply
#   .os.nv_ram.template_format: was cty.StringVal("raw"), but now null
#   .os.nv_ram.format:          was cty.StringVal("raw"), but now null
#
# Measured here on run 1, on ubuntu-latest with `/usr/share/OVMF/OVMF_CODE_4M.fd`
# and `loader_format = "raw"`. libvirt treats `raw` as the default for `<nvram>`
# and omits it from the XML it hands back, so the provider's post-apply read
# disagrees with its own plan -- exactly the shape of acceptance.md defect 3, on a
# different attribute, and the apply dies *after* the volumes are written. The
# acceptance run never met it because it ran on Fedora, where the firmware is
# qcow2 and libvirt does echo the format back. `variables.tf` says in as many
# words that **RHEL ships a raw .fd**, and acceptance.md's "Still open" names a
# RHEL 9 or 10 target as untested -- so this is a live production failure on the
# exact platform the tool is being built for, found by this gate before anyone
# took it to a site.
#
# It is a defect to file and fix, not something to route this gate around
# permanently. Autoselection is used here because it is the branch that can
# actually reach a booted guest, which is what this gate exists to observe. When
# the raw-loader bug is fixed, the pinned branch belongs back here.
export LOADER=""
export LOADER_FORMAT=""
export NVRAM_TEMPLATE=""

# What autoselection reads. Without these libvirt has nothing to select from and
# refuses to define an EFI domain -- a failure worth naming, since it looks like
# an XML problem rather than a missing package.
FIRMWARE_DESCRIPTORS="/usr/share/qemu/firmware"

# Local socket, which is the one thing here that is not what a site runs. The
# config schema refuses anything but `qemu+ssh://<host>/system`
# (`orchestrator/backends/libvirt/schema.py:353`), so reaching a local libvirtd
# through the full CLI would mean running sshd on the runner and dialling
# localhost -- a second daemon, host keys and an agent, all of it standing in
# front of the thing being tested. The transport is what the rig covers
# (`tests/test_libvirt_rig.py`); what this gate is for is on the far side of it.
export URI="qemu:///system"
VIRSH=(virsh --connect "$URI")

# **Measured: 18 s**, on ubuntu-latest, from `apply` returning to the guest
# answering at its configured address -- Rocky 9 on q35 with KVM, autoselected
# OVMF and 2 vCPUs. 180 s is a tenfold margin, which is the right shape for a
# bound whose job is to separate "never" from "slow" rather than to police a
# regression. It is also short enough that an injected defect reports in three
# minutes instead of at the job ceiling. Overrideable the way `schema.py`'s
# ceilings are, so a slower runner raises it from the outside rather than by
# editing this file.
BOOT_DEADLINE="${VCOWS_SMOKE_BOOT_DEADLINE:-180}"

# `orchestrator/tofu.py` gives `apply` no timeout by design: at a site it streams
# a multi-GB golden image through an SSH tunnel with no resume, and a clock there
# kills a live upload. Neither half is true here -- the upload is a local copy into
# a pool on the same disk, measured at 1 s for the whole 646 MB, with the entire
# four-resource apply at 5.3 s -- so an apply still running after five minutes is
# wedged, and saying so beats holding the runner to the job ceiling.
APPLY_TIMEOUT=300

CACHE="$REPO/.cache/smoke"
WORKDIR="$CACHE/run"
BOOT_SECONDS=0

# -- the host ----------------------------------------------------------------

sudo_prefix() { [ "$(id -u)" -eq 0 ] || printf 'sudo'; }

host_packages() {
    local sudo
    sudo="$(sudo_prefix)"
    have apt-get || die "this gate is written for the ubuntu-latest runner and needs apt-get"
    log "installing qemu and libvirt"
    $sudo apt-get update -qq
    # `ovmf` and `iputils-ping` are the two additions to
    # terraform-provider-libvirt's own recipe. Neither is optional: `main.tf`
    # emits `firmware = "efi"` for every domain and libvirt refuses to define one
    # whose loader is absent, and the address assertion is a ping.
    $sudo apt-get install -y -qq \
        qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
        ovmf iputils-ping
}

configure_libvirtd() {
    local sudo conf=/etc/libvirt/qemu.conf
    sudo="$(sudo_prefix)"

    # Copied from terraform-provider-libvirt's acceptance-test setup, which is
    # known to work unmodified on this runner image. Each line is there because a
    # hosted runner's qemu uid cannot read a volume file libvirt has just created:
    # root/root plus `dynamic_ownership = 0` stops libvirt relabelling, and
    # `security_driver = "none"` takes AppArmor out of the path, so a domain is
    # refused for what its XML says rather than for where its disks live.
    $sudo sed -i 's/^#\?user = .*/user = "root"/' "$conf"
    $sudo sed -i 's/^#\?group = .*/group = "root"/' "$conf"
    $sudo sed -i 's/^#\?dynamic_ownership = .*/dynamic_ownership = 0/' "$conf"
    $sudo grep -q '^security_driver' "$conf" \
        || printf 'security_driver = "none"\n' | $sudo tee -a "$conf" >/dev/null
    $sudo systemctl restart libvirtd

    # `usermod -a -G libvirt` is not enough on its own and is left out for that
    # reason: a group added to the runner user does not reach the shell already
    # running. The socket mode is what actually lets this script and the provider
    # -- both unprivileged -- reach the daemon in this job.
    $sudo chmod 666 /var/run/libvirt/libvirt-sock

    $sudo mkdir -p /var/lib/libvirt/images
    "${VIRSH[@]}" pool-info "$POOL" >/dev/null 2>&1 \
        || "${VIRSH[@]}" pool-define-as --name "$POOL" --type dir \
            --target /var/lib/libvirt/images
    "${VIRSH[@]}" pool-start "$POOL" >/dev/null 2>&1 || true
    "${VIRSH[@]}" pool-autostart "$POOL" >/dev/null 2>&1 || true

    # The NAT network is not incidental, it is the instrument. Its bridge is where
    # the host sees the guest's ARP, and its lease table is the second and
    # independent witness that cloud-init did not fall back to DHCP.
    "${VIRSH[@]}" net-start "$NETWORK" >/dev/null 2>&1 || true
    "${VIRSH[@]}" net-autostart "$NETWORK" >/dev/null 2>&1 || true
    "${VIRSH[@]}" net-info "$NETWORK" >/dev/null \
        || die "libvirt network '$NETWORK' is absent; the gate has no bridge to watch"
}

fetch_guest() {
    local image="$CACHE/$GUEST_NAME"
    mkdir -p "$CACHE"
    if [ -f "$image" ] && printf '%s  %s\n' "$GUEST_SHA256" "$image" | sha256sum -c --status -; then
        log "guest image cached and verified"
        return
    fi
    log "downloading $GUEST_NAME (646 MB)"
    curl -fsSL --retry 3 --retry-delay 5 -o "$image.part" "$GUEST_URL"
    mv "$image.part" "$image"
    printf '%s  %s\n' "$GUEST_SHA256" "$image" | sha256sum -c --status - \
        || die "$GUEST_NAME does not match its recorded sha256"
}

# -- the module --------------------------------------------------------------

build_workdir() {
    local lock
    lock="$REPO/docs/provider-$(provider_version).lock.hcl"
    [ -f "$lock" ] || die "no committed lock at $lock"
    [ -d "$MIRROR" ] || die "no provider mirror at $MIRROR -- run 'just ensure-mirror'"

    rm -rf "$WORKDIR"
    mkdir -p "$WORKDIR"
    # Copied, never edited. That is the claim this whole gate rests on: whatever
    # the offline tests say about `main.tf`, this ran the file itself.
    cp "$MODULE"/*.tf "$WORKDIR/"
    cp "$lock" "$WORKDIR/.terraform.lock.hcl"

    # The shipped CLI config with one path substituted, exactly as
    # `tests/conftest.py:tofu_env` does it -- so this resolves the provider the
    # way the image does, from a filesystem mirror with no `direct` block, and a
    # missing mirror fails here instead of quietly reaching the registry.
    sed "s#/opt/tofu-mirror#$MIRROR#" "$REPO/container/tofurc" > "$WORKDIR/tofurc"
    export TF_CLI_CONFIG_FILE="$WORKDIR/tofurc"
    export CHECKPOINT_DISABLE=1
    export TF_IN_AUTOMATION=1
}

# The tfvars come out of the production renderer, not out of this script, and that
# is the point: `prepare.seed_files` writes the network-config whose one wrong
# route literal cost the first acceptance run, and `render.render` decides every
# value the module is handed. A tfvars document assembled here would test this
# script's idea of the contract instead of the code's. Prints the derived MAC.
render_tfvars() {
    "$PY" - "$REPO" "$WORKDIR" "$CACHE/$GUEST_NAME" <<'PY'
import json
import os
import pathlib
import sys

sys.path.insert(0, sys.argv[1])

from orchestrator.backends.base import Prepared
from orchestrator.backends.libvirt import prepare, render

workdir = pathlib.Path(sys.argv[2])
cfg = {
    "schema_version": 1,
    "deployment": os.environ["DEPLOYMENT"],
    "backend": "libvirt",
    # Only this URI's shape is used, and only so that render() can be handed what
    # it requires: the schema refuses a local socket, so the one field that cannot
    # be honest on a runner is substituted after render rather than faked before.
    "target": {
        "libvirt": {"uri": "qemu+ssh://vcows@vcows/system", "pool": os.environ["POOL"]}
    },
    "image": {
        "source_qcow2": sys.argv[3],
        "base_volume_name": os.environ["BASE_VOLUME"],
    },
    "vms": [
        {
            "name": os.environ["VM_NAME"],
            "vcpus": 2,
            "memory_mib": 2048,
            # The image's virtual size is 10 GiB, so 12 makes the overlay's own
            # capacity and growpart do something -- which is what A3 measured.
            "disk_gb": 12,
            "firmware": "efi",
            # Empty means "not set", which is what selects libvirt's own firmware
            # autoselection in main.tf. Passed through the environment rather than
            # conditionally omitted so the shell above stays the single place the
            # firmware decision is written down, with its reason beside it.
            **{
                key: os.environ[key.upper()]
                for key in ("loader", "loader_format", "nvram_template")
                if os.environ[key.upper()]
            },
            "machine": "q35",
            "nics": [
                {
                    "network": os.environ["NETWORK"],
                    "ip_cidr": os.environ["VM_ADDRESS"] + "/24",
                    "gateway": "192.168.122.1",
                    "nameservers": ["192.168.122.1"],
                }
            ],
        }
    ],
}

seeds = {}
for vm in cfg["vms"]:
    files = prepare.seed_files(vm, cfg)
    if os.environ.get("VCOWS_SMOKE_INJECT_DEFECT5"):
        # acceptance.md defect 5, put back exactly: one literal, in the document
        # the production code just produced. cloud-init reads it, logs "Applying
        # network configuration from ds", throws out of _normalize_net_keys, and
        # applies nothing -- so the guest boots healthy on a DHCP address.
        files["network-config"] = files["network-config"].replace(
            b"0.0.0.0/0", b"default"
        )
        print("INJECTED acceptance.md defect 5: this run must fail", file=sys.stderr)
    seeds[vm["name"]] = str(
        prepare.build_seed_iso(files, workdir / f"{vm['name']}-seed.iso")
    )

prepared = Prepared(
    workdir=workdir,
    artifacts={
        "seed_isos": seeds,
        # What preflight discovers on a real run. `create` is true because the
        # runner's pool is empty, which is also what makes this exercise
        # virStorageVolUpload rather than skip it.
        "base_volume": {
            "name": os.environ["BASE_VOLUME"],
            "create": True,
            "path": "",
        },
    },
)

tfvars = render.render(cfg, prepared)
tfvars["uri"] = os.environ["URI"]
(workdir / "main.auto.tfvars.json").write_text(json.dumps(tfvars, indent=2))
print(tfvars["vms"][os.environ["VM_NAME"]]["nics"][0]["mac"])
PY
}

# -- the assertions ----------------------------------------------------------

wait_for_address() {
    local start elapsed
    start=$SECONDS
    log "waiting up to ${BOOT_DEADLINE}s for $VM_NAME to answer at $VM_ADDRESS"
    while :; do
        elapsed=$((SECONDS - start))
        if ping -c 1 -W 1 "$VM_ADDRESS" >/dev/null 2>&1; then
            BOOT_SECONDS=$elapsed
            log "measured: $VM_ADDRESS answered ${elapsed}s after apply returned"
            return 0
        fi
        [ "$elapsed" -lt "$BOOT_DEADLINE" ] || return 1
        sleep 5
    done
}

# The address answering is not by itself proof that *our* guest holds it. This is:
# the host's neighbour entry for that address carries the MAC render.py derived,
# which is the same MAC the seed's network-config matched on.
assert_owner() {
    local bridge="$1" mac="$2" lladdr=""
    # `|| true` on the assignment, not inside it: `head -1` can close the pipe
    # under the `pipefail` lib.sh sets, and an empty result has to reach the
    # message below rather than exiting the script with no explanation.
    lladdr="$(ip neigh show "$VM_ADDRESS" dev "$bridge" 2>/dev/null | sed -n 's/.*lladdr \([0-9a-f:]*\).*/\1/p' | head -1)" || true
    [ -n "$lladdr" ] \
        || die "$VM_ADDRESS answered but the host has no neighbour entry for it on $bridge"
    [ "$lladdr" = "$mac" ] \
        || die "$VM_ADDRESS is held by $lladdr, not by $VM_NAME's $mac"
    log "ok: $VM_ADDRESS is held by $mac, the MAC the seed's network-config matched on"
}

# The second witness, and **the only one that fired**. Under defect 5 cloud-init
# applies nothing and falls back to DHCP, so a lease for our MAC means the static
# configuration did not take. That is a different fact from "the address answers",
# and the difference is not theoretical: with the defect injected, libvirt's own
# dnsmasq leased the guest 192.168.122.60 -- the address the config asked for.
# `wait_for_address` passed in 24 s and `assert_owner` passed on the right MAC. A
# gate that checked only whether the configured address answers would have been
# green with acceptance.md defect 5 present. This check is what made it red.
assert_no_dhcp_lease() {
    local mac="$1"
    if "${VIRSH[@]}" net-dhcp-leases "$NETWORK" | grep -qi -- "$mac"; then
        "${VIRSH[@]}" net-dhcp-leases "$NETWORK" >&2
        die "$VM_NAME took a DHCP lease: cloud-init did not apply the static address (acceptance.md defect 5)"
    fi
    log "ok: no DHCP lease for $mac -- the address is configured, not leased"
}

assert_marker() {
    "${VIRSH[@]}" dumpxml "$VM_NAME" | grep -q 'urn:vcows:1' \
        || die "the defined domain carries no vcows marker: destroy could never find it"
    log "ok: the domain libvirt actually defined carries the marker"
}

# What a red run needs in front of whoever reads the log. The lease table is the
# first thing to look at: an entry for our MAC at another address is defect 5's
# exact signature, and is the difference between "the gate found the bug" and
# "the guest never booted".
diagnose() {
    log "--- diagnostics ---"
    local sudo
    sudo="$(sudo_prefix)"
    "${VIRSH[@]}" list --all 2>/dev/null >&2 || true
    "${VIRSH[@]}" net-dhcp-leases "$NETWORK" 2>/dev/null >&2 || true
    ip neigh show 2>/dev/null >&2 || true
    "${VIRSH[@]}" dumpxml "$VM_NAME" 2>/dev/null >&2 || true
    $sudo tail -n 60 "/var/log/libvirt/qemu/$VM_NAME.log" 2>/dev/null >&2 || true
}

# -- teardown ----------------------------------------------------------------

# Runs on every exit path, the deadline expiring mid-boot included. `tofu destroy`
# first, because tearing down is part of what the job proves; virsh after it,
# because a destroy that failed halfway is exactly when a runner is left holding a
# domain. The base volume goes too: a persistent runner would otherwise fail the
# next run's `create = true`.
teardown() {
    log "teardown"
    if [ -f "$WORKDIR/terraform.tfstate" ]; then
        tofu -chdir="$WORKDIR" destroy -auto-approve -input=false -no-color \
            || log "warning: tofu destroy failed; falling back to virsh"
    fi
    if "${VIRSH[@]}" dominfo "$VM_NAME" >/dev/null 2>&1; then
        log "warning: $VM_NAME survived tofu destroy; removing it by hand"
        "${VIRSH[@]}" destroy "$VM_NAME" >/dev/null 2>&1 || true
        "${VIRSH[@]}" undefine --nvram "$VM_NAME" >/dev/null 2>&1 || true
    fi
    local volume
    for volume in "$VM_NAME.qcow2" "$VM_NAME-seed.iso" "$BASE_VOLUME"; do
        "${VIRSH[@]}" vol-delete --pool "$POOL" "$volume" >/dev/null 2>&1 || true
    done
}

on_exit() {
    local rc=$?
    trap - EXIT
    [ "$rc" -eq 0 ] || diagnose
    teardown
    exit "$rc"
}

# -- the run -----------------------------------------------------------------

main() {
    local bridge mac total
    total=$SECONDS
    need curl tofu
    need_venv
    # Stated as a requirement rather than worked around. See the header: the
    # variable that was supposed to make TCG possible is not read by the pinned
    # provider, and reaching TCG anyway would mean editing the two lines of
    # `main.tf` this gate exists to run unmodified.
    [ -c /dev/kvm ] \
        || die "no /dev/kvm: this gate runs the module's own type='kvm' and cpu mode='host-passthrough' unmodified"

    host_packages
    configure_libvirtd
    [ -d "$FIRMWARE_DESCRIPTORS" ] \
        || die "no firmware descriptors at $FIRMWARE_DESCRIPTORS: libvirt has nothing to autoselect and will refuse every EFI domain"
    fetch_guest
    build_workdir

    mac="$(render_tfvars | tail -1)"
    [ -n "$mac" ] || die "the renderer produced no MAC"

    trap on_exit EXIT
    log "tofu init"
    tofu -chdir="$WORKDIR" init -input=false -no-color >/dev/null
    log "tofu apply"
    timeout "$APPLY_TIMEOUT" tofu -chdir="$WORKDIR" apply \
        -auto-approve -input=false -no-color \
        || die "apply failed or exceeded ${APPLY_TIMEOUT}s"

    bridge="$("${VIRSH[@]}" net-info "$NETWORK" | sed -n 's/^Bridge: *//p')" || true
    [ -n "$bridge" ] || die "libvirt network '$NETWORK' names no bridge"
    assert_marker

    wait_for_address \
        || die "$VM_NAME never answered at $VM_ADDRESS within ${BOOT_DEADLINE}s -- the lease table below names the address it did come up on"
    assert_owner "$bridge" "$mac"
    assert_no_dhcp_lease "$mac"

    log "measured: boot to address ${BOOT_SECONDS}s, whole gate $((SECONDS - total))s"
    log "smoke gate passed"
}

main "$@"
