# F — lows and nits, confirmed

Confirmer, Phase 3 · target `672a500` · worktree
`scratchpad/rv3`, execution in `mktemp -d` copies. `RX-F1` belongs to another
verifier and is untouched here.

Verdicts: `RX-F2` **DOWNGRADED** medium→low · `RX-F3` **CONFIRMED** low ·
`RX-F4` **CONFIRMED** low · `RX-F5` **REFUTED** · `RX-F6` **DOWNGRADED**
low→nit, and it is two issues not three · `RX-F7` **CONFIRMED** low, mechanism
corrected · `RX-F8` **CONFIRMED** nit · `RX-F9` **CONFIRMED** nit, count
corrected (six, not four).

---

## RX-F2 — five CLAUDE.md anchors have drifted · DOWNGRADED medium → low

Every one of the five is wrong at `672a500`, and every one was right at
`059c1ca` (`git show 059c1ca:<file>` checked per row). The claim is true as
stated.

| CLAUDE.md | says | correct at `672a500` | at `059c1ca` |
|---|---|---|---|
| `:39` | `scripts/lint.sh:34-77` | `workflows_carry_no_logic()` is `:40-114`; `:34` is a lone `#`, `:77` is blank inside the function's comment body | `:34` was the `workflows_carry_no_logic() {` line |
| `:85` | `scripts/image-scan.sh:92` | flag test at `:93`; `:92` is blank. Other `write-baseline` hits: `:5`, `:85`, `:106` | `:92` was the flag test |
| `:99` | `Containerfile:45` `BASE_DIGEST` | `ARG BASE_DIGEST=sha256:827d…` at `:80` (a second bare `ARG BASE_DIGEST` at `:87`); `:45` is mid-comment about the UBI base | `:45` |
| `:100` | `Containerfile:62` `TOFU_RPM_SHA256` | `:97` | `:62` |
| `:101` | `Containerfile:69` `PROVIDER_SHA256` | `:104` | `:69` |

Sound, re-verified: `CLAUDE.md:102` → `docs/provider-0.9.8.lock.hcl:8` is the
`h1:` line; `:47` quotes `tests/conftest.py:7` verbatim; `:49`'s `gate()` `:44`
and `require()` `:61` both hold; `:82` → `render.py:61`.

**Why low, not medium.** The defect is documentation-only and self-correcting at
read time: each drifted anchor is written beside a unique grep target
(`workflows_carry_no_logic`, `--write-baseline`, `BASE_DIGEST`,
`TOFU_RPM_SHA256`, `PROVIDER_SHA256`), so the worst outcome is one wasted read
followed by one `grep`. Nothing here changes what an agent *does* — the pins
cannot be edited at a comment line. Reachability is maximal (CLAUDE.md loads
every session) and consequence is near zero; on this review's scale that is low.
The finder's own fix is the right one and costs nothing: name the symbol, drop
the number, exactly as `scripts/lint.sh:129-134` already argues for cross-file
citations.

## RX-F3 — unquoted `$CLAUDE_PROJECT_DIR` · CONFIRMED low

Reproduced, not reasoned. `672a500`'s two hooks copied under
`/tmp/tmp.ZCWkjgfH78/space dir`, `CLAUDE_PROJECT_DIR` set to that path:

```
sh -c '$CLAUDE_PROJECT_DIR/.claude/hooks/session-probe.sh'    -> 127
sh -c '"$CLAUDE_PROJECT_DIR/.claude/hooks/session-probe.sh"'  -> 0
sh -c '$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh'      -> 127
```

`.claude/settings.json:8` and `:19` are the two unquoted command strings.
Exit 127 is not 2, so the Stop hook produces a non-blocking error rather than a
block — the gate is absent and nothing reaches the model. The finder's wording
"treats that as a pass" is loose but the consequence is as described.

Latent, and low for the reason the finder gives: no path in play has a space,
and the `sh -c` result models the harness rather than proving it. Fix is four
characters.

## RX-F4 — `cve-triage` understates what `--write-baseline` destroys · CONFIRMED low

`.claude/skills/cve-triage/SKILL.md:26-30` says "five groups" and names five,
then "The 99 accepted IDs survive". Measured against `docs/cve-baseline.json`:

* at `672a500` — `.rationale | keys | length` = **6**, `.accepted | length` = **100**
* at `053869f`, the commit that added the skill — **6** and **100**

So this is an authoring error, not drift, exactly as claimed. The unnamed sixth
group is `CVE-2026-11979` — the one acceptance tied to `BASE_DIGEST`, which is
the entry an agent re-triages after a re-pin. Fix is one line.

## RX-F5 — the denied spellings are not the likely ones · REFUTED

The sub-facts hold: `.claude/settings.json:28-30` denies exactly
`./scripts/…`, `scripts/…` and `bash scripts/…`, so `sh scripts/…`,
`bash ./scripts/…` and `cd scripts && ./image-scan.sh …` are uncovered, and
nothing denies `Write`/`Edit` on `docs/cve-baseline.json`.

It is not a defect. `CLAUDE.md:90-95` states the incompleteness in the file
itself — prefix not pattern, "an absolute path, or a new `just` recipe … routes
around it silently", "a guardrail against the obvious helpful action, not a
boundary" — and `cve-triage/SKILL.md:35-38` says the same. `_PLAN.md:88-89`
carries it forward: "this rule is the boundary, not the deny entry." The
finder's own Q3 answer concedes it. Enumerating two more spellings adds surface
to a list the repo has already documented as unclosable, which is the cost this
project treats as a defect in its own right.

One residue, below filing: `CLAUDE.md:92-93` — "the rule covers the spellings
used from the repo root and nothing else" — reads as covering *all* repo-root
spellings when it covers three of them. Wording, not mechanism. Not filed.

## RX-F6 — three issues closed with no repo artifact · DOWNGRADED low → nit; two, not three

The count is wrong. **#50 does have a repo reference**: `26627ad`'s body names
it — "*#50 — `Bash(just:*)` no longer blanket-allows the mutating recipes.
Applied to `.claude/settings.local.json`, which is untracked, so it appears in no
diff here and was closed by hand.*" `26627ad` is an ancestor of `672a500`. The
finder asserts `git log --all` over the range holds no `#50` reference and then
quotes that very line. #50 is also precisely the case `CLAUDE.md:75-76` already
describes: a change confined to `settings.local.json`.

Two remain: **#47** landed in `~/.claude/local-plugins/ty-lsp/` (its closing
comment: "No repo commit, as the issue predicted — LSP servers can only be
declared by a plugin, and the absolute path is why this lives under
`~/.claude`") and **#52** is a user-level `enabledPlugins` decision. Both
`CLOSED`/`COMPLETED` with `commit_id: null`. Those two are genuinely outside the
project directory, wider than `CLAUDE.md:67-76` describes.

Nit, not low. The section's subject is the tracking status of two files inside
`.claude/`; it never claimed to enumerate every place work can land. The record
for #47 and #52 is the issue comment, which exists and is complete. Two more
sentences in a file at 128 of its 200-line ceiling buys a reader nothing they
cannot get from the issue.

## RX-F7 — an unwritable `.cache/` downgrades a block to a non-blocking error · CONFIRMED low, mechanism corrected

Reproduced in a `mktemp -d` copy of `672a500` with `.venv` symlinked in, an
unused `import os` appended to `orchestrator/marker.py` so `just lint` fails, and
the state file removed:

```
chmod 700 .cache -> hook exit 2, state written "block a4921c24…"
chmod 500 .cache -> hook exit 1 on the same class of broken tree
  static-gate.sh: line 87: …/.cache/static-gate.state: Permission denied
```

**Correction to the mechanism.** The finder blames `mkdir -p` at `:86`.
`mkdir -p` on an existing directory succeeds whatever its mode; the failing
command is the `printf … > "$STATE"` redirect at `:87`. Everything downstream is
as claimed: `record block` is the first command of `fail()` (`:91`), so under
`set -e` the script exits with that status and never reaches `exit 2` at `:94`.

**It is slightly worse than the finding says.** `fail()` dies *before* its two
`printf … >&2` at `:92-93`, so the lint output is lost as well as the block —
the model gets neither the diagnostic nor the stop. `record pass` at `:105` has
the same exposure on the healthy path, harmlessly.

Low: nothing in normal use produces a read-only `.cache/`. `record block || true`
is the fix, one word.

## RX-F8 — `delivery/SKILL.md:20` cites `bundle.sh:52` · CONFIRMED nit

`scripts/bundle.sh`: the `die` is `:51`, `:52` is the `done`. At `053869f`, the
commit that added the skill, the `die` was `:52` — so the citation was correct
when written and has since drifted by one. Same class as RX-F2, one line.

## RX-F9 — `CLAUDE.md:49-51` lists three banned skip forms · CONFIRMED nit, count corrected

`tests/test_gates.py:76-83` — `BANNED` holds **six**, not the four the finder
states: `pytest.skip`, `pytest.importorskip`, `pytest.xfail`,
`pytest.mark.skip`, `pytest.mark.skipif`, `pytest.mark.xfail`. All six are
enforced by the single `if name in BANNED` filter at `:92`.

`CLAUDE.md:50-51` names three, omitting `pytest.xfail`, `pytest.mark.skipif` and
`pytest.mark.xfail`. The finder's reasoning for why `pytest.mark.skipif` is the
costly omission checks out: `conftest.gate()` returns exactly that at
`conftest.py:53` on the available path, so a reader has a live example in front
of them of the form the AST walk rejects.

---

## Method

* All anchors re-verified at `672a500` in the detached worktree, including the
  ones these findings assert are wrong, and against `059c1ca` / `053869f` where
  the finding claims a "correct when written" baseline.
* RX-F3 and RX-F7 reproduced in `mktemp -d` copies; both exit statuses observed,
  neither inferred.
* Nothing in the repo or in either `.claude/` tree was modified.
  `scripts/image-scan.sh --write-baseline` was not run. The rig was not touched.
