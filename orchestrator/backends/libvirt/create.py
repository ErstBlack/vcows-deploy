"""The apply, through python3-libvirt. Not `tofu apply`.

One function per resource the OpenTofu module declared, in the order its
dependency edges imposed: the base volume when the host does not already have
it, then per VM a seed ISO, an overlay and a domain. Ported from the #198 spike,
which created a VM on the rig, booted it, and had it torn down by the shipped
``vcows destroy`` -- so the XML here is the XML that was measured, and it mirrors
``libvirt_domain.vm`` block by block, including the firmware exclusivity that
resource's ``os`` block was commented for.

**The upload is dense, and there is no second path.** On the golden image
``vol.upload`` plus ``stream.sendAll`` and
``VIR_STORAGE_VOL_UPLOAD_SPARSE_STREAM`` plus ``sparseSendAll`` both took 2.5 s
(tofu-eval M3): 617 MiB is allocated of 646 MB, so there are no holes for the
sparse stream to skip, and it would be a second code path earning nothing.

**Nothing is rolled back.** A ``libvirtError`` is re-raised with the resource
it failed on named in its message, which is what the provider did too -- state is
what let tofu resume, and here the marker plus ``preflight.orphan_volumes`` is
what lets a later run see the leftovers.

``import libvirt`` stays inside the functions that need it, for the reason
``__init__`` gives: importing the registry must not drag the binding in.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

VOLUME_XML = """<volume type='file'>
  <name>{name}</name>
  <capacity unit='bytes'>{capacity}</capacity>
  <target><format type='{fmt}'/></target>
  {backing}
</volume>"""

BACKING_XML = "<backingStore><path>{path}</path><format type='qcow2'/></backingStore>"

DOMAIN_XML = """<domain type='kvm'>
  <name>{domain_name}</name>
  <memory unit='MiB'>{memory_mib}</memory>
  <vcpu>{vcpus}</vcpu>
  <metadata>{marker_xml}</metadata>
  <os{firmware_attr}>
    <type arch='x86_64' machine='{machine}'>hvm</type>
{loader_xml}    <boot dev='hd'/>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough'/>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='{overlay}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
{interfaces}    <serial type='pty'><target port='0'/></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <rng model='virtio'><backend model='random'>/dev/urandom</backend></rng>
  </devices>
</domain>
"""

INTERFACE_XML = """    <interface type='{kind}'>
      <mac address='{mac}'/>
      <source {kind}='{source}'/>
      <model type='{model}'/>
    </interface>
"""

#: Where libvirt keeps a domain's own UEFI varstore. Not configurable: it is the
#: daemon's directory, and a varstore written anywhere else is one the domain's
#: undefine will not clean up.
NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"


def firmware_xml(vm: dict) -> tuple[str, str]:
    """The ``os`` block's two halves, with the same exclusivity the module kept:
    autoselect only when nothing is pinned, and the pin as the whole config."""
    if vm["loader"] is None:
        return (" firmware='efi'" if vm["firmware"] == "efi" else "", "")
    fmt = vm["loader_format"]
    lines = (
        f"    <loader readonly='yes' type='pflash' format='{fmt}'>"
        f"{vm['loader']}</loader>\n"
    )
    if vm["nvram_template"] is not None:
        ext = "qcow2" if fmt == "qcow2" else "fd"
        nv_fmt = f" format='{fmt}'" if fmt != "raw" else ""
        lines += (
            f"    <nvram template='{vm['nvram_template']}'{nv_fmt}>"
            f"{NVRAM_DIR}/{vm['domain_name']}_VARS.{ext}</nvram>\n"
        )
    return "", lines


def domain_xml(vm: dict, overlay_path: str, seed_path: str) -> str:
    firmware, loader = firmware_xml(vm)
    interfaces = "".join(
        INTERFACE_XML.format(
            kind="network" if n["network"] else "bridge",
            source=n["network"] or n["bridge"],
            mac=n["mac"],
            model=n["model"],
        )
        for n in vm["nics"]
    )
    return DOMAIN_XML.format(
        **vm,
        firmware_attr=firmware,
        loader_xml=loader,
        overlay=overlay_path,
        seed=seed_path,
        interfaces=interfaces,
    )


def upload(conn: Any, pool: Any, name: str, fmt: str, source: str) -> Any:
    """What ``libvirt_volume`` with ``create.content.url`` did: create the volume
    at the file's size, then stream the bytes in.

    The capacity is the source file's size on purpose. Spike A4: ``vol-upload``
    writes the qcow2 header from offset 0 and libvirt then reads the declared
    capacity back out of it, so whatever is asked for here is discarded. The
    number that survives is the overlay's, below.
    """
    size = os.path.getsize(source)
    vol = pool.createXML(
        VOLUME_XML.format(name=name, capacity=size, fmt=fmt, backing=""), 0
    )
    stream = conn.newStream(0)
    with open(source, "rb", buffering=0) as handle:
        fd = handle.fileno()
        try:
            vol.upload(stream, 0, size, 0)
            stream.sendAll(lambda _stream, nbytes, _opaque: os.read(fd, nbytes), None)
            stream.finish()
        except BaseException:
            # `virStreamSendAll` aborts the stream itself only when the *handler*
            # raises inside its loop. Anything else -- the daemon refusing a
            # write, a Ctrl-C -- leaves it neither aborted nor finished, and the
            # daemon holds the half-written volume open until the connection
            # drops. `BaseException` because an interrupt leaks it just as well.
            #
            # All three calls are inside: a stream is live from `vol.upload`
            # onwards, so a refusal there or a `finish` that fails leaves exactly
            # the same half-written volume a failed send does.
            stream.abort()
            raise
    return vol


def overlay(pool: Any, vm: dict, base_path: str) -> Any:
    """``libvirt_volume.overlay``: capacity here and only here (spike A4)."""
    return pool.createXML(
        VOLUME_XML.format(
            name=vm["overlay_name"],
            capacity=vm["disk_bytes"],
            fmt="qcow2",
            backing=BACKING_XML.format(path=base_path),
        ),
        0,
    )


@contextmanager
def _made(what: str) -> Iterator[None]:
    """One line per created resource, naming it and what it cost.

    **The name rides on the exception, not only on the log line.** The log is
    stderr; ``run.json``'s ``error`` field is what an air-gapped site ships back,
    and ``cli._guard`` fills it from the exception's text. Without this it reads
    ``libvirtError: operation failed: ...`` and says nothing about which of a
    run's four-per-VM objects was being made.

    The error is re-raised rather than replaced: ``args`` is rewritten in place
    so the type, ``get_error_code()`` and ``get_error_message()`` all survive.
    Constructing a fresh ``libvirtError`` cannot do this -- its ``__init__``
    re-reads ``virGetLastError()`` and discards the message it was given whenever
    there is one, which after a real failure there always is. There is nothing to
    translate through ``errors``: no code here matches on a code, and no failure
    here is the benign one.

    **Only ``libvirtError`` is caught, where the Proxmox ``_made`` catches every
    ``Exception``, and the divergence is deliberate**: the ``args`` rewrite is
    specific to this type, and the other failure that reaches here -- an
    ``OSError`` from ``upload``'s ``open(source)`` -- already names the file it
    could not read.
    """
    import libvirt

    started = time.monotonic()
    try:
        yield
    except libvirt.libvirtError as exc:
        exc.args = (f"could not create {what}: {exc}",)
        log.error("%s", exc)
        raise
    log.info("created %s in %.1fs", what, time.monotonic() - started)


def create(conn: Any, tfvars: dict) -> dict:
    """Create everything ``render`` described, and report it as the inventory.

    Keyed by the logical name, with the same four fields the module's
    ``outputs.tf`` emitted, so ``inventory.json`` is unchanged by the move off
    OpenTofu.
    """
    pool = conn.storagePoolLookupByName(tfvars["pool"])
    base = tfvars["base_volume"]
    base_path = base["path"]
    if base["create"]:
        with _made(f"base volume {base['name']}"):
            base_path = upload(conn, pool, base["name"], "qcow2", base["source"]).path()

    vms: dict[str, dict] = {}
    for name, vm in tfvars["vms"].items():
        with _made(f"seed volume {vm['seed_name']}"):
            seed = upload(conn, pool, vm["seed_name"], "iso", vm["seed_iso"])
        with _made(f"overlay volume {vm['overlay_name']}"):
            disk = overlay(pool, vm, base_path)
        with _made(f"domain {vm['domain_name']}"):
            dom = conn.defineXML(domain_xml(vm, disk.path(), seed.path()))
            # Autostart before start, so a host rebooted between the two brings
            # the domain back rather than leaving it defined and off.
            dom.setAutostart(1)
            dom.create()
        vms[name] = {
            "name": dom.name(),
            "uuid": dom.UUIDString(),
            "configured_address": vm["configured_address"],
            "disks": [disk.path(), seed.path()],
        }
    return vms
