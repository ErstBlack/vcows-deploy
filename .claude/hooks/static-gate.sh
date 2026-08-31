#!/usr/bin/env bash
# The static gate, run once per turn when Claude has changed a source file.
#
# **Stop, not PostToolUse.** The earlier form of this hook matched
# Edit|Write|MultiEdit, so a file rewritten by `sed -i`, a heredoc or a
# `python3 - <<PY` block reached no gate at all until someone ran `just check`.
# That was not hypothetical: across the five commits of PR #56, every one of
# them written through Bash, the hook fired zero times. `Stop` fires once per
# turn however the edit was made, so the hole closes by construction instead of
# by guessing which Bash commands write files.
#
# **Exit 2 blocks here.** Unlike PostToolUse, which is documented non-blocking, a
# Stop hook exiting 2 prevents the turn from ending and puts stderr in front of
# the model, so a break is fixed in the same turn rather than reported after it.
#
# **The signature is content, not `git status`.** Issue #57 proposed
# `git status --porcelain` as the guard. Measured: it is blind to a second edit
# of an already-modified file, because the porcelain line stays ` M path` either
# way -- and a second edit is the common case inside a turn. A guard that misses
# it rebuilds the failure conftest.py:7 names, so this hashes the contents of
# the files the six gates read or are configured by. 0.033s against lint's 2.9s:
# correctness is free here.
#
# **A given tree state blocks at most once.** Exit 2 continues the turn, which
# produces another Stop, so an unconditional block would wedge the session.
# `stop_hook_active` is not in the current hook documentation and is not relied
# on. Instead the state file records the verdict beside the signature: a
# signature already recorded as `block` reports and exits 0. Blocking a second
# time requires the model to have actually changed something.
#
# Measured on 26627ad: ./scripts/lint.sh 2.85/3.26/2.84s, `ty check` 0.33s.
# Signature 0.033/0.034/0.039s over the 74 files the pattern below selects, on
# d9d9252. `just check` stays rejected -- the suite is ~24s and shells out to
# the real tofu binary.

set -euo pipefail
IFS=$'\n\t'

# $CLAUDE_PROJECT_DIR is set by the harness. The fallback keeps the script
# runnable by hand for the checks in the issue.
REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$REPO"

# .cache/ is gitignored and is where uv's cache already lives, so the stamp
# needs no new ignore rule. It does not exist on a fresh clone.
STATE="$REPO/.cache/static-gate.state"

# Every file the six gates read or are configured by: ruff and ty over *.py and
# their [tool.*] tables in pyproject.toml, tofu fmt over *.tf and *.tftest.hcl,
# hadolint over Containerfile, shellcheck over scripts/*.sh and
# .claude/hooks/*.sh, workflows_carry_no_logic over the two pipeline files, and
# the justfile that runs lint and typecheck.
# Enumerated through git so .venv/, .cache/ and .tools/ fall out of scope via
# .gitignore rather than via a second list that would drift from it.
signature() {
    { git ls-files -z; git ls-files -o --exclude-standard -z; } \
        | grep -zE '\.(py|sh|tf|ya?ml|toml)$|\.tftest\.hcl$|(^|/)(Containerfile|justfile)$' \
        | sort -z \
        | xargs -0 -r sha256sum \
        | sha256sum \
        | cut -d' ' -f1
}

# Failing open means running the gate, never skipping it: if git is unavailable
# or a file vanishes mid-hash, `cur` is empty and every branch below falls
# through to running lint.
cur="$(signature)" || cur=""

if [ -n "$cur" ] && [ -f "$STATE" ]; then
    verdict=""
    sig=""
    IFS=' ' read -r verdict sig < "$STATE" || true
    if [ "$sig" = "$cur" ]; then
        case "$verdict" in
            pass)
                exit 0
                ;;
            block)
                # Already reported on this exact tree. Blocking again would only
                # repeat a message the model has seen and could not act on.
                exit 0
                ;;
        esac
    fi
fi

record() {
    [ -n "$cur" ] || return 0
    mkdir -p "$(dirname "$STATE")"
    printf '%s %s\n' "$1" "$cur" > "$STATE"
}

fail() {
    record block
    printf '%s\n' "$1" >&2
    printf '%s\n' "$2" >&2
    exit 2
}

if ! output="$(just lint 2>&1)"; then
    fail "just lint failed:" "$output"
fi

if ! output="$(just typecheck 2>&1)"; then
    fail "just typecheck failed:" "$output"
fi

record pass
exit 0
