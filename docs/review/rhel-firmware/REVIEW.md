# Review — `lane/rhel-firmware`

Input: `git diff origin/master...lane/rhel-firmware` and nothing else. Base
`e1cbc53`, head at the time of writing `6a4c420` plus this record commit.

```
 333  +0   docs/archive/plans/issue-75.md                        (reverification, landed in d093b10)
 600  +0   docs/review/rhel-firmware/reverify/RX-75.txt   (evidence, landed in d093b10)
  10  -5   orchestrator/backends/libvirt/tofu/main.tf
  82 -12   scripts/smoke-libvirt.sh
  85  -7   tests/libvirt-module.tftest.hcl
```

Three executable files, 177 added and 24 removed. One issue closed, `#75`.

## Lens 1 — did it do what the plan said?

### `6a4c420` against `docs/archive/plans/issue-75.md` §5

§5 specifies two lines:

```hcl
format   = each.value.loader_format == "raw" ? null : each.value.loader_format
template = each.value.nvram_template
```

Both landed verbatim. `template_format` is gone. **Pass.**

§5 also says `:134-135`'s comment "needs rewording, since one of the two
attributes is gone". The reworded comment is eight lines where the old one was
two, and it records the measurement rather than restating the code: which value
libvirt drops, which it keeps, that `templateFormat` is dropped for every value,
that nothing under `os` is `computed`, and why `format` must still be sent for
`qcow2`. §6 priced the file at **−3/+2**; it landed at **−5/+10**, and the
difference is entirely that comment. That is a real overrun against a plan whose
own governing constraint is that surface is a defect — the judgement is that a
ternary with no stated reason is the thing a later lane deletes, and this defect
was reachable precisely because `main.tf:161`'s standing note ("the provider's
schema is the ground truth") was not restated where it applied. Recorded rather
than hidden: `docs/archive/plans/issue-75.md` §10 carries the corrected numbers.

Each of §5's five rejected alternatives stayed rejected. Nothing here bumps the
provider, nulls `loader_format`, touches `schema.py`'s enum, or adds a
`lifecycle` block. `docs/provider-0.9.8.lock.hcl` and the `Containerfile` are
untouched, so `just verify-provider` gates exactly what it gated.

### `6a4c420` against §7

| §7 said | landed |
|---|---|
| split `:172-175` into `format == loader_format` and `template_format == null` | yes, and the second carries the correction its old message got wrong |
| new `run "a_raw_loader_declares_no_format_libvirt_would_drop"` with `app03` at `loader_format = "raw"` | yes, modelled on the bios block including its caveat |
| +69/−1 | **+85/−7** |
| repin the smoke VM, three assertions | repinned, **four** assertions |
| `plan -detailed-exitcode` asserted at 0 | yes, one `check`, seven lines with its comment |

Both overruns are §3's measurements catching up with §7's estimate, and both are
in `docs/archive/plans/issue-75.md` §10. The third tftest assertion is `_VARS.fd`, which
§7 did not name; it has its own teeth proof below. The fourth smoke assertion
splits `templateFormat` off `format='raw'`, which §3's table requires — they are
dropped under different rules and one line asserting both would name the wrong
reason on failure.

### The correction the plan did not schedule

`tests/libvirt-module.tftest.hcl:314` cited `main.tf:205` for the non-null
bridge arm. `docs/review/drift/REVIEW.md` L12 measured the truth as `:204` and
left it for this lane. **The landed number is neither.** The replacement comment
in `main.tf` is six lines longer than the two it replaces, so everything below
`:138` shifts +5 and the arm is at `:209`. Both endpoints are quoted in
`reverify/IMPL-75.txt` §A. Re-measured rather than copied, which is the whole
point of L12 having been left open.

## Lens 2 — do the new assertions have teeth?

### Which half of the defect an offline gate can pin

Stated plainly, because the plan and both new files depend on it.

**It can pin what the module emits.** `mock_provider "libvirt" {}` satisfies the
pinned provider's schema with generated values and performs no post-apply read.
So `tofu test` sees the `os` object `main.tf` built, and can assert that
`nv_ram.format` is null on the raw arm and that `nv_ram.template_format` is null
on every arm.

**It cannot pin any part of why those values are right.** That libvirt drops
`format='raw'`, keeps `format='qcow2'`, and drops `templateFormat` for every
value is a property of libvirtd's XML formatter. That the provider rebuilds
`os.nv_ram` from the returned XML while preserving the rest of `os` from the plan
is a property of the 0.9.8 binary. That OpenTofu then rejects the result is a
property of `optional=true, computed=false` in the provider schema. None of the
three is observable without a real libvirtd and the real provider, which is why
`#75` survived every offline gate this repo has and why the acceptance run could
not have caught it either.

The consequence for a reader: **a green `tofu test` is not evidence that `#75` is
fixed.** It is evidence that the module no longer emits the two attributes. The
new run block says so in its own header, as the bios block already did.

### The offline assertions, each mutated

Harness rebuilt from `tests/test_tofu_module.py`'s `mocked` fixture. Full output
in `reverify/IMPL-75.txt` §C.

| mutation | result |
|---|---|
| the module hunk reverted, tftest kept | `Failure! 3 passed, 2 failed` — two run blocks, three assertions |
| `main.tf:133`'s suffix ternary hardcoded to `.qcow2` | `Failure! 4 passed, 1 failed` — the `_VARS.fd` assertion alone |
| neither | `Success! 5 passed, 0 failed` |

Each failure names its own attribute and its own value:
`template_format is "qcow2"`, `format is "raw"`, `template_format is "raw"`,
`app03_VARS.qcow2`. No assertion fails for a reason another assertion already
covers.

**The second mutation is the one worth keeping.** `the_module_renders_what_the_acceptance_run_settled`
**passed** it. That block's existing suffix assertion is on `app02`, which is
`qcow2`, so a hardcoded `.qcow2` is invisible to it — the exact inversion of the
bug `454ee7c` fixed, and undetectable until a raw fixture existed. That is the
independent justification for the new `run` block over one more line on `app02`,
and it is not the justification §7 gave.

### The smoke assertions, and what is actually the gate

Four `check` lines replace `contains "$xml" '<nvram'`. Their teeth are not
symmetric with the offline ones and the script now says so:

* **The apply is the regression gate.** Put `template_format` back in `main.tf`
  and `tofu apply` exits 1 with "Provider produced inconsistent result after
  apply" at `smoke-libvirt.sh`'s apply line, before a single `check` runs. The
  gate fires; the assertions never get the chance.
* **The four `check` lines are evidence, not the trap.** They record what
  libvirtd stored — loader verbatim, `_VARS.fd` suffix, no `format='raw'`, no
  `templateFormat` — so a later reader can tell whether the libvirt behaviour the
  module is shaped around still holds. If libvirt 11 or 12 starts echoing
  `templateFormat`, the fourth line goes red on a still-green apply, which is the
  only warning this project would get.
* **One silent-pass was found and closed during implementation.** The first form
  extracted the `<nvram>` line into a variable and ran the two `absent` checks
  against it. `absent` on an empty haystack passes, so a missing varstore would
  have passed both — `tests/conftest.py:7`'s failure mode exactly. They now run
  against the whole `dumpxml` output, with the reason written beside them:
  `format=` and `templateFormat=` appear on no other element of this domain.

The `plan -detailed-exitcode` check has real teeth of a different kind: nothing
before it asserted that the provider's *refresh* read agrees with state. A drift
there is `#75` one step later, showing as a permanent diff rather than a failed
apply. Measured 0 on CI 33373910589.

**Not asserted, and named because the repin makes it newly observable:**
`assert_gone` does not check that the varstore file under
`/var/lib/libvirt/qemu/nvram/` is removed by `tofu destroy`. Until this lane the
gate created no varstore from a template, so there was nothing to check;
`docs/review/2026-08-29/11-lifecycle-recovery.md` already records that varstores
are "the one object class nothing tracks". Left alone — it is a finding about
`destroy`, not about `#75`, and widening this lane into it is the thing the plan
was written to avoid.

## Lens 3 — what moved?

### Behaviour

`main.tf` sends libvirt strictly less than it did: `templateFormat` never, and
`format` not when it is `raw`. **The XML libvirt receives is otherwise
unchanged** — CI 33373910589's stored `<os>` block is byte-identical to
33373539104's, and `main.tf:133`'s suffix ternary was not touched, so
`_VARS.fd` still follows a raw template. The `qcow2` path sends exactly what it
sent before.

No other file in `orchestrator/` changed. `schema.py`, `render.py`,
`variables.tf` and `tests/golden/libvirt.tfvars.json` are untouched, so nothing
an operator writes in `config.yaml` means anything different.

### Line numbers

One shift, `main.tf` +5 below `:138`. Every citation into that file was
re-measured (`reverify/IMPL-75.txt` §A):

| citation | state |
|---|---|
| `tftest:74` → `main.tf:88` | above the edit, holds |
| `tftest:328` → `main.tf:25`, `:34` | above the edit, holds |
| `tftest:329` → the bridge arm | `:205` → `:204` → **`:209`**, rewritten |
| `tftest:364` → `main.tf:34` | above the edit, holds |
| `tftest:415`, `:456` → `main.tf:116` | above the edit, holds |
| `tftest:468` → `main.tf:142`, `:133` | written by this lane, measured |
| `tftest:518` → `main.tf:133` | written by this lane, measured |

No live file outside `tests/libvirt-module.tftest.hcl` cites `main.tf` by line.
Dated documents under `docs/review/2026-08-2*/`, `docs/review/2026-08-3*/` and
`docs/research/tooling-2026-08-30.md` carry numbers into the shifted region and are left
alone, which is the boundary `docs/review/drift` drew: a dated report records
what was true when it was written.

`scripts/smoke-libvirt.sh` grew 70 lines net, of which 47 are the comment at the
tfvars recording the coverage the repin gives up. Nothing cites that file by
line.

### Coverage

**Gained:** the raw firmware branch, offline and in CI. Before this lane it was
evaluated by no test and applied by no gate — not `tofu test`, not the smoke
gate, not the Fedora acceptance run, not the rig.

**Lost:** libvirt autoselecting a firmware from the host's own descriptors and
materialising a varstore from no template. No gate anywhere applies that branch
against a real libvirtd now. What the module *emits* on it is still pinned
offline — `app01`'s `os.firmware == "efi"`, `os.nv_ram == null`, `os.loader ==
null`, `os.loader_type == null`, `os.loader_readonly == null` — so the loss is
the round trip, not the expression.

The swap is a judgement and the review does not soften it: it trades a branch
with no known defect, which the acceptance run applied, for a branch with a
reproduced one that had never been applied anywhere. Carrying both was priced at
roughly +60 lines against a −5/+10 fix, because `DOMAIN`, `OVERLAY_VOL`,
`SEED_VOL`, `MAC` and `MARKER_ID` are script globals that four functions key
off. `scripts/smoke-libvirt.sh` carries the reasoning at the tfvars so it is
found by a reader of the gate rather than a reader of this file.

### The gates

`just check`: six lint gates ok, `ty` clean, **439 passed, 25 skipped** — the
baseline, unchanged, before and after. `just test-tofu` green.
`just smoke-libvirt` was **not** run locally: it `sudo`-installs packages and
starts a system daemon. CI only.

## Ledger

| # | item | verdict |
|---|---|---|
| L1 | `main.tf`'s two expression lines are §5's, verbatim | **pass** |
| L2 | Five rejected alternatives all stayed rejected; provider pin, lock and `Containerfile` untouched | **pass** |
| L3 | `main.tf` landed −5/+10 against §6's −3/+2 | **overrun, recorded** — all six lines are the comment §5 asked for; plan §10 carries the corrected number |
| L4 | tftest landed +85/−7 against §7's +69/−1 | **overrun, recorded** — one assertion and one comment block beyond the estimate |
| L5 | Smoke gate landed four assertions against §7's three | **deliberate** — §3's table drops `format` and `templateFormat` under different rules |
| L6 | The falsified message at `tftest:172-175` was corrected, not merely made to pass | **pass** |
| L7 | Offline teeth: revert the hunk → `3 passed, 2 failed`, three assertions each naming its attribute | **pass**, replayed in `IMPL-75.txt` §C |
| L8 | The `_VARS.fd` assertion has independent teeth, and the existing app02 assertion cannot substitute | **pass** — the suffix mutation is invisible to the golden fixture |
| L9 | Which half of `#75` the offline gate cannot pin is stated, in this review and in the tftest itself | **pass** |
| L10 | A silent-pass in the first draft of the smoke assertions was found and closed before commit | **pass** — `absent` on an empty extraction |
| L11 | `tftest:314`'s bridge citation re-measured to `:209`, not copied from either prior value | **pass** |
| L12 | Every other citation into `main.tf` from a live file re-measured; none moved | **pass** |
| L13 | `just check` `439 passed, 25 skipped` before and after; `just test-tofu` green | **pass** |
| L14 | Green CI smoke run on the landed branch | see below |
| L15 | Exactly one closing reference on PR #108, `#75` | **pass** — `gh pr view 108 --json closingIssuesReferences` returns `[75]`; `#107` is named in the body with no keyword |
| L16 | `assert_gone` does not check the varstore file is removed | **open** — newly observable because of the repin, but a `destroy` finding, not `#75`'s |
| L17 | Autoselect has no CI coverage anywhere | **open by decision** — recorded in `scripts/smoke-libvirt.sh` and above |
| L18 | The provider bug is not fixed and not reported upstream by this lane | **open** — §5 says file it; `0.9.8` and `verify-provider` are unchanged here |
| L19 | Dated docs under `docs/review/2026-08-*/` carry `main.tf` numbers into the shifted region | **left** — a dated report records what was true when written |

L16, L17, L18 and L19 are what this lane found or caused and did not fix. Three
are deliberate; L16 is the one a later lane should pick up.

### CI

| run | what |
|---|---|
| 33373539104 | the defect reproduced on the shipped module (pre-lane) |
| 33373910589 | the candidate fix, all gates green, no refresh drift (pre-lane) |
| **33376925933** | PR #108 at `6a4c420`, the landed module and the landed smoke gate. `check`, `tofu` and `smoke` all green |

`33376925933` is the run L14 asks for and the only one that matters for this
lane: it applied the repinned raw firmware against a real libvirtd on the code
that ships. All four new assertions passed, the refresh-drift check returned 0,
and apply and destroy were both clean:

```
ok    the pinned raw loader reached the domain verbatim
ok    the varstore path follows the raw template's suffix
ok    libvirt omits format='raw' from the varstore, which is why the module must not declare it
ok    libvirt omits templateFormat from the varstore, for every value
ok    the applied domain re-reads clean: no attribute drifts on refresh
Apply complete! Resources: 4 added.   Destroy complete! Resources: 4 destroyed.
```

This record commit adds documents only — no file under `orchestrator/`,
`tests/` or `scripts/` changes after `6a4c420` — so its own CI run re-validates
the identical tree.

Five CI runs were dispatched by the reverification (`reverify/RX-75.txt` §K);
implementation spent two more — the PR run above and this record commit's.
