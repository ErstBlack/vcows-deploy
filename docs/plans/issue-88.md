# Issue #88 — the agent layer: a hook that fails open, and five claims that are not true

Reverified at `aed962d` on branch `lane/agent-layer`. Raw transcripts:
`docs/review-agent-layer/reverify/RX-F7.txt`, `RX-F3.txt`, `RX-F4.txt`,
`RX-F8.txt`, `RX-F9.txt`, `RX-F6.txt`.

## Scope: #88 does NOT close in this lane

**RX-F2 is out of scope here and is not planned below.** It re-anchors five
`CLAUDE.md` line citations, and two of them — `scripts/lint.sh:34-77` and
`scripts/image-scan.sh:92` — point into files that two other lanes are editing
right now. Re-anchoring before those land means doing it twice and being wrong
once. RX-F2 is deferred to a later lane, and **#88 therefore closes in that later
lane, not in this one.** A commit from this lane must not carry `Closes #88`.

This lane plans six of the seven items: **RX-F7, RX-F3, RX-F4, RX-F8, RX-F9,
RX-F6.**

Hook reproduction ran in a `git clone` of this worktree under
`/home/ssullivan/.claude/jobs/1e5fc8c0/tmp/L6`, `.venv` and `.tools` symlinked,
the hook invoked as `.claude/settings.json:8` invokes it. **The worktree's own
`.claude/hooks/static-gate.sh` was never edited**, because it is the Stop hook of
the session doing the work.

---

## 1. Reverification verdict

Six for six. Every claim held; four carry corrections.

| id | verdict | correction |
|---|---|---|
| RX-F7 | **Reproduced**, both arms, mechanism confirmed | one addition: `:105` `record pass` has the same exposure on the healthy path |
| RX-F3 | **Reproduced** under a real repo at a space path | not firing in this worktree; one claim is modelled, not proven |
| RX-F4 | **Confirmed**, six groups / 100 ids at both commits | none |
| RX-F8 | **Confirmed**, and the number survived a change to the file | `bundle.sh` did change since the pin, but not above the `die`; still `:51` |
| RX-F9 | **Confirmed**, six banned forms | none |
| RX-F6 | **Confirmed as history, and no longer true of the tree** | all three issues now have a repo artifact; the finder's `git log` command was wider than intended |

### 1.7 RX-F7 — `.claude/hooks/static-gate.sh:87`

Reproduced on a tree broken by an unused `import os` appended to
`orchestrator/marker.py` (`./scripts/lint.sh` exit 1), state file removed before
each arm:

```
chmod 700 .cache -> hook exit=2   state=block ebc9ced8…   stderr = 46 lines
chmod 500 .cache -> hook exit=1   state file never written  stderr = 1 line:
  static-gate.sh: line 87: …/.cache/static-gate.state: Permission denied
```

Exactly as #88 states. **The 46 lines of lint diagnostic are lost along with the
block** — one line of stderr instead of 46.

### 1.3 RX-F3 — `.claude/settings.json:8,19`

A real clone of `aed962d` checked out at
`/home/ssullivan/.claude/jobs/1e5fc8c0/tmp/L6/space dir`, `.venv`/`.tools`
symlinked, `CLAUDE_PROJECT_DIR` set to it:

```
sh -c '$CLAUDE_PROJECT_DIR/.claude/hooks/session-probe.sh'    -> 127
  sh: …/tmp/L6/space: No such file or directory
sh -c '"$CLAUDE_PROJECT_DIR/.claude/hooks/session-probe.sh"'  -> 0
sh -c '$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh'      -> 127
sh -c '"$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh"'    -> 0, 4.090s,
  state=pass 098b6aa6…
```

The quoted arm ran the full gate against a real repo, so this is not a
path-resolution toy.

### 1.4 RX-F4 — `.claude/skills/cve-triage/SKILL.md:26-30`

```
aed962d : .rationale|keys|length = 6    .accepted|length = 100
053869f : .rationale|keys|length = 6    .accepted|length = 100
```

`git diff --stat 053869f..aed962d` is empty for both `docs/cve-baseline.json` and
the skill. Neither file has changed since the skill shipped, so this is an
authoring error and not drift, exactly as #88 says.

### 1.8 RX-F8 — `.claude/skills/delivery/SKILL.md:20`

The `die` line at each commit in play:

```
053869f (skill shipped) : 52
672a500 (review pin)    : 51
aed962d (HEAD)          : 51
```

### 1.9 RX-F9 — `CLAUDE.md:49-51`

`BANNED` read out of the AST rather than counted by eye: span `76-83`, **6**
members. `CLAUDE.md:50-51` names 3.

### 1.6 RX-F6 — #47 and #52

Every `closed` timeline event carries `commit_id: null` for all three issues —
closed by hand, as claimed. But the tree at `aed962d` is no longer the tree the
finding describes; see §3.6.

---

## 2. Anchor table

All at `aed962d`.

| anchor | state |
|---|---|
| `static-gate.sh:86` `mkdir -p "$(dirname "$STATE")"` | ok |
| `static-gate.sh:87` `printf … > "$STATE"` | ok — **this is the failing command** |
| `static-gate.sh:90-95` `fail()`; `:91` `record block`; `:92-93` the two `printf … >&2`; `:94` `exit 2` | ok, all |
| `static-gate.sh:105` `record pass` | ok — same exposure, not named by #88 |
| `.claude/settings.json:8` / `:19` — the two unquoted command strings | ok, verbatim |
| `.claude/settings.json:28-30` — the three deny entries | ok |
| `.claude/skills/cve-triage/SKILL.md:26-30` — "five groups", "99 accepted IDs" | ok as cited, both wrong |
| `docs/cve-baseline.json` `.rationale` / `.accepted` | 6 / 100 |
| `.claude/skills/delivery/SKILL.md:20` — cites `bundle.sh:52` | ok as cited, wrong |
| `scripts/bundle.sh:51` `die`, `:52` `done` | ok |
| `CLAUDE.md:49-51` — three banned forms | ok as cited, undercounts |
| `tests/test_gates.py:76-83` `BANNED` | ok — 76-83 is the exact AST span |
| `tests/test_gates.py:92` `if name in BANNED` | ok — one filter enforces all six |
| `tests/conftest.py:53` `return pytest.mark.skipif(False, reason=reason)` | ok |
| `CLAUDE.md:67-76` — the `.claude/` tracking-status rule | ok |
| `CLAUDE.md:90-95`, `cve-triage/SKILL.md:35-38` — deny list is a guardrail | ok, both |
| `scripts/lint.sh:183-188` — shellcheck globs `.claude/hooks/*.sh` | ok |

---

## 3. Corrections to the issue body

### 3.7 RX-F7 — `:105` has the same exposure, on the healthy path

#88 stops at `fail()`. `record pass` at `:105` is the last statement before
`exit 0`, and it calls the same `record`. On a tree with **nothing wrong with
it**:

```
clean-tree lint.sh exit=0
chmod 500 .cache -> hook exit=1
  static-gate.sh: line 87: …/.cache/static-gate.state: Permission denied
```

The hook reports a failure for a tree that passed both gates. Harmless in the
sense that exit 1 does not block, but it means an unwritable `.cache/` breaks the
hook on *every* turn, not only on failing ones — which is what makes the
one-line form of the fix (§5.7) the right one.

### 3.3 RX-F3 — say what is proven and what is modelled, and that it is not firing here

**Proven:** the two command strings, as written in `settings.json`, expanded by a
POSIX shell against a `CLAUDE_PROJECT_DIR` containing a space, resolve to a
nonexistent path and exit 127; adding two double-quotes makes both exit 0 against
a real repo.

**Modelled, not proven:** that the harness expands these strings through a shell
*without* quoting the result. `sh -c` reproduces the failure a word-splitting
expansion would produce; it does not observe what the harness does. One thing
that *is* observable: the harness must expand `$CLAUDE_PROJECT_DIR` somehow,
because the hook demonstrably runs in this repo and the literal string is not a
path. Whether it word-splits the expansion is measured by nothing above.

**Not firing here.** `/home/ssullivan/vcows-wt/agent-layer` and
`/home/ssullivan/vcows-deploy` both contain no space. This is latent, and the
case for fixing it is the cost of the fix, not the probability of the failure.

### 3.4 RX-F4 — no corrections

Six groups and 100 accepted ids, at `aed962d` and at `053869f`, exactly as #88
states. The sixth group is `CVE-2026-11979`, exactly as #88 names it. Nothing in
this item needed changing.

### 3.8 RX-F8 — `bundle.sh` changed since the pin, and the number still holds

#88 was written against `672a500`. `scripts/bundle.sh` has changed since:

```
$ git log --oneline 672a500..aed962d -- scripts/bundle.sh
454ee7c Section 4 of the tooling survey, decided item by item (#21) (#72)
$ git diff --stat 672a500..aed962d -- scripts/bundle.sh
 scripts/bundle.sh | 2 +-
```

The change is at `:107` — `cut -f1` → `cut -f1 || true` inside a `log` line,
which is below the `die` and moves nothing. **The correct current line is `:51`,
the same number #88 gives.** Verified by re-reading the file at HEAD, not by
assuming the diff was harmless.

### 3.9 RX-F9 — name the three that are missing, not just the count

`BANNED` = `pytest.skip`, `pytest.importorskip`, `pytest.xfail`,
`pytest.mark.skip`, `pytest.mark.skipif`, `pytest.mark.xfail`.
`CLAUDE.md:50-51` names the first, second and fourth. **Omitted:
`pytest.xfail`, `pytest.mark.skipif`, `pytest.mark.xfail`.**

`pytest.mark.skipif` is the costly one: `conftest.gate()` *returns* exactly that
at `conftest.py:53` on the available path, so a reader has a live example in
front of them of a form the AST walk rejects.

### 3.6 RX-F6 — two corrections, and the finding is now historical

**Correction A — the finder's command was wider than its own description.**
`git log --all` over `4eb378b..672a500` does not mean what it looks like: `--all`
adds every ref as a positive tip, so the range's upper bound is discarded.

```
git log --all --oneline 4eb378b..672a500 | wc -l   ->  114
git log      --oneline 4eb378b..672a500 | wc -l   ->   21
```

114 commits surveyed, not the 21 in the range. That is why the `#50` reference
the finder reports finding by hand was in its own output all along. On the
strict range: `#47` 0, `#50` **1** (`26627ad`'s body), `#52` 0. `F-lows.md`
already corrected the conclusion; this names the reason.

**Correction B — at `aed962d` all three issues have a repo artifact.**

```
#47 -> docs/review-2026-08-31/{REVIEW.md, finders/F-agent-layer.md, verify/F-lows.md}
#50 -> the same three
#52 -> the same three
```

and the GitHub timelines now agree:

```
#50  closed commit_id=null   referenced commit_id=26627ad  (2026-08-30)
#47  closed commit_id=null   referenced commit_id=b58f924  (2026-08-31)
#52  closed commit_id=null   referenced commit_id=b58f924  (2026-08-31)
```

`b58f924` is the commit that recorded this review. **The act of filing RX-F6
closed the gap RX-F6 describes.** `REVIEW.md:270-279` states the case in the repo,
in the tree, permanently.

**Correction C — a small one.** `F-lows.md` calls `CLAUDE.md` "128 of its
200-line ceiling"; it is **130** lines at `aed962d`. The 200-line contract is
real (`docs/review-2026-08-29/_progress.md:82`), so the argument stands; the
number is off by two.

---

## 4. The defect

### 4.7 RX-F7 — `set -e` turns a failed bookkeeping write into the hook's exit status

`fail()` at `:90-95` is:

```
fail() {
    record block          # :91
    printf '%s\n' "$1" >&2   # :92
    printf '%s\n' "$2" >&2   # :93
    exit 2                   # :94
}
```

`record block` is the **first** statement. Under `set -e` (`:35`), if it fails
the shell exits immediately with `record`'s status and `:92-94` never run. So the
hook exits 1 instead of 2, and — worse than #88's framing already implies — the
model gets neither the block nor the diagnostic.

**The failing command is `:87`, not `:86`.** Isolated on the mode-500 directory:

```
mkdir -p "$W/.cache"                              -> exit 0
printf '%s %s\n' block deadbeef > "$W/…/state"    -> exit 1, Permission denied
```

and in the order `record()` runs them, under `set -e`:

```
  reached-after-mkdir
  …/static-gate.state: Permission denied
  combined exit=1
```

`reached-after-mkdir` prints and `reached-after-printf` does not. `mkdir -p` on
an existing directory succeeds whatever its mode; the redirect is the failure.

Reproduced directly on `fail()` in isolation, with a marker string standing in
for the diagnostic:

```
fail() harness exit=1
stderr: …/static-gate.state: Permission denied
does THE-DIAGNOSTIC appear on either stream? 0
```

`:12-14` of the same file says exit 2 exists so a break is fixed in the same
turn. Exit 1 routes stderr to the debug log instead. **The one thing this hook
exists to do is the thing that stops working.**

### 4.3 RX-F3 — 127 is not 2, and only 2 blocks

For a Stop hook, exit 2 blocks and puts stderr in front of the model. Every other
non-zero status is a non-blocking error. Exit 127 ("command not found") is
therefore indistinguishable from "no hook configured": the Stop gate is absent
for the whole session, silently. `session-probe.sh` fails the same way, so a
session under a space path gets no environment probe either.

### 4.4 RX-F4 — the skill's argument is quantitative, and the numbers are low

`cve-triage/SKILL.md`'s case against `--write-baseline` is *how much reasoning it
deletes*. Undercounting six groups as five, and 100 ids as 99, weakens the exact
argument the file is making. The missing group is `CVE-2026-11979`:

```
count  : 1 finding, MEDIUM, libxml2 2.12.5-10.el10 in the rocky base layer
recheck: on the next BASE_DIGEST re-pin; recheck date 2026-12
```

Its `why` ends: *"libxml2 comes from the base layer … not from the
Containerfile's `dnf -y install`, so no rebuild picks it up. Only re-pinning
`BASE_DIGEST` can."* It is the one acceptance tied to `BASE_DIGEST` — the entry
an agent re-triages after a re-pin, and the one the skill exists to protect.

### 4.8 RX-F8 — a line number that was right when written

`:52` was correct at `053869f` and drifted by one. Same class as RX-F2: a
cross-file line number goes stale on any edit above the target, which is what
`scripts/lint.sh:129-134` already argues in this repo.

### 4.9 RX-F9 — the rule file understates the rule the test enforces

`CLAUDE.md:49-51` says "fails on any bare `pytest.skip`, `pytest.importorskip` or
`pytest.mark.skip`". A reader who wants a conditional skip reaches for
`pytest.mark.skipif`, which the list does not forbid and `test_gates.py:92` does.
The failure mode is a wasted turn, not a wrong gate — but it is in the section
whose subject is that a gate must be able to become a failure.

### 4.6 RX-F6 — there is no defect left to fix

The claim was: two issues landed outside the project directory, so a fresh clone
holds no trace. At `aed962d` a fresh clone holds three traces of each (§3.6 B),
and the GitHub timeline for each now carries a real `commit_id`. The rule at
`CLAUDE.md:67-76` was never wrong about its own subject — the two files in
`.claude/` with opposite tracking status — it simply does not enumerate every
place work can land, and never claimed to.

---

## 5. The fix

### 5.7 RX-F7 — one `|| true`, inside `record()`

`static-gate.sh:87`:

```
    printf '%s %s\n' "$1" "$cur" > "$STATE" || true
```

Three options were built and measured, not argued:

| | edit | broken+700 | broken+500 | clean+700 | clean+500 |
|---|---|---|---|---|---|
| **O1** | `record block \|\| true` at `:91` only | 2 | 2 | 0 | **1** |
| **O2** | O1 plus `record pass \|\| true` at `:105` | 2 | 2 | 0 | 0 |
| **O3** | `\|\| true` on `:87`, inside `record()` | 2 | 2 | 0 | 0 |

**Take O3.** O1 is the fix #88 names and it leaves the healthy path broken (§3.7).
O2 fixes both but edits two call sites for one property. O3 is one word at the
one command that can fail, covers both callers, and cannot be missed by a future
third caller. `record`'s contract is already best-effort — `:85` returns 0 when
`cur` is empty — so the guard belongs there.

Under O3 with a mode-500 `.cache`, the broken-tree stderr is **47** lines: the
one-line permission warning plus all 46 diagnostic lines. The warning is worth
keeping visible; it is now delivered alongside the block rather than instead of
it.

### 5.3 RX-F3 — two double-quotes

`.claude/settings.json:8` and `:19`, JSON-escaped:

```json
"command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh\"",
"command": "\"$CLAUDE_PROJECT_DIR/.claude/hooks/session-probe.sh\"",
```

Verified the candidate round-trips through `json.loads` and `jq`, and that the
decoded string is `"$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh"` with the
quotes intact.

Four characters, and it is correct under both readings of §3.3: if the harness
already quotes, the extra quotes are inert; if it does not, they are the fix.
That asymmetry is the whole argument for doing it despite the failure being
latent.

### 5.4 RX-F4 — one line

`cve-triage/SKILL.md:26-30`: "five groups" → "six groups", add
`CVE-2026-11979` to the parenthesised list, "The 99 accepted IDs survive" → "100".

Consider naming the sixth group **last with its reason** — it is the
`BASE_DIGEST`-tied entry, and the skill's own subject is what an agent loses by
regenerating. One clause, not a paragraph.

### 5.8 RX-F8 — one line

`delivery/SKILL.md:20`: `scripts/bundle.sh:52` → `scripts/bundle.sh:51`.

**Prefer dropping the number.** `lint.sh:129-134` argues in this repo that a
line number pointing into another file goes stale on every edit above the target
— which is precisely what happened here. The `die` string is a unique grep target
(`run 'just scan' first`), so *"`scripts/bundle.sh` checks for the archive, the
SBOM and the trivy report and dies with:"* loses nothing and cannot drift again.

### 5.9 RX-F9 — name all six, or name none

`CLAUDE.md:50-51`. Two forms, both one line:

* **O1** — enumerate: "…fails on any bare `pytest.skip`, `pytest.importorskip`,
  `pytest.xfail`, `pytest.mark.skip`, `pytest.mark.skipif` or `pytest.mark.xfail`."
* **O2** — stop enumerating: "…fails on any of the six forms in
  `test_gates.py`'s `BANNED`, `pytest.mark.skipif` and `pytest.mark.xfail`
  included."

**Take O1.** The section's value is that a reader learns the forbidden forms
without opening a second file, and six names on one line is not a cost. O2 trades
that for a citation that can itself drift — the failure this issue already
contains twice.

### 5.6 RX-F6 — record it and close it

**Nothing is actionable. That is the honest answer, and it is a change of state,
not a re-litigation of the severity.**

`F-lows.md` downgraded RX-F6 low → nit on the argument that the record for #47
and #52 exists in the GitHub issue comments. §3.6 B establishes something
stronger: at `aed962d` the record also exists **in the repository**, in
`docs/review-2026-08-31/REVIEW.md:270-279`, and both issues carry a `referenced`
timeline event pointing at `b58f924`. The gap the finding names is closed by the
commit that filed the finding.

**Rejected: widening `CLAUDE.md:67-76` to name `~/.claude/settings.json`
`enabledPlugins` and `~/.claude/local-plugins/`.** It is two sentences in a
130-line file under a 200-line contract, describing a case that has occurred
twice and is already documented in-tree. The section's subject is the tracking
status of two files inside `.claude/`; extending it into an enumeration of every
directory work can land in makes it a different, longer, less true rule. This
repo treats unjustified surface as a defect, and a rule that lists two of the
many places a decision can land invites the next reader to think the list is
closed — the same failure `CLAUDE.md:90-95` already warns about for the deny
list.

**So: state the reverification result in the commit body, and let RX-F6 close
with the rest of #88 in the later lane.** No file changes.

### Deliberately not filed — stays refuted

`.claude/settings.json:28-30` leaving `sh scripts/…` and `bash ./scripts/…`
uncovered was **refuted**, and this reverification does not reopen it. The
spellings are real; the reason it is not a defect is that `CLAUDE.md:90-95` and
`cve-triage/SKILL.md:35-38` both state in the repo that the deny list is a prefix
matcher and *"a guardrail against the obvious helpful action, not a boundary"*.
Both anchors re-verified at `aed962d`. Adding two entries adds surface to an
enumeration the repo documents as unclosable. Not planned, not measured, not
reopened.

---

## 6. Surface cost

| id | files | change |
|---|---|---|
| RX-F7 | `.claude/hooks/static-gate.sh` | +1 word (`\|\| true`) on `:87` |
| RX-F3 | `.claude/settings.json` | +4 characters across `:8` and `:19` |
| RX-F4 | `.claude/skills/cve-triage/SKILL.md` | 2 edited lines |
| RX-F8 | `.claude/skills/delivery/SKILL.md` | 1 edited line, and it gets shorter |
| RX-F9 | `CLAUDE.md` | 1 edited line |
| RX-F6 | — | none |

Five files, no new file, no new function, no new test, no new gate, no new
config key. RX-F8 and the RX-F6 rejection both *remove* surface or decline to add
it.

If this lane also carries #84, `static-gate.sh` takes two edits in one commit —
one alternation and one `|| true`. Measured together in §8.

---

## 7. The failing test

Nothing in `tests/` references `.claude/hooks/`, `.claude/skills/` or
`CLAUDE.md`. The only gate over any of them is `shellcheck` on the hooks
(`lint.sh:188`), which cannot see any of these six. So each item's falsifiable
claim is made by running something, and each was:

**7.7 RX-F7 — the two chmod arms are the test.** Before the fix:
`chmod 700` → exit 2 with 46 lines; `chmod 500` → exit 1 with 1 line. After O3:
`chmod 500` → exit 2 with 47 lines, `chmod 700` → exit 2 with 46 lines and
`state=block ebc9ced8…` unchanged. Clean tree under `chmod 500`: exit 1 before,
exit 0 after. Four arms, each flips exactly as predicted.

**7.3 RX-F3 — the space-path clone is the test.** Unquoted 127 / quoted 0 for
both hooks, and the quoted `static-gate.sh` arm ran the real gate to completion
(4.090s, `pass 098b6aa6…`) rather than merely resolving a path.

**7.4 RX-F4 — `jq` at two commits is the test.** 6/100 at `aed962d` and 6/100 at
`053869f`, with `git diff --stat` empty for both files across that span. Any
claim of "five" or "99" is falsified by one command.

**7.8 RX-F8 — `grep -n` at three commits.** 52 / 51 / 51.

**7.9 RX-F9 — the AST, not the eye.** `BANNED` `literal_eval`'d out of
`tests/test_gates.py`: span 76-83, 6 members. The set difference against
`CLAUDE.md`'s three is computed, not read.

**7.6 RX-F6 — `gh api …/timeline` and `git grep`.** The claim "no artifact
anywhere in the repo" is falsified by `git grep -l '#47'` returning three tracked
files.

**No new pytest is proposed for any of the six.** The three documentation items
(RX-F4, RX-F8, RX-F9) would each need a test that re-states the fact it is
checking — a second copy of the number, in the repo, able to drift from the
first. That is the failure this issue *is*.

## 8. Verification

Baseline at `aed962d`, re-measured on the clean scratch clone rather than taken
on trust: six lint gates ok, `ty` clean, **411 passed, 25 skipped** in 37.09s,
`just check` exit 0 in 42.3s. Only RX-F7 can move it; the other five edit files
no gate reads.

### 8.7 RX-F7 — the patched hook still passes the gate that lints it

Each candidate installed at `.claude/hooks/static-gate.sh` in the scratch clone,
with `shellcheck` run using `lint.sh:183-188`'s exact flag set
(`-x -s bash -o check-extra-masked-returns -o check-unassigned-uppercase
-o quote-safe-variables -o avoid-nullary-conditions`):

```
O2 (record block/pass || true) : shellcheck exit=0   ./scripts/lint.sh exit=0
O3 (|| true on :87)            : shellcheck exit=0   ./scripts/lint.sh exit=0
COMBINED (#84 alternation + O3): shellcheck exit=0   ./scripts/lint.sh exit=0
```

`|| true` is not flagged by any of the four optional checks this repo enables —
`check-set-e-suppressed`, which would flag it, is deliberately not in `lint.sh`'s
list. `COMBINED` is measured because this lane may land #84 and RX-F7 as one
commit to the same file.

Behavioural arms, all four measured in §7.7: broken/700, broken/500, clean/700,
clean/500.

### 8.3 RX-F3 — the only available check is that the JSON round-trips

No gate parses `.claude/settings.json` (`workflows_carry_no_logic` reads only the
two pipeline files; nothing in `tests/` references it). The candidate with both
command strings quoted parses under `json.loads` and under `jq`, decoding to
`"$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh"` with the quotes intact —
escaped in the file as `"\"$CLAUDE_PROJECT_DIR/…/static-gate.sh\""`.

That the fix *works* cannot be verified in this worktree at all, because no path
in play has a space (§3.3). §7.3's space-path clone is the substitute, and it is
the strongest available: the quoted string ran the real gate to completion there.

### 8.4 RX-F4 — re-run the two `jq` queries after the edit

`jq '.rationale|keys|length'` → 6 and `jq '.accepted|length'` → 100 against
`docs/cve-baseline.json` must agree with the two numbers the edited SKILL.md
prints, and the six group names must match `jq -r '.rationale|keys[]'`. One
command per number; nothing else can verify prose.

### 8.8 RX-F8 — re-grep the die

`grep -n "run 'just scan' first" scripts/bundle.sh` → `51`. If §5.8's preferred
form is taken and the number is dropped, the check becomes that the grep target
still resolves to exactly one line, which it does.

### 8.9 RX-F9 — the AST comparison must come back empty

Re-run §7.9's set difference after the edit: the six `BANNED` members
`literal_eval`'d from `tests/test_gates.py` minus the forms named in
`CLAUDE.md:50-51` must be the empty set. It is currently
`{pytest.xfail, pytest.mark.skipif, pytest.mark.xfail}`.

### 8.6 RX-F6 — nothing to verify

No file changes. The verification *is* §3.6 B: `git grep -l '#47'` returns three
tracked files and the timeline carries `referenced commit_id=b58f924`.

Scratch clone left clean — `git status --porcelain` shows only the `.tools` and
`.venv` symlinks the harness added.

## 9. Non-goals

* **RX-F2.** Out of this lane by the carve-out at the top. `#88` does not close
  here.
* **`.claude/settings.json:28-30`.** Refuted and staying refuted; see §5.
* **A test for any of the five documentation items.** See §7.
* **Mirroring `~/.claude/` config into the repo** (RX-F6). It carries
  machine-specific absolute paths, and §3.6 B shows the record already exists
  in-tree without it.
* **`session-probe.sh`'s own behaviour.** Verified sound by the finder and
  unchanged here; its only exposure is RX-F3, which the same two quotes fix.
* **#84's alternation.** Same file as RX-F7, separate issue, planned in
  `docs/plans/issue-84.md`. §8 measures the combined patch because the lane may
  land both as one commit.
* **`#23`.** Named in #88 as having RX-F6's shape. It is a different issue and
  nothing here touches it.
