# Review — `lane/drift`

Input: `git diff origin/master...lane/drift` and nothing else. Two commits of
change plus this record. `origin/master` is `80b1706`.

| | |
|---|---|
| `cd75cb8` | issue 90's outstanding items, and three sites it does not name |
| `640bfdf` | `#88`'s `RX-F2` — the five `CLAUDE.md` anchors |

Eight files changed outside the two plans: **+29 / −20**. Nothing executable
changed. The plans are `docs/archive/plans/issue-90.md` (230 lines) and
`docs/archive/plans/issue-88-rx-f2.md` (174).

---

## Lens 1 — did it do what the plan said?

### `cd75cb8` against `docs/archive/plans/issue-90.md` §5

The plan tabulates seven edits across seven files. The diff is seven files, and
every edit matches the table:

| plan §5 says | diff |
|---|---|
| `entrypoint.py:189` ``cli.py:670``'s → ``orchestrator/cli.py``'s | `-1/+1`, exactly that |
| `findings.md:419` "copy commands" → "copy commands or claims" | `-1/+1` |
| `image.yml:60` `source_revision (lib.sh:129)` → `source_revision in lib.sh` | `-1/+1` |
| `image-scan.sh:80-81`, **same two lines, same file length** | `-2/+2`, `wc -l` 190 before and after |
| `cve-triage/SKILL.md:30-31`, same two lines | `-2/+2` |
| `delivery/SKILL.md`, **+5 lines** | `-2/+8`, i.e. +5 net + the reworded 2 |
| `CLAUDE.md:48-49` and `:54` | `-6/+6` |

Two things the plan promised and the diff honours that are easy to miss:
`scripts/image-scan.sh` holds its line count because `tests/test_scripts.py`
pins five of its lines, and the `cve-triage` edit holds its two lines because
`docs/review/agent-layer/REVIEW.md` §2.3 already recorded that file's anchors
for later readers.

The `delivery/SKILL.md` edit is the one item in `cd75cb8` that issue 90 never
names. It was handed here explicitly: `docs/review/scan-bundle/REVIEW.md:271`
F3 records that the skill "describes `bundle.sh`'s precondition as a check for
three files with one `die` message. There are now four checks and two more
`die`s … Its `:25` claim goes from false to true", and marks it out of that
lane's permitted set.

**Not in the plan and not in the diff:** `tests/libvirt-module.tftest.hcl:314`,
which cites `main.tf:205` for the non-null bridge arm at `:204`. The plan §5
records the measurement and the decision to leave it — `main.tf` has not moved
since `4eb378b`, so it is an authoring error from `454ee7c` rather than drift,
it is in neither issue, and `lane/rhel-firmware` may still move that file. The
commit body says the same. Consistent.

### `640bfdf` against `docs/archive/plans/issue-88-rx-f2.md` §5

The plan lists five line edits in one file. The diff is `CLAUDE.md`, `+5/−5`,
those five lines, file length unchanged. Two de-anchored, three re-anchored,
matching §2's decision column row for row.

### Commit bodies

Both are imperative, sentence-length subjects, bodies stating what was measured,
no `Co-Authored-By`. `cd75cb8` carries `Closes #90`; `640bfdf` carries
`Closes #88`. Neither body contains a closing keyword adjacent to any other
issue number — checked, because a PR body's *"for whoever closes #90"* is what
closed issue 90 by accident on 2026-08-31.

---

## Lens 2 — is every new number one this lane measured at HEAD?

This replaces "does the test have teeth" for a documentation lane. **Three
numbers were written, nine were removed across eight citations, six were left in
place, and two claims were restated in prose.** Every one below was produced by
a `grep -n` run in this worktree
at `80b1706`, captured in `docs/review/drift/reverify/anchors.txt` and
re-runnable from it.

### Numbers written

| written | the `grep -n` | output |
|---|---|---|
| `CLAUDE.md:100` → `Containerfile:80` | `grep -n 'BASE_DIGEST' Containerfile` | `68:# and compare .Digest against BASE_DIGEST…`, **`80:ARG BASE_DIGEST=sha256:827d37bc…`**, `87:ARG BASE_DIGEST`, `194:`, `212:` |
| `CLAUDE.md:101` → `Containerfile:97` | `grep -n 'TOFU_RPM_SHA256' Containerfile` | **`97:ARG TOFU_RPM_SHA256=547fe4544d…`**, `132:` |
| `CLAUDE.md:102` → `Containerfile:104` | `grep -n 'PROVIDER_SHA256' Containerfile` | **`104:ARG PROVIDER_SHA256=061e518785…`**, `144:`, `195:` |

Three numbers, and only three. Every other number this lane touched was removed
rather than corrected, which is lens 2's real answer: **the smallest set of
numbers that answers the question, and no others.**

### Numbers removed, and the grep that made removal safe

| removed | the `grep -n` | output | hits |
|---|---|---|---|
| `CLAUDE.md:39` `scripts/lint.sh:34-77` | `grep -n 'workflows_carry_no_logic' scripts/lint.sh` | `40:workflows_carry_no_logic() {`, `199:    gate "workflows carry no logic" workflows_carry_no_logic` | 1 definition |
| `CLAUDE.md:86` `image-scan.sh:92` | `grep -n 'write-baseline' scripts/image-scan.sh` | `:5`, `:100`, `108:    if [ "${1:-}" = "--write-baseline" ]; then`, `:121`, `:185` | 1 branch |
| `CLAUDE.md:49` `(`:44`)`, `(`:61`)` | `grep -n '^def ' tests/conftest.py` | `37:def _parse(`, `46:def demanded(`, **`50:def gate(`**, **`67:def require(`** | 1 each |
| `CLAUDE.md:54` `conftest.py:37` | `grep -n 'GATES' tests/conftest.py` | `9:`, `10:` (prose), **`43:GATES = _parse(os.environ.get("VCOWS_GATES", ""))`**, `47:` | — |
| `entrypoint.py:189` `cli.py:670` | `grep -n 'os.umask' orchestrator/cli.py` | `:153`, `:238`, `:520`, `:761` (all prose) and **`763:    os.umask(0o077)`** | 1 call |
| `image.yml:60` `lib.sh:129` | `grep -n 'source_revision' scripts/lib.sh` | **`136:source_revision() {`** | 1 |
| `image-scan.sh:80` `README.md:262-264` | `grep -n '^## ' README.md` | 11 headings, **`275:## Delivering it`**; the block is `:277-281` | 1 heading |
| `cve-triage/SKILL.md:30` "100 accepted IDs" | `jq '.accepted\|length' docs/cve-baseline.json` | **`99`** | — |

### Numbers left in place, re-measured rather than assumed

`CLAUDE.md:13` → `pyproject.toml:26-34`; `:23` → `fake_libvirt.py:25`;
`:28` → `README.md:7`; `:47` → `tests/conftest.py:7`; `:83` → `render.py:61`;
`:103` → `docs/provider-0.9.8.lock.hcl:8`. All six re-read. `pyproject.toml:26-34`
names a block that runs to `:36` and is recorded as a non-goal in both plans:
it predates this campaign and is in neither issue.

### The automated re-check

A 37-assertion script was run after the edits, its source in the lane transcript.
It does not assert a hardcoded table — it **parses every `path:N` citation out of
the live files**, resolves it, and requires that a backticked token or quoted
phrase from the same sentence appears in the cited line. It also asserts that
each de-anchored citation's named symbol still resolves to exactly one line, and
that none of the removed numbers came back. `ALL PASS`, exit 0.

Its teeth were proved in both directions rather than assumed:

```
CLAUDE.md `Containerfile:80` -> `:81`
  FAIL  CLAUDE.md -> Containerfile:81 -- nothing in the citing sentence appears at Containerfile:81
CLAUDE.md re-anchored back to `scripts/lint.sh:40-124`
  PASS  CLAUDE.md -> scripts/lint.sh:40-124  -- matched 'workflows_carry_no_logic'
  FAIL  CLAUDE.md no longer contains 'lint.sh:'
```

The second is the one worth noting: a **correct** re-anchoring still fails,
because this lane's decision was that the number should not be there. The script
enforces the decision, not just the arithmetic.

An earlier version of the script asserted a hardcoded expectation table and
passed the first teeth check. That is recorded because it is the failure mode a
documentation lane is most likely to ship: a verifier that agrees with itself.

---

## Lens 3 — what moved?

**Nothing. A documentation-only change moves nothing, and this lens is not
empty because of that — it is the finding.** Stated explicitly rather than left
blank:

* **No executable line changed.** Every hunk outside the two plans is a comment
  body, a Markdown paragraph, or a YAML comment. `ty` is clean and the suite is
  `439 passed, 25 skipped` — the baseline, to the test.
* **No file this lane edited changed length except two.**
  `.claude/skills/delivery/SKILL.md` gains 5 lines after `:24`, so a citation at
  or below `:24` is unaffected and `:25` and below shift `+5`. Nothing in the
  repo cites that file by line: `grep -rn 'delivery/SKILL\.md:[0-9]'` over live
  files returns nothing — every hit is in `docs/archive/plans/` or `docs/review/` —
  and `8915cd7` already removed the only number *inside* it.
  `docs/archive/plans/issue-88-rx-f2.md` and `docs/archive/plans/issue-90.md` are new files.
* `CLAUDE.md`, `container/entrypoint.py`, `.github/workflows/image.yml`,
  `docs/findings.md`, `.claude/skills/cve-triage/SKILL.md` and
  `scripts/image-scan.sh` are all the same length after as before. This was a
  constraint, not an accident: `tests/test_scripts.py` pins five
  `image-scan.sh` lines and that file's own comments pin four more, so the
  `README.md` citation was rewritten to fit its existing two lines.
* **What this lane removes is future movement.** Five citations went from a
  number to a name. Those five can no longer be invalidated by an edit above
  them, which is five fewer entries in the next lane's "what moved" table.

### Anchors pointing into `lane/rhel-firmware`'s files

`#75` is still in flight and may move `main.tf`, `variables.tf` and
`smoke-libvirt.sh`. **This lane wrote no citation into any of the three.** What
already points there, for a follow-up to re-check when `#75` lands:

| citer | target | measured at `80b1706` |
|---|---|---|
| `tests/libvirt-module.tftest.hcl:74` | `main.tf:88` | `type = "kvm"` — holds |
| `tests/libvirt-module.tftest.hcl:313` | `main.tf:25`, `main.tf:34` | `count = var.base_volume.create ? 1 : 0`, `base_path = …` — both hold |
| `tests/libvirt-module.tftest.hcl:314` | `main.tf:205` | **wrong**: `:205` is `}`, the bridge arm is `:204`. Authoring error, not drift; not in either issue; not fixed |
| `tests/libvirt-module.tftest.hcl:349` | `main.tf:34` | holds |
| `tests/libvirt-module.tftest.hcl:400`, `:441` | `main.tf:116` | `firmware = each.value.firmware == "efi" ? "efi" : null` — holds |
| `tests/libvirt-module.tftest.hcl:39` | `destroy.py:440-445` | the `owned` set — holds |
| `orchestrator/…/tofu/variables.tf:10` | `schema.py:198-211` | `def connection_uri` and its docstring — holds |
| `scripts/smoke-libvirt.sh:454` | `lib.sh:16` | `set -euo pipefail` — holds |

Six of the eight point *out of* those files and are unaffected by `#75`. Two
point *into* `main.tf` from `tests/libvirt-module.tftest.hcl`, and are what a
follow-up must re-measure.

---

## Ledger

| # | item | verdict |
|---|---|---|
| L1 | `cd75cb8` matches `docs/archive/plans/issue-90.md` §5, seven files, seven edits | **pass** |
| L2 | `640bfdf` matches `docs/archive/plans/issue-88-rx-f2.md` §5, five lines, one file | **pass** |
| L3 | Three numbers written, each backed by a quoted `grep -n` at `80b1706` | **pass** |
| L4 | Nine numbers or counts removed across eight citations, each backed by a hit count that makes removal safe | **pass** |
| L5 | Six numbers left in place were re-measured, not assumed | **pass** |
| L6 | Nothing was copied from either issue body, from `docs/review/2026-08-31/`, or from a lane review | **pass** — the issue's own `cli.py:705` is measured `:763`, and `#88`'s `lint.sh:40-114` is measured `:40-124` |
| L7 | The capture is re-runnable: `sed -n 's/^[$] //p' docs/review/drift/reverify/anchors.txt \| sh` | **pass**, replayed |
| L8 | The 37-assertion re-check passes and was proved to fail on both a wrong number and a restored one | **pass** |
| L9 | `just check`: six lint gates, `ty` clean, `439 passed, 25 skipped` | **pass**, baseline unchanged |
| L10 | No behaviour change; no file cited by line changed length except `delivery/SKILL.md`, which nothing cites by line | **pass** |
| L11 | `#94`, `#95`, `#96`, `#105` untouched; no closing keyword near any of them | **pass** |
| L12 | `tests/libvirt-module.tftest.hcl:314` → `main.tf:205` is wrong and is **not fixed** | **open** — authoring error, in neither issue, in a file `#75` may move |
| L13 | `CLAUDE.md:13` → `pyproject.toml:26-34` names a block ending at `:36` | **open** — predates this campaign, in neither issue |
| L14 | `CLAUDE.md:92-93`'s "the spellings used from the repo root" covers three of them | **open** — `verify/F-lows.md` recorded it below filing; still not filed |

L12, L13 and L14 are the whole of what this lane found and did not fix. None is
a citation this campaign moved, which is the boundary both plans draw.
