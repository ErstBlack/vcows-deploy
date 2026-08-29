# Step 1 spike results — 2026-08-28

Ran against the Fedora 44 rig (libvirt daemon 12.0.0) from the Rocky 10.2 dev box
(libvirt client 11.10.0). Scripts in `docs/spikes/`.

| Spike | Result |
|---|---|
| A1 — seed ISO, `pycdlib` vs `xorrisofs` | **PASS both.** Decision data below. |
| A2 — marker round trip | **PASS.** Byte-identical, un-reindented. |
| A3 — golden image contents (stand-in) | **PASS.** growpart + cloud-init present. |
| A4 — `vol-upload` discards capacity | **CONFIRMED.** findings.md F5 is correct. |

---

## A4 — `vol-upload` silently discards declared capacity — CONFIRMED

The whole base-plus-overlay design rests on this, so it was verified rather than
inherited.

```
vol-create-as images captest.qcow2 20G --format qcow2
  Capacity:  20.00 GiB          Allocation:   196.00 KiB
vol-upload --pool images captest.qcow2 Rocky-9-GenericCloud-Base...qcow2
  Capacity:  10.00 GiB   <--    Allocation:   616.20 MiB
```

Capacity dropped 20 GiB → 10 GiB, the golden image's own virtual size. The upload
writes from offset 0 including the qcow2 header, and the declared capacity is
silently lost with no warning and a zero exit.

**Consequence, unchanged from findings.md F5:** the base volume is uploaded at
whatever size the golden image declares, and per-VM capacity is set on the
*overlay*, which is the only place it sticks. R-F stands.

## A2 — marker round trip — PASS

Defined `vcows-spike-probe01` carrying a 97-byte payload in a namespaced
`<metadata>` element and read it back three ways.

- `dom.metadata(VIR_DOMAIN_METADATA_ELEMENT, ns, 0)` → byte-identical
- `XMLDesc(VIR_DOMAIN_XML_INACTIVE)` + ElementTree → byte-identical
- payload appears verbatim as a substring of the dumped XML

No re-indentation, no entity escaping, no whitespace injection. Emitted as:

```xml
    <vcows xmlns="https://example.invalid/vcows">{"v":"0.1.0.0","deployment":"spike","name":"probe01","id":"c6e9ec4a-..."}</vcows>
```

**Detail that matters for the parser:** the two read paths disagree on namespace.
`dom.metadata()` returns the element with the **xmlns stripped** (`<vcows>…</vcows>`,
tag `vcows`), while `XMLDesc` returns it namespaced (tag `{https://…/vcows}vcows`).
Preflight uses the `XMLDesc` path regardless — it is one round trip per domain and
yields `devices/disk/source/@file` from the same document.

**Not yet verified:** persistence across a `virtqemud` restart. `vcows` cannot
restart the service (interactive auth required), and four running guests keep the
socket-activated daemon alive so the idle-timeout route is unavailable. One
command closes it, and running guests survive a libvirt daemon restart:

```bash
sudo systemctl restart virtqemud
```

Then re-read; `vcows-spike-probe01` is left defined for exactly this.

## A3 — golden image contents — PASS (stand-in, per D3)

Read-only inspection of `Rocky-9-GenericCloud-Base.latest.x86_64.qcow2` via
`guestfish --ro`; never booted.

| Check | Result |
|---|---|
| `/usr/bin/cloud-init` | present |
| `/usr/bin/growpart` | present |
| `/usr/sbin/sgdisk` | present — growpart's GPT path, and this disk is GPT |
| `/usr/sbin/sfdisk` | present — growpart's MBR path |
| `/usr/sbin/xfs_growfs` | present — root is xfs |
| `/usr/bin/qemu-ga` | present |
| `growpart` in `cloud_init_modules` | **yes**, immediately before `resizefs` |
| `datasource_list` | **unset** — auto-detect, which is correct for NoCloud |

Layout is GPT: `sda1` unknown (BIOS boot), `sda2` vfat (**ESP — the image is
UEFI**), `sda3` xfs (/boot), `sda4` xfs (root). Root is the last partition, so
growpart can extend it.

**F5's dependency holds** for this image. Per D3 this remains **UNVERIFIED against
the real golden artifact**; re-run `docs/spikes/a3_golden_image.sh` against it.

## A1 — seed ISO: `pycdlib` vs `xorrisofs` — PASS both

Same `user-data` + `meta-data` built both ways, then each ISO read back with the
*other* toolchain.

| | `xorrisofs` | `pycdlib` 1.20.0 |
|---|---|---|
| ISO size | 378,880 B | 67,584 B |
| Volume label `cidata` | yes | yes |
| Both files via Joliet | byte-exact | byte-exact |
| Both files via Rock Ridge | byte-exact | byte-exact |
| Ships as | RPM, 349 KB + `libisoburn` | **one 228 KB wheel, zero deps, pure Python** |
| License | **GPL-2.0-or-later** | **LGPL-2.1-only** |
| Failure mode | exit code + stderr parsing | Python exception |
| API cost | one command line | three names per file (`/USER_DATA.;1` + `rr_name` + `joliet_path`) |

Both outputs are correct and mutually readable. The split is on vendoring and
licence, not on correctness.

### `isoinfo` is not available on Rocky 10.2

findings.md R4 says to "add `isoinfo` assertions on the seed ISO". `isoinfo` ships
with genisoimage/cdrkit, which findings.md itself establishes is absent from Rocky
10 repos. Verification uses `xorriso -indev … -find`, a direct primary-volume-
descriptor read for the label, and `pycdlib`'s reader — which cross-checks each
builder against the other rather than trusting one tool's self-report.

---

## Out-of-scope finding: the container may not need `qemu-img`

Not a spike deliverable; it surfaced while measuring A1's BOM.

`qemu-img` is **14.2 MB** and **GPL-2.0-only** — the most constrained licence in
the bundle, with no upgrade path (F15's point). Its only use under the settled
design is R-F's "`disk_gb` ≥ golden image virtual size" validation, because every
volume operation — create, upload, overlay via `backing_store` — happens on the
*hypervisor* through libvirt, not in the container.

That validation does not need it. The qcow2 virtual size is a big-endian `u64` at
byte offset 24 of the header:

```python
def qcow2_virtual_size(path):
    with open(path, "rb") as f:
        hdr = f.read(32)
    if hdr[:4] != b"QFI\xfb":
        raise ValueError("not a qcow2")
    return struct.unpack(">Q", hdr[24:32])[0]
```

Verified against `qemu-img info --output=json` on a 20 GiB image: both return
`21474836480`.

**Caveat on how much this buys.** Dropping `qemu-img` and `xorriso` removes the
two GPL binaries *we add*, but the Rocky 10 base image still ships GPL userspace
(bash, coreutils, and so on), so the D8 source sidecar cannot go to zero. Its size
is dominated by the base image's package set, not by our additions — which is the
useful reframing for D5 when it is measured.
