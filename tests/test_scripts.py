"""`scripts/*.sh`, executed rather than linted.

Nothing else in this suite runs a shell script. `scripts/lint.sh` reads them --
six gates, four of them shellcheck optional checks -- and `tests/` covers the
Python. So a guard that parses cleanly, passes every gate and then does nothing
at run time is invisible here, which is how `containerfile_arg`'s `die` survived
two reviews, a fix aimed at it, and a commit that enabled four extra shellcheck
checks specifically to catch its class.

This file is the place for that: a test that runs a script in a tree it owns and
asserts what the script *did*. The two helpers are deliberately not specific to
`lib.sh`. `_tree` copies any scripts you name into `tmp_path`, and `_run`
executes an arbitrary bash snippet there -- `_run(tree, "bash scripts/bundle.sh")`
is as valid as sourcing the library and calling one function.

Copying rather than running in place is not decoration: `REPO` (`lib.sh:24-25`)
is derived from `BASH_SOURCE` and `readonly`, so relocating `lib.sh` is the only
way to point the helpers that read `$REPO/Containerfile` at a Containerfile a
test controls.

No `conftest.gate()`. `bash` is not an optional dependency -- `test_image.py` and
`test_seed_iso.py` already shell out -- and `test_gates.py:29`'s `KNOWN` is a
closed set of five names, so a gate here would be a skip nothing could ever
demand. Unconditional is what gives these teeth.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

#: Enough of a Containerfile for `containerfile_arg` to parse. Override the
#: version line to drive the guard.
CONTAINERFILE = "FROM scratch\nARG VCOWS_VERSION=9.9.9.9\n"


def _tree(tmp_path: Path, *scripts: str, containerfile: str = CONTAINERFILE) -> Path:
    """A scratch repo root holding `scripts/lib.sh`, any extra scripts, and a
    Containerfile."""
    (tmp_path / "scripts").mkdir()
    for name in ("lib.sh", *scripts):
        shutil.copy(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "Containerfile").write_text(containerfile)
    return tmp_path


def _run(tree: Path, script: str, **env: str) -> subprocess.CompletedProcess:
    """Run `script` under bash in `tree`, with `scripts/lib.sh` already sourced.

    Every `VCOWS_*` variable is dropped from the child environment and put back
    only if a test asks for it. The tree is the fixture, and an ambient override
    must not decide the outcome: `image_tag` returns `$VCOWS_IMAGE_TAG` verbatim
    when it is set (`lib.sh:103`), so under `just test-image` -- which exports
    `VCOWS_IMAGE` and `VCOWS_GATES` into pytest, and is normally invoked with
    `VCOWS_IMAGE_TAG=` on the command line -- both tests below failed while the
    same two passed under `just test`. Measured, not anticipated.
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith("VCOWS_")}
    return subprocess.run(
        ["bash", "-c", f"source '{tree}/scripts/lib.sh'\n{script}"],
        capture_output=True,
        text=True,
        cwd=tree,
        env=clean | env,
        check=False,
    )


def test_a_guard_two_levels_inside_a_substitution_stops_the_caller(tmp_path):
    """`image_tag` -> `containerfile_arg` -> `die`, called as `x="$(image_tag)"`.

    That is the shape of `image-build.sh:23`, `image-scan.sh:70` and
    `test-image.sh:14`, and of `install-tools.sh:115` -> `fetch` -> `digest`.
    Without `shopt -s inherit_errexit` (`lib.sh:21`) the `die` exits only the
    innermost subshell, `image_tag` returns `localhost/vcows-deploy:` at status
    0, and the builder is invoked with an empty tag.
    """
    tree = _tree(tmp_path, containerfile="FROM scratch\nARG VCOWS_VERSION=\n")
    done = _run(tree, 'tag="$(image_tag)"\necho "REACHED tag=[$tag]"')
    assert "no 'ARG VCOWS_VERSION=' in Containerfile" in done.stderr
    assert done.returncode != 0, f"the die did not stop the caller: {done.stdout!r}"
    assert "REACHED" not in done.stdout


def test_the_same_call_succeeds_when_the_arg_is_present(tmp_path):
    """The vacuity guard: the test above must fail for the guard, not the harness."""
    tree = _tree(tmp_path)
    done = _run(tree, 'tag="$(image_tag)"\necho "REACHED tag=[$tag]"')
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "REACHED tag=[localhost/vcows-deploy:9.9.9.9]"
