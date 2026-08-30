#!/usr/bin/env bash
# Signs the delivery tarball, and verifies it the way a site would.
#
#   scripts/sign.sh            sign .cache/scan/image.tar
#   scripts/sign.sh --verify   verify an existing signature
#
# **Keyless signing does not work at an air-gapped site, and the defaults hide
# that.** `cosign sign-blob` writes to the public Rekor transparency log unless
# told not to, and `verify-blob` fetches TUF trust-root metadata from
# tuf-repo-cdn.sigstore.dev. Both were confirmed to fail under `unshare -rn`.
# With --tlog-upload=false at sign time and --insecure-ignore-tlog at verify,
# both succeed with nothing but a local public key -- which is the shape this
# delivery actually has: a tarball handed over, verified somewhere with no
# network.
#
# The private key stays on the build host and is gitignored. The public key ships
# beside the tarball.

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

KEY="$REPO/.cache/cosign.key"
PUB="$REPO/.cache/cosign.pub"
# A signing config naming no transparency log. cosign 3 refuses
# --tlog-upload=false against its default config and tells you to build one
# of these; `signing-config create` does it with no network, which is the
# only reason this is viable here at all.
CONF="$REPO/.cache/signing-config.json"

main() {
    have cosign || die "cosign not on PATH -- run scripts/install-tools.sh"
    local archive="$REPO/.cache/scan/image.tar"
    local sig="${archive}.bundle"
    [ -f "$archive" ] || die "no $archive -- run 'just scan' first, which writes it"

    if [ "${1:-}" = "--verify" ]; then
        [ -f "$sig" ] || die "no signature at $sig -- run 'just sign' first"
        cosign verify-blob --key "$PUB" --bundle "$sig" \
            --insecure-ignore-tlog "$archive"
        log "verified $(basename "$archive") against $(basename "$PUB")"
        return
    fi

    if [ ! -f "$KEY" ]; then
        log "generating a key pair in .cache/ (gitignored; back it up yourself)"
        # An empty password: this is a build-host key protected by the host, not
        # by a passphrase nobody will remember. Say so rather than pretend.
        ( cd "$REPO/.cache" && COSIGN_PASSWORD="" cosign generate-key-pair >/dev/null )
    fi
    if [ ! -f "$CONF" ]; then
        # No rekor entry, so nothing tries to reach a transparency log.
        cosign signing-config create \
            --fulcio="url=https://unused.invalid,api-version=1,start-time=2024-01-01T00:00:00Z,operator=local" \
            --out "$CONF" >/dev/null
    fi
    # cosign 3 replaced --output-signature with a bundle: a detached signature is
    # no longer a complete artifact. An older write-up will show the 2.x flag.
    COSIGN_PASSWORD="" cosign sign-blob --key "$KEY" --signing-config "$CONF" \
        --yes --bundle "$sig" "$archive" >/dev/null
    log "signed  $sig"
    log "ship    $PUB beside the tarball; verify with 'just verify-signature'"
}

main "$@"
