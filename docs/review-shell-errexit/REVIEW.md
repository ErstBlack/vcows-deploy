# Scoped review — lane `shell-errexit`

Input: `git diff origin/master...lane/shell-errexit` and nothing else. Base
`411c12d`, head `213dadc`. One fix commit, two files, `+108 / −6`.

```
 scripts/lib.sh        | 19 +++++++----
 tests/test_scripts.py | 95 +++++++++++++++++++++++++++++++++++++++++++++++++++
```

Plans under review: `docs/plans/issue-77.md`, `docs/plans/issue-76.md`. Raw
evidence they rest on: `docs/review-shell-errexit/reverify/RX-E1.txt`,
`RX-E2.txt`, `RX-E3.txt`, `RX-E9.txt`.

The second commit on this branch carries this file, the two plans and the four
transcripts. It changes no code and is not reviewed here.

---

## Lens 1 — did it do what the plan said?

Yes, with one deliberate substitution and one arithmetic correction.

**The edit is `issue-77.md` §5 verbatim.** `shopt -s inherit_errexit` and its
four-line comment after `set -euo pipefail`, plus the eight-line replacement of
the `image_tag` comment. `git show --stat` reports `scripts/lib.sh | 19 ++++---`,
which is `+13 / −6`: the plan's §6 predicted `+5` and a `6 → 8` comment swap.
Same edit, counted differently — `+5` and `+8 −6` is `+13 −6`.

**`tests/test_shell_errexit.py` became `tests/test_scripts.py`.** Directed
substitution, not drift: a second lane proposed the same rig under the general
name for two other issues and will add to this file. The module docstring is
written for that — it states the gap the file exists to close, and both helpers
take the script set as an argument rather than hardcoding `lib.sh`.

**One number in the plan's own patch text was wrong and is corrected.**
`issue-77.md` §5's replacement comment cites the new option as `(:17)`. Its own
layout puts the four comment lines between `set -euo pipefail` (`:16`) and the
`shopt`, so the option lands at `:21`. Landed as `:21`, verified against the
committed file.

**Confirmed absent, each named as a non-goal in `issue-77.md` §9 or
`issue-76.md` §9:** no edit to `scripts/install-tools.sh` (`git diff` names two
files), none to `lib.sh:142-146` or `lint.sh:183-187` — both of which assert
something false at `origin/master` and become true here — no `|| die` on
`install-tools.sh:68`, no post-install re-verification pass, no new shellcheck
optional check, no change to `docs/cve-baseline.json`, and nothing touching
`install-tools.sh:107`'s silent exit 1 (issue 95).

**What the plans got right and this confirmed independently.** The three-shape
measurement (`B` two levels, `D` one level, `C` argument position) reproduces on
`411c12d` exactly as `RX-E3.txt` records it, and the `#76` harness reproduces on
a fresh clone with a hand-made `.tools/bin`: mismatched bytes installed and
executable at exit 0 without the option, nothing installed at exit 1 with it.

---

## Lens 2 — do the tests have teeth?

Three mutations and the control, each run against the committed tree.

| mutation | result |
|---|---|
| `shopt -s inherit_errexit` commented out | `1 failed, 1 passed` — `AssertionError: the die did not stop the caller: 'REACHED tag=[localhost/vcows-deploy:]\n'` |
| harness broken: the fixture's `ARG VCOWS_VERSION` renamed to `ARG NOT_THE_ARG` | `1 failed, 1 passed` — the **vacuity guard** fails, not the defect test |
| the vacuity guard's expected tag changed to `:0.0.0.0` | `1 failed, 1 passed` |
| none | `2 passed` |

Whole suite with the option reverted and the file present: `1 failed, 413
passed, 25 skipped`. So the failure is visible from `just check`, not only from
a targeted invocation.

Row 2 is the one that matters for the pair. Without it, a fixture that stopped
producing the guard's precondition would leave the defect test passing for the
wrong reason, and `#77`'s whole history is defects that passed for the wrong
reason.

**A real defect in the test file, found by running it rather than by reasoning
about it.** Both tests passed under `just test` and both failed under
`just test-image`. `image_tag` returns `$VCOWS_IMAGE_TAG` verbatim when it is set
(`lib.sh:103`), `just test-image` is normally invoked with that variable set, and
`test-image.sh:15-16` exports `VCOWS_IMAGE` and `VCOWS_GATES` into the pytest
process. `_run` now builds the child environment with every `VCOWS_*` name
dropped. This is recorded because it is the second time in this lane that a
green run under one invocation said nothing about another.

**What the tests do not cover, stated rather than implied.** They exercise
`image_tag → containerfile_arg → die` and nothing else. The `install-tools.sh`
chain that `#76` reports — `install_one:115 → fetch:63 → digest:40` — is the same
mechanism and the same one-line remedy, but it is covered by argument, not by an
assertion. `issue-76.md` §7 priced the direct test at roughly forty lines of
harness (`install-tools.sh:169` runs `main "$@"` at source time, so the file has
to be rewritten to be sourceable) and declined it knowingly. The consequence is
live: a `|| true` appended to `install-tools.sh:68` specifically would not turn
these two tests red.

---

## Lens 3 — what moved

`scripts/lib.sh` gains 5 lines after `:16` and 2 more inside the replaced
comment. **A `lib.sh` citation at or below `:16` is unaffected; `:17`-`:88`
shift +5; `:94` and below shift +7. The replaced block `:88-93` becomes
`:93-100`.**

| what | `origin/master` | here |
|---|---|---|
| `set -euo pipefail` | `:16` | `:16` |
| `REPO=` / `readonly REPO` | `:19-20` | `:24-25` |
| `PATH="$TOOLS_BIN:$PATH"` | `:26` | `:31` |
| `need`'s install-tools hint arm | `:63` | `:68` |
| `containerfile_arg`'s `die` | `:81` | `:86` |
| the `image_tag` comment | `:88-93` | `:93-100` (8 lines, not 6) |
| `version="$(containerfile_arg …)"` | `:100` | `:107` |
| `provider_version`'s `die` | `:110` | `:117` |
| "Both path filters already treat them…" | `:124-125` | `:129-130` |
| `source_revision()` | `:129` | `:136` |
| `provider="$(provider_version)"` | `:131` | `:138` |
| `sha="$(git … rev-parse HEAD)"` | `:134` | `:141` |
| the SC2312 comment | `:135-139` | `:142-146` |
| `dirt="$(git … status --porcelain …)"` | `:140` | `:147` |

**Who points at those numbers.** `grep -rn 'lib\.sh:[0-9]'` over the whole tree,
excluding this lane's own plans and transcripts, returns hits in exactly three
places:

1. **One live file outside this lane's set: `.github/workflows/image.yml:60`** —
   "source_revision (lib.sh:129) calls only `rev-parse HEAD` and `status
   --porcelain`". The claim stays true; the number becomes `:136`. F1.
2. **One live file that survives:** `scripts/smoke-libvirt.sh:454` cites
   `lib.sh:16`'s `set -e`, which did not move.
3. **Archived evidence**, which is quoted-at-a-date by construction and is not
   updated: `docs/tooling-2026-08-30.md`, `docs/review-2026-08-30/`,
   `docs/review-2026-08-31/`, `docs/review-workflow-gate/`,
   `docs/review-tofu-module/`, and `docs/plans/issue-87.md`,
   `issue-90-pipeline-comments.md`, `issue-92.md` from earlier lanes of this
   campaign. 60-odd hits, none of them a claim about the current tree.

`grep -n 'lib\.sh' CLAUDE.md README.md` returns nothing at all — neither file
names the library, let alone a line in it. No test other than the new one does.

**Two comments this branch makes true without editing them.**
`lib.sh:142-146` says a bare assignment sends a failing `git status` to "the
`set -e` in this file", and `lint.sh:183-187` says both of `454ee7c`'s two real
findings "now assign on their own line, where `set -e` sees the failure". At
`origin/master` the first is false — measured, a `git` that succeeds for
`rev-parse` and fails for `status` yields a clean 40-hex SHA at exit 0 through
`sha="$(source_revision)"` — and the second is half false, since
`image-build.sh:41`'s `built="$(now_utc)"` is one level and always worked. Both
hold here. Editing them would be surface for no behaviour, which is why the plan
listed them as non-goals and why they are not in the diff.

**One citation this branch creates.** `lib.sh:98` points at `lib.sh:21` from
inside the same file. Same class as the finding the workflow-gate lane raised
against its own `.gitlab-ci.yml:170-172`: correct as landed, and silently wrong
after any insertion above line 21. Accepted here for the same reason — the
comment has to name the thing that closes the hole, and naming it by wording
would be vaguer than naming it by line.

---

## Ledger

### Raised

| | |
|---|---|
| **F1** | `.github/workflows/image.yml:60` cites `source_revision` as `lib.sh:129`; this branch moves it to `:136`. Correct at `origin/master`, stale here. Out of this lane's permitted file set, so not fixed. The claim the comment makes — that `--depth 1` suffices because the function calls only `rev-parse HEAD` and `status --porcelain` — is unaffected and still true. |
| **F2** | The `install-tools.sh` chain `#76` reports is closed by measurement and covered by no assertion. Priced and declined in `issue-76.md` §7; recorded here so it is not re-derived as a new finding, and so the gap is on the record if `:68` is ever annotated away. |
| **F3** | `scripts/os-deps.sh` and `scripts/smoke-libvirt.sh` were read, not run — the first sudo-installs packages, the second starts a system daemon. Reading is a weaker instrument than the other six blast-radius items got. CI's `smoke` job is the only thing that can close this, and it must be green before this merges. |
| **F4** | `lib.sh:98`'s self-citation of `:21`. See Lens 3. Accepted, not fixed. |

### Confirmed

* **`#77`'s defect, in full**, on `411c12d`. Shape B (`tag="$(image_tag)"`, guard
  two levels down) exits 0 with `localhost/vcows-deploy:` in hand without the
  option and 1 with it. Shape D (one level, assignment) exits 1 either way —
  that is the boundary the issue claims, and it holds. Shape C (one level,
  argument) exits 0 either way and is what `check-extra-masked-returns` is for.
* **`#76` is subsumed, measured not asserted.** A hadolint payload whose sha256
  is not the pin at `install-tools.sh:36` lands in `.tools/bin`, executes, and
  the script exits 0 without the option; with it, `.tools/bin` is empty and the
  exit is 1. The unpinned-digest arm goes from a cascade ending at `unzip`'s exit
  9 to exit 1 at `digest`'s own `die`.
* **`#77`'s consequence is worse than `#77` says.** Its evidence is the
  `.git`-absent case (`--build-arg GIT_SHA=`, exit 0). With a `git` that succeeds
  for `rev-parse` and fails for `status`, `image-build.sh` records
  `--build-arg GIT_SHA=1111…1111` — a clean 40-hex SHA for a tree whose dirty
  state was never determined — at exit 0. Exit 42 with the option.
* **The two mechanisms are complementary.** `shellcheck -o all` is silent on
  shape B (only SC2250/SC2292 style noise); the repo's four flags emit SC2312 on
  shape C, which `inherit_errexit` does not fix. The assignment form in
  `image_tag` stays, and the new comment says why.
* **Blast radius, off then on at `411c12d`.** `just check` 412 → 414 passed / 25
  skipped, six lint gates ok both ways. `just lint`, `just verify-provider` and
  `bash scripts/bundle.sh` byte-identical. `just image` exit 0 in 45s,
  `org.opencontainers.image.revision` a clean `411c12d1…`. `just test-image`
  422 → 424 passed / 15 skipped, the +2 being these tests.

### Refuted

| | |
|---|---|
| **`#77`'s "five distinct inert guards"** | Six, of two kinds, and the issue's own table lists six. Three `die` guards — `lib.sh:86` via `image_tag:107`, `lib.sh:117` via `source_revision:138`, `install-tools.sh:40` via `fetch:63` — and three unchecked commands — `lib.sh:141` (`git rev-parse`), `install-tools.sh:67` (`curl`), `:68` (`sha256sum -c`). The "five" collapsed `curl` and `sha256sum` into one. Corrected in the commit body. |
| **`#77`'s `lint.sh:159` citation** | Stale, and stale twice: `:183-188` when the plan was written, `:193-198` on this branch's base. The claim it carries is unchanged. |
| **`#76`'s caller list** | "`ci.yml:50,79`" and "`.gitlab-ci.yml:38` (all four jobs)" both undercount. GitHub runs the script three times in `ci.yml` (`:50`, `:79`, `:114`, jobs at `:30`, `:54`, `:93`) plus `image.yml:72` and `scheduled.yml:64`. GitLab reaches five jobs by two routes: the `&bootstrap` anchor at `:44` spliced at `:57`, `:82`, `:164`, `:195`, and `smoke`'s direct call at `:120`. |
| **Issue 90's `lib.sh:88-93` item, as stated** | It says the comment diagnoses the empty tag as an argument-position problem and that this is wrong. Half right. Argument position **is** a real mechanism — shape C fails open where shape D does not — it is simply not the mechanism at `image_tag`'s call sites, which are two levels deep, where both forms fail open. `950ca7e` installed the comment in the same commit that rewrote `image_tag` into the assignment form, so it is that remedy's own record of what it thought it had done. The replacement says both halves. |
| **`issue-77.md` §5's `(:17)`** | The option lands at `:21` under the plan's own layout. Corrected before commit. |

### Downgraded

| | |
|---|---|
| `#76` | From an issue with its own fix to zero lines in `scripts/install-tools.sh`. Site six of `#77`'s six, closed by the same option, proved on the same harness. |
| `tests/test_shell_errexit.py` | From a file named for one defect to `tests/test_scripts.py`, a rig for `scripts/*.sh`. Same two tests; the justification moves from this defect to the gap three lanes measured independently — nothing in the suite executed a shell script. |
| `#77`'s remedy for `install-tools.sh` | The issue contemplates guarding `:68`. Measured redundant: with the option on, `:68`'s non-zero already ends the substitution, and `sha256sum`'s own `WARNING: 1 computed checksum did NOT match` already names the file and the mechanism. |

### Not closed by this branch

`Closes #77` and `Closes #76` are in commit `213dadc`. **Issue 90 stays open** —
this branch corrects one of its items and carries no closing keyword for it. Its
remaining items are elsewhere in the tree and are other lanes' work. Issue 95
(`install-tools.sh:107`) is untouched and was measured identical on both sides of
this change.
