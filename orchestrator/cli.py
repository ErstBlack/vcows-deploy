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
import json
import os
import shutil
import sys
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
from .config import ConfigError, load, validate, vm_names


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
    path = (
        Path(override) if override else Path("runs") / cfg["deployment"] / _timestamp()
    )
    path.mkdir(parents=True, exist_ok=True)
    path = path.resolve()
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


def _record(
    run: Path,
    command: str,
    cfg: dict,
    started: str,
    outcome: str,
    decisions: list[Decision] | None = None,
    extra: dict | None = None,
) -> None:
    """``run.json``: what was asked, what was decided, what happened.

    Not the R5 build manifest -- that is baked at image build time and copied in
    by Stage 5. This is the half a runtime can actually observe.
    """
    _write_json(
        run / "run.json",
        {
            "vcows": VERSION,
            "command": command,
            "deployment": cfg["deployment"],
            "backend": cfg["backend"],
            "started": started,
            "finished": _timestamp(),
            "outcome": outcome,
            "decisions": [
                {"vm": d.vm_name, "action": d.action.value, "reason": d.reason}
                for d in (decisions or [])
            ],
            **(extra or {}),
        },
    )


# -- validate ---------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Offline only. No connection is opened and nothing is written."""
    cfg = load(args.config, REGISTRY)
    # `load` raises on anything fatal, so what is left here is warnings -- which it
    # discards. Re-running the same validation is cheaper than a second entry point.
    for problem in validate(cfg, REGISTRY):
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
    cfg = load(args.config, REGISTRY)
    _, decisions, problems = _look(cfg)
    _report(decisions, problems)
    refused = [d for d in decisions if d.action is Action.REFUSE]
    return 1 if refused or any(p.fatal for p in problems) else 0


# -- deploy -----------------------------------------------------------------


def cmd_deploy(args: argparse.Namespace) -> int:
    cfg = load(args.config, REGISTRY)
    backend = REGISTRY[cfg["backend"]]
    started = _timestamp()
    run = _run_dir(cfg, args.run_dir)

    discovered, decisions, problems = _look(cfg)
    _report(decisions, problems)

    # Nothing has been touched yet, and this is the last point where that is true.
    if any(d.action is Action.REFUSE for d in decisions) or any(
        p.fatal for p in problems
    ):
        _record(run, "deploy", cfg, started, "refused", decisions)
        print("refusing to deploy; nothing was changed", file=sys.stderr)
        return 1

    creating = {d.vm_name for d in decisions if d.action is Action.CREATE}
    if not creating:
        _record(run, "deploy", cfg, started, "nothing-to-create", decisions)
        print("nothing to create")
        return 0

    # D23: the module only ever creates, so VMs that already exist are dropped
    # here rather than skipped later. Against a *reused* state, dropping a key
    # from `for_each` would plan a destroy of that live VM; against the fresh
    # state of a new run directory (D40) there is nothing to drop it from.
    create_cfg = {**cfg, "vms": [vm for vm in cfg["vms"] if vm["name"] in creating]}

    seed = run / "seed"
    seed.mkdir()
    workdir = run / "tofu"
    workdir.mkdir()

    with backend.prepare(create_cfg, seed, discovered) as prepared:
        tfvars = backend.render(create_cfg, prepared)
        _stage_module(module_dir(backend), workdir)
        _write_json(workdir / "main.auto.tfvars.json", tfvars)

        tofu.init(workdir)
        planned = tofu.plan(workdir, workdir / "plan.bin")
        if not planned.changes.get("add"):
            raise tofu.TofuError(
                f"plan proposes no creates for {len(creating)} VM(s); refusing to apply"
            )
        tofu.apply(workdir, workdir / "plan.bin")
        raw = tofu.outputs(workdir)

    inventory = backend.parse_outputs(raw)
    _write_json(run / "inventory.json", {"vms": inventory.vms})
    _record(
        run,
        "deploy",
        cfg,
        started,
        "ok",
        decisions,
        extra={"created": sorted(creating), "tofu": tofu.version(workdir)},
    )
    print(f"created {len(inventory.vms)} VM(s); run directory {run}")
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
    cfg = load(args.config, REGISTRY)
    backend = REGISTRY[cfg["backend"]]
    started = _timestamp()
    run = _run_dir(cfg, args.run_dir)
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
        for problem in discovered.problems:
            print(f"  {problem}", file=sys.stderr)
        for e in others:
            assert e.marker is not None  # `others` comes from `marked`
            print(
                f"  {e.name:<20} skip    belongs to deployment "
                f"{e.marker.deployment or '<unset>'!r}, not {deployment!r}"
            )
        for e in targets:
            print(f"  {e.name:<20} destroy {e.marker.name if e.marker else ''}")

        if not targets:
            _record(run, "destroy", cfg, started, "nothing-to-destroy")
            print(f"no VMs marked for deployment {deployment!r} on this target")
            return 0

        if not _confirm(len(targets), deployment, args.yes):
            _record(run, "destroy", cfg, started, "cancelled")
            return 1

        backend.destroy(cfg, session, targets)

    _record(
        run,
        "destroy",
        cfg,
        started,
        "ok",
        extra={"destroyed": sorted(e.name for e in targets)},
    )
    print(f"destroyed {len(targets)} VM(s)")
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
