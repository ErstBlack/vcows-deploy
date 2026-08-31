#!/usr/bin/env bash
# Installs the pinned tool binaries this repo builds and checks itself with, into
# .tools/bin. Called directly and never through `just`, because it is what puts
# `just` on a runner that has none.
#
# **Pinned tarballs with hardcoded digests, not CI marketplace actions.** The
# Containerfile already downloads OpenTofu from a pinned URL and runs
# `sha256sum -c -` before installing it; `astral-sh/setup-uv@v3` is a mutable tag
# running somebody else's code on the runner with the job's token. Matching the
# Containerfile's standard is the stronger of the two, and it is the reason
# `.github/` can be deleted at the GitLab migration without losing a mechanism.
#
# Digests below were taken from each project's own published checksums file at
# the pinned tag. Refreshing one means fetching that file again, not trusting a
# download.
#
# The tofu version is read out of the Containerfile rather than pinned here, so
# CI cannot end up testing a different OpenTofu than the image ships. Bumping the
# Containerfile without adding a digest below is a hard failure, which is the
# intended way to find out.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

UV_VERSION=0.12.7
JUST_VERSION=1.46.0
HADOLINT_VERSION=2.15.1
TRIVY_VERSION=0.74.0
SYFT_VERSION=1.51.1

# tool:version -> sha256 of the artifact named in fetch() below.
digest() {
    case "$1" in
        uv:0.12.7)        echo 788f18abea7c5f55d6216e4f5613fd89d4d59b631efeec117b2b07fe72f1da21 ;;
        just:1.46.0)      echo 79966e6e353f535ee7d1c6221641bcc8e3381c55b0d0a6dc6e54b34f9db36eaa ;;
        hadolint:2.15.1)  echo c7187db94eeeeca956519a6af171adc31453941a1e777961f6e680f697c8c507 ;;
        trivy:0.74.0)     echo 2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a ;;
        syft:1.51.1)      echo 8fcb33017a0dc1058298c923c436d19dfa68ae93968e0b423248542e3afb9fc3 ;;
        tofu:1.12.6)      echo 5dc43da4f750f33873dc25e94587128709e819e544b7be9016b255316153c3a8 ;;
        *) die "no pinned digest for $1 -- add it from the project's published checksums file" ;;
    esac
}

url() {
    case "$1" in
        uv)         echo "https://github.com/astral-sh/uv/releases/download/${2}/uv-x86_64-unknown-linux-gnu.tar.gz" ;;
        just)       echo "https://github.com/casey/just/releases/download/${2}/just-${2}-x86_64-unknown-linux-musl.tar.gz" ;;
        hadolint)   echo "https://github.com/hadolint/hadolint/releases/download/v${2}/hadolint-linux-x86_64" ;;
        trivy)      echo "https://github.com/aquasecurity/trivy/releases/download/v${2}/trivy_${2}_Linux-64bit.tar.gz" ;;
        syft)       echo "https://github.com/anchore/syft/releases/download/v${2}/syft_${2}_linux_amd64.tar.gz" ;;
        # The .zip rather than the .rpm the image installs: one artifact that
        # works on any runner, with no package manager and no second digest to
        # keep in step with TOFU_RPM_SHA256.
        tofu)       echo "https://github.com/opentofu/opentofu/releases/download/v${2}/tofu_${2}_linux_amd64.zip" ;;
    esac
}

# Verified download into a scratch dir. The digest check is `sha256sum -c -`
# rather than a string compare so a truncated download fails here instead of
# unpacking into something surprising.
fetch() {
    local tool="$1" version="$2" dest="$3" want file src
    # Here rather than in main(): a box that already carries all six tools never
    # reaches this function, and should not be refused for lacking curl.
    need curl
    want="$(digest "${tool}:${version}")"
    src="$(url "$tool" "$version")"
    [ -n "$src" ] || die "$tool: no download URL -- add a case arm to url()"
    file="$dest/$(basename "$src")"
    curl -fsSL --retry 3 -o "$file" "$src"
    echo "${want}  ${file}" | sha256sum -c - >/dev/null
    printf '%s\n' "$file"
}

# A regular, executable file -- not merely something with the execute bit, which
# every directory has.
installed() {
    [ -f "$TOOLS_BIN/$1" ] && [ -x "$TOOLS_BIN/$1" ]
}

# The version a binary on PATH reports, or empty when it will not say. All six
# put a dotted version in the first line of `--version` -- measured: `just
# 1.46.0`, `OpenTofu v1.12.6`, `Haskell Dockerfile Linter 2.15.1`,
# `Version: 0.74.0`, `syft 1.51.1`, `uv 0.12.5` -- so one extraction serves all
# six and there is no per-tool case arm to keep in step with the pins above.
#
# Two failures, and they must not read the same. A tool that prints no dotted
# triple is the empty return this function is documented to make, and install_one
# has a `${found:-version unknown}` written for it -- but as one pipeline the
# grep's no-match was status 1, pipefail made the whole pipeline 1, and `set -e`
# aborted install_one before its own log line. The `:-` could never fire. A tool
# that cannot run at all is the other failure and still returns 1, so the
# assignment is split: only the grep's no-match is forgiven.
#
# The first line is taken with `${out%%...}` rather than a leading `head -1`,
# which under pipefail could also read a SIGPIPE'd producer as a failure.
version_of() {
    local out
    out="$("$1" --version 2>/dev/null)" || return 1
    printf '%s\n' "${out%%$'\n'*}" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true
}

install_one() {
    local tool="$1" version="$2" tmp file
    tmp="$TMP/$tool"; mkdir -p "$tmp"
    if installed "$tool" && [ "${FORCE:-0}" != 1 ]; then
        log "  $tool: already in .tools/bin"
        return
    fi
    # A system copy is fine and is what the maintainer's Rocky box has for
    # `just` (EPEL) and `tofu`. Only install what is genuinely missing.
    #
    # `have` is `command -v` and says nothing about version, and this arm covers
    # all six tools rather than the two the sentence above names -- so on any
    # box with a distro copy, the pins and digests at the top of this file are
    # advisory and the fetch machinery never runs. That is the intended
    # behaviour; being silent about it was not. Report the version beside the
    # path, and say so when it is not the pinned one. A warning, not a failure:
    # the early return is deliberate.
    if have "$tool" && [ "${FORCE:-0}" != 1 ]; then
        local found path
        path="$(command -v "$tool")"
        found="$(version_of "$path")" \
            || die "$tool: $path is on PATH but '$tool --version' failed"
        log "  $tool: using $path (${found:-version unknown})"
        if [ "$found" != "$version" ]; then
            log "  warning: $tool ${found:-of unknown version} is on PATH, but this repo pins $version"
        fi
        return
    fi
    log "  $tool $version: downloading"
    file="$(fetch "$tool" "$version" "$tmp")"
    case "$file" in
        *.tar.gz) tar -xzf "$file" -C "$tmp" ;;
        *.zip)    need unzip
                  unzip -q "$file" -d "$tmp" ;;
        *)        chmod +x "$file"
                  rm -rf "${TOOLS_BIN:?}/$tool"
                  mv "$file" "$TOOLS_BIN/$tool"
                  installed "$tool" || die "$tool: install did not produce a binary"
                  return ;;
    esac
    # uv's tarball nests under a directory; the rest unpack flat. -type f with a
    # name match covers both without hardcoding either layout.
    rm -rf "${TOOLS_BIN:?}/$tool"
    find "$tmp" -type f -name "$tool" -perm -u+x -exec mv {} "$TOOLS_BIN/$tool" \;
    installed "$tool" || die "$tool: archive did not contain an executable named $tool"
}

# Binaries land in .tools/bin so CI can cache one directory, but a later step --
# or a later workflow step, which is a fresh shell -- has to be able to find them.
# `lib.sh` prepending TOOLS_BIN only helps processes that source it, which `just`
# is not. So expose them somewhere already on PATH.
#
# Deliberately not $GITHUB_PATH: nothing under scripts/ may read a CI platform's
# variables, because that is what keeps .github/ deletable. A symlink works the
# same on a runner, in a container and on a developer box.
expose_on_path() {
    local dir=/usr/local/bin sudo="" tool
    [ -w "$dir" ] || sudo=sudo
    for tool in "$TOOLS_BIN"/*; do
        [ -x "$tool" ] || continue
        $sudo ln -sf "$tool" "$dir/$(basename "$tool")" 2>/dev/null || {
            log "  note: could not link $(basename "$tool") into $dir;"
            log "        add $TOOLS_BIN to PATH yourself"
            return 0
        }
    done
}

main() {
    mkdir -p "$TOOLS_BIN"
    TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
    local tofu_version
    tofu_version="$(containerfile_arg TOFU_VERSION)"
    log "installing tools into .tools/bin"
    install_one uv       "$UV_VERSION"
    install_one just     "$JUST_VERSION"
    install_one tofu     "$tofu_version"
    install_one hadolint "$HADOLINT_VERSION"
    install_one trivy    "$TRIVY_VERSION"
    install_one syft     "$SYFT_VERSION"
    expose_on_path
    log "done"
}

main "$@"
