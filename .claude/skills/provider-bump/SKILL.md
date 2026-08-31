---
name: provider-bump
description: "Use when bumping terraform-provider-libvirt past 0.9.8, when `just verify-provider` fails, or when a provider version, SHA256 or h1: hash disagrees across files. Covers the four places, the schema diff, the mirror rebuild and the CI cache-key trap."
---

# Bumping the libvirt provider

`docs/ci.md` calls this "a step in a runbook". This is that runbook.

Nothing about it is automatic. `dependabot.yml` is deliberately not pointed at
the `Containerfile`: a bot can change a version string but cannot recompute a
downloaded artifact's SHA256 or a lock file's `h1:` hash, and a bump that moved
one without the other would surface much later as a checksum error that reads
like corruption.

## The four places

The version and its hashes are stated in four places that nothing compares at
build time. `scripts/verify-provider.sh` is the gate for that -- it runs in under
a second and needs no network.

1. `orchestrator/backends/libvirt/tofu/main.tf` -- `version = "= X"`
2. `Containerfile` -- `ARG PROVIDER_VERSION` / `ARG PROVIDER_SHA256`
3. `docs/provider-X.lock.hcl` -- the version, and the `h1:` hash
4. `.tools/tofu-mirror/...` -- the zip, its real sha256, and the index's
   `h1:`/`zh:` pair

`just verify-provider` is what catches a half-finished bump. Run it after every
edit, not once at the end.

Note that place 3 is a **versioned filename**. Bumping renames it, and that has a
consequence in CI -- see the cache trap below.

## The cache-key trap

`.github/workflows/ci.yml` keys the provider mirror cache on
`orchestrator/backends/libvirt/tofu/main.tf`, **not** on
`docs/provider-<v>.lock.hcl`:

```yaml
key: tofu-mirror-${{ hashFiles('orchestrator/backends/libvirt/tofu/main.tf') }}
```

A bump renames the lock file, so `hashFiles` would find no match, return `''`,
and the key would collapse to a constant -- producing a stale-mirror cache hit on
exactly the run where the mirror changed. There are no `restore-keys` for the
same reason: a prefix fallback is how an old mirror gets restored into a new
pipeline.

If you move the pin somewhere `main.tf` does not declare it, fix this key in the
same commit.

## Procedure

1. **Schema diff first.** Compare the new provider's schema against
   `docs/provider-schema-0.9.8.json`. A removed or renamed attribute is a code
   change in `orchestrator/backends/libvirt/`, not a version bump. Find that
   before touching any hash.
2. Update `main.tf`, then the `Containerfile` ARGs, then rename and regenerate
   the lock file. Compute the real SHA256 and `h1:` -- do not copy them from a
   release page.
3. Rebuild the mirror: `just mirror`, then `just verify-mirror`.
4. `just verify-provider` -- must pass before anything else.
5. `just image && just scan`. **Expect the CVE set to move.** The provider is a
   Go binary and most accepted findings live in it, so the baseline's
   `provider/golang.org/x/crypto` and `provider/stdlib` groups need re-triage.
   Use the `cve-triage` skill. Do not run `--write-baseline`.
6. `just check`, then `just test-tofu`.
7. Update `docs/provider-schema-<new>.json` if the schema moved.

## Why 0.9.8 is still pinned

The x/crypto fix that would clear nine HIGH findings exists only on unreleased
main -- 0.9.8 is the latest tag. There is no version to bump to that improves the
scan. The baseline accepts those findings on reachability instead
(`render.py:61` uses `qemu+sshcmd`, whose dialer execs OpenSSH and never enters
`x/crypto/ssh`). Check whether that is still true before assuming a bump is
worth doing: the monthly rebuild is when to ask.
