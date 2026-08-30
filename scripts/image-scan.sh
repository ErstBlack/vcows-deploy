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
    if have podman; then
        podman save --format docker-archive -o "$out" "$tag"
    elif have buildah; then
        buildah push --format docker "$tag" "docker-archive:${out}:${tag}"
    else
        die "neither podman nor buildah on PATH"
    fi
}

main() {
    have trivy || die "trivy not on PATH -- run scripts/install-tools.sh"
    have syft  || die "syft not on PATH -- run scripts/install-tools.sh"
    have jq    || die "jq not on PATH -- run scripts/os-deps.sh"

    local version tag out archive report sbom found new
    version="$(containerfile_arg VCOWS_VERSION)"
    tag="${VCOWS_IMAGE_TAG:-localhost/vcows-deploy:${version}}"
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

    found="$(jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique | .[]' "$report")"

    if [ "${1:-}" = "--write-baseline" ]; then
        jq -n --arg image "$tag" \
              --arg generated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
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
    log "no findings outside the baseline"
}

main "$@"
