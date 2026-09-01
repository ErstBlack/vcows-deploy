# `bpg/proxmox` — licence provenance

The OpenTofu provider the Proxmox backend uses is redistributed inside the image,
so its licence travels with it. Unlike `dmacvicar/libvirt` there is nothing
surprising here: upstream ships a `LICENSE` file and the grant is plain.

## The artifact

| | |
|---|---|
| Provider | `registry.opentofu.org/bpg/proxmox` |
| Version | `0.111.1`, pinned exactly (`version = "= 0.111.1"`) |
| Artifact | `terraform-provider-proxmox_0.111.1_linux_amd64.zip` |
| SHA256 | `6ed47bc00d0913a1d0880618fa1376115e9edab6b4a658c081061a7f0e4ca360` |
| Lock hash | `h1:ML2D3UUZTM99yrll/EBXj7wBYMb8xmQgomqFNybEoxY=` (`docs/provider-0.111.1.lock.hcl`) |
| Licence | MPL-2.0, `LICENSE` beside this file |
| Upstream | https://github.com/bpg/terraform-provider-proxmox |

## Two things worth recording

**It is signed, and libvirt's is not.** `tofu providers mirror` reported
`Package authenticated: signed` for this provider and
`Package authenticated: signing skipped` for `dmacvicar/libvirt` — measured
2026-09-01 against tofu 1.12.6. So the lock hash is a second anchor here rather
than the only one.

**It is pre-1.0 and says so.** Upstream states it does not guarantee backward
compatibility across minor versions. That is why the module pins with `=` rather
than a range, and why a bump is a deliberate edit with the release notes read
rather than something a bot does.
