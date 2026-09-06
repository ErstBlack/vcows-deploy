"""The golden qcow2, out as a VMDK. One ``qemu-img`` call and nothing else.

**The conversion happens at the site rather than on the hypervisor**, because
nothing on the far side will change a disk's format for us: ``api``'s notes on
the import path say why ``CopyVirtualDisk_Task`` is not an option on vCenter, and
an ESXi host is not something vcows talks to at all. So ``prepare`` runs this
into the run's own workdir and the VMDK dies with the run, in the one phase that
is allowed to touch the local filesystem and cannot reach the target.

``qemu-img`` is an RPM in the image, installed beside the SDKs for this one call.
``orchestrator/qcow2.py`` still reads the header itself rather than shelling out
for a virtual size: that runs in ``validate``, where no subprocess should be
needed, and this runs in ``prepare``, where the bytes have to move anyway.

**The subformat is the ``import`` knob's, and the two are not interchangeable.**
An ``ImportVApp`` lease reads a ``streamOptimized`` stream and nothing else; a
plain datastore PUT wants ``monolithicFlat``, which is a descriptor file plus the
``-flat`` extent qemu-img writes beside it. Handing either path the other's
format fails at the far end, mid-upload, which is why the mapping is here rather
than at the call site.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

#: ``target.vsphere.import`` to the VMDK subformat that path can read.
SUBFORMAT = {"ovf": "streamOptimized", "datastore": "monolithicFlat"}


class ConversionError(Exception):
    """``qemu-img`` refused to convert the golden image.

    Ours rather than ``CalledProcessError``, for the reason ``VsphereApiError``
    is ours: what an operator needs is the sentence qemu-img wrote on stderr, and
    ``CalledProcessError`` renders as its own exit status with the output it
    captured nowhere in the message.
    """


def to_vmdk(source: str | Path, dest: Path, subformat: str) -> Path:
    """Convert ``source`` into ``dest`` as a VMDK of ``subformat``.

    ``capture_output`` rather than letting qemu-img write through: its progress
    and its complaints would otherwise land in the middle of the run's own
    output, and the stderr is wanted in the exception rather than on the
    terminal.
    """
    log.info("converting %s to %s (%s)", source, dest, subformat)
    try:
        # Fixed argv and no shell, and `qemu-img` by name: it is the RPM in our
        # own image, resolved against that image's PATH. Same pair of exemptions,
        # for the same reason, as `container/manifest.py`'s call to `rpm`.
        subprocess.run(  # noqa: S603
            [  # noqa: S607
                "qemu-img",
                "convert",
                "-O",
                "vmdk",
                "-o",
                f"subformat={subformat}",
                str(source),
                str(dest),
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        # `errors="replace"` rather than a strict decode: this is the error path
        # already, and a decode that raised here would replace qemu-img's
        # sentence with a UnicodeDecodeError naming nothing.
        stderr = (exc.stderr or b"").decode(errors="replace").strip()
        raise ConversionError(
            f"qemu-img could not convert {source} to a {subformat} VMDK "
            f"(exit {exc.returncode}): {stderr}"
        ) from exc
    return dest
