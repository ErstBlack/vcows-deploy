"""The apply: what reaches PVE, and what comes back.

Driven through ``ProxmoxBackend.create`` rather than ``create.create`` directly,
because the wiring between ``render`` and the parameters the API is sent is
exactly what a unit test of either half cannot see -- a key renamed on one side
and read on the other passes both.

``FakeProxmox`` dispatches on the API path and records what each call was handed,
so these assert on the calls that were made rather than on the fact that a call
was made. Nothing here needs a cluster; ``tests/test_proxmox_rig.py`` is where a
VM actually boots.
"""

from __future__ import annotations

import logging

import pytest

from orchestrator.backends.base import Prepared
from orchestrator.backends.proxmox import ProxmoxBackend, api
from orchestrator.backends.proxmox import create as create_mod
from orchestrator.marker import Marker
from tests.fake_proxmox import FakeProxmox, ResourceException, upid

IMAGE_BYTES = b"QFI\xfb" + b"\x00" * 508 + b"golden image body"
SEED_BYTES = {"app01": b"app01 seed iso" * 40, "app02": b"app02 seed iso" * 40}


@pytest.fixture(autouse=True)
def _no_polling_delay(monkeypatch):
    """proxmoxer's task poller sleeps once per wait. Fine against a cluster,
    pure latency here."""
    monkeypatch.setattr(api, "POLL_INTERVAL", 0)


@pytest.fixture
def sources(tmp_path, pve_cfg):
    """The files the uploads read. `render` is pure, but `create` opens every
    path the values name.

    Every file is deliberately named something other than what it has to arrive
    on the cluster as, because PVE names the stored file after the multipart
    part -- so an upload that forgets to rename the handle stores the local name
    and every later reference to the volid misses.
    """
    image = tmp_path / "local-copy.qcow2"
    image.write_bytes(IMAGE_BYTES)
    pve_cfg["image"]["source_qcow2"] = str(image)
    seeds = {}
    for name, body in SEED_BYTES.items():
        iso = tmp_path / f"{name}-cidata.iso"
        iso.write_bytes(body)
        seeds[name] = str(iso)
    return seeds


@pytest.fixture
def prepared(sources):
    return Prepared(
        artifacts={
            "seed_isos": sources,
            "image": {"create": True, "volid": "local:import/golden.qcow2"},
        },
    )


@pytest.fixture
def world():
    return FakeProxmox()


@pytest.fixture
def session(world):
    return api.Session(
        prox=world, node="pve1", datastore="local-lvm", import_datastore="local"
    )


def deployed(cfg, session, prepared) -> dict:
    return ProxmoxBackend().create(cfg, session, prepared)


def only_app01(cfg) -> dict:
    """One VM, for the tests whose question is about a single disk."""
    cfg["vms"] = [cfg["vms"][0]]
    return cfg


def config_of(world, vmid: str = "100") -> dict:
    return world.vms[("pve1", vmid)]


def paths(world, verb: str) -> list[str]:
    return [f"{'/'.join(p)}" for v, p in world.calls if v == verb]


# -- the uploads -----------------------------------------------------------


def test_the_image_is_uploaded_as_import_content_when_the_cluster_lacks_it(
    pve_cfg, session, world, prepared
):
    deployed(pve_cfg, session, prepared)

    assert world.uploads[0] == {
        "storage": "local",
        "content": "import",
        "filename": "golden.qcow2",
    }, "the part's name, not the local file's -- see the `sources` fixture"


def test_a_declared_checksum_is_sent_for_pve_to_verify(
    pve_cfg, session, world, prepared
):
    """Optional in the config, so an absent one means "not declared" rather than
    "no checksum" -- and an empty `checksum` sent to PVE is a rejected upload."""
    pve_cfg["image"]["sha256"] = "a" * 64

    deployed(pve_cfg, session, prepared)

    assert world.uploads[0]["checksum"] == "a" * 64
    assert world.uploads[0]["checksum-algorithm"] == "sha256"
    assert "checksum" not in world.uploads[1], "no seed ISO declares one"


def test_the_image_is_not_uploaded_when_the_cluster_already_has_it(
    pve_cfg, session, world, sources
):
    """The second deploy to a cluster. Re-uploading would be a multi-GB no-op,
    and `render` does not even carry a source path in this case."""
    prepared = Prepared(
        artifacts={
            "seed_isos": sources,
            "image": {"create": False, "volid": "local:import/golden.qcow2"},
        },
    )
    deployed(pve_cfg, session, prepared)

    assert [u["content"] for u in world.uploads] == ["iso", "iso"]
    assert "import-from=local:import/golden.qcow2" in config_of(world)["scsi0"], (
        "the volid preflight found is what the disk imports from"
    )


def test_each_seed_iso_is_uploaded_as_iso_content_under_its_own_name(
    pve_cfg, session, world, prepared
):
    deployed(pve_cfg, session, prepared)

    assert [(u["content"], u["filename"]) for u in world.uploads[1:]] == [
        ("iso", "app01-seed.iso"),
        ("iso", "app02-seed.iso"),
    ]
    assert world.content["local"]["iso"] == [
        "local:iso/app01-seed.iso",
        "local:iso/app02-seed.iso",
    ]


# -- the VM ----------------------------------------------------------------


def test_the_created_vm_is_the_body_the_cluster_accepted(
    pve_cfg, session, world, prepared
):
    """The whole body, not one key at a time. This is the shape PVE 8.4.0 took in
    the #198 dry run (M4) with this
    config's names and MAC substituted, so a key renamed or a value flipped here
    is a parameter no cluster was ever measured accepting -- and PVE ignores what
    it does not recognise rather than refusing it.

    Identity is the marker, so a description that mangles it produces a VM no
    later run can prove is ours -- `preflight` reads back exactly this string.
    `size=` and `status` are the fake's: PVE records the imported disk's size on
    the config, and the resize and start rewrite it from there.
    """
    deployed(pve_cfg, session, prepared)

    assert config_of(world) == {
        "vmid": "100",
        "name": "app01",
        "description": Marker.for_vm("app01", "lab-a").to_description(),
        "bios": "ovmf",
        "machine": "q35",
        "onboot": 1,
        "cores": 2,
        "cpu": "host",
        "memory": 4096,
        "ostype": "l26",
        "scsihw": "virtio-scsi-pci",
        "scsi0": (
            "local-lvm:0,import-from=local:import/golden.qcow2,"
            "discard=on,ssd=1,size=40G"
        ),
        "ide2": "local:iso/app01-seed.iso,media=cdrom",
        "boot": "order=scsi0;ide2",
        "efidisk0": "local-lvm:1,efitype=4m",
        "net0": "virtio=52:54:00:be:a8:60,bridge=vmbr0",
        "status": "running",
    }


def test_the_nic_is_attached_the_way_the_config_spelled_it(
    pve_cfg, session, world, prepared
):
    """app02 pins a MAC and a VLAN; app01 has neither, and an untagged NIC must
    not acquire a `tag=` at all."""
    deployed(pve_cfg, session, prepared)

    assert config_of(world)["net0"] == "virtio=52:54:00:be:a8:60,bridge=vmbr0"
    assert (
        config_of(world, "101")["net0"]
        == "virtio=52:54:00:aa:bb:cc,bridge=vmbr0,tag=42"
    )


def test_an_efi_vm_gets_a_varstore_disk(pve_cfg, session, world, prepared):
    """`ovmf` with no `efidisk0` is a VM whose firmware settings do not survive
    a reboot."""
    deployed(pve_cfg, session, prepared)
    assert config_of(world)["efidisk0"] == "local-lvm:1,efitype=4m"


def test_a_bios_vm_gets_no_varstore_disk(pve_cfg, session, world, prepared):
    """`efidisk0` beside `seabios` is a disk nothing ever reads, paid for on the
    datastore."""
    pve_cfg["vms"][0]["firmware"] = "bios"
    deployed(only_app01(pve_cfg), session, prepared)

    assert config_of(world)["bios"] == "seabios"
    assert "efidisk0" not in config_of(world)


def test_every_vm_is_started(pve_cfg, session, world, prepared):
    """Created and off is not deployed."""
    deployed(pve_cfg, session, prepared)

    assert "nodes/pve1/qemu/100/status/start" in paths(world, "post")
    assert [c["status"] for c in world.vms.values()] == ["running", "running"]


# -- the resize ------------------------------------------------------------


def test_the_disk_is_grown_to_the_configured_size(pve_cfg, session, world, prepared):
    """`import-from` gives the disk the golden image's size, so `disk_gb` is
    honoured only by growing it afterwards."""
    deployed(pve_cfg, session, prepared)

    assert paths(world, "put") == [
        "nodes/pve1/qemu/100/resize",
        "nodes/pve1/qemu/101/resize",
    ]
    assert "size=40G" in config_of(world)["scsi0"]
    assert "size=60G" in config_of(world, "101")["scsi0"]


def test_a_disk_already_the_configured_size_is_not_resized(
    pve_cfg, session, world, prepared
):
    world.imported_gb = 40
    deployed(only_app01(pve_cfg), session, prepared)

    assert paths(world, "put") == []


def test_a_disk_smaller_than_the_image_is_refused_rather_than_deployed(
    pve_cfg, session, world, prepared
):
    """PVE cannot shrink a disk. Creating the VM anyway would leave it running at
    a size the config does not describe, which nothing downstream would notice."""
    world.imported_gb = 100

    with pytest.raises(api.ProxmoxApiError) as raised:
        deployed(only_app01(pve_cfg), session, prepared)

    assert str(raised.value) == (
        "could not create vm app01 (100): disk_gb 40 is below the imported "
        "image's 100 GiB and PVE cannot shrink it"
    )
    assert paths(world, "put") == []
    assert "nodes/pve1/qemu/100/status/start" not in paths(world, "post")


def test_a_resize_that_answers_with_no_upid_is_still_a_success(
    pve_cfg, session, world, prepared
):
    """Current PVE answers a resize with a UPID; older ones answer with nothing,
    having already done the work. Waiting on the second answer would turn a
    working cluster into a failed deploy."""
    world.resize_returns_upid = False
    deployed(only_app01(pve_cfg), session, prepared)

    assert paths(world, "put") == ["nodes/pve1/qemu/100/resize"]
    assert "size=40G" in config_of(world)["scsi0"]
    assert world.vms[("pve1", "100")]["status"] == "running"


# -- the disk string PVE answers with --------------------------------------


@pytest.mark.parametrize(
    ("disk", "gb"),
    [
        ("local-lvm:vm-100-disk-0,size=10G", 10),
        ("local-lvm:vm-100-disk-0,size=20480M", 20),
        ("local-lvm:vm-100-disk-0,size=1T", 1024),
        (
            "local-lvm:0,import-from=local:import/golden=v2.qcow2,"
            "discard=on,ssd=1,size=40G",
            40,
        ),
    ],
)
def test_the_imported_size_is_read_back_in_gibibytes(disk, gb):
    """`disk_gb` is compared against this number, so a unit read wrong is a
    resize to the wrong size or a deploy refused for no reason.

    The last case is a file name carrying an `=`: each field splits on its
    first one only, and splitting on every one turns a legal volid into a
    `ValueError` in the middle of a create.
    """
    assert create_mod._size_gb(disk) == gb


def test_a_unit_the_table_does_not_carry_is_refused_rather_than_guessed():
    """G is what an import answers with and T and M are the two the table also
    knows. Anything else raises inside `_made`, which names the VM -- a guessed
    number would resize a disk to a size the config never asked for.
    """
    with pytest.raises(KeyError):
        create_mod._size_gb("local-lvm:vm-100-disk-0,size=1048576K")


# -- the tasks -------------------------------------------------------------


def test_every_task_the_cluster_started_was_waited_on(
    pve_cfg, session, world, prepared
):
    """A UPID that is never polled is a create that reports success while PVE is
    still working, which is how a deploy hands back a VM that has not booted."""
    deployed(pve_cfg, session, prepared)

    # image, two seeds, two creates, two resizes, two starts.
    assert len(world.upids) == 9
    assert len(set(world.upids)) == 9, "each one distinguishable from the others"
    assert set(world.waited) == set(world.upids)


def test_a_failed_task_names_the_vm_and_rolls_nothing_back(
    pve_cfg, session, world, prepared, caplog
):
    """The provider left its leftovers too. Undoing them here would mean deleting
    a VM on a failure path with no state to say which ones this run made -- the
    marker and `preflight._orphan_seeds` are what report them instead.
    """
    world.task_fails = {upid("pve1", "qmcreate", "101")}

    with pytest.raises(api.ProxmoxApiError) as raised:
        deployed(pve_cfg, session, prepared)

    # Whole, not a prefix: the resource is named once and only once, by `_made`.
    # `api.wait` is handed the bare step, so a doubled name fails here.
    assert str(raised.value) == (
        f"could not create vm app02 (101): create: "
        f"task {upid('pve1', 'qmcreate', '101')} ended with 'task failed somehow'"
    ), "the name rides on the exception: run.json's error field is built from it"
    assert config_of(world)["status"] == "running", "app01 is left created and running"
    assert paths(world, "delete") == [], "nothing is torn down"

    failure = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert failure == [str(raised.value)]


def test_a_cluster_that_cannot_allocate_a_vmid_says_which_vm_it_was_for(
    pve_cfg, session, world, prepared
):
    """`/cluster/nextid` is a PVE call like any other and fails like one. Left
    outside `_made` it would escape as proxmoxer's own type naming nothing."""
    world.nextid_error = ResourceException("500 unable to allocate a VM ID")

    with pytest.raises(api.ProxmoxApiError) as raised:
        deployed(only_app01(pve_cfg), session, prepared)

    assert str(raised.value) == (
        "could not create vmid for app01: 500 unable to allocate a VM ID"
    )
    assert paths(world, "post") == ["nodes/pve1/storage/local/upload"] * 2, (
        "the uploads happened; nothing after the vmid did"
    )


def test_each_created_resource_is_logged_with_what_it_cost(
    pve_cfg, session, world, prepared, caplog
):
    """One line per resource, so a slow deploy says which upload was slow."""
    caplog.set_level(logging.INFO, logger=create_mod.log.name)
    deployed(pve_cfg, session, prepared)

    made = [
        r.getMessage()
        for r in caplog.records
        if r.levelname == "INFO" and r.name == create_mod.log.name
    ]
    assert [m.split(" in ")[0] for m in made] == [
        "created image golden.qcow2",
        "created seed app01-seed.iso",
        "created vmid for app01",
        "created vm app01 (100)",
        "created seed app02-seed.iso",
        "created vmid for app02",
        "created vm app02 (101)",
    ]
    assert all(m.endswith("s") for m in made)


# -- what comes back -------------------------------------------------------


def test_the_inventory_is_keyed_by_logical_name_with_the_seed_it_made(
    pve_cfg, session, world, prepared
):
    """The shape `outputs.tf` emitted, unchanged, because `inventory.json` is
    what a site ships back and reads months later. The image is not in `disks`:
    it is shared by every VM on the cluster and is not this VM's to delete."""
    vms = deployed(pve_cfg, session, prepared)

    assert set(vms) == {"app01", "app02"}
    assert vms["app01"] == {
        "name": "app01",
        "vmid": 100,
        "node": "pve1",
        "configured_address": "192.168.122.60",
        "disks": ["local:iso/app01-seed.iso"],
    }
    assert vms["app02"]["vmid"] == 101, "the second VM does not reuse the first's id"
    assert vms["app02"]["configured_address"] == "192.168.122.61"


def test_the_reported_address_is_the_configured_one_not_a_lease(
    pve_cfg, session, world, prepared
):
    """Nothing here asks PVE what address the guest got. The name carries the
    distinction and so does the value."""
    pve_cfg["vms"][0]["nics"][0]["ip_cidr"] = "10.9.9.9/24"
    pve_cfg["vms"][0]["nics"][0]["gateway"] = "10.9.9.1"
    assert deployed(pve_cfg, session, prepared)["app01"]["configured_address"] == (
        "10.9.9.9"
    )


# -- the dependency that is never imported ---------------------------------


def test_requests_toolbelt_is_importable():
    """proxmoxer streams a multipart upload only when it can import this, and
    reads the whole file into memory when it cannot -- a 646 MB golden image
    silently, and anything over 2 GiB as an `OverflowError` mid-deploy. Nothing
    in `orchestrator/` imports it, so this is the only thing that would notice an
    image or a venv built without it.
    """
    import requests_toolbelt

    assert requests_toolbelt.__version__
