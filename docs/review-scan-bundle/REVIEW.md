# Scoped review — lane `scan-bundle`

Input: `git diff origin/master...lane/scan-bundle` and nothing else. Merge base
`ea0932d`, head `49d0abf`. Three fix commits, four files, `+319 / −6`.

```
 docs/cve-baseline.json |   3 +-
 scripts/bundle.sh      |  26 ++++++
 scripts/image-scan.sh  |  52 ++++++++++-
 tests/test_scripts.py  | 244 ++++++++++++++++++++++++++++++++++++++++++++++++-
```

| commit | | `image-scan.sh` | `bundle.sh` | `cve-baseline.json` | `test_scripts.py` |
|---|---|---|---|---|---|
| `c9171d5` | `#83`, the threshold | +17 −3 | — | — | +96 −1 |
| `0db8bc5` | `#79`, the verdict file | +26 | +26 | — | +153 −6 |
| `49d0abf` | issue 90's two baseline items | +6 | — | +1 −2 | +3 −3 |

Plans under review: `docs/plans/issue-83.md`, `docs/plans/issue-79.md`,
`docs/plans/issue-90-cve-baseline.md`. Raw evidence they rest on:
`docs/review-scan-bundle/reverify/RX-E5.txt`, `RX-E6.txt`.

`origin/master` advanced to `e555fe9` during this lane (the CLI-records lane,
PR #102). It touches `orchestrator/`, `tests/test_cli.py` and
`tests/test_libvirt_schema.py` and none of this lane's four files, so the merge
base is unmoved and this diff is unaffected.

The fourth commit on this branch carries this file, the three plans and the two
transcripts. It changes no code and is not reviewed here.

---

## Lens 1 — did it do what the plan said?

Yes, for all three, with two arithmetic corrections to `issue-83.md` and one
verification step deliberately not performed.

### `#83` — `docs/plans/issue-83.md` §5

Landed verbatim: `[ "$missing" -eq "$accepted" ]` → `[ $((missing * 3)) -gt
"$accepted" ]`, the `die` string §5 specifies word for word, and the comment
rewritten to carry the disjoint-families measurement the number is derived from.
The plan's three-part scope claim held — a one-line change would have left `:135`
and `:126-130` both asserting equality.

**C1 — `issue-83.md` §5 is wrong by one about the trimmed baseline.** It says
"At the 99 ids the `CVE-2026-58055` deletion in #90 leaves, it first fires at
33." Measured on the 99-id file: green at `gone=33`, red at `gone=34`. `-gt`
makes `33 * 3 = 99` not greater than 99, so the firing point does not move at
all. Corrected in the shipped comment and in `49d0abf`'s body, not only here.

**C2 — the two partial-loss numbers move with the trim, and the plan does not
say so.** `CVE-2026-58055` was a rocky id, so it came out of *both* partial
losses. At 99 ids the same three real-report scenarios are `gone=0`, `gone=44`
and `gone=55`, not `1`, `45` and `56`. Both are still red, with 10 and 21 ids of
margin. Measured, and recorded in the comment.

**C3 — §6's "+6/−6" is short.** Actual for `#83` alone: `image-scan.sh +17 −3`.
The extra is the comment, which §5 requires and §6 does not count.

Two corrections the plan made to the review and to `#83` reproduced
independently here. `missing * 2 -gt accepted` — the remedy both
`finders/E-build-pipeline.md` and `verify/E-mediums.md` §RX-E5 name — first fires
at `gone=51` and leaves the 45-of-100 gobinary loss green. That is now a test
row, not only an argument: substituting `* 2` fails the `[34]` and `[45]` cases.

### `#79` — `docs/plans/issue-79.md` §5

Landed exactly as shaped. `.cache/scan/PASSED` holding one line of `sha256sum
image.tar` output; cleared at `image-scan.sh:89`, immediately after
`out=`/`mkdir -p` and before `save_archive`; written at `:187` as the last act of
a passing run; two `|| die` clauses in `bundle.sh` at `:75-78`, inside the
precondition block.

Every "do not" in §5 was honoured and is verifiable in the diff:

* **`bundle.sh:84-91` is untouched.** The revision warning stays non-fatal, and
  the passing test asserts it still fires and still does not stop the bundle.
  §5's severity table is the reason, and it is now pinned by an assertion rather
  than by a comment.
* **The stamp is not folded into `:119`'s `image.tar.sha256`.** Two `sha256sum`
  calls over 444 MB, 2.0s each, deliberately.
* **No timestamp, tag or `generated` field in the stamp.** One line, one format.
* No `bundle: scan` in the `justfile`, no re-scan inside `bundle.sh`, no deletion
  of `.cache/scan` on failure, no vcows key inside `trivy.json`.

§8's verification ran in full except step 4 — see **F4**. Step 3's byte-level
assertion is the strongest single result in this lane: the delivery `.tar.gz`
came out at `d6250b3f32c6f7b32b31572b7df2b0d3b834e819fb3424c29179b04d966ee7bc`,
identical to both transcript runs, so the fix changed nothing on the passing
path. `image.tar.sha256` also hashes to `0363778822…` as before, so the stamp
did not reshape the delivery artefact.

### Issue 90's two items — `docs/plans/issue-90-cve-baseline.md` §5

Both hunks landed as written; the replacement sentence is §5's proposed text word
for word. `rationale` is untouched — `jq -S '.rationale'` is byte-identical
across the edit — `generated` is unchanged, and `rationale["rocky-base"]`'s
"55 findings, 44 HIGH" was left alone for the reason §5 gives and is measured
correct against the post-removal live set.

All three of the plan's arithmetic claims reproduced against a fresh real trivy
run, this time of the **`672a500` podman image** rather than the `bbd96ba`
archive RX-E5 used — a different artefact, identical arithmetic:

```
per-target  rocky=55  provider=37  tofu=18   sum=110
union=99   shared=11   rocky^provider=0   rocky^tofu=0   provider^tofu=11
delta vs the trimmed file : {"accepted":99,"found":99,"new":[],"gone":[]}
delta vs the pre-edit file: {"accepted":100,"new":[],"gone":["CVE-2026-58055"]}
```

`just scan` end to end with real podman, trivy and syft: exit 0, 24s, and **no
`gone:` line at all** — the first clean differential this gate has reported.

`--write-baseline` was not run at any point, under any spelling. The two hunks
are hand-edits.

### Confirmed absent

Each named as a non-goal in one of the three plans' §9: no change to
`scan_floor` (`image-scan.sh:56-64`), to the `new` check, or to
`--write-baseline`; no `generated` bump; no new key, group or schema in
`docs/cve-baseline.json`; no group membership for `accepted` (RX-E8); no
`justfile` recipe; no `conftest.gate()` and no new `VCOWS_GATES` name; nothing
in `orchestrator/`, `scripts/lib.sh`, `scripts/lint.sh`,
`scripts/install-tools.sh` or `tests/conftest.py`; no rebuild of the image; no
signing; no `README.md` edit; and nothing touching RX-E10 or RX-E12.

---

## Lens 2 — do the new tests have teeth?

Nine new cases in `tests/test_scripts.py`, five of them one parametrized
function. Seven mutations against the committed tree, each run to completion.

| # | mutation | result |
|---|---|---|
| M1 | threshold back to `[ "$missing" -eq "$accepted" ]` | `3 failed, 4 passed` — the `[34]`, `[45]` and `[56]` rows |
| M2 | **the reviews' own remedy, `$((missing * 2))`** | `2 failed` — exactly `[34]` and `[45]` |
| M3 | harness broken: the fake report's ids under a key nothing reads | `5 failed` — all five rows, the vacuity guard |
| M4 | drop `bundle.sh`'s `[ -f "$scan/PASSED" ]` | `1 failed` — `..._refuses_an_archive_no_scan_has_accepted` |
| M5 | drop `bundle.sh`'s `sha256sum -c --status PASSED` | `1 failed` — `..._refuses_a_stamp_that_describes_a_different_archive` |
| M6 | drop `image-scan.sh:89`'s `rm -f "$out/PASSED"` | `1 failed` — "a failed scan left its verdict behind" |
| M7 | drop `image-scan.sh:187`'s stamp write | `1 failed` — the same lifecycle test |
| M8 | harness broken: the fake archive's revision label renamed | `1 failed` — only the passing test, which is its guard |
| — | none | `11 passed` |

M2 is the row that carries the whole `* 3`-over-`* 2` argument, and it is the
one that makes the parametrized list worth five cases rather than two. The
review documents' prescribed fix is now a thing the suite refuses, not a thing a
commit body argues against.

M3 and M8 are the vacuity guards, one per rig. Without them a fixture that
stopped producing the precondition would leave the defect tests green for the
wrong reason.

Visible from `just check`, not only from a targeted invocation — measured on the
whole suite:

```
M1 whole suite   3 failed, 422 passed, 25 skipped
M4 whole suite   1 failed, 424 passed, 25 skipped
clean            425 passed, 25 skipped
```

**What the tests do not cover, stated rather than implied.**

1. **`--write-baseline` writing no stamp.** §8 step 4. Asserted by the ordering
   of three lines and by a comment, and by nothing executable. See **F4**.
2. **The real analyser-loss scenarios.** The parametrized rows are synthetic ids
   on a synthetic 100-id baseline, chosen so the file does not move when
   `docs/cve-baseline.json` is trimmed — which is exactly what `49d0abf` then
   did, so the choice paid for itself inside one branch. The 45 and 56 rows are
   *modelled on* the real report; a change to trivy's `Results` shape would
   break the real scan and not these tests. `scan_floor` is the guard for that
   and is unchanged.
3. **The 444 MB path.** `_fake_archive` is a 2 KB tar. `gzip -9` over the real
   archive is 86-89s and is exercised by hand, not by the suite. The passing
   test does run a real `gzip`, `tar`, `sha256sum` and `git`.

---

## Lens 3 — what moved

`scripts/image-scan.sh` gains 15 lines after `:75` and 25 more inside the
threshold block. **A citation at or below `:75` is unaffected; `:76`-`:139`
shift +15; `:140` and below shift +35.** `scripts/bundle.sh` gains 26 lines after
`:53`: **at or below `:53` unaffected, `:54` and below shift +26.**

| what | `origin/master` | here |
|---|---|---|
| `image-scan.sh` `rm -f "$out"` in `save_archive` | `:34` | `:34` |
| `tag="$(image_tag)"` | `:70` | `:70` |
| `out="$REPO/.cache/scan"; mkdir -p` | `:71` | `:71` |
| **`rm -f "$out/PASSED"`** | — | **`:89`** |
| `save_archive "$tag" "$archive"` | `:77` | `:92` |
| `trivy` / `syft` | `:80-81` | `:95-96` |
| `scan_floor "$report" "$sbom"` | `:87` | `:102` |
| `--write-baseline` flag test | `:93` | `:108` |
| its `return` | `:101` | `:116` |
| the one-read `delta` jq | `:113-117` | `:128-132` |
| the `new` check | `:119-124` | `:134-139` |
| the `gone` comment | `:126-130` | `:141-160` |
| **the threshold `if`** | `:134` | **`:169`** |
| the `die` it guards | `:135` | `:170` |
| the non-fatal `gone:` log | `:137-140` | `:172-175` |
| `log "no findings outside the baseline"` | `:141` | `:176` |
| **the `PASSED` write** | — | **`:187`** |
| `bundle.sh` `archive_label()` | `:32-39` | `:32-39` |
| the `[ -f "$f" ]` loop | `:50-52` | `:50-52` |
| **the two `PASSED` checks** | — | **`:75-78`** |
| `archive_label` calls → `name` | `:54-56` | `:80-82` |
| the non-fatal revision warning | `:58-65` | `:84-91` |
| `out="$REPO/.cache/delivery"` | `:67` | `:93` |
| `gzip -9 -n` | `:88` | `:114` |
| `sha256sum image.tar` → `image.tar.sha256` | `:93` | `:119` |
| `SHA256SUMS` | `:102-103` | `:128-129` |
| `du -h … \| cut -f1` | `:107` | `:133` |

**Who points at those numbers.** `grep -rn 'image-scan\.sh:[0-9]\|bundle\.sh:[0-9]'`
over the whole tree, excluding this lane's own plans and transcripts, returns
hits in three kinds of place.

1. **`tests/test_scripts.py`** — the only live file whose citations this branch
   both breaks and can fix, since it is in the permitted set. Three moved with
   `49d0abf`'s six-line comment: the threshold `:148` → `:169` and the stamp
   write `:181` → `:187`, twice. `image-scan.sh:70`, `:128-132`, `bundle.sh:32-39`,
   `:50-52` and `:84-91` are unmoved and were re-checked, not assumed.
2. **Two live files outside the permitted set: `CLAUDE.md` and the two skills.**
   F1, F2 and F3 below.
3. **Archived evidence**, quoted-at-a-date by construction and not updated:
   `docs/review-2026-08-30/`, `docs/review-2026-08-31/`, `docs/review-agent-layer/`,
   `docs/review-shell-errexit/`, `docs/review-workflow-gate/`,
   `docs/tooling-2026-08-30.md`, and `docs/plans/issue-77.md`, `issue-88.md`,
   `issue-90-pipeline-comments.md`, `issue-92.md`. Roughly 60 hits, none of them
   a claim about the current tree.

`README.md` and the `justfile` name the two scripts but no line in either, and
their claims are unaffected — `just bundle` still "assembles the delivery bundle
from what `just scan` wrote", which is now truer than it was.

**One claim this branch makes true without editing it.**
`.claude/skills/delivery/SKILL.md:25`: "You cannot ship something that was not
scanned against `docs/cve-baseline.json`, which is the point." At `origin/master`
that is **false**, and RX-E6 is 380 lines of proof that it is false. Here it is
true. The same file's `:20-23` describes the precondition as three files and one
`die`, which is now four checks and two more `die`s — accurate as far as it goes,
incomplete. F3.

**One citation this branch creates, deliberately.** `bundle.sh:66-73` and
`image-scan.sh:77-88, 179-186` cite line numbers inside their own files
(`:114`, `:119`, `:85-90`, `:92`, `:95-96`, `:128`, `:116`). Same class as the
self-citation the `shell-errexit` lane accepted at `lib.sh:98`: correct as
landed, silently wrong after any insertion above them. Accepted for the same
reason — the comments have to name the ordering they depend on, and naming it by
wording would be vaguer than naming it by line. This lane's own experience is
the argument: `49d0abf` moved two of them and they were corrected in the same
commit that moved them, which is the maintenance this style costs.

---

## Ledger

### Raised

| | |
|---|---|
| **F1** | `CLAUDE.md:86` cites `scripts/image-scan.sh:92` for `--write-baseline`. It was already wrong at `origin/master` — `:92` was blank and the flag test was `:93`, recorded in both `verify/F-lows.md:24` and `finders/F-agent-layer.md:36`. Here `:92` is `save_archive "$tag" "$archive"` and the flag test is `:108`, so the citation is wrong in a new and more misleading way. Out of this lane's permitted file set. The claim it carries — that the flag destroys `rationale` — is unaffected and still true. |
| **F2** | `.claude/skills/cve-triage/SKILL.md:30` says "The 100 accepted IDs survive; every reason they were accepted does not." `49d0abf` makes it 99. One number, in a file corrected on master an hour ago by `8915cd7`. Out of the permitted set. |
| **F3** | `.claude/skills/delivery/SKILL.md:20-25` describes `bundle.sh`'s precondition as a check for three files with one `die` message. There are now four checks and two more `die`s, and the skill's "If the scan goes red, stop and use the `cve-triage` skill. Do not bundle around it." is now enforced by the script rather than by the reader. Its `:25` claim goes from false to true — see Lens 3. Out of the permitted set. |
| **F4** | **`--write-baseline` writing no stamp is asserted by nothing executable.** `issue-79.md` §8 step 4 asks for it. The briefing for this lane forbids running that flag absolutely, under any spelling or path, so the step was not performed and no test was written for it. What holds the property is the ordering of three lines — the `rm` at `:89`, the `return` at `:116`, the write at `:187` — and the comment at `:179-186` that names it. A future edit that moved the write above the `return` would authorise a bundle from a `--write-baseline` run and turn nothing red. Recorded so the gap is on the record rather than rediscovered. |
| **F5** | The `#79` fix adds a way for `just bundle` to fail that a site-facing document does not mention. `README.md:259-267` documents three commands and no `.cache/scan` internals, so nothing it says is now false, and adding the stamp to it would document an implementation detail to an audience that never sees it. No edit proposed; recorded because the new `die` message is the only place the mechanism is explained to a human. |

### Confirmed

* **`#83` reproduces and is closed.** The unmodified guard was green at every
  loss from 1 to 99 of 100 and red only at 100. Patched, the sweep's first red
  row moves to `gone=34`, and the two real analyser-loss scenarios driven from
  the real Phase 0 report go from `EXIT=0` to `EXIT=1` at 45 and 56 of 100.
* **`#79` reproduces and is closed, on the real artefacts.** With one finding
  injected into the real report: scan exits 1, `.cache/scan` holds three files
  and no stamp, `bundle.sh` exits 1 naming the missing stamp, `.cache/delivery`
  does not exist. With the finding removed: scan exits 0, `PASSED` holds
  `bff0f9cf46bd…  image.tar` — matching an independent `sha256sum` of the same
  file, and matching the digest RX-E6 recorded — and the bundle is byte-identical
  to both transcript runs. A second scan that dies after a passing one removes
  the earlier stamp; a stamp holding a wrong digest is refused by name.
* **CI is unaffected, re-verified at HEAD.** `just scan` and `just bundle` are
  consecutive steps of the same job at four sites, and the only cached path on
  either platform is `.cache/uv`, never `.cache/scan` — so the stamp is always
  written by the same job that reads it. `.cache/` is in both `.gitignore:30` and
  `.containerignore:10`, so `PASSED` reaches neither git nor the image.
* **The x/crypto acceptance is untouched and still sound.**
  `orchestrator/backends/libvirt/render.py:61` still emits
  `connection_uri(target, "sshcmd")`, which is what the group's `why` rests on.
* **The four real binaries under `/home/ssullivan/vcows-deploy/.tools/bin` are
  intact.** Digests recorded before any harness was built and re-checked after
  each of the two scratch clones. Both clones carried a freshly created real
  `.tools/bin`; the worktree's symlink was never written through.

### Refuted

| | |
|---|---|
| **The reviews' prescribed remedy for `#83`** | `missing * 2 -gt accepted`, in both `finders/E-build-pipeline.md` and `verify/E-mediums.md`. First fires at `gone=51` of 100 and leaves green the 45-of-100 loss the same document's reachability paragraph names as the realistic trigger. Now a failing test row, not an argument. |
| **`issue-83.md` §5's "at 99 ids it first fires at 33"** | It stays at 34. `-gt` makes `33 * 3 = 99` not greater than 99. Measured on the trimmed file. |
| **`issue-83.md` §5's 45/56 as durable numbers** | They are the losses at a 100-id baseline. `CVE-2026-58055` is a rocky id and came out of both, so at 99 they are 44 and 55. |
| **`issue-83.md` §6's "+6/−6"** | `+17 −3` for `#83`'s own commit. The comment §5 mandates is not in §6's count. |
| **Issue `#83`'s "one-character-class change"** | Three things assert equality, and all three had to move. The plan already said so; this confirms it. |
| **The plans' CI line numbers** | Drifted on master since `aed962d`. `.github/workflows/image.yml:79-80` → `:81-82` and its `upload-artifact` `:90` → `:92` (still with no `if: always()`); `.gitlab-ci.yml:160-161` → `:168-169` and `:190-191` → `:198-199`. Both claims the plans make about them still hold at the new numbers. |
| **The brief's "the cached archive is at `672a500`"** | Already refuted by `issue-79.md` §3 C1 and re-confirmed here. `.cache/scan/image.tar` carries revision `bbd96bab86fdf4badeb6072c43218b953f56fe31`; the podman image `localhost/vcows-deploy:0.1.0.0` is the one at `672a500`. This lane used both — the archive for the `#79` end-to-end, the podman image for the `just scan` that verified the trimmed baseline — and they agree on every number in issue 90's note. |

### Downgraded

| | |
|---|---|
| `issue-83.md` §7 and `issue-79.md` §7's "one new file" | `tests/test_scripts.py` already existed on master, written by the `shell-errexit` lane for exactly this. Nine cases added to it, its module docstring extended to say the file now covers `image-scan.sh` and `bundle.sh` and why. No `conftest.gate()`, per that file's own argument. |
| `issue-79.md` §7's three proposed bundle tests | Landed as three, plus one on the `image-scan.sh` side covering both halves of the stamp's lifecycle in one run — the plan splits the write and the clear across §5 and §8 step 5 and proposes a test for neither. |

### Not closed by this branch

`Closes #83` is in `c9171d5` and `Closes #79` is in `0db8bc5`. **Issue 90 stays
open**, verified `OPEN` after this branch was written. `49d0abf` lands the two
of its eleven items that live in `docs/cve-baseline.json` and carries no closing
keyword anywhere in its subject or body; other lanes have landed others, most
recently two on master as `e555fe9`. Every remaining item is in a file outside
this lane's permitted set and is untouched here. RX-E8 (group membership for
`accepted`) and RX-E12 (`image.tar.sha256`'s "usable directly" claim) remain
nits, deliberately left.
