#!/usr/bin/env bash
# Builds the deliverable, implementing the invocation documented at the top of
# the Containerfile so it stops being a command people retype.
#
# **The `-dirty` suffix is the point.** `.git/` is outside the build context, so
# only the caller can compute the commit, and `container/manifest.py` records
# `unknown` rather than trust anything that is not 40 hex or 40 hex plus
# `-dirty`. The image built at e5d5a2c recorded a clean SHA for a commit that did
# not contain the `container/entrypoint.py` it shipped, which is exactly the
# question the manifest exists to answer.
#
# The dirty check covers only the paths the Containerfile COPYs. A change under
# docs/ or tests/ cannot reach the image, and flagging the build for one would
# make the suffix mean nothing. The lock file's name carries the provider
# version, so it is derived rather than hardcoded -- a version bump that left it
# spelled 0.9.8 here would quietly stop watching the file that ships.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

main() {
    local version provider tag builder ship sha dirty
    version="$(containerfile_arg VCOWS_VERSION)"
    provider="$(sed -n 's/.*version *= *"= *\([0-9.]*\)".*/\1/p' "$MODULE/main.tf" | head -1)"
    tag="${VCOWS_IMAGE_TAG:-localhost/vcows-deploy:${version}}"

    [ -d "$MIRROR" ] || die "no .tools/tofu-mirror -- run 'just mirror' first"

    # podman preferred. buildah takes the same Containerfile and the same flags
    # (podman build is buildah underneath), so it is a fine substitute *for
    # building* -- but `just test-image` still needs podman, because the gate
    # asserts ENTRYPOINT, WORKDIR and per-run isolation, and `buildah run`
    # honours none of the three.
    if have podman;   then builder=(podman build)
    elif have buildah; then builder=(buildah build)
        log "warning: building with buildah; 'just test-image' will still need podman"
    else die "neither podman nor buildah on PATH"
    fi

    ship=(orchestrator container licenses "docs/provider-${provider}.lock.hcl")
    sha="$(git -C "$REPO" rev-parse HEAD)"
    dirty=""
    if [ -n "$(git -C "$REPO" status --porcelain -- "${ship[@]}")" ]; then
        dirty="-dirty"
        log "warning: shipped paths are modified; recording ${sha}-dirty"
    fi

    log "building $tag"
    "${builder[@]}" -t "$tag" \
        --build-arg GIT_SHA="${sha}${dirty}" \
        --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$REPO"
    log "built $tag"
    log "run 'VCOWS_IMAGE=$tag just test-image' to exercise the offline gate"
}

main "$@"
