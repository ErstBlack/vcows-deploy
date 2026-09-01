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

#: Every line vcows writes goes through here: prefixed, level-tagged, on stderr.
#: The one exception is `_confirm`'s prompt -- see its docstring.
#:
#: Named explicitly rather than by ``__name__``. The image's
#: ``/usr/local/bin/vcows`` is ``python3 -m orchestrator.cli``, so ``__name__``
#: is ``__main__`` on exactly the path that ships, and a log read weeks after
#: delivery would say which module a line came from for every module but this one.
log = logging.getLogger("orchestrator.cli")


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
            log.warning(
                "cannot make %s 0700; it stays %04o. This run's seed ISOs carry "
                "user_data verbatim, and anyone who can read that directory can "
                "read them.",
                path,
                stat.S_IMODE(path.stat().st_mode),
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


def _problem(problem: Problem) -> None:
    """One problem row, logged at the level it says it is.

    The level replaces the severity word ``Problem.__str__`` carries, so a line
    reads ``WARNING ... [target.libvirt] ...`` rather than restating it.
    ``__str__`` itself is left alone: `run.json` records `str(p)`, and the record
    should not change shape because the log got tidier.

    Routing on the problem's own severity is also a fix. Every one of these
    previously went out at WARNING, fatal ones included.

    ``where`` is frequently empty -- a `Problem` about the run rather than about
    a field -- so it is omitted rather than rendered as an empty bracket. The
    earlier version emitted a dangling ``: `` for those.
    """
    message = (
        f"[{problem.where}] {problem.message}" if problem.where else problem.message
    )
    log.log(logging.ERROR if problem.fatal else logging.WARNING, "%s", message)


def _row(name: str, verb: str, detail: str) -> str:
    """One report line. `_report` and the destroy loop cannot share a *function*
    -- one prints `Decision`s and the other `Existing`s, which findings.md §3
    keeps as separate carriers -- so they share the shape instead."""
    # rstrip: an empty detail column would otherwise leave the line's own
    # padding hanging off the end of the log record.
    return f"{name:<{_NAME_W}} {verb:<{_VERB_W}} {detail}".rstrip()


def _report(decisions: list[Decision], problems: list[Problem]) -> None:
    for d in decisions:
        log.info("%s", _row(d.vm_name, d.action.value, d.reason))
    for p in problems:
        _problem(p)


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


def _decision(d: Decision) -> dict[str, Any]:
    """One decision, as `run.json` records it.

    ``existing`` is present only when there is one -- a create decided against
    nothing, and an empty key would read as "there was a VM and it had no name".
    It carries the identity `reason` states in English, so a reader does not have
    to parse a sentence to find out which domain was skipped: for a SKIP and for
    the "belongs to another deployment" refusal, the id appears nowhere else in
    the run directory at all.
    """
    record: dict[str, Any] = {
        "vm": d.vm_name,
        "action": d.action.value,
        "reason": d.reason,
    }
    if d.existing is not None:
        record["existing"] = {"name": d.existing.name, "id": d.existing.id}
    return record


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
            "decisions": [_decision(d) for d in run.decisions],
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
                log.error(
                    "this run left no record -- %s could not be written (%s). "
                    "The failure below is reported on this stream only.",
                    run.path / "run.json",
                    unwritable.strerror,
                )
        raise


# -- validate ---------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Offline only. No connection is opened and nothing is written."""
    cfg, problems = load(args.config, REGISTRY)
    # `load` raises on anything fatal, so what is left here is warnings.
    for problem in problems:
        _problem(problem)
    log.info(
        "%s: valid (%d VMs, deployment %r)",
        args.config,
        len(cfg["vms"]),
        cfg["deployment"],
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
        log.warning("%s", problem.message)
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
        log.error("refusing to deploy; nothing was changed")
        return 1

    creating = {d.vm_name for d in decisions if d.action is Action.CREATE}
    if not creating:
        _record(run, "nothing-to-create")
        log.info("nothing to create")
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
    log.info("created %d VM(s); run directory %s", len(inventory.vms), run.path)
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
            log.warning(
                "these were computed for a deploy; none of them changes this teardown"
            )
        for problem in advisory:
            _problem(problem)
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
            log.info(
                "%s",
                _row(
                    e.name,
                    "skip",
                    f"belongs to deployment "
                    f"{e.marker.deployment or '<unset>'!r}, not {deployment!r}",
                ),
            )
        for e in targets:
            # The marker's logical name, and only when it differs: for the
            # ordinary case where the domain is named after it, repeating it
            # made the detail column say `app01  destroy  app01`.
            marked = e.marker.name if e.marker else ""
            log.info(
                "%s",
                _row(
                    e.name, "destroy", f"marked {marked!r}" if marked != e.name else ""
                ),
            )

        if not targets:
            _record(run, "nothing-to-destroy")
            log.info("no VMs marked for deployment %r on this target", deployment)
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
        # Through `_row`, like every other report line. This site hand-cut the
        # name column to `{:<20}` -- the literal `_NAME_W` exists to stop, and the
        # one the comment on `_row` was written about missed.
        log.info("%s", _row(name, "skipped", "not removed by this run"))
    for problem in out.problems:
        _problem(problem)

    _record(
        run,
        "failed" if out.failed else "partial" if out.skipped else "ok",
        destroyed=sorted(out.destroyed),
        skipped=sorted(out.skipped),
        problems=run.extra["problems"] + [str(p) for p in out.problems],
    )
    log.info("destroyed %d object(s)", len(out.destroyed))
    if out.failed:
        # `Outcome`'s docstring says a backend that returns this "without its
        # consumer reading it reproduces that defect exactly" -- the silent
        # partial success findings.md §1 rejects `tofu destroy` for. The libvirt
        # backend raises on `out.failed`, so nothing reaches here through it; the
        # branch exists because `Backend.destroy` explicitly permits a backend to
        # return rather than raise, and until now such a backend got "ok" and
        # exit 0. The problems themselves were printed above.
        log.error("the teardown reported a fatal problem; %s/run.json has it", run.path)
        return 1
    if out.skipped:
        # Not a failure -- nothing here raised, and every target was attempted --
        # but not a success either. A domain already gone is a resume finishing
        # somebody else's crash; a volume that would not resolve is a leak. Both
        # end with an object this run did not account for, and a script that
        # reads only the exit code has to be told.
        log.error(
            "%d object(s) were not removed by this run; %s/run.json names them",
            len(out.skipped),
            run.path,
        )
        return 1
    return 0


def _confirm(count: int, deployment: str, yes: bool) -> bool:
    """The only destructive verb, so the only one that asks.

    Refusing when stdin is not a terminal is deliberate: a scripted destroy should
    have to say so rather than inherit an answer from an absent operator.

    **The prompt below is the only thing vcows writes that is not a log line**,
    and the only thing left on stdout. ``input()`` writes it with no trailing
    newline so the cursor stays where the operator types; a handler would add
    both a newline and a prefix and put the cursor on the next line. Being the
    sole unprefixed output, it is trivially separable from the log -- which is
    the reason it is the exception rather than an oversight.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        log.error("not a terminal: pass --yes to destroy non-interactively")
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
        log.info("image   %s built %s", build["git_sha"], build["built"])
        log.info(
            "base    %s@%s", build["base_image"]["name"], build["base_image"]["digest"]
        )
        log.info(
            "provider %s %s", build["provider"]["source"], build["provider"]["version"]
        )
        packages, sources = len(build["packages"]), len(build["source_rpms"])
        log.info("packages %s from %s sources", packages, sources)
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("image: %s will not parse (%s)", MANIFEST, exc)


def cmd_version(args: argparse.Namespace) -> int:
    log.info("vcows-deploy %s", VERSION)
    _print_manifest()
    try:
        info = tofu.version()
    # The same four classes `_tofu_version` names for the identical call: both
    # sites intend "report and carry on", so the narrower tuple was a divergence
    # and not a policy. `subprocess.TimeoutExpired` and `json.JSONDecodeError`
    # -- a slow `tofu` and a `tofu` printing something unparseable, the two
    # states this command is run to discover -- used to reach `main` and exit 1.
    except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:
        log.info("tofu: unavailable (%s)", exc)
        return 0
    log.info(
        "tofu %s on %s",
        info.get("terraform_version", "?"),
        info.get("platform", "?"),
    )
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
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        for problem in exc.problems:
            _problem(problem)
        return 1
    except UsageError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("interrupted")
        return 1
    except Exception as exc:
        # Backends raise their own exceptions -- findings.md §3 explicitly rules out
        # a shared hierarchy, so there is nothing narrower to catch and importing
        # one would break the seam. `str()` on the libvirt backend's DestroyError
        # already carries every per-object failure.
        log.error("%s: %s", type(exc).__name__, exc)
        if os.environ.get("VCOWS_TRACEBACK"):
            # The message above is what an operator needs; this is what a bug
            # report needs, and an air-gapped site cannot just re-run it here.
            # `exc_info` rather than `log.exception`, which would repeat the
            # message that was just logged at ERROR above.
            log.error("traceback follows", exc_info=exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
