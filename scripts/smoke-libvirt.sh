#!/usr/bin/env bash
# The end-to-end smoke gate: the shipped OpenTofu module applied against a real
# libvirtd, on an unmodified hosted runner.
#
# Everything else that reads this module reads a substitute. `tofu validate` and
# `tofu console` never evaluate an attribute value; `libvirt-module.tftest.hcl`
# evaluates them against `mock_provider "libvirt" {}`, which generates values
# rather than sending XML anywhere. So the three things the mock stands in for
# have never run in CI at all:
#
#   * `virStorageVolUpload` -- the provider streaming a local file into a pool
#   * libvirtd parsing and accepting the domain XML the module renders
#   * define, start and undefine of that domain
#
# This runs all three, and asserts against what libvirtd actually created --
# `virsh dumpxml`, `virsh vol-dumpxml`, `qemu-img info` -- rather than against
# what tofu planned. A plan that agrees with itself is what the mock already
# proves.
#
# **No guest is booted and no guest address is observed.** The domain reaches
# firmware and stops there; nothing here needs it to reach a login prompt. The
# defect class `docs/acceptance.md` records -- guests healthy on the wrong
# addresses -- is not what this gate covers.
#
# ## No /dev/kvm
#
# The domain runs under TCG, so this behaves the same on a GitHub-hosted runner
# and on a GitLab.com SaaS runner. GitLab is the destination, and its shared
# runners have no `/dev/kvm`.
#
# Getting there needs a two-attribute override on a *copy* of the module, and the
# reason is worth stating because the obvious mechanism does not exist here.
# `docs/tooling-2026-08-30.md` §4.1 credits `TF_PROVIDER_LIBVIRT_DOMAIN_TYPE=qemu`
# with swapping `type='kvm'` for `type='qemu'`. Measured against the mirrored
# provider: `strings` over `terraform-provider-libvirt_v0.9.8` contains no
# `TF_..._DOMAIN_TYPE` string at all, and it would not matter if it did --
# `main.tf` sets `type = "kvm"` explicitly, and an env-var default cannot beat a
# declared attribute. The override is the honest form of the same swap.
#
# `cpu` goes with it. `main.tf` pins `host-passthrough`, which QEMU renders as
# `-cpu host` -- a model registered only for KVM and HVF. Under TCG the domain
# defines and then fails to start. Setting it null leaves libvirt to pick its own
# default, which is the only part of this the runner can supply.
#
# The shipped tree is never edited: the module is copied into a temp directory
# and `smoke_override.tf` is written beside the copy. `tofu fmt`, `just test-tofu`
# and the image build all keep reading the real module.
#
# ## What this is not
#
# Not the rig gate. `tests/test_libvirt_rig.py` and `VCOWS_RIG_URI` stay exactly
# as they are: a named skip against real hardware with a real pool and a real
# golden image. This gate creates its own 64 MiB throwaway qcow2 and asserts
# nothing about a guest.
#
# Not part of `just lint` or `just check`. It installs packages and starts a
# system daemon; it is a CI job of its own.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

URI="qemu:///system"
POOL="default"
POOL_DIR="/var/lib/libvirt/images"
# Hardcoded in main.tf's nv_ram path and nowhere configurable, so it is a
# constant here too rather than something read back from the domain.
NVRAM_DIR="/var/lib/libvirt/qemu/nvram"
NETWORK="default"

# One prefix on everything, so the cleanup below can name what it removes and a
# half-applied run leaves nothing that looks like somebody else's.
DOMAIN="vcows-smoke01"
BASE_VOL="vcows-smoke-golden.qcow2"
OVERLAY_VOL="vcows-smoke01.qcow2"
SEED_VOL="vcows-smoke01-seed.iso"
MARKER_ID="9f2b8d40-5c1e-5a3f-9a77-1c2d3e4f5a6b"
MAC="52:54:00:be:a8:60"

# The firmware pin, in one place: the tfvars below is substituted from these and
# the assertions read them back, so the two cannot drift apart. Ubuntu's `ovmf`
# ships raw .fd builds and all four of its /usr/share/qemu/firmware descriptors
# declare "raw" -- measured, CI run 33374623746.
LOADER="/usr/share/OVMF/OVMF_CODE_4M.fd"
NVRAM_TEMPLATE="/usr/share/OVMF/OVMF_VARS_4M.fd"

WORK=""

# Accumulate and report every assertion, rather than dying on the first --
# scripts/lint.sh's argument, for the same reason: one verdict per thing checked
# is what a reader of a failed CI log wants.
fail=0
check() {
    local what="$1"; shift
    # stderr, where `log` and `die` already write. On stdout these interleaved
    # with the final `die` and the log showed the verdict above the last two
    # results it was a verdict on.
    if "$@" >/dev/null 2>&1; then
        printf '  ok    %s\n' "$what" >&2
    else
        printf '  FAIL  %s\n' "$what" >&2
        fail=1
    fi
}

contains() { grep -qF -- "$2" <<<"$1"; }
absent()   { ! grep -qF -- "$2" <<<"$1"; }

vsh() { virsh -c "$URI" "$@"; }

# `tofu destroy` is the thing under test, so it runs in `main` where its exit
# status is read. This is the second line of defence: an apply that failed
# halfway leaves objects the state may not record, and a runner left with a
# defined domain fails every later run for a reason that has nothing to do with
# the change under review.
cleanup() {
    local status=$? vol
    set +e
    trap - EXIT
    if [ -n "$WORK" ] && [ -f "$WORK/terraform.tfstate" ]; then
        tofu -chdir="$WORK" destroy -auto-approve -input=false >/dev/null 2>&1
    fi
    vsh destroy "$DOMAIN" >/dev/null 2>&1
    vsh undefine --nvram "$DOMAIN" >/dev/null 2>&1
    for vol in "$BASE_VOL" "$OVERLAY_VOL" "$SEED_VOL"; do
        vsh vol-delete --pool "$POOL" "$vol" >/dev/null 2>&1
    done
    [ -n "$WORK" ] && rm -rf "$WORK"
    exit "$status"
}

# -- the module copy --------------------------------------------------------

# Deliberately first, before a single package is installed: a typo in the tfvars
# below, or an override that no longer matches a resource in main.tf, is a
# three-second failure, and finding it after ninety seconds of apt is ninety
# seconds wasted on every run that has one.
prepare() {
    local version lock
    version="$(provider_version)"
    lock="$REPO/docs/provider-${version}.lock.hcl"
    [ -f "$lock" ] || die "no committed lock at $lock"
    [ -d "$MIRROR" ] || die "no provider mirror -- run 'just ensure-mirror' first"

    WORK="$(mktemp -d)"
    cp "$MODULE"/*.tf "$WORK/"
    # The committed lock, so init cannot quietly select a different build --
    # the same reason tests/test_tofu_module.py copies it.
    cp "$lock" "$WORK/.terraform.lock.hcl"

    # The shipped CLI config with one path substituted, not a second config.
    # tests/conftest.py has the argument: a test-only tofurc grows a `direct`
    # block and stops exercising the air-gap behaviour the image ships.
    sed "s|/opt/tofu-mirror|$MIRROR|" "$REPO/container/tofurc" > "$WORK/tofurc"
    export TF_CLI_CONFIG_FILE="$WORK/tofurc"
    export CHECKPOINT_DISABLE=1
    export NO_COLOR=1

    cat > "$WORK/smoke_override.tf" <<'HCL'
// Written by scripts/smoke-libvirt.sh into a copy of the module. The shipped
// tree is not edited. See that script's header for why each of these two is
// here; the short version is that a hosted runner has no /dev/kvm and that
// `-cpu host` is a model QEMU registers only under KVM and HVF.
//
// An override naming a resource the module does not have is an error rather
// than a silent no-op, so this cannot rot into nothing.
resource "libvirt_domain" "vm" {
  type = "qemu"
  cpu  = null
}
HCL

    # A placeholder rather than an unquoted heredoc: the marker is JSON inside
    # JSON, and in an unquoted heredoc every \" in it would collapse to " and
    # produce a document tofu cannot parse.
    #
    # One VM, `firmware = "efi"` with a raw .fd loader and varstore template
    # pinned beside it -- the app02 shape from tests/golden/libvirt.tfvars.json
    # in RHEL's format rather than Fedora's. That is the branch #75 died on, and
    # the branch the delivery target takes (variables.tf's loader comment --
    # "RHEL ships a raw .fd"). Before #75 it had never been applied against a
    # real libvirtd anywhere: not here, not on the rig, not in the acceptance
    # run.
    #
    # **This replaced the autoselect shape** -- loader, loader_format and
    # nvram_template all null, app01's -- and the swap gives up real coverage,
    # recorded here because nothing else records it. Nothing anywhere now
    # exercises libvirt selecting a firmware from the host's own descriptors and
    # materialising a varstore from no template. What the module *emits* on that
    # branch is still pinned offline (libvirt-module.tftest.hcl asserts app01's
    # `os.firmware == "efi"`, `os.nv_ram == null` and the three null firmware
    # arms); what libvirtd does with it is pinned by nothing. The trade taken:
    # the branch given up has no known defect and the Fedora acceptance run
    # applied it, while the branch taken here had a live one. Carrying both means
    # two VMs in one apply, and DOMAIN/OVERLAY_VOL/SEED_VOL/MAC/MARKER_ID are
    # script globals that four functions key off -- roughly +60 lines against a
    # -3/+2 module fix. If autoselect later needs its own CI coverage, that is
    # its own change.
    #
    # A qcow2 pin cannot substitute on this runner class: beside `firmware =
    # "efi"` libvirt validates the pin against the host's descriptors rather than
    # deferring to it, and every Ubuntu descriptor declares raw, so a qcow2 pin is
    # refused at define with "Unable to find 'efi' firmware that is compatible
    # with the current configuration" (CI runs 33374365926, 33374623746).
    sed -e "s|@WORK@|$WORK|g" \
        -e "s|@LOADER@|$LOADER|g" \
        -e "s|@NVRAM_TEMPLATE@|$NVRAM_TEMPLATE|g" \
        > "$WORK/main.auto.tfvars.json" <<'JSON'
{
  "uri": "qemu:///system",
  "pool": "default",
  "base_volume": {
    "name": "vcows-smoke-golden.qcow2",
    "create": true,
    "path": "",
    "source": "@WORK@/golden.qcow2"
  },
  "vms": {
    "smoke01": {
      "domain_name": "vcows-smoke01",
      "overlay_name": "vcows-smoke01.qcow2",
      "seed_name": "vcows-smoke01-seed.iso",
      "marker_xml": "<vcows xmlns=\"urn:vcows:1\">{\"v\":\"0.1.0.0\",\"deployment\":\"smoke\",\"name\":\"smoke01\",\"id\":\"9f2b8d40-5c1e-5a3f-9a77-1c2d3e4f5a6b\"}</vcows>",
      "vcpus": 1,
      "memory_mib": 512,
      "disk_bytes": 268435456,
      "seed_iso": "@WORK@/seed.iso",
      "firmware": "efi",
      "machine": "q35",
      "loader": "@LOADER@",
      "loader_format": "raw",
      "nvram_template": "@NVRAM_TEMPLATE@",
      "configured_address": "192.168.122.60",
      "nics": [
        {
          "mac": "52:54:00:be:a8:60",
          "model": "virtio",
          "network": "default",
          "bridge": null
        }
      ]
    }
  }
}
JSON

    log "initialising the module copy at $WORK"
    tofu -chdir="$WORK" init -input=false >/dev/null
    tofu -chdir="$WORK" validate >/dev/null
    log "  ok    the module validates with the smoke override applied"
}

# -- the host ---------------------------------------------------------------

# The recipe is terraform-provider-libvirt's own CI, which runs its acceptance
# suite on unmodified ubuntu-latest. `libvirt-dev` is in that list and not in
# this one: nothing here compiles against the headers -- the provider ships as a
# prebuilt binary in the mirror. `ovmf` is here and not in that list, because
# this module asks libvirt to select an EFI firmware and a host with no
# descriptors installed has none to select.
packages() {
    have apt-get || die "this gate installs libvirt with apt-get -- it needs a Debian or Ubuntu runner"
    log "installing qemu, libvirt and ovmf"
    apt-get update -qq
    apt-get install -y -qq \
        qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients ovmf
}

# user/group root, no dynamic ownership and no security driver, which is what
# terraform-provider-libvirt's own CI sets. The pool, the uploaded volumes and
# the domain all belong to root here; letting libvirt relabel and confine them
# buys nothing on a runner that is deleted at the end of the job, and costs a
# class of AppArmor denial that reads like a module bug.
configure_qemu() {
    local conf=/etc/libvirt/qemu.conf
    if grep -q '^# vcows-smoke' "$conf" 2>/dev/null; then
        return
    fi
    cat >> "$conf" <<'CONF'

# vcows-smoke -- appended by scripts/smoke-libvirt.sh
user = "root"
group = "root"
dynamic_ownership = 0
security_driver = "none"
CONF
}

# Under systemd, restart the unit so the file above is read. Without systemd --
# a Docker executor, which is what .gitlab-ci.yml's `linux` tag describes --
# start the daemons directly. virtlogd is socket-activated under systemd and is
# not optional either way: a domain with a serial console cannot start without
# it, and this module gives every domain one.
start_libvirtd() {
    local i
    if [ -d /run/systemd/system ]; then
        systemctl restart libvirtd \
            || systemctl restart virtlogd virtqemud virtstoraged virtnetworkd
    else
        pgrep -x virtlogd >/dev/null || virtlogd -d
        pgrep -x libvirtd >/dev/null || libvirtd -d
    fi
    for i in $(seq 1 30); do
        if vsh version >/dev/null 2>&1; then
            log "  libvirtd answering on $URI after ${i}s"
            return
        fi
        sleep 1
    done
    die "libvirtd did not answer on $URI within 30s"
}

# vcows never creates a pool -- preflight refuses a missing one, and that is the
# behaviour at a site. The runner is the site here, so standing the pool up is
# part of preparing the host rather than part of what is under test.
storage_pool() {
    local err state
    if ! vsh pool-info "$POOL" >/dev/null 2>&1; then
        vsh pool-define-as --name "$POOL" --type dir --target "$POOL_DIR" >/dev/null
    fi
    # `pool-build` creates the target directory. Usually a no-op -- the package
    # ships /var/lib/libvirt/images -- and not optional: a dir pool whose target
    # is missing refuses to start, and that refusal is what the first run of this
    # job hit.
    vsh pool-build "$POOL" >/dev/null 2>&1 || true
    # The reason has to reach the log. The first spelling of this discarded
    # pool-start's stderr and died with "defined but will not start", which named
    # the symptom and cost a CI round trip to get behind.
    # Not `vsh pool-info | grep -q`. lib.sh sets `pipefail`, `grep -q` exits on
    # the first match, and virsh then dies of SIGPIPE with 141 -- so the pipeline
    # reports failure exactly when the grep succeeded. Measured on the third CI
    # run, where an already-active network was reported as one that would not
    # start. Every readback here goes through a variable for that reason.
    if ! err="$(vsh pool-start "$POOL" 2>&1)"; then
        state="$(vsh pool-info "$POOL" 2>&1 | tr -s ' ' || true)"
        if ! contains "$state" 'State: running'; then
            log "$(vsh pool-list --all 2>&1 || true)"
            log "$(vsh pool-dumpxml "$POOL" 2>&1 || true)"
            die "storage pool $POOL will not start: $err"
        fi
    fi
}

# The module renders `<interface type='network'><source network='default'/>`, so
# libvirtd needs that network defined and active or the domain will not define.
# libvirt-daemon-system defines it; nothing starts it on a fresh runner.
default_network() {
    local err state
    vsh net-info "$NETWORK" >/dev/null 2>&1 \
        || die "libvirt's '$NETWORK' network is not defined -- libvirt-daemon-system should have"
    # Same treatment as the pool above: the reason has to reach the log, or a
    # reader gets the symptom and another CI round trip.
    if ! err="$(vsh net-start "$NETWORK" 2>&1)"; then
        # `net-start` on an already-active network fails with "Requested
        # operation is not valid", which is the normal case here: libvirtd
        # autostarts it. The readback is what decides, and it goes through a
        # variable for the pipefail reason given above.
        state="$(vsh net-info "$NETWORK" 2>&1 | tr -s ' ' || true)"
        if ! contains "$state" 'Active: yes'; then
            log "$(vsh net-dumpxml "$NETWORK" 2>&1 || true)"
            log "$(journalctl -u libvirtd --no-pager -n 40 2>&1 || true)"
            die "libvirt's '$NETWORK' network will not start: $err"
        fi
    fi
}

# The golden image and the seed. Both are throwaway: the point is that real bytes
# go through `virStorageVolUpload` and that libvirt detects what it was handed,
# not that anything in them boots.
inputs() {
    need qemu-img xorriso
    qemu-img create -f qcow2 "$WORK/golden.qcow2" 64M >/dev/null
    mkdir -p "$WORK/cidata"
    printf 'instance-id: vcows-smoke\nlocal-hostname: %s\n' "$DOMAIN" \
        > "$WORK/cidata/meta-data"
    printf '#cloud-config\n' > "$WORK/cidata/user-data"
    xorriso -as mkisofs -quiet -output "$WORK/seed.iso" \
        -volid CIDATA -joliet -rock "$WORK/cidata"
}

# -- what libvirtd actually created -----------------------------------------

# Every readback here ends in `|| true`. Without it a failing command inside a
# `local x="$(...)"` assignment takes `set -e` and kills the run mid-report --
# measured: the fourth CI run stopped after four ok lines with no message at all,
# because `qemu-img info` on the overlay exited non-zero. The point of `check` is
# that one broken assertion becomes one FAIL line and the rest still run.
assert_volumes() {
    local vols base overlay seed
    vols="$(vsh vol-list "$POOL" 2>&1 || true)"
    check "the base volume exists in $POOL"    contains "$vols" "$BASE_VOL"
    check "the overlay volume exists in $POOL" contains "$vols" "$OVERLAY_VOL"
    check "the seed volume exists in $POOL"    contains "$vols" "$SEED_VOL"

    # The upload is the assertion. A volume that was allocated and never written
    # is zeros, and qemu-img calls zeros `raw`; only a real transfer of the qcow2
    # header makes this say qcow2.
    # `-U`, and it is not cosmetic. The domain is running, so QEMU holds a write
    # lock on the overlay and `qemu-img info` without it fails with `Failed to
    # get shared "write" lock` -- which is what stopped the fourth CI run. The
    # base only escaped because a backing file is opened read-only. Both carry it
    # so the two lines cannot drift apart.
    base="$(qemu-img info -U "$POOL_DIR/$BASE_VOL" 2>&1 || true)"
    check "virStorageVolUpload wrote a real qcow2 header into the base volume" \
        contains "$base" "file format: qcow2"

    # The chain, read off the file rather than off the plan. The mock can only
    # compare two generated strings to each other.
    overlay="$(qemu-img info -U "$POOL_DIR/$OVERLAY_VOL" 2>&1 || true)"
    check "the overlay backs onto the base volume on disk" \
        contains "$overlay" "backing file: $POOL_DIR/$BASE_VOL"

    # libvirt inspects uploaded content and reports the format it detects. The
    # module declares `iso` for exactly that reason -- declaring `raw` made the
    # provider's post-apply read disagree with its own plan, after the volume had
    # already been written. Nothing but a real libvirtd can say whether that is
    # still true of this provider and this libvirt.
    seed="$(vsh vol-dumpxml --pool "$POOL" "$SEED_VOL" 2>&1 || true)"
    check "libvirt detects the seed volume as iso" \
        contains "$seed" "<format type='iso'/>"
}

assert_domain() {
    local xml state info
    xml="$(vsh dumpxml "$DOMAIN" 2>&1 || true)"

    check "the domain runs under TCG, not KVM" \
        contains "$xml" "<domain type='qemu'"
    check "the marker survived DomainDefineXML" \
        contains "$xml" 'urn:vcows:1'
    check "the marker carries the id destroy discovers by" \
        contains "$xml" "$MARKER_ID"
    # The firmware pin, read off what libvirtd stored rather than off the plan.
    # The second line is the whole varstore element in one needle -- template
    # attribute, directory and .fd suffix -- so it is also what proves the
    # element is there for the two `absent` assertions below to mean anything.
    check "the pinned raw loader reached the domain verbatim" \
        contains "$xml" "<loader readonly='yes' type='pflash'>$LOADER</loader>"
    check "the varstore path follows the raw template's suffix" \
        contains "$xml" "template='$NVRAM_TEMPLATE'>$NVRAM_DIR/${DOMAIN}_VARS.fd<"

    # #75's other half, and the half no offline gate can reach. The mock
    # satisfies the schema with generated values and never reads anything back,
    # so libvirt-module.tftest.hcl can only pin that the module stopped emitting
    # these two attributes -- never that emitting them was wrong. These two lines
    # are that evidence, and the apply above is the regression gate: put either
    # attribute back in main.tf and `tofu apply` exits 1 with "Provider produced
    # inconsistent result after apply", after all three volumes exist.
    #
    # Not scoped to the <nvram> line: `format=` and `templateFormat=` appear on
    # no other element of this domain -- the disks carry <driver type=>, not a
    # format attribute -- and an `absent` against an extracted line that came
    # back empty would pass without having checked anything.
    check "libvirt omits format='raw' from the varstore, which is why the module must not declare it" \
        absent "$xml" "format='raw'"
    check "libvirt omits templateFormat from the varstore, for every value" \
        absent "$xml" 'templateFormat'
    check "acpi reached the domain" contains "$xml" '<acpi/>'
    check "apic reached the domain" contains "$xml" '<apic/>'
    check "the hpet timer is off, as this host's own guests have it" \
        contains "$xml" "<timer name='hpet' present='no'/>"
    check "the guest clock follows the host in UTC" \
        contains "$xml" "<clock offset='utc'>"
    check "the overlay disk passes discard=unmap" \
        contains "$xml" "discard='unmap'"
    # No trailing `/>` on these two. libvirt writes `<source file='...'
    # index='2'/>` for a running domain, so matching the self-closing form
    # asserted the index rather than the path -- measured, and the only two
    # assertions the fifth CI run failed. The path is the claim: destroy parses
    # `<source file=>`, and a module emitting a volume name rather than a
    # computed path is what this is guarding against.
    check "the root disk is the overlay's path, not its name" \
        contains "$xml" "<source file='$POOL_DIR/$OVERLAY_VOL'"
    check "the cdrom is the seed volume's path" \
        contains "$xml" "<source file='$POOL_DIR/$SEED_VOL'"
    check "the root disk is vda on virtio" \
        contains "$xml" "<target dev='vda' bus='virtio'/>"
    check "the seed is sda on sata" \
        contains "$xml" "<target dev='sda' bus='sata'/>"
    check "the seed is read-only" contains "$xml" '<readonly/>'
    check "the domain carries a virtio-rng reading /dev/urandom" \
        contains "$xml" '/dev/urandom'
    check "the NIC carries the derived MAC" \
        contains "$xml" "<mac address='$MAC'/>"
    check "the NIC is on the $NETWORK network" \
        contains "$xml" "<source network='$NETWORK'"

    state="$(vsh domstate "$DOMAIN" || true)"
    check "the domain is running" contains "$state" 'running'
    info="$(vsh dominfo "$DOMAIN" | tr -s ' ' || true)"
    check "the domain is set to autostart" contains "$info" 'Autostart: enable'
}

assert_gone() {
    local domains vols vol
    domains="$(vsh list --all --name 2>&1 || true)"
    vols="$(vsh vol-list "$POOL" 2>&1 || true)"
    check "destroy undefined the domain" absent "$domains" "$DOMAIN"
    for vol in "$BASE_VOL" "$OVERLAY_VOL" "$SEED_VOL"; do
        check "destroy removed $vol" absent "$vols" "$vol"
    done
}

main() {
    local uid drift
    # qemu:///system is root's socket, and adding this user to the libvirt group
    # would not take effect in the shell that added it. Re-exec once rather than
    # scatter sudo through every virsh call -- and `sudo "$0"` rather than
    # `sudo -E`, which needs the SETENV sudoers tag a runner's NOPASSWD:ALL does
    # not grant. lib.sh puts .tools/bin back on PATH when it is sourced again.
    # Assigned rather than inline: inline, an `id` that fails puts an empty
    # string in a numeric test, the test is false, the re-exec is skipped, and
    # every virsh below fails against root's socket saying nothing about why.
    # As an assignment the failure reaches lib.sh's `set -euo pipefail`. SC2312.
    uid="$(id -u)"
    if [ "$uid" -ne 0 ]; then
        have sudo || die "this gate needs root or sudo: it starts libvirtd and writes /etc/libvirt"
        log "re-running under sudo"
        exec sudo "$0" "$@"
    fi

    need tofu
    trap cleanup EXIT

    prepare
    packages
    configure_qemu
    start_libvirtd
    storage_pool
    default_network
    inputs

    log "applying the module against $URI"
    tofu -chdir="$WORK" apply -auto-approve -input=false

    log "what libvirtd created"
    assert_volumes
    assert_domain

    # A successful apply proves the provider's *create* read agreed with its
    # plan. Nothing else proves its *refresh* read does, and a disagreement there
    # is #75 one step later: not a failed apply but a permanent diff that every
    # subsequent plan re-proposes and no apply can settle. `-detailed-exitcode`
    # is 0 for no changes, 1 for an error and 2 for a diff, so the assertion is
    # on the number and not on `set -e`.
    log "re-reading the applied domain"
    drift=0
    tofu -chdir="$WORK" plan -detailed-exitcode -input=false >/dev/null 2>&1 || drift=$?
    check "the applied domain re-reads clean: no attribute drifts on refresh" \
        test "$drift" -eq 0

    log "destroying"
    tofu -chdir="$WORK" destroy -auto-approve -input=false
    assert_gone

    [ "$fail" -eq 0 ] || die "the smoke gate failed -- see the FAIL lines above"
    log "the module applies, libvirtd accepts what it renders, and destroy removes it"
}

main "$@"
