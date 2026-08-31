# Issue #75 — the pinned-loader firmware path dies after the volumes are written

## 1. Reverification verdict

**Reproduced, on a second CI run against a second commit, and the defect is
wider than filed.**

`gh workflow run ci.yml --ref spike/rhel-firmware-repro`, run **33373539104**,
job 99429789235: the shipped module at `be50ae6` with the smoke gate's tfvars
repinned to Ubuntu's raw `.fd` firmware.

```
libvirt_volume.base[0]:        Creation complete after 0s
libvirt_volume.seed["smoke01"]:    Creation complete after 0s
libvirt_volume.overlay["smoke01"]: Creation complete after 0s
libvirt_domain.vm["smoke01"]: Creating...
Error: Provider produced inconsistent result after apply
  .os.nv_ram.template_format: was cty.StringVal("raw"), but now null.
Error: Provider produced inconsistent result after apply
  .os.nv_ram.format: was cty.StringVal("raw"), but now null.
SPIKE apply exit status: 1
```

Two diagnostics, identical to the original run 33355418177, on a module
`git log --oneline -- .../tofu/main.tf` shows has not changed since `4eb378b`.

**Not transient.** A second apply of the same config taints the domain, destroys
it, recreates it and fails on both attributes again.

`raw` is not an exotic value. `schema.py:503-514` makes `loader_format`
**mandatory** whenever `loader` is set, so every operator who pins a RHEL
firmware must write it. `validate` on such a config exits **0**.

## 2. Anchor table

All at `be50ae6`, all re-read or re-run.

| anchor | state |
|---|---|
| `main.tf:119` `loader_format = each.value.loader_format` | ok — and it does **not** fail; see §3 |
| `main.tf:127-139` the `nv_ram` block | ok |
| `main.tf:133` the `.qcow2`/`.fd` suffix ternary | ok, and correct — run 33373910589 wrote `vcows-smoke01_VARS.fd` |
| `main.tf:136` `format = each.value.loader_format` | ok, **fails** |
| `main.tf:138` `template_format = each.value.loader_format` | ok, **fails** |
| `main.tf:161` "the provider's schema is the ground truth" | ok |
| `schema.py:134` `"loader_format": {"enum": ["raw", "qcow2"]}` | ok |
| `schema.py:503-514` `loader` without `loader_format` is an error | ok — the issue did not name this |
| `render.py:96` passes `loader_format` through verbatim | ok |
| `variables.tf:62-63` "RHEL ships a raw .fd" | ok |
| `scripts/smoke-libvirt.sh:193-195` pins `loader`/`loader_format`/`nvram_template` all null | ok |
| `tests/libvirt-module.tftest.hcl:19` `mock_provider "libvirt" {}` | ok |
| `tests/libvirt-module.tftest.hcl:172-175` asserts `template_format == loader_format` | ok — and it is the assertion the fix must delete |
| `docs/provider-schema-0.9.8.json` — `nv_ram.format`, `nv_ram.template_format` | `optional: true`, **not** `computed` |
| `docs/rhel9-target.md:62-73` check C2, "the raw `.fd` varstore" | ok — this failure is the one C2 was written to go looking for |

## 3. Corrections to the issue body

**C1 — "The apply dies with three volumes on the hypervisor and no domain" is
wrong.** libvirt accepted the XML. The domain was defined *and started*, and
`virsh dumpxml` returned it as `<domain type='qemu' id='1'>` carrying its marker.
The failure is OpenTofu's post-apply consistency check, downstream of a
successful `DomainDefineXML` and `DomainCreate`. What is left behind is three
volumes **and a running VM** — worse in extent, better in recoverability, since
the domain carries the marker and `vcows destroy` finds it while the volumes
carry none. `findings.md §2`'s orphan-volume gap is still reached, but by the
volumes only.

**C2 — "libvirt treats `raw` as the default for `<nvram>` and omits it" explains
one of the two attributes, not both.** Measured on libvirt 10.0.0 by `virsh
define`/`dumpxml` with no provider in the loop (run 33374187704, run
33374623746):

| sent on `<nvram>` | echoed back |
|---|---|
| `format='raw'` | dropped |
| `format='qcow2'` | **kept** |
| `templateFormat='raw'` | dropped |
| `templateFormat='qcow2'` | dropped |
| `templateFormat='raw'` beside `format='qcow2'` | **dropped** |

`format` follows the default rule the issue names. `templateFormat` does not:
libvirt 10.0.0 never echoes it, for any value, including one that differs from
`format`. It is write-only.

**C3 — "This is the RHEL path, and it is the target" understates it.** Because
`templateFormat` is dropped unconditionally, `loader_format: qcow2` carries the
same defect on this libvirt. The Fedora acceptance run passed with `qcow2` on
libvirt **12.0.0**, so the two versions must differ here — that part is inference
from `acceptance.md`, not something measured in this reverification, because an
Ubuntu runner cannot host the module's qcow2 path at all (§9). The fix does not
depend on which way libvirt 12 behaves.

**C4 — `.os.loader_format` carries the same `"raw"` and does not fail, and the
reason is the provider, not libvirt.** The stored XML has no `format` on
`<loader>` either — libvirt dropped it there too. Yet the provider's state
records `"loader_format": "raw"`, and records `"type_machine": "q35"` where
libvirt stored `machine='pc-q35-noble'`. The provider mirrors the `nv_ram` object
out of the returned XML and preserves the rest of `os` from the plan. So the
diagnostic's "This is a bug in the provider" is accurate, and it is an internal
inconsistency in the provider rather than a libvirt quirk it merely reports.

**C5 — the second open question is answered more strongly than it is asked.**
`loader_format: raw` is not just reachable through `config.py`; `schema.py:503`
makes it the only spelling validation accepts for a pinned loader. There is no
hand-written-tfvars-only escape and no config that pins a RHEL firmware and
avoids the failing attribute.

**Correct as filed:** the volumes are written first; `main.tf:133`'s `.fd` branch
had never run against a real libvirt; `mock_provider` cannot see this; the
acceptance run could not have caught it.

## 4. The defect

Three layers, and only the third is a bug.

**libvirt.** `<nvram>`'s `format` is omitted from the formatted XML when it is
`raw`, and `templateFormat` is omitted always. Deliberate — a formatter that does
not restate its own defaults. libvirt does not probe either: given a qcow2
template and no `format`, run 33374187704 shows it adds none. It also refuses
`loader.format` and `nvram.format` that disagree (`XML error: Format mismatch`),
so the two are one setting, which is what `main.tf:134-135` already says.

**The provider.** `terraform-provider-libvirt` 0.9.8 rebuilds `os.nv_ram` from
the XML libvirt returns and leaves the rest of `os` at its planned value. Nothing
under `os` is `computed` in the schema, so OpenTofu requires the applied value to
equal the planned one, and there is no room to absorb a difference. Two
attributes the module planned as `"raw"` come back null and the apply dies.

**The module.** `main.tf:136` and `:138` declare values libvirt is guaranteed not
to return. That is the layer this project controls, and `main.tf:161` already
states the rule: the provider's ground truth wins.

The failure mode is the bad one because of the ordering. `libvirt_volume.base`,
`.overlay` and `.seed` complete first; the domain is defined, started, and then
rejected by OpenTofu. The next `deploy` hits `findings.md §2`'s orphan-volume
refusal — the accepted gap, reached through a path an operator following the
README chose deliberately.

## 5. The fix

`main.tf:136-138`. Emit only what libvirt will hand back.

```hcl
      format   = each.value.loader_format == "raw" ? null : each.value.loader_format
      template = each.value.nvram_template
```

`template_format` goes entirely: there is no value for which libvirt echoes it,
so any value at all is a planned attribute that comes back null. `format` stays
conditional rather than being dropped too, because §3's table shows `qcow2` *is*
echoed — and it has to be sent: libvirt does not probe, and a qcow2 varstore
declared raw is the non-booting inversion of acceptance defect 3.

This changes nothing libvirt receives. Run 33373910589's stored `<os>` block is
byte-identical to run 33373539104's, and `main.tf:133`'s suffix ternary is
untouched, so `_VARS.fd` still follows the raw template.

`main.tf:134-135`'s comment ("one config field settles both") needs rewording,
since one of the two attributes is gone.

**Rejected — pin the provider and report it upstream, changing nothing here.**
The diagnostic asks for it and §4 agrees it is a provider bug, so file it. As
*the* fix it is wrong twice over: the provider is pinned at `0.9.8` behind four
hand-maintained hashes that no bot can recompute (`CLAUDE.md`), so a bump is a
deliberate multi-file ritual, not something that arrives; and it leaves the RHEL
target — the target — broken for as long as upstream takes. `main.tf:161`'s note
is the project's standing answer to exactly this shape.

**Rejected — drop `format` as well as `template_format`.** Measured wrong. Run
33374187704 echoes `format='qcow2'` back, so it costs nothing to declare, and run
33374623746 shows libvirt performs no detection: a qcow2 varstore with no format
is opened raw. This trades a failing apply for a VM that does not boot.

**Rejected — `lifecycle { ignore_changes = [os] }`.** Does not apply. OpenTofu's
"inconsistent result after apply" check runs on the value the provider returns
from `ApplyResourceChange`; `ignore_changes` shapes the *plan*. It also blinds
the resource to every other firmware attribute.

**Rejected — remove `"raw"` from `schema.py:134`'s enum.** Shuts the RHEL path
off to make the error go away. The RHEL path is the target.

**Rejected — null `loader_format` at `:119` when it is raw, for symmetry.**
Measured unnecessary: §3 C4 shows the provider preserves it and OpenTofu accepts
it. Adding a second ternary against a failure that does not occur is surface the
defect does not warrant.

## 6. Surface cost

`orchestrator/backends/libvirt/tofu/main.tf`: **−3/+2** lines of expression, one
attribute deleted, one comment reworded. No new variable, no new file, no change
to `variables.tf`, `schema.py`, `render.py` or the golden tfvars.

`tests/libvirt-module.tftest.hcl`: **+69/−1**, 447 → 515 lines. One existing
assertion split in two, one new `run` block. Priced in §7.

`scripts/smoke-libvirt.sh`: a four-line tfvars change plus three assertions.
Priced in §7.

`tofu fmt -check` on the patched `main.tf` and tftest: exit 0.

## 7. The failing test

The two halves of this defect need different gates, and neither gate can do the
other's job.

### What can be pinned offline, and what that is worth

`mock_provider` satisfies the schema with generated values and performs no
post-apply read. It can therefore pin **what the module emits** and nothing about
what comes back. That is a real regression guard on the expression at `:136`, and
it is not the defect.

Built and measured locally against the harness `tests/test_tofu_module.py:168-187`
sets up (module `*.tf` + `docs/provider-0.9.8.lock.hcl` + the golden tfvars,
`tofu init` then `tofu test`, no CI):

* `tests/libvirt-module.tftest.hcl:172-175` asserts
  `nv_ram.template_format == loader_format`. With the fix and the tftest
  unchanged: `Failure! 3 passed, 1 failed`. So the fix **requires** touching it,
  and its message — "libvirt reads the declared format, not the extension" — is
  a claim §3's table falsifies for `template_format`. Splitting it into
  `format == loader_format` and `template_format == null` is a correction, not
  new surface.
* A new `run "a_raw_loader_declares_no_format_libvirt_would_drop"`, modelled on
  the existing `a_bios_domain_is_given_no_firmware_and_no_varstore` block, with a
  hand-written `app03` carrying `loader_format = "raw"`. The golden tfvars pins
  `qcow2` on `app02`, so the raw arm of `:136` is evaluated by nothing today.
* With the fix: `Success! 5 passed, 0 failed`, exit 0.
* **Teeth, proved by reverting the fix and keeping the test:**
  `Failure! 3 passed, 2 failed` — two run blocks, three assertions, each naming
  its attribute (`template_format is "qcow2"`, `format is "raw"`,
  `template_format is "raw"`).

Price: +69/−1 lines, no runtime cost (`tofu test` runs in the existing `tofu`
job), and it needs the caveat comment the bios block already carries — a green
run here must not be read as having settled #75.

### What only the smoke gate can pin

That libvirt drops the attribute and the provider returns null is a property of
libvirtd plus the provider binary. No offline gate can observe it. The smoke gate
is the only surface that can, and it currently pins the other branch:
`smoke-libvirt.sh:193-195` sets `loader`, `loader_format` and `nvram_template`
all null.

**Recommended: repin the single smoke VM to the raw `.fd` loader**, with three
assertions on what libvirtd stored:

```
ok    the pinned raw loader reached the domain verbatim
ok    the varstore path follows the raw template's suffix
ok    libvirt omits format='raw', which is why the module must not declare it
```

Measured, run 33373910589, all 27 assertions green, `Apply complete! 4 added`,
`Destroy complete! 4 destroyed`.

**Price, measured on the same runner class:**

| | apply → done |
|---|---|
| baseline, run 33372851070, autoselect | 7.54 s |
| repinned + drift check, run 33373910589 | 12.10 s |

Of the 4.6 s, about 3.4 s is the drift check below; the branch swap itself is
about **1.1 s**.

**What the swap costs in coverage:** autoselect loses its only CI exercise.
`smoke-libvirt.sh:166-170` chose it deliberately — "a thing only a real libvirtd
does". That reasoning was written before this defect existed and it now points
the other way: autoselect has no known defect and was exercised by the Fedora
acceptance run, while the pinned branch has a live one and has never run
anywhere.

**The alternative, and why not:** carry both branches as two VMs in one apply.
`DOMAIN`, `OVERLAY_VOL`, `SEED_VOL`, `MAC` and `MARKER_ID` are script globals and
`assert_volumes`, `assert_domain`, `assert_gone` and `cleanup` all key off them,
so this means parameterising four functions — **estimated** at roughly +60 lines,
not measured, because it was not built. That is a large multiple of the fix
itself against a branch the acceptance run already covers. Take the swap; if
autoselect later needs its own CI coverage, that is its own change.

### One addition worth its price

`tofu plan -detailed-exitcode` after the apply, asserted at 0. A successful apply
proves the *create* read matched; nothing today proves the *refresh* read does,
and a drift there is the same defect one step later, showing up as a permanent
diff rather than a failed apply. Four lines and one `check`. Measured cost
**3.4 s** (the assert block goes from 0.13 s to 3.50 s); measured result on the
fixed module: `0 (0 = no drift, 2 = drift)`.

## 8. Verification

| what | how | result |
|---|---|---|
| the defect still exists at `be50ae6` | CI 33373539104 | two diagnostics, apply exit 1, volumes written, domain running |
| it is not transient | CI 33373539104 probe/05 | second apply taints, recreates, fails identically |
| libvirt is what drops the attributes | CI 33374187704, 33374623746 — `virsh define`/`dumpxml`, no provider | `format='raw'` dropped, `format='qcow2'` kept, `templateFormat` dropped for every value |
| the fix does not change the XML libvirt receives | CI 33373910589 vs 33373539104 | stored `<os>` blocks byte-identical |
| the fix survives a real apply | CI 33373910589 | 27/27 assertions ok, apply + destroy clean |
| no refresh-time drift remains | CI 33373910589 | `plan -detailed-exitcode` = 0 |
| the raw shape boots libvirt's own varstore | CI 33374623746 | domain started, varstore is a 540672-byte raw copy of `OVMF_VARS_4M.fd` |
| the offline test passes with the fix | local `tofu test` | 5 passed, 0 failed |
| the offline test fails without it | local `tofu test`, fix reverted | 3 passed, 2 failed, three named assertions |
| `just lint` / `just typecheck` / `just test` | CI 33373910589 `check` job | green |
| `just test-tofu` | CI 33373910589 `tofu` job | green |

Raw output with exit statuses: `docs/review-rhel-firmware/reverify/RX-75.txt`.

Five CI runs of a budget of six. The sixth was not spent — see §9.

## 9. Non-goals

* **The provider bug itself.** §4 identifies it and §5 says to file it upstream.
  Nothing here bumps `0.9.8`, and `just verify-provider` stays as it is.
* **`os.loader_format` and `os.type_machine`.** The provider preserves both from
  the plan rather than from the XML, which is inconsistent with how it treats
  `nv_ram` and is exactly why this failure is asymmetric. It is not failing, so
  it is not being changed.
* **The module's `qcow2` path end to end.** It cannot be exercised on an Ubuntu
  runner. Run 33374365926 tried and died earlier, at define:
  `Unable to find 'efi' firmware that is compatible with the current
  configuration` — because `main.tf:116` emits `firmware = "efi"` beside the pin
  and every one of the runner's four `/usr/share/qemu/firmware/*.json`
  descriptors declares format `raw`. This is the reverse of `rhel9-target.md`'s
  C1 question: libvirt's autoselection does not defer to a pin, it validates the
  pin against the host's descriptors and refuses a format they do not carry.
  Worth its own issue; it is not #75.
* **Making the smoke gate cover both firmware branches.** Priced in §7 and
  deferred.
* **`docs/rhel9-target.md` C2.** This settles the CI half of it on libvirt 10.0.0.
  The rig half — a real RHEL/Rocky 9 or 10 target — stays open, and so does
  `acceptance.md`'s "Still open" entry.

## 10. What landed, and where it differs from this plan

Written at `be50ae6`; implemented on top of `e1cbc53`. Rebase was clean — the
drift lane touched no file this lane owns. Re-measured, and three numbers above
are now stale:

| this plan says | landed |
|---|---|
| baseline for `just check` | **439 passed, 25 skipped**, not what §8 implies |
| the fix is at `main.tf:136-138` | correct at `origin/master`; the landed expression is `:142-143` |
| `main.tf` **−3/+2** | **−5/+10**. The two lines of expression are as §5 wrote them; the comment they replace grew from 2 lines to 8, which is what §5 asked for when it said `:134-135` needed rewording |
| `tests/libvirt-module.tftest.hcl` **+69/−1** | **+85/−7**. §7's split assertion carries the correction that falsified its old message, and the new run block carries a third assertion — `_VARS.fd` — proved separately in `reverify/IMPL-75.txt` |
| `scripts/smoke-libvirt.sh` "three assertions" | **four**: §3's table says `templateFormat` is dropped for *every* value, so it gets its own line rather than riding on `format='raw'`'s |

**The comment growth is not incidental.** `main.tf` below `:138` shifts +5, which
moves the non-null bridge arm from `:204` to `:209`. `docs/review-drift/REVIEW.md`
L12 left `tests/libvirt-module.tftest.hcl:314`'s citation of `main.tf:205`
unfixed for this lane; it lands as `:209`, which is neither the wrong number nor
the number the drift lane measured. Both endpoints are in `reverify/IMPL-75.txt`
§A.

§7's recommendation is taken: the smoke gate is repinned rather than carrying
both branches. §7's own statement of the cost stands and is now written into
`scripts/smoke-libvirt.sh` beside the tfvars, so a reader of the gate finds it.

Not done, and deliberately: the firmware-descriptor finding in §9 is filed as
issue #107, which this lane does not touch and must not close.
