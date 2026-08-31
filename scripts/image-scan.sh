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
# path that stopped matching -- then `found` is empty, its difference against the
# baseline is empty, and
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

    local tag out archive report sbom found delta new gone accepted missing
    tag="$(image_tag)"
    out="$REPO/.cache/scan"; mkdir -p "$out"
    archive="$out/image.tar"
    report="$out/trivy.json"
    sbom="$out/sbom.spdx.json"

    # **The verdict has to outlive this process.** Everything this script writes
    # -- archive at :92, report and SBOM at :95-96 -- is on disk and complete
    # before the baseline is read at :128, so a passing .cache/scan and a failing
    # one are byte-identical apart from what is inside trivy.json, and nothing
    # reads that for a verdict. README.md:262-264 documents `just scan` and
    # `just bundle` as separate commands, so the second routinely runs in a
    # different shell, where an exit status is not available to be asked. The
    # answer therefore has to be a file, in the directory that is already the
    # interface between the two scripts.
    #
    # Cleared here rather than in save_archive so that a crash anywhere after
    # this point -- in podman, trivy, syft, scan_floor, or either baseline check
    # -- leaves no stamp behind for bundle.sh to find.
    rm -f "$out/PASSED"

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

    # Kept as a JSON array. Every question below is a set operation, and jq can
    # answer all of them without the ids ever becoming text.
    found="$(jq -c '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique' "$report")"

    if [ "${1:-}" = "--write-baseline" ]; then
        jq -n --arg image "$tag" \
              --arg generated "$(now_utc || true)" \
              --argjson accepted "$found" \
              '{image: $image, generated: $generated,
                note: "Findings reviewed and accepted at this image. Most live in the statically linked golang.org/x/crypto/ssh inside /usr/bin/tofu and the vendored terraform-provider-libvirt, and can only be cleared by bumping those pins. Anything not listed here fails scripts/image-scan.sh.",
                accepted: $accepted}' > "$BASELINE"
        log "wrote $(basename "$BASELINE") with $(jq 'length' <<<"$found" || true) accepted findings"
        return
    fi

    if [ ! -f "$BASELINE" ]; then
        log "no docs/cve-baseline.json yet -- $(jq 'length' <<<"$found" || true) findings, none classified"
        die "run 'scripts/image-scan.sh --write-baseline' once the pins are settled"
    fi

    # One read of the baseline, answering all three questions asked of it. jq's
    # `-` is set difference; `found` arrives sorted from `unique` and `.accepted`
    # is stored sorted, so both lists come out in the order the old sort-and-comm
    # pair produced.
    delta="$(jq -c --argjson found "$found" '{
        new:      ($found - .accepted),
        gone:     (.accepted - $found),
        accepted: (.accepted | length)
    }' "$BASELINE")"

    new="$(jq -r '.new[]' <<<"$delta")"
    if [ -n "$new" ]; then
        log "findings not in docs/cve-baseline.json:"
        printf '%s\n' "$new" | sed 's/^/  /' >&2
        die "$(jq '.new | length' <<<"$delta" || true) new finding(s)"
    fi

    # The other direction. One or two accepted ids disappearing is ordinary --
    # a pin bump fixed them, and the baseline should be trimmed. *Most* of them
    # disappearing at once is not a clean image; it is a scan that did not read
    # this image, and scan_floor's structural checks cannot catch it because the
    # report is well-formed and simply about something else.
    #
    # **A proportion, and the proportion is measured.** trivy emits one Results
    # entry per analyser x target. This image has three, in two disjoint
    # families: os-pkgs over the rocky layer, and lang-pkgs over the two Go
    # binaries. Measured against the real report, rocky shares no id with either
    # binary, so an analyser that stops running takes 45 or 56 of the 100 with
    # it -- a large slice, and never the whole set. Equality needs *both*
    # families to fail at once, and a report with no Results at all is
    # scan_floor's job already, so the equality this used to test sat where
    # nothing realistic lands. `* 3` first fires at 34 of 100: below the 45 an
    # emptied gobinary analyser costs, and 33 clear of the 1 a real scan reports
    # today. Halving was measured and rejected -- it first fires at 51, above
    # the 45 it exists to catch. Multiplication rather than `accepted / 3` so
    # the firing point does not depend on how integer division rounds.
    gone="$(jq -r '.gone[]' <<<"$delta")"
    accepted="$(jq '.accepted' <<<"$delta")"
    missing="$(jq '.gone | length' <<<"$delta")"
    if [ "$accepted" -gt 0 ] && [ $((missing * 3)) -gt "$accepted" ]; then
        die "$missing of $accepted accepted findings are absent -- more than a third of the baseline vanished at once. That is a scan that did not read this image, not a clean one."
    fi
    if [ -n "$gone" ]; then
        log "baseline entries no longer found ($missing of $accepted; stale, or fixed by a pin bump):"
        printf '%s\n' "$gone" | sed 's/^/  /' >&2
    fi
    log "no findings outside the baseline"

    # The last act of a passing run, and the only thing that writes this file.
    # It holds `sha256sum image.tar` output -- one line, one format bundle.sh
    # already produces at :119 -- so the verdict is bound to the bytes it was
    # reached about and cannot be inherited by an archive a later run wrote. No
    # timestamp: delivering an older image on purpose is legitimate, so an age
    # limit would refuse a bundle for a reason that is not the CVE verdict.
    #
    # --write-baseline returns at :116, above this and below the rm, so
    # recording what is there now never authorises a bundle.
    ( cd "$out" && sha256sum image.tar ) > "$out/PASSED"
}

main "$@"
