#!/usr/bin/env bash
# Builds or checks .tools/tofu-mirror, the filesystem provider mirror the `tofu`
# test gate needs (tests/conftest.py) and the one path `podman build` reads that
# a fresh clone does not have.
#
#   scripts/mirror.sh                 download, verify, install
#   scripts/mirror.sh --verify-only   check an existing (or cache-restored) one
#   scripts/mirror.sh --ensure        verify if present, build if absent
#
# --ensure is what both CI pipelines call, so neither has to branch on whether
# the cache hit. GitLab exposes no cache-hit flag at all, and expressing the same
# thing two different ways is how the two files start to drift.
#
# Three things here are less obvious than they look.
#
# **`providers mirror` runs against the module directory directly.** Verified
# against 1.12.6: it writes nothing into the source tree -- no `.terraform/`, no
# lock -- because the module declares no `module` or `backend` blocks and so has
# no installation step to persist. `providers lock` is the opposite and *does*
# write `.terraform.lock.hcl` where it runs, which is why that half happens under
# `-chdir` in a temp directory. A stray lock file under orchestrator/ would make
# every later image build compute a `-dirty` SHA for a reason nobody could find.
#
# **The mirror is built fresh and swapped in, never updated in place.**
# `providers mirror` adds to an existing mirror rather than pruning it, so
# refreshing a cached one across a version bump would leave both versions there.
#
# **--verify-only re-checks the digest on every restore, not only on build.**
# GitLab's cache overwrites the same key at the end of every job, so a restored
# mirror is untrusted input each run -- unlike actions/cache, which refuses to
# overwrite an existing key. The check costs ~200ms on 26MB.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Regenerate the lock against the *mirror* and diff it against the committed one.
# Per findings.md R6 a lock built against the registry records different hashes
# than one built against a mirror, and the mismatch surfaces as a checksum error
# that reads like corruption. CI's job is to prove the committed lock is still
# correct, not to quietly rewrite it.
lock_matches() {
    local mirror="$1" version="$2" tmp
    tmp="$TMP/lockdir"; mkdir -p "$tmp"
    cp "$MODULE"/*.tf "$tmp/"
    tofu -chdir="$tmp" providers lock \
        -fs-mirror="$mirror" -platform=linux_amd64 >/dev/null
    if diff -u "$REPO/docs/provider-${version}.lock.hcl" "$tmp/.terraform.lock.hcl"; then
        log "  ok    committed lock matches one generated against the mirror"
    else
        die "docs/provider-${version}.lock.hcl is stale -- diff above"
    fi
}

main() {
    need tofu
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    local version; version="$(provider_version)"

    local mode="${1:-}"
    if [ "$mode" = "--ensure" ]; then
        if [ -d "$MIRROR" ]; then mode=--verify-only; else mode=""; fi
    fi

    if [ "$mode" != "--verify-only" ]; then
        log "mirroring dmacvicar/libvirt $version"
        # -platform explicitly, never the host default: free today, and the
        # difference between working and silently producing a darwin or arm64
        # mirror the day someone runs this somewhere else.
        tofu -chdir="$MODULE" providers mirror \
            -platform=linux_amd64 "$TMP/mirror" >/dev/null
        # Verify before installing, so a bad download never becomes the mirror.
        "$REPO/scripts/verify-provider.sh" "$TMP/mirror"
        lock_matches "$TMP/mirror" "$version"
        rm -rf "$MIRROR"
        mkdir -p "$(dirname "$MIRROR")"
        mv "$TMP/mirror" "$MIRROR"
        log "installed .tools/tofu-mirror"
    else
        [ -d "$MIRROR" ] || die "no mirror at .tools/tofu-mirror -- run 'just mirror'"
        "$REPO/scripts/verify-provider.sh"
        lock_matches "$MIRROR" "$version"
    fi
}

main "$@"
