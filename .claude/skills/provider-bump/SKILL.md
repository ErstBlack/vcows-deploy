---
name: provider-bump
description: "Use when bumping dmacvicar/libvirt past 0.9.8 or bpg/proxmox past 0.111.1, when `just verify-provider` fails, or when a provider version, SHA256 or h1: hash disagrees across files. Covers the four places each provider is pinned, the shared mirror that is built one provider at a time, the schema diff and the CI cache-key trap."
---

# Bumping a provider

`docs/ci.md` calls this "a step in a runbook". This is that runbook, and there
are two providers it applies to: `dmacvicar/libvirt` for the libvirt backend and
`bpg/proxmox` for the Proxmox one. Each is bumped on its own. Nothing here bumps
both at once, and nothing needs to.

Nothing about it is automatic. `dependabot.yml` is deliberately not pointed at
the `Containerfile`: a bot can change a version string but cannot recompute a
downloaded artifact's SHA256 or a lock file's `h1:` hash, and a bump that moved
one without the other would surface much later as a checksum error that reads
like corruption.

## The four places, per provider

Each provider's version and hashes are stated in four places that nothing
compares at build time. `scripts/verify-provider.sh` is the gate for that -- it
runs in under a second, needs no network, and walks both providers.

| | libvirt | proxmox |
|---|---|---|
| 1. module | `orchestrator/backends/libvirt/tofu/main.tf` `version = "= X"` | `orchestrator/backends/proxmox/tofu/main.tf` `version = "= X"` |
| 2. Containerfile | `ARG PROVIDER_VERSION` / `ARG PROVIDER_SHA256` | `ARG PVE_PROVIDER_VERSION` / `ARG PVE_PROVIDER_SHA256` |
| 3. committed lock | `docs/provider-0.9.8.lock.hcl` | `docs/provider-0.111.1.lock.hcl` |
| 4. mirror | `.tools/tofu-mirror/registry.opentofu.org/dmacvicar/libvirt/` | `.tools/tofu-mirror/registry.opentofu.org/bpg/proxmox/` |

Place 1 is the source of truth: `scripts/lib.sh`'s `provider_version` reads it
from a module directory, and `backend_modules` enumerates every
`orchestrator/backends/*/tofu` so the gate finds a third backend without being
told. Place 3 is the version, and the `h1:` hash. Place 4 is the zip, its real
sha256, and the index's `h1:`/`zh:` pair.

`just verify-provider` is what catches a half-finished bump. Run it after every
edit, not once at the end.

**Place 3 is a versioned filename that carries the version and not the
provider.** Bumping renames it, which is the cache trap below. Two providers that
ever pin the same version string would also collide on the same file; 0.9.8 and
0.111.1 do not, and renaming the scheme is #154, not this runbook.

**The same gate covers the one pip-installed dependency.** `verify-provider.sh`
also checks `ARG PROXMOXER_VERSION` / `ARG PROXMOXER_SHA256` against
`licenses/proxmoxer/PROVENANCE.md` and the wheel URL in the `Containerfile`.
Not a provider, but the same failure -- a version bumped in one place and not
the other -- so a failing check there is not a provider problem.

## The cache-key trap

Both pipelines key the provider mirror cache on the modules' `main.tf`, **not**
on `docs/provider-<v>.lock.hcl`. `.github/workflows/ci.yml`:

```yaml
key: tofu-mirror-${{ hashFiles('orchestrator/backends/*/tofu/main.tf') }}
```

`.gitlab-ci.yml`'s `.mirror_cache` anchor lists both `main.tf` paths explicitly,
because GitLab's `cache:key:files` takes at most two paths. A third backend
needs a decision there rather than a third entry.

A bump renames the lock file, so a key on it would find no match, return `''`,
and collapse to a constant -- producing a stale-mirror cache hit on exactly the
run where the mirror changed. There are no `restore-keys` for the same reason: a
prefix fallback is how an old mirror gets restored into a new pipeline. One
mirror holds every provider, so a bump in either module invalidates it.

If you move a pin somewhere its `main.tf` does not declare it, fix both keys in
the same commit.

## The mirror is built one provider at a time

`scripts/mirror.sh`'s `mirror_all` runs `tofu providers mirror` for each module
into its own temporary directory and copies the result into the shared one.
Measured 2026-09-01 against tofu 1.12.6: pointing `providers mirror` at a
directory that already holds another provider rewrites *that* provider's index
too, and drops its `zh:` hash -- the one cross-check that the artifact in the
mirror is the artifact `PROVIDER_SHA256` asserts. `just mirror` does this
correctly. Running `tofu providers mirror` by hand into `.tools/tofu-mirror`
does not.

## Procedure

1. **Schema diff first.** A removed or renamed attribute is a code change in
   the backend package, not a version bump. Find that before touching any hash.
   For libvirt, compare the new schema against `docs/provider-schema-0.9.8.json`.
   For proxmox there is no committed snapshot: run
   `tofu providers schema -json` against the new version and diff it by hand
   against the attributes `orchestrator/backends/proxmox/render.py` and
   `schema.py` rely on (the `ca_file` note in `schema.py` is what that check
   looks like). bpg/proxmox is pre-1.0 and says it does not guarantee backward
   compatibility across minor versions, so read the release notes.
2. Update that provider's `main.tf`, then its `Containerfile` ARG pair, then
   rename and regenerate its lock file. Compute the real SHA256 and `h1:` -- do
   not copy them from a release page.
3. Rebuild the mirror: `just mirror`, then `just verify-mirror`.
4. `just verify-provider` -- must pass before anything else.
5. `just image && just scan`. **Expect the CVE set to move.** Each provider is a
   Go binary and most accepted findings live in them. The baseline's
   `provider/golang.org/x/crypto` and `provider/stdlib` groups are the libvirt
   binary's; for proxmox, #148 is where the 0.111.1 findings stand. Use the
   `cve-triage` skill. Do not run `--write-baseline`.
6. `just check`, then `just test-tofu`.
7. For libvirt, update `docs/provider-schema-<new>.json` if the schema moved.
   A proxmox snapshot is not added here; that belongs with #154's scheme.

## Why 0.9.8 is still pinned

The x/crypto fix that would clear nine HIGH findings exists only on unreleased
main -- 0.9.8 is the latest tag. There is no version to bump to that improves the
scan. The baseline accepts those findings on reachability instead
(`render.py`'s `connection_uri(target, "sshcmd")` builds a URI whose dialer
execs OpenSSH and never enters `x/crypto/ssh`). Check whether that is still true
before assuming a bump is worth doing: the monthly rebuild is when to ask.

Two facts about this provider do not carry over to the other. 0.9.x ships no
upstream `LICENSE` file, and `tofu providers mirror` reports its package as
`signing skipped`. `licenses/dmacvicar-libvirt/PROVENANCE.md` has the evidence.

## Why 0.111.1 is pinned exactly

bpg/proxmox is pre-1.0 and states that minor versions may break compatibility,
which is why the module pins with `=` rather than a range. Its artifact is
`signed` and it ships a `LICENSE` (MPL-2.0), so the lock's `h1:` is a second
anchor there rather than the only one. `licenses/bpg-proxmox/PROVENANCE.md`
records both.
