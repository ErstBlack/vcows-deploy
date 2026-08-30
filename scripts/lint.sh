#!/usr/bin/env bash
# Every static gate, in one pass.
#
#   scripts/lint.sh          check
#   scripts/lint.sh --fix    apply what ruff can fix, then check the rest
#
# **Runs all five and reports all five.** `just`'s recipe dependencies are
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
workflows_carry_no_logic() {
    "$PY" - "$REPO" <<'PY'
import sys, pathlib, yaml

repo = pathlib.Path(sys.argv[1])
# `just <recipe>`, plus the two bootstrap scripts that must run before `just`
# exists on a fresh runner: os-deps.sh brings curl and unzip, install-tools.sh
# brings just itself.
ok = ("just ", "./scripts/os-deps.sh", "./scripts/install-tools.sh")
bad = []

def commands(node):
    """Every shell command in a workflow, from either platform's shape."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("run", "script", "before_script", "after_script"):
                if isinstance(value, str):
                    yield from value.strip().splitlines()
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            yield from item.strip().splitlines()
            else:
                yield from commands(value)
    elif isinstance(node, list):
        for item in node:
            yield from commands(item)

files = list((repo / ".github" / "workflows").glob("*.yml"))
gitlab = repo / ".gitlab-ci.yml"
if gitlab.is_file():
    files.append(gitlab)

for path in files:
    for command in commands(yaml.safe_load(path.read_text())):
        command = command.strip()
        if command and not command.startswith(ok):
            bad.append(f"{path.name}: {command}")

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
    # DL4006 (:94, :108) wants pipefail before a piped RUN. Both pipes are
    # `echo "$SHA  file" | sha256sum -c -`, and echo cannot fail. False positive.
    #
    # DL3041 (:82) is **not** a false positive: `dnf -y install` really is
    # unpinned, and that is why two builds of the same commit differ. The
    # project's answer is archival rather than pinning -- manifest.json records
    # the exact version and licence of all ~161 packages that shipped -- with the
    # monthly rebuild-and-scan as the control that notices when the drift starts
    # mattering. Pinning six names here would not make the closure reproducible
    # and would break on the first Rocky update. Ignored knowingly, not silently.
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
    gate "shellcheck"        shellcheck -x -s bash "$REPO"/scripts/*.sh "$REPO"/.claude/hooks/*.sh
    gate "workflows carry no logic" workflows_carry_no_logic

    if [ ${#failed[@]} -gt 0 ]; then
        die "${#failed[@]} gate(s) failed: ${failed[*]}"
    fi
    log "all gates pass"
}

main "$@"
