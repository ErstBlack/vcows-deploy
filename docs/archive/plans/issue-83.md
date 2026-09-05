# Issue #83 — the scan's "did not read this image" guard fires only at 100% loss

Reverified at `aed962d`. Raw output: `docs/review/scan-bundle/reverify/RX-E5.txt`.

## 1. Reverification verdict

**Reproduced, unchanged from the review's `672a500` pin.** `scripts/image-scan.sh:134` is
byte-identical at both commits.

The guard's whole response curve, driven through the unmodified script at `aed962d` against the
committed 100-id baseline, with fake `podman`/`trivy`/`syft` in a scratch `.tools/bin`:

```
present   gone     EXIT   last log line
100       0        0      no findings outside the baseline
99        1        0      no findings outside the baseline
90        10       0      no findings outside the baseline
75        25       0      no findings outside the baseline
55        45       0      no findings outside the baseline
44        56       0      no findings outside the baseline
25        75       0      no findings outside the baseline
5         95       0      no findings outside the baseline
1         99       0      no findings outside the baseline
0         100      1      error: none of the 100 accepted findings are present -- the scan did not read this image
```

One row out of twenty is red, and it is the row that cannot happen for any reason other than an
empty report — which `scan_floor` (`:56-64`) already refuses.

The important half is not the synthetic sweep. Driven from the **real** Phase 0 `trivy.json`
(`sha256 f690d0e1…`) with one analyser's results emptied and nothing else changed:

```
--- ALL     : os-pkgs=60 lang-pkgs=39 lang-pkgs=21 ---
baseline entries no longer found (1 of 100; stale, or fixed by a pin bump):
no findings outside the baseline
EXIT=0

--- OS_ONLY : os-pkgs=60 lang-pkgs=0  lang-pkgs=0  ---   (trivy stopped walking Go binaries)
baseline entries no longer found (45 of 100; stale, or fixed by a pin bump):
no findings outside the baseline
EXIT=0

--- GO_ONLY : os-pkgs=0  lang-pkgs=39 lang-pkgs=21 ---   (trivy stopped reading the rocky layer)
baseline entries no longer found (56 of 100; stale, or fixed by a pin bump):
no findings outside the baseline
EXIT=0
```

Both scanner regressions the guard exists to catch pass green today.

## 2. Anchor table

Every line re-read at `aed962d`. `scripts/image-scan.sh` **did** change between `672a500` and
`aed962d` — four `|| true` annotations for SC2312 at `:95`, `:100`, `:105`, `:123`. All four are
in-place substitutions on existing lines, so the file's line count and every anchor below are
unmoved. `git diff --stat 672a500 aed962d -- scripts/image-scan.sh` → `8 ++++----`, 4 insertions,
4 deletions.

| anchor | 672a500 | aed962d | state |
|---|---|---|---|
| `image-scan.sh:134` the guard | `:134` | `:134` | **identical text**, `[ "$accepted" -gt 0 ] && [ "$missing" -eq "$accepted" ]` |
| `image-scan.sh:126-130` the comment reasoning in proportions | `:126-130` | `:126-130` | unchanged |
| `image-scan.sh:131-133` `gone` / `accepted` / `missing` | same | same | unchanged |
| `image-scan.sh:135` the `die` message | same | same | unchanged; says "none of the $accepted" |
| `image-scan.sh:137-140` the non-fatal `gone:` log | same | same | unchanged |
| `image-scan.sh:56-64` `scan_floor` | same | same | unchanged; still 3 Results / 456 packages |
| `image-scan.sh:113-117` the one-read `delta` jq | same | same | unchanged |
| `image-scan.sh:119-124` the `new` check, which runs **first** | `:123` gained `\|\| true` | same lines | behaviourally unchanged |
| `docs/cve-baseline.json:4` the note, `:48` `accepted` | same | same | file byte-identical across the range |

`jq '.accepted | length' docs/cve-baseline.json` → `100` at `aed962d`.

## 3. Corrections to the issue body

**The issue body is accurate.** Its measured block, its "the equality at `:134` is the only gap",
and its reachability paragraph all reproduce.

**One correction, and it is to the proposed fix, not to the issue.** Both
`finders/E-build-pipeline.md` and `verify/E-mediums.md` §RX-E5 name `missing * 2 -gt accepted` as
the remedy ("or any proportion"). Measured, that threshold first fires at **gone = 51 of 100**, so
it does **not** catch the loss the same verify document names as the realistic trigger:

```
===== candidate N2 =====  if [ "$accepted" -gt 0 ] && [ $((missing * 2)) -gt "$accepted" ]; then
    first FAIL at gone=51 of 100
    real ALL      EXIT=0  no findings outside the baseline
    real OS_ONLY  EXIT=0  no findings outside the baseline      <-- 45 gone, still green
    real GO_ONLY  EXIT=1  error: none of the 100 accepted findings are present …
```

RX-E5's own sentence — "a trivy release that stops walking Go binaries … drops ~45 at once" — is
right about the number and wrong that halving catches it. 45 is below half. Picking the threshold
by feel is what produced that, which is why section 5 picks it from the table.

**A second correction, to the fix's scope.** The issue calls this a one-character-class change. The
`if` is one line, but `:135`'s message ("none of the `$accepted` accepted findings are present")
becomes false the moment the test is proportional, and `:126-130`'s comment describes equality
("*All* of them disappearing at once"). A one-line change leaves the file asserting two things that
are no longer true. This is the same defect class as `lib.sh:88-93` in #90.

## 4. The defect

`:134` reads `[ "$accepted" -gt 0 ] && [ "$missing" -eq "$accepted" ]`. The comment above it
(`:126-130`) argues in proportions — "One or two accepted ids disappearing is ordinary … *All* of
them disappearing at once is not a clean image" — and the code implements only the endpoint of that
argument. There is no expression of "most", so the interval `1 ≤ missing ≤ accepted-1` is entirely
inside the ordinary branch.

Why that interval is where the real failure lives: trivy's report is a list of `Results`, one per
**analyser × target**. This image has exactly three, and they split into two analyser families:

| analyser family | targets | distinct ids | share of the 100-id baseline |
|---|---|---|---|
| `os-pkgs` (rocky 10.2) | `image.tar` | 55 | 55% |
| `lang-pkgs` (`gobinary`) | `terraform-provider-libvirt_v0.9.8`, `usr/bin/tofu` | 44 (37 + 18 − 11 shared) | 44% |

Measured with `jq` over the real report: `rocky ∩ provider = 0`, `rocky ∩ tofu = 0`,
`provider ∩ tofu = 11`. The two families are disjoint. So a scanner that loses **either one**
family loses a large but strictly partial slice — never the whole set, never zero. Losing both is
the only path to `missing == accepted`, and that case is already caught twice over by `scan_floor`
refusing a report with no `Results`.

The guard is therefore positioned exactly where nothing that can realistically go wrong will land.
`scan_floor` catches "the report is structurally empty"; `:134` catches "the report is about a
different image entirely". Neither catches "the report is about this image but half-read", which is
the mode a trivy upgrade actually produces.

Consequence is this repo's S1 shape, stated in RX-E5 and re-confirmed: the exit code is 0, `just
bundle` runs next in both pipelines (`.github/workflows/image.yml:79-80`, `.gitlab-ci.yml:160-161`
and `:190-191`), and the delivery bundle ships a `trivy.json` that reports the image as materially
cleaner than it is. The 45-line `gone:` wall goes to stderr, where in CI it is a green job's log.

## 5. The fix

**`[ "$missing" -eq "$accepted" ]` → `[ $((missing * 3)) -gt "$accepted" ]`, plus the two things
that stop being true when it changes.**

### Choosing the number from the table, not from taste

Two quantities bound the threshold. Both are measured, not assumed.

**Floor — the largest loss that must stay green.** A real scan of the current image today reports
`gone = 1 of 100` (`CVE-2026-58055`, and `new` is empty). That is the entire observed legitimate
loss in this repo's history. The `note` at `docs/cve-baseline.json:4` calls one or two disappearing
"ordinary", and `image-scan.sh:126-127` says the same.

**Ceiling — the smallest loss that must go red.** Every analyser-loss combination, measured against
the committed baseline from the real report:

| what stops being read | ids still found | gone of 100 |
|---|---|---|
| nothing (today) | 99 | **1** |
| the `gobinary` analyser (provider + tofu) | 55 | **45** |
| the `os-pkgs` analyser (rocky) | 44 | **56** |
| both | 0 | 100 |

45 is the ceiling. The threshold has to fire strictly below 45 and stay quiet at 1.

Candidates, each patched into a scratch copy and run against both the synthetic sweep and the three
real-report scenarios:

| candidate | first fires at | catches gone=45? | catches gone=56? | headroom above today's 1 |
|---|---|---|---|---|
| `missing -eq accepted` (today) | 100 | no | no | — |
| `missing * 2 -gt accepted` (the finder's) | 51 | **no** | yes | 50 |
| **`missing * 3 -gt accepted`** | **34** | **yes** | **yes** | **33** |
| `missing * 4 -gt accepted` | 26 | yes | yes | 25 |
| `missing * 10 -gt accepted` | 11 | yes | yes | 10 |

`* 3` is the pick: it is the loosest candidate that clears the measured ceiling, with 11 ids of
margin below 45, and it leaves 33 ids of headroom — thirty-three times the largest legitimate loss
this repo has ever recorded. `* 4` and `* 10` also work and buy nothing: the failure they would
additionally catch is a 26-to-33-id loss, which no analyser boundary in this image produces, while
the headroom they spend is real. A future base re-pin that clears a run of rocky rows is the
plausible large *legitimate* loss, and `* 3` survives one clearing a third of the baseline.

Multiplication rather than `[ "$missing" -gt $((accepted / 3)) ]`: integer division makes the
firing point depend on how `accepted` rounds, and the point of this change is that the number is
chosen rather than inherited.

The expression is proportional, so it re-scales when the baseline is trimmed. At the 99 ids the
`CVE-2026-58055` deletion in #90 leaves, it first fires at 33.

### The two things that must change with it

* **`:135`, the message.** "none of the `$accepted` accepted findings are present" is false under
  any proportional test. It has to name what happened:
  `die "$missing of $accepted accepted findings are absent -- more than a third of the baseline vanished at once. That is a scan that did not read this image, not a clean one."`
* **`:126-130`, the comment.** It currently argues to the endpoint ("*All* of them disappearing at
  once"). It should state the measured reason for the number: the two analyser families are
  disjoint at 55 and 44 ids, so losing either produces a large partial loss and never a total one.

`shellcheck -x -s bash` with all four optional checks `lint.sh:183-187` enables is clean on the
patched line — measured for all four candidates, `EXIT=0` each time.

### Rejected

* **`missing * 2 -gt accepted`** — the finder's own proposal. Measured to miss the 45-of-100 case,
  which is the case the finder's reachability paragraph is about. This is the whole reason the
  threshold needed a table.
* **An absolute cap (`missing -gt 10`).** Does not re-scale. The baseline is already going from 100
  to 99 in #90, and a provider bump changes it by ~37. A constant would have to be revisited on
  every baseline edit, and nothing would remind anyone.
* **Deriving the expected count per `Results` entry** — asserting the report still carries three
  targets, or that each analyser family contributes at least one id. Strictly more precise: it would
  catch a single Go binary vanishing (8 ids for `usr/bin/tofu` alone, below any workable threshold).
  Rejected on surface: it needs the id-to-group mapping the file does not have, which is exactly
  RX-E8, downgraded to a nit and explicitly left alone because the fix is "new schema plus new
  coupling for a lookup that has been needed once."
* **Failing on any `gone` at all.** Turns the routine stale row into a red gate and makes the scan
  non-differential, which is the always-red-then-muted failure `image-scan.sh:7-13` exists to avoid.

### Residual, stated rather than hidden

A single Go binary dropping out of the report — `usr/bin/tofu` alone is 8 ids, the provider alone is
27 — stays below `* 3`'s firing point. No threshold catches the 8 without going below the ordinary
range. That is not this guard's job: a binary missing from the image is an image change, and
`test_image.py` asserts the image's own tofu version.

## 6. Surface cost

One file, one hunk. The `if` condition, the `die` string, and the four-line comment above them. No
new function, no new file, no new gate, no new dependency, no change to `scan_floor`, to the `new`
check, or to `--write-baseline`. Roughly +6/−6.

## 7. The failing test

**Nothing in the suite executes `scripts/*.sh`.** Measured: `grep -rn "scripts/" tests/
--include=*.py` returns three hits in two files. `conftest.py:84` is a message string.
`test_image.py:266` is a docstring. `test_image.py:274` is the only one that runs anything —
`subprocess.run(["bash", "-c", f"source {REPO}/scripts/lib.sh && source_revision"])` — and it sits
inside a module gated on `image`, so the default `pytest -q` never reaches it. `scripts/` is
otherwise covered by `shellcheck` alone, which checks syntax and masked returns and cannot see a
threshold.

**What that costs, concretely.** This defect and #79 are both behavioural defects in shell that the
411-test suite is structurally unable to express. Both were found by hand-built fake-binary
harnesses, and both would silently regress: reverting `* 3` back to `-eq` passes `just check`.
`CLAUDE.md`'s gate-discipline section calls a gate that never runs a first-class defect; this is the
adjacent case, a script with no gate at all.

**Proposed: `tests/test_scripts.py`, one new file.**

The injection point already exists and needs no PATH manipulation from the test: `lib.sh:26`
prepends `$REPO/.tools/bin` to `PATH`, and `$REPO` is derived from `BASH_SOURCE`. So a test that
copies `scripts/` into `tmp_path` and writes fake `podman`/`trivy`/`syft` into
`tmp_path/.tools/bin` gets the fakes chosen by the script's own mechanism. That is exactly how both
harnesses in this lane worked.

```
def test_a_report_missing_a_third_of_the_baseline_is_not_a_clean_scan(tmp_path):
    repo = _shell_repo(tmp_path)          # scripts/, Containerfile ARG, docs/cve-baseline.json
    _fake_scanners(repo, ids=first(67))   # 33 of 100 gone -- below the threshold
    assert _run(repo).returncode == 0
    _fake_scanners(repo, ids=first(66))   # 34 of 100 gone
    r = _run(repo)
    assert r.returncode == 1
    assert "did not read this image" in r.stderr
```

Both boundary rows, from the table in section 5, on a synthetic 100-id baseline written into
`tmp_path` so the test does not move when `docs/cve-baseline.json` is trimmed.

**No new `VCOWS_GATES` name, and no skip.** `tests/test_gates.py:28` holds `KNOWN = {"tofu",
"image", "rig", "pycdlib", "libvirt"}` as a closed set and AST-walks the suite for bare
`pytest.skip` / `importorskip` / `mark.skip`. The proposed test needs `bash` and `jq` and nothing
else — no podman, no image, no 444 MB archive, no network. `jq` is installed by
`scripts/os-deps.sh:30,32`, which every job that runs the suite executes first
(`.github/workflows/ci.yml:49`, `.gitlab-ci.yml:36-38`). So the test runs unconditionally and needs
no gate at all. If `jq` is absent the test fails, and that failure is accurate.

Cost: about 60 lines including a `_shell_repo` fixture, most of which #79 needs too. If both issues
land together the fixture is written once. **`_shell_repo` belongs in the new test file, not in
`conftest.py`** — `conftest.py` is the gate mechanism and adding a shell-rig helper to it widens the
file `test_gates.py` treats as the one privileged implementation.

## 8. Verification

For the implementer. Nothing below has been run against a fix, because this plan changes nothing.

1. `just check` — expect the recorded `aed962d` baseline: six lint gates ok, `ty` clean,
   `411 passed, 25 skipped`, plus the new test. Confirmed green at `aed962d` today, unpatched.
2. Re-run the sweep in `RX-E5.txt` §3 against the patched script. Expect the first `EXIT=1` to move
   from `gone=100` to `gone=34`.
3. Re-run the three real-report scenarios in `RX-E5.txt` §4. Expect `ALL` green, `OS_ONLY` and
   `GO_ONLY` both red — the two rows that are the point of the change.
4. `shellcheck -x -s bash -o check-extra-masked-returns -o check-unassigned-uppercase -o
   quote-safe-variables -o avoid-nullary-conditions scripts/image-scan.sh` → `EXIT=0`. Already
   measured clean on the patched line.
5. Revert the `* 3` to `-eq` and confirm the new test goes red. A threshold test that passes both
   ways is worse than none.
6. Do **not** run `scripts/image-scan.sh --write-baseline` at any point. It regenerates
   `docs/cve-baseline.json` with only `image`, `generated`, `note` and `accepted`, destroying every
   `why` and `recheck`.

## 9. Non-goals

* Trimming `CVE-2026-58055`, and the `note`'s arithmetic. Both are #90 and both are planned in
  `docs/archive/plans/issue-90-cve-baseline.md`. They interact only in that a 99-id baseline moves the
  firing point from 34 to 33, which is the expression working as intended.
* `scan_floor`'s structural floors (`:56-64`). Re-confirmed working; they are the other half of the
  same defence and need nothing.
* Giving `accepted` group membership (RX-E8). Already downgraded to a nit and left alone.
* `bundle.sh` shipping what a failed scan left behind. That is #79.
