# Third review — the remediation, `4eb378b..origin/master`

Written 2026-08-31. This is a plan, not a review. No fixes are applied by it.

## Why another review

The 2026-08-30 review read PR #1 and merged it. Everything it found became an issue, and
those issues have since been closed:

* **21 commits, 2,471 insertions and 495 deletions across 48 files** in
  `4eb378b..origin/master`.
* **46 issues closed** — `#8`–`#20`, `#23`–`#53`, `#57`, `#63`. Three remain open: `#6`
  (signing), `#7` (LICENSE), `#21` (tooling adoptions).
* Every one of those commits has been **read only by its author.** This is the same
  property PR #1 had when the last review was written, and it is the same argument.

Three surfaces in that range have never been read by any review at all: the project
`CLAUDE.md`, the `.claude/` layer (`settings.json`, two hooks, three skills), and
`scripts/bundle.sh`.

`REVIEW.md:480-517` recorded what the last review could not read or run. Two of those
constraints are gone this time: **a hypervisor is reachable and a live image scan can be
run.** The rig gate has never executed, in either review.

## What the evidence currently says

| | |
|---|---|
| Last recorded suite, default gates (`6497f30`) | 379 passed, 25 skipped |
| Last recorded suite with the image gate | 389 passed, 15 skipped |
| Rig-gate runs *inside* a review | **0** — it ran once outside one, 2026-08-29 (`docs/findings.md:404`) |
| Live trivy output checked against `docs/cve-baseline.json`'s rationales | **0** |
| Closed issues never re-verified against HEAD | 46 |

## Deferred by decision, 2026-08-31

Settled. No agent spends budget arguing them.

* **`#21`** — in flight in another session. The review pins to `origin/master`; `#21`'s
  tftest and shellcheck work is read by the next pass, not this one.
* **Mutation testing** — mutmut's clean baseline still does not complete
  (`justfile:114`, `pyproject.toml:74-85`). Deferred, recorded, not closed.
* **`docs/rhel9-target.md` C1, C2, C5** — old-libvirt firmware pin, the raw `.fd` varstore
  branch, and cloud-init 22.1/23.1 through the `sysconfig` renderer. Separate environment.
* **D3, the real golden artifact** — both runs to date used the
  `Rocky-9-GenericCloud-Base` stand-in.
* **`#6` and `#7`** — open by decision, not defects to re-find. This review carries no
  release framing and does not gate on them.

## Findings numbering

**`RX-<dimension><n>`** — `RX-A1`, `RX-B1`, `RX-F3`. The letter is the dimension that found
it, so seven agents number in parallel without colliding. This avoids the first review's
`RV-n`, its remediation sessions' `S-n`, the second review's `RW-n`, and
`docs/findings.md`'s `F`/`R`/`D`.

Severity is one of `critical` · `high` · `medium` · `low` · `nit`, on the same scale the
last review used: severity turns on **reachability and consequence**, not on how surprising
the code is. A wrong answer in an active path outranks a latent seam.

---

## Phase 0 — Evidence baseline (mechanical, no agents)

Facts first, so every later agent argues from the same numbers. All of it into
`evidence/`, one file per gate, `_SUMMARY.md` last.

* `pytest` at default gates. Record the number; do not assume the delta from 379/25.
* **`VCOWS_GATES=all` with the rig and image gates supplied**, which closes
  `tests/test_libvirt_rig.py` (15 tests) and the image gate (10) in one pass. Under `all`,
  a skip is a failure by construction — `tests/conftest.py:44`.

  ```bash
  VCOWS_GATES=all \
  VCOWS_RIG_URI=qemu+ssh://vcows@vcows/system \
  VCOWS_IMAGE=localhost/vcows-deploy:0.1.0.0 \
    .venv/bin/python -m pytest -q
  ```

  The last recorded all-gates run is `docs/findings.md:404`: 390 passed, 0 skipped,
  2026-08-29, against libvirt 12.0.0 on the same host. Reproducing that number is the
  point of this step; a delta from it is a Phase 0 fact, not a footnote.
* `just lint`, `just typecheck`, `just verify-provider`.
* `just image`, then `just scan`, then `just bundle`. The scan output is dimension E's
  input.
* **`scripts/image-scan.sh --write-baseline` is not run, by anyone, for any reason.** It
  regenerates the object with only `image`/`generated`/`note`/`accepted` and discards every
  `why` and `recheck` in the file. `.claude/settings.json` denies the repo-root spelling
  and nothing else, so this rule is the boundary, not the deny entry.

A red *test* baseline stops the review. A red scan is recorded and handed to E.

## Phase 1 — The claims ledger (2 agents)

The 46 closed issues are this review's version of the last one's 115-item checklist. Split
`#8`–`#40` and `#41`–`#63` across two agents. Per issue, one of:

`DONE <file:line>` · `PARTIAL <what is missing>` · `NOT DONE` · `SUPERSEDED <why>`

No prose. Output to `ledger/`, plus the list of issues whose closing commit claims more
than the code does.

**Verify the fix against the defect the issue described, not against the remedy it
suggested.** This is new, and it is the instruction that matters. Measured across four
chunks of `#24` and the standalone issues: the measurements in these bodies usually hold,
and the suggested remedies are frequently written from a misreading. `#19`'s remedy would
have replaced a correct sentence with a wrong one. `#63` asked for a correction to
`scripts/lib.sh:124-125` that must not be made. `#43` reviewed only the tests for coupling
to a format and missed the production call site in `config._blame_the_filename`. **An
issue closed by faithfully applying a wrong remedy is a finding, not a `DONE`.**

## Phase 2 — Dimension fan-out (7 agents, parallel)

Each agent writes its full report to `finders/` and returns a ≤25-line digest — one line
per finding, `RX-<letter><n> | severity | file:line | ≤90 chars`.

| # | Dimension | Surface | Bar |
|---|---|---|---|
| A | Destroy and preflight remediation | `destroy.py` (+160), `preflight.py` (+57), `_reverify` / `_deletable` / `_claimed_elsewhere`, the RW-A1 silence, `#9`, `#31`. Reproduction stays on `tests/fake_libvirt`; the rig is used **read-only**, to settle the libvirt error-code claims dimension A inherited rather than observed | blocking |
| B | Reporting spine and record completeness | `cli.py` (+123), `backends/base.py`, `tofu.py`, `container/entrypoint.py` (+79); the RW-B3/B4/B5 fixes, `#10`, `#11`, `#13`. Does every failure path now write a complete `run.json`? | blocking |
| C | Validation and schema | `backends/libvirt/schema.py` (+159), `config.py`, `marker.py`; `#27`, and `#43`'s jsonschema unification including the `removeprefix(".")` path that `config._blame_the_filename` dispatches on | blocking |
| D | Gate machinery and property tests | `tests/test_gates.py` (+134), `conftest.py`, `test_properties.py`, `libvirt-module.tftest.hcl`; `#14`, `#16`, `#17`, `#63`. Bar: **mutate each new gate and observe it fail.** A gate that cannot fail is the defect class this repo exists to catch | blocking |
| E | Build, pipeline, shell, scan | `scripts/*.sh` (`bundle.sh` +116, `lint.sh`, `lib.sh`, `image-scan.sh`), `justfile`, `Containerfile`, the three GitHub workflows, `.gitlab-ci.yml`, after `#18`, `#37`–`#40`, `#63`. Plus Phase 0's live trivy output against `docs/cve-baseline.json`'s per-CVE `why` and `recheck` — the first time those rationales are checked against a real scan | mixed |
| F | The agent layer, never reviewed | `CLAUDE.md`, `.claude/settings.json`, `hooks/static-gate.sh`, `hooks/session-probe.sh`, `skills/{cve-triage,delivery,provider-bump}`. Does a hook fail open? Does the deny rule cover what `CLAUDE.md` claims for it? Do the skills instruct anything the repo's rules contradict? | **non-blocking, files as issues** |
| G | The unread list | `REVIEW.md:480-517`: `preflight.py` outside the destructive halves, `cli.py` outside destroy and reporting, `backends/libvirt/tofu/` beyond `main.tf` and `outputs.tf`, `docs/archive/orchestrator-architecture.md`, `docs/research/future-backends.md`, `docs/spikes/README.md`, and the rootless-podman `--run-dir` / `--user` / bind-mount matrix, now runnable | mixed |

F and G exist so those surfaces are read once by someone before they are trusted, not to
hold up anything. Their findings go to list 2 in Phase 4.

## Phase 3 — Adversarial verify

The contract that held twice, applied to A–D. Critical and high get 3 skeptics on distinct
lenses (reproduce · reachability · already-handled); medium 2; low and nit batched
1-per-8. Skeptics default to `refuted: true` when uncertain. E, F and G findings take a
single confirmer. **Verifier output lands in `verify/`** — the last review left that
directory empty and the arbitration is not reconstructable from `REVIEW.md` alone.

## Phase 4 — `REVIEW.md`

Written from disk, four lists and nothing else:

1. **Fix before anything else lands** — verified A–D findings, each with its reproduction.
2. **File and schedule** — E, F and G, plus any A–D finding the reproduction did not
   survive. Issue text written, ready to file.
3. **Claims ledger** — Phase 1's output, including every overclaim, naming the commit.
4. **Still accepted unverified** — rhel9 C1/C2/C5, D3, mutmut, and whatever this review's
   own environment could not reach. Named, so the record stays honest about what it is.

**Findings are filed as issues, not fixed here.** Filing is what forces the supersession
check, and it is this repo's standing pattern.

---

## Rules carried over (they held twice)

* Agents may read, run `pytest` and `podman build`. **No agent edits a tracked file.** No
  agent runs `--write-baseline`.
* **The rig is live and is not yours** (`docs/review/2026-08-29/_BRIEF.md:26`).
  `qemu+ssh://vcows@vcows/system` hosts four VMs belonging to someone else plus two probe
  fixtures. Read-only libvirt calls are fine. **Do not define, start, stop, undefine or
  delete anything on it, and do not create volumes.** The rig gate itself is read-only
  apart from `pool.refresh(0)`; that is the whole of what Phase 0 changes there.
* The orchestrator never reads source or report files during the run — only one-line
  `jq` / `wc` queries against the ledger.
* **Every `file:line` is re-verified before it is written.** Both prior reviews shipped
  citations that had already drifted; `IMAGE_LICENSES` moved from `Containerfile:78` to
  `:113` between the last review and this plan.
* Agents read `docs/review/2026-08-29/_BRIEF.md` and `_ORIENTATION.md`. Neither is
  rewritten.

## Budget

~22 agent completions, against the last review's 35 and the first's 135. The changed
surface is a fifth of PR #1's, the invariant register already exists in `_BRIEF.md`, and
Phase 1 is 46 issues rather than 115 checklist items.

## Still open

**What a Phase 0 rig failure means.** The rig gate has never run, so its first execution
is as likely to expose a stale test as a real defect. This plan treats a rig failure as a
dimension-A finding rather than as a stop, and the baseline rule above ("a red test
baseline stops the review") is scoped to the gates that have passed before. Say otherwise
before Phase 0 runs.
