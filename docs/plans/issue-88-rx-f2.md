# Issue 88, RX-F2 — the five `CLAUDE.md` anchors

Lane `lane/drift`. `#88`'s other six items landed in `8915cd7`; `RX-F2` was
deferred to this lane because re-anchoring while other lanes were still moving
those lines meant doing it twice and being wrong once. That is exactly what
happened to `scripts/lint.sh:34-77` in the interval.

## 1. Reverification verdict

**All five confirmed wrong at `80b1706`, and two of the five are wrong by a
different amount than `#88` records**, because the campaign moved them again
after the issue was filed.

| `CLAUDE.md` | says | `#88` records the truth as | measured at `80b1706` |
|---|---|---|---|
| `:39` | `scripts/lint.sh:34-77` | `:40-114` | **`:40-124`** |
| `:86` | `scripts/image-scan.sh:92` | `:93` | **`:108`** |
| `:100` | `Containerfile:45` | `:80` | `:80` |
| `:101` | `Containerfile:62` | `:97` | `:97` |
| `:102` | `Containerfile:69` | `:104` | `:104` |

`#88`'s body cites the `CLAUDE.md` lines as `:39,85,99,100,101`. `8915cd7`
inserted one line into the banned-skip list, so at `80b1706` they are
`:39,86,100,101,102`.

`docs/review-2026-08-31/verify/F-lows.md` downgraded this medium→low on the
grounds that "each drifted anchor is written beside a unique grep target". That
is true for two of the five and **false for the other three**, which is the whole
decision in this plan.

## 2. Anchor table

Measured in this worktree at `80b1706`. Raw capture in
`docs/review-drift/reverify/anchors.txt`.

| # | claim | grep, and what it returned | hits | decision |
|---|---|---|---|---|
| 1 | `scripts/lint.sh:34-77` is `workflows_carry_no_logic` | `grep -n 'workflows_carry_no_logic' scripts/lint.sh` → `40:workflows_carry_no_logic() {`, `199:    gate "workflows carry no logic" workflows_carry_no_logic`; the closing brace is `124: }` | 1 definition | **de-anchor** |
| 2 | `scripts/image-scan.sh:92` is `--write-baseline` | `grep -n 'write-baseline' scripts/image-scan.sh` → `:5` (usage), `:100` (comment), **`:108`** (`if [ "${1:-}" = "--write-baseline" ]; then`), `:121` (a `die` string), `:185` (comment) | 5, one branch | **de-anchor** |
| 3 | `Containerfile:45` is `BASE_DIGEST` | `grep -n 'BASE_DIGEST' Containerfile` → `:68` comment, **`:80` `ARG BASE_DIGEST=sha256:827d…`**, `:87` bare `ARG BASE_DIGEST`, `:194`, `:212` | **5** | **re-anchor to `:80`** |
| 4 | `Containerfile:62` is `TOFU_RPM_SHA256` | `grep -n 'TOFU_RPM_SHA256' Containerfile` → **`:97` `ARG TOFU_RPM_SHA256=547fe…`**, `:132` the `sha256sum -c -` | **2** | **re-anchor to `:97`** |
| 5 | `Containerfile:69` is `PROVIDER_SHA256` | `grep -n 'PROVIDER_SHA256' Containerfile` → **`:104` `ARG PROVIDER_SHA256=061e5…`**, `:144`, `:195` | **3** | **re-anchor to `:104`** |
| — | `docs/provider-0.9.8.lock.hcl:8` is the `h1:` hash | `grep -n 'h1:' docs/provider-0.9.8.lock.hcl` → `8:    "h1:yqZeKoJ+EZc3687/+ZBqBmtwzvBPLNwaEHW74+bSc6Y=",` | 1 | **correct, unchanged** |

### Why two go one way and three go the other

**The test is whether the number is doing work a name cannot.**

**De-anchored (1 and 2).** `workflows_carry_no_logic` is one definition in one
file, and the sentence already names it — the number was pure redundancy that
could go wrong, and did, **twice in one week**: `:34-77` was right at `059c1ca`,
became `:40-114` by `672a500`, and `d1952aa` made it `:40-124`. A citation that
has been wrong twice in a week is evidence about the citation form, not about the
two authors who edited around it. The same holds for `--write-baseline`: the
claim is about what the flag does, not what any one line says, and the flag name
is the search a reader performs anyway.

The repo already writes both of these de-anchored, in live files:

* `.github/workflows/image.yml:90` — "A `uses:`, so `workflows_carry_no_logic` in
  scripts/lint.sh still passes"
* `scripts/image-build.sh:14` — "It lives in `source_revision` in lib.sh"

and `8915cd7` chose the same for `delivery/SKILL.md`'s `bundle.sh:52`, which now
carries no number at all. `scripts/lint.sh:129-134` argues for it in as many
words.

**Re-anchored (3, 4 and 5).** Here the number is the only thing that
disambiguates, and `F-lows.md`'s "unique grep target" premise does not hold:
`BASE_DIGEST` occurs **five** times in the `Containerfile`, `PROVIDER_SHA256`
three, `TOFU_RPM_SHA256` two. Only one occurrence of each is the `ARG` carrying
the value; the rest are a re-declaration after `FROM`, the `sha256sum -c -`
checks, and label plumbing. The section's subject is *the exact line a human must
hand-edit*, and `BASE_DIGEST` is the worst case: `:80` carries the digest and
`:87` is a bare `ARG BASE_DIGEST` that looks like the same declaration and holds
nothing. Dropping the number there would send a reader to a line where editing is
a no-op.

Re-anchoring accepts that these three will drift again if the `Containerfile`
grows above line 80. That is the cost of the only form that answers the question
the section asks. `just verify-provider` is the gate that catches a half-finished
bump, so a stale number here costs a grep, not a bad pin.

## 3. Corrections to the issue body

1. **`#88`'s "correct at `672a500`" numbers are now wrong for two of the five.**
   `lint.sh` is `:40-124`, not `:40-114` (`d1952aa` added 10 lines).
   `image-scan.sh`'s flag test is `:108`, not `:93` (`80b1706` added 15 lines
   after `:75`).
2. **`#88` cites the wrong `CLAUDE.md` lines**, through no fault of its own:
   `8915cd7` — the commit that closed `#88`'s other six items — added a line at
   `:50`, shifting `:85,99,100,101` to `:86,100,101,102`.
3. **`F-lows.md`'s justification for the downgrade is half wrong.** "Each
   drifted anchor is written beside a unique grep target … so the worst outcome
   is one wasted read followed by one `grep`" holds for `workflows_carry_no_logic`
   and `--write-baseline`. For the three pins the grep returns 2, 3 and 5 hits,
   and for `BASE_DIGEST` one of the wrong hits is a plausible-looking bare `ARG`.
   The verdict (low) stands; the reasoning behind "the finder's own fix is the
   right one" does not apply to three of the five.

## 4. The defect

`CLAUDE.md` is loaded into every session in this repo, so a wrong anchor in it is
read more often than a wrong anchor anywhere else and is read with authority. It
changes nothing an agent *does* — the pins cannot be edited at a comment line and
the gate cannot be disabled from a rule file — so the cost is a wasted read, paid
every session, on a file that exists to save reads.

What makes it worth a commit rather than a shrug is that this is the second time
these five have been corrected in a week and the first correction was never
landed. `#45` filed them, `#88`'s RX-F2 re-filed them as the PARTIAL half, and
two of the five moved again between the filing and now.

## 5. The fix

One file, five lines.

```
:39   `scripts/lint.sh:34-77` (`workflows_carry_no_logic`)  ->  `scripts/lint.sh`'s `workflows_carry_no_logic`
:86   `scripts/image-scan.sh:92` `--write-baseline`          ->  `scripts/image-scan.sh`'s `--write-baseline`
:100  Containerfile:45  ->  Containerfile:80
:101  Containerfile:62  ->  Containerfile:97
:102  Containerfile:69  ->  Containerfile:104
```

**Rejected: de-anchoring all five**, which is what `F-lows.md` and the finder
both propose. It is wrong for the three pins for the reason in §2, and the
section it would damage is the one section of `CLAUDE.md` whose whole content is
four exact locations.

**Rejected: re-anchoring all five.** `:34-77` would become `:40-124` and be wrong
again the next time anything is inserted into `lint.sh` — `docs/review-workflow-gate/REVIEW.md:123-127`
had to write "a later lane re-anchors it to `scripts/lint.sh:40-124`" for a
citation that had already been re-anchored once. Landing that number would
schedule the same sentence for a third time.

**Rejected: a lint gate over `file:line` citations.** Reasoned in
`docs/plans/issue-90.md` §7 and not repeated: the correct remedy for most
citations is to remove the number, and a gate would entrench the form being
retired.

## 6. Surface cost

`CLAUDE.md`, **+5 / −5**. The file stays at its current length, which matters
because §"Already evaluated and rejected" notes it is near a 200-line ceiling.
Two citations lost a number and can no longer go stale; three kept one and can.

## 7. The failing test

No test. The check with teeth is that each of the five numbers written or removed
here came from a `grep -n` run at `80b1706`, captured in
`docs/review-drift/reverify/anchors.txt` and re-runnable from it. For the two
de-anchored rows the same capture carries the hit **count**, which is the property
the de-anchoring depends on: one definition for `workflows_carry_no_logic`, one
branch for `--write-baseline`. For the three re-anchored rows the capture shows
the counts that make a number necessary — 5, 2 and 3.

## 8. Verification

`just check`: six lint gates ok, `ty` clean, `439 passed, 25 skipped`, the
baseline count. `CLAUDE.md` is not read by any gate, so the suite is evidence
that nothing else changed, not that this changed correctly.

Post-edit, all five greps were re-run and `CLAUDE.md` read back against them.

## 9. Non-goals

* `#88`'s RX-F7, RX-F3, RX-F9, RX-F4, RX-F8 and RX-F6. Landed in `8915cd7`.
* `CLAUDE.md:47`'s `tests/conftest.py:7` and `:83`'s `render.py:61`, both
  re-measured here and both correct.
* `CLAUDE.md:13`'s `pyproject.toml:26-34`, which names a block that runs to
  `:36`. It predates this campaign and is not in either issue.
* The `CLAUDE.md:92-93` wording residue `F-lows.md` records under RX-F5 as
  "below filing". Still not filed.
