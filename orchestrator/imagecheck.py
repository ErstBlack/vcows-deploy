"""Offline checks on the core ``image`` block. Backend-independent by nature.

``config.IMAGE_SCHEMA`` is core, so the checks on it are too: both read
``image.source_qcow2`` and ``image.sha256``, and neither knows what a storage
pool or a datastore is.

**Called by each backend rather than by ``config.validate``.** Every backend
wants them, but where they land in the problem list is the backend's to choose --
``config.validate`` runs before any backend check, so hoisting the calls there
would reorder every existing message for no gain.
"""

from __future__ import annotations

import hashlib
import logging

from . import qcow2
from .problems import Problem

log = logging.getLogger(__name__)


def check_image_digest(cfg: dict) -> list[Problem]:
    """The declared ``image.sha256``, actually computed.

    ``config.py``'s ``sha256`` pattern only proves the string is 64 hex
    characters; without this a corrupted or substituted golden image deploys with
    no signal.

    Optional, and the cost is why it stays optional: this reads the whole image.
    Measured through this function -- 424 MiB in 2.46 s, ~172 MiB/s -- so roughly
    12 s for a 2 GiB golden image and 59 s for a 10 GiB one. CPU-bound, so a warm
    page cache does not help; with no ``sha256`` declared the call returns in
    8 microseconds. ``config.load`` runs the offline checks for every verb
    (``cmd_validate``, ``cmd_preflight``, ``cmd_deploy``, ``cmd_destroy``), and
    ``destroy`` reads only ``cfg["backend"]`` and ``cfg["deployment"]`` and never
    touches ``cfg["image"]``, so it loads with ``verify_digest=False`` and each
    backend's ``validate`` then does not call this at all. The skip is the
    caller's, because what has to not happen is the call: a flag read in here
    would still be a function every backend calls on a teardown.

    **No wider skip than that.** An operator who sets the field has asked for the
    check on every verb that acts on the image, and verifying in ``preflight``
    instead would put an offline check in the connected phase, so
    ``vcows validate`` would report a corrupt image as valid.

    Unreadable is a warning, for the same reason ``check_disk_capacity`` says:
    ``validate`` is the offline phase and the golden image is bind-mounted at run
    time.
    """
    declared = cfg["image"].get("sha256")
    if declared is None:
        return []

    source = cfg["image"]["source_qcow2"]
    try:
        with open(source, "rb") as fh:
            # `file_digest` rather than a read loop: it chunks internally, so a
            # multi-GB image never lands in memory.
            actual = hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError as exc:
        return [
            Problem.warning(
                f"cannot read {source} to check its sha256 ({exc.strerror}); "
                f"the declared digest was not verified",
                where="image.sha256",
            )
        ]

    # The schema pattern admits either case, so both sides are compared in one.
    if actual != declared.lower():
        return [
            Problem.error(
                f"{source} has sha256 {actual}, but the config declares "
                f"{declared.lower()}; this is not the image the config describes",
                where="image.sha256",
            )
        ]
    return []


def check_disk_capacity(cfg: dict) -> list[Problem]:
    """An overlay smaller than its backing image cannot be created.

    Uses the qcow2 header read rather than ``qemu-img info`` -- see
    orchestrator/qcow2.py. Degrades to a warning rather than an error when the
    image cannot be read, because ``validate`` is the offline phase and the golden
    image is bind-mounted at run time.
    """
    source = cfg["image"]["source_qcow2"]
    try:
        virtual = qcow2.virtual_size(source)
    except OSError as exc:
        return [
            Problem.warning(
                f"cannot read {source} to check disk_gb against it ({exc.strerror}); "
                f"a VM whose disk_gb is below the image's virtual size will fail "
                f"at create time",
                where="image.source_qcow2",
            )
        ]
    except qcow2.NotAQcow2 as exc:
        return [Problem.error(str(exc), where="image.source_qcow2")]

    # The success path returns no Problem, so this line is the only evidence in
    # the log that `validate` opened the image at all.
    log.debug("%s: virtual size %.1f GiB", source, virtual / 1024**3)

    problems = []
    for i, vm in enumerate(cfg["vms"]):
        want = vm.get("disk_gb")
        if isinstance(want, int) and want * 1024**3 < virtual:
            problems.append(
                Problem.error(
                    f"disk_gb is {want} but {source} has a virtual size "
                    f"of {virtual / 1024**3:.1f} GiB; an overlay cannot be "
                    f"smaller than the image it backs onto",
                    where=f"vms[{i}].disk_gb",
                )
            )
    return problems
