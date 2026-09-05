#!/usr/bin/env bash
# The offline container gate, demanded rather than skipped.
#
# Exists because the recipe needs to tell pytest *which* image to exercise, and
# the tag is computed from the Containerfile. Left to the caller,
# `just image && just test-image` fails the gate with setup errors.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_venv
have podman || die "podman not on PATH -- this gate needs a runtime, not a builder"
VCOWS_IMAGE="$(image_tag)"
export VCOWS_IMAGE
export VCOWS_GATES=image
log "exercising $VCOWS_IMAGE"
exec "$PY" -m pytest -q -rs "$@"
