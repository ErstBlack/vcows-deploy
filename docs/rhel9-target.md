# What a RHEL 9 host would settle

Everything vcows has run against is Fedora 44 with libvirt 12.0.0. Fedora being
*newer* than any shipping target means it cannot surface failures that run in
that direction, and the rig session left exactly that residue: questions no
privilege level on this hardware can answer, because the answer depends on the
host being older.

This is that list. It is a work order, not a design document.

## Read this first: "a RHEL 9 host" is three different machines

The checks below do **not** all want the same vintage, and getting a current
Rocky 9 box does not cover the list. Match the host to the check or the run
proves less than it looks like it did.

| vintage | libvirt | what only it can answer |
|---|---|---|
| **current 9.x** (9.8) | 11.10.0 | monolithic vs split daemons, SELinux, the storage cache, a non-Fedora acceptance run. **Not** the flag shed: 11.10.0 accepts every bit vcows passes, so the gate never fires. |
| **9.0 / 9.1 EUS** | 8.0.0 / 8.5.0 | the `undefine_mask` flag shed. This is the only vintage where `destroy.py`'s version gate has ever had anything to do. |
| **9.0 – 9.3** | — | cloud-init 22.1 / 23.1 and the `sysconfig` renderer path. |

The container's own client is libvirt **11.10.0**. Against a 9.0 EUS daemon that
is a three-major gap in the client-newer-than-daemon direction, which nothing has
exercised — the rig's gap was 11.10.0 against 12.0.0, one minor the other way.

## Before you start

**The rig gate is not portable, and it will fail on fixtures rather than on code.**
`tests/test_libvirt_rig.py` asserts against *this* rig: a pool called `images`
holding a `_cloud-images` directory entry and a specific base image, a live lease
on `192.168.122.82`, and DHCP reservations on `.101`–`.105`. The two probe
domains it reads are the exception — its own `probes` fixture defines and
undefines them. Pointing `VCOWS_RIG_URI` at a new host without staging the rest
produces failures that say nothing about the code. Either stage the fixtures or
run the checks below by hand and leave the gate on its own rig.

What to bring: the shipped container image, a config, a golden qcow2 matching the
vintage under test, and read access to `/var/lib/libvirt/qemu/nvram/` — which
means root, or a group that can read it. The unprivileged deploy account cannot,
and that is what made the varstore question unanswerable for as long as it was.

## The checks

### C1 — the flag shed nobody has ever seen fire

**Needs 9.0/9.1 EUS.** `undefine_mask` gates on the *daemon's* version against
`_GATED = ((5006000, CHECKPOINTS), (8009000, TPM))`. On 8.0.0 and 8.5.0 the mask
sheds exactly one bit — `UNDEFINE_TPM` — and keeps checkpoints, since both are
above 5.6.0. No run in this project's history has shed anything: every target has
been ≥ 8.9.0. Confirm the undefine succeeds with the reduced mask, and confirm
what it leaves behind, which is the second half of the varstore question.

### C2 — a teardown where the TPM bit is genuinely unused

**Needs any 9.x.** Every domain on this rig carries a `<tpm>`, so the TPM bit is
exercised there by accident. `create.DOMAIN_XML` emits no TPM device, so a
vcows-created domain on an old daemon is the case that has never been isolated:
the bit is dropped *and* there was nothing for it to do.

### C3 — cloud-init 22.1 / 23.1 and the `sysconfig` renderer

**Needs a 9.0–9.3 image, which is a download rather than a hypervisor.** Schedule
this first of everything here. It is the same shape as the acceptance run's
defect 5, which was the worst of the five that run found: cloud-init accepted the
document, threw inside its own normaliser, applied nothing, fell back to DHCP,
and both guests came up **healthy on addresses nobody asked for** with
`cloud-init status: done`. Nothing short of checking the address noticed.

vcows writes network-config v2 keyed on `nic0`/`nic1` and matched by MAC, with the
default route as `0.0.0.0/0`. The keys are identifiers rather than device names:
cloud-init renames a matched interface only when the entry carries `set-name`,
which vcows deliberately does not write, so the guest keeps whatever name the
image gives it. Old cloud-init on RHEL renders through `sysconfig` rather than
netplan, and that path is untested. The check is not "did it boot" — it is
`ip -4 addr` inside the guest showing `configured_address` exactly, on the
device carrying the MAC vcows derived, whatever that device is called.

### C4 — monolithic `libvirtd`

**Needs any 9.x configured monolithic.** vcows dials one scheme, `qemu+ssh`
(`connection_uri` in `orchestrator/backends/libvirt/schema.py`), and reaches the
daemon through `virt-ssh-helper` on the hypervisor. Every run to date has been
against split daemons. Confirm the helper resolves to the monolithic socket, and
confirm `virt-ssh-helper` or `nc` is present on a stock RHEL 9 at all — the
README lists it as a hypervisor-side prerequisite and nothing has checked it on
that distro.

### C5 — SELinux, on a distro that is not Fedora

**Needs any 9.x, Enforcing.** The acceptance run found SELinux refusing to let
`sshd` open a libvirt socket. `qemu+ssh` is the only transport vcows has, so
confirm RHEL 9's policy lets `virt-ssh-helper` reach the daemon under the deploy
account's ssh session.

### C6 — the storage cache precondition, on a second host

**Needs any 9.x.** D35 rests on a measurement taken here: three of four running
domains' disks did not resolve until `pool.refresh(0)`, because they were written
out of band. `findings.md` calls this the rule that inverts without the refresh.
Confirm the same cache behaviour on a different distro and a different libvirt —
if a target refreshes on its own, the refresh is merely harmless there rather
than load-bearing, which is worth knowing before somebody decides it is redundant.

### C7 — a full acceptance run

**Needs any 9.x.** `docs/archive/acceptance.md` closes with "A RHEL 9 or RHEL 10 target" as
still open. Re-run the definition of done end to end — pool, upload, overlay,
cloud-init, domain, boot, reachable address, destroy by marker — and record it the
way `docs/archive/acceptance.md` records the first one, defects included.

### C8 — the device and machine choices, on older QEMU

**Needs any 9.x.** Three lines of `create.DOMAIN_XML`
(`orchestrator/backends/libvirt/create.py`) have only ever been validated against
QEMU 10.2.2:

* `machine='{machine}'` passthrough — an alias like `q35` resolves to whatever
  `pc-q35-*` the target has. Confirm it defines.
* `discard='unmap'` on the overlay's `<driver>`.
* `<rng model='virtio'>` backed by `/dev/urandom`.

`virsh define` then `virsh dumpxml` answers all three; no boot and no KVM needed.

## What a RHEL 9 host still will not settle

**D3, the real golden artifact.** Both runs to date used the stock
`Rocky-9-GenericCloud-Base` stand-in, so `cloud-init` and `growpart` are confirmed
for that image and nothing else. No amount of hypervisor time substitutes for the
artifact. It is the last item on the list that hardware cannot close.

## Where results go

The same places the rig session's did, and the same discipline: where a result
contradicts a shipped comment, the comment changes with it.

* `docs/findings.md` §2 for anything that changes an accepted gap or a rule.
* `docs/archive/acceptance.md` for a run, defects and all.
