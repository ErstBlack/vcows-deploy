"""The apply: what reaches the hypervisor, and what comes back.

Driven through ``LibvirtBackend.create`` rather than ``create.create`` directly,
because the wiring between ``render`` and the XML templates is exactly what a
unit test of either half cannot see -- a key renamed on one side and read on the
other passes both.

The fake records every argument it is handed, so these assert on the calls that
were made rather than on the fact that a call was made. Nothing here needs a real
hypervisor; ``tests/test_libvirt_rig.py`` is where a domain actually boots.
"""

from __future__ import annotations

import pytest

from orchestrator.backends.base import Prepared
from orchestrator.backends.libvirt import LibvirtBackend
from orchestrator.backends.libvirt import create as create_mod
from tests.fake_libvirt import FakeConnection, FakePool, lv_error

BASE_BYTES = b"QFI\xfb" + b"\x00" * 508 + b"golden image body"
SEED_BYTES = {"app01": b"app01 seed iso" * 40, "app02": b"app02 seed iso" * 40}


@pytest.fixture
def sources(tmp_path, cfg):
    """The files the upload reads. `render` is pure, but `create` is not: it
    stats and reads every path the values name."""
    base = tmp_path / "golden.qcow2"
    base.write_bytes(BASE_BYTES)
    cfg["image"]["source_qcow2"] = str(base)
    seeds = {}
    for name, body in SEED_BYTES.items():
        iso = tmp_path / f"{name}-seed.iso"
        iso.write_bytes(body)
        seeds[name] = str(iso)
    return seeds


@pytest.fixture
def prepared(sources):
    return Prepared(
        artifacts={
            "seed_isos": sources,
            "base_volume": {"name": "golden.qcow2", "create": True, "path": ""},
        },
    )


@pytest.fixture
def pool():
    return FakePool("images", {}, path="/var/lib/libvirt/images")


@pytest.fixture
def conn(pool):
    return FakeConnection(pools=[pool])


def deployed(cfg, conn, prepared) -> dict:
    return LibvirtBackend().create(cfg, conn, prepared)


def domain_of(conn, name: str) -> str:
    """The document one domain was defined from, out of a finished create."""
    import libvirt

    (dom,) = [d for d in conn.domains if d.name() == name]
    return dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)


def defined(cfg, conn, prepared, name: str) -> str:
    deployed(cfg, conn, prepared)
    return domain_of(conn, name)


# -- the base volume -------------------------------------------------------


def test_the_base_image_is_uploaded_when_the_host_does_not_have_it(
    cfg, conn, pool, prepared
):
    deployed(cfg, conn, prepared)

    assert pool.created[0] == "golden.qcow2", "made before anything backs onto it"
    length, stream = pool.uploads["golden.qcow2"]
    assert stream.data == BASE_BYTES, "the bytes are the source file's, whole"
    assert length == len(BASE_BYTES)
    assert stream.finished, "an unfinished stream leaves the volume half-written"


def test_the_base_image_is_not_uploaded_when_the_host_already_has_it(cfg, conn, pool):
    """The second deploy to a host. Re-uploading would be a multi-GB no-op at
    best, and at worst would overwrite the image every other overlay backs onto.
    """
    prepared = Prepared(
        artifacts={
            "seed_isos": {"app01": "/nonexistent", "app02": "/nonexistent"},
            "base_volume": {
                "name": "golden.qcow2",
                "create": False,
                "path": "/var/lib/libvirt/images/golden.qcow2",
            },
        },
    )
    cfg["vms"] = []

    assert deployed(cfg, conn, prepared) == {}
    assert pool.created == []
    assert pool.uploads == {}


def test_the_capacity_declared_for_the_base_is_the_source_files_size(
    cfg, conn, pool, prepared
):
    """Spike A4: whatever is declared here is discarded when libvirt reads the
    uploaded qcow2 header, so the honest number is the file's own size."""
    deployed(cfg, conn, prepared)
    assert (
        f"<capacity unit='bytes'>{len(BASE_BYTES)}</capacity>"
        in (pool.volumes["golden.qcow2"])
    )


# -- the per-VM volumes ----------------------------------------------------


def test_the_overlay_backs_onto_the_base_and_carries_the_configured_capacity(
    cfg, conn, pool, prepared
):
    deployed(cfg, conn, prepared)

    overlay = pool.volumes["app01.qcow2"]
    assert (
        "<backingStore><path>/var/lib/libvirt/images/golden.qcow2</path>"
        "<format type='qcow2'/></backingStore>" in overlay
    )
    assert f"<capacity unit='bytes'>{40 * 1024**3}</capacity>" in overlay
    assert "<format type='qcow2'/>" in overlay
    # The one place `disk_gb` survives, so it must not also be on the base.
    assert f"{40 * 1024**3}" not in pool.volumes["golden.qcow2"]


def test_the_overlay_backs_onto_the_path_the_host_already_had(cfg, conn, pool, sources):
    """`create: False` means the backing path comes from what preflight found,
    not from a volume this run made."""
    prepared = Prepared(
        artifacts={
            "seed_isos": sources,
            "base_volume": {
                "name": "golden.qcow2",
                "create": False,
                "path": "/somewhere/else/golden.qcow2",
            },
        },
    )
    deployed(cfg, conn, prepared)
    assert "<path>/somewhere/else/golden.qcow2</path>" in pool.volumes["app01.qcow2"]


def test_the_seed_iso_is_uploaded_raw_and_whole(cfg, conn, pool, prepared):
    deployed(cfg, conn, prepared)

    assert "<format type='iso'/>" in pool.volumes["app01-seed.iso"]
    length, stream = pool.uploads["app01-seed.iso"]
    assert stream.data == SEED_BYTES["app01"]
    assert length == len(SEED_BYTES["app01"])


# -- the domain ------------------------------------------------------------


def test_the_defined_domain_carries_the_marker_byte_identical(cfg, conn, prepared):
    """Identity is the marker, so a document that mangles it produces a VM no
    later run can prove is ours -- and `preflight` reads back exactly this
    string."""
    from orchestrator.backends.libvirt.render import render

    marker = render(cfg, prepared)["vms"]["app01"]["marker_xml"]
    deployed(cfg, conn, prepared)
    assert f"<metadata>{marker}</metadata>" in domain_of(conn, "app01")


def test_the_domain_carries_the_disks_the_run_just_made(cfg, conn, prepared):
    xml = defined(cfg, conn, prepared, "app01")
    assert "<source file='/var/lib/libvirt/images/app01.qcow2'/>" in xml
    assert "<source file='/var/lib/libvirt/images/app01-seed.iso'/>" in xml


def test_autoselected_firmware_pins_nothing(cfg, conn, prepared):
    """app01 sets no loader, so libvirt picks the descriptors itself. The
    exclusivity matters: a `<loader>` beside `firmware='efi'` is refused."""
    xml = defined(cfg, conn, prepared, "app01")
    assert "<os firmware='efi'>" in xml
    assert "<loader" not in xml and "<nvram" not in xml


def test_a_pinned_loader_replaces_the_autoselection_and_names_its_varstore(
    cfg, conn, prepared
):
    """app02 pins Fedora's qcow2 OVMF. The varstore suffix follows the format --
    an `.fd` varstore against a qcow2 loader is the mismatch acceptance paid for.
    """
    xml = defined(cfg, conn, prepared, "app02")
    assert "firmware='efi'" not in xml
    assert (
        "<loader readonly='yes' type='pflash' format='qcow2'>"
        "/usr/share/edk2/ovmf/OVMF_CODE_4M.qcow2</loader>" in xml
    )
    assert (
        "<nvram template='/usr/share/edk2/ovmf/OVMF_VARS_4M.qcow2' format='qcow2'>"
        f"{create_mod.NVRAM_DIR}/app02_VARS.qcow2</nvram>" in xml
    )


def test_a_loader_with_no_template_asks_for_no_varstore():
    """`schema` refuses this pair in a config, so it reaches here only from a
    values dict built by hand. The branch still decides something: a `<nvram>`
    path with no template behind it is a define libvirt refuses."""
    firmware, loader = create_mod.firmware_xml(
        {
            "firmware": "efi",
            "loader": "/usr/share/OVMF/OVMF_CODE.fd",
            "loader_format": "raw",
            "nvram_template": None,
            "domain_name": "app01",
        }
    )
    assert firmware == ""
    assert "<nvram" not in loader
    assert loader.endswith("/usr/share/OVMF/OVMF_CODE.fd</loader>\n")


def test_bios_asks_for_no_firmware_at_all(cfg, conn, prepared):
    cfg["vms"][0]["firmware"] = "bios"
    xml = defined(cfg, conn, prepared, "app01")
    assert "<os>" in xml
    assert "firmware=" not in xml


def test_the_nic_is_attached_the_way_the_config_spelled_it(cfg, conn, prepared):
    cfg["vms"][0]["nics"][0] = {
        "bridge": "br0",
        "ip_cidr": "192.168.122.60/24",
        "gateway": "192.168.122.1",
    }
    xml = defined(cfg, conn, prepared, "app01")
    assert "<interface type='bridge'>" in xml
    assert "<source bridge='br0'/>" in xml
    assert "<source network=" not in xml


def test_every_domain_is_autostarted_and_started(cfg, conn, prepared):
    """Defined and off is not deployed, and a host reboot must bring it back."""
    deployed(cfg, conn, prepared)

    for dom in conn.domains:
        assert dom.autostart() == 1
        assert dom.active
        assert dom.log == ["autostart:1", "start"], "autostart is set before the start"


# -- what comes back -------------------------------------------------------


def test_the_inventory_is_keyed_by_logical_name_with_both_disks(cfg, conn, prepared):
    """The shape `outputs.tf` emitted, unchanged, because `inventory.json` is
    what a site ships back and reads months later."""
    vms = deployed(cfg, conn, prepared)

    assert set(vms) == {"app01", "app02"}
    assert vms["app01"] == {
        "name": "app01",
        "uuid": conn.domains[0].UUIDString(),
        "configured_address": "192.168.122.60",
        "disks": [
            "/var/lib/libvirt/images/app01.qcow2",
            "/var/lib/libvirt/images/app01-seed.iso",
        ],
    }
    assert vms["app02"]["configured_address"] == "192.168.122.61"


def test_the_reported_address_is_the_configured_one_not_a_lease(cfg, conn, prepared):
    """Nothing here asks libvirt what address the guest got. The name carries the
    distinction and so does the value."""
    cfg["vms"][0]["nics"][0]["ip_cidr"] = "10.9.9.9/24"
    cfg["vms"][0]["nics"][0]["gateway"] = "10.9.9.1"
    assert deployed(cfg, conn, prepared)["app01"]["configured_address"] == "10.9.9.9"


# -- failure ---------------------------------------------------------------


def test_a_define_failure_names_the_vm_and_rolls_nothing_back(
    cfg, conn, pool, prepared, caplog
):
    """The provider left its leftovers too. Undoing them here would mean deleting
    volumes on a failure path with no state to say which ones this run made --
    the marker and `preflight.orphan_volumes` are what report them instead.
    """
    import libvirt

    conn.define_errors["app02"] = lv_error(1, "XML error: something in app02")

    with pytest.raises(libvirt.libvirtError) as raised:
        deployed(cfg, conn, prepared)

    assert [d.name() for d in conn.domains] == ["app01"], "app01 is left defined"
    assert conn.domains[0].active, "and left running"
    assert "app02.qcow2" in pool.volumes, "its volumes are left for preflight to find"
    assert pool.deleted == []

    assert str(raised.value) == (
        "could not create domain app02: XML error: something in app02"
    ), "the name rides on the exception: run.json's error field is built from it"
    assert raised.value.get_error_code() == 1, "and the code survives the rewrite"

    failure = [r for r in caplog.records if r.levelname == "ERROR"]
    assert [r.getMessage() for r in failure] == [str(raised.value)]


def test_a_send_that_fails_partway_aborts_the_stream(cfg, conn, prepared):
    """`virStreamSendAll` aborts the stream itself only when the *handler* raises
    inside its loop. A daemon that refuses a write does not go through the
    handler, and an unaborted stream leaves the daemon holding a half-written
    volume open until the connection drops."""
    import libvirt

    conn.send_error = lv_error(38, "cannot write to stream")

    with pytest.raises(libvirt.libvirtError) as raised:
        deployed(cfg, conn, prepared)

    (stream,) = conn.streams
    assert stream.aborted
    assert not stream.finished, "an aborted stream is not also a finished one"
    assert str(raised.value).startswith("could not create base volume golden.qcow2: ")


def test_an_upload_the_daemon_refuses_aborts_the_stream(cfg, conn, pool, prepared):
    """The stream is live from `vol.upload` onwards, not from the first byte, so
    a refusal there leaves the same half-written volume a failed send does."""
    import libvirt

    pool.volume_upload_error = lv_error(38, "cannot start upload")

    with pytest.raises(libvirt.libvirtError) as raised:
        deployed(cfg, conn, prepared)

    (stream,) = conn.streams
    assert stream.aborted
    assert not stream.finished, "an aborted stream is not also a finished one"
    assert str(raised.value).startswith("could not create base volume golden.qcow2: ")


def test_each_created_resource_is_logged_with_what_it_cost(cfg, conn, prepared, caplog):
    """One line per resource, so a slow deploy says which upload was slow."""
    import logging

    caplog.set_level(logging.INFO, logger=create_mod.log.name)
    deployed(cfg, conn, prepared)

    made = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert [m.split(" in ")[0] for m in made] == [
        "created base volume golden.qcow2",
        "created seed volume app01-seed.iso",
        "created overlay volume app01.qcow2",
        "created domain app01",
        "created seed volume app02-seed.iso",
        "created overlay volume app02.qcow2",
        "created domain app02",
    ]
    assert all(m.endswith("s") for m in made)
