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

**Exit codes are 0 and 1.** Anything richer is a contract with no consumer at v0.1.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
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


class UsageError(Exception):
    """The command cannot run as invoked, and the reason is a sentence.

    Not a ``ConfigError`` -- nothing is wrong with the config -- and deliberately
    not a raw ``OSError`` reaching ``main``'s catch-all, which would print
    ``error: FileExistsError: /runs/lab-a`` and leave the operator to work out
    which of the two paths they passed it means.
    """


def manifest() -> dict | None:
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def module_dir(backend: Backend) -> Path:
    """The backend's tofu module, by convention rather than by method.

    findings.md §3 fixes the layout as ``backends/<name>/tofu/`` and deliberately
    does not put it on the ABC. Reading it off the class's own module keeps that
    promise without an eighth abstract method nobody would implement differently.
    """
    package = sys.modules[type(backend).__module__]
    assert package.__file__ is not None  # every backend is a file on disk
    return Path(package.__file__).parent / "tofu"


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
    )
    path.mkdir(parents=True, exist_ok=True)
    path = path.resolve()
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
    # mkdir's mode argument is masked by umask; this is not.
    os.chmod(path, 0o700)
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _report(decisions: list[Decision], problems: list[Problem]) -> None:
    for d in decisions:
        print(f"  {d.vm_name:<20} {d.action.value:<7} {d.reason}")
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

    Not the R5 build manifest -- that is baked at image build time and copied in
    by Stage 5. This is the half a runtime can actually observe.
    """
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
        # exception that says what actually went wrong.
        with contextlib.suppress(OSError):
            _record(run, "failed", error=f"{type(exc).__name__}: {exc}")
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

        inited = tofu.init(workdir)
        planned = tofu.plan(workdir, workdir / "plan.bin")
        if not planned.changes.get("add"):
            raise tofu.TofuError(
                f"plan proposes no creates for {len(creating)} VM(s); refusing to apply"
            )
        applied = tofu.apply(workdir, workdir / "plan.bin")
        raw = tofu.outputs(workdir)

    # OpenTofu printed these live -- `_run` inherits stdout -- so they are not
    # re-printed. What they were missing is the run directory an air-gapped site
    # ships back, where nothing recorded them at all.
    run.extra["tofu_warnings"] = [
        str(d) for r in (inited, planned, applied) for d in r.warnings
    ]

    inventory = backend.parse_outputs(raw)
    if len(inventory.vms) != len(creating):
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
        tofu=tofu.version(workdir),
    )
    print(f"created {len(inventory.vms)} VM(s); run directory {run.path}")
    return 0


def _stage_module(source: Path, workdir: Path) -> None:
    """Copy the static module into the run directory.

    The lock file travels with it when the module ships one. Today the libvirt
    module does not -- the committed lock lives at ``docs/provider-0.9.8.lock.hcl``
    and the constraint is pinned exactly -- and Stage 5 replaces this copy with a
    pre-initialised tree anyway (R6).
    """
    for tf in sorted(source.glob("*.tf")):
        shutil.copy(tf, workdir)
    lock = source / ".terraform.lock.hcl"
    if lock.is_file():
        shutil.copy(lock, workdir)


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
        # the local copy, or an orphaned volume, must not block a teardown.
        for problem in config_problems + discovered.problems:
            print(f"  {problem}", file=sys.stderr)
        run.extra["problems"] = [str(p) for p in config_problems + discovered.problems]
        for e in others:
            assert e.marker is not None  # `others` comes from `marked`
            print(
                f"  {e.name:<20} skip    belongs to deployment "
                f"{e.marker.deployment or '<unset>'!r}, not {deployment!r}"
            )
        for e in targets:
            print(f"  {e.name:<20} destroy {e.marker.name if e.marker else ''}")

        if not targets:
            _record(run, "nothing-to-destroy")
            print(f"no VMs marked for deployment {deployment!r} on this target")
            return 0

        if not _confirm(len(targets), deployment, args.yes):
            _record(run, "cancelled")
            return 1

        out = backend.destroy(cfg, session, targets)

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
        "partial" if out.skipped else "ok",
        destroyed=sorted(out.destroyed),
        skipped=sorted(out.skipped),
        problems=run.extra["problems"] + [str(p) for p in out.problems],
    )
    print(f"destroyed {len(out.destroyed)} object(s)")
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


def cmd_version(args: argparse.Namespace) -> int:
    print(f"vcows-deploy {VERSION}")
    try:
        info = tofu.version()
    except (tofu.TofuError, OSError) as exc:
        print(f"tofu: unavailable ({exc})")
        return 0
    print(f"tofu {info.get('terraform_version', '?')} on {info.get('platform', '?')}")
    build = manifest()
    if build is not None:
        print(f"image   {build['git_sha']} built {build['built']}")
        print(f"base    {build['base_image']['name']}@{build['base_image']['digest']}")
        print(f"provider {build['provider']['source']} {build['provider']['version']}")
        packages, sources = len(build["packages"]), len(build["source_rpms"])
        print(f"packages {packages} from {sources} sources")
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
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
