# Sourced by every script in here. Not executable, and deliberately holds no
# commands of its own -- sourcing something that acts is how a `--help` ends up
# rebuilding a mirror.
#
# **Versions are read out of the Containerfile, never redeclared.** The image is
# the deliverable, so it owns the pin; a script that repeated `1.12.6` would let
# CI test a different OpenTofu than the one that ships, silently, for as long as
# it took someone to notice. `tests/test_image.py` asserts the image's own tofu
# version, so the two halves of that claim now come from one place.

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

# Tool binaries this repo installs for itself. `.tools/` is already gitignored as
# the provider mirror's home, so the bin directory needs no new rule.
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

# The libvirt module. Still named `MODULE` and still the libvirt one, because
# every caller that wants *the* module -- smoke-libvirt.sh, verify-provider.sh --
# means that one. Callers that want all of them use `backend_modules`.
MODULE="$REPO/orchestrator/backends/libvirt/tofu"
MIRROR="$REPO/.tools/tofu-mirror"
readonly MODULE

# Every backend's OpenTofu module, one path per line. Discovered rather than
# listed: `cli.module_dir` resolves `backends/<name>/tofu` by convention, so a
# list here would be a second place to forget when a backend is added.
backend_modules() {
    find "$REPO/orchestrator/backends" -mindepth 2 -maxdepth 2 -type d -name tofu \
        | sort
}

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Which installer provides which tool, for `need`'s message. Data rather than
# case arms: an entry nothing has asked for yet costs a line of a table, where an
# arm nothing reaches is a dead branch, and adding a tool is one entry instead of
# editing control flow. Seven of the twelve below are not passed to `need` from
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
    [uv]=install-tools       [just]=install-tools  [tofu]=install-tools
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

# The one timestamp format this project writes: RFC 3339, UTC, second precision.
# It reaches the image as BUILD_DATE and the CVE baseline as `generated`, and a
# second spelling would make those two look like different clocks.
now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# One `ARG NAME=value` out of the Containerfile. Fails loudly rather than
# returning empty: an unset version silently becomes a URL like
# `.../download/v/tofu__amd64.rpm`, which 404s in a way that reads like a
# network problem rather than a parsing one.
containerfile_arg() {
    local name="$1" value
    value="$(sed -n "s/^ARG ${name}=//p" "$REPO/Containerfile" | head -1)"
    [ -n "$value" ] || die "no 'ARG ${name}=' in Containerfile"
    printf '%s\n' "$value"
}

# The tag `just image` builds and `just test-image` exercises. One definition, so
# the two recipes cannot disagree about which image is under test.
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
    local version
    if [ -n "${VCOWS_IMAGE_TAG:-}" ]; then
        printf '%s\n' "$VCOWS_IMAGE_TAG"
        return
    fi
    version="$(containerfile_arg VCOWS_VERSION)"
    printf '%s\n' "localhost/vcows-deploy:$version"
}

# The provider version a module pins. Derived rather than hardcoded: a bump
# renames docs/provider-<version>.lock.hcl, and a literal 0.9.8 here would
# quietly stop watching the file that ships.
#
# Takes a module directory, defaulting to the libvirt one so every existing
# caller keeps its meaning -- which is why SC2120 is silenced rather than fixed:
# most callers correctly pass nothing.
# shellcheck disable=SC2120
provider_version() {
    local module="${1:-$MODULE}" version
    version="$(sed -n 's/.*version *= *"= *\([0-9.]*\)".*/\1/p' "$module/main.tf" | head -1)"
    [ -n "$version" ] || die "no pinned provider version in $module/main.tf"
    printf '%s\n' "$version"
}

# The provider a module pins, as `namespace/name`.
# shellcheck disable=SC2120
provider_source() {
    local module="${1:-$MODULE}" src
    src="$(sed -n 's/.*source *= *"\([^"]*\)".*/\1/p' "$module/main.tf" | head -1)"
    [ -n "$src" ] || die "no provider source in $module/main.tf"
    printf '%s\n' "$src"
}

# The commit the shipped tree is at, with a `-dirty` suffix when anything the
# image actually carries is modified. One definition, so the manifest the image
# records and the name of the delivery bundle cannot claim different commits.
#
# The set is every path that reaches the image. That is the Containerfile's COPY
# sources, plus the Containerfile and .containerignore themselves: those two
# decide the base digest, the OpenTofu RPM and its checksum, the provider digest,
# the whole dnf install list and every OCI label. Leaving them out let a build
# from an edited Containerfile record a clean 40-hex SHA for a commit that did
# not describe the image, which is the exact failure the suffix exists to catch.
# Both path filters already treat them as image inputs, so this also stops the
# two mechanisms disagreeing.
#
# Paths that cannot reach the image -- docs/, tests/ -- stay out on purpose. A
# suffix that fires for everything means nothing.
source_revision() {
    local provider ship sha dirt
    # The two git calls below are the reason: the comment on `dirt` explains that
    # a *failing* git has to reach `set -e` rather than read as a clean tree. A
    # missing git takes that same path and names nothing, so name it here.
    need git
    provider="$(provider_version)"
    ship=(orchestrator container licenses Containerfile .containerignore
          "docs/provider-${provider}.lock.hcl")
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
