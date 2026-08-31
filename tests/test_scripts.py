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
test controls. It is also what makes `_fake_tools` safe. `lib.sh:31` prepends
`$REPO/.tools/bin` to `PATH`, so the fakes need no PATH manipulation from the
test side -- but a checkout's own `.tools/bin` can be a symlink to a real
toolchain, and writing a fake `trivy` through one destroys it. Every directory
these helpers write is under `tmp_path`.

`scripts/image-scan.sh` and `scripts/bundle.sh` are covered here for the same
reason `lib.sh` is. Both carry guards that fire on a proportion or on the
presence of a file, which `shellcheck` cannot evaluate: the scan's
"did not read this image" test passed at every loss short of 100% for the whole
life of the script, and `bundle.sh` would assemble a delivery for an image the
CVE gate had just rejected.

No `conftest.gate()`. `bash` is not an optional dependency -- `test_image.py` and
`test_seed_iso.py` already shell out -- and `test_gates.py:29`'s `KNOWN` is a
closed set of five names, so a gate here would be a skip nothing could ever
demand. Unconditional is what gives these teeth.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

#: Enough of a Containerfile for `containerfile_arg` to parse. Override the
#: version line to drive the guard.
CONTAINERFILE = "FROM scratch\nARG VCOWS_VERSION=9.9.9.9\n"

#: A synthetic baseline of exactly 100 ids, so a `gone` count reads as a
#: percentage and the rows below are the table in `docs/plans/issue-83.md` §5.
#: Synthetic rather than `docs/cve-baseline.json` so the thresholds asserted here
#: do not move when that file is trimmed.
BASELINE_IDS = [f"CVE-2026-{n:05d}" for n in range(100)]

#: One fake scanner. `-o PATH` is podman, `--output PATH` is trivy, and
#: `-o spdx-json=PATH` is syft, so `${2#*=}` covers all three.
_FAKE = """#!/usr/bin/env bash
out=""
while [ $# -gt 0 ]; do
    case "$1" in -o|--output) out="${2#*=}"; shift;; esac
    shift
done
%s > "$out"
"""


def _tree(tmp_path: Path, *scripts: str, containerfile: str = CONTAINERFILE) -> Path:
    """A scratch repo root holding `scripts/lib.sh`, any extra scripts, and a
    Containerfile."""
    (tmp_path / "scripts").mkdir()
    for name in ("lib.sh", *scripts):
        shutil.copy(SCRIPTS / name, tmp_path / "scripts" / name)
    (tmp_path / "Containerfile").write_text(containerfile)
    return tmp_path


def _fake_tools(tree: Path, found: list[str]) -> None:
    """podman, trivy and syft in the tree's own `.tools/bin`, reporting `found`.

    `lib.sh:31` puts that directory first on `PATH`, so this is the script's own
    injection point and the test manipulates nothing. The archive is a stub:
    nothing in `image-scan.sh` reads it, only the two scanners it hands it to.
    """
    (tree / "report.json").write_text(
        json.dumps(
            {"Results": [{"Vulnerabilities": [{"VulnerabilityID": i} for i in found]}]}
        )
    )
    binaries = tree / ".tools" / "bin"
    binaries.mkdir(parents=True)
    payloads = {
        "podman": "printf 'stub docker-archive\\n'",
        "trivy": f"cat '{tree / 'report.json'}'",
        "syft": """printf '{"packages":[{"name":"stub"}]}\\n'""",
    }
    for name, payload in payloads.items():
        (binaries / name).write_text(_FAKE % payload)
        (binaries / name).chmod(0o755)


def _baseline(tree: Path, ids: list[str] = BASELINE_IDS) -> None:
    """`docs/cve-baseline.json` as `image-scan.sh:113-117` reads it: `.accepted`
    and nothing else."""
    (tree / "docs").mkdir()
    (tree / "docs" / "cve-baseline.json").write_text(json.dumps({"accepted": ids}))


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


#: `gone` of the 100, and whether the scan must refuse it. The middle pair is the
#: threshold's own boundary. The outer three are the measured ones: 1 is what a
#: real scan of this image reports today, 45 is an emptied `gobinary` analyser,
#: and 56 is an emptied `os-pkgs` one.
GONE_ROWS = [(1, False), (33, False), (34, True), (45, True), (56, True)]


@pytest.mark.parametrize(("gone", "red"), GONE_ROWS)
def test_a_scan_missing_a_third_of_the_baseline_is_not_clean(tmp_path, gone, red):
    """`image-scan.sh:148` on a 100-id baseline, at five points of the curve.

    The guard tested `missing -eq accepted`, so it fired only at a total loss --
    a case `scan_floor` (`:56-64`) already refuses. The two losses it exists to
    catch are partial and large, because trivy's three `Results` split into two
    disjoint analyser families: 55 ids on the rocky layer and 44 across the two
    Go binaries. `gone=45` is the row that decides the threshold. The remedy both
    review documents prescribe, `missing * 2 -gt accepted`, first fires at 51 and
    leaves that row green; `* 3` fires at 34 and turns it red.
    """
    tree = _tree(tmp_path, "image-scan.sh")
    _baseline(tree)
    _fake_tools(tree, BASELINE_IDS[: 100 - gone])
    done = _run(tree, "bash scripts/image-scan.sh")
    if red:
        assert done.returncode == 1, done.stderr
        assert f"{gone} of 100 accepted findings are absent" in done.stderr
        assert "did not read this image" in done.stderr
    else:
        assert done.returncode == 0, done.stderr
        assert f"baseline entries no longer found ({gone} of 100" in done.stderr
        assert "no findings outside the baseline" in done.stderr
