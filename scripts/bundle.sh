#!/usr/bin/env bash
# Assembles the delivery bundle: the thing that actually goes on the medium.
#
#   scripts/bundle.sh    -> .cache/delivery/
#
# **This is the step that was missing.** README described the delivery as a
# `podman save | gzip` tarball that no script produced, while the only concrete
# artifact was the uncompressed docker-archive `just scan` writes for trivy and
# syft to seek. Two different byte streams were both called "the delivery
# tarball", which is how the old signing step came to sign one and ship the
# other. One script now produces one named artifact, and the name says which
# commit it came from.
#
# **Named from the archive, not from the worktree.** The version and revision are
# read out of the image config inside image.tar rather than from `git rev-parse`,
# because the archive can be older than HEAD -- someone edits, rebuilds, forgets
# to re-scan -- and a bundle named after the worktree would then claim a commit
# it does not contain. The worktree revision is still computed, only to warn when
# the two disagree.
#
# **Integrity, not authenticity.** SHA256SUMS catches corruption and a mismatched
# pairing. It does not catch substitution, because nothing here is signed; see
# "Why signing was removed" in docs/ci.md.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# One label out of the image config inside a docker-archive, without loading the
# image. manifest.json names the config blob; the config carries the labels the
# Containerfile set. Reading the archive rather than `podman inspect` on the tag
# is deliberate: the tag may have been rebuilt since this archive was written.
archive_label() {
    local archive="$1" key="$2" config value
    config="$(tar -xOf "$archive" manifest.json | jq -r '.[0].Config')"
    [ -n "$config" ] || die "no manifest.json in $archive -- not a docker-archive?"
    value="$(tar -xOf "$archive" "$config" | jq -r --arg k "$key" '.config.Labels[$k] // empty')"
    [ -n "$value" ] || die "image in $archive carries no $key label"
    printf '%s\n' "$value"
}

main() {
    need gzip jq

    local scan out archive sbom report version revision worktree name

    scan="$REPO/.cache/scan"
    archive="$scan/image.tar"
    sbom="$scan/sbom.spdx.json"
    report="$scan/trivy.json"
    for f in "$archive" "$sbom" "$report"; do
        [ -f "$f" ] || die "no $(basename "$f") -- run 'just scan' first, which writes it"
    done

    version="$(archive_label "$archive" org.opencontainers.image.version)"
    revision="$(archive_label "$archive" org.opencontainers.image.revision)"
    name="vcows-deploy-${version}-${revision}.tar.gz"

    # Not fatal. Delivering an older image on purpose is legitimate; delivering
    # one by accident because a rebuild was skipped is the thing worth saying out
    # loud, and the filename records which it was either way.
    worktree="$(source_revision)"
    if [ "$revision" != "$worktree" ]; then
        log "warning: the archive was built at $revision but the tree is at $worktree"
        log "         run 'just image && just scan' to bundle the current tree"
    fi

    out="$REPO/.cache/delivery"
    rm -rf "$out"
    mkdir -p "$out"

    # -n drops the stored filename and mtime, so the same archive always
    # compresses to the same bytes and the digest below identifies the content
    # rather than the moment it was packed.
    #
    # **gzip rather than pigz, and the 15x is worth paying.** pigz compressed
    # this archive in 5.5s against gzip's 82s, and 268 KB smaller. It is also not
    # byte-reproducible: measured over 12 runs on one host with identical input,
    # pigz produced two distinct outputs, 150516700 and 150516701 bytes -- one
    # byte apart, both decompressing to identical content. It is a deflate
    # block-framing difference that depends on how the parallel block assembly
    # happens to interleave, and it appeared roughly once in a dozen runs, which
    # is the worst possible frequency: often enough to happen to a real delivery,
    # rare enough to look like corruption rather than a known property. An
    # artifact whose identity is its digest cannot be built by something that
    # changes the digest without changing the content. gzip is single-threaded
    # and emits one deflate stream, so it has no such seam.
    log "compressing $(basename "$archive") -> $name"
    gzip -9 -n < "$archive" > "$out/$name"

    # The digest of what is *inside* the gzip, so a site can check after
    # decompressing as well as before. Written in `sha256sum -c` format so it is
    # usable directly rather than read by eye.
    ( cd "$scan" && sha256sum image.tar ) > "$out/image.tar.sha256"

    # The SBOM and the report travel with the image they describe. A loose SBOM
    # reconstructed later is an SBOM for whatever was current then.
    cp "$sbom" "$report" "$out/"

    # Named explicitly rather than globbed: a glob would depend on the shell
    # expanding words before performing the redirection to avoid hashing the
    # file being written, and a fixed order makes SHA256SUMS itself reproducible.
    ( cd "$out" && sha256sum \
        "$name" sbom.spdx.json trivy.json image.tar.sha256 > SHA256SUMS )

    log ""
    log "bundle  $out"
    log "  $name  ($(du -h "$out/$name" | cut -f1))"
    log "  sbom.spdx.json, trivy.json, image.tar.sha256, SHA256SUMS"
    log ""
    log "on receipt:  sha256sum -c SHA256SUMS"
    log "             gunzip -c $name | podman load"
    log ""
    log "not signed -- see 'Why signing was removed' in docs/ci.md"
}

main "$@"
