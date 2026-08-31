#!/usr/bin/env bash
# Scans the built image and writes an SBOM beside it.
#
#   scripts/image-scan.sh                    fail on anything not in the baseline
#   scripts/image-scan.sh --write-baseline   record what is there now as accepted
#
# **Differential, not absolute.** A gate that fails on HIGH would be red from the
# first run and stay red: the pinned tofu and provider binaries carry HIGH CVEs
# in the golang.org/x/crypto/ssh they statically link, and no pipeline can fix
# that -- only a version bump can. An always-red gate gets muted within a month,
# and then it is green by neglect, which is the failure this repo already has a
# name for. So docs/cve-baseline.json holds what has been looked at and accepted,
# and this fails only on what is new. Red means new.
#
# **One real archive, not a process substitution.** `trivy --input <(podman save
# ...)` hands trivy a FIFO, and a tar in a pipe is not seekable, so it works
# until it doesn't. The archive is written once and read by trivy, syft, and
# whatever signs it later.
#
# syft is here because it is the only tool that sees inside the two Go binaries:
# manifest.py runs `rpm -qa`, which cannot reach a statically linked module list,
# and the provider is not an RPM at all.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

BASELINE="$REPO/docs/cve-baseline.json"

save_archive() {
    local tag="$1" out="$2"
    # podman refuses to write into an existing docker-archive ("doesn't support
    # modifying existing images"), so a second scan fails unless the previous
    # archive is cleared first.
    rm -f "$out"
    if have podman; then
        podman save --format docker-archive -o "$out" "$tag"
    elif have buildah; then
        buildah push --format docker "$tag" "docker-archive:${out}:${tag}"
    else
        die "neither podman nor buildah on PATH"
    fi
}

# A differential gate can only compare what it was handed. If trivy analysed
# nothing -- a database that failed to download, a report schema that moved, a jq
# path that stopped matching -- then `found` is empty, `comm -13` is empty, and
# this script logs "no findings outside the baseline" and exits 0. That is green
# by neglect arriving through the front door, and it is the one failure mode a
# subset check cannot see. So assert the scan happened before trusting silence.
#
# The floors are structural, not vulnerability counts: an image with genuinely
# zero CVEs must still pass. What cannot legitimately happen is a report with no
# results section at all, or an SBOM with no packages for an image that carries
# several hundred. Measured at the current pins: 3 Results, 456 packages.
scan_floor() {
    local report="$1" sbom="$2" results packages
    results="$(jq '(.Results // []) | length' "$report")"
    [ "$results" -gt 0 ] ||
        die "trivy wrote no .Results -- the scan analysed nothing. Check the vulnerability database and the report schema before trusting an empty finding set."
    packages="$(jq '(.packages // []) | length' "$sbom")"
    [ "$packages" -gt 0 ] ||
        die "syft found no packages -- an empty SBOM for an image built from a full base is a broken scan, not a clean one."
}

main() {
    need trivy syft jq

    local tag out archive report sbom found new gone accepted missing
    tag="$(image_tag)"
    out="$REPO/.cache/scan"; mkdir -p "$out"
    archive="$out/image.tar"
    report="$out/trivy.json"
    sbom="$out/sbom.spdx.json"

    log "saving $tag"
    save_archive "$tag" "$archive"

    log "scanning"
    trivy image --quiet --input "$archive" --format json --output "$report"
    syft scan "docker-archive:$archive" -q -o spdx-json="$sbom"
    log "  report $report"
    log "  sbom   $sbom"

    # Before the baseline is consulted, and before --write-baseline can record an
    # empty set as "accepted", which would bake the broken scan in.
    scan_floor "$report" "$sbom"

    found="$(jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique | .[]' "$report")"

    if [ "${1:-}" = "--write-baseline" ]; then
        jq -n --arg image "$tag" \
              --arg generated "$(now_utc)" \
              --argjson accepted "$(printf '%s' "$found" | jq -R . | jq -s .)" \
              '{image: $image, generated: $generated,
                note: "Findings reviewed and accepted at this image. Most live in the statically linked golang.org/x/crypto/ssh inside /usr/bin/tofu and the vendored terraform-provider-libvirt, and can only be cleared by bumping those pins. Anything not listed here fails scripts/image-scan.sh.",
                accepted: $accepted}' > "$BASELINE"
        log "wrote $(basename "$BASELINE") with $(printf '%s' "$found" | grep -c . || true) accepted findings"
        return
    fi

    if [ ! -f "$BASELINE" ]; then
        log "no docs/cve-baseline.json yet -- $(printf '%s' "$found" | grep -c . || true) findings, none classified"
        die "run 'scripts/image-scan.sh --write-baseline' once the pins are settled"
    fi

    new="$(comm -13 \
        <(jq -r '.accepted[]' "$BASELINE" | sort) \
        <(printf '%s\n' "$found" | sort))"
    if [ -n "$new" ]; then
        log "findings not in docs/cve-baseline.json:"
        printf '%s\n' "$new" | sed 's/^/  /' >&2
        die "$(printf '%s' "$new" | grep -c .) new finding(s)"
    fi

    # The other direction. One or two accepted ids disappearing is ordinary --
    # a pin bump fixed them, and the baseline should be trimmed. *All* of them
    # disappearing at once is not a clean image; it is a scan that did not read
    # this image, and scan_floor's structural checks cannot catch it because the
    # report is well-formed and simply about something else.
    gone="$(comm -23 \
        <(jq -r '.accepted[]' "$BASELINE" | sort) \
        <(printf '%s\n' "$found" | sort))"
    accepted="$(jq '.accepted | length' "$BASELINE")"
    missing="$(printf '%s' "$gone" | grep -c . || true)"
    if [ "$accepted" -gt 0 ] && [ "$missing" -eq "$accepted" ]; then
        die "none of the $accepted accepted findings are present -- the scan did not read this image"
    fi
    if [ -n "$gone" ]; then
        log "baseline entries no longer found ($missing of $accepted; stale, or fixed by a pin bump):"
        printf '%s\n' "$gone" | sed 's/^/  /' >&2
    fi
    log "no findings outside the baseline"
}

main "$@"
