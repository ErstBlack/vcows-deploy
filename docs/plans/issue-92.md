# Issue #92 — `just check` is red on master: eight SC2312 in `smoke-libvirt.sh`

## 1. Reverification verdict

**Reproduced, on two independent surfaces.**

Clean `b58f924` worktree:

```
$ just check
  ok    ruff check / ruff format / hadolint / tofu fmt
  FAIL  shellcheck
  ok    workflows carry no logic
error: 1 gate(s) failed: shellcheck
```

`just typecheck` green; `just test` `411 passed, 25 skipped`. Only the shellcheck
gate is red.

CI agrees, and dates it:

| run | commit | `check` |
|---|---|---|
| 33359240404 | `b58f924` | **failure** (`tofu` and `smoke` both pass) |
| 33357163360 | `a3068e3` | **failure** |
| 33357112629 | `454ee7c` | success |

Identical eight SC2312 hits in the runner log, so this is not a difference
between the maintainer's EPEL shellcheck 0.10.0 and the apt one `os-deps.sh:30`
installs on `ubuntu:24.04`.

## 2. Anchor table

All at `b58f924`, all confirmed by running the gate.

| anchor | state |
|---|---|
| `scripts/lint.sh:183-187` — the four `-o` flags | ok |
| `scripts/smoke-libvirt.sh:298, 299, 300, 320, 321, 427, 429, 448` | ok, all eight |
| `scripts/smoke-libvirt.sh:292-296` — "Every readback here goes through a variable" | ok |
| `scripts/lib.sh:16` `set -euo pipefail` | ok |

## 3. Corrections to the issue body

None. Everything the issue states was re-measured here and held.

One thing the issue did not name, found while writing the fix:
`assert_domain:387` already reads `xml="$(vsh dumpxml "$DOMAIN" 2>&1 || true)"`.
The adjacent idiom for this exact situation was already in the same function,
three lines above two of the eight findings.

## 4. The defect

Neither PR was wrong. **PR #72** (`454ee7c`) enabled four shellcheck optional
checks, measured 9 findings across the scripts that then existed, fixed 2 and
annotated 7, and landed green — `smoke-libvirt.sh` did not exist in its tree.
**PR #73** (`a3068e3`) added `smoke-libvirt.sh`, forked at `672a500` where
`lint.sh` had no `check-extra-masked-returns`
(`git show 8925601:scripts/lint.sh | grep -c check-extra-masked-returns` → `0`).

They merged 51 seconds apart. PR #73's `pull_request` run was never re-run
against the base #72 had just moved, so the combination was tested by nothing
until the push run on master, which failed. `ci.yml` has no `concurrency:` group
and the repo has no merge queue, so nothing re-runs a PR when its base moves.

## 5. The fix

`183b927` set the split, and this follows it rather than inventing one:

* a readback whose **value decides something** → assign on its own line, then
  test (`lib.sh:140`, `image-build.sh:41`)
* a masked return **nobody reads**, inside a `log` or `die` argument → `|| true`,
  "the honest annotation" (`bundle.sh:107`, `image-scan.sh:95,100,105,123`,
  `os-deps.sh:27`)

| line | remedy | why that one |
|---|---|---|
| `:298` `pool-info` readback | variable | it is the readback `:296` says goes through a variable |
| `:299` `pool-list`, `:300` `pool-dumpxml` | `\|\| true` | diagnostics on the death path |
| `:320` `net-info` readback | variable | `:318-319` says so in as many words |
| `:321` `net-dumpxml` | `\|\| true` | `:322` beside it already carried it |
| `:427` `domstate`, `:429` `dominfo` | variable | matches `:387`'s `xml=` in the same function |
| `:448` `id -u` | variable, **no** `\|\| true` | see below |

**`:448` is the one with correctness content, and `|| true` would be wrong
there** — an empty string reaching a numeric test is the defect, not the cure.

**Rejected:** dropping the `-o` flag. `183b927`'s body records that each of the
four was measured at 0 findings before being enabled, and that
`require-variable-braces` was deliberately left out so the real findings would
not be buried. Turning a gate off to make a script pass is the failure mode
`conftest.py:7` names.

## 6. Surface cost

One file, +20/−12. Four `local` declarations extended, five `|| true`, three
readbacks moved to variables, one four-line comment on the only site with
correctness content. No new function, no new file, no gate change.

## 7. The failing test

The gate is the test. Proved it still has teeth two ways:

* A scratch script containing `echo "masked: $(false | cat)"` fed to
  `shellcheck -o check-extra-masked-returns` → exit 1.
* Reverting one of the five `|| true` annotations in the patched file →
  `FAIL shellcheck`, `error: 1 gate(s) failed`. Restoring it → `ok shellcheck`.

## 8. Verification

Behaviour compared against `origin/master` under a stubbed `virsh`, using a
sourceable copy with the trailing `main "$@"` removed.

`storage_pool` and `default_network`, four scenarios each — start succeeds /
already-active / will-not-start / the readback itself fails:

```
start_ok      storage_pool     NEW rc=0  identical-output=YES
running       storage_pool     NEW rc=0  identical-output=YES
dead          storage_pool     NEW rc=1  identical-output=YES
probe_broken  storage_pool     NEW rc=1  identical-output=YES
start_ok      default_network  NEW rc=0  identical-output=YES
running       default_network  NEW rc=0  identical-output=YES
dead          default_network  NEW rc=1  identical-output=YES
probe_broken  default_network  NEW rc=1  identical-output=YES
```

8 of 8 byte-identical, and the death path still logs both diagnostics before the
`die` carries the reason.

`assert_domain`'s two changed assertions, domain up / shut off / gone: identical
verdicts and identical `fail` in all three, including `gone`, where virsh exits 1
and both versions produce two `FAIL` lines rather than aborting.

`:448` is the one behavioural **change**, and it is the point. With an `id` that
exits 1:

```
old  rc=0  id: broke | [: : integer expression expected | GUARD: already root | REACHED THE REST OF main
new  rc=1  id: broke
```

Master skips the sudo re-exec and carries on, so every later
`virsh -c qemu:///system` fails against root's socket for a reason nothing names.

`just check` on the patched tree: all six lint gates ok, `ty` clean,
`411 passed, 25 skipped`.

## 9. Non-goals

* The smoke gate's own behaviour on a real libvirtd. It is unchanged by
  construction and CI's `smoke` job is the only thing that can say so — it was
  passing on the red runs and must still pass here.
* The merge-order race itself. Nothing here adds a `concurrency:` group or a
  merge queue; that is a pipeline decision, not this fix.
* The other seven optional checks, and `require-variable-braces` in particular.
