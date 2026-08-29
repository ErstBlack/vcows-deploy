# `dmacvicar/libvirt` — licence provenance

The OpenTofu provider this image ships is redistributed inside it, so the licence
travels with it. That is not a formality here: **0.9.x ships no `LICENSE` file**,
and the obvious conclusion — that the grant lapsed — is wrong. What follows is the
evidence, recorded because the next person to check will find the same 404.

## The artifact

| | |
|---|---|
| Provider | `registry.opentofu.org/dmacvicar/libvirt` |
| Version | `0.9.8`, pinned exactly (`version = "= 0.9.8"`) |
| Artifact | `terraform-provider-libvirt_0.9.8_linux_amd64.zip` |
| SHA256 | `061e5187853729e1d8ba20938402ad6e778b4097436925d0bef7741c8aa26ee1` |
| Lock hash | `h1:yqZeKoJ+EZc3687/+ZBqBmtwzvBPLNwaEHW74+bSc6Y=` (`docs/provider-0.9.8.lock.hcl`) |
| Upstream | https://github.com/dmacvicar/terraform-provider-libvirt |

## Why there is no LICENSE file upstream

* `LICENSE` is a 404 at both `main` and the `v0.9.8` tag, and the GitHub API
  reports `license: null`.
* `main` has **no common ancestor** with `v0.8.3`. Nothing was deleted: the Apache
  grant that covered ≤ 0.8.3 never carried into the rewrite by lineage, so its
  absence is a gap in the new history rather than a revocation in the old one.
* Tracked upstream as issue #1371.

## The grant that does ship

Verbatim from `README.md` inside the artifact above:

```
## License

* Apache 2.0
```

Apache-2.0 §4(a) puts the obligation to supply the licence text on the
redistributor regardless of whether upstream ships a copy, which is why `LICENSE`
in this directory is the canonical text rather than a copy of anything upstream
publishes.

| | |
|---|---|
| Source | https://www.apache.org/licenses/LICENSE-2.0.txt |
| SHA256 | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

## Integrity

**The registry serves no signature for this provider.** `tofu providers mirror`
reports "signing skipped", so there is no GPG chain to verify against — the `h1:`
hash in the committed lock file is the only integrity anchor, and it is the reason
the lock is committed and copied into the image rather than regenerated at a site.
