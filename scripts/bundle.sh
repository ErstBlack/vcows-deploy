#!/usr/bin/env bash
# Assembles the delivery bundle: the thing that actually goes on the medium.
#
#   scripts/bundle.sh    -> .cache/delivery/
#
# **One script, one named artifact, and the name says which commit it came
# from.** The other byte stream in this repo is the uncompressed docker-archive
# `just scan` writes for trivy and syft to read; that one is not the delivery.
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

# The tag `podman load` will restore, which is the one vcows.sh has to name.
# Read from the archive for the same reason the labels above are, and for one
# more: `image_tag` computes a tag from the Containerfile, and a worktree suffix
# or an edit since the build moves it away from what the archive stores.
archive_tag() {
    local archive="$1" tag
    tag="$(tar -xOf "$archive" manifest.json | jq -r '.[0].RepoTags[0] // empty')"
    [ -n "$tag" ] || die "image in $archive carries no RepoTags -- built without a tag?"
    printf '%s\n' "$tag"
}

main() {
    need gzip jq

    local scan out archive sbom report version revision worktree name tag

    scan="$REPO/.cache/scan"
    archive="$scan/image.tar"
    sbom="$scan/sbom.spdx.json"
    report="$scan/trivy.json"
    for f in "$archive" "$sbom" "$report"; do
        [ -f "$f" ] || die "no $(basename "$f") -- run 'just scan' first, which writes it"
    done

    # The loop above asks whether `just scan` *ran*. This asks whether it
    # *passed*, which is a different question. A scan that dies on a new finding
    # leaves all three files above complete and current, because image-scan.sh
    # writes them before it reads the baseline -- so without this stamp the
    # bundle is correctly named, internally consistent and verified by its own
    # SHA256SUMS, with the rejected id sitting inside the trivy.json it ships.
    #
    # The digest in the stamp is what makes it mean something. It binds the
    # verdict to the bytes the gate accepted, so a stamp left by an earlier pass
    # cannot vouch for an image.tar written since -- by a second scan that died
    # after rewriting it, or by a hand copy. sha256sum over 444 MB is 2.0s
    # against the delivery `gzip -9`'s 86s, and it is deliberately not folded
    # into the `sha256sum image.tar` that writes image.tar.sha256 for the
    # delivery: one is an artifact a site checks, one is an internal verdict,
    # and coupling them would let a change to the stamp reshape a file
    # README.md describes to sites.
    #
    # Fatal, unlike the revision warning below, because the two answer questions
    # of different shapes. "Is this archive the current tree?" has a legitimate
    # no, which is why that one only warns. "Did the CVE gate accept this
    # archive?" does not.
    [ -f "$scan/PASSED" ] ||
        die "no PASSED stamp in .cache/scan -- 'just scan' has not accepted this archive. Run it and read what it says before bundling."
    ( cd "$scan" && sha256sum -c --status PASSED ) ||
        die "the PASSED stamp in .cache/scan does not describe image.tar -- re-run 'just scan'"

    tag="$(archive_tag "$archive")"
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

    # The wrapper, naming the tag the archive stores rather than the one this
    # tree would compute. `sed` rather than `cp`: it is the only file in the
    # bundle whose contents depend on which image it is shipped beside.
    sed "s|@IMAGE@|$tag|" "$REPO/scripts/vcows.sh" > "$out/vcows.sh"
    chmod 0755 "$out/vcows.sh"

    # The template and the site instructions, unmodified. The template is
    # refused as it stands -- every value a site must supply is a placeholder
    # the schema rejects -- so a copy that was never edited is stopped by
    # `validate` rather than at a hypervisor.
    cp "$REPO/config.example.yaml" "$REPO/SITE.md" "$out/"

    # Named explicitly rather than globbed: a glob would depend on the shell
    # expanding words before performing the redirection to avoid hashing the
    # file being written, and a fixed order makes SHA256SUMS itself reproducible.
    ( cd "$out" && sha256sum \
        "$name" sbom.spdx.json trivy.json image.tar.sha256 vcows.sh \
        config.example.yaml SITE.md > SHA256SUMS )

    log ""
    log "bundle  $out"
    log "  $name  ($(du -h "$out/$name" | cut -f1 || true))"
    log "  vcows.sh  ($tag)"
    log "  config.example.yaml, SITE.md"
    log "  sbom.spdx.json, trivy.json, image.tar.sha256, SHA256SUMS"
    log ""
    log "on receipt:  ./vcows.sh install, then SITE.md"
    log ""
    log "not signed -- see 'Why signing was removed' in docs/ci.md"
}

main "$@"
