# Pre-merge review plan — PR #1 (`feature/scaffold` → `master`)

Written 2026-08-30. This is a plan, not a review. No fixes are applied by it.

## Why another review

The 2026-08-29 review covered `45d5b92..da3f45c` — eleven commits, the code *before*
remediation. Twenty-two commits have landed since:

* **13,048 insertions across 99 files** in `da3f45c..HEAD`.
* The **remediation for that review's own findings** (S1–S12) — the fixes have never
  been read by anyone but their author.
* A **new CI and tooling layer that no review has ever touched**: 1,165 lines of
  shell and pipeline YAML.

Merging PR #1 merges all of that. The last review's verdict — "shippable as v0.1 once
section 2 is fixed" — does not transfer to the code that claims to fix it.

## What the evidence currently says

| | |
|---|---|
| Checklist items in S1–S12 marked done | **0 of 115** |
| Commits claiming S1–S12 landed | 12 |
| Blocked-list items closed on the rig | 6 of 9 |

The remediation checklist is the merge evidence, and it is empty. Closing that gap is
Phase 1 and is the single highest-value item in this plan.

## Deferred by decision, 2026-08-30

These are settled. They are not findings, and no agent spends budget arguing them.

* **Mutation testing** — the mutmut baseline is broken (enum identity between the
  editable install and the copied tree). S7's exit criterion, "re-run agent 12's
  fourteen surviving mutations", is **deferred post-merge**. Recorded, not closed.
* **CI and signing defects** — anything in `scripts/*.sh`, the `justfile`, the three
  GitHub workflows, `.gitlab-ci.yml`, cosign signing or the CVE baseline is
  **post-merge**. Reviewed once, filed as issues, never blocking.
* **2.15 — EFI firmware pin on old libvirt** — needs a separate environment. Work
  order is `docs/rhel9-target.md`.
* **D3 — the real golden artifact** — tested in a separate environment.
* **cloud-init 22.1 / 23.1 on RHEL 9.0–9.3 EUS** — same environment as 2.15.

What is left blocking is exactly one class: **the Python that deletes VMs and disks,
the schema that guards it, and the tests that pin both.**

---

## Phase 0 — Evidence baseline (mechanical, no agents)

Facts first, so every later agent argues from the same numbers.

* `VCOWS_GATES=all` full suite with the image gate supplied; record passed / failed /
  skipped. Last known good: 390 passed, 0 skipped (2026-08-29 rig session).
* `scripts/lint.sh`, ruff (incl. the new bandit rules), `ty`.
* `just image` — build only, to confirm it still builds. Scan and signature results
  are recorded, not judged.
* PR #1 CI status on both platforms, recorded as-is.
* All of it into `evidence/`. A red *test* baseline stops the review; a red CI or scan
  result does not.

## Phase 1 — Close the ledger (2 agents)

Take the 115 unchecked items in `2026-08-29-remediation-checklist.md` and, for each,
find the code or test at HEAD that closes it. Split S1–S6 / S7–S12 across two agents.

Per item, one of: `DONE <file:line>` · `PARTIAL <what is missing>` · `NOT DONE` ·
`SUPERSEDED <why>`. No prose. Output is a reconciled checklist plus the list of items
whose commit message claims more than the code does.

This is the gate. A merge with an unreconciled ledger is a merge on an author's word.

## Phase 2 — Dimension fan-out (7 agents, parallel)

Each agent writes its full report to `docs/review/2026-08-30/finders/` and returns a
≤25-line digest — one line per finding, `RW-n | severity | file:line | ≤90 chars`.
Findings are numbered **RW-1…** to avoid colliding with the last review's `RV-n` and
`findings.md`'s `F`/`R`/`D`.

| # | Dimension | Surface | Bar |
|---|---|---|---|
| A | Destroy-path regression | `destroy.py`, `preflight.py` destructive halves, the disk-delete allowlist, post-`_confirm` re-verification | blocking |
| B | Reporting spine + run dir | `cli.py` (+401), `Outcome` plumbing, exit codes, `run.json` and manifest on every failure path, umask and secret residue | blocking |
| C | Validation tightening | S5/S6 — every new pattern and numeric error-code match: does it reject what it claims, and does it reject nothing legitimate? `schema.py`, `config.py`, `marker.py` | blocking |
| D | Security adversary | Attack the fixes: `ssh_keyfile`/`known_hosts` pattern bypass, `install()`-on-`validate`, `source_qcow2` `^/`, `container/entrypoint.py` | blocking |
| E | Test teeth | New `test_properties.py`, `libvirt-module.tftest.hcl`, `test_entrypoint.py`, `test_manifest.py`, `test_gates.py` — plus `test_tofu_driver.py` and `docs/spikes*`, which no agent read last round | blocking |
| F | Doc and decision drift | S12 claimed sixteen comments corrected; verify at HEAD, plus `findings.md`, `README.md`, `docs/ci.md` | mixed |
| G | Build and pipeline layer | `scripts/*.sh`, `justfile`, workflows, `.gitlab-ci.yml`, `sign.sh`, `verify-provider.sh`, `mirror.sh`, `cve-baseline.json` — one pass, one report | **non-blocking, files as issues** |

G exists so the layer is read once by someone before it is trusted, not to hold up the
merge. Its findings go straight to list 2 in Phase 4.

## Phase 3 — Adversarial verify

Same contract that worked last round, applied only to A–E. Critical/high get 3
skeptics on distinct lenses (reproduce · reachability · already-handled); medium 2;
low and nit batched 1-per-8. Skeptics default to `refuted: true` when uncertain.
F and G findings take a single confirmer.

## Phase 4 — Merge decision

`REVIEW.md`, written from disk, containing four lists and nothing else:

1. **Blocks the merge** — verified findings from A–E, with the reproduction for each.
2. **Merge, track as an issue** — everything from F and G, mutmut, and any A–E finding
   the reproduction did not survive. Issue text written, ready to file.
3. **Reconciled ledger** — Phase 1's output, as the PR's evidence.
4. **Accepted at merge, verified elsewhere** — 2.15, D3, the cloud-init renderer path.
   Named explicitly so the merge records what it is knowingly shipping unverified.

---

## Rules carried over (they held last time)

* Agents may read, run pytest and `podman build`. No agent edits a tracked file.
* The orchestrator never reads source or report files during the run — only
  one-line `jq`/`wc` queries against `99-LEDGER.jsonl`.
* Findings only. Fixes are a separate session against the reconciled ledger.

## Budget

~35 agent completions, against the last review's 135. The surface is a third the size,
the invariant register already exists in `_BRIEF.md`, and five of the last round's
nineteen dimensions are now out of scope by decision.

## Still open

**The merge bar.** The deferrals above imply findings can ride out as issues, so this
plan assumes: **list 1 must be empty to merge, list 2 need not be.** Say otherwise
before Phase 2 starts — it changes what the verifiers in Phase 3 are for.
