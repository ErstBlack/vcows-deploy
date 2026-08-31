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

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
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

#: The revision label inside the fake docker-archive, and so the tail of the
#: bundle's own filename. Deliberately not the fixture repo's HEAD: the archive
#: being older than the tree is the case `bundle.sh:84-91` warns about and does
#: not refuse, and the passing test below asserts it is still only a warning.
ARCHIVE_REVISION = "b" * 40

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


def _report(tree: Path, found: list[str]) -> None:
    """What the fake trivy will hand back. Rewritable between runs."""
    (tree / "report.json").write_text(
        json.dumps(
            {"Results": [{"Vulnerabilities": [{"VulnerabilityID": i} for i in found]}]}
        )
    )


def _fake_tools(tree: Path, found: list[str]) -> None:
    """podman, trivy and syft in the tree's own `.tools/bin`, reporting `found`.

    `lib.sh:31` puts that directory first on `PATH`, so this is the script's own
    injection point and the test manipulates nothing. The archive is a stub:
    nothing in `image-scan.sh` reads it, only the two scanners it hands it to.
    """
    _report(tree, found)
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
    """`docs/cve-baseline.json` as `image-scan.sh:128-132` reads it: `.accepted`
    and nothing else."""
    (tree / "docs").mkdir()
    (tree / "docs" / "cve-baseline.json").write_text(json.dumps({"accepted": ids}))


def _stamp_line(archive: Path) -> str:
    """`sha256sum image.tar` output, which is the whole format of the PASSED
    stamp `image-scan.sh:187` writes and `bundle.sh:77` checks."""
    return f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  image.tar\n"


def _fake_archive(path: Path, revision: str) -> None:
    """A docker-archive as `archive_label` (`bundle.sh:32-39`) reads one.

    Two JSON members in a tar: `manifest.json` naming the config blob, and the
    blob carrying the two OCI labels the bundle is named from. Nothing in
    `bundle.sh` opens a layer, so 2 KB stands in for the real 444 MB.
    """
    config = {
        "config": {
            "Labels": {
                "org.opencontainers.image.version": "9.9.9.9",
                "org.opencontainers.image.revision": revision,
            }
        }
    }
    members = {"manifest.json": [{"Config": "config.json"}], "config.json": config}
    with tarfile.open(path, "w") as tar:
        for name, payload in members.items():
            blob = json.dumps(payload).encode()
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))


def _bundle_tree(tmp_path: Path, stamp: str | None) -> Path:
    """A tree `bundle.sh` runs to completion in, `stamp` becoming `.cache/scan/PASSED`.

    `None` writes no stamp at all. Past the precondition the script reaches
    `source_revision` (`lib.sh:136`), which parses the module's `main.tf` for the
    pinned provider version and calls `git rev-parse HEAD`; neither is what these
    tests are about, and both stand between the precondition and the delivery.
    """
    tree = _tree(tmp_path, "bundle.sh")
    module = tree / "orchestrator" / "backends" / "libvirt" / "tofu"
    module.mkdir(parents=True)
    (module / "main.tf").write_text('      version = "= 0.9.8"\n')
    scan = tree / ".cache" / "scan"
    scan.mkdir(parents=True)
    _fake_archive(scan / "image.tar", ARCHIVE_REVISION)
    (scan / "sbom.spdx.json").write_text('{"packages":[]}')
    (scan / "trivy.json").write_text('{"Results":[]}')
    if stamp is not None:
        (scan / "PASSED").write_text(stamp)
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "fixture"], check=True)
    return tree


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
    """`image-scan.sh:169` on a 100-id baseline, at five points of the curve.

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


def test_the_scan_stamps_a_pass_and_takes_the_stamp_back_on_a_failure(tmp_path):
    """`image-scan.sh:89` and `:187`, the two halves of the verdict.

    The exit status is the only place the scan used to record whether it
    accepted the image, and an exit status does not survive the process. Both
    halves matter: a run that dies has to remove a stamp an earlier run left, or
    `bundle.sh` reads yesterday's verdict about today's archive.
    """
    tree = _tree(tmp_path, "image-scan.sh")
    _baseline(tree)
    _fake_tools(tree, BASELINE_IDS)
    scan = tree / ".cache" / "scan"

    assert _run(tree, "bash scripts/image-scan.sh").returncode == 0
    assert (scan / "PASSED").read_text() == _stamp_line(scan / "image.tar")

    _report(tree, [*BASELINE_IDS, "CVE-2099-99999"])
    done = _run(tree, "bash scripts/image-scan.sh")
    assert done.returncode == 1, done.stderr
    assert "1 new finding(s)" in done.stderr
    assert not (scan / "PASSED").exists(), "a failed scan left its verdict behind"


def test_bundle_refuses_an_archive_no_scan_has_accepted(tmp_path):
    """The defect: three complete files and no verdict was enough to ship.

    A scan that dies on a new finding writes the archive, the report and the SBOM
    before it reads the baseline, so `bundle.sh:50-52` saw exactly what a passing
    scan leaves and assembled a correctly named, checksum-verifying delivery for
    an image the CVE gate had rejected.
    """
    tree = _bundle_tree(tmp_path, stamp=None)
    done = _run(tree, "bash scripts/bundle.sh")
    assert done.returncode == 1, done.stderr
    assert "has not accepted this archive" in done.stderr
    assert not (tree / ".cache" / "delivery").exists()


def test_bundle_refuses_a_stamp_that_describes_a_different_archive(tmp_path):
    """Why the stamp holds a digest rather than being empty.

    A second scan that rewrites `image.tar` and then dies, or an archive copied
    in by hand, leaves a stamp that vouches for bytes that are no longer there.
    An empty marker would be about four lines lighter and could not tell the two
    apart; the digest costs 2.0s over the real 444 MB.
    """
    tree = _bundle_tree(tmp_path, stamp=f"{'0' * 64}  image.tar\n")
    done = _run(tree, "bash scripts/bundle.sh")
    assert done.returncode == 1, done.stderr
    assert "does not describe image.tar" in done.stderr
    assert not (tree / ".cache" / "delivery").exists()


def test_bundle_proceeds_when_the_stamp_matches(tmp_path):
    """The regression guard: a precondition that also refuses valid input is
    worse than none.

    Also pins the severity split. The archive's revision is not the fixture's
    HEAD, so `bundle.sh:84-91` fires -- and must stay a warning, because
    delivering an older image on purpose is legitimate where shipping an
    unaccepted one is not.
    """
    tree = _bundle_tree(tmp_path, stamp=None)
    scan = tree / ".cache" / "scan"
    (scan / "PASSED").write_text(_stamp_line(scan / "image.tar"))
    done = _run(tree, "bash scripts/bundle.sh")
    assert done.returncode == 0, done.stderr
    assert "warning: the archive was built at" in done.stderr
    assert sorted(p.name for p in (tree / ".cache" / "delivery").iterdir()) == [
        "SHA256SUMS",
        "image.tar.sha256",
        "sbom.spdx.json",
        "trivy.json",
        f"vcows-deploy-9.9.9.9-{ARCHIVE_REVISION}.tar.gz",
    ]
