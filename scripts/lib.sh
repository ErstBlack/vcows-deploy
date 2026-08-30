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

need_venv() {
    [ -x "$PY" ] || die "no venv -- run 'just dev-env' first"
}
