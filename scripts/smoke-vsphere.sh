#!/usr/bin/env bash
# The end-to-end vSphere gate: `vcows deploy` and `vcows destroy`, run as a site
# runs them, against the pinned govmomi simulator on an unmodified runner.
#
# Everything else that reads `orchestrator/backends/vsphere/` reads
# `tests/fake_vsphere.py`, which records the specs it was handed and answers with
# objects it invented rather than with anything a server deserialised. So what
# that fake stands in for has never run in CI at all:
#
#   * the SOAP round trip itself -- pyVmomi serialising the ConfigSpec, the
#     CloneSpec and the OVF import spec this backend builds, and a server taking
#     them
#   * the datastore `/folder` PUT, which is plain HTTPS with a session cookie and
#     not an SDK call
#   * the `ImportVApp` lease: `CreateImportSpec`, the POST to the device URL, the
#     progress calls and `HttpNfcLeaseComplete`
#   * the PropertyCollector reads every phase depends on, against a server that
#     answers with real SOAP rather than with objects the fake constructed
#
# This runs all of them, in the order a deploy runs them, and then tears the
# result down through the shipped `destroy` rather than through govc -- so the
# marker round trip through `config.annotation` is on the gate too.
#
# **The assertions live in `tests/test_vsphere_smoke.py`, not here.** This script
# starts the simulator, builds the inputs and drives the two verbs; that file
# says what the result has to look like, behind `VCOWS_GATES=vcsim`. Every
# constant below is exported for it, and it is invoked twice -- once with the VM
# running and once after the teardown -- because those are two different
# subjects.
#
# ## What a green run does not prove
#
# vcsim negotiates `urn:vim25/6.5`, the target is vCenter 7, and it validates
# almost nothing: `docs/research/vsphere-vcsim-2026-09.md` section 2 measured it
# accepting a 33-byte "streamOptimized VMDK", a `52:54:00` manual MAC and a
# linked-clone spec it then ignored. So this gate proves the call shapes and the
# ordering, and proves nothing about acceptance. Section 4 of that document is
# the list of assertions this gate must not make; the test file carries it, row
# by row, with the first-contact item each defers to.
#
# ## Not the rig gate
#
# `tests/test_vsphere_rig.py` and `VCOWS_VSPHERE_ENDPOINT` stay as they are: a
# named skip, read-only, against a real vCenter. This one creates a throwaway
# image and a VM of its own and destroys both.
#
# Not part of `just lint` or `just check` either, for the reason
# `scripts/smoke-libvirt.sh` is not: it starts a server and drives a whole
# deploy. It is a CI job of its own.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# vcsim's **default model**, and the names it gives it: one datacenter `DC0`,
# one datastore `LocalDS_0`, one cluster `DC0_C0`, four hosts and the port group
# `VM Network`. Started with no `-dc`, `-ds`, `-cluster` or `-pg` flag, which is
# what the C2 spike measured -- passing the defaults back in would be a second
# place for the two to disagree.
HOST=127.0.0.1
PORT=8989
ENDPOINT="https://$HOST:$PORT"
DATACENTER=DC0
DATASTORE=LocalDS_0
CLUSTER=DC0_C0
NETWORK="VM Network"

# One prefix on everything, as the libvirt gate does, so a half-applied run
# leaves nothing that looks like somebody else's. vcsim's inventory lives in the
# process and dies with it, but the assertions name these and a stray VM from
# vcsim's own model must not answer to one of them.
DEPLOYMENT=vcows-vsmoke
VM=vcows-vsmoke01
# The template's name is `image.base_volume_name`: on this backend the shared
# image is a marked template VM, so the name is a VM name and carries no
# `.qcow2` suffix.
TEMPLATE=vcows-vsmoke-golden

# Neither is derived here. `Marker.for_vm` and `derive_mac` compute both at a
# site and `tests/test_marker.py` pins those derivations; a literal is what lets
# the assertions read the value back rather than recompute it with the function
# under test. Only the id, never the whole payload -- the marker carries the
# tool's version and a bump would otherwise fail this gate for nothing.
#
# The clone's own marker id is deliberately not here: vcsim drops the annotation
# off a `CloneSpec` (see `mark_the_clone`), so nothing asserts it and a constant
# with no reader would only look like coverage.
TEMPLATE_MARKER_ID=86ed3914-d64b-530d-b992-2dbb2583c2eb
MAC=52:54:00:b8:72:ce

# Where `create.upload` puts this VM's seed ISO, and the spelling every later SDK
# call wants: `[datastore] vcows/<vm>/<vm>-seed.iso`.
SEED_PATH="[$DATASTORE] vcows/$VM/$VM-seed.iso"

# `create.SNAPSHOT_NAME`, and nowhere configurable, so it is a constant here for
# the reason NVRAM_DIR is one in the libvirt gate.
SNAPSHOT=vcows-base

# **1 GiB, not the 64 MiB the libvirt gate uses, and the size is forced.** A
# linked clone is the default and its delta disk cannot be extended, so
# `schema._check_linked_clone_disk` refuses a `disk_gb` above the image's virtual
# size while `imagecheck.check_disk_capacity` refuses one below it -- and
# `disk_gb` is an integer of at least 1. So the virtual size has to be exactly
# `disk_gb` GiB. It is still a throwaway: the qcow2 is empty, so the file is
# under a megabyte and the VMDK converted from it is streamOptimized and nearly
# as small.
DISK_GB=1

WORK=""
VCSIM_PID=""

# The constants above, over the environment, for the reason the libvirt gate
# gives: the test asserts about the objects this script names, and a second copy
# there would be one fixture maintained in two languages. Exported rather than
# passed through CI, because the workflow gate rejects any `VAR=x just recipe`
# line.
export VCOWS_VSMOKE_ENDPOINT="$ENDPOINT"
export VCOWS_VSMOKE_DATACENTER="$DATACENTER"
export VCOWS_VSMOKE_DATASTORE="$DATASTORE"
export VCOWS_VSMOKE_CLUSTER="$CLUSTER"
export VCOWS_VSMOKE_NETWORK="$NETWORK"
export VCOWS_VSMOKE_DEPLOYMENT="$DEPLOYMENT"
export VCOWS_VSMOKE_VM="$VM"
export VCOWS_VSMOKE_TEMPLATE="$TEMPLATE"
export VCOWS_VSMOKE_TEMPLATE_MARKER_ID="$TEMPLATE_MARKER_ID"
export VCOWS_VSMOKE_MAC="$MAC"
export VCOWS_VSMOKE_SEED_PATH="$SEED_PATH"
export VCOWS_VSMOKE_SNAPSHOT="$SNAPSHOT"
export VCOWS_VSMOKE_VCPUS=2
export VCOWS_VSMOKE_MEMORY_MIB=512

# One pytest invocation, one phase, selected by node id rather than by a marker
# or a `-k` expression -- the libvirt gate's reason applies unchanged: a
# conditional skip inside the file would have to go through `conftest.gate()` or
# `conftest.require()`, and neither can express "the VM has not been destroyed
# yet". Deselection is not a skip.
#
# `VCOWS_GATES=vcsim` demands the gate rather than letting it skip, so a run
# where the constants above did not arrive fails instead of passing quietly.
asserts() {
    VCOWS_GATES=vcsim "$PY" -m pytest -q -rs "$REPO/tests/test_vsphere_smoke.py::$1"
}

# vcsim holds its whole inventory in the process, so killing it is the teardown
# of everything except the files under $WORK. It still runs on every exit path:
# a run that failed halfway leaves a listening socket on 8989, and the next run
# would connect to it and assert about the previous run's VMs.
cleanup() {
    local status=$?
    set +e
    trap - EXIT
    [ -n "$VCSIM_PID" ] && kill "$VCSIM_PID" >/dev/null 2>&1
    [ -n "$WORK" ] && rm -rf "$WORK"
    exit "$status"
}

# -- the simulator ----------------------------------------------------------

# `-l` and nothing else. vcsim serves HTTPS with a self-signed certificate it
# generates per run, which is why the config below carries `insecure: true`: there
# is no CA to hand `ca_cert`, and `api.connect` would refuse the certificate.
start_vcsim() {
    local i
    need vcsim
    vcsim -l "$HOST:$PORT" > "$WORK/vcsim.log" 2>&1 &
    VCSIM_PID=$!
    for i in $(seq 1 30); do
        # `/about` is vcsim's own unauthenticated page. `-k` because of the
        # certificate above, and the status is all this reads -- the question is
        # whether the socket answers, not what it says.
        if curl -sk -o /dev/null "$ENDPOINT/about"; then
            log "  vcsim answering on $ENDPOINT after ${i}s"
            return
        fi
        sleep 1
    done
    log "$(cat "$WORK/vcsim.log" || true)"
    die "vcsim did not answer on $ENDPOINT within 30s"
}

# -- the inputs -------------------------------------------------------------

# The golden image and the config a site would write. The image is empty and
# throwaway: what is under test is that `prepare` converts it, that the lease
# takes the bytes and that vCenter's inventory ends up with a template, not
# anything inside it.
inputs() {
    need qemu-img
    qemu-img create -f qcow2 "$WORK/golden.qcow2" "${DISK_GB}G" > /dev/null

    # Interpolated from the constants above rather than written out, so the
    # config this deploys and the values the assertions read back cannot drift.
    # `insecure: true` for the reason `start_vcsim` gives; `validate` warns about
    # it, which is correct and not a failure.
    #
    # No `import:` and no `clone:` key: both first-contact knobs run at their
    # defaults, `ovf` and `linked`, because those are what a delivered bundle
    # uses. The `datastore` import path is the alternative and is not exercised
    # here.
    cat > "$WORK/config.yaml" <<CONFIG
schema_version: 1
deployment: $DEPLOYMENT
backend: vsphere
target:
  vsphere:
    endpoint: $ENDPOINT
    user: vcows@vsphere.local
    password: not-a-password
    datacenter: $DATACENTER
    datastore: $DATASTORE
    network: $NETWORK
    cluster: $CLUSTER
    insecure: true
image:
  source_qcow2: $WORK/golden.qcow2
  base_volume_name: $TEMPLATE
vms:
  - name: $VM
    vcpus: $VCOWS_VSMOKE_VCPUS
    memory_mib: $VCOWS_VSMOKE_MEMORY_MIB
    disk_gb: $DISK_GB
    nics:
      - ip_cidr: 192.168.122.60/24
        gateway: 192.168.122.1
        nameservers: [192.168.122.1]
CONFIG
}

# -- the two verbs ----------------------------------------------------------

# `python -m orchestrator.cli`, which is what the image's `/usr/local/bin/vcows`
# is -- so this drives the shipped entry point and every phase behind it,
# including the config load and the offline validation, rather than calling
# `create.create` the way the libvirt gate has to.
vcows() {
    "$PY" -m orchestrator.cli "$@"
}

# **The one thing vcsim does not do, put back by hand between the two phases.**
#
# Measured on 0.56.0: `CloneVM_Task` accepts a `CloneSpec` whose `config` carries
# an annotation, applies `numCPUs` and `memoryMB` off that same `ConfigSpec`, and
# **drops the annotation** -- the property comes back absent, for a source that
# carries one and for one that does not, and `ReconfigVM_Task` on the same VM
# afterwards sets it. The C2 spike measured the annotation round trip on the
# template, which goes through `ReconfigVM_Task`, and never on a clone.
#
# The backend is not changed for it. `create.clone_vm` puts the annotation on the
# `CloneSpec` deliberately: a clone that appeared carrying the template's marker,
# or none, is a VM another run's preflight misreads, and it would be one for as
# long as a reconfigure took. Moving the marker after the clone *in the product*
# would open exactly that window, to satisfy a simulator. Whether vCenter applies
# a `CloneSpec` annotation is a first-contact question and is #318's.
#
# So it goes on here instead, and **after the deploy has been asserted**: the
# clone's marker is therefore N11 in `tests/test_vsphere_smoke.py`, asserted
# nowhere, and what this buys is the other half of the gate. `vcows destroy`
# finds its targets by marker, so without this the teardown has nothing to
# destroy and `Destroy_Task`, `DeleteDatastoreFile_Task`, the power-off and the
# uuid re-verify are all off the gate.
mark_the_clone() {
    "$PY" - "$ENDPOINT" "$VM" "$DEPLOYMENT" <<'PY'
import sys

from pyVmomi import vim

from orchestrator.backends.vsphere import api
from orchestrator.marker import Marker

endpoint, name, deployment = sys.argv[1:4]
cfg = {
    "target": {
        "vsphere": {
            "endpoint": endpoint,
            "user": "vcows@vsphere.local",
            "password": "not-a-password",
            "insecure": True,
        }
    }
}
with api.connect(cfg) as session:
    vm = api.find_by_name(session.content, vim.VirtualMachine, name)
    if vm is None:
        raise SystemExit(f"{name} is not on the simulator; the deploy did not run")
    api.wait(
        vm.ReconfigVM_Task(
            spec=vim.vm.ConfigSpec(
                annotation=Marker.for_vm(name, deployment).to_description()
            )
        ),
        "annotate",
    )
PY
}

main() {
    local applied destroyed

    # `import orchestrator` resolves by cwd -- the venv installs the RPM bindings
    # but never this project -- so the run has to happen from the tree.
    cd "$REPO"
    need_venv
    WORK="$(mktemp -d)"
    trap cleanup EXIT

    inputs
    start_vcsim

    log "deploying through the shipped CLI against $ENDPOINT"
    vcows deploy "$WORK/config.yaml" --run-dir "$WORK/deploy"
    # The seed ISO as `prepare` wrote it, for the one assertion that compares the
    # bytes vCenter gave back against the bytes that were PUT.
    export VCOWS_VSMOKE_SEED_LOCAL="$WORK/deploy/seed/$VM-seed.iso"

    # Both statuses are captured rather than left to `set -e`: an aborting
    # assertion phase would skip the destroy below, so a single failed needle
    # would cost the teardown assertions too. pytest already reports every
    # failure within a phase.
    applied=0
    asserts TestApplied || applied=$?

    # Between the two phases, so nothing above is asserted against it and
    # nothing below has to work around vcsim. See `mark_the_clone`.
    log "putting back the annotation vcsim dropped from the clone"
    mark_the_clone

    log "tearing down through the shipped CLI"
    vcows destroy "$WORK/config.yaml" --yes --run-dir "$WORK/teardown"
    destroyed=0
    asserts TestDestroyed || destroyed=$?

    if [ "$applied" -ne 0 ] || [ "$destroyed" -ne 0 ]; then
        die "the vSphere smoke gate failed -- see the pytest output above"
    fi
    log "the deploy runs, vcsim accepts every call it makes, and destroy removes it"
}

main "$@"
