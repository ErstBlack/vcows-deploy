"""The OpenTofu driver: subprocess, and the machine-readable stream beside it.

Backend-agnostic on purpose. It knows about a directory and a binary, never about
a VM, a pool or a hypervisor -- the same reason ``config.py`` knows nothing about
NICs. Swapping OpenTofu for something else means rewriting this file and nothing
else.

**Why a subprocess and not something cleverer.** OpenTofu ships as a Go
binary with no Python API. The only in-process path is speaking ``tfplugin6`` gRPC
to the provider directly, which means reimplementing the graph walker, dependency
resolution and state -- the entire reason for choosing OpenTofu in the first place
(``docs/orchestrator-architecture.md``:226). ``python-terraform`` is inactive, and
every other wrapper is a PyPI package around this same subprocess, which R7's
air-gap rule turns into one more thing to vendor for no capability.

**Why not ``Popen`` with ``-json``.** That was the architecture doc's §5.3 shape,
and it is strictly worse: ``-json`` *replaces* the human-readable output, so the
operator watching a multi-GB upload sees a JSON stream instead of progress, and
reading it live means a line reader and the classic two-pipe deadlock. ``-json-into``
(1.12.0) writes the same stream to a file *while* normal output goes to the
terminal, so tofu simply inherits stdout and we read the file after it exits.
Measured on 1.12.6 against ``plan``, ``apply``, ``init`` and ``output``.

``Popen`` itself *is* used below, with no pipes: what that paragraph rejects is
reading the stream live, not the call. It is there so that a Ctrl-C mid-apply
waits for tofu to unwind, which ``subprocess.run`` does not -- it sleeps 0.25 s
and SIGKILLs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: init/output/version only. A hang there is a name lookup or a stuck registry,
#: never work. `plan` and `apply` get none: `vol-upload` streams the whole golden
#: image through the SSH tunnel with no resume, so any timeout long enough to be
#: safe is too long to be useful, and one that fires kills a live upload.
SHORT_TIMEOUT = 120


@dataclass(frozen=True)
class Diagnostic:
    """One error or warning, read out of the JSON stream rather than off stderr.

    Never parsed from text: the human-readable rendering is boxed, wrapped and
    coloured, and OpenTofu is free to reword it in any release.
    """

    severity: str
    summary: str
    detail: str = ""
    address: str = ""

    def __str__(self) -> str:
        where = f" [{self.address}]" if self.address else ""
        return f"{self.severity}{where}: {self.summary}"


@dataclass(frozen=True)
class Result:
    returncode: int
    diagnostics: tuple[Diagnostic, ...] = ()
    #: `change_summary`'s counts -- add / change / remove / import / forget.
    changes: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> tuple[Diagnostic, ...]:
        """The half that does not stop the run.

        OpenTofu already rendered these on the operator's terminal -- ``_run``
        inherits stdout -- so nothing re-prints them. They are here so the run
        directory can record them, which is the copy that outlives the terminal.
        """
        return tuple(d for d in self.diagnostics if d.severity == "warning")


class TofuError(Exception):
    """A tofu invocation failed, or produced something we refuse to continue on."""

    def __init__(self, message: str, result: Result | None = None):
        self.result = result
        super().__init__(message)


def _binary() -> str:
    tofu = shutil.which("tofu")
    if tofu is None:
        raise TofuError("`tofu` is not on PATH")
    return tofu


def _env() -> dict[str, str]:
    """The child's environment.

    ``TF_CLI_CONFIG_FILE`` is passed through untouched -- it is what points
    OpenTofu at the provider mirror, and it is set by the image (R6) or by the
    tests, never invented here.

    ``NO_COLOR`` is deliberately absent: OpenTofu 1.12.6 ignores it, and colour is
    written even when stdout is a file. ``-no-color`` is the only lever, and it is
    applied per invocation below.
    """
    return {**os.environ, "CHECKPOINT_DISABLE": "1", "TF_IN_AUTOMATION": "1"}


def _read_stream(path: Path) -> tuple[tuple[Diagnostic, ...], dict[str, int]]:
    """Parse the ``-json-into`` file. Malformed lines are skipped, not fatal.

    The exit code is the authority on success, so a stream that is missing or
    truncated -- tofu killed mid-write, a full disk -- must not turn a successful
    apply into a failure or hide an unsuccessful one.
    """
    try:
        text = path.read_text()
    except OSError:
        return (), {}

    diagnostics: list[Diagnostic] = []
    changes: dict[str, int] = {}
    for line in text.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("type") == "diagnostic":
            d = message.get("diagnostic", {})
            diagnostics.append(
                Diagnostic(
                    severity=d.get("severity", message.get("@level", "error")),
                    summary=d.get("summary", message.get("@message", "")),
                    detail=d.get("detail", ""),
                    address=d.get("address", ""),
                )
            )
        elif message.get("type") == "change_summary":
            changes = dict(message.get("changes", {}))
    return tuple(diagnostics), changes


def _run(cmd: str, workdir: Path, args: tuple[str, ...] = ()) -> Result:
    """One invocation, with stdout and stderr inherited.

    The operator sees exactly what they would running tofu by hand, live. That is
    the whole point of ``-json-into``: we get the structure without taking their
    output away.

    Every path is resolved first. ``-chdir`` moves OpenTofu's working directory
    before it interprets anything else, so a relative ``-json-into`` or ``-out``
    would be written relative to the *module* rather than to where the caller
    meant -- silently, and only when the caller happened to pass a relative path.
    """
    workdir = workdir.resolve()
    stream = workdir / f"{cmd}.json"
    argv = [_binary(), f"-chdir={workdir}", cmd, "-input=false"]
    # No env-var route exists (1.12.6 ignores NO_COLOR), and the two cases really
    # do differ: colour is legible on a terminal and noise in a piped log.
    if not sys.stdout.isatty():
        argv.append("-no-color")
    argv.append(f"-json-into={stream}")
    argv.extend(args)

    # Not `start_new_session`: Ctrl-C must reach tofu so it shuts down the way it
    # would if the operator had run it themselves.
    proc = subprocess.Popen(argv, env=_env())
    try:
        proc.wait(timeout=SHORT_TIMEOUT if cmd == "init" else None)
    except KeyboardInterrupt:
        # tofu already has the SIGINT -- it is in our process group -- and what
        # it does with it is release the state lock and stop between resources.
        # `subprocess.run` gives it 0.25 s and then SIGKILL, which lands in the
        # middle of an apply and leaves the state file as whatever it was
        # mid-write. So wait; a second Ctrl-C is the operator saying they meant
        # it, and gets the kill.
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.kill()
            proc.wait()
        raise
    except BaseException:
        # The timeout on `init`, and anything else. Same contract as
        # `subprocess.run`: kill, reap, re-raise.
        proc.kill()
        proc.wait()
        raise

    diagnostics, changes = _read_stream(stream)
    result = Result(proc.returncode, diagnostics, changes)
    if proc.returncode != 0:
        errors = "; ".join(str(d) for d in result.errors)
        raise TofuError(
            f"tofu {cmd} failed (exit {proc.returncode})"
            + (f": {errors}" if errors else ""),
            result,
        )
    return result


def init(workdir: Path) -> Result:
    """Provider installation from whatever ``TF_CLI_CONFIG_FILE`` points at.

    R6 asked for the image never to do this at runtime; D48 decided otherwise and
    Stage 5 shipped that decision. The image resolves from ``/opt/tofu-mirror``
    with no ``direct`` block, and ``TF_PLUGIN_CACHE_DIR=/opt/tofu/plugin-cache``
    is warmed at build time, so this runs offline and produces symlinks rather
    than a 26 MB unpack. It has to run: D40 makes every deploy a new directory,
    and something has to put ``.terraform/`` in it.
    """
    return _run("init", workdir)


def plan(workdir: Path, out: Path) -> Result:
    """Plan into a file, so ``apply`` cannot decide something else.

    A saved plan freezes its variable values -- verified: it applies with the
    tfvars file deleted -- and OpenTofu refuses to apply it if the state has moved
    underneath, rather than quietly re-planning. Together those are what make the
    plan in the run directory a record of what was actually done.
    """
    return _run("plan", workdir, ("-out", str(out.resolve())))


def apply(workdir: Path, planfile: Path) -> Result:
    """Apply a saved plan. No ``-auto-approve``: a plan file needs no approval."""
    return _run("apply", workdir, (str(planfile.resolve()),))


def outputs(workdir: Path) -> dict:
    """``tofu output -json``, captured rather than inherited: it is short, it is
    the handoff, and it is the one place we want the bytes rather than the view."""
    completed = subprocess.run(
        [_binary(), f"-chdir={workdir.resolve()}", "output", "-json"],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=SHORT_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise TofuError(
            f"tofu output failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()}"
        )
    return json.loads(completed.stdout or "{}")


def version(workdir: Path | None = None) -> dict:
    """``terraform_version``, ``platform`` and the resolved ``provider_selections``.

    The R5 build manifest records what was *built*; this records what actually ran,
    which is the only one of the two a runtime can observe. ``provider_selections``
    is empty unless this is asked inside an initialised directory, which is why
    ``workdir`` is here at all.
    """
    completed = subprocess.run(
        [
            _binary(),
            *([f"-chdir={workdir.resolve()}"] if workdir else []),
            "version",
            "-json",
        ],
        env=_env(),
        capture_output=True,
        text=True,
        timeout=SHORT_TIMEOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise TofuError(f"tofu version failed (exit {completed.returncode})")
    return json.loads(completed.stdout or "{}")
