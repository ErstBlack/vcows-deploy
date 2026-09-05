# Issue 90 — the citations this campaign moved, re-measured at `80b1706`

Lane `lane/drift`, run last on purpose: every other lane of the 2026-08-31
campaign moved lines that this issue's own numbers point at.

## 1. Reverification verdict

**Reproduced for the two items the issue lists as unassigned, and three more the
issue does not name.** Nine of the eleven items were outstanding when the issue
was reopened; seven of those nine landed in other lanes and were re-measured
here rather than trusted:

| item | site | state at `80b1706` |
|---|---|---|
| 1 | `docs/cve-baseline.json` `note` | **landed** (`80b1706`) — reads "99 distinct ids, because 11 ids are reported against both Go binaries" |
| + | the stale `CVE-2026-58055` row | **landed** — `jq '.accepted\|index("CVE-2026-58055")'` → `null` |
| 2 | `scripts/lib.sh` `image_tag` comment | **landed** (`ea0932d`) — `:93-100` now names `inherit_errexit` |
| 3 | `.github/workflows/image.yml:60-64` | **landed** (`d1952aa`) as prose, **but its `lib.sh` number went stale after** — see below |
| 4 | `.gitlab-ci.yml:10-18` | **landed** (`d1952aa`) — `:17-20` carries the 153 MiB measurement |
| 5 | `container/entrypoint.py:189` | **outstanding** |
| 6 | `destroy.py` `preflight.py` citation | **landed** (`51114bb`) — `:476` reads ``preflight.py:23-24`` |
| 8 | `orchestrator/cli.py` EROFS comment | **landed** (`e555fe9`) — `:159-164` |
| 9 | `destroy.py` vanished-branch docstrings | **landed** (`51114bb`) |
| 10 | `orchestrator/cli.py` `cmd_version` | **landed** (`e555fe9`) — `:713` names both exceptions |
| 11 | `docs/findings.md:419` | **outstanding** |
| + | `CLAUDE.md` `gate()` / `require()` / `GATES` | **outstanding** (added by the issue's first comment) |

Item 7 is not in the issue body; the numbering runs 1-6 and 8-11.

`#88`'s RX-F2 — `CLAUDE.md`'s `lint.sh`, `image-scan.sh` and three
`Containerfile` anchors — is deliberately not in this plan. It is
`docs/archive/plans/issue-88-rx-f2.md`, on the same branch.

## 2. Anchor table

Every number below was measured with `grep -n` at `80b1706` in this worktree.
Nothing was copied out of the issue body, out of `docs/review/2026-08-31/`, or
out of a lane review. The raw capture is `docs/review/drift/reverify/anchors.txt`.

**The issue's own numbers are stale, which is the point of the issue.** It says
`os.umask(0o077)` is at `cli.py:705`; that was true at `672a500` and is `:763`
today.

| citing site | the claim | measured truth at `80b1706` | decision |
|---|---|---|---|
| `container/entrypoint.py:189` | ``cli.py:670``'s `os.umask(0o077)` | `grep -n 'os.umask' orchestrator/cli.py` → `763:    os.umask(0o077)`, and it is the only `os.umask` in the file | **de-anchor** |
| `docs/findings.md:419` | the errata covers the doc's "commands" | the table beneath it corrects a licence (§2), two versions (§13, §8), and three design claims (§4.2, §10, §5.3) — none of them commands | **re-word**, not an anchor |
| `.github/workflows/image.yml:60` | `source_revision (lib.sh:129)` | `grep -n 'source_revision' scripts/lib.sh` → `136:source_revision() {`, the only hit in the file | **de-anchor** |
| `scripts/image-scan.sh:80` | `README.md:262-264` documents `just scan` and `just bundle` | `grep -n '^## ' README.md` → `275:## Delivering it`; the three-command block is `:277-281` | **de-anchor** |
| `.claude/skills/cve-triage/SKILL.md:30` | "The 100 accepted IDs survive" | `jq '.accepted\|length' docs/cve-baseline.json` → **99** | **de-anchor** |
| `.claude/skills/delivery/SKILL.md:20-26` | `bundle.sh` checks three files; "a bundle is always a bundle *of a scanned image*" | `grep -n 'die ' scripts/bundle.sh` → `:51` (the three-file loop), **`:76`** and **`:78`** (the `PASSED` stamp and its sha256) | **restate**, not an anchor |
| `CLAUDE.md:48-49` | `conftest.gate()` (`:44`), `conftest.require()` (`:61`) | `grep -n '^def ' tests/conftest.py` → `50:def gate(`, `67:def require(` | **de-anchor** |
| `CLAUDE.md:54` | `VCOWS_GATES` (`conftest.py:37`) | `grep -n 'GATES' tests/conftest.py` → `43:GATES = _parse(...)`; `:37` is now `def _parse` | **de-anchor** |

### Re-anchor or de-anchor, and why

The rule this lane applied: **a line number earns its place only when the symbol
beside it is not a unique grep.** Five of the eight rows fail that test, and the
repo already contains the de-anchored form in two live files —
`.github/workflows/image.yml:90` ("`workflows_carry_no_logic` in
scripts/lint.sh") and `scripts/image-build.sh:14` ("It lives in
`source_revision` in lib.sh"). `8915cd7` made the same choice for
`delivery/SKILL.md`'s `bundle.sh:52`, which now carries no number at all.

* **`os.umask(0o077)`** — de-anchored. The symbol is already in the sentence and
  is the only `os.umask` in a 791-line file. The number was right at `8b24bfb`,
  wrong at `672a500` (`:705`), and wrong again today (`:763`): **three values in
  one week for a claim that never changed.**
* **`source_revision`** — de-anchored. `docs/review/shell-errexit/REVIEW.md` F1
  recommended re-anchoring to `:136`. De-anchoring subsumes that and does not
  come back: `lane/rhel-firmware` may still move `scripts/lib.sh`, and the
  function name cannot move.
* **`README.md:262-264`** — de-anchored to the section heading. `be50ae6` added
  16 lines above `README.md:69`, so **sixty** `README.md:<line>` citations across
  `docs/` went stale at once. A heading is greppable and survives insertions.
* **the accepted-id count** — de-anchored. It read 99 when `RX-F4` was filed
  against it, was corrected to 100 in `8915cd7`, and `80b1706`'s deletion of the
  stale `CVE-2026-58055` row made it 99 again. Two corrections in one week, in
  opposite directions, for a sentence whose point is *what survives*, not how
  many. The six rationale groups keep their count because the same sentence
  enumerates all six, so the number is checkable where it stands.
* **`docs/findings.md:419`** — not an anchor. One word, per
  `docs/review/2026-08-31/verify/G-lows.md:121-124`.
* **`delivery/SKILL.md`** — not an anchor. `80b1706` added a precondition the
  skill does not mention, and its `:25` sentence ("a bundle is always a bundle
  *of a scanned image*") was **false before `80b1706` and is now true but
  understated**: the gate is acceptance, not scanning.

Nothing is re-anchored in this plan. `docs/archive/plans/issue-88-rx-f2.md` re-anchors
three, which is where the distinction is worth reading.

### Measured and left alone

| claim | measured | why untouched |
|---|---|---|
| `CLAUDE.md:47` → `tests/conftest.py:7` | holds, verbatim | a quote from one line is genuinely line-specific; `lint.sh:176`, `test_gates.py:225` and `static-gate.sh:20` all cite the same line |
| `CLAUDE.md:28` → `README.md:7` | holds (`7:## Read this first: …`) | above `be50ae6`'s insertion point |
| `CLAUDE.md:83` → `render.py:61` | holds (`"uri": connection_uri(target, "sshcmd"),`) | |
| `CLAUDE.md:103` → `docs/provider-0.9.8.lock.hcl:8` | holds (`"h1:yqZeKoJ+…"`) | |
| `CLAUDE.md:13` → `pyproject.toml:26-34` | the block runs `:26-36` | `pyproject.toml` last moved at `672a500`, before this campaign. Not drift this issue covers. |
| `destroy.py:476` → `preflight.py:23-24` | holds | landed in `51114bb` |
| all 21 `lib.sh`/`bundle.sh`/`image-scan.sh` citations in `tests/test_scripts.py` | all hold | the lanes that moved those files maintained them |
| the internal `:92`, `:95-96`, `:116`, `:128`, `:169`, `:187` self-citations in `image-scan.sh` and `:85-90`, `:114`, `:119` in `bundle.sh` | all hold | written by `80b1706` against its own tree |

## 3. Corrections to the issue body

1. **Item 5's number is wrong twice over.** The body says `:705`; the first
   comment says `:705` is now `:763`. Measured here: **`:763`**. The body also
   says "`:670` is `parser.add_argument(`; `:669` is the constructor" — at
   `80b1706` `cli.py:670` is `if yes:`, inside `_confirm`. The parenthetical was
   describing a tree three merges old.
2. **The issue's "still outstanding" table is itself stale.** It lists item 1,
   item 2, item 6, item 8, item 9 and item 10 as outstanding; all six landed
   before `80b1706` and were re-measured here. The genuinely outstanding items
   were **two**, not nine.
3. **Item 3 landed but re-broke.** `d1952aa` rewrote the `image.yml:60-64`
   comment the issue asked for, then `ea0932d` moved `source_revision` from
   `lib.sh:129` to `:136` — so the number inside the *corrected* comment went
   stale. `docs/review/shell-errexit/REVIEW.md` F1 flagged it for this lane.
4. **Two sites the issue never names have drifted for the same reason**, and are
   fixed here because they are the same defect: `image-scan.sh:80` →
   `README.md:262-264` (moved by `be50ae6`) and `cve-triage/SKILL.md`'s accepted
   count (moved by `80b1706`).
5. **Item 11's `config.py` correction needs no edit.** The issue says "corrected
   citation for the chain: `config.py:44`, not `:43`". Measured: `config.py:44`
   is `"required": ["source_qcow2", "base_volume_name"]` and `:43` is
   `"additionalProperties": False`, so the correction is right — but the only
   place that carries `config.py:43` is
   `docs/review/2026-08-31/finders/G-unread-list.md:83`, a dated finder report.
   `RX-G7`'s substantive half was **refuted**; no errata row is added, so nothing
   live ever cites the chain. The correction is recorded, not applied.

## 4. The defect

Not one defect. Two mechanisms, and they want opposite fixes.

**Drift.** A citation that was correct when written and that an unrelated edit
above it invalidated. `os.umask`, `source_revision`, `README.md:262-264` and the
`gate()`/`require()`/`GATES` anchors are all drift. The count of accepted ids is
drift of the same shape in a different unit. Drift is not a mistake by the author
who wrote the citation — it is a cost the *citation form* imposes on every later
edit, and the only durable fix is to stop paying it.

**Staleness of claim.** `findings.md:419` and `delivery/SKILL.md:20-26` are not
numbers at all. The first was narrower than its own table from the day it was
written; the second was accurate until `80b1706` added a precondition. Both need
words, not digits.

The reason this issue kept reopening is that it treated both as one list of
numbers to correct. Correcting a number that will drift again buys one week.

## 5. The fix

Seven edits across seven files, all comment or prose.

| file | edit |
|---|---|
| `container/entrypoint.py:189` | ``cli.py:670``'s → ``orchestrator/cli.py``'s |
| `docs/findings.md:419` | "copy commands" → "copy commands or claims" |
| `.github/workflows/image.yml:60` | `source_revision (lib.sh:129)` → `source_revision in lib.sh` |
| `scripts/image-scan.sh:80-81` | `README.md:262-264` → `README.md`'s "Delivering it"; **same two lines, same file length**, because `test_scripts.py` and the file's own comments cite `image-scan.sh` by line |
| `.claude/skills/cve-triage/SKILL.md:30-31` | "The 100 accepted IDs survive" → "Every accepted id survives"; same two lines |
| `.claude/skills/delivery/SKILL.md:25-26` | +5 lines: the `PASSED` stamp, its sha256 check, and "*of an accepted image*" |
| `CLAUDE.md:48-49`, `:54` | drop `(`:44`)` and `(`:61`)`; `conftest.py:37` → `tests/conftest.py` |

**Rejected: renumbering.** Every drifted anchor here sits beside a symbol that
greps to exactly one line. Renumbering restores the citation and leaves the next
lane the same work. `docs/review/workflow-gate/REVIEW.md:123-127` had to write
"a later lane re-anchors it" for a citation that had *already* been re-anchored
once. That sentence is the cost.

**Rejected: de-anchoring `tests/conftest.py:7`.** It is a verbatim quotation of
one line, cited identically by three other live files. The line number is what
makes the quote checkable.

**Rejected: fixing `tests/libvirt-module.tftest.hcl:314`.** Measured:
`main.tf:205` is `}`; the non-null bridge arm is `:204`. `main.tf` has not moved
since `4eb378b`, so this is an authoring off-by-one from `454ee7c`, not drift,
and it is in neither issue. It also points into a file `lane/rhel-firmware` may
still move. Reported, not fixed.

## 6. Surface cost

Seven files, **+21 / −15** (this plan and the lane record are separate commits), of which +5/−2 is the `delivery` skill's new
paragraph and the rest is one line in, one line out. No new file, no new gate, no
behaviour, no test. Five citations that could go wrong again went from a number
to a name, so the repo has five fewer things a later edit can break.

## 7. The failing test

There is no test, and adding one would be the wrong trade.

The check that has teeth here is that **every number this branch writes was
produced by a `grep -n` at `80b1706`, and the capture is re-runnable**:
`docs/review/drift/reverify/anchors.txt` holds every command and its output, and
`sed -n 's/^\$ //p' docs/review/drift/reverify/anchors.txt | sh` reproduces it.
Where a de-anchored citation replaced a number, the corresponding grep proves the
symbol resolves to exactly one line, which is the property the de-anchoring
relies on.

**Rejected: a lint gate that verifies `file:line` citations.** It would have to
parse prose out of seven file types, and the campaign's own evidence is that the
correct remedy for most citations is to *remove* the number — a gate would
entrench the form this plan is retiring. `scripts/lint.sh:129-134` already argues
for naming the symbol; the gate is a reader following that rule.

## 8. Verification

`just check` on the branch: six lint gates ok, `ty` clean, `439 passed, 25
skipped` — the baseline count, unchanged, because nothing here is behaviour.

Post-edit, every grep in §2 was re-run and each edited file was read back to
confirm it now says what the grep says. `scripts/image-scan.sh` was checked for
length (190 lines before and after) and its six internal self-citations re-read,
because `tests/test_scripts.py` pins five of them.

## 9. Non-goals

* `orchestrator/backends/base.py:211` (`#94`), `scripts/install-tools.sh:107`
  (`#95`), `RX-B2` (`#96`) and the README `--run-dir` gap (`#105`). Filed, open,
  untouched.
* `#88`'s other six items. They landed in `8915cd7`; only `RX-F2` is deferred
  here, and it has its own plan.
* The sixty `README.md:<line>` citations under `docs/`, and every `file:line`
  under `docs/review/2026-08-29/`, `-08-30/`, `-08-31/` and `docs/archive/plans/`. Those
  are records pinned to the commits they name.
  `docs/review/readme-rootless/REVIEW.md` §O6 settled this and it is not
  reopened.
* `CLAUDE.md:13`'s `pyproject.toml:26-34`, which is two lines short of the block
  it names and predates this campaign.
