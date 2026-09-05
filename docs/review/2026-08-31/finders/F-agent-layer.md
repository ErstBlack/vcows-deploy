# F — the agent layer

`CLAUDE.md`, `.claude/settings.json`, `.claude/hooks/*.sh`, `.claude/skills/*` ·
`672a500` · 2026-08-31 · non-blocking, file as issues.

## Summary

* **The static gate fails open on a file class.** Its signature covers
  `.py .sh .tf .ya?ml Containerfile`, so a turn confined to `pyproject.toml`
  (ruff's and ty's config) or a `.hcl` file (`tofu fmt -recursive` does check
  `.tftest.hcl`) leaves it unchanged. Reproduced: exit 0 in 0.043s on a tree
  where `./scripts/lint.sh` exits 1 — `conftest.py:7`'s own shape.
* **Five CLAUDE.md anchors no longer resolve.** All were correct at `059c1ca`;
  six commits since moved them, the three `Containerfile` pins by 35 lines each.
* **The deny rule is honestly described** — CLAUDE.md and `cve-triage/SKILL.md`
  both call it a guardrail not a boundary and name the same route-arounds; the
  gap is that the three denied spellings are not the three most likely.
* **The skills contradict no repo rule**, and **#47, #50 and #52 have no repo
  artifact and no commit reference**, by design — all landed in `~/.claude/`,
  further out than CLAUDE.md's `.claude/` rule describes.

## Q1 — the anchor table

| CLAUDE.md | anchor | at `672a500` |
|---|---|---|
| :13 | `pyproject.toml:26-34` | marginal — trap :30-33, lockfile sentence :34-36 |
| :23 / :28 / :34 | `fake_libvirt.py:25`, `README.md:7`, `marker.py` | yes / yes / yes |
| :39 | `scripts/lint.sh:34-77` | **NO** — `workflows_carry_no_logic()` is :40-114 |
| :47 | `tests/conftest.py:7` | yes, quoted verbatim |
| :49 | `gate()` `:44`, `require()` `:61`, `test_gates.py` AST walk | yes, yes, yes (`:76-98`) |
| :53 | `conftest.py:37` `VCOWS_GATES` | yes |
| :54 / :55 | five names closed; no whitespace strip | yes — `test_gates.py:27`, enforced `:101`/`:119`/`:130-140` |
| :69 | `settings.json` committed | yes |
| :71 | `settings.local.json` ignored by `~/.config/git/ignore` | yes — `check-ignore -v` -> that file, line 1 |
| :82 | `render.py:61` | yes — `"uri": connection_uri(target, "sshcmd")` |
| :85 | `scripts/image-scan.sh:92` | **NO** — flag test at `:93`, `:92` blank |
| :99 / :100 / :101 | `Containerfile:45/:62/:69` — the three pins | **NO** — `:80` / `:97` / `:104` |
| :102 | `docs/provider-0.9.8.lock.hcl:8` | yes, the `h1:` |
| :104 | `dependabot.yml` not pointed at the Containerfile | yes, in its own header |
| :126 | `just lint` is six gates | yes — `lint.sh:126,127,149,150,159,160` |

Five wrong, one marginal, sixteen sound.

## Findings

### RX-F1 — the static gate's signature omits files that change what `just lint` reports
**high** · high confidence · `.claude/hooks/static-gate.sh:52-58`, comments `:47-51` and `:61-63`
`signature()` filters `git ls-files` through
`grep -zE '\.(py|sh|tf|ya?ml)$|(^|/)Containerfile$'`. Outside it: `pyproject.toml`
(`[tool.ruff]`, `[tool.ruff.lint.per-file-ignores]`, `[tool.ty.*]` at `:123-170`,
ruff's and ty's own config); `*.hcl` (`tofu fmt -check -recursive`, `lint.sh:150`,
does check `tests/libvirt-module.tftest.hcl`); `justfile`. With the previous
verdict `pass`, `:70-80` short-circuits and neither gate runs.

Reproduced in a `mktemp -d` copy of `672a500` — run 1: hook exit 0, 3.181s, state
`pass 1d0b99d3…`; then
`sed -i 's/^line-length = 88$/line-length = 40/' pyproject.toml` and
`./scripts/lint.sh` -> exit 1; run 2: hook exit 0 in **0.043s**, state
`pass 1d0b99d3…`, byte-identical. For the `.hcl` half: one unformatted `variable`
block prepended to `tests/libvirt-module.tftest.hcl` makes
`tofu fmt -check -recursive .` exit 3 and name the file, while the regex rejects `.hcl`.
**Why it matters:** the hook exists because `053869f` measured that Bash-written
edits reached no gate. Same hole, different axis — not which tool wrote the file
but which file was written. The header at `:61-63` asserts the opposite:
*"Failing open means running the gate, never skipping it."* A rule false in its
own docstring is worse than no rule (`conftest.py:7`), and `672a500` is itself a
`pyproject.toml`/`justfile` commit. **Fix:** add `|\.(toml|hcl)$|(^|/)justfile$`
to the alternation and correct `:47-51` to "every file the six gates read **or
are configured by**" — one alternation and two comment lines, and those four
files are rarely touched, so the extra 3.2s runs cost close to nothing.

### RX-F2 — five CLAUDE.md anchors have drifted since the file was written
**medium** · high confidence · `CLAUDE.md:39, :85, :99, :100, :101`
All five were correct at `059c1ca` (`git show 059c1ca:<file>` puts the three ARGs
at `:45/:62/:69`, the lint function at `:34-77`, the flag test at `:92`).
`053869f` and `c124ffe` added 35 lines above the pins; `0355d59` and `2b20608`
moved `lint.sh`; `0355d59` moved `image-scan.sh` by one. CLAUDE.md was not
updated. Now `grep -n 'ARG BASE_DIGEST=' Containerfile` -> 80,
`grep -n workflows_carry_no_logic scripts/lint.sh` -> 40,
`grep -n write-baseline scripts/image-scan.sh` -> 5, 85, 93, 106.
`Containerfile:45` sits mid-paragraph in the UBI comment; `lint.sh:77` is inside
the `commands()` generator.
**Why it matters:** CLAUDE.md loads into every session here and sells itself
(`:8`) as "one rule, an anchor, and the reason the anchor is worth opening". This
is the defect `c124ffe` fixed *inside* `scripts/lint.sh`, whose comment at
`:129-134` says a line number pointing into another file "goes stale on every
edit above the target" — applied to the lint script and not to the file that now
carries the most cross-file line numbers in the repo. **Fix:** for the four pins do
what `lint.sh:129-134` argues — name the `ARG`, drop the number; each is a unique
`grep` target. Five edited lines, and it removes surface.

### RX-F3 — unquoted `$CLAUDE_PROJECT_DIR`: a path with a space removes both hooks
**low** · high confidence · `.claude/settings.json:8` and `:19`
`672a500` copied to `/tmp/space dir test`:
`sh -c '$CLAUDE_PROJECT_DIR/.claude/hooks/static-gate.sh'` -> exit 127; the same
string quoted -> exit 0. For `Stop`, only exit 2 blocks; 127 is a non-blocking
error, so the gate is absent for the whole session and nothing reaches the model.
Low because no path in play has a space, not because the failure is benign.
**Fix:** quote both command strings — four characters.

### RX-F4 — `cve-triage` understates what `--write-baseline` destroys
**low** · high confidence · `.claude/skills/cve-triage/SKILL.md:26-30`
It says five rationale groups and "the 99 accepted IDs survive". The file holds
**six** and **100**, and held both at `053869f`, the commit that added the skill —
an authoring error, not drift; `053869f`'s body repeats both wrong figures.
`git show 053869f:docs/cve-baseline.json | jq '.rationale|keys'` -> six. The
missing group is `CVE-2026-11979`, the one entry tying an acceptance to
`BASE_DIGEST`. The skill's case against the flag is quantitative — how much
reasoning it deletes — so undercounting the base-pin group weakens exactly the
entry an agent triages after a re-pin. **Fix:** one line.

### RX-F5 — the denied spellings are not the likely ones
**low** · medium confidence · `.claude/settings.json:28-30`; `CLAUDE.md:90-95`
Covered: `./scripts/…`, `scripts/…`, `bash scripts/…`. Uncovered and equally
natural: `sh scripts/image-scan.sh --write-baseline`, `bash ./scripts/… ` (entry 3
lacks the `./`), and `cd scripts && ./image-scan.sh --write-baseline`. Nothing
denies `Write`/`Edit` on `docs/cve-baseline.json` either — but `cve-triage:67`
*instructs* a hand-edit there, so a deny would block the right path to stop the wrong one.
Prefix semantics come from `26627ad`'s body, measured there with a throwaway
`Bash(echo vcows-deny-probe:*)`: `echo vcowsprobe hello` denied,
`echo vcowsprobeXYZ` allowed — an argument boundary, not a character prefix. Not
re-measured; both probes are forbidden here. **Q3's answer:** `CLAUDE.md:93-95` and `cve-triage:35-38` both say
prefix-not-pattern, both name the absolute path and the new `just` recipe, and
both call it a guardrail rather than a boundary. **It is a guardrail, and
CLAUDE.md says so.** The only gap is that `bash ./scripts/…` sits one character
from a covered spelling. **Fix:** two array entries; do not chase completeness,
which the file already says is unreachable.

### RX-F6 — three issues closed with no repo artifact; the `.claude/` rule is narrower than the practice
**low** · high confidence · `CLAUDE.md:67-76`
**Q5's answer: nothing in the repo closes #47 or #52**, nor #50. `git log --all`
over `4eb378b..672a500` holds no `#47`/`#50`/`#52` reference, and
`gh api repos/:owner/:repo/issues/{47,50,52}/timeline` returns
`{"event":"closed","commit_id":null}` for all three. Each was closed by hand, by
design: #47 — *"No repo commit, as the issue predicted — LSP servers can only be
declared by a plugin"* (it landed in `~/.claude/local-plugins/ty-lsp/`); #52 —
*"Disabling an MCP server is user-level config (`~/.claude/settings.json`
`enabledPlugins`)"*; #50 per `26627ad` — *"Applied to
`.claude/settings.local.json`, which is untracked… closed by hand."* Corroborated
for #52 by this session's tool roster: no GitHub MCP tools, `context7` present.
CLAUDE.md names one untracked file as the surface that "leaves no artifact". Two
of these three landed outside the project directory entirely, so an agent
following the rule's own remedy (`git check-ignore -v`) still finds nothing, and
a fresh clone holds no trace of any of the three. **Fix:** widen the second
paragraph to name `~/.claude/settings.json` `enabledPlugins` and
`~/.claude/local-plugins/`, and say the issue comment is the record of record
there — two sentences. Do **not** mirror the local config into the repo; it
carries machine-specific absolute paths.

### RX-F7 — an unwritable `.cache/` downgrades a block to a non-blocking error
**low** · high confidence · `.claude/hooks/static-gate.sh:84-95`
`fail()` calls `record` before `exit 2`; `record` runs `mkdir -p`, and under
`set -e` a failure there ends the script with `mkdir`'s status. Scratch copy with
a broken `orchestrator/marker.py`: `chmod 500 .cache` -> exit 1; `chmod 700`, same
tree -> exit 2. `:12-14` says exit 2 exists so a break is fixed in the same turn;
exit 1 routes stderr to the debug log, not the model. **Fix:** `record block || true`.

### RX-F8 / RX-F9 — two stale counts
**nit** · `.claude/skills/delivery/SKILL.md:20` cites `bundle.sh:52`; the `die` is
`:51` (`:52` is the `done`), correct at `053869f`. Everything else in that skill
verified: `gzip -9 -n` at `bundle.sh:88`, `.cache/delivery` at `:67`,
`.cache/scan/{trivy.json,sbom.spdx.json}` at `image-scan.sh:73-74`, `source_rpms`
at `container/manifest.py:149`, `licenses/dmacvicar-libvirt/`, `docs/ci.md:170`.
**nit** · `CLAUDE.md:49-51` names three banned skip forms; `tests/test_gates.py:76-81`
`BANNED` holds four — it also bans `pytest.mark.skipif`, the one a reader is
likeliest to think is allowed, because `conftest.gate()` returns one at `:53`.

## Checked and sound

* **`session-probe.sh`.** Exits 0 unconditionally (`:36`), silent on the healthy
  path, and resolves `tofu` through the PATH `scripts/lib.sh` builds rather than
  testing `.tools/bin/tofu` on disk — `26627ad`'s recorded correction to #51's own
  spec, and right: this box has `/usr/bin/tofu` and nothing in `.tools/bin`. No
  fail-open; a missing or broken `.venv/bin/python` fires the reporting path. Its
  only exposure is RX-F3.
* **`static-gate.sh` NUL-safety and failure handling.**
  `git ls-files -z | grep -z | sort -z | xargs -0` is correct end to end. An empty
  `cur` falls through to running both gates (`:66`, `:85`); a corrupt state file
  leaves `sig` empty and also falls through; `|| cur=""` does suppress `errexit`.
  The block-once design at `:24-29` behaves as described, and the hooks are
  themselves linted (`lint.sh:159` globs `.claude/hooks/*.sh` into shellcheck).
* **Q4: skills against repo rules.** None can lead to `--write-baseline`
  (`cve-triage:13-40`, `provider-bump:67` forbid it by name). `uv sync` appears
  once under `.claude/`, in `session-probe.sh:24`, forbidding it; no `Co-Authored-By`
  anywhere. `cve-triage:67-70` prescribes the hand-edit *with*
  `count`/`why`/`recheck`, matching `CLAUDE.md:78-88`, so it cannot lead to a
  mechanical append. `provider-bump`'s four places match
  `scripts/verify-provider.sh:12-15` verbatim; the ci.yml cache key is as quoted
  at `:77`, with the same reasoning at `:70-72`.
* **The five-name gate set is enforced, not just claimed** — `test_gates.py:101`
  fails on any `gate()`/`require()` string outside `KNOWN`, `:119` proves each is
  demandable. `git check-ignore -v` confirms `settings.local.json` is ignored by
  `~/.config/git/ignore:1` alone, as `CLAUDE.md:71-73` says.

## Not checked

* Whether the permission matcher behaves as `26627ad` measured — both probes are
  forbidden here, so RX-F5 rests on that recorded measurement. Likewise whether
  the harness quotes hook command strings: RX-F3 assumes not, which the `sh -c`
  result models but does not prove.
* Skill invocation — whether the three fire on their frontmatter triggers.