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
# It stands in for a `podman run` line carrying five mounts and two SELinux
# label spellings. Credentials are inline in the config, so the three mounts
# left all carry data and each has a default.
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
  vcows.sh version
  vcows.sh validate  [-c FILE] [-i DIR] [-- PODMAN FLAGS]
  vcows.sh preflight [-c FILE] [-i DIR] [-r DIR] [-- PODMAN FLAGS]
  vcows.sh deploy    [-c FILE] [-i DIR] [-r DIR | --run-dir DIR] [-- PODMAN FLAGS]
  vcows.sh destroy   [-c FILE] [-i DIR] [-r DIR | --run-dir DIR] [-y] [-- PODMAN FLAGS]

  -c, --config FILE  the deployment config           (default ./config.yaml)
  -i, --images DIR   where the golden images are     (default ./images)
  -r, --runs DIR     where run records are written   (default ./runs)
      --run-dir DIR  this one run's own directory    (deploy and destroy)
  -y, --yes          destroy without being asked
  --                 pass everything after it to podman run

install verifies SHA256SUMS and loads the image, version reports what is inside
it, and neither takes flags nor mounts anything. Every VCOWS_* variable set here
is forwarded into the container.
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

    local name
    local -a opts=(--rm)

    # podman copies the value of a bare `-e NAME` from its own environment, so
    # this forwards whatever is set without the wrapper knowing any of the
    # names. VCOWS_LOG_LEVEL and the VCOWS_MAX_* ceilings are read from the
    # container's environment, and this is their only way in.
    for name in "${!VCOWS_@}"; do opts+=(-e "$name"); done

    case "$verb" in
        install)
            [ $# -eq 0 ] || usage
            install
            return
            ;;
        # No config and no mounts: it answers what is inside the image, which is
        # the question a site has before it has written a config.
        version)
            [ $# -eq 0 ] || usage
            exec podman run "${opts[@]}" "$IMAGE" version
            ;;
        validate | preflight | deploy | destroy) ;;
        *) usage ;;
    esac

    # `runs` starts empty rather than at its default so that it can be told
    # apart from `--run-dir`, which is unset: the two name different things --
    # a directory to write runs *under*, and the run's own directory -- and both
    # arrive at the same `/runs` mount, so taking both is a contradiction.
    local config="config.yaml" images="images" runs="" run_dir="" yes=""
    local -a extra=()
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
            --run-dir)
                [ $# -ge 2 ] || usage
                # deploy and destroy only, because the container's argparse
                # accepts it on those two and would refuse it later otherwise.
                case "$verb" in deploy | destroy) ;; *) usage ;; esac
                run_dir="$2"
                shift 2
                ;;
            --)
                shift
                extra=("$@")
                break
                ;;
            -y | --yes)
                [ "$verb" = destroy ] || usage
                yes=1
                shift
                ;;
            *) usage ;;
        esac
    done
    if [ -n "$run_dir" ] && [ -n "$runs" ]; then
        usage
    fi

    # Every path is checked here rather than left to podman, and then made
    # absolute: podman reads a relative `-v` source as a *named volume*, so
    # `-c sub/config.yaml` would mount an empty volume over /config.yaml and the
    # failure would name neither the file nor the flag.
    if ! [ -f "$config" ] || ! [ -r "$config" ]; then
        die "$config is not a readable file (-c/--config)"
    fi
    config="$(realpath "$config")"
    local -a mounts=(-v "$config:/config.yaml:ro,z")
    local -a args=("$verb" /config.yaml)

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
            # `--run-dir` mounts the run's own directory at /runs and names it
            # inside the container, so vcows writes the record into the mount
            # rather than into a subdirectory of it. That is the shape README
            # describes, and the one that works under `--user`.
            local dir="${runs:-runs}" flag="-r/--runs"
            if [ -n "$run_dir" ]; then
                dir="$run_dir"
                flag="--run-dir"
            fi
            mkdir -p "$dir" || die "$dir cannot be made a directory ($flag)"
            [ -w "$dir" ] || die "$dir is not a writable directory ($flag)"
            dir="$(realpath "$dir")"
            # `:Z` relabels the host path into a category private to one
            # container, which is right for a directory belonging to one run and
            # wrong for the config and the shared golden images above.
            mounts+=(-v "$dir:/runs:Z")
            [ -z "$run_dir" ] || args+=(--run-dir /runs)
            ;;
    esac

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

    opts+=("${extra[@]}")
    exec podman run "${opts[@]}" "${mounts[@]}" "$IMAGE" "${args[@]}"
}

main "$@"
