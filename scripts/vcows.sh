#!/usr/bin/env bash
# The wrapper a site runs. Shipped in the delivery bundle beside the image.
#
# **The one script in here that sources nothing.** Everything else is
# `source lib.sh`; this one leaves the repository entirely and lands on a medium
# that carries no `scripts/` directory. It lives here anyway so that `just lint`
# reads it with the same four optional shellcheck checks as the rest. (A comment
# line beginning with the word `shellcheck` is parsed as a directive, which is
# why this one does not.)
#
# What it replaces is five lines of `podman run` in README.md carrying five
# mounts and two SELinux label spellings, two of which were credentials the
# config already named by path. Those are inline in the config now, so the
# mounts left are the three that carry data, and each has a default.
#
# `bundle.sh` substitutes IMAGE below with the tag stored inside `image.tar`,
# which is what `podman load` restores -- worktree suffix included. Run out of
# the repository it is the literal placeholder.

set -euo pipefail

IMAGE="@IMAGE@"

usage() {
    cat >&2 <<'EOF'
usage:
  vcows.sh install
  vcows.sh validate  [-c FILE] [-i DIR]
  vcows.sh preflight [-c FILE] [-i DIR] [-r DIR]
  vcows.sh deploy    [-c FILE] [-i DIR] [-r DIR]
  vcows.sh destroy   [-c FILE] [-i DIR] [-r DIR] [-y]

  -c, --config FILE  the deployment config           (default ./config.yaml)
  -i, --images DIR   where the golden images are     (default ./images)
  -r, --runs DIR     where run records are written   (default ./runs)
  -y, --yes          destroy without being asked

install verifies SHA256SUMS and loads the image, and takes no flags.
EOF
    exit 2
}

die() {
    printf 'vcows.sh: %s\n' "$*" >&2
    exit 1
}

# Nothing here reads the tarball's name from SHA256SUMS: the list also covers the
# SBOM, the report and this script, and a bundle carrying two deliveries would
# verify clean and still leave no answer as to which image was meant.
install() {
    local here
    here="$(dirname "$0")"
    cd "$here" || die "$here is not a directory to run from"
    [ -f SHA256SUMS ] ||
        die "no SHA256SUMS in $PWD -- run this from the unpacked delivery bundle"
    sha256sum -c SHA256SUMS >&2 ||
        die "SHA256SUMS does not describe $PWD; the bundle is damaged or incomplete"

    local -a archives
    shopt -s nullglob
    archives=(vcows-deploy-*.tar.gz)
    shopt -u nullglob
    [ "${#archives[@]}" -eq 1 ] ||
        die "expected exactly one vcows-deploy-*.tar.gz in $PWD, found ${#archives[@]}"

    gunzip -c "${archives[0]}" | podman load >&2
    # The archive stores its own tag, so this asks whether the bundle and this
    # script came from the same build rather than whether podman said "Loaded".
    podman image exists "$IMAGE" ||
        die "${archives[0]} loaded but left no $IMAGE -- this script came from another build"
    printf '%s\n' "loaded $IMAGE" >&2
}

main() {
    [ $# -ge 1 ] || usage
    local verb="$1"
    shift

    if [ "$verb" = install ]; then
        [ $# -eq 0 ] || usage
        install
        return
    fi
    case "$verb" in
        validate | preflight | deploy | destroy) ;;
        *) usage ;;
    esac

    local config="config.yaml" images="images" runs="runs" yes=""
    while [ $# -gt 0 ]; do
        case "$1" in
            -c | --config)
                [ $# -ge 2 ] || usage
                config="$2"
                shift 2
                ;;
            -i | --images)
                [ $# -ge 2 ] || usage
                images="$2"
                shift 2
                ;;
            -r | --runs)
                [ $# -ge 2 ] || usage
                runs="$2"
                shift 2
                ;;
            -y | --yes)
                [ "$verb" = destroy ] || usage
                yes=1
                shift
                ;;
            *) usage ;;
        esac
    done

    # Every path is checked here rather than left to podman, and then made
    # absolute: podman reads a relative `-v` source as a *named volume*, so
    # `-c sub/config.yaml` would mount an empty volume over /config.yaml and the
    # failure would name neither the file nor the flag.
    if ! [ -f "$config" ] || ! [ -r "$config" ]; then
        die "$config is not a readable file (-c/--config)"
    fi
    config="$(realpath "$config")"
    local -a mounts=(-v "$config:/config.yaml:ro,z")

    # `mkdir -p` because a site's first run has neither directory, and a tool
    # that refuses to start until two empty ones exist is a step that gets
    # skipped. A path that exists as something else fails at the mkdir, before
    # anything is relabelled.
    #
    # Images for every verb, not only the two that create: `validate` checks
    # `disk_gb` against the image offline, and `destroy` runs the same checks
    # before it tears down -- measured, without the mount each of them warns
    # that a file sitting in ./images cannot be read.
    mkdir -p "$images" || die "$images cannot be made a directory (-i/--images)"
    [ -r "$images" ] || die "$images is not a readable directory (-i/--images)"
    images="$(realpath "$images")"
    mounts+=(-v "$images:/images:ro,z")
    case "$verb" in
        preflight | deploy | destroy)
            mkdir -p "$runs" || die "$runs cannot be made a directory (-r/--runs)"
            [ -w "$runs" ] || die "$runs is not a writable directory (-r/--runs)"
            runs="$(realpath "$runs")"
            # `:Z` relabels the host path into a category private to one
            # container, which is right for a directory belonging to one run and
            # wrong for the config and the shared golden images above.
            mounts+=(-v "$runs:/runs:Z")
            ;;
    esac

    local -a opts=(--rm)
    local -a args=("$verb" /config.yaml)
    if [ "$verb" = destroy ]; then
        if [ -n "$yes" ]; then
            args+=(--yes)
        elif [ -t 0 ]; then
            # Only for a terminal, and only when there is a prompt to answer.
            # vcows refuses a non-tty stdin with a sentence of its own; `-it`
            # against one would put podman's error in front of it.
            opts+=(-it)
        fi
    fi

    exec podman run "${opts[@]}" "${mounts[@]}" "$IMAGE" "${args[@]}"
}

main "$@"
