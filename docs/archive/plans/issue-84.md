# Issue #84 — the static-gate hook never hashes `.toml`, `.tftest.hcl` or the `justfile`

Reverified at `aed962d` on branch `lane/agent-layer`. Raw transcript:
`docs/review/agent-layer/reverify/RX-F1.txt`.

All reproduction ran in a `git clone` of this worktree under
`/home/ssullivan/.claude/jobs/1e5fc8c0/tmp/L6`, with `.venv` and `.tools`
symlinked in so `ty`, `hadolint`, `tofu` and `shellcheck` resolve. The hook was
invoked exactly as `.claude/settings.json:8` invokes it — `CLAUDE_PROJECT_DIR`
set, a synthetic `Stop` payload on stdin. **The worktree's own
`.claude/hooks/static-gate.sh` was never edited**, because it is the Stop hook of
the session doing the work.

## 1. Reverification verdict

**Reproduced, all four mutations, plus the control and the negative.** Nothing in
the issue's mechanism failed to hold at `aed962d`.

Clean baseline: exit 0 in 3.616s cold, 0.034s warm,
`pass 098b6aa62df3a3457101940572eb2efe79faaa4b9ad833fc09fc189139a892eb`.
Then, each mutation with the recorded `pass` left byte-identical:

| mutation | real gate | hook |
|---|---|---|
| `pyproject.toml:124` line-length 88 → 40 | `lint.sh` exit 1 (2 gates: ruff check, ruff format) | **exit 0 in 0.044s** |
| `pyproject.toml:168` `[tool.ty.src] exclude = []` | `just typecheck` exit 1 | **exit 0 in 0.055s** |
| misformatted block in `tests/libvirt-module.tftest.hcl` | `tofu fmt -check` exit 3, `lint.sh` exit 1 | **exit 0 in 0.025s** |
| `justfile:55` → a script that does not exist | `just lint` exit 127 | **exit 0 in 0.035s** |

The signature is unchanged in all four. Not "recomputed and coincidentally
equal" — the files are not in the input set at all.

**The control is what makes the fix the alternation and not the cache.** Same
broken tree as mutation 1, `.cache/static-gate.state` removed:

```
exit=2  elapsed=4.544s  state=[block 098b6aa62df3a3457101940572eb2efe79faaa4b9ad833fc09fc189139a892eb]
```

`block 098b6aa6…` and `pass 098b6aa6…` — the same signature on a clean tree and
a broken one. `record pass` is doing exactly what `:24-29` documents. There is no
second defect in the cache.

The hook is therefore not blind in the absolute sense: on the first `Stop` of a
session with no state file it runs the gates and blocks. It is blind to a
*change confined to those files while a `pass` is already recorded*, which is the
normal state of any session past its first turn, since `.cache/` persists across
sessions.

## 2. Anchor table

All at `aed962d`, all re-read rather than carried over.

| anchor | state |
|---|---|
| `static-gate.sh:54` — `grep -zE '\.(py\|sh\|tf\|ya?ml)$\|(^\|/)Containerfile$'` | ok, verbatim |
| `static-gate.sh:52-59` — `signature()` | ok |
| `static-gate.sh:20-21` — "this hashes the contents of / the files the six gates actually read" | ok as cited, **and false** |
| `static-gate.sh:47-49` — "Every file the six gates read: …" | ok as cited, **and false** |
| `static-gate.sh:61-63` — "Failing open means running the gate, never skipping it" | ok as cited, **and TRUE** — see below |
| `static-gate.sh:31-33` — "signature 0.034/0.033/0.033s over **70 files**" | ok as cited, **and stale** — 71 at `aed962d`. #84 does not name this line |
| `scripts/lint.sh:126` `ruff check`, `:127` `ruff format`, `:150` `tofu fmt -check -recursive` | ok, all three |
| `justfile:63` `.venv/bin/ty check` | ok |
| `.github/workflows/ci.yml:52`, `.gitlab-ci.yml:50` — `just check` | ok, both |
| `.claude/settings.json:8` — how the hook is invoked | ok |

**`:61-63` is true, and I measured it rather than reading it.** With a `pass`
already recorded and a `git` on `PATH` that exits 1, `cur` is empty, so the
`[ -n "$cur" ]` guard at `:66` fails and the hook falls through to the gates:

```
$ PATH=<failing git> hook -> exit=0 elapsed=4.000s
```

4.0s, not the 0.03s a short-circuit would take. The claim is scoped to the
empty-`cur` path and that path behaves as described. **The comments the fix must
correct are `:20-21`, `:47-49`, and — not named by #84 — `:31-33`.**

## 3. Corrections to the issue body

**C1 — the file count is 79 at the review pin, not 78, and 112 at `aed962d`.**

```
aed962d : 183 tracked, 71 inside the signature, 112 outside
672a500 : 149 tracked, 70 inside, 79 outside
```

`70` matches the hook's own `:31-33` comment and the issue. `78` does not; the
complement at `672a500` is 79. The jump 79 → 112 is `b58f924`, which committed
`docs/review/2026-08-31/` — 32 `.md` and `.txt` files, none of which any gate
reads. **The count is not the interesting number and the fix does not turn on
it**; it is quoted in the issue's Scope section, so it should be right.

**C2 — "four matter" is four gate-reasons across three files.**

| file | gate that reads it | reproduced |
|---|---|---|
| `pyproject.toml` `:123-164` | `ruff check`, `ruff format` (`lint.sh:126-127`) | 1a |
| `pyproject.toml` `:166-171` | `ty check` (`justfile:63`) | 1b |
| `tests/libvirt-module.tftest.hcl` | `tofu fmt -check -recursive` (`lint.sh:150`) | 1c |
| `justfile` | none reads it — but it is how the hook runs both gates | 1d |

Three distinct paths. The corrected alternation adds exactly those three files
and nothing else (71 → 74, measured).

**C3 — `:31-33` is a third false comment line, and #84 names only two.** It
records "over 70 files"; the count is 71 at `aed962d` and becomes 74 under the
fix. A commit that corrects `:20-21` and `:47-49` and leaves `:31-33` at 70 has
left the same class of defect in the same header.

**C4 — the frequency claim holds exactly.** Over `4eb378b..672a500` (21 commits),
5 touch a blind-spot file a gate reads: `672a500`, `e4371ff`, `491d465`,
`950ca7e`, `282cb7b` — `pyproject.toml` 3, `justfile` 2,
`tests/libvirt-module.tftest.hcl` 1. `672a500` is itself a `justfile` +
`pyproject.toml` commit (`justfile | 27 +++--`, `pyproject.toml | 11 ++-`).

**C5 — the `docs/provider-0.9.8.lock.hcl` measurement is correct. I re-ran it
rather than inheriting it.** A deliberately misformatted `provider` block
appended to the lock file:

```
$ tofu fmt -check -recursive . ; echo $?
exit=0
$ tofu fmt -check docs/ ; echo $?
exit=0
$ ./scripts/lint.sh >/dev/null ; echo $?
lint.sh exit=0
```

`tofu fmt` handles `.tf`, `.tfvars`, `.tofu` and `.tftest.hcl`, not lock files.
So `\.(toml|hcl)$` — the finder's original proposal — over-includes by exactly
one file, and `#84`'s `\.toml$|\.tftest\.hcl$|(^|/)justfile$` is right.

## 4. The defect

`signature()` (`:52-59`) enumerates through `git ls-files` and then filters with
`grep -zE '\.(py|sh|tf|ya?ml)$|(^|/)Containerfile$'`. That set is `#46`'s written
spec plus `ya?ml`, and it is a set of files gates *operate on*. It is not the set
of files gates *read*, and three files are on the second list and not the first:

* `pyproject.toml` carries `[tool.ruff]`, `[tool.ruff.lint.per-file-ignores]` and
  `[tool.ty.*]` at `:123-171` — the configuration of two of the six gates.
* `tests/libvirt-module.tftest.hcl` is inside `tofu fmt -check -recursive`'s
  scope, which is `.tf`, `.tfvars`, `.tofu` **and** `.tftest.hcl`.
* the `justfile` is how the hook invokes `just lint` and `just typecheck` at
  `:97` and `:101`.

With a `pass` already recorded for the current signature, `:66-82` short-circuits
to `exit 0` before either gate runs. So an edit confined to one of those three
returns a pass in ~0.03s while `just lint` or `just typecheck` exits non-zero.

**The residue after CI is a comment-accuracy defect, and that is not a small
thing in this file.** `just check` runs in both pipelines and catches all four
reproductions from a clean checkout with no state file, so nothing here can reach
`master`. What survives is that `:20-21` and `:47-49` assert coverage the code
does not have, in a script whose reason for existing is `tests/conftest.py:7` —
"a gate that quietly passes because it did not run is worse than no gate." A
reader of those comments will believe a `pyproject.toml` turn was checked.

## 5. The fix

**One alternation and three comment blocks. Nothing else.**

`static-gate.sh:54`, from:

```
        | grep -zE '\.(py|sh|tf|ya?ml)$|(^|/)Containerfile$' \
```

to:

```
        | grep -zE '\.(py|sh|tf|ya?ml|toml)$|\.tftest\.hcl$|(^|/)(Containerfile|justfile)$' \
```

`#84` writes the addition as `\.toml$|\.tftest\.hcl$|(^|/)justfile$` appended to
the existing string. **Measured: the two spellings select the identical 74 files**,
over tracked and untracked both (`diff` of the two selections is empty). The
compact form above folds `toml` into the existing extension group and `justfile`
into the existing bare-name group, so it stays one alternation of two shapes
rather than growing to four. Either is correct; prefer the compact one because
this repo treats added surface as a defect.

**Do not write `\.(toml|hcl)$`.** It pulls in `docs/provider-0.9.8.lock.hcl`,
which C5 measured is not in any gate's scope — 75 files instead of 74, for a file
whose content no gate can object to.

Then the three comments:

* `:20-21` — "the files the six gates actually read" is the claim the mutations
  falsify. It becomes true once the fix lands, but only if it is read as
  including configuration. Say so: *"the files the six gates read **or are
  configured by**"*.
* `:47-49` — "Every file the six gates read: ruff and ty over `*.py`, tofu fmt
  over `*.tf`, …". The enumeration is what is wrong, so extend the enumeration:
  ruff and ty over `*.py` **and `pyproject.toml`**, tofu fmt over `*.tf` **and
  `*.tftest.hcl`**, and the `justfile` that runs them.
* `:31-33` — "over 70 files" → 74. Not named by #84; see C3.

### Rejected

**R1 — changing the cache.** The control in §1 records `block 098b6aa6…` and
`pass 098b6aa6…` on the same signature. The cache is faithfully reporting a
verdict about an input set that is wrong. Touching `record`/`STATE` treats the
mechanism and leaves the cause.

**R2 — hashing every tracked file.** Removes the filter entirely, so `:50-51`'s
"enumerated through git so `.venv/`, `.cache/` and `.tools/` fall out of scope
via `.gitignore` rather than via a second list" stops being the design. It also
makes every `docs/**.md` edit — the most common non-code turn in this repo —
invalidate the signature and pay 3.5s. The 112 files outside are outside for a
reason; three of them are wrong.

**R3 — a second list of "config files a gate reads."** `:50-51` explicitly
rejects a second list because it drifts from the first. The fix must stay inside
the one regex.

## 6. Surface cost

One line changed in one file, plus three comment edits in the same file's header.
No new function, no new file, no new list, no new gate, no test file.

Measured cost at runtime: **none.** Signature timing, three runs each:

```
current  : 0.031s 0.037s 0.031s
proposed : 0.032s 0.034s 0.040s
```

Three more `sha256sum` inputs on a 74-file list is inside the noise. The real
cost is that a turn touching one of those three files now pays the ~3.5s gate run
it should always have paid — measured 5 times in 21 commits, so roughly a quarter
of commits and only the subset of those that break a gate.

## 7. The failing test

**The four-mutation harness is the test, and it is a real one: it fails before
the fix and passes after, on the same trees.**

Nothing in `tests/` references `static-gate.sh`; the only gate over the hook is
`shellcheck` (`lint.sh:188` globs `.claude/hooks/*.sh`), and shellcheck cannot
see this defect. So the falsifiable claim has to be made by running the hook.

Patched hook (`static-gate-FIXED.sh`), each mutation applied to a tree whose
recorded state is a fresh `pass 4cae18dd…`:

```
1a: exit=2  elapsed=3.993s  state=[block 881ae00a…]  stderr: just lint failed:
1b: exit=2  elapsed=4.066s  state=[block d497a6b7…]  stderr: just typecheck failed:
1c: exit=2  elapsed=3.930s  state=[block 04125d61…]  stderr: just lint failed:
1d: exit=2  elapsed=0.059s  state=[block dee90b51…]  stderr: just lint failed:
```

Four for four, exit 0 → exit 2, and the signature now moves on every mutation
(four distinct `block` hashes where the unpatched hook produced one unchanged
`pass`). `1d` blocks in 0.059s because `just lint` itself dies at 127 immediately
— the block is still correct.

Regression arm, clean tree, patched hook:

```
cold: exit=0  elapsed=3.380s  state=[pass 4cae18dd…]
warm: exit=0  elapsed=0.038s
```

No false positive, and the warm path is still ~0.04s.

**A pytest was considered and is not proposed.** The only shape that would work
is one that greps the alternation out of the shell script and asserts three
filenames match it — a second copy of the same list, inside the repo, which is
the thing `:50-51` argues against. If the maintainer wants a standing check, the
one with content is *"every path `lint.sh` and `justfile` pass to a gate is
inside the signature"*, and deriving that mechanically from the shell is more
machinery than the defect warrants.

## 8. Verification

**The patched hook still passes the gate that lints it.** With each candidate
installed at `.claude/hooks/static-gate.sh` in the scratch clone and
`shellcheck` run with `lint.sh:183-188`'s exact flag set
(`-x -s bash -o check-extra-masked-returns -o check-unassigned-uppercase
-o quote-safe-variables -o avoid-nullary-conditions`):

```
static-gate-FIXED   : shellcheck exit=0   ./scripts/lint.sh exit=0
static-gate-COMBINED: shellcheck exit=0   ./scripts/lint.sh exit=0
```

`COMBINED` is the alternation fix plus #88's RX-F7 guard, checked together
because both land in this file and the lane may commit them as one change.

Baseline at `aed962d`, re-measured on the clean scratch clone rather than taken
on trust: six lint gates ok, `ty` clean, **411 passed, 25 skipped** in 37.09s,
`just check` exit 0 in 42.3s.

Scratch clone left clean — `git status --porcelain` shows only the `.tools` and
`.venv` symlinks the harness added.

## 9. Non-goals

* **The cache.** `record pass` and the block-once design at `:24-29` behave as
  documented; see R1.
* **`docs/provider-0.9.8.lock.hcl`.** Measured out of scope in C5. Nothing in
  this fix should mention `.hcl` except as `\.tftest\.hcl$`.
* **RX-F7's `record block` guard.** Same file, different issue — it belongs to
  #88 and is planned there. If both land in one commit, §8 already shows the
  combined patch passing shellcheck and `lint.sh`.
* **Making the hook run `just check`.** `docs/research/tooling-2026-08-30.md` §5.3
  rejected it on cost (~24s suite, two tests shelling out to real `tofu`) and
  `:31-33` records the rejection. Nothing here reopens it.
* **The 112-vs-79 file count.** Corrected in C1 because the issue quotes it; the
  fix does not turn on it and no work follows from it.
* **`#57`.** This closes its PARTIAL half. `#57` argued a different axis — which
  *tool* wrote the file — and that argument is sound on its own axis.
