"""What libvirtd actually created, asserted against a real daemon.

The other half of `scripts/smoke-libvirt.sh`, and the half that is assertions
rather than host provisioning. The script builds the host, applies the shipped
module against it and destroys it again; this file says what the result has to
look like. It is a gate rather than a test file for the same reason
`tests/test_image.py` is: it needs something no bare `pytest` run has, and a gate
that quietly passes because it did not run is worse than no gate.

**Every needle here is the one the shell matched, character for character.** The
assertions moved; what they assert did not. `virsh dumpxml` and `XMLDesc(0)`
return the same document, so the port is diffable against the shell it replaces
(`#122`). Where the shell was parsing prose rather than matching XML -- `virsh
domstate | grep running`, `virsh dominfo | tr -s ' '`, `virsh vol-list | grep -F`
-- the binding answers directly, and a volume name can no longer match as a
substring of an unrelated one.

**No guest is booted and no guest address is observed**, exactly as before. The
domain reaches firmware and stops there. The defect class `docs/acceptance.md`
records -- guests healthy on the wrong addresses -- is not what this covers.

Run it through `just smoke-libvirt`. Invoking pytest here directly does nothing:
the constants below come from the script, which is also what re-execs under sudo
so that `qemu:///system` is reachable at all.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import gate


def _fact(name: str) -> str:
    """One constant from `scripts/smoke-libvirt.sh`, or "" when it did not run.

    The script stays the single source of truth for all fourteen. It writes the
    tfvars these describe and it tears down what they name, so a second copy here
    would be a fixture maintained in two languages -- and the `""` default is
    what lets this module import cleanly when the gate is not available.
    """
    return os.environ.get(f"VCOWS_SMOKE_{name}", "")


URI = _fact("URI")
POOL = _fact("POOL")
POOL_DIR = _fact("POOL_DIR")
NVRAM_DIR = _fact("NVRAM_DIR")
NETWORK = _fact("NETWORK")
DOMAIN = _fact("DOMAIN")
BASE_VOL = _fact("BASE_VOL")
OVERLAY_VOL = _fact("OVERLAY_VOL")
SEED_VOL = _fact("SEED_VOL")
MARKER_ID = _fact("MARKER_ID")
MAC = _fact("MAC")
LOADER = _fact("LOADER")
NVRAM_TEMPLATE = _fact("NVRAM_TEMPLATE")
WORK = _fact("WORK")
#: "1" or "0", written by the script's `probe_pinned_loader_escapes_autoselection`.
PROBE_DEFINED = _fact("PROBE_DEFINED")

# root is part of the predicate, not an assumption, for the reason podman is part
# of the image gate's: qemu:///system is root's socket, so a non-root run would
# report the gate available and then die inside `libvirt.open` with a connection
# error, which reads as a broken suite rather than as a gate that was not
# supplied. The script re-execs under sudo before it gets here.
pytestmark = gate(
    "smoke",
    all(
        (
            URI,
            POOL,
            POOL_DIR,
            NVRAM_DIR,
            NETWORK,
            DOMAIN,
            BASE_VOL,
            OVERLAY_VOL,
            SEED_VOL,
            MARKER_ID,
            MAC,
            LOADER,
            NVRAM_TEMPLATE,
            WORK,
            PROBE_DEFINED,
        )
    )
    and os.geteuid() == 0,
    "run `just smoke-libvirt` rather than pytest: the script exports every "
    "VCOWS_SMOKE_* constant this file reads, applies the module they describe, "
    "and re-execs under sudo so qemu:///system is reachable",
)


@pytest.fixture(scope="module")
def conn():
    """The daemon the module was applied against.

    `import libvirt` inside the fixture rather than at module scope, following
    `tests/test_libvirt_rig.py`: the import is the gate's dependency, not this
    file's, and collection must not turn on it.
    """
    import libvirt

    connection = libvirt.open(URI)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(scope="module")
def domain(conn):
    return conn.lookupByName(DOMAIN)


@pytest.fixture(scope="module")
def domain_xml(domain) -> str:
    """One capture, read by every assertion below -- `virsh dumpxml`'s document.

    The running domain's XML, not the persistent config: libvirt writes `<source
    file='...' index='2'/>` for a domain that is up, and two of the needles below
    exist because of it.
    """
    return domain.XMLDesc(0)


@pytest.fixture(scope="module")
def pool(conn):
    return conn.storagePoolLookupByName(POOL)


@pytest.fixture
def volumes(pool) -> list[str]:
    """The pool's volume names, re-read each time.

    `virsh vol-list` re-read the pool on every call; the binding caches, and a
    stale list is the shape of a gate that passes over what destroy left behind.
    """
    pool.refresh(0)
    return pool.listVolumes()


def qemu_img(path: str) -> str:
    """`qemu-img info` on a pool file. Not a libvirt call, so still a subprocess.

    `-U`, and it is not cosmetic. The domain is running, so QEMU holds a write
    lock on the overlay and `qemu-img info` without it fails with `Failed to get
    shared "write" lock` -- which is what stopped the fourth CI run. The base only
    escaped because a backing file is opened read-only. Both callers go through
    here so the two cannot drift apart.
    """
    done = subprocess.run(
        ["qemu-img", "info", "-U", path], capture_output=True, text=True, check=False
    )
    return done.stdout + done.stderr


# -- the domain XML, needle by needle ----------------------------------------

#: `(what it proves, the literal the shell matched)`. Each pair is one `check`
#: line from `assert_domain`, and pytest reports one result per pair, which is
#: what the hand-rolled ok/FAIL accumulator existed to provide.
PRESENT = [
    ("the domain runs under TCG, not KVM", "<domain type='qemu'"),
    ("the marker survived DomainDefineXML", "urn:vcows:1"),
    ("the marker carries the id destroy discovers by", MARKER_ID),
    # The firmware pin, read off what libvirtd stored rather than off the plan.
    # The second needle is the whole varstore element -- template attribute,
    # directory and .fd suffix -- so it is also what proves the element is there
    # for the two absences below to mean anything.
    (
        "the pinned raw loader reached the domain verbatim",
        f"<loader readonly='yes' type='pflash'>{LOADER}</loader>",
    ),
    (
        "the varstore path follows the raw template's suffix",
        f"template='{NVRAM_TEMPLATE}'>{NVRAM_DIR}/{DOMAIN}_VARS.fd<",
    ),
    ("acpi reached the domain", "<acpi/>"),
    ("apic reached the domain", "<apic/>"),
    (
        "the hpet timer is off, as this host's own guests have it",
        "<timer name='hpet' present='no'/>",
    ),
    ("the guest clock follows the host in UTC", "<clock offset='utc'>"),
    ("the overlay disk passes discard=unmap", "discard='unmap'"),
    # No trailing `/>` on these two. libvirt writes `<source file='...'
    # index='2'/>` for a running domain, so matching the self-closing form
    # asserted the index rather than the path -- measured, and the only two
    # assertions the fifth CI run failed. The path is the claim: destroy parses
    # `<source file=>`, and a module emitting a volume name rather than a
    # computed path is what this is guarding against.
    (
        "the root disk is the overlay's path, not its name",
        f"<source file='{POOL_DIR}/{OVERLAY_VOL}'",
    ),
    ("the cdrom is the seed volume's path", f"<source file='{POOL_DIR}/{SEED_VOL}'"),
    ("the root disk is vda on virtio", "<target dev='vda' bus='virtio'/>"),
    ("the seed is sda on sata", "<target dev='sda' bus='sata'/>"),
    ("the seed is read-only", "<readonly/>"),
    ("the domain carries a virtio-rng reading /dev/urandom", "/dev/urandom"),
    ("the NIC carries the derived MAC", f"<mac address='{MAC}'/>"),
    (f"the NIC is on the {NETWORK} network", f"<source network='{NETWORK}'"),
]

#: The absences, which are assertions about what the module must *not* emit.
#:
#: None is scoped to the `<nvram>` line. `format=` and `templateFormat=` appear
#: on no other element of this domain -- the disks carry `<driver type=>`, not a
#: format attribute -- and an absence checked against an extracted line that came
#: back empty would pass without having checked anything.
#:
#: **No entry here for `firmware='efi'`, deliberately, and the attempt is
#: recorded so it is not made a third time.** `#141` fixed `#107` by stopping the
#: module emitting `firmware = "efi"` beside a pin, but libvirt fills the
#: attribute back into the stored XML when the pinned loader matches a descriptor
#: it can name -- so an absence FAILs against this raw `.fd` pin (CI run
#: 33436774063, and again here on 33438908683) while passing against a qcow2 one
#: (run 33437247928). Nothing in this capture distinguishes "the module sent it"
#: from "libvirt deduced it". `test_a_pinned_loader_escapes_autoselection` below
#: carries that instead, and `libvirt-module.tftest.hcl` carries what the module
#: emits.
ABSENT = [
    # #75's other half, and the half no offline gate can reach. The mock
    # satisfies the schema with generated values and never reads anything back,
    # so libvirt-module.tftest.hcl can only pin that the module stopped emitting
    # these two attributes -- never that emitting them was wrong. These two are
    # that evidence, and the apply is the regression gate: put either attribute
    # back in main.tf and `tofu apply` exits 1 with "Provider produced
    # inconsistent result after apply", after all three volumes exist.
    (
        "libvirt omits format='raw' from the varstore, which is why the module "
        "must not declare it",
        "format='raw'",
    ),
    (
        "libvirt omits templateFormat from the varstore, for every value",
        "templateFormat",
    ),
]


class TestApplied:
    """After `tofu apply`, and while the domain is running."""

    # -- the volumes ---------------------------------------------------------

    @pytest.mark.parametrize(
        "volume", [BASE_VOL, OVERLAY_VOL, SEED_VOL], ids=["base", "overlay", "seed"]
    )
    def test_the_volume_exists_in_the_pool(self, volumes, volume):
        assert volume in volumes

    def test_the_upload_wrote_a_real_qcow2_header_into_the_base_volume(self):
        """The upload is the assertion. A volume that was allocated and never
        written is zeros, and qemu-img calls zeros `raw`; only a real transfer of
        the qcow2 header by virStorageVolUpload makes this say qcow2.
        """
        assert "file format: qcow2" in qemu_img(f"{POOL_DIR}/{BASE_VOL}")

    def test_the_overlay_backs_onto_the_base_volume_on_disk(self):
        """The chain, read off the file rather than off the plan. The mock can
        only compare two generated strings to each other.
        """
        assert f"backing file: {POOL_DIR}/{BASE_VOL}" in qemu_img(
            f"{POOL_DIR}/{OVERLAY_VOL}"
        )

    def test_libvirt_detects_the_seed_volume_as_iso(self, pool):
        """libvirt inspects uploaded content and reports the format it detects.
        The module declares `iso` for exactly that reason -- declaring `raw` made
        the provider's post-apply read disagree with its own plan, after the
        volume had already been written. Nothing but a real libvirtd can say
        whether that is still true of this provider and this libvirt.
        """
        seed = pool.storageVolLookupByName(SEED_VOL)
        assert "<format type='iso'/>" in seed.XMLDesc(0)

    # -- the domain ----------------------------------------------------------

    @pytest.mark.parametrize(
        "needle", [needle for _, needle in PRESENT], ids=[what for what, _ in PRESENT]
    )
    def test_the_domain_xml_carries(self, domain_xml, needle):
        assert needle in domain_xml

    @pytest.mark.parametrize(
        "needle", [needle for _, needle in ABSENT], ids=[what for what, _ in ABSENT]
    )
    def test_the_domain_xml_omits(self, domain_xml, needle):
        assert needle not in domain_xml

    def test_the_domain_is_running(self, domain):
        import libvirt

        assert domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING

    def test_the_domain_is_set_to_autostart(self, domain):
        assert domain.autostart() == 1

    # -- #107, which is a libvirt property rather than a module one -----------

    def test_a_pinned_loader_escapes_autoselection(self):
        """A qcow2 pin defines with no `firmware` attribute beside it, on a host
        whose four descriptors declare only raw.

        The verdict of `probe_pinned_loader_escapes_autoselection`, which defines
        one throwaway domain out of band of the module before the apply. That
        work stays in the script; only the result crosses. `#141`'s fix is worth
        something only while omitting the attribute keeps a pin out of
        autoselection's validation, and that is a property of libvirt, not of
        anything this repo controls. Before `#141` the same configuration was
        refused at define with "Unable to find 'efi' firmware that is compatible
        with the current configuration" (CI runs 33374365926, 33374623746).

        Define is the whole test -- no start, no boot, no KVM -- because define
        is where the descriptor match happens. The script logs the refusal text,
        so a define that failed for an unrelated reason is told apart there
        rather than read as a `#107` regression here.
        """
        assert PROBE_DEFINED == "1"

    # -- the refresh read ----------------------------------------------------

    def test_the_applied_domain_re_reads_clean(self):
        """No attribute drifts on refresh.

        A successful apply proves the provider's *create* read agreed with its
        plan. Nothing else proves its *refresh* read does, and a disagreement
        there is #75 one step later: not a failed apply but a permanent diff that
        every subsequent plan re-proposes and no apply can settle.
        `-detailed-exitcode` is 0 for no changes, 1 for an error and 2 for a diff,
        so the assertion is on the number.
        """
        done = subprocess.run(
            ["tofu", f"-chdir={WORK}", "plan", "-detailed-exitcode", "-input=false"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr


class TestDestroyed:
    """After `tofu destroy`, and before the script's own cleanup trap.

    The subject here is the provider's destroy. Running after cleanup's
    `undefine --nvram` and `vol-delete` would assert cleanup's work instead.
    """

    def test_destroy_undefined_the_domain(self, conn):
        assert DOMAIN not in [d.name() for d in conn.listAllDomains()]

    @pytest.mark.parametrize(
        "volume", [BASE_VOL, OVERLAY_VOL, SEED_VOL], ids=["base", "overlay", "seed"]
    )
    def test_destroy_removed_the_volume(self, volumes, volume):
        assert volume not in volumes

    def test_destroy_removed_the_varstore(self):
        """A file rather than a libvirt object, so a path test and not an absence
        over a listing like the four above.
        """
        assert not Path(NVRAM_DIR, f"{DOMAIN}_VARS.fd").exists()
