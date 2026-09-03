"""What libvirtd actually created, asserted against a real daemon.

The other half of `scripts/smoke-libvirt.sh`, and the half that is assertions
rather than host provisioning. The script builds the host, runs the shipped
`create` against it and then the shipped `destroy`; this file says what the
result has to look like. It is a gate rather than a test file for the same reason
`tests/test_image.py` is: it needs something no bare `pytest` run has, and a gate
that quietly passes because it did not run is worse than no gate.

**Every needle here is still the one the shell matched, character for
character**, with one exception and one narrowing, both forced by the move off
the OpenTofu provider (`#204`) and both marked where they are: `create.py`
declares `format='raw'` on the loader where the module passed null, and whether
libvirt hands that attribute back depends on its version, so the loader needle
accepts both measured echoes and the two absences are scoped to the `<nvram>`
line they were always about.

The subject changed; what is asserted about it did not. These are assertions
about a document libvirtd stored, and libvirtd does not know which client sent
it.

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

    The script stays the single source of truth for all thirteen. It writes the
    values these describe and it tears down what they name, so a second copy here
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
            PROBE_DEFINED,
        )
    )
    and os.geteuid() == 0,
    "run `just smoke-libvirt` rather than pytest: the script exports every "
    "VCOWS_SMOKE_* constant this file reads, creates the VM they describe, and "
    "re-execs under sudo so qemu:///system is reachable",
)


@pytest.fixture(scope="module")
def conn():
    """The daemon the create ran against.

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
    """One capture, read by almost every assertion below -- `virsh dumpxml`'s
    document.

    The running domain's XML, not the persistent config: libvirt writes `<source
    file='...' index='2'/>` for a domain that is up, and two of the needles below
    exist because of it.
    """
    return domain.XMLDesc(0)


@pytest.fixture(scope="module")
def varstore(domain_xml) -> str:
    """The stored `<nvram>` line, which is what the two absences are about.

    Exactly one, and a failure rather than an empty string when there is none:
    an absence checked against a line that came back empty passes without having
    checked anything.
    """
    lines = [line for line in domain_xml.splitlines() if "<nvram " in line]
    assert len(lines) == 1, domain_xml
    return lines[0]


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
    #
    # The loader needle is the one that changed with #204: the module passed
    # `format = null` for a raw loader, where `create.firmware_xml` declares the
    # format it was given. What libvirt echoes for a raw loader is a version
    # fact, measured: 11.1.0 (the dev box, test driver) stores `format='raw'`;
    # 10.0.0 (this gate's runner, qemu driver, after the descriptor match that
    # fills `firmware='efi'` back) stores the element without it. Both are the
    # pinned loader reaching the domain verbatim, so either form passes; a
    # needle is a string or a tuple of acceptable strings.
    (
        "the pinned raw loader reached the domain verbatim",
        (
            f"<loader readonly='yes' type='pflash'>{LOADER}</loader>",
            f"<loader readonly='yes' type='pflash' format='raw'>{LOADER}</loader>",
        ),
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
    # `<source file=>`, and a create emitting a volume name rather than the path
    # the pool gave it is what this is guarding against.
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

#: The absences, and both are matched against the `<nvram>` line rather than the
#: whole document.
#:
#: They used to be matched against the whole document, on the argument that
#: `format=` and `templateFormat=` appeared on no other element of it. The loader
#: beside the varstore now carries `format='raw'` -- see the needle above -- so
#: that argument is gone and a document-wide search for it would match the loader
#: and say nothing about the varstore. The `varstore` fixture is what keeps the
#: narrowing honest: it refuses to hand back a line that is not there.
#:
#: What they prove is one claim each about the stored varstore: that
#: `create.firmware_xml` writes no format attribute on a raw one, and that
#: libvirt fills neither in. The second is not the first -- libvirt filling an
#: attribute back is exactly what no offline test can see, and it is what makes
#: the `firmware='efi'` paragraph below a paragraph rather than an entry.
#:
#: **No entry here for `firmware='efi'`, deliberately, and the attempt is
#: recorded so it is not made a third time.** `#141` fixed `#107` by stopping
#: `firmware = "efi"` being emitted beside a pin, but libvirt fills the attribute
#: back into the stored XML when the pinned loader matches a descriptor it can
#: name -- so an absence FAILs against this raw `.fd` pin (CI run 33436774063,
#: and again here on 33438908683) while passing against a qcow2 one (run
#: 33437247928). Nothing in this capture distinguishes "vcows sent it" from
#: "libvirt deduced it". `test_a_pinned_loader_escapes_autoselection` below
#: carries that instead, and `tests/test_libvirt_create.py` carries what
#: `create.firmware_xml` emits.
ABSENT = [
    # #75's other half, and the half no offline gate can reach.
    # `tests/fake_libvirt.py` records the XML it is handed and never reads
    # anything back, so it can only pin that vcows does not declare these two --
    # never what libvirtd does with the varstore afterwards.
    (
        "libvirt omits format='raw' from the varstore, which is why "
        "firmware_xml must not declare it",
        "format='raw'",
    ),
    (
        "libvirt omits templateFormat from the varstore, for every value",
        "templateFormat",
    ),
]


class TestApplied:
    """After `create.create`, and while the domain is running."""

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
        """The chain, read off the file rather than off the XML that asked for
        it. `tests/fake_libvirt.py` can only compare two strings this repo wrote.
        """
        assert f"backing file: {POOL_DIR}/{BASE_VOL}" in qemu_img(
            f"{POOL_DIR}/{OVERLAY_VOL}"
        )

    def test_libvirt_detects_the_seed_volume_as_iso(self, pool):
        """libvirt inspects uploaded content and reports the format it detects.
        `create.upload` declares `iso` for exactly that reason -- declaring `raw`
        made the provider's post-apply read disagree with its own plan, after the
        volume had already been written. The client changed and the detection did
        not: nothing but a real libvirtd can say what it makes of these bytes.
        """
        seed = pool.storageVolLookupByName(SEED_VOL)
        assert "<format type='iso'/>" in seed.XMLDesc(0)

    # -- the domain ----------------------------------------------------------

    @pytest.mark.parametrize(
        "needle", [needle for _, needle in PRESENT], ids=[what for what, _ in PRESENT]
    )
    def test_the_domain_xml_carries(self, domain_xml, needle):
        forms = (needle,) if isinstance(needle, str) else needle
        assert any(form in domain_xml for form in forms), forms

    @pytest.mark.parametrize(
        "needle", [needle for _, needle in ABSENT], ids=[what for what, _ in ABSENT]
    )
    def test_the_varstore_omits(self, varstore, needle):
        assert needle not in varstore

    def test_the_domain_is_running(self, domain):
        import libvirt

        assert domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING

    def test_the_domain_is_set_to_autostart(self, domain):
        assert domain.autostart() == 1

    # -- #107, which is a libvirt property rather than one of ours ------------

    def test_a_pinned_loader_escapes_autoselection(self):
        """A qcow2 pin defines with no `firmware` attribute beside it, on a host
        whose four descriptors declare only raw.

        The verdict of `probe_pinned_loader_escapes_autoselection`, which defines
        one throwaway domain out of band of the create, and before it. That
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


class TestDestroyed:
    """After `destroy.destroy`, and before the script's own cleanup trap.

    The subject here is the teardown `vcows destroy` runs. Running after
    cleanup's `undefine --nvram` and `vol-delete` would assert cleanup's work
    instead.
    """

    def test_destroy_undefined_the_domain(self, conn):
        assert DOMAIN not in [d.name() for d in conn.listAllDomains()]

    @pytest.mark.parametrize("volume", [OVERLAY_VOL, SEED_VOL], ids=["overlay", "seed"])
    def test_destroy_removed_the_volume(self, volumes, volume):
        assert volume not in volumes

    def test_destroy_left_the_base_volume_alone(self, volumes):
        """The golden image is shared, and nothing marks it as ours.

        `tofu destroy` deleted it -- the provider had created it and held it in
        state, and `libvirt_volume.base`'s `count` guard protects it in config
        only. This is the same object and the opposite claim: `destroy` resolves
        what to delete from the marker, `_deletable` allows only the two names
        the marker's own name derives, and volumes carry no marker at all. A
        teardown that took the base with it would take every other deployment's
        base image on that host.
        """
        assert BASE_VOL in volumes

    def test_destroy_removed_the_varstore(self):
        """A file rather than a libvirt object, so a path test and not an absence
        over a listing like the three above.
        """
        assert not Path(NVRAM_DIR, f"{DOMAIN}_VARS.fd").exists()
