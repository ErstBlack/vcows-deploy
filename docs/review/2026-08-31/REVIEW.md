# Third review — the remediation, `4eb378b..672a500`

Written 2026-08-31. Subject: the 21 commits that closed 46 issues after the 2026-08-30
review merged PR #1. Read only by their author until now.

**No merge is gated by this document.** Everything here is on `master` already. The four
lists below say what to fix first, what to file, what the closed issues actually delivered,
and what this project still ships unverified.

## Filed as

Fifteen issues, 2026-08-31. `#75` was filed independently by the `#21` session and is
referenced, not duplicated.

| issue | findings |
|---|---|
| #76 | RX-E1 |
| #77 | RX-E2 (subsumes RX-E3, RX-E9) |
| #78 | RX-D3 |
| #79 | RX-E6 |
| #80 | RX-B7 |
| #81 | RX-A1 |
| #82 | RX-E4 |
| #83 | RX-E5 |
| #84 | RX-F1 (closes #57's PARTIAL) |
| #85 | RX-G1 |
| #86 | RX-G2, RX-G3 |
| #87 | RX-D1, RX-D2, RX-D5, RX-D6, RX-D7, RX-D8, RX-D10 |
| #88 | RX-F2, RX-F3, RX-F4, RX-F6, RX-F7, RX-F8, RX-F9 (closes #45's PARTIAL) |
| #89 | RX-B1, RX-B2, RX-B3, RX-B6, RX-C1, RX-C2 |
| #90 | RX-A2, RX-A4, RX-C3, RX-E7, RX-E10, RX-E11, RX-G4, RX-G5, RX-G6, RX-G7, RX-G8 |

Not filed: the five refuted findings, and RX-E8, RX-E12 and RX-G9, recorded here as leave-it
with the reason each fix costs more surface than the defect warrants.

## 0. The numbers

| | |
|---|---|
| Range | `4eb378b..672a500`, 21 commits, 2,471 insertions / 495 deletions, 48 files |
| Closed issues reconciled | 46 |
| Findings raised | 56 |
| Findings refuted in verification | 5 |
| Findings downgraded | 9 |
| Duplicates merged | 3 |
| Agents | 21 — 2 ledger, 7 dimension, 12 verification |

Phase 0 baseline, `evidence/_SUMMARY.md`: **436 passed, 0 skipped** under `VCOWS_GATES=all`
with the rig and image gates supplied; lint, typecheck and `verify-provider` green; image
built; live trivy scan clean against the baseline; bundle assembled. **The rig gate and the
live scan had never run inside a review.** Both did here.

---

## 1. FIX FIRST

Five findings. Each either ships a wrong artifact or gives a wrong answer silently.

### RX-E1 — `install-tools.sh`'s sha256 check fails open — **high**

`scripts/install-tools.sh:68`. A download whose bytes do not match the pinned digest is
installed anyway and the script exits 0. Reproduced in a scratch copy with two `url()` arms
repointed at local mismatched payloads: `sha256sum: WARNING: 1 computed checksum did NOT
match`, `install_one rc=0`, `SCRIPT EXIT=0`, and `.tools/bin/hadolint` and `.tools/bin/tofu`
hold and execute the wrong bytes.

Reachable from `justfile:51`, `ci.yml:50,79`, `image.yml:70`, `scheduled.yml:62`,
`.gitlab-ci.yml:38` (all four jobs) and `README.md:306`. No upstream guard — `lib.sh:63`
checks PATH presence only. **The non-adversarial trigger is a version bump with no matching
digest arm**, which `install-tools.sh:19-20` promises is "a hard failure", and `ci.yml:7-9`
cites this very check as the reason the pipelines use no marketplace actions.

Held at high rather than critical: the blast radius is the build host and the runner, not a
deployed target.

Still present at `origin/master` (`a3068e3`): `install-tools.sh` is unchanged since the pin.

### RX-E2 — `lib.sh` sets no `inherit_errexit`, so nested `die` is swallowed — **high**

`scripts/lib.sh:16`. Under `set -e` without `shopt -s inherit_errexit`, a `die` two levels
inside `$(...)` does not stop the script. Reproduced on the real scripts: emptying
`ARG VCOWS_VERSION=` in the `Containerfile` makes `image-build.sh` print
`error: no 'ARG VCOWS_VERSION=' in Containerfile`, then continue and invoke the builder as
`-t localhost/vcows-deploy:` with exit 0. Adding the shopt to the same copy gives exit 1.

**Six outer call sites, five distinct inert guards**, each run: `image-build.sh:23`,
`image-scan.sh:70`, `test-image.sh:14` (via `image_tag`→`containerfile_arg`);
`image-build.sh:38` and `bundle.sh:61` (via `source_revision`→`provider_version`, and the
bare `git rev-parse`); `install-tools.sh:115` (via `fetch`→`digest`, and `curl`/`sha256sum`
— this is RX-E1's mechanism). Each produces **a wrong value that ships**, not an empty
string a later step catches: `localhost/vcows-deploy:`, `--build-arg GIT_SHA=`, a `ship`
array carrying `docs/provider-.lock.hcl`.

Not caught by anything: `lint.sh:159`'s shellcheck exits 0, and the minimal two-level
pattern under `shellcheck -o all` emits no SC2311/SC2312. RW-G5 found one site's symptom
last round and blamed argument position; the remedy that landed dropped the `|| exit` half
that would have worked. `archive_label` is safe — its inner substitutions are `-n`-tested.
Single-level sites fail correctly, which is the boundary.

**Subsumes RX-E9** (`source_revision` returning `""` at rc 0) and the substance of **RX-E3**
(`image_tag`'s empty tag, whose diagnosis at `lib.sh:88-93` is false for the same reason).
One fix, not three.

Still present at `origin/master` (`a3068e3`): `lib.sh` gained 8 lines in `454ee7c` and
`shopt -s inherit_errexit` is still absent.

**Note on the `#21` work, which landed as `454ee7c` after this review's pin:** it turns on
four optional shellcheck checks and
rewrites `source_revision`'s `git status` to a tested assignment, commenting "the failure
reaches the `set -e` in this file instead." Measured: running those scripts over a
`672a500` tree with `.git` absent gives byte-identical output, `GIT_SHA=`, exit 0. **It does
not close this, and its comment records the wrong cause.**

### RX-D3 — the domain `type` is unasserted, so silent TCG reports success — **medium**

`orchestrator/backends/libvirt/tofu/main.tf:88`. Changing `kvm` to `qemu` leaves `tofu test`
and the whole suite green at 411 passed / 25 skipped, exit 0. Nothing downstream can catch
it: no config field, and absent from `render.py`, `outputs.tf`, `preflight.py`, `destroy.py`
and all 15 rig tests. The only mitigant is that the provider marks `type` required, so
deletion is loud — **only a deliberate value edit is silent**, and the VM boots under
emulation while the deploy reports success.

**Re-checked against `a3068e3`, which landed after this review's pin.** The new
`scripts/smoke-libvirt.sh:389` is the first thing anywhere to assert a domain type — and it
asserts `<domain type='qemu'`, because the CI job forces TCG through an OpenTofu override
file while `main.tf` keeps `type = "kvm"`. So the finding survives and gets slightly worse:
editing `main.tf`'s `kvm` to `qemu` now passes the tftest suite *and* the new smoke gate,
whose only domain-type assertion expects the overridden value.

### RX-E6 — the bundle ships the image the CVE gate rejected — **medium**

`scripts/bundle.sh:50-52`. `image-scan.sh` exits 1 on a finding outside the baseline but
leaves a complete `.cache/scan`. `bundle.sh` then produces a correctly named 144M bundle
whose `SHA256SUMS` verifies and whose shipped `trivy.json` contains the rejected CVE. CI is
safe — consecutive steps, no `if: always()` on the upload. **The hand-run path documented at
`README.md:261-264` is not.**

### RX-B7 — `--run-dir` under `--user` writes no `run.json` at all — **medium**

Found during verification of RX-G2, not by a dimension. For the exact case `README.md:66-68`
documents, `/runs` stays `0755` root-owned, the `_record` write raises `OSError`, and
`_guard`'s `contextlib.suppress(OSError)` at `cli.py:261-262` swallows it. The identical
failure **without** `--user` does write the record. An air-gapped site's only account of the
deployment silently does not exist, and the suppression is what hides it.

Raised by a verifier and not itself independently verified. Treat the reproduction as
single-sourced.

---

## 2. FILE AND SCHEDULE

### Confirmed at medium

| id | file:line | what |
|---|---|---|
| RX-A1 | `destroy.py:553-557` | #9's warning fires before the delete: two shapes report "was deleted" when nothing was. #9's own commit says the warning accompanies "every delete taken", so the gap is unintended |
| RX-E4 | `scripts/lint.sh:68-71` | `curl … \| sh` in `.gitlab-ci.yml`'s `.bootstrap` anchor passes `workflows carry no logic`; the same string in `script:` fails it. The extractor sees 13 commands and none of the three bootstrap lines, across all four jobs |
| RX-E5 | `scripts/image-scan.sh:134` | the "scan did not read this image" guard is an equality test on total loss: 99 of 100 baseline ids vanishing passes green |
| RX-F1 | `.claude/hooks/static-gate.sh:54` | the signature omits `.toml`, `.tftest.hcl` and `justfile`, so those edits return a cached pass in ~0.03s while `lint.sh` exits 1. Downgraded from high: `just check` runs in both pipelines and catches every case. **Never hashed is the cause; the cache is only how it surfaces.** Correct pattern is `\.toml$\|\.tftest\.hcl$\|(^\|/)justfile$` — lock files are *not* in the blind spot, `tofu fmt` ignores them |
| RX-G1 | `orchestrator/cli.py:134` | `_run_dir`'s mkdir is unguarded; `--user 4242` gives a raw `PermissionError` and a `:ro` mount a raw `OSError`, both with a relative path because `resolve()` is at `:135`. One `except OSError` → `UsageError` |
| RX-G2 | `README.md:66` | "deploy runs clean" under `--user` is false — measured, the documented recipe fails verbatim, **and `destroy --yes` fails identically**, which the finder missed. Doc fix: the 2026-08-29 checklist measured `preflight` only, and `README.md:66` had already generalised to `deploy` by `4eb378b` |
| RX-G3 | `README.md:62-64` | the `:U` remedy leaves the run directory `drwx------ 528529:1000`; the operator cannot `ls` or `rm -rf` it. Doc fix — the `0700` is deliberate (`cli.py:150-153`, pinned by two tests) and must not be loosened |

### Confirmed at low or nit

Twenty-six, listed in full in the finder and verify reports. The ones worth naming:

* **RX-D2** (`tests/conftest.py:37`) — file this **above** RX-D1 despite both being low.
  Forcing `GATES=set()` leaves 411/25 exit 0 under both the default and `VCOWS_GATES=all`;
  a `.strip()` variant keeps `test_gates.py` 16/16 green while falsifying `CLAUDE.md:55-56`.
  It silences all five gates at once.
* **RX-D5** (`tests/test_gates.py:76-83`) — four scanner bypasses reproduced, including a
  bare `pytestmark = pytest.mark.skip` that needs no alias and skips four tests. ~6 lines.
* **RX-D7** (`orchestrator/tofu.py:256`) — `_capture`'s timeout unpinned; the other half of
  #17. One assertion.
* **RX-D8** (`tests/golden/libvirt.tfvars.json`) — one fixture, so the base-present, BIOS
  and bridge branches are never evaluated. Not a recorded scope decision: `.tftest.hcl:13-17`
  scopes out only `depends_on`.
* **RX-F2** (`CLAUDE.md:39,85,99,100,101`) — five anchors drifted, all correct at `059c1ca`;
  the three `Containerfile` pins by 35 lines. Each sits beside a unique grep target, so the
  fix costs one grep. A rule file teaches the wrong line with authority.
* **RX-F3**, **RX-F7** (`.claude/`) — unquoted `$CLAUDE_PROJECT_DIR` exits 127 under a path
  with a space and the Stop gate is simply absent; an unwritable `.cache` makes `fail()`
  exit 1 rather than 2 at the `printf > "$STATE"` redirect (`:87`, not the `mkdir` at `:86`),
  losing the lint output along with the block.
* **RX-E7**, **RX-E10**, **RX-E11** — baseline note arithmetic (11 shared ids, not 2), the
  GitLab artifact cap against a measured 153 MiB delivery directory, and a `fetch-depth: 0`
  whose stated justification is false (a `--depth 1` clone gives the full 40-hex SHA).
  All three are comment or note fixes.

### Marked leave-it, with the reason

* **RX-E8** — `accepted` carrying no group membership is the *recorded* design;
  `cve-triage/SKILL.md:67-69` prescribes exactly that edit shape. Fixing it rewrites the
  file schema plus `image-scan.sh:113-117`'s jq for a lookup needed once.
* **RX-E12** — `image.tar.sha256`'s check has no documented command. Nothing incorrect, only
  unstated; one line if `bundle.sh` is touched anyway.
* **RX-G9** — the fix costs three signatures and a message shape; a comment at
  `preflight.py:186` is the right size.

### Refuted

| id | why |
|---|---|
| RX-B4 | #35's commit body names that exact line: "deliberately not folded in … the only literal width left in the file, which is correct" |
| RX-C4 | each `where` names the check that could not run, and both messages name the unreadable path |
| RX-C5 | `mac_of` derives a MAC, so `.mac` would name a key the config lacks; `where` is the deepest key that exists |
| RX-D9 | recorded design — `docs/ci.md:52-58` tables all four gates and `:60-62` plus `docs/research/tooling-2026-08-29.md:487-497` record why `all` is never set. Residual: that table omits `libvirt` |
| RX-F5 | the uncovered deny spellings are real, but `CLAUDE.md:90-95` and two other files state the list is a guardrail, not a boundary. Two more entries add surface to an enumeration the repo documents as unclosable |

### Three findings whose measurement held and whose remedy did not

Recorded because it is the same defect class the ledger found in the issue backlog, now in
this review's own output.

* **RX-B6** — putting the message in `run.extra["tofu"]` makes the field `dict | str`,
  because `tofu.version` returns a `dict`. Correct fix is a `Problem.warning`.
* **RX-B2** — changing `cli.py:552` to `except BaseException` alone captures nothing;
  `destroy.py` leaves `out` attached to nothing after an already-executed `undefine`. And
  the "docstring contradiction" framing is wrong: `_guard` catches `BaseException` at `:258`
  and does what it documents.
* **RX-G4** — the uncaught `OSError` names the mount; the widened guard the finder wanted
  would name the mode and defer the failure. Fix the comment at `cli.py:148-152`.

---

## 3. CLAIMS LEDGER

46 closed issues, verified against the **defect each described** rather than the remedy each
suggested. Reports: `ledger/a-issues-8-40.md`, `ledger/b-issues-41-63.md`.

| range | issues | DONE | PARTIAL | NOT DONE | SUPERSEDED |
|---|---|---|---|---|---|
| #8–#40 | 31 | 30 | 1 | 0 | 0 |
| #41–#63 | 15 | 8 | 3 | 3 | 1 |
| **total** | **46** | **38** | **4** | **3** | **1** |

### No overclaims. Every commit in range understates.

Several name what did not land: `2f8ebe2` declines #9's stronger fix, `9f8c442` reports #43
as +3 lines rather than a reduction, and #24 was held open until #67 merged. This is the
opposite of the last review's result, which found one overclaim against item 2.1.

### Eleven wrong remedies, zero applied

The instruction this review gave its ledger agents turned out to be one the author had
already followed. Each refusal is recorded with its measurement: #19's "5 delegate and 3"
(4/3/1 was already correct), #25's `%|SOURCERPM?..|` (does not work on rpm 4.19.1.1), #27's
hoisting (reorders the problems), #28's drift (36 lines and three ignores, not one and two),
#30's 66 sites (65), #31's cited test (pins nothing), #16's `derive_id` path
(`orchestrator/marker.py:161`), #17's `Stubborn.wait` (did not record its timeout), #18's
"ten uses" (twelve), #20's Dependabot claim (has never opened a `uv.lock` PR), #29's timing
(no scheduled run exists to time).

**#63's trap was avoided correctly** — its body asked for a correction to `scripts/lib.sh:124-125`
that must not be made, and the sentence is true (`image.yml:22-23`, `.gitlab-ci.yml:102-103`).

### PARTIAL — #23, the OpenTofu-container spike

Genuinely rejected with measurements, but only in a GitHub comment. Nothing in
`docs/spikes/README.md`, nothing beside D7 at `Containerfile:93-95` — the exact placement the issue
asked for "so the next person does not re-ask it".

### PARTIAL — #44, #45, #57

`#44`'s printf shim reason lives only in `c124ffe`'s message, not in the file. `#45` is
RX-F2's anchor drift. `#57` is RX-F1: the fix landed but left `.hcl` outside the signature.

### NOT DONE — #47, #50, #52, and what that means

All three closed with no repo artifact. On inspection **#50 is covered**: `26627ad`'s body
names it and it is the `settings.local.json` case `CLAUDE.md:75-76` already describes. **#47
and #52 landed outside the project directory entirely** — `~/.claude/local-plugins/ty-lsp/`
and the MCP roster — which is wider than the rule `CLAUDE.md` states. #52's resolution also
reversed the issue's own proposal on serena, argued only in a GitHub comment.

The consequence is structural, not clerical: **a decision recorded only in a GitHub comment
or a machine-local file is a decision this repo cannot show anyone.** #23 has the same shape.

### One correction to this review's own index

`ledger/_issues.md` is generated by `git log --grep` and is wrong in three places: #33 and
#34 are closed by `01a513c`, not `e4371ff`; #42's row names a commit whose body closes only
#41; `a347f51` says `Refs #29`, not `Closes`. The index carries this warning at its head.

---

## 4. STILL ACCEPTED UNVERIFIED

### Closed by this review

* **The rig gate, inside a review.** 15 tests, `qemu+ssh://vcows@vcows/system`, libvirt
  12.0.0. Nothing on it was defined, started, stopped, undefined or deleted.
* **The live CVE scan against the baseline's rationales.** All six rationale groups match
  the real trivy output to the number, and the x/crypto acceptance still holds at
  `render.py:61`. `CVE-2026-58055` is a stale `rocky-base` row — **no pin moved**; trivy's
  DB updated 16 hours after the baseline was generated. The correct hand-edit is to delete
  the id, leave `rationale["rocky-base"]` alone, and fix the `note` per RX-E7.
  **Not `--write-baseline`.**
* **The rootless-podman matrix**, flagged as deserving its own agent on 2026-08-29 and never
  run until now. It produced RX-G1, RX-G2, RX-G3 and RX-B7.
* **The inherited libvirt error-code claim.** "`NO_DOMAIN` and `ACCESS_DENIED` only" is
  **wrong** — read-only probes returned 42, 8, 6, 29, and 50 for every unresolvable
  `storageVolLookupByPath` including paths outside every pool. `errors.py:23-27` and
  `destroy.py:531` are conservative enough that all of them land in the branch that touches
  no disk, so the conclusion survives while its premise does not.

### Still shipped unverified

**C2 is no longer unverified — it has been run, and it fails.** The raw `.fd` varstore
branch of `main.tf:133`, which the last review listed as "never rendered against a real
`.fd` template", was reached by the new T1 smoke gate on its first CI run and is filed as
**issue #75**: the apply dies at the domain define with
`.os.nv_ram.template_format: was cty.StringVal("raw"), but now null`, **after** the base,
overlay and seed volumes are already written. That is `findings.md` §2's accepted
orphan-volume gap reached through a path nobody chose.

This review did not find it and could not have: `#75` names the same root cause as **RX-D8**
— `tests/libvirt-module.tftest.hcl` uses `mock_provider`, which satisfies the schema with
generated values and never performs a post-apply read, and the single golden fixture never
evaluates the BIOS branch. RX-D8 is the coverage gap; #75 is what was hiding in it. **File
RX-D8 referencing #75, not as an independent discovery.**

Otherwise unchanged from `docs/review/2026-08-30/REVIEW.md:444-478`, and re-stated so the
record does not quietly drop them: **2.15** (the EFI firmware pin on libvirt 8.0.0/8.5.0,
`docs/rhel9-target.md:47-60`), **cloud-init 22.1/23.1 through the `sysconfig` renderer** on
RHEL 9.0–9.3 (C5, and `rhel9-target.md:93` says to schedule it first), and **D3, the real
golden artifact** — both runs to date used the `Rocky-9-GenericCloud-Base` stand-in.

**Mutation testing** remains deferred: mutmut's clean baseline does not complete
(`justfile:114`). Dimension D substituted hand mutation and proved 9 gates and 9 `main.tf`
attributes by hand, which is not the same coverage.

### What this review did not reach

* `.gitlab-ci.yml` has still never been executed; its runner executor is unknown.
* GitHub branch-protection settings were not inspected.
* The seven pinned SHA256 digests in `install-tools.sh` were not checked against upstream
  checksum files — **and RX-E1 means the script would not tell you if they were wrong.**
* The dmacvicar/libvirt 0.9.8 provider was not executed; the go-libvirt `sshcmd` dialer's
  argv construction is still reasoned about rather than observed.
* Issue **#21** is excluded by decision — in flight at review time, landed as `454ee7c`
  while Phase 3 ran, followed by the TCG smoke gate `a3068e3` and issue **#75**. None of
  those three is read here. RX-E1, RX-E2 and RX-D3 were each re-checked against
  `origin/master` and all three survive; the rest of this review is pinned at `672a500`.

---

## 5. AN ERROR THIS REVIEW MADE

Phase 3 contaminated the working repository, and the record should say so.

To avoid re-downloading 432 MB of pinned binaries, the review worktree's `.tools/bin` was a
**symlink** to the live `/home/ssullivan/vcows-deploy/.tools/bin`. The verifier reproducing
RX-E6 made a `cp -a` scratch copy — which copies the symlink, not the tree — and wrote its
scan-tampering fixtures through it into the live directory: a `podman` returning 0 for
everything, a `syft` replaying a canned SBOM, and a `trivy` injecting `CVE-2099-99999`.
`lib.sh:26` prepends that directory to `PATH` for every script in this repo.

The fakes were removed, which also removed the real `trivy` and `syft` they had overwritten;
`cosign` and `hadolint` were untouched. `.cache/delivery` and `.cache/scan` in the live tree
predate the review and carry no injected finding. `.tools/` is gitignored, so `git status`
showed nothing at any point. **`just tools` restores the two binaries**, and their digests
should be checked by hand against `install-tools.sh:37-38` — which is RX-E1's point exactly.

The symlink has been replaced with a real directory. RX-E5 and RX-E6 were reproduced using
those fixtures and their findings stand; what was wrong was where the fixtures were written.
