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

MODULE="$REPO/orchestrator/backends/libvirt/tofu"
MIRROR="$REPO/.tools/tofu-mirror"
readonly MODULE

log()  { printf '%s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# `have` plus the message, for the tools whose absence should stop a script. The
# hint has to be right, so the case is a table of which installer provides what:
# os-deps.sh installs jq, curl, unzip, git, xorriso and shellcheck;
# install-tools.sh installs uv, just, tofu, hadolint, trivy and syft. **gzip is
# in neither** -- it is assumed present -- so it falls through to the bare
# message rather than being told to run a script that would not supply it.
#
# A tool whose absence needs more explanation than "install it" keeps its own
# `have ... || die`: test-image.sh's podman line says the gate needs a runtime
# rather than a builder, which no table can express.
need() {
    local tool
    for tool in "$@"; do
        have "$tool" && continue
        case "$tool" in
            jq|curl|unzip|git|xorriso|shellcheck)
                die "$tool not on PATH -- run scripts/os-deps.sh" ;;
            uv|just|tofu|hadolint|trivy|syft)
                die "$tool not on PATH -- run scripts/install-tools.sh" ;;
            *)  die "$tool not on PATH" ;;
        esac
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
# The version is assigned before it is used, not interpolated inline. A `die`
# inside a command substitution exits only the subshell, and when that
# substitution is an *argument* rather than an assignment the enclosing command
# still succeeds -- so the inline form returned `localhost/vcows-deploy:` with an
# empty version and status 0, and containerfile_arg's promise to fail loudly did
# not hold at its own call site.
image_tag() {
    local version
    if [ -n "${VCOWS_IMAGE_TAG:-}" ]; then
        printf '%s\n' "$VCOWS_IMAGE_TAG"
        return
    fi
    version="$(containerfile_arg VCOWS_VERSION)"
    printf '%s\n' "localhost/vcows-deploy:$version"
}

# The provider version the module pins. Derived rather than hardcoded: a bump
# renames docs/provider-<version>.lock.hcl, and a literal 0.9.8 here would
# quietly stop watching the file that ships.
provider_version() {
    local version
    version="$(sed -n 's/.*version *= *"= *\([0-9.]*\)".*/\1/p' "$MODULE/main.tf" | head -1)"
    [ -n "$version" ] || die "no pinned provider version in $MODULE/main.tf"
    printf '%s\n' "$version"
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
    local provider ship sha
    provider="$(provider_version)"
    ship=(orchestrator container licenses Containerfile .containerignore
          "docs/provider-${provider}.lock.hcl")
    sha="$(git -C "$REPO" rev-parse HEAD)"
    if [ -n "$(git -C "$REPO" status --porcelain -- "${ship[@]}")" ]; then
        log "warning: shipped paths are modified; recording ${sha}-dirty"
        sha="${sha}-dirty"
    fi
    printf '%s\n' "$sha"
}

need_venv() {
    [ -x "$PY" ] || die "no venv -- run 'just dev-env' first"
}
