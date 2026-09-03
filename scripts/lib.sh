# Sourced by every script in here. Not executable, and deliberately holds no
# commands of its own -- sourcing something that acts is how a `--help` ends up
# rebuilding an image.
#
# **Versions are read out of the Containerfile, never redeclared.** The image is
# the deliverable, so it owns the pin.

# Every variable here is consumed by a *caller*, which shellcheck cannot see when
# it lints this file alone. The callers are checked with `shellcheck -x`, which
# follows the source and does catch a genuinely unused one.
# shellcheck disable=SC2034

set -euo pipefail
# A `die` inside `$(helper)` has to stop the caller. Command substitutions do not
# inherit errexit, so without this every guard one call level down is inert:
# containerfile_arg's die exited only its own subshell and `tag="$(image_tag)"`
# carried on with `localhost/vcows-deploy:` at status 0.
shopt -s inherit_errexit
IFS=$'\n\t'

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly REPO

# Tool binaries this repo installs for itself. `.tools/` is already gitignored,
# so the bin directory needs no new rule.
TOOLS_BIN="$REPO/.tools/bin"
readonly TOOLS_BIN
PATH="$TOOLS_BIN:$PATH"
export PATH

# GitLab can only cache paths inside $CI_PROJECT_DIR, so the uv cache lives in
# the tree rather than under ~/.cache. Set unconditionally: one path on both CI
# platforms and on a developer box means one cache key and nothing to diverge.
export UV_CACHE_DIR="$REPO/.cache/uv"

PY="$REPO/.venv/bin/python"
readonly PY

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Which installer provides which tool, for `need`'s message. Data rather than
# case arms: an entry nothing has asked for yet costs a line of a table, where an
# arm nothing reaches is a dead branch, and adding a tool is one entry instead of
# editing control flow. Six of the twelve below are not passed to `need` from
# anywhere in the tree today.
#
# **Keyed by command, not by package, and it cannot be generated from either
# installer.** os-deps.sh installs ShellCheck on dnf and shellcheck on apt, and
# its python3-libvirt puts no command on PATH at all.
#
# **gzip is deliberately absent**, as are qemu-img and the rest of what `need` is
# passed: they are in neither installer, so naming one would send the reader to a
# script that would not supply it. A miss falls through to the bare message.
declare -rA TOOL_INSTALLER=(
    [jq]=os-deps             [curl]=os-deps        [unzip]=os-deps
    [git]=os-deps            [xorriso]=os-deps     [shellcheck]=os-deps
    [uv]=install-tools       [just]=install-tools
    [hadolint]=install-tools [trivy]=install-tools [syft]=install-tools
    [gitleaks]=install-tools
)

# `have` plus the message, for the tools whose absence should stop a script.
#
# A tool whose absence needs more explanation than "install it" keeps its own
# `have ... || die`: test-image.sh's podman line says the gate needs a runtime
# rather than a builder, which no table can express. So does a tool whose absence
# should be *reported* rather than stop the run: lint.sh runs hadolint and
# `shellcheck` through `gate`, which prints FAIL and carries on, so a box missing
# one still learns whether the other five gates pass.
need() {
    local tool script
    for tool in "$@"; do
        have "$tool" && continue
        # `:-` because this file sets `set -u`.
        script="${TOOL_INSTALLER[$tool]:-}"
        [ -n "$script" ] && die "$tool not on PATH -- run scripts/$script.sh"
        die "$tool not on PATH"
    done
}

# True in a linked worktree, false in the main checkout and outside a repo. A
# linked worktree's `--git-dir` is `<main>/.git/worktrees/<name>` while its
# `--git-common-dir` stays `<main>/.git`; in the main checkout the two answers
# are the same string. CI clones normally, so it takes the false branch without
# needing a platform variable read.
#
# **Both git calls are assigned on their own line and forgiven.** This file runs
# `set -e` with `inherit_errexit`, so a bare `$(git ...)` in a directory that is
# not a repo would kill the caller instead of answering false -- which is what
# `tests/test_scripts.py`'s `_tree` does on every run: it copies this file into a
# tmp dir and expects `image_tag` to work there.
in_linked_worktree() {
    local common dir
    common="$(git -C "$REPO" rev-parse --git-common-dir 2>/dev/null)" || return 1
    dir="$(git -C "$REPO" rev-parse --git-dir 2>/dev/null)" || return 1
    [ -n "$common" ] && [ "$common" != "$dir" ]
}

# The worktree's name, for anything that leaves the tree -- an image tag, a rig
# test's `deployment`. Empty in the main checkout, in CI and outside a repo, so
# the checkout that ships names nothing differently. `VCOWS_WORKTREE` overrides,
# and is sanitised the same way rather than trusted verbatim.
#
# Sanitising is lowercase plus `tr -c`: a branch name may carry `/`, `+` or an
# uppercase letter, none of which a container tag accepts.
#
# `tests/conftest.py`'s `WORKTREE` is the same rule for the Python side. Two
# implementations because neither language can call the other cheaply; one rule.
worktree_tag() {
    local name="${VCOWS_WORKTREE:-}"
    if [ -z "$name" ]; then
        in_linked_worktree || return 0
        name="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)" || return 0
    fi
    # The `-` is last in the set on purpose: anywhere else `tr` reads it as a
    # range endpoint and refuses the whole set ("reverse collating sequence
    # order"). `\n` is in the set so the trailing newline survives.
    printf '%s\n' "${name,,}" | tr -c 'a-z0-9._\n-' '-'
}

# The one timestamp format this project writes: RFC 3339, UTC, second precision.
# It reaches the image as BUILD_DATE and the CVE baseline as `generated`, and a
# second spelling would make those two look like different clocks.
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# One `ARG NAME=value` out of the Containerfile. Fails loudly rather than
# returning empty: an unset value reads downstream like a network or runtime
# problem rather than a parsing one.
containerfile_arg() {
    local name="$1" value
    value="$(sed -n "s/^ARG ${name}=//p" "$REPO/Containerfile" | head -1)"
    [ -n "$value" ] || die "no 'ARG ${name}=' in Containerfile"
    printf '%s\n' "$value"
}

# The tag `just image` builds and `just test-image` exercises. One definition, so
# the two recipes cannot disagree about which image is under test.
#
# A linked worktree appends `-<worktree_tag>`, because podman's image store is
# per-machine and not per-checkout: two worktrees building at once would
# otherwise be building over each other's tag.
#
# The version is assigned before it is used rather than interpolated inline.
# Argument position is a real mechanism -- measured, a substitution in an
# argument fails open where the same call in an assignment does not -- but it is
# not the one that produced `localhost/vcows-deploy:` here. containerfile_arg's
# die is two levels down, and at two levels both forms fail open; `shopt -s
# inherit_errexit` (:21) is what closes it. The assignment stays because
# check-extra-masked-returns still flags the argument form, which that option
# does not cover.
image_tag() {
    local version suffix
    if [ -n "${VCOWS_IMAGE_TAG:-}" ]; then
        printf '%s\n' "$VCOWS_IMAGE_TAG"
        return
    fi
    version="$(containerfile_arg VCOWS_VERSION)"
    suffix="$(worktree_tag)"
    printf '%s\n' "localhost/vcows-deploy:$version${suffix:+-$suffix}"
}

# The commit the shipped tree is at, with a `-dirty` suffix when anything the
# image actually carries is modified. One definition, so the manifest the image
# records and the name of the delivery bundle cannot claim different commits.
#
# The set is every path that reaches the image. That is the Containerfile's COPY
# sources, plus the Containerfile and .containerignore themselves: those two
# decide the base digest, the whole dnf install list and every OCI label.
# Leaving them out let a build
# from an edited Containerfile record a clean 40-hex SHA for a commit that did
# not describe the image, which is the exact failure the suffix exists to catch.
# Both path filters already treat them as image inputs, so this also stops the
# two mechanisms disagreeing.
#
# Paths that cannot reach the image -- docs/, tests/ -- stay out on purpose. A
# suffix that fires for everything means nothing.
source_revision() {
    local ship sha dirt
    # The two git calls below are the reason: the comment on `dirt` explains that
    # a *failing* git has to reach `set -e` rather than read as a clean tree. A
    # missing git takes that same path and names nothing, so name it here.
    need git
    ship=(orchestrator container licenses Containerfile .containerignore)
    sha="$(git -C "$REPO" rev-parse HEAD)"
    # Assigned on its own line, then tested. Inside `[ -n "$(git ...)" ]` a git
    # that fails is indistinguishable from a clean tree: the substitution comes
    # back empty, the test is false, and this records a clean SHA for a dirty
    # one -- an image claiming a revision it does not have. As a bare assignment
    # the failure reaches the `set -e` in this file instead. SC2312.
    dirt="$(git -C "$REPO" status --porcelain -- "${ship[@]}")"
    if [ -n "$dirt" ]; then
        log "warning: shipped paths are modified; recording ${sha}-dirty"
        sha="${sha}-dirty"
    fi
    printf '%s\n' "$sha"
}

need_venv() {
    [ -x "$PY" ] || die "no venv -- run 'just dev-env' first"
}
