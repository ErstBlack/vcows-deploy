---
name: delivery
description: Use when assembling or shipping a vcows delivery bundle, when `just bundle` fails, or when asked what a site receives. Covers the required order of image/scan/bundle and the GPL source medium that no script produces.
---

# Assembling a delivery

There is no `just deliver`. The sequence is three recipes in one order, plus one
step no script performs.

```
just image     # build
just scan      # trivy against the baseline, plus an SBOM
just bundle    # -> .cache/delivery/
```

## The order is enforced, not conventional

`just bundle` assembles what ships **from what `just scan` already wrote**. It
does not build anything itself. `scripts/bundle.sh:52` checks for the archive,
the SBOM and the trivy report and dies with:

    no <name> -- run 'just scan' first, which writes it

So a bundle is always a bundle *of a scanned image*. You cannot ship something
that was not scanned against `docs/cve-baseline.json`, which is the point.

If the scan goes red, stop and use the `cve-triage` skill. Do not bundle around
it.

## What the bundle holds

The compressed image, the SBOM, and the trivy report describing *that* image,
plus a `SHA256SUMS` covering all three and the digest of the uncompressed
archive inside the gzip -- so a site can verify before or after decompressing.

On receipt:

```
sha256sum -c SHA256SUMS
gunzip -c vcows-deploy-*.tar.gz | podman load
```

Compression is `gzip -9 -n`. `-n` drops the stored filename and mtime so the
same archive always compresses to the same bytes. **Do not swap in pigz.** It
was measured and rejected: over 12 runs on identical input it produced two
outputs one byte apart, both decompressing to identical content. An artifact
whose identity is its digest cannot be produced by something that changes the
digest without changing the content. gzip costs 82s against 5.5s on a 444 MB
archive, once per delivery.

## Nothing here is signed

`SHA256SUMS` gives integrity, not authenticity -- it catches corruption and a
mismatched pairing, not substitution. Do not describe the bundle to anyone as
signed.

There was a `just sign` on cosign 3 and it worked, verified air-gapped under
`unshare -rn`. It was removed in `950ca7e` because it signed
`.cache/scan/image.tar` while the README promised a gzip tarball: two byte
streams both called "the delivery tarball", which at a site reads as tampering
rather than as a packaging bug. `docs/ci.md` section "Why signing was removed"
is the current rationale and records what reinstating it needs.

(Older notes, including survey section 5.2, describe the order as "sign refuses
without the archive scan writes". That is stale. The dependency is `bundle` on
`scan`.)

## The step a human forgets

**The GPL source medium.** The image contains GPL-2.0-**only** components, and
GPLv2 §3 offers no network-server option for source. Source therefore ships as a
**separate medium accompanying the delivery**, mirrored by `reposync` against the
`source_rpms` list in `/opt/vcows/manifest.json`.

No recipe produces this. It is not in `just bundle` and nothing fails if it is
missing. It is the licence obligation, so a delivery without it is incomplete
even though every gate is green.

The provider's licence and provenance are already vendored in the image at
`/opt/vcows/licenses/dmacvicar-libvirt/`; upstream ships no `LICENSE` in 0.9.x
and the note there explains why that is a gap rather than a revocation.

## Checklist

1. `just image`
2. `just scan` -- green, or triage via `cve-triage`
3. `just bundle`
4. `reposync` the `source_rpms` list from the manifest onto the source medium
5. Ship both media together
