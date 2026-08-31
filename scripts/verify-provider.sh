#!/usr/bin/env bash
# The provider version and its hashes are stated in four places that nothing
# compares. `docs/review-2026-08-29/2026-08-29-review.md` files that as an S4:
# "provider facts live in five places and the build compares none of them". S8
# has since removed one of the five -- `manifest.py` now reads the lock hash out
# of the committed lock rather than an `ARG PROVIDER_LOCK_HASH` -- so four
# remain, and a half-finished version bump can still leave them disagreeing in a
# way that surfaces much later as a checksum error reading like corruption.
#
# This is the gate for that. It runs in under a second and needs no network.
#
#   1. orchestrator/backends/libvirt/tofu/main.tf   version = "= X"
#   2. Containerfile                                ARG PROVIDER_VERSION / _SHA256
#   3. docs/provider-X.lock.hcl                     version, and the h1: hash
#   4. .tools/tofu-mirror/...                       the zip, its real sha256,
#                                                   and the index's h1:/zh: pair

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

have jq || die "jq not on PATH -- run scripts/os-deps.sh"

fail=0
check() {
    local what="$1" want="$2" got="$3"
    if [ "$want" = "$got" ]; then
        printf '  ok    %s\n' "$what"
    else
        printf '  FAIL  %s\n         want %s\n         got  %s\n' "$what" "$want" "$got"
        fail=1
    fi
}

# Usage: verify-provider.sh [mirror-dir]
# The argument lets mirror.sh check a freshly downloaded candidate before it
# becomes .tools/tofu-mirror.
main() {
    local mirror="${1:-$MIRROR}"
    local mod_version cf_version cf_sha lock lock_version lock_h1
    local zip index_h1 index_zh zip_sha

    # 1. The module is the source of truth for which version is pinned.
    mod_version="$(provider_version)"
    log "provider version per main.tf: $mod_version"

    # 2. Containerfile.
    cf_version="$(containerfile_arg PROVIDER_VERSION)"
    cf_sha="$(containerfile_arg PROVIDER_SHA256)"
    check "Containerfile PROVIDER_VERSION" "$mod_version" "$cf_version"

    # 3. The committed lock. Its filename carries the version, which is why the
    #    CI cache is keyed on main.tf and not on this path.
    lock="$REPO/docs/provider-${mod_version}.lock.hcl"
    [ -f "$lock" ] || die "no committed lock at docs/provider-${mod_version}.lock.hcl"
    lock_version="$(sed -n 's/ *version *= *"\([^"]*\)".*/\1/p' "$lock" | head -1)"
    lock_h1="$(sed -n 's/.*"\(h1:[^"]*\)".*/\1/p' "$lock" | head -1)"
    check "lock file version" "$mod_version" "$lock_version"

    # 4. The mirror: the artifact that actually ships.
    if [ ! -d "$mirror" ]; then
        log "  skip  mirror checks -- no .tools/tofu-mirror (run 'just mirror')"
        [ "$fail" -eq 0 ] || exit 1
        return
    fi
    local dir="$mirror/registry.opentofu.org/dmacvicar/libvirt"
    zip="$dir/terraform-provider-libvirt_${mod_version}_linux_amd64.zip"
    [ -f "$zip" ] || die "mirror has no $(basename "$zip")"

    zip_sha="$(sha256sum "$zip" | cut -d' ' -f1)"
    check "mirrored zip sha256 vs Containerfile" "$cf_sha" "$zip_sha"

    index_h1="$(jq -r '.archives.linux_amd64.hashes[] | select(startswith("h1:"))' "$dir/${mod_version}.json")"
    index_zh="$(jq -r '.archives.linux_amd64.hashes[] | select(startswith("zh:")) | ltrimstr("zh:")' "$dir/${mod_version}.json")"
    check "mirror index h1: vs committed lock" "$lock_h1" "$index_h1"
    check "mirror index zh: vs Containerfile"  "$cf_sha"  "$index_zh"

    # Exactly one provider, no strays left by an earlier version's mirror run.
    local n
    n="$(find "$mirror" -name '*.zip' | wc -l)"
    check "mirror holds exactly one provider zip" "1" "$n"

    [ "$fail" -eq 0 ] || die "provider facts disagree -- see FAIL lines above"
    log "all provider facts agree"
}

main "$@"
