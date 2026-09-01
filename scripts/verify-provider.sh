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

need jq

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

    # Exactly one *libvirt* zip, no strays left by an earlier version's mirror
    # run. Scoped to this provider's directory since the mirror gained a second
    # one: a bare count over the whole mirror now says only how many backends
    # there are, which is not a fact worth asserting here.
    local n
    n="$(find "$dir" -name '*.zip' | wc -l)"
    check "mirror holds exactly one libvirt zip" "1" "$n"

    # -- the Proxmox provider, the same four places -------------------------
    local pve_module pve_version pve_cf_version pve_cf_sha pve_lock
    local pve_lock_h1 pve_dir pve_zip pve_zip_sha pve_index_h1 pve_index_zh
    pve_module="$REPO/orchestrator/backends/proxmox/tofu"
    pve_version="$(provider_version "$pve_module")"
    pve_cf_version="$(containerfile_arg PVE_PROVIDER_VERSION)"
    pve_cf_sha="$(containerfile_arg PVE_PROVIDER_SHA256)"
    check "Containerfile PVE_PROVIDER_VERSION" "$pve_version" "$pve_cf_version"

    pve_lock="$REPO/docs/provider-${pve_version}.lock.hcl"
    [ -f "$pve_lock" ] || die "no committed lock at docs/provider-${pve_version}.lock.hcl"
    pve_lock_h1="$(sed -n 's/.*"\(h1:[^"]*\)".*/\1/p' "$pve_lock" | head -1)"
    # Assigned first, then compared: inside `check "$(...)"` a failing sed is
    # indistinguishable from an empty match. Same reason lib.sh's
    # `source_revision` splits its git calls out (SC2312).
    local pve_lock_version
    pve_lock_version="$(sed -n 's/ *version *= *"\([^"]*\)".*/\1/p' "$pve_lock" | head -1)"
    check "proxmox lock file version" "$pve_version" "$pve_lock_version"

    pve_dir="$mirror/registry.opentofu.org/bpg/proxmox"
    pve_zip="$pve_dir/terraform-provider-proxmox_${pve_version}_linux_amd64.zip"
    if [ -f "$pve_zip" ]; then
        pve_zip_sha="$(sha256sum "$pve_zip" | cut -d' ' -f1)"
        check "mirrored proxmox zip sha256 vs Containerfile" "$pve_cf_sha" "$pve_zip_sha"
        pve_index_h1="$(jq -r '.archives.linux_amd64.hashes[] | select(startswith("h1:"))' "$pve_dir/${pve_version}.json")"
        pve_index_zh="$(jq -r '.archives.linux_amd64.hashes[] | select(startswith("zh:")) | ltrimstr("zh:")' "$pve_dir/${pve_version}.json")"
        check "proxmox mirror index h1: vs committed lock" "$pve_lock_h1" "$pve_index_h1"
        check "proxmox mirror index zh: vs Containerfile"  "$pve_cf_sha"  "$pve_index_zh"
    else
        die "mirror has no $(basename "$pve_zip")"
    fi

    # -- the one pip-installed dependency ------------------------------------
    # Not a provider, but the same kind of pin and the same failure: a version
    # bumped in one place and not the other. The provenance note is the second
    # record, and it is what a licence audit reads.
    local whl_version whl_sha prov
    whl_version="$(containerfile_arg PROXMOXER_VERSION)"
    whl_sha="$(containerfile_arg PROXMOXER_SHA256)"
    prov="$REPO/licenses/proxmoxer/PROVENANCE.md"
    local whl_v_hits whl_s_hits
    whl_v_hits="$(grep -c "\`$whl_version\`" "$prov" || true)"
    whl_s_hits="$(grep -c "$whl_sha" "$prov" || true)"
    check "proxmoxer version in PROVENANCE.md" "1" "$whl_v_hits"
    check "proxmoxer sha256 in PROVENANCE.md" "1" "$whl_s_hits"
    # The URL must name the version the ARG pins, or the build downloads one
    # wheel and every record describes another.
    grep -q "proxmoxer-${whl_version}-py3-none-any.whl" "$REPO/Containerfile" \
        || die "PROXMOXER_URL does not name proxmoxer-${whl_version}-py3-none-any.whl"
    log "  ok    proxmoxer URL names the pinned version"

    # Every backend's pinned version must have a committed lock beside it. The
    # filename carries only the version, so two providers that ever pin the same
    # version string would collide -- 0.9.8 and 0.111.1 do not. Renaming the
    # scheme to carry the provider is filed work, not done here.
    local module lock_v modules
    modules="$(backend_modules)"
    while read -r module; do
        lock_v="$(provider_version "$module")"
        [ -f "$REPO/docs/provider-${lock_v}.lock.hcl" ] \
            || die "no committed lock at docs/provider-${lock_v}.lock.hcl for $module"
    done <<< "$modules"

    [ "$fail" -eq 0 ] || die "provider facts disagree -- see FAIL lines above"
    log "all provider facts agree"
}

main "$@"
