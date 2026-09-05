# Archived: the RHEL 9 checks that were about the OpenTofu module

Two checks lifted out of `docs/rhel9-target.md` whose subject was `main.tf`
and the `dmacvicar/libvirt` provider. That module is gone, so neither is a work
order; the firmware measurements are what makes them worth keeping.

---

## The checks

### C1 — `<os firmware='efi'>` beside a pinned loader (review 2.15)

**Closed 2026-08-31 by #107. No host needed.** The module no longer emits the two
together: `main.tf`'s `firmware` line is now guarded on `loader == null`, so a
pinned loader stands alone and never reaches autoselection's validation at all.

**Verified, not reasoned.** CI run 33437247928 pinned a qcow2 loader on the same
Ubuntu runner whose four descriptors all declare `raw` — the configuration that
run 33374623746 saw refused at define — and it defined. One caveat for anyone
re-checking by hand: libvirt fills `firmware='efi'` back into the stored XML when
the pin matches a descriptor it can name, so a raw `.fd` pin dumps with the
attribute present even though the module never sent it. `virsh dumpxml` cannot
tell the two apart; the module's own output is pinned by
`tests/libvirt-module.tftest.hcl`.

**What stands guard, and what it is standing in for.** The fix is only worth
anything while omitting the attribute keeps a pin out of that validation — a
property of libvirt, not of anything this repo controls, and one that no
assertion over the module's own fixture can reach, because the format this
runner's descriptors refuse is `qcow2` while the fixture pins `raw` (that pin is
#75's branch and the delivery target's shape, so it is not tradeable).
`scripts/smoke-libvirt.sh`'s `probe_pinned_loader_escapes_autoselection` defines
one throwaway domain out of band of the module, with a qcow2 loader and no
`firmware` attribute, and asserts it defines. A libvirt that reopens #107 without
anyone touching the module is what that probe exists to name. It is early notice
rather than the only defence: the failure it guards against is a refusal at
define, which is loud — the value is that the notice arrives in CI instead of at
a site.

The rest of this section is the record of how that was arrived at, and the last
paragraph's condition is the one that was met.

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

Since #107 that last point narrows to the **autoselect** branch only — a VM with
no `loader`. Those still hand libvirt the choice and are still bounded by what
the host's descriptors carry, and `virsh domcapabilities` is still the way to ask
before a deploy. A VM that pins a loader is no longer subject to it, which is
what makes the RHEL raw `.fd` shape deployable from a config written against a
Fedora rig.

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

