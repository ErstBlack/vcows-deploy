#!/usr/bin/env bash
# The 3.0s static gate, run after Claude edits a source file.
#
# **This cannot undo the edit.** PostToolUse fires after the tool has already
# run, and is listed as non-blocking. What exit 2 does buy is the only thing that
# matters here: it routes stderr back into the transcript. A hook that exits 0
# has its stderr written to the debug log and nowhere else, so a failure reported
# that way is a failure nobody sees. Hence: not a gate, but exit 2 anyway.
#
# **It does not see edits made through Bash.** The matcher is Edit|Write|MultiEdit,
# so a file rewritten by `sed -i`, a heredoc or a python one-liner reaches no gate
# at all until someone runs `just check`. That is the shape conftest.py:7 already
# names -- a gate that quietly passes because it did not run -- and it is a real
# hole, not a rounding error: an agent told to prefer Bash for file edits bypasses
# this hook entirely. Widening the matcher does not fix it on its own, because a
# Bash event carries .tool_input.command and no .tool_input.file_path, so the
# path filter below would exit 0 on every one. Closing it needs a way to decide
# which Bash calls touched source, which is more machinery than this hook has.
#
# Cost is the reason this runs at all. Measured on master at 491d465:
# ./scripts/lint.sh is 3.0s for all six gates and `ty check` is 0.33s. `just
# check` was considered and rejected -- the suite is ~24s and shells out to the
# real tofu binary, which is a Stop-hook cost, not a per-edit one.

set -euo pipefail
IFS=$'\n\t'

# $CLAUDE_PROJECT_DIR is set by the harness. The fallback keeps the script
# runnable by hand for the checks in the issue.
REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

path="$(jq -r '.tool_input.file_path // empty')"
[ -n "$path" ] || exit 0

# Editing a file outside the tree -- a plan, a scratchpad script -- must not lint
# this repo. Compared as a prefix rather than with realpath, which would resolve
# a symlinked worktree into a path that no longer starts with $REPO.
case "$path" in
    "$REPO"/*) ;;
    *) exit 0 ;;
esac

# Non-source edits cost nothing. Markdown, JSON and YAML are ungated by
# scripts/lint.sh anyway, so linting after them would burn 3.0s to report the
# state of files nobody touched.
case "$path" in
    *.py|*.sh|*.tf|*/Containerfile) ;;
    *) exit 0 ;;
esac

cd "$REPO"

fail() {
    printf '%s\n' "$1" >&2
    printf '%s\n' "$2" >&2
    exit 2
}

if ! output="$(just lint 2>&1)"; then
    fail "just lint failed after editing ${path#"$REPO"/}:" "$output"
fi

# ty reads the venv, so it only has anything to say about Python.
case "$path" in
    *.py)
        if ! output="$(just typecheck 2>&1)"; then
            fail "just typecheck failed after editing ${path#"$REPO"/}:" "$output"
        fi
        ;;
esac

exit 0
