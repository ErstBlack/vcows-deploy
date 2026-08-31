# Scoped review — lane `agent-layer`

Input: `git diff origin/master...lane/agent-layer` and nothing else. 128 diff
lines, five files, two commits, base `d9d9252`.

```
94c12f2 Keep the static gate blocking when .cache is unwritable, and correct four stale claims in the agent layer
259fa9f Put pyproject.toml, the tftest file and the justfile inside the static gate's signature

 .claude/hooks/static-gate.sh       | 21 ++++++++++++---------
 .claude/settings.json              |  4 ++--
 .claude/skills/cve-triage/SKILL.md |  7 ++++---
 .claude/skills/delivery/SKILL.md   |  2 +-
 CLAUDE.md                          |  5 +++--
 5 files changed, 22 insertions(+), 17 deletions(-)
```

Every hook run below was driven the way `.claude/settings.json:8` drives it —
`CLAUDE_PROJECT_DIR` set, a synthetic `Stop` payload on stdin — inside a
`git clone` of the worktree at `/home/ssullivan/.claude/jobs/1e5fc8c0/tmp/L6impl`
with `.venv` and `.tools` symlinked in. The worktree's own hook was edited only
once the patch was proven in the clone.

---

## Commit 1 — `259fa9f`, the signature alternation (`Closes #84`)

### 1.1 Did it do what the plan said?

`docs/plans/issue-84.md` §5 asks for one alternation and three comment blocks in
one file. The diff is exactly that, and nothing else.

| plan §5 item | in the diff |
|---|---|
| `:54` → `\.(py\|sh\|tf\|ya?ml\|toml)$\|\.tftest\.hcl$\|(^\|/)(Containerfile\|justfile)$` | yes, verbatim, one line |
| `:20-21` "actually read" → "read **or are configured by**" | yes |
| `:47-49` extend the enumeration to `pyproject.toml`, `*.tftest.hcl`, the `justfile` | yes |
| `:31-33` "over 70 files" → 74 | yes |
| §5 R1 cache untouched, R2 no bare `git ls-files`, R3 no second list | yes — `record()`, `STATE` and the `:66-82` short-circuit are byte-identical in this commit |

**Not described by the plan, and therefore flagged:** the `:31-33` block grew
from three lines to four, and its measurement sentence was re-scoped. The plan
says only "`over 70 files` → 74". Writing 74 into a sentence whose subject is
`26627ad` would have asserted that `26627ad` selected 74 files, which is false —
it selected 70. The commit splits the sentence so the lint/`ty` timings stay
attributed to `26627ad` and the signature timing carries its own commit and its
own re-measured numbers (`0.033/0.034/0.039s`, measured here, three runs). This
is a correction *to* the plan, not a departure from its intent, but it is a diff
line the plan does not predict.

Second flag, smaller: §5 offers two spellings and prefers the compact one. The
diff takes the compact one. Both were re-measured on the branch base and select
the identical 74 files (`diff` of the two selections is empty), so the choice
carries no behavioural content.

### 1.2 Does it have teeth?

The same six arms, before and after, on the same trees. `before` = the hook at
`d9d9252`; `after` = the hook this commit installs.

```
########## BEFORE — unpatched hook at d9d9252
        | grep -zE '\.(py|sh|tf|ya?ml)$|(^|/)Containerfile$' \

== negative: clean tree, cold then warm ==
  clean cold                    exit=0  elapsed=4.268s  state=[pass 098b6aa6...]
  clean warm                    exit=0  elapsed=0.039s  state=[pass 098b6aa6...]

== 1a: pyproject.toml:124 line-length 88 -> 40 ==
  1a hook                       exit=0  elapsed=0.038s  state=[pass 098b6aa6...]
== 1b: pyproject.toml:168 [tool.ty.src] exclude -> [] ==
  1b hook                       exit=0  elapsed=0.038s  state=[pass 098b6aa6...]
== 1c: misformatted block in tests/libvirt-module.tftest.hcl ==
  1c hook                       exit=0  elapsed=0.040s  state=[pass 098b6aa6...]
== 1d: justfile:55 -> a script that does not exist ==
  1d hook                       exit=0  elapsed=0.038s  state=[pass 098b6aa6...]

== control: 1a's broken tree, state file REMOVED ==
  control (no state)            exit=2  elapsed=5.125s  state=[block 098b6aa6...]
      stderr[1]: just lint failed:
```

```
########## AFTER — 259fa9f
        | grep -zE '\.(py|sh|tf|ya?ml|toml)$|\.tftest\.hcl$|(^|/)(Containerfile|justfile)$' \

== negative: clean tree, cold then warm ==
  clean cold                    exit=0  elapsed=3.875s  state=[pass f67a3292...]
  clean warm                    exit=0  elapsed=0.035s  state=[pass f67a3292...]

== 1a ==  exit=2  elapsed=4.972s  state=[block 064d3879...]  stderr[1]=just lint failed:
== 1b ==  exit=2  elapsed=3.994s  state=[block 6d410f48...]  stderr[1]=just typecheck failed:
== 1c ==  exit=2  elapsed=3.330s  state=[block f8df2d44...]  stderr[1]=just lint failed:
== 1d ==  exit=2  elapsed=0.066s  state=[block a402543c...]  stderr[1]=just lint failed:

== control: 1a's broken tree, state file REMOVED ==
  control (no state)            exit=2  elapsed=5.190s  state=[block 064d3879...]
```

Four for four, exit 0 → exit 2, and the recorded signature — byte-identical
across all four mutations before — is now four distinct hashes. `1d` blocks in
0.066s because `just lint` itself dies at 127 immediately; the block is still
correct.

**The control is what makes this the alternation's fix and not the cache's.**
On the unpatched hook the broken tree records `block 098b6aa6…` and the clean
tree records `pass 098b6aa6…` — the same signature, opposite verdicts. The
cache reports faithfully about an input set that was wrong, so the cache is not
touched.

**The negative holds:** clean tree, patched hook, still a fast cached pass
(0.035s, exit 0).

Counts, re-measured on the branch base rather than inherited:

```
tracked total : 183
INSIDE current: 71     INSIDE new: 74     INSIDE \.(toml|hcl)$: 75
added by new  : justfile, pyproject.toml, tests/libvirt-module.tftest.hcl
added on top by the over-broad form: docs/provider-0.9.8.lock.hcl
```

The over-broad form is rejected on a measurement, re-run here rather than
inherited. A deliberately misformatted `provider` block appended to
`docs/provider-0.9.8.lock.hcl`:

```
tofu fmt -check -recursive .  exit=0
tofu fmt -check docs/         exit=0
./scripts/lint.sh             exit=0
```

`tofu fmt` handles `.tf`, `.tfvars`, `.tofu` and `.tftest.hcl`, not lock files.

`:61-63` was checked rather than trusted, because the commit leaves it alone.
With a `pass` recorded and a `git` on `PATH` that exits 1:

```
warm, normal PATH : exit=0  elapsed=0.039s
warm, failing git : exit=0  elapsed=3.316s
```

3.3s, not 0.04s. `cur` is empty, the `:66` guard fails, the gates run. True as
written; correcting it would have introduced a false comment.

Gate cost: `shellcheck -x -s bash -o check-extra-masked-returns
-o check-unassigned-uppercase -o quote-safe-variables -o avoid-nullary-conditions`
over `scripts/*.sh .claude/hooks/*.sh` — `scripts/lint.sh:183-188`'s exact flag
set — exit 0. `just check`: six lint gates ok, `ty` clean, **411 passed, 25
skipped**.

Signature timing, three runs each, same clone: current `0.035/0.034/0.033s` over
71, proposed `0.033/0.034/0.039s` over 74. Inside the noise.

### 1.3 What moved?

The header grew by 3 lines, so every anchor from the old `:34` down shifts.

| old | new | what |
|---|---|---|
| `:20-22` | `:20-22` | the "signature is content" paragraph — text changed, span unchanged |
| `:31-33` | `:31-34` | the measurement block, now 4 lines |
| `:35` | `:36` | `set -euo pipefail` |
| `:45` | `:46` | `STATE=` |
| `:47-51` | `:48-54` | the enumeration comment, now 7 lines |
| `:52-59` | `:55-62` | `signature()` |
| **`:54`** | **`:57`** | **the grep — the line `#84` names** |
| `:61-63` | `:64-66` | the fail-open comment |
| `:64` | `:67` | `cur="$(signature)"` |
| `:66` | `:69` | the `[ -n "$cur" ] && [ -f "$STATE" ]` guard |
| `:84-88` | `:87-91` | `record()` |
| `:86` / `:87` | `:89` / `:90` | `mkdir -p` / the state write |
| `:90-95` | `:93-98` | `fail()` |
| `:91` | `:94` | `record block` |
| `:97` / `:101` | `:100` / `:104` | `just lint` / `just typecheck` |
| `:105` / `:106` | `:108` / `:109` | `record pass` / `exit 0` |

Citations into those lines live in `docs/review-2026-08-31/` — `REVIEW.md:160`,
`finders/F-agent-layer.md:47` and `:151`, `ledger/b-issues-41-63.md:27`,
`verify/RX-F1.md:20-24`. **They are an archived record of that review and should
not be re-anchored**; they were correct about `672a500`. This table is what a
later lane needs if it cites the hook afresh.

Nothing outside `docs/review-2026-08-31/` cites a `static-gate.sh` line number.

---

## Commit 2 — `94c12f2`, six of `#88`'s seven items (does **not** close `#88`)

### 2.1 Did it do what the plan said?

`docs/plans/issue-88.md` plans six items and defers RX-F2. The diff carries five
edits and one record-only item, and the commit body carries RX-F6 and the RX-F2
deferral.

| item | plan §5 | in the diff |
|---|---|---|
| RX-F7 | O3 — `\|\| true` on the state write **inside `record()`**, not `record block \|\| true` at the call site | yes, one word, one line |
| RX-F3 | two double-quotes at `settings.json:8` and `:19` | yes, 4 characters |
| RX-F4 | "five"→"six", add `CVE-2026-11979` **last with its reason**, "99"→"100" | yes |
| RX-F8 | **drop** the line number rather than move it to `:51` | yes — `scripts/bundle.sh:52` → `scripts/bundle.sh` |
| RX-F9 | O1 — enumerate all six `BANNED` forms | yes |
| RX-F6 | record only, no file change | yes — no diff line, stated in the body |
| RX-F2 | deferred, `#88` stays open | no `Closes #88` anywhere in the branch; the body says so |
| `settings.json:28-30` | stays refuted, no deny entries | yes — `permissions.deny` is untouched, still three entries |

**Nothing in the diff is outside this list.** The whole commit is 11 insertions
and 9 deletions across the five files (`static-gate.sh` 1/1,
`settings.json` 2/2, `cve-triage/SKILL.md` 4/3, `delivery/SKILL.md` 1/1,
`CLAUDE.md` 3/2), and every one is accounted for above.

One flag on shape, not content: `cve-triage/SKILL.md` gains a line (5 → 6) and
`CLAUDE.md` gains a line (130 → 131). §6 predicts "2 edited lines" and "1 edited
line" respectively. Both grew because the added clause and the three added form
names would otherwise have run past the files' wrap width. `CLAUDE.md` is at 131
of a 200-line contract (`docs/review-2026-08-29/_progress.md:82`).

### 2.2 Does it have teeth?

**RX-F7 — the four arms.** Broken tree = an unused `import os` appended to
`orchestrator/marker.py` (`./scripts/lint.sh` exit 1). `before` is the hook after
commit 1 but without the `|| true`, so the arm isolates this commit's one word.

```
BEFORE (no || true)                          AFTER (94c12f2)
 broken + 700  exit=2  46 lines  block ...    exit=2  46 lines  block ...
 broken + 500  exit=1   1 line   <never>      exit=2  47 lines  <never>
 clean  + 700  exit=0   0 lines  pass  ...    exit=0   0 lines  pass  ...
 clean  + 500  exit=1   1 line   <never>      exit=0   1 line   <never>
```

Two arms flip, two are held unchanged. The single stderr line in the failing
arms is
`static-gate.sh: line 90: .../.cache/static-gate.state: Permission denied`, and
after the fix it is *joined by* the 46 diagnostic lines rather than replacing
them. The `clean + 500` arm is the one `#88` does not name and the reason O3 was
taken over `#88`'s own `record block || true`: that form leaves `clean + 500` at
exit 1, because `record pass` at the end of the healthy path has the same
exposure.

Which command fails was measured, not assumed: `mkdir -p` returns 0 on an
existing mode-500 directory; the redirect is the failure.

**RX-F3 — a real clone at a space path.** `git clone` of this branch's base
checked out at `.../tmp/L6impl/space dir`, `CLAUDE_PROJECT_DIR` set to it, both
command strings exactly as `settings.json` holds them:

```
session-probe.sh  unquoted  exit=127  sh: .../tmp/L6impl/space: No such file or directory
session-probe.sh  quoted    exit=0    0.151s
static-gate.sh    unquoted  exit=127  sh: .../tmp/L6impl/space: No such file or directory
static-gate.sh    quoted    exit=0    3.725s   state=pass 098b6aa6...
```

The quoted `static-gate.sh` arm ran the real gate to completion, so this is not
a path-resolution toy. **Proven** is the shell behaviour above. **Modelled** is
that the harness expands these strings through a shell without quoting the
result — `sh -c` reproduces what a word-splitting expansion produces, it does
not observe the harness. **Not firing here**: no path in play contains a space,
so the fix is justified by its cost (4 characters, inert under the other
reading), not by the probability of the failure.

The only available gate on the file is that it still parses. It does, under both
readers, decoding with the quotes intact:

```
json.loads -> '"$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh"'
jq -r      ->  "$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh"
deny still: ['Bash(./scripts/image-scan.sh --write-baseline:*)',
             'Bash(scripts/image-scan.sh --write-baseline:*)',
             'Bash(bash scripts/image-scan.sh --write-baseline:*)']
```

**RX-F4 — `jq` at two commits.** The two numbers the skill now prints must equal
the file:

```
jq '.rationale|keys|length' docs/cve-baseline.json          -> 6    (skill: "six groups")
jq '.accepted|length'       docs/cve-baseline.json          -> 100  (skill: "The 100 accepted IDs")
git show 053869f:docs/cve-baseline.json | jq ... -> 6 and 100
git diff --stat 053869f..d9d9252 -- docs/cve-baseline.json .claude/skills/cve-triage/SKILL.md
  (empty)
```

Every one of the six `jq -r '.rationale|keys[]'` names is now present in the
skill. Six and 100 at both commits with an empty diff between them: this was an
authoring error, not drift.

**RX-F8 — the grep target.** `grep -c "run 'just scan' first" scripts/bundle.sh`
= 1, at `:51`; it was `:52` at `053869f`, which is how the citation went stale.
Dropping the number rather than moving it means it cannot go stale a third time.

**RX-F9 — the AST, not the eye.** `BANNED` `literal_eval`'d out of
`tests/test_gates.py`: span 76-83, 6 members. The set difference against the
forms `CLAUDE.md` now names:

```
BANNED - named = []      (it was {pytest.xfail, pytest.mark.skipif, pytest.mark.xfail})
```

**RX-F6 — nothing to verify, because nothing changed.** Re-checked read-only:
every `closed` event still carries `commit_id: null` for `#47`, `#50` and `#52`,
and every one now also carries a `referenced` event — `#47` and `#52` at
`b58f924`, `#50` at `26627ad`. A fresh clone holds
`docs/review-2026-08-31/{REVIEW.md, finders/F-agent-layer.md, verify/F-lows.md}`
for each.

One correction to the plan's own evidence: `git grep -l '#47'` returns **four**
tracked files, not three. The fourth, `docs/tooling-2026-08-30.md:227`, is
`(#4785)` — a substring, not a reference. The plan's conclusion is unaffected;
its command was one character short of precise.

`just check` after this commit: six lint gates ok, `ty` clean, **411 passed, 25
skipped** in 30.61s, exit 0. The patched hook passes `lint.sh:183-188`'s exact
shellcheck flag set at exit 0; `|| true` is not flagged, because
`check-set-e-suppressed` — the check that would flag it — is deliberately not in
`lint.sh`'s list of four.

### 2.3 What moved?

`static-gate.sh` moves nothing further: the `|| true` is appended to an existing
line. The state write is at `:90` after both commits (`:87` at `d9d9252`).

`.claude/settings.json`: no line count change. `:8` and `:19` still hold the two
command strings; `:28-30` still hold the three deny entries.

`.claude/skills/delivery/SKILL.md`: no line count change. `:20` still holds the
sentence, now without the number.

`.claude/skills/cve-triage/SKILL.md` gains one line at `:29`, so:

| old | new | what |
|---|---|---|
| `:26-30` | `:26-31` | the `--write-baseline` paragraph |
| `:35-38` | `:36-39` | "a guardrail … not a boundary" — cited by `docs/review-2026-08-31/verify/F-lows.md:86` |
| `:67-69` | `:68-70` | the hand-edit shape — cited by `REVIEW.md:195`, `verify/E-lows.md:44` |

`CLAUDE.md` gains one line at `:51`, so **every anchor from `:52` down shifts
+1**. The ones that matter to work already queued:

| old | new | what |
|---|---|---|
| `:39` | `:39` | `scripts/lint.sh:34-77` — **unchanged**, it is above the edit. RX-F2 anchor 1 |
| `:49-51` | `:49-52` | the banned-forms sentence this commit rewrote |
| `:53-56` / `:55-56` | `:54-57` / `:56-57` | `VCOWS_GATES` and the whitespace claim |
| `:67-76` | `:68-77` | the `.claude/` tracking-status rule |
| `:71-73` / `:75-76` | `:72-74` / `:76-77` | the `~/.config/git/ignore` sentence / the "no artifact" consequence |
| `:78` / `:78-88` | `:79` / `:79-89` | the `cve-baseline.json` section |
| `:85` | **`:86`** | `scripts/image-scan.sh:92` — **RX-F2 anchor 2** |
| `:90-95` / `:92-93` / `:93-95` | `:91-96` / `:93-94` / `:94-96` | the deny-list guardrail paragraph |
| `:99-101` | **`:100-102`** | `Containerfile:45` / `:62` / `:69` — **RX-F2 anchors 3, 4, 5** |
| `:102` | `:103` | `docs/provider-0.9.8.lock.hcl:8` |
| `:110` | `:111` | the `VCOWS_GATES=all` rejection |

**RX-F2's five anchors are now at `CLAUDE.md:39`, `:86`, `:100`, `:101`,
`:102`.** The lane that does RX-F2 must re-read them there, not at the numbers
`docs/review-2026-08-31/REVIEW.md:180` records. That is exactly the drift RX-F2
is about, and this commit added one line of it — unavoidably, since RX-F9's fix
is in the same file and above them.

---

## Ledger

**Raised (2)** — both against the plans, neither blocking:

* **L1.** `docs/plans/issue-84.md` §5 asks for "over 70 files → 74" at
  `:31-33`. Applied literally that produces a false sentence, because the
  surrounding measurement is attributed to `26627ad`, which selected 70. The
  commit re-scoped the sentence and re-measured the signature timing rather than
  editing one number inside a claim about another commit.
* **L2.** `docs/plans/issue-88.md` §3.6 B and §7.6 rest on
  `git grep -l '#47'` returning three tracked files. It returns four; the fourth
  is `(#4785)` in `docs/tooling-2026-08-30.md:227`, a substring match. The
  finding's conclusion holds.

**Confirmed (11)**

* The `#84` mechanism, all four mutations, on this base: exit 0 in ~0.04s while
  the real gate exits non-zero, signature byte-identical across all four.
* The `#84` fix: exit 2 with four distinct block hashes, and the negative (clean
  tree still a fast cached pass) intact.
* `#84`'s file counts: 71 → 74, the three added files named, over-broad
  `\.(toml|hcl)$` = 75.
* `docs/provider-0.9.8.lock.hcl` is in no gate's scope — three commands, all
  exit 0 on a deliberately misformatted block.
* `static-gate.sh:61-63` true as written — 3.316s against a 0.039s warm path.
* RX-F7, all four arms, both directions.
* RX-F3, both hooks, unquoted 127 / quoted 0 against a real repo at a space path.
* RX-F4, 6 groups and 100 ids at HEAD and at `053869f`, with an empty diff
  between.
* RX-F8, the die at `:51`, `:52` at `053869f`, one grep hit.
* RX-F9, `BANNED` span 76-83 and 6 members out of the AST.
* RX-F6 as history: three `closed` events at `commit_id: null`, three
  `referenced` events with real commits, three in-tree artifacts each.

**Refuted (1)**

* `.claude/settings.json:28-30`. Not reopened, no deny entries added. The repo
  states in two places — `CLAUDE.md:91-96` and `cve-triage/SKILL.md:36-39`, both
  re-read at their **new** numbers — that the list is a prefix matcher and a
  guardrail, not a boundary. Adding entries adds surface to an enumeration the
  repo documents as unclosable.

**Downgraded (2)**

* **RX-F6 → no action.** `docs/review-2026-08-31/verify/F-lows.md` had it at
  low → nit on the argument that the record exists in GitHub comments. It is
  weaker than that now: the record also exists in the repository, and the commit
  that filed the finding is the one that put it there. Widening
  `CLAUDE.md:68-77` was rejected — its subject is the tracking status of two
  files, and a list of two of the many places a decision can land invites the
  next reader to think the list is closed.
* **`#84`'s residue.** The runtime defect cannot reach `master`: `just check`
  runs in both pipelines from a clean checkout with no state file and catches
  all four mutations. What this commit removes is a comment-accuracy defect in
  the one file whose reason for existing is `tests/conftest.py:7`.

**Deferred (1)**

* **RX-F2**, five `CLAUDE.md` anchors, two of which point into `scripts/lint.sh`
  and `scripts/image-scan.sh` while other lanes edit them. `#88` stays open for
  it. Their post-branch positions are in §2.3.
