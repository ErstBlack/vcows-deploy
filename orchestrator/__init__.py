"""vcows-deploy -- deploy pre-built golden qcow2 images as VMs to KVM/libvirt.

The version is four-digit Major.Minor.Patch.Hotfix and this is its only
definition. Seven things consume it. Each is asserted against this constant, but
not all by the default suite -- two sit behind the image gate and are checked
only when ``VCOWS_IMAGE`` names a built image:

  * ``--version`` output -- ``test_cli.test_version_prints_the_single_definition``
  * the ownership marker's ``v`` -- ``test_marker.test_round_trip``
  * ``pyproject.toml``'s ``project.version`` -- ``test_version.test_pyproject_agrees``
  * ``run.json``'s ``vcows`` -- ``test_cli.test_deploy_runs_the_whole_pipeline``
  * ``ARG VCOWS_VERSION``, which names the image --
    ``test_version.test_the_image_tag_agrees``
  * the ``org.opencontainers.image.version`` label -- **image gate**,
    ``test_image.test_the_labels_are_ours_and_not_the_bases``
  * the build manifest copied into every run directory -- **image gate**,
    ``test_image.test_the_build_manifest_records_what_shipped``

What no test asserts is the tag a build was actually invoked with: ``podman build
-t`` takes it from the operator, and only ``ARG VCOWS_VERSION`` inside the file is
checked. A bump landed here and in the Containerfile can still ship under last
release's tag.
"""

from __future__ import annotations

import logging
import os
import sys
import time

VERSION = "0.1.0.0"

#: Every line vcows writes is a log line: prefixed, level-tagged, on stderr. The
#: sole exception is `cli._confirm`'s prompt, which `input()` writes to stdout
#: with no trailing newline so the cursor stays where the operator types -- being
#: the only unprefixed output there is, it is trivially separable from the log.
#:
#: `%(levelname)-7s` is padded so columns line up across levels, which is what
#: keeps `cli._row`'s report table aligned once every row carries a prefix.
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

#: UTC, matching the run directory's name. `asctime` is localtime unless the
#: converter below says otherwise, and a site in another timezone would read a
#: stamp that disagrees with the directory it is describing.
LOG_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"

#: Not WARNING: the purpose is traceability *after* delivery, and `destroy`
#: cannot be re-run to recover what was not captured the first time. WARNING is
#: a usable quiet mode -- it drops the report and keeps the problems.
LOG_LEVEL_DEFAULT = "INFO"

log = logging.getLogger(__name__)


def _log_level() -> str:
    """``VCOWS_LOG_LEVEL``, or the default if it is unset or not a level name.

    Never fatal: `basicConfig` raises `ValueError` on an unknown level, which
    would turn a typo in an environment variable into a run that does not start.
    """
    raw = os.environ.get("VCOWS_LOG_LEVEL")
    if raw is None:
        return LOG_LEVEL_DEFAULT
    if raw.upper() not in logging.getLevelNamesMapping():
        # Deferred: the logger is configured immediately below, and this has to
        # be the level's own decision rather than something it reports through.
        return LOG_LEVEL_DEFAULT
    return raw.upper()


class _Stderr(logging.StreamHandler):
    """A stderr handler that resolves ``sys.stderr`` when it writes.

    ``StreamHandler`` binds its stream once, at construction. That is wrong for
    anything configured at import: pytest's `capsys` replaces ``sys.stderr``
    *per test*, long after this handler was built, so a bound handler writes
    past the capture and every assertion about stderr sees an empty string.
    Measured -- it is what made 39 tests fail before this existed.

    The same property makes the handler correct for any other reassignment of
    the stream, which is the general form of the bug rather than a test-only
    workaround.
    """

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        """Swallowed: the property above is the only answer."""


def configure_logging() -> None:
    """Configure the root logger. Idempotent -- handlers are replaced, not added.

    Clearing rather than appending is what makes a second call safe. Left to
    accumulate, every line would be emitted once per call.
    """
    logging.Formatter.converter = time.gmtime
    handler = _Stderr()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATEFMT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(_log_level())


# Configured at package import, deliberately, and this is the one place it can
# be. `backends.libvirt.schema` computes `MAX_VCPUS` and its two siblings as
# module-level constants -- they are consumed as literals inside `VM_SCHEMA` --
# and `_ceiling` reports a malformed `VCOWS_MAX_*` while doing so. That happens
# on the import chain `cli` -> `backends` -> the libvirt package -> `schema`,
# before `main()` is reached, so a logger configured in `main` would miss it:
# the record would fall through to `logging.lastResort`, which reaches stderr
# unprefixed and ignores VCOWS_LOG_LEVEL entirely.
#
# Configuring as an import side effect is wrong for a library and right for an
# application package. `orchestrator` is only ever imported to be run.
configure_logging()

if (_bad := os.environ.get("VCOWS_LOG_LEVEL")) is not None and _bad.upper() not in (
    logging.getLevelNamesMapping()
):
    log.warning(
        "ignoring VCOWS_LOG_LEVEL=%r: not a level name. Using %s.",
        _bad,
        LOG_LEVEL_DEFAULT,
    )

__all__ = ["VERSION", "configure_logging"]
