#!/usr/bin/env bash
# The end-to-end smoke gate: the shipped create path applied against a real
# libvirtd, on an unmodified hosted runner.
#
# Everything else that reads `orchestrator/backends/libvirt/create.py` reads a
# substitute. `tests/test_libvirt_create.py` drives it against
# `tests/fake_libvirt.py`, which records what it was handed and answers with
# objects it invented rather than with anything a daemon parsed. So the three
# things that fake stands in for have never run in CI at all:
#
#   * `virStorageVolUpload` -- a local file streamed into a pool
#   * libvirtd parsing and accepting the domain XML `create.domain_xml` renders
#   * define, start and undefine of that domain
#
# This runs all three, and then tears the result down through the shipped
# `destroy.destroy` rather than with virsh, so the marker round trip is on the
# gate too. What libvirtd actually created is then asserted against rather than
# what was sent -- a document that agrees with itself is what the fake already
# proves.
#
# **The assertions live in `tests/test_libvirt_smoke.py`, not here** (`#122`).
# This script builds the host and drives the create and the teardown; that file
# says what the result has to look like, behind `VCOWS_GATES=smoke`. Every
# constant below is exported for it, and it is invoked twice -- once with the
# domain running and once after the teardown -- because those are two different
# subjects.
#
# **No guest is booted and no guest address is observed.** The domain reaches
# firmware and stops there; nothing here needs it to reach a login prompt. The
# defect class `docs/archive/acceptance.md` records -- guests healthy on the wrong
# addresses -- is not what this gate covers.
#
# ## No /dev/kvm
#
# The domain runs under TCG, so this behaves the same on a GitHub-hosted runner
# and on a GitLab.com SaaS runner. GitLab is the destination, and its shared
# runners have no `/dev/kvm`.
#
# Getting there needs a two-attribute override, and `create.py` offers no lever
# for one: `DOMAIN_XML` writes `<domain type='kvm'>` because that is what a site
# gets, and a switch nothing but this gate would set is surface a site pays for.
# So the override is applied to a *copy* of that template, inside the driver
# below and in that process only -- the shipped tree is never edited, and
# `just check`, `just image` and every other reader keep seeing `kvm`.
#
# `cpu` goes with it. `create.py` emits `<cpu mode='host-passthrough'/>`, which
# QEMU renders as `-cpu host` -- a model registered only for KVM and HVF. Under
# TCG the domain defines and then fails to start. Dropping the element leaves
# libvirt to pick its own default, which is the only part of this the runner can
# supply. Each substitution is checked against the template it names, so an
# override that no longer matches `create.py` is an error rather than a silent
# no-op.
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
# `create.NVRAM_DIR`, and nowhere configurable, so it is a constant here too
# rather than something read back from the domain.
NVRAM_DIR="/var/lib/libvirt/qemu/nvram"
NETWORK="default"

# One prefix on everything, so the cleanup below can name what it removes and a
# half-applied run leaves nothing that looks like somebody else's.
DOMAIN="vcows-smoke01"
BASE_VOL="vcows-smoke-golden.qcow2"
OVERLAY_VOL="vcows-smoke01.qcow2"
SEED_VOL="vcows-smoke01-seed.iso"
# Neither is derived here. `Marker.for_vm` and `derive_mac` compute both at a
# site, and `tests/test_marker.py` pins those derivations; a literal is what
# lets the assertions read the value back rather than recompute it with the
# function under test. The marker's `name`, though, is not free: `destroy`
# derives the two volume names it is allowed to delete from it, so it is the
# domain's stem here exactly as it is at a site.
MARKER_ID="9f2b8d40-5c1e-5a3f-9a77-1c2d3e4f5a6b"
MAC="52:54:00:be:a8:60"

# The firmware pin, in one place: the values file below is substituted from these
# and the assertions read them back, so the two cannot drift apart. Ubuntu's `ovmf`
# ships raw .fd builds and all four of its /usr/share/qemu/firmware descriptors
# declare "raw" -- measured, CI run 33374623746.
LOADER="/usr/share/OVMF/OVMF_CODE_4M.fd"
NVRAM_TEMPLATE="/usr/share/OVMF/OVMF_VARS_4M.fd"

# The probe below defines its own domain, so it needs a name the asserts and the
# EXIT trap can tell apart from the one the create makes.
PROBE_DOMAIN="vcows-smoke-probe"

WORK=""

vsh() { virsh -c "$URI" "$@"; }

# Kept when the assertions left, because host provisioning still reads virsh
# output back: `storage_pool` and `default_network` both decide whether an
# already-active object is the failure it looks like. `absent` went with the
# assertions -- nothing below needs the negative form.
contains() { grep -qF -- "$2" <<<"$1"; }

# The constants above, over the environment, because tests/test_libvirt_smoke.py
# asserts about the objects this script names and a second copy there would be
# one fixture maintained in two languages.
#
# Exported here rather than passed through CI: the workflow gate rejects any
# `VAR=x just recipe` line, and `sudo "$0"` drops the environment at the re-exec
# anyway. This is the same rule scripts/test-image.sh follows for VCOWS_IMAGE.
export VCOWS_SMOKE_URI="$URI"
export VCOWS_SMOKE_POOL="$POOL"
export VCOWS_SMOKE_POOL_DIR="$POOL_DIR"
export VCOWS_SMOKE_NVRAM_DIR="$NVRAM_DIR"
export VCOWS_SMOKE_NETWORK="$NETWORK"
export VCOWS_SMOKE_DOMAIN="$DOMAIN"
export VCOWS_SMOKE_BASE_VOL="$BASE_VOL"
export VCOWS_SMOKE_OVERLAY_VOL="$OVERLAY_VOL"
export VCOWS_SMOKE_SEED_VOL="$SEED_VOL"
export VCOWS_SMOKE_MARKER_ID="$MARKER_ID"
export VCOWS_SMOKE_MAC="$MAC"
export VCOWS_SMOKE_LOADER="$LOADER"
export VCOWS_SMOKE_NVRAM_TEMPLATE="$NVRAM_TEMPLATE"

# One pytest invocation, one phase. The two phases are selected by node id rather
# than by a marker or a `-k` expression: a conditional skip inside the file would
# have to go through conftest.gate() or conftest.require(), and neither can
# express "the domain has not been destroyed yet". Deselection is not a skip.
#
# `-p no:cacheprovider` and PYTHONDONTWRITEBYTECODE because this runs as root
# after the re-exec, and a root-owned .pytest_cache/ or __pycache__/ left in the
# work tree is a failure the next unprivileged run reports as something else.
asserts() {
    VCOWS_GATES=smoke PYTHONDONTWRITEBYTECODE=1 \
        "$PY" -m pytest -q -rs -p no:cacheprovider "$REPO/tests/test_libvirt_smoke.py::$1"
}

# The teardown is the thing under test, so it runs in `main` where its exit
# status is read. This is the second line of defence: a create that failed
# halfway leaves objects with nothing to record them but their marker, and a
# runner left with a defined domain fails every later run for a reason that has
# nothing to do with the change under review.
cleanup() {
    local status=$? vol
    set +e
    trap - EXIT
    vsh destroy "$DOMAIN" >/dev/null 2>&1
    vsh undefine --nvram "$DOMAIN" >/dev/null 2>&1
    vsh undefine --nvram "$PROBE_DOMAIN" >/dev/null 2>&1
    for vol in "$BASE_VOL" "$OVERLAY_VOL" "$SEED_VOL"; do
        vsh vol-delete --pool "$POOL" "$vol" >/dev/null 2>&1
    done
    [ -n "$WORK" ] && rm -rf "$WORK"
    exit "$status"
}

# -- the values -------------------------------------------------------------

# Deliberately first, before a single package is installed: a typo in the values
# below, or an override that no longer matches `create.py`, is a three-second
# failure, and finding it after ninety seconds of apt is ninety seconds wasted on
# every run that has one.
prepare() {
    WORK="$(mktemp -d)"

    # `render.render`'s output, written by hand -- the same fixture role
    # tests/golden/libvirt.tfvars.json plays offline, and the reason this gate
    # needs no config.yaml: `schema._check_target` requires a
    # `qemu+ssh://host/system` URI and this runner's daemon is on a local socket.
    # `create.create` takes these values and a connection, so the URI is an
    # argument to the driver below rather than a key here.
    #
    # A placeholder rather than an unquoted heredoc: the marker is JSON inside
    # JSON, and in an unquoted heredoc every \" in it would collapse to " and
    # produce a document `json.load` cannot parse.
    #
    # One VM, `firmware = "efi"` with a raw .fd loader and varstore template
    # pinned beside it -- the app02 shape from tests/golden/libvirt.tfvars.json
    # in RHEL's format rather than Fedora's. That is the branch #75 died on, and
    # the branch the delivery target takes (schema.py's loader comment -- "RHEL
    # ships a raw .fd"). Before #75 it had never been applied against a real
    # libvirtd anywhere: not here, not on the rig, not in the acceptance run.
    #
    # **This replaced the autoselect shape** -- loader, loader_format and
    # nvram_template all null, app01's -- and the swap gives up real coverage,
    # recorded here because nothing else records it. Nothing anywhere now
    # exercises libvirt selecting a firmware from the host's own descriptors and
    # materialising a varstore from no template. What vcows *emits* on that
    # branch is still pinned offline (test_libvirt_create.py's
    # `test_autoselected_firmware_pins_nothing`); what libvirtd does with it is
    # pinned by nothing. The trade taken: the branch given up has no known defect
    # and the Fedora acceptance run applied it, while the branch taken here had a
    # live one. Carrying both means two VMs in one create, and
    # DOMAIN/OVERLAY_VOL/SEED_VOL/MAC/MARKER_ID are script globals that four
    # functions key off -- roughly +60 lines against a -3/+2 fix. If autoselect
    # later needs its own CI coverage, that is its own change.
    #
    # A qcow2 pin cannot substitute on this runner class, and the reason changed
    # with #107. It used to be that beside `firmware = "efi"` libvirt validates
    # the pin against the host's descriptors rather than deferring to it, and
    # every Ubuntu descriptor declares raw, so a qcow2 pin was refused at define
    # with "Unable to find 'efi' firmware that is compatible with the current
    # configuration" (CI runs 33374365926, 33374623746). That measurement is what
    # produced #107, and `create.firmware_xml` emits no `firmware` attribute
    # beside a pin, so it no longer applies. What remains is simpler and was
    # always true: Ubuntu ships no qcow2 OVMF build for this to point at -- every
    # descriptor declaring raw is the evidence -- so there is no qcow2 file here
    # to pin.
    sed -e "s|@WORK@|$WORK|g" \
        -e "s|@LOADER@|$LOADER|g" \
        -e "s|@NVRAM_TEMPLATE@|$NVRAM_TEMPLATE|g" \
        > "$WORK/tfvars.json" <<'JSON'
{
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
      "marker_xml": "<vcows xmlns=\"urn:vcows:1\">{\"v\":\"0.1.0.0\",\"deployment\":\"smoke\",\"name\":\"vcows-smoke01\",\"id\":\"9f2b8d40-5c1e-5a3f-9a77-1c2d3e4f5a6b\"}</vcows>",
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
          "kind": "network",
          "source": "default"
        }
      ]
    }
  }
}
JSON

    # The reason this function runs first: the values are read and every domain
    # XML is rendered from them, so a key this fixture spells wrong fails here
    # rather than after apt.
    "$PY" - "$WORK/tfvars.json" <<'PY'
import json
import sys

from orchestrator.backends.libvirt import create

with open(sys.argv[1]) as handle:
    values = json.load(handle)
for vm in values["vms"].values():
    create.domain_xml(vm, "overlay", "seed")
PY
    log "  ok    the values render a domain XML"
}

# -- the host ---------------------------------------------------------------

# The recipe is the libvirt provider project's own CI, which runs its acceptance
# suite on unmodified ubuntu-latest. `libvirt-dev` is in that list and not in
# this one: nothing here compiles against the headers -- os-deps.sh installs the
# distro's python3-libvirt and `just dev-env` is what makes it visible. `ovmf` is
# here and not in that list, because the values below pin a firmware out of it,
# and the probe converts the same two files.
packages() {
    have apt-get || die "this gate installs libvirt with apt-get -- it needs a Debian or Ubuntu runner"
    log "installing qemu, libvirt and ovmf"
    apt-get update -qq
    apt-get install -y -qq \
        qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients ovmf
}

# user/group root, no dynamic ownership and no security driver, which is what
# that project's CI sets. The pool, the uploaded volumes and
# the domain all belong to root here; letting libvirt relabel and confine them
# buys nothing on a runner that is deleted at the end of the job, and costs a
# class of AppArmor denial that reads like a bug in the XML vcows rendered.
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
# it, and `create.DOMAIN_XML` gives every domain one.
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

# `create.domain_xml` renders `<interface type='network'><source
# network='default'/>`, so libvirtd needs that network defined and active or the
# domain will not define. libvirt-daemon-system defines it; nothing starts it on
# a fresh runner.
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

# #107, and the one property in this file that is about libvirt rather than about
# what vcows renders. The fix for #107 is that a pinned loader is emitted with no
# `firmware = "efi"` beside it -- `create.firmware_xml`'s exclusivity -- because
# autoselection does not defer to a pin: it validates the pin against the host's
# own firmware descriptors and refuses a format they do not carry. That fix is
# only worth anything while omitting the attribute actually keeps a pin out of
# that validation, which is a property of libvirt and not of anything this repo
# controls.
#
# Nothing else can stand guard over it:
#
#   * tests/test_libvirt_create.py pins what vcows *emits*
#     (`test_a_pinned_loader_replaces_the_autoselection_and_names_its_varstore`)
#     against a fake, which cannot refuse anything.
#   * `virsh dumpxml` cannot carry it either. libvirt fills `firmware='efi'` back
#     into the stored XML when the pin matches a descriptor it can name, so the
#     raw .fd fixture below dumps with the attribute present even though nothing
#     sent it -- an `absent` on it FAILs against the raw pin (CI run
#     33436774063) and passes against a qcow2 one (run 33437247928). Nothing in
#     that capture distinguishes "vcows sent it" from "libvirt deduced it".
#   * This gate's own fixture cannot carry it, because the format this runner's
#     descriptors refuse is qcow2 and the fixture pins raw. Swapping it would
#     give up the raw .fd branch, which is #75's and the delivery target's shape;
#     carrying both means a second VM in the create, which the values above price
#     at roughly +60 lines because DOMAIN and four other globals are keyed off by
#     four functions.
#
# So this defines one throwaway domain directly, out of band of the create, with
# the shape `create.firmware_xml` now renders: a qcow2 loader and no `firmware`
# attribute, on a host whose four descriptors all declare raw. Before #107 that same
# configuration was refused at define with "Unable to find 'efi' firmware that is
# compatible with the current configuration" (runs 33374365926, 33374623746).
# `define` is the whole test -- no start, no boot, no KVM -- because define is
# where the descriptor match happens.
#
# The refusal is loud rather than silent, so this is early notice and not the
# only line of defence. It earns its place because the notice arrives in CI
# instead of at a site, and because a libvirt that changes this behaviour is the
# one thing that reopens #107 without anyone touching this repo.
probe_pinned_loader_escapes_autoselection() {
    local err="" defined=0
    # Ubuntu ships no qcow2 OVMF build -- that is the same fact its descriptors
    # record -- so convert the raw ones. Under $WORK, which cleanup removes, and
    # only the declared format differs from the values file's own pin.
    qemu-img convert -O qcow2 "$LOADER" "$WORK/probe_CODE.qcow2"
    qemu-img convert -O qcow2 "$NVRAM_TEMPLATE" "$WORK/probe_VARS.qcow2"
    cat > "$WORK/probe.xml" <<XML
<domain type='qemu'>
  <name>$PROBE_DOMAIN</name>
  <memory unit='MiB'>256</memory>
  <vcpu>1</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash' format='qcow2'>$WORK/probe_CODE.qcow2</loader>
    <nvram template='$WORK/probe_VARS.qcow2' format='qcow2'>$WORK/probe_VARS_live.qcow2</nvram>
  </os>
  <!-- Not decoration: libvirt refuses a UEFI x86_64 domain without it, with
       "unsupported configuration: UEFI requires ACPI on this architecture"
       (measured, CI run 33438506248). apic follows it here for the same reason
       main.tf emits both. -->
  <features><acpi/><apic/></features>
</domain>
XML
    # The error text is logged rather than asserted on. A define that fails for
    # an unrelated reason -- a machine type this qemu does not carry, say --
    # would otherwise read as a #107 regression, and the log is what tells the
    # two apart.
    if err="$(vsh define "$WORK/probe.xml" 2>&1)"; then
        defined=1
    else
        log "  the probe's define was refused: $err"
    fi
    vsh undefine --nvram "$PROBE_DOMAIN" >/dev/null 2>&1 || true
    # The probe itself stays here rather than moving to pytest with the rest of
    # the assertions (#122): it converts two firmware images, writes a domain and
    # defines it, which is host work of exactly the kind this script owns. Only
    # the verdict crosses, and TestApplied carries it with the rationale above.
    export VCOWS_SMOKE_PROBE_DEFINED="$defined"
}

# -- the create and the teardown --------------------------------------------

# `orchestrator.backends.libvirt.create.create`, against this runner's daemon,
# with the values `prepare` wrote. The same call `cli._deploy` makes, handed the
# same shape `render.render` returns.
#
# The TCG override lives here because this is the only process that needs it. It
# rewrites a *copy* of the template held by this interpreter -- the file on disk
# is untouched -- and each substitution is checked against the text it names, so
# an override that no longer matches `create.py` stops the gate instead of
# quietly leaving `type='kvm'` in place for the domain to fail to start on. The
# header above says why each of the two is here.
create_vm() {
    log "creating through orchestrator.backends.libvirt.create against $URI"
    "$PY" - "$URI" "$WORK/tfvars.json" <<'PY'
import json
import logging
import sys

import libvirt

from orchestrator.backends.libvirt import create

logging.basicConfig(level=logging.INFO, format="  %(message)s")

for old, new in (
    ("<domain type='kvm'>", "<domain type='qemu'>"),
    ("  <cpu mode='host-passthrough'/>\n", ""),
):
    if old not in create.DOMAIN_XML:
        raise SystemExit(f"create.DOMAIN_XML no longer carries {old!r}")
    create.DOMAIN_XML = create.DOMAIN_XML.replace(old, new)

uri, values = sys.argv[1], sys.argv[2]
with open(values) as handle:
    tfvars = json.load(handle)
conn = libvirt.open(uri)
try:
    create.create(conn, tfvars)
finally:
    conn.close()
PY
}

# `orchestrator.backends.libvirt.destroy.destroy`, which is what `vcows destroy`
# runs. It is driven by markers rather than by state, so the domain is looked up
# by name and read with preflight's own `marker_of` and `disks_of` -- the
# enumeration is all this replaces, for the same reason the values file replaces
# a config.yaml. If the marker did not survive define, `_deletable` refuses both
# volumes and TestDestroyed says so.
#
# No `--nvram` and no `vol-delete` here: what the varstore and the two volumes
# cost is exactly what is under test.
tear_down() {
    log "tearing down through orchestrator.backends.libvirt.destroy"
    "$PY" - "$URI" "$DOMAIN" <<'PY'
import logging
import sys
from xml.etree import ElementTree as ET

import libvirt

from orchestrator.backends.base import Existing
from orchestrator.backends.libvirt import destroy, preflight

logging.basicConfig(level=logging.INFO, format="  %(message)s")

uri, name = sys.argv[1], sys.argv[2]
conn = libvirt.open(uri)
try:
    dom = conn.lookupByName(name)
    root = ET.fromstring(dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE))
    outcome = destroy.destroy(
        {},
        conn,
        [
            Existing(
                name=dom.name(),
                id=dom.UUIDString(),
                marker=preflight.marker_of(root),
                disks=preflight.disks_of(root),
            )
        ],
    )
finally:
    conn.close()
print(f"  destroyed {outcome.destroyed}, skipped {outcome.skipped}")
PY
}

main() {
    local uid applied destroyed
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

    # The heredocs below `import orchestrator`, which resolves by cwd alone --
    # the venv installs the RPM bindings but never this project -- so the run
    # has to happen from the tree rather than from wherever this was invoked.
    cd "$REPO"

    need_venv
    trap cleanup EXIT

    prepare
    packages
    configure_qemu
    start_libvirtd
    storage_pool
    default_network
    inputs

    # Out of band of the create, and before it, so a libvirt that reopened #107
    # is named as such rather than surfacing as a define failure.
    log "probing whether a pinned loader escapes firmware autoselection"
    probe_pinned_loader_escapes_autoselection

    create_vm

    # Both statuses are captured rather than left to `set -e`, and that is
    # deliberate: an aborting assertion phase would skip the destroy below, so a
    # single failed needle would leave the runner with a defined domain and cost
    # the destroy assertions too. This is what the ok/FAIL accumulator bought,
    # and pytest already reports every failure within a phase.
    log "what libvirtd created"
    # The <os> block, live and stored, so a needle that misses in CI is readable
    # from the job log: pytest truncates the document before it reaches <os>.
    log "$(vsh dumpxml "$DOMAIN" | sed -n '/<os/,/<\/os>/p' || true)"
    log "$(vsh dumpxml --inactive "$DOMAIN" | sed -n '/<os/,/<\/os>/p' || true)"
    applied=0
    asserts TestApplied || applied=$?

    tear_down
    destroyed=0
    asserts TestDestroyed || destroyed=$?

    if [ "$applied" -ne 0 ] || [ "$destroyed" -ne 0 ]; then
        die "the smoke gate failed -- see the pytest output above"
    fi
    log "the create runs, libvirtd accepts what it renders, and destroy removes it"
}

main "$@"
