#!/usr/bin/env bash
# Creates the worktree Claude Code asked for, and builds its `.tools` and `.venv`
# before anything is handed the tree.
#
# **This hook creates the worktree rather than reacting to one.** Claude Code
# runs its own `git worktree add` only when a `WorktreeCreate` hook prints
# nothing, so a hook that merely wanted to install tools would fire before the
# directory it needed to install into existed. Printing the path on stdout is how
# this says the creation is already done, and the branch name below
# (`worktree-<name>`) is the one the harness uses itself.
#
# **A non-zero exit aborts the creation**, which is the whole reason setup lives
# here instead of in a `SessionStart` hook: a worktree whose venv failed to build
# is never handed to an agent, and it is removed before exiting so a retry starts
# from nothing rather than from half a tree.
#
# Measured: ~12 s end to end -- `install-tools.sh` from scratch 8-9 s
# for 366 MB, `just dev-env` 3 s. The hook's timeout in settings.json is 120.
#
# `set -e` is deliberately absent: every step's failure is caught and reported
# with the step's name, and an unnoticed early exit would leave the worktree
# behind.

set -uo pipefail
IFS=$'\n\t'

# The first stderr line has to be this script's message, so a missing jq is
# reported here rather than as bash's own "command not found" ahead of it.
if ! command -v jq >/dev/null 2>&1; then
    printf 'vcows: worktree setup failed at jq\n' >&2
    printf 'jq is not on PATH -- run scripts/os-deps.sh\n' >&2
    exit 1
fi

# The harness sends `name` and `cwd` and nothing else -- measured against Claude
# Code 2.1.259, and the hooks documentation lists no other field. Everything
# else is derived from `cwd`, which is the checkout the session was in when it
# asked: the repo root through git's common dir, so a
# request made from inside a linked worktree still lands beside it rather than
# under it, and the source branch from that checkout's HEAD, so a worktree cut
# while on a feature branch starts from the feature branch and not from master.
input="$(cat)"
name="$(jq -r '.name // empty' <<<"$input" 2>/dev/null)"
cwd="$(jq -r '.cwd // empty' <<<"$input" 2>/dev/null)"
cwd="${cwd:-$PWD}"

if [ -z "$name" ]; then
    printf 'vcows: worktree setup failed at input\n' >&2
    printf 'expected name in the hook JSON, got:\n' >&2
    printf '%s\n' "$input" >&2
    exit 1
fi

# Absolute, because `--git-common-dir` is otherwise relative to `cwd` and this
# script's own working directory is not promised to be `cwd`.
common="$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || common=""
if [ -z "$common" ]; then
    printf 'vcows: worktree setup failed at input\n' >&2
    printf 'cwd is not inside a git repository: %s\n' "$cwd" >&2
    exit 1
fi
REPO="$(dirname "$common")"
# A branch name when on one, the commit when detached: `worktree add` accepts
# either as the start point.
source_branch="$(git -C "$cwd" symbolic-ref -q --short HEAD 2>/dev/null || git -C "$cwd" rev-parse HEAD)"
# The directory the harness itself uses, so `EnterWorktree` with `path` and the
# exit-time cleanup both recognise the tree.
path="$REPO/.claude/worktrees/$name"

branch="worktree-$name"

# Removing the worktree *and* the branch: `worktree add -b` refuses a branch that
# already exists, so leaving the branch behind would make every retry fail on the
# first step for a reason unrelated to the one being retried.
#
# Only once this script created them. `worktree add` itself fails when the
# branch already exists, and deleting that branch would destroy someone's work
# in the name of tidying up after a creation that never happened.
created=0
fail() {
    printf 'vcows: worktree setup failed at %s\n' "$1" >&2
    printf '%s\n' "$2" >&2
    if [ "$created" = 1 ]; then
        git -C "$REPO" worktree remove --force "$path" >/dev/null 2>&1
        git -C "$REPO" branch -D "$branch" >/dev/null 2>&1
    fi
    exit 1
}

out="$(git -C "$REPO" worktree add "$path" -b "$branch" "$source_branch" 2>&1)" \
    || fail "git worktree add" "$out"
created=1

# The new tree's own .tools/bin, never the main checkout's: `just` and `uv` have
# to come from the worktree being built, and install-tools.sh is what puts them
# there on a box that has neither.
export PATH="$path/.tools/bin:$PATH"

# Output captured per step so a failure reports what the step said and a success
# says nothing at all -- stdout belongs to the path.
step() {
    local label="$1" out
    shift
    out="$(cd "$path" && "$@" 2>&1)" || fail "$label" "$out"
}

step "scripts/install-tools.sh" ./scripts/install-tools.sh
step "just dev-env" just dev-env
# The proof, not a formality: `--system-site-packages` is the flag the venv
# exists for, and `tests/fake_libvirt.py` imports libvirt at module scope, so a
# venv without it fails collection rather than one test.
step "import libvirt" .venv/bin/python -c 'import libvirt'

printf '%s\n' "$path"
exit 0
