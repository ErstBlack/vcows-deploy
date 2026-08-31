# What a RHEL 9 host would settle

Everything vcows has ever run against is Fedora 44 with libvirt 12.0.0 — the
acceptance run on 2026-08-29 and the rig session the same evening. Fedora being
*newer* than any shipping target means it cannot surface failures that run in
that direction, and the rig session ended with exactly that residue: a short list
of questions no privilege level on this hardware can answer, because the answer
depends on the host being older.

This is that list, written while the reasoning was fresh. It is a work order, not
a design document.

## Read this first: "a RHEL 9 host" is three different machines

The checks below do **not** all want the same vintage, and getting a current
Rocky 9 box does not cover the list. Match the host to the check or the run
proves less than it looks like it did.

| vintage | libvirt | what only it can answer |
|---|---|---|
| **current 9.x** (9.8) | 11.10.0 | the raw `.fd` varstore, monolithic vs split daemons, a non-Fedora acceptance run. **Not** the flag shed: 11.10.0 accepts every bit vcows passes, so the gate never fires. |
| **9.0 / 9.1 EUS** | 8.0.0 / 8.5.0 | the `undefine_mask` flag shed, and the firmware pin against genuinely old libvirt. This is the only vintage where `destroy.py`'s version gate has ever had anything to do. |
| **9.0 – 9.3** | — | cloud-init 22.1 / 23.1 and the `sysconfig` renderer path. |

The container's own client is libvirt **11.10.0**. Against a 9.0 EUS daemon that
is a three-major gap in the client-newer-than-daemon direction, which nothing has
exercised — the rig's gap was 11.10.0 against 12.0.0, one minor the other way.

## Before you start

**The rig gate is not portable, and it will fail on fixtures rather than on code.**
`tests/test_libvirt_rig.py` asserts against *this* rig: two probe domains by name
(`vcows-probe02`, `vcows-spike-probe01`), a pool called `images` containing a
`_cloud-images` directory entry and a specific base image, a live lease on
`192.168.122.82`, and DHCP reservations on `.101`–`.105`. Pointing
`VCOWS_RIG_URI` at a new host without staging those will produce failures that
say nothing about the code. Either stage the fixtures or run the checks below by
hand and leave the gate on its own rig.

What to bring: the shipped container image, a config, a golden qcow2 matching the
vintage under test, and read access to `/var/lib/libvirt/qemu/nvram/` — which
means root, or a group that can read it. The unprivileged deploy account cannot,
and that is what made the varstore question unanswerable for as long as it was.

## The checks

### C1 — `<os firmware='efi'>` beside a pinned loader (review 2.15)

**Needs 9.0/9.1 EUS.** `main.tf`'s `os` block emits `firmware = "efi"` *and* a
pinned `loader`/`loader_format`/`loader_readonly` together. On libvirt 12.0.0 the pin is
honoured exactly — measured 2026-08-29: `app02` came back carrying its configured
`OVMF_CODE_4M.qcow2` and named template with `secure-boot` and `enrolled-keys`
both `no`, while the autoselected `app01` got the secboot build with both `yes`.
The construct is therefore not wrong in principle.

**Answered 2026-08-31, and the answer is worse than the question.** Autoselection
neither overrides nor defers: beside `firmware = "efi"`, libvirt validates the pin
against the host's own firmware descriptors and refuses a format they do not
carry. Measured on Ubuntu, whose four descriptors all declare `raw`: a qcow2 pin
is refused at define with *Unable to find 'efi' firmware that is compatible with
the current configuration*. So the deciding fact about a target is not its libvirt
version but its descriptor set, which `virsh domcapabilities` answers before a
deploy. Filed as #107; `scripts/smoke-libvirt.sh` carries the run ids.

`virsh define` then `virsh dumpxml` is enough — no boot, no KVM, so this can run
on a box that is only a libvirt install. Diff the stored `<os>` block against the
tfvars. If the pin is ignored, the module must stop emitting `firmware = "efi"`
whenever a loader is set, and 2.15 drops to a schema fix.

### C2 — the raw `.fd` varstore

**Needs any current 9.x.** Rocky 9 and Rocky 10 `edk2-ovmf` ship raw `.fd`
templates; this rig has only qcow2, so the `.fd` half of `main.tf`'s `nv_ram`
suffix expression — `_VARS.${loader_format == "qcow2" ? "qcow2" : "fd"}` — was
rendered against no real template until 2026-08-31.

**Partly closed.** `scripts/smoke-libvirt.sh` now pins a raw `.fd` loader and
template on every CI run, asserts the rendered `<nvram template='…_VARS.fd'>`
against a real libvirtd, and `assert_gone` asserts the varstore file is gone after
`tofu destroy` (#111, 2026-08-31). So both halves of the check below run in CI, and
the subject of the destroy half is the provider's teardown rather than the script's
own `undefine --nvram`, which happens later in `cleanup`. One gap remains: it is
Ubuntu with libvirt 10.0.0 rather than RHEL 9 EUS. Closing this needs the same VM on
a 9.x target.

The qcow2 path is settled: watched at two-second resolution on 2026-08-29, both
varstores appeared at define and were gone by the next sample after destroy. That
was never evidence about `.fd`, because the extension, the template format, and
libvirt's own handling all differ — which is why the destroy half was worth
asserting separately rather than assuming. It passed first time, on CI run
33430036395: the provider's destroy removes a raw `.fd` varstore on libvirt
10.0.0. What is still unmeasured is a 9.x daemon, not the format.

### C3 — the flag shed nobody has ever seen fire

**Needs 9.0/9.1 EUS.** `undefine_mask` gates on the *daemon's* version against
`_GATED = ((5006000, CHECKPOINTS), (8009000, TPM))`. On 8.0.0 and 8.5.0 the mask
sheds exactly one bit — `UNDEFINE_TPM` — and keeps checkpoints, since both are
above 5.6.0. No run in this project's history has shed anything: every target has
been ≥ 8.9.0. Confirm the undefine succeeds with the reduced mask, and confirm
what it leaves behind, which is the second half of the varstore question.

### C4 — a teardown where the TPM bit is genuinely unused

**Needs any 9.x.** Every domain on this rig carries a `<tpm>`, so the TPM bit is
exercised there by accident. vcows' own module emits no TPM device, so a
vcows-created domain on an old daemon is the case that has never been isolated:
the bit is dropped *and* there was nothing for it to do.

### C5 — cloud-init 22.1 / 23.1 and the `sysconfig` renderer

**Needs a 9.0–9.3 image, which is a download rather than a hypervisor.** Schedule
this first of everything here. It is the same shape as the acceptance run's
defect 5, which was the worst of the five that run found: cloud-init accepted the
document, threw inside its own normaliser, applied nothing, fell back to DHCP,
and both guests came up **healthy on addresses nobody asked for** with
`cloud-init status: done`. Nothing short of checking the address noticed.

vcows writes network-config v2 keyed on `nic0`/`nic1` and matched by MAC, with the
default route as `0.0.0.0/0`. Old cloud-init on RHEL renders through `sysconfig`
rather than netplan, and that path is untested. The check is not "did it boot" —
it is `ip -4 addr` inside the guest matching `configured_address` exactly, and the
interfaces actually being named `nic0`/`nic1`.

### C6 — monolithic `libvirtd`, and the fallback that has never run

**Needs any 9.x configured monolithic.** The provider's `sshcmd` dialer runs

```sh
sh -c 'which virt-ssh-helper >/dev/null 2>&1; if test $? = 0; then virt-ssh-helper "%s"; else ... nc $ARG -U %s; fi'
```

Every run to date has hit the first branch. **The `nc -U` fallback has never
executed**, and it is the branch a monolithic host takes. `preflight` uses
`qemu+ssh` and the apply uses `qemu+sshcmd`, so both need confirming on that
shape. Related: confirm `virt-ssh-helper` or `nc` is actually present on a stock
RHEL 9 — the README lists it as a hypervisor-side prerequisite and nothing has
checked it on that distro.

### C7 — SELinux, on a distro that is not Fedora

**Needs any 9.x, Enforcing.** The acceptance run found SELinux refusing to let
`sshd` open a libvirt socket, which is what killed the provider's `qemu+ssh`
transport and forced `sshcmd`. Confirm the same shape holds — if RHEL 9's policy
differs, the transport split may be narrower or wider than documented.

### C8 — the module's device and machine choices on older QEMU

**Needs any 9.x.** Each of these is one line in `main.tf` that has only ever been
validated against QEMU 10.2.2:

* `type_machine` passthrough — an alias like `q35` resolves to whatever
  `pc-q35-*` the target has. Confirm it defines.
* `discard = "unmap"` on the overlay driver (added in S10).
* `rngs = [{ model = "virtio", backend = { random = "/dev/urandom" } }]` (added
  in S10).

### C9 — the storage cache precondition, on a second host

**Needs any 9.x.** D35 rests on a measurement taken here: three of four running
domains' disks did not resolve until `pool.refresh(0)`, because they were written
out of band. `findings.md` calls this the rule that inverts without the refresh.
Confirm the same cache behaviour on a different distro and a different libvirt —
if a target refreshes on its own, the refresh is merely harmless there rather
than load-bearing, which is worth knowing before somebody decides it is redundant.

### C10 — a full acceptance run

**Needs any 9.x.** `acceptance.md` closes with "A RHEL 9 or RHEL 10 target" as
still open. Re-run the definition of done end to end — pool, upload, overlay,
cloud-init, domain, boot, reachable address, destroy with the state file deleted —
and record it the way `acceptance.md` records the first one, defects included.

## What a RHEL 9 host still will not settle

**D3, the real golden artifact.** Both runs to date used the stock
`Rocky-9-GenericCloud-Base` stand-in, so `cloud-init` and `growpart` are confirmed
for that image and nothing else. No amount of hypervisor time substitutes for the
artifact. It is the last item on the list that hardware cannot close.

## Where results go

The same places the rig session's did, and the same discipline: where a result
contradicts a shipped comment, the comment changes with it.

* `docs/findings.md` §2 for anything that changes an accepted gap or a rule,
  §6 for anything that closes a verification item.
* `docs/acceptance.md` for a run, defects and all.
* `docs/review-2026-08-29/2026-08-29-remediation-checklist.md` — C1, C2 and C3
  are Blocked rows there and each names what it is waiting for.
