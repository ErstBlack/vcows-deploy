# Future backends — surviving research

findings.md §5 cut vSphere and Proxmox from v0.1 and directed that the research
move here rather than be lost. This file is **not a plan and not a commitment**.
Nothing in it is built.

> Migration status: only the image-conversion chain below has been moved and
> re-verified. The rest of the vSphere/Proxmox material still sits in
> `orchestrator-architecture.md` §6.1, §6.3, §6.4, §7 and has **not** been
> re-checked against the errata appendix.

---

## qcow2 → VMDK → OVA, without qemu-img

Investigated 2026-08-28, prompted by dropping `qemu-img` from the v0.1 container.

The chain is three steps, and `qemu-img` is only load-bearing in the first:

```
qcow2  --decode-->  raw  --vmdk-convert-->  streamOptimized VMDK  --ova-compose-->  OVA
```

### Step 2 and 3: open-vmdk, and it is now packaged

`open-vmdk` **0.3.12 is in EPEL 10**, `Apache-2.0`, shipping:

| binary | role |
|---|---|
| `/usr/bin/vmdk-convert` | raw → streamOptimized VMDK |
| `/usr/bin/mkova.sh` | VMDK → OVA |
| `/usr/bin/ova-compose` | OVA assembly from a spec |

`orchestrator-architecture.md` §6.1 treats open-vmdk as a build-from-source
dependency. It is a single `dnf install` on Rocky 10, and D6 already permits EPEL
in the build mirror. **Correction to the architecture doc**, noted here rather than
in the errata appendix because it only affects a cut path.

open-vmdk is not optional. §6.1 already records that qemu's own
`subformat=streamOptimized` output is sometimes rejected by ESXi with
*"Unsupported or invalid disk type 7"*, with `vmdk-convert` as the fallback. Since
the fallback is needed anyway, the sensible design makes it the primary path — at
which point qemu's VMDK writer is never used, and only its qcow2 *reader* matters.

### Step 1: decoding qcow2 without qemu

| Option | Licence | Air-gap viability |
|---|---|---|
| `qemu-img convert -O raw` | GPL-2.0-only | 14.2 MB RPM; the licence F15 finds awkward |
| `libqcow-python` (libyal) | LGPL-3.0-or-later | **manylinux cp312 wheels published** — vendorable, no gcc |
| `nbdkit` / `libnbd` | LGPL | Not an escape: qcow2 decode still comes from qemu's block layer, and `qemu-nbd` ships in the **same RPM** as `qemu-img` |
| Hand-rolled | — | Uncompressed qcow2 is tractable; compressed clusters (zlib/zstd) are real work and real risk. Not recommended |

`libqcow-python` publishing binary wheels matters because it sidesteps R7's trap
exactly — PyPI sdist-only packages need gcc plus network access for build
isolation, which is what makes `python3-libvirt` an RPM. libqcow is not in that
category.

**So a future vSphere path could be GPL-free end to end**: LGPL for the decoder,
Apache-2.0 for the VMDK and OVA tooling. Worth knowing, but not worth optimising
for — the Rocky 10 base image ships GPL userspace (bash, coreutils) regardless, so
this does not eliminate the D8 source obligation.

### Practical caveat

The raw intermediate is full-size: a 10 GiB qcow2 decodes to a 10 GiB raw file.
Sparse writes help on disk but not in a container with a small writable layer.
Whatever does the decode should stream into `vmdk-convert` rather than landing a
raw file, and `prepare()` being a context manager (§3) is what gives that step a
place to own a scratch path and clean it up.

### Why none of this gates v0.1

`qemu-img` is out of the v0.1 image because nothing in the settled design uses it:
every volume operation — create, upload, overlay via `backing_store` — happens on
the hypervisor through libvirt, and R-F's size validation is a 10-line qcow2
header read (verified against `qemu-img info`; see `spikes.md`).

Adding a package back to a Containerfile is a one-line, fully reversible change.
It is the opposite of a one-way door, and `prepare()` is per-backend precisely so
a future backend can bring its own tools without touching core.

---

## Re-measured 2026-09-02, after the second backend

Four facts above or in `orchestrator-architecture.md` moved. Recorded here so
the next reader does not re-derive them; the decisions they feed live in #196
(vSphere effort and decisions) and #198 (OpenTofu's role), not in this file.

| Claim | Now |
|---|---|
| pyVmomi needs pinning as a wheel, or `vcf-sdk` replaces it | `python3-pyvmomi` 9.0.0.0 is in EPEL 10 (`dnf repoquery`, 2026-09-02). The SDK path is an RPM like `python3-libvirt`, not a wheel like `proxmoxer`. `vcf-sdk` (12 dependencies, no RPM) is not needed for preflight or destroy. |
| Provider status | `vsphere` v2.17.0, released 2026-09-01, MPL-2.0, active. `ovf_deploy.local_ovf_path`, `vsphere_file`, `cdrom` from a datastore path, `clone` with `linked_clone`, and `annotation` cover what the marker, the seed ISO and a shared-image design need. |
| The conversion chain is the hard part | Only if vcows converts at the site. Delivery shipping an OVA beside the qcow2, built where `qemu-img` is free to use, removes every step above from the image. That is #196's D1, and the recommended option. |
| One connection to the target | Not established for vSphere. `ovf_deploy` streams disk bytes to the ESXi host's HTTP lease URL rather than to vCenter, so the container may need a route to every host. Rig-only fact; nothing in this repo has spoken to a vCenter. |

One more rig-only fact: `cloudinit.derive_mac` emits `52:54:00` and is
documented as permanent, and vCenter has historically restricted manually
assigned MACs to `00:50:56:00` through `00:50:56:3F`. A rejection means a
per-backend MAC policy in core. Neither this nor the lease question can be
settled from documentation.
