"""The command line: five verbs, and the run directory they write.

``validate``, ``preflight``, ``deploy``, ``destroy``, ``version``. Separately
invocable phases are findings.md §5's sanctioned replacement for a hooks directory
-- an operator who wants to look before they leap runs ``preflight`` -- which is
the whole reason the first two are commands rather than flags on ``deploy``.

**The run directory carries secrets.** The seed ISOs are kept deliberately, so that
debugging a VM that will not boot means inspecting the media it was actually given
rather than rebuilding it -- and those ISOs contain ``user_data`` verbatim. It is
created 0700 and documented as an artifact to handle like the config it came from
(F12). OpenTofu's state encryption is not used: it would encrypt the state file
while the ISOs sit unencrypted beside it, at the cost of a passphrase that, if
lost, makes unreadable a file D23 already calls disposable.

**Exit codes are 0 and 1** -- from vcows. Anything richer is a contract with no
consumer at v0.1. argparse is the exception and it is not ours: an unknown verb,
a missing config path or a bad flag never reaches a command function, and
``parser.error`` exits **2** before it could. So a wrapper script testing for 1
sees a usage mistake as success.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import VERSION, tofu
from .backends import REGISTRY
from .backends.base import (
    Action,
    Backend,
    Decision,
    Discovered,
    Problem,
    decide,
)
from .config import ConfigError, load, vm_names

#: The R5 build manifest, baked into the image at build time: what OpenTofu, what
#: provider, which RPMs and which git revision produced this container. Absent
#: outside the image, where there is nothing to record -- a checkout is not a
#: release, and inventing a manifest for one would make the two indistinguishable.
MANIFEST = Path(os.environ.get("VCOWS_MANIFEST", "/opt/vcows/manifest.json"))

#: The one non-`.tf` file `_stage_module` will carry out of a module directory.
LOCK_NAME = ".terraform.lock.hcl"

#: The log carries the *detail*; the prints below carry the headline. That split
#: is #136's, and it is why nothing here re-prints a `Problem`, a `Decision` or an
#: `Outcome` -- each of those is already printed where it arrives (findings.md
#: §3), and repeating one would make this the fifth result carrier that section
#: refuses. What the log records is what would otherwise be destroyed.
#:
#: Named explicitly rather than by ``__name__``, which is the idiom everywhere
#: else here. The image's ``/usr/local/bin/vcows`` is ``python3 -m
#: orchestrator.cli``, so ``__name__`` is ``__main__`` on exactly the path that
#: ships -- and a log read weeks after delivery would say which module a line
#: came from for every module except this one.
log = logging.getLogger("orchestrator.cli")

#: Default INFO rather than WARNING because the purpose is traceability *after*
#: delivery: a site dumps `podman logs` weeks later, and `destroy` cannot be
#: re-run to reproduce anything that was not recorded the first time. DEBUG adds
#: the per-object recovery detail.
LOG_LEVEL_DEFAULT = "INFO"

#: `%(name)s` is the module, so a line says where it came from without the
#: message having to. The `Z` is made true by the `gmtime` converter in `_logging`
#: -- `asctime` is localtime otherwise, and a site in another timezone would read
#: a stamp that disagrees with the run directory's name.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%dT%H:%M:%SZ"


def _log_level() -> str:
    """``VCOWS_LOG_LEVEL``, or the default if it is unset or not a level name.

    Same shape and the same voice as ``schema._ceiling``: an override that will
    not parse is reported and ignored rather than taken silently, and never
    fatal. `basicConfig` raises `ValueError` on an unknown level, which would
    turn a typo in an environment variable into a run that does not start.
    """
    raw = os.environ.get("VCOWS_LOG_LEVEL")
    if raw is None:
        return LOG_LEVEL_DEFAULT
    if raw.upper() not in logging.getLevelNamesMapping():
        print(
            f"vcows: ignoring VCOWS_LOG_LEVEL={raw!r}: not a level name. "
            f"Using {LOG_LEVEL_DEFAULT}.",
            file=sys.stderr,
        )
        return LOG_LEVEL_DEFAULT
    return raw.upper()


def _logging() -> None:
    """Configure the root logger. Called once, from ``main``.

    ``force=True`` is not decorative. `basicConfig` is a no-op when the root
    logger already has handlers, so without it the first test in a session binds
    the handler to *that* test's `capsys` stderr and every later test reads a
    stream that is no longer connected to anything.

    ``sys.stderr`` is resolved here rather than at import for the same reason:
    `capsys` has already replaced it by the time `main` runs, and a module-level
    handler would hold the real one and escape capture entirely.
    """
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=_log_level(),
        stream=sys.stderr,
        format=LOG_FORMAT,
        datefmt=LOG_DATEFMT,
        force=True,
    )


class UsageError(Exception):
    """The command cannot run as invoked, and the reason is a sentence.

    Not a ``ConfigError`` -- nothing is wrong with the config -- and deliberately
    not a raw ``OSError`` reaching ``main``'s catch-all, which would print
    ``error: FileExistsError: /runs/lab-a`` and leave the operator to work out
    which of the two paths they passed it means.
    """


def manifest() -> dict | None:
    """The build manifest, or ``None`` when there is none to read.

    Absent and unreadable are different facts and used to be one return value.
    Absent is ordinary -- a checkout is not a release. A file that exists and will
    not parse is the R5 record of a delivered artifact being unreadable, which is
    worth a line on stderr rather than the silence a dev box gets.
    """
    if not MANIFEST.is_file():
        return None
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{MANIFEST}: {exc}") from exc


def module_dir(backend: Backend) -> Path:
    """The backend's tofu module, by convention rather than by method.

    findings.md §3 fixes the layout as ``backends/<name>/tofu/`` and deliberately
    does not put it on the ABC. Reading it off the class's own module keeps that
    promise without an eighth abstract method nobody would implement differently.

    Strictly this resolves beside the file *defining the class*, so it is
    ``backends/<name>/tofu/`` only while that file is the package's
    ``__init__.py``. A backend defining its class in a submodule gets that
    submodule's directory, and ``_stage_module`` then reports an empty one.
    """
    return Path(inspect.getfile(type(backend))).parent / "tofu"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run_dir(cfg: dict, override: str | None) -> Path:
    """A directory of this run's own. Empty is fine; occupied is not.

    Every deploy applies against fresh state (D23/D40), so a second run in a
    directory that already holds one is refused rather than merged. Refusing it
    *here* is the point: this runs before anything connects, where the alternative
    was a bare ``FileExistsError`` out of ``seed.mkdir()`` after the preflight had
    already spent a session and printed clean -- or, for destroy, no error at all
    and a ``run.json`` overwritten beside the earlier run's ``inventory.json``.

    An empty directory that already exists still works. That is the bind-mounted
    mountpoint an operator hands the container, and it is the documented shape.
    """
    path = (
        Path(override) if override else Path("runs") / cfg["deployment"] / _timestamp()
    ).resolve()
    # `exist_ok` covers an existing *directory* and nothing else, so a `--run-dir`
    # naming a regular file reaches `main`'s catch-all as the raw `FileExistsError`
    # `UsageError` above exists to replace. Classified here, before the mkdir,
    # because after it there is nothing left to classify.
    if path.exists() and not path.is_dir():
        raise UsageError(
            f"{path} is a file, not a directory. Every run writes its own "
            f"directory. Pass a --run-dir that does not exist yet, or one that "
            f"is an empty directory."
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(
            f"cannot create the run directory {path}: {exc.strerror}. Every run "
            f"writes its own directory; check the mount and the UID it is owned by."
        ) from exc
    if any(path.iterdir()):
        raise UsageError(
            f"{path} is not empty. Every run writes its own directory -- the "
            f"OpenTofu state is thrown away between them, so two runs cannot "
            f"share one. "
            + (
                "Pass a --run-dir that does not exist yet, or one that is empty, "
                "or omit it and let vcows write runs/<deployment>/<timestamp>."
                if override
                else "Remove it, or move it aside."
            )
        )
    # `main` sets a 0o077 umask, so a directory vcows created is already private
    # and this has work to do only for one an operator handed us. Skipped when it
    # is already tight, because the chmod is the half that can fail: a bind mount
    # owned by another UID (README's `--user`) refuses it with EACCES, and that
    # must not stop a run that is otherwise fine.
    #
    # EACCES only. EROFS -- `/runs` mounted `:ro` -- is deliberately *not* caught:
    # a run directory that cannot be chmod'ed because the filesystem is read-only
    # cannot be written to either, and the uncaught OSError names the mount
    # (`Read-only file system: '/runs'`). Widening this to `except OSError` was
    # measured: it reports the mode instead of the cause and defers the failure to
    # `run.json`, whose errno never mentions the mount.
    if path.stat().st_mode & 0o077:
        try:
            os.chmod(path, 0o700)
        except PermissionError:
            print(
                f"vcows: cannot make {path} 0700; it stays "
                f"{stat.S_IMODE(path.stat().st_mode):04o}. This run's seed ISOs "
                f"carry user_data verbatim, and anyone who can read that "
                f"directory can read them.",
                file=sys.stderr,
            )
    # The join key. A `podman logs` dump weeks after delivery has no other way to
    # say which run.json it belongs to -- the container is gone, and the record
    # the site ships home names a directory this process never otherwise states.
    log.info("run directory %s", path)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


#: The report layout: a name, a short verb, then free text. Written once because
#: the widths were previously repeated as literal padding -- `"skip    "` and
#: `"destroy "` are hand-cut to match `{:<7}` plus a separator -- so changing
#: either width silently misaligned two of the three sites.
_NAME_W = 20
_VERB_W = 7


def _row(name: str, verb: str, detail: str) -> str:
    """One report line. `_report` and the destroy loop cannot share a *function*
    -- one prints `Decision`s and the other `Existing`s, which findings.md §3
    keeps as separate carriers -- so they share the shape instead."""
    return f"  {name:<{_NAME_W}} {verb:<{_VERB_W}} {detail}"


def _report(decisions: list[Decision], problems: list[Problem]) -> None:
    for d in decisions:
        print(_row(d.vm_name, d.action.value, d.reason))
    for p in problems:
        print(f"  {p}", file=sys.stderr)


@dataclass
class _Run:
    """One run's directory, and everything the record of it will need.

    A holder rather than five arguments because the failure path has to write the
    same record as the success path, from an ``except`` block that can see none of
    the locals the body accumulated. Fields are filled in as they are learned, so
    a run that dies at its third step still records its first two.
    """

    path: Path
    command: str
    cfg: dict
    started: str
    decisions: list[Decision] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _record(run: _Run, outcome: str, **extra: Any) -> None:
    """``run.json``: what was asked, what was decided, what happened.

    The R5 build manifest is copied in beside it, not merged into it: that half is
    baked at image build time and this half is what a runtime can observe. The
    copy matters because the run directory is what an air-gapped site ships back,
    and "which build did this" is unanswerable from it otherwise. Absent outside
    the image, where there is nothing to copy.
    """
    if MANIFEST.is_file():
        # Suppressed for the same reason `_guard` suppresses: a failure copying
        # provenance must not cost the record of what happened.
        with contextlib.suppress(OSError):
            # `copyfile`, not `copy`: the latter carries the source's mode across
            # and would land 0644 in a 0700 directory whose every other file the
            # umask made private.
            shutil.copyfile(MANIFEST, run.path / "manifest.json")
    _write_json(
        run.path / "run.json",
        {
            "vcows": VERSION,
            "command": run.command,
            "deployment": run.cfg["deployment"],
            "backend": run.cfg["backend"],
            "started": run.started,
            "finished": _timestamp(),
            "outcome": outcome,
            "decisions": [
                {"vm": d.vm_name, "action": d.action.value, "reason": d.reason}
                for d in run.decisions
            ],
            **run.extra,
            **extra,
        },
    )


def _guard(run: _Run, body: Callable[[], int]) -> int:
    """Run one verb's body, and leave a record whatever it does.

    ``BaseException``, so a Ctrl-C mid-teardown -- the run with the most to say and
    the least chance of saying it -- writes one too. The exception then continues
    to ``main``, which owns the message and the exit code; a failure writing the
    record must not replace it with a worse one.
    """
    try:
        return body()
    except BaseException as exc:
        # Suppressed, not handled: a full disk here must not replace the
        # exception that says what actually went wrong. Reported, though: the
        # run directory is the whole account an air-gapped site ships back, and
        # its absence is otherwise indistinguishable from a run that never ran.
        try:
            _record(run, "failed", error=f"{type(exc).__name__}: {exc}")
        except OSError as unwritable:
            # The original invariant restated: a closed stderr must not become
            # the exception the operator sees instead of `exc`.
            with contextlib.suppress(OSError):
                print(
                    f"vcows: this run left no record -- {run.path / 'run.json'} "
                    f"could not be written ({unwritable.strerror}). The failure "
                    f"below is reported on this stream only.",
                    file=sys.stderr,
                )
        raise


# -- validate ---------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Offline only. No connection is opened and nothing is written."""
    cfg, problems = load(args.config, REGISTRY)
    # `load` raises on anything fatal, so what is left here is warnings.
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        f"{args.config}: valid ({len(cfg['vms'])} VMs, "
        f"deployment {cfg['deployment']!r})"
    )
    return 0


# -- preflight --------------------------------------------------------------


def _look(cfg: dict) -> tuple[Discovered, list[Decision], list[Problem]]:
    """One connected pass. The session is closed before this returns.

    The provider opens its own connection from ``var.uri``, so holding ours across
    a multi-GB upload would add a second idle SSH session that buys the transfer
    nothing -- and libvirt-python registers no keepalive here, so a socket that
    hangs rather than resetting can wedge the CLI after a successful apply.
    """
    backend = REGISTRY[cfg["backend"]]
    with backend.connect(cfg) as session:
        discovered = backend.preflight(cfg, session)
    decisions, policy = decide(vm_names(cfg), discovered.vms, cfg["deployment"])
    return discovered, decisions, list(discovered.problems) + policy


def cmd_preflight(args: argparse.Namespace) -> int:
    cfg, config_problems = load(args.config, REGISTRY)
    _, decisions, problems = _look(cfg)
    _report(decisions, config_problems + problems)
    refused = [d for d in decisions if d.action is Action.REFUSE]
    return 1 if refused or any(p.fatal for p in problems) else 0


# -- deploy -----------------------------------------------------------------


def cmd_deploy(args: argparse.Namespace) -> int:
    cfg, config_problems = load(args.config, REGISTRY)
    started = _timestamp()
    run = _Run(_run_dir(cfg, args.run_dir), "deploy", cfg, started)
    return _guard(run, lambda: _deploy(run, config_problems))


def _note_warnings(run: _Run, result: tofu.Result) -> None:
    """Add one step's warnings to the record, now rather than at the end."""
    run.extra["tofu_warnings"] += [str(d) for d in result.warnings]


def _tofu_version(run: _Run, workdir: Path) -> dict | None:
    """What actually ran, or ``None`` -- never an exception over a good deploy.

    This is provenance, and it is asked for *after* the apply succeeded and
    ``inventory.json`` is on disk. Letting it raise -- `tofu version` exits
    non-zero, takes longer than ``SHORT_TIMEOUT``, or prints something that will
    not parse -- reaches ``_guard``, which then writes ``outcome: "failed"`` over
    a deploy that created every VM it was asked to. Tolerated the way
    ``_print_manifest`` tolerates a manifest that will not parse.
    """
    try:
        return tofu.version(workdir)
    except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:
        # Both halves, not just stderr: `tofu: null` in the shipped record reads
        # as "vcows did not try" and means "tried and could not". The field stays
        # `dict | None` -- a sentence in it would make it `dict | str` and break
        # a consumer reading `record["tofu"]["terraform_version"]`.
        problem = Problem.warning(
            f"cannot record the tofu version ({exc})", where="tofu"
        )
        print(f"vcows: {problem.message}", file=sys.stderr)
        run.extra["problems"].append(str(problem))
        return None


def _deploy(run: _Run, config_problems: list[Problem]) -> int:
    cfg = run.cfg
    backend = REGISTRY[cfg["backend"]]

    discovered, decisions, found = _look(cfg)
    problems = config_problems + found
    _report(decisions, problems)
    # As soon as they exist, so that every record from here on carries them --
    # including the failure one. A refusal's reason belonged only to stderr.
    run.decisions = decisions
    run.extra["problems"] = [str(p) for p in problems]
    # Same rule, same reason: the list exists from here on so that whichever
    # record gets written carries whatever had been learned by then.
    run.extra["tofu_warnings"] = []

    # Nothing has been touched yet, and this is the last point where that is true.
    if any(d.action is Action.REFUSE for d in decisions) or any(
        p.fatal for p in problems
    ):
        _record(run, "refused")
        print("refusing to deploy; nothing was changed", file=sys.stderr)
        return 1

    creating = {d.vm_name for d in decisions if d.action is Action.CREATE}
    if not creating:
        _record(run, "nothing-to-create")
        print("nothing to create")
        return 0

    # D23: the module only ever creates, so VMs that already exist are dropped
    # here rather than skipped later. Against a *reused* state, dropping a key
    # from `for_each` would plan a destroy of that live VM; against the fresh
    # state of a new run directory (D40) there is nothing to drop it from.
    create_cfg = {**cfg, "vms": [vm for vm in cfg["vms"] if vm["name"] in creating]}

    seed = run.path / "seed"
    seed.mkdir()
    workdir = run.path / "tofu"
    workdir.mkdir()

    with backend.prepare(create_cfg, seed, discovered) as prepared:
        tfvars = backend.render(create_cfg, prepared)
        _stage_module(module_dir(backend), workdir)
        _write_json(workdir / "main.auto.tfvars.json", tfvars)

        # OpenTofu printed these live -- `_run` inherits stdout -- so they are not
        # re-printed. What they were missing is the run directory an air-gapped
        # site ships back, where nothing recorded them at all. Accumulated after
        # each step rather than after all three: a plan that warns and an apply
        # that raises is exactly the run whose warnings are worth keeping, and
        # collecting them at the end means that run records none of them.
        try:
            inited = tofu.init(workdir)
            _note_warnings(run, inited)
            planned = tofu.plan(workdir, workdir / "plan.bin")
            _note_warnings(run, planned)
            if not planned.changes:
                # No change summary at all is not the same fact as a plan that
                # creates nothing. `_read_stream` returns `{}` for a stream that
                # is missing or will not parse -- deliberately, since the exit
                # code is the authority on success -- and without this branch
                # that arrives as "the module proposes no creates", which sends
                # whoever reads it to the module rather than to the file.
                raise tofu.TofuError(
                    f"tofu plan exited 0 but reported no change summary; "
                    f"{workdir / 'plan.json'} is the stream it should be in"
                )
            if not planned.changes.get("add"):
                raise tofu.TofuError(
                    f"plan proposes no creates for {len(creating)} VM(s); "
                    f"refusing to apply"
                )
            applied = tofu.apply(workdir, workdir / "plan.bin")
            _note_warnings(run, applied)
            raw = tofu.outputs(workdir)
        except tofu.TofuError as exc:
            # The step that raised warned too, and `TofuError.result` is the only
            # thing carrying those warnings. Without this they die with the
            # exception -- the run whose warnings are worth most keeps none of
            # its own.
            if exc.result is not None:
                _note_warnings(run, exc.result)
            raise

    inventory = backend.parse_outputs(raw)
    # Names, not counts. The message below already computes the set difference and
    # carries an `or 'names differ'` fallback, so the intent was always a set
    # comparison; a length test made that fallback reachable only when the lengths
    # already differed, and let two same-sized disagreeing lists through.
    if set(inventory.vms) != set(creating):
        # The module is the authority on what it created and `creating` is the
        # authority on what was asked for. When they disagree the run has two
        # artifacts that contradict each other, and recording either as the truth
        # is worse than refusing to record.
        raise tofu.TofuError(
            f"the module reported {len(inventory.vms)} VM(s) for the "
            f"{len(creating)} it was asked to create: "
            f"{', '.join(sorted(set(creating) - set(inventory.vms))) or 'names differ'}"
        )
    _write_json(run.path / "inventory.json", {"vms": inventory.vms})
    _record(
        run,
        "ok",
        created=sorted(creating),
        tofu=_tofu_version(run, workdir),
    )
    print(f"created {len(inventory.vms)} VM(s); run directory {run.path}")
    return 0


def _stage_module(source: Path, workdir: Path) -> None:
    """Copy the static module into the run directory.

    The lock file travels with it when the module ships one, and in the image it
    does: the Containerfile copies ``docs/provider-0.9.8.lock.hcl`` in as the
    module's ``.terraform.lock.hcl``. A checkout has no lock beside the ``.tf``
    files, so the dev-box path stages three files and the image path stages four.

    **This copy is not superseded.** R6 asked whether the run directory could be
    seeded from a pre-initialised tree instead; D48 decided against it, and what
    shipped is the mirror plus a plugin cache warmed at build time
    (``TF_PLUGIN_CACHE_DIR=/opt/tofu/plugin-cache``), so ``init`` symlinks into
    that cache rather than unpacking a 26 MB provider into every run directory.
    Every deploy still stages and still initialises. Reading this as transitional
    and removing it breaks every deploy.

    **Module content this does not copy is refused, not skipped.** It copies two
    patterns, so a ``.tftpl``, a ``modules/`` subdirectory or a ``main.tf.json``
    would be left behind in silence and the apply would run against a module
    missing a piece of itself -- diagnosed at a site, through OpenTofu's error for
    whatever the absent file defined. Widening the copy instead would invent a
    layout nobody has chosen.

    Dotfiles other than the lock are byproducts rather than content: ``tofu init``
    run in the source tree leaves ``.terraform/`` and a state file behind, and the
    staged copy initialises itself, so neither is a missing piece.
    """
    if not any(source.glob("*.tf")):
        raise RuntimeError(f"no module to stage: {source} holds no .tf files")
    for entry in sorted(source.iterdir()):
        if entry.name.startswith(".") and entry.name != LOCK_NAME:
            continue
        if not (entry.is_file() and (entry.suffix == ".tf" or entry.name == LOCK_NAME)):
            raise RuntimeError(
                f"the module at {source} holds {entry.name!r}, which staging does "
                f"not copy -- only *.tf and {LOCK_NAME}. Applying without it would "
                f"run against an incomplete module."
            )
        # `copyfile` rather than `copy`, so the module lands at the umask `main`
        # set rather than at whatever mode a checkout or an image layer gave it.
        # It is the only thing in the run directory that comes from a file
        # already on disk, and so the only one that could arrive world-readable.
        shutil.copyfile(entry, workdir / entry.name)


# -- destroy ----------------------------------------------------------------


def cmd_destroy(args: argparse.Namespace) -> int:
    cfg, config_problems = load(args.config, REGISTRY)
    started = _timestamp()
    run = _Run(_run_dir(cfg, args.run_dir), "destroy", cfg, started)
    return _guard(run, lambda: _destroy(args, run, config_problems))


def _destroy(
    args: argparse.Namespace, run: _Run, config_problems: list[Problem]
) -> int:
    cfg = run.cfg
    backend = REGISTRY[cfg["backend"]]
    deployment = cfg["deployment"]

    with backend.connect(cfg) as session:
        discovered = backend.preflight(cfg, session)

        marked = [e for e in discovered.vms if e.marker is not None]
        targets = [
            e
            for e in marked
            if e.marker is not None and e.marker.deployment == deployment
        ]
        others = [e for e in marked if e not in targets]

        # Advisory here, fatal on deploy: a base image whose size disagrees with
        # the local copy, or an orphaned volume, must not block a teardown. Said
        # out loud rather than left as a comment, because these name a shared
        # golden image and a volume of unknown ownership to an operator who is
        # already tearing things down.
        advisory = config_problems + list(discovered.problems)
        if advisory:
            print(
                "  these were computed for a deploy; none of them changes "
                "this teardown",
                file=sys.stderr,
            )
        for problem in advisory:
            print(f"  {problem}", file=sys.stderr)
        run.extra["problems"] = [str(p) for p in advisory]
        # The same argument `Result.warnings` in `tofu.py` makes: the run
        # directory is the copy that outlives the terminal. `findings.md`'s
        # "reported as found and skipped, with their deployment names" rule
        # mandates the report, and the `skip` row below satisfies it -- but a
        # marked VM this teardown deliberately left alone appeared in no shipped
        # artifact at all. The deployment name goes with it: a bare list of names
        # drops the half of the row that explains why the VM was left.
        run.extra["left_alone"] = {
            e.name: e.marker.deployment or "<unset>"
            for e in others
            if e.marker is not None
        }
        for e in others:
            assert e.marker is not None  # noqa: S101  `others` comes from `marked`
            print(
                _row(
                    e.name,
                    "skip",
                    f"belongs to deployment "
                    f"{e.marker.deployment or '<unset>'!r}, not {deployment!r}",
                )
            )
        for e in targets:
            print(_row(e.name, "destroy", e.marker.name if e.marker else ""))

        if not targets:
            _record(run, "nothing-to-destroy")
            print(f"no VMs marked for deployment {deployment!r} on this target")
            return 0

        if not _confirm(len(targets), deployment, args.yes):
            _record(run, "cancelled")
            return 1

        try:
            out = backend.destroy(cfg, session, targets)
        except BaseException as exc:
            # The teardown with a fatal problem is the run with the most to
            # record, and it is the one that reaches `_guard` as an exception
            # rather than a return value -- so the `destroyed`/`skipped` record
            # below never runs for it. `BaseException`, because a Ctrl-C
            # mid-teardown arrives here too and carries the same accumulator;
            # widening this alone records nothing, though -- `destroy` has to
            # attach `out` to the interrupt for there to be anything to read.
            # `getattr` rather than catching the
            # backend's own error type: core stays backend-agnostic, and a
            # backend that carries no outcome simply records nothing extra.
            partial = getattr(exc, "outcome", None)
            if partial is not None:
                run.extra["destroyed"] = sorted(partial.destroyed)
                run.extra["skipped"] = sorted(partial.skipped)
                run.extra["problems"] += [str(p) for p in partial.problems]
            raise

    # What it did, not what it was asked to do. A teardown that undefines a domain
    # and leaves both its volumes on disk is the failure findings.md §1 rejects
    # `tofu destroy` for, and it is indistinguishable from success unless this
    # loop runs.
    for name in out.skipped:
        print(f"  {name:<20} skipped, not removed by this run")
    for problem in out.problems:
        print(f"  {problem}", file=sys.stderr)

    _record(
        run,
        "failed" if out.failed else "partial" if out.skipped else "ok",
        destroyed=sorted(out.destroyed),
        skipped=sorted(out.skipped),
        problems=run.extra["problems"] + [str(p) for p in out.problems],
    )
    print(f"destroyed {len(out.destroyed)} object(s)")
    if out.failed:
        # `Outcome`'s docstring says a backend that returns this "without its
        # consumer reading it reproduces that defect exactly" -- the silent
        # partial success findings.md §1 rejects `tofu destroy` for. The libvirt
        # backend raises on `out.failed`, so nothing reaches here through it; the
        # branch exists because `Backend.destroy` explicitly permits a backend to
        # return rather than raise, and until now such a backend got "ok" and
        # exit 0. The problems themselves were printed above.
        print(
            f"the teardown reported a fatal problem; {run.path}/run.json has it",
            file=sys.stderr,
        )
        return 1
    if out.skipped:
        # Not a failure -- nothing here raised, and every target was attempted --
        # but not a success either. A domain already gone is a resume finishing
        # somebody else's crash; a volume that would not resolve is a leak. Both
        # end with an object this run did not account for, and a script that
        # reads only the exit code has to be told.
        print(
            f"{len(out.skipped)} object(s) were not removed by this run; "
            f"{run.path}/run.json names them",
            file=sys.stderr,
        )
        return 1
    return 0


def _confirm(count: int, deployment: str, yes: bool) -> bool:
    """The only destructive verb, so the only one that asks.

    Refusing when stdin is not a terminal is deliberate: a scripted destroy should
    have to say so rather than inherit an answer from an absent operator.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        print(
            "not a terminal: pass --yes to destroy non-interactively", file=sys.stderr
        )
        return False
    answer = input(
        f"destroy {count} VM(s) from deployment {deployment!r}? type 'yes': "
    )
    return answer.strip() == "yes"


# -- version ----------------------------------------------------------------


def _print_manifest() -> None:
    """What this image is, printed before anything that can return early.

    It used to sit below ``tofu.version()``, which bails on a missing or broken
    binary -- so the one command that answers "which build is this" answered
    nothing at all on exactly the image somebody would be asking about.
    """
    try:
        build = manifest()
        if build is None:
            return
        print(f"image   {build['git_sha']} built {build['built']}")
        print(f"base    {build['base_image']['name']}@{build['base_image']['digest']}")
        print(f"provider {build['provider']['source']} {build['provider']['version']}")
        packages, sources = len(build["packages"]), len(build["source_rpms"])
        print(f"packages {packages} from {sources} sources")
    except (ValueError, KeyError, TypeError) as exc:
        print(f"image: {MANIFEST} will not parse ({exc})", file=sys.stderr)


def cmd_version(args: argparse.Namespace) -> int:
    print(f"vcows-deploy {VERSION}")
    _print_manifest()
    try:
        info = tofu.version()
    # The same four classes `_tofu_version` names for the identical call: both
    # sites intend "report and carry on", so the narrower tuple was a divergence
    # and not a policy. `subprocess.TimeoutExpired` and `json.JSONDecodeError`
    # -- a slow `tofu` and a `tofu` printing something unparseable, the two
    # states this command is run to discover -- used to reach `main` and exit 1.
    except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:
        print(f"tofu: unavailable ({exc})")
        return 0
    print(f"tofu {info.get('terraform_version', '?')} on {info.get('platform', '?')}")
    return 0


# -- entry point ------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vcows", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version", action="version", version=f"vcows-deploy {VERSION}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, func, help_ in [
        ("validate", cmd_validate, "check a config offline; no connection is opened"),
        (
            "preflight",
            cmd_preflight,
            "report what exists on the target and what would be done",
        ),
        ("deploy", cmd_deploy, "create the VMs that do not exist yet"),
        ("destroy", cmd_destroy, "tear down this deployment's VMs, by marker"),
    ]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("config", help="path to the deployment config")
        p.set_defaults(func=func)
        if name in ("deploy", "destroy"):
            p.add_argument("--run-dir", help="where to write this run's artifacts")
        if name == "destroy":
            p.add_argument(
                "--yes", action="store_true", help="do not ask for confirmation"
            )

    sub.add_parser("version", help="print versions").set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The run directory is 0700, and until this its *contents* were not: the
    # OpenTofu state, the saved plan and the JSON streams are written by a
    # subprocess and the seed ISOs by pycdlib, so vcows opens none of them and no
    # per-file chmod can reach them. A umask is the only lever that covers a
    # child process, and it has to be set before the first verb runs.
    os.umask(0o077)
    # Before parsing, so a log line is possible from the first thing that runs.
    _logging()
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except UsageError as exc:
        print(f"vcows: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1
    except Exception as exc:
        # Backends raise their own exceptions -- findings.md §3 explicitly rules out
        # a shared hierarchy, so there is nothing narrower to catch and importing
        # one would break the seam. `str()` on the libvirt backend's DestroyError
        # already carries every per-object failure.
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if os.environ.get("VCOWS_TRACEBACK"):
            # The message above is what an operator needs; this is what a bug
            # report needs, and an air-gapped site cannot just re-run it here.
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
