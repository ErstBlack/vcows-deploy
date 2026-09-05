#!/usr/bin/env bash
# Builds the deliverable, implementing the invocation documented at the top of
# the Containerfile so it stops being a command people retype.
#
# **The `-dirty` suffix is the point.** `.git/` is outside the build context, so
# only the caller can compute the commit, and `container/manifest.py` records
# `unknown` rather than trust anything that is not 40 hex or 40 hex plus
# `-dirty`. Without the suffix an image records a clean SHA for a commit that
# does not contain the `container/entrypoint.py` it shipped, which is exactly the
# question the manifest exists to answer.
#
# The dirty check covers the paths that reach the image and nothing else: a
# change under docs/ or tests/ cannot get in, and flagging the build for one
# would make the suffix mean nothing. It lives in `source_revision` in lib.sh
# rather than here, because the delivery bundle names the same commit and the
# two must not be able to disagree.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

main() {
    local tag builder sha built
    tag="$(image_tag)"

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

    sha="$(source_revision)"
    # Not inline in --build-arg below: there a now_utc that fails is masked and
    # the image ships a BUILD_DATE label that is empty rather than absent. SC2312.
    built="$(now_utc)"

    log "building $tag"
    "${builder[@]}" -t "$tag" \
        --build-arg GIT_SHA="$sha" \
        --build-arg BUILD_DATE="$built" \
        "$REPO"
    log "built $tag"
    log "run 'just test-image' to exercise the offline gate"
}

main "$@"
