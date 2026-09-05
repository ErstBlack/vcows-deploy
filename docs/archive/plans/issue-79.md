# Issue #79 — `bundle.sh` ships the image a failed scan rejected

Reverified at `aed962d`. Raw output: `docs/review/scan-bundle/reverify/RX-E6.txt`.

## 1. Reverification verdict

**Reproduced end to end, twice, at `aed962d`.** Two scratch clones, both running `scripts/`
byte-identical to `aed962d` (proved by `diff -r` in the transcript), both fed the real 444 MB
docker-archive, the real 7.0 MB SBOM, and the real 2.4 MB trivy report with one finding injected.

The two commands `README.md:262-264` documents, in order:

```
$ cd <scratch> && ./scripts/image-scan.sh
saving localhost/vcows-deploy:0.1.0.0
scanning
  report <scratch>/.cache/scan/trivy.json
  sbom   <scratch>/.cache/scan/sbom.spdx.json
findings not in docs/cve-baseline.json:
  CVE-2099-99999
error: 1 new finding(s)
EXIT=1

$ ls -A <scratch>/.cache/scan
image.tar
sbom.spdx.json
trivy.json
EXIT=0

$ cd <scratch> && ./scripts/bundle.sh
compressing image.tar -> vcows-deploy-0.1.0.0-bbd96bab86fdf4badeb6072c43218b953f56fe31.tar.gz

bundle  <scratch>/.cache/delivery
  vcows-deploy-0.1.0.0-bbd96bab86fdf4badeb6072c43218b953f56fe31.tar.gz  (144M)
  sbom.spdx.json, trivy.json, image.tar.sha256, SHA256SUMS
EXIT=0   (wall 86s)

$ cd <scratch>/.cache/delivery && sha256sum -c SHA256SUMS
vcows-deploy-0.1.0.0-bbd96bab….tar.gz: OK
sbom.spdx.json: OK
trivy.json: OK
image.tar.sha256: OK
EXIT=0

$ jq -c '…|map(select(startswith("CVE-2099")))' <delivery>/trivy.json
["CVE-2099-99999"]
EXIT=0
```

A complete, correctly named, internally consistent, checksum-verifying 153 MiB delivery bundle for
an image the CVE gate rejected, with the rejected finding sitting inside the shipped report. The run
above is `e6b`, where the tree sits at the archive's own recorded revision, so **`bundle.sh:62-65`
stayed silent** — no output at all between the failed scan and the finished bundle except the
bundle's own success lines.

The second run, `e6a`, is the same thing with the tree at `aed962d`. Identical outcome, `EXIT=0`,
identical bundle, plus one non-fatal line:

```
warning: the archive was built at bbd96bab86fdf4badeb6072c43218b953f56fe31 but the tree is at aed962d48ee85641ef9580515b698c50881a271d
         run 'just image && just scan' to bundle the current tree
```

That warning is about rebuild skew, not about the scan verdict, and it fires or not independently of
whether the scan passed. Both runs produced a `.tar.gz` with the same digest,
`d6250b3f32c6f7b32b31572b7df2b0d3b834e819fb3424c29179b04d966ee7bc`.

`ls -A .cache/scan` after the failed scan returns three files and nothing else. There is no record
anywhere on disk that the gate said no.

## 2. Anchor table

Every line re-read at `aed962d`. Both scripts the issue cites **did** change between the review's
`672a500` and HEAD, and one workflow file moved by 34 lines.

| anchor | 672a500 | aed962d | state |
|---|---|---|---|
| `bundle.sh:50-52` the whole precondition | `:50-52` | `:50-52` | **unmoved and unchanged.** `[ -f "$f" ]` over `image.tar`, `sbom.spdx.json`, `trivy.json`. Still no scan-status input of any kind |
| `bundle.sh:54-56` labels → `name` | same | same | unchanged |
| `bundle.sh:58-65` the non-fatal revision warning | `:58-65` | `:58-65` | unchanged. The issue and the brief cite `:61-65`; `:61` is `worktree="$(source_revision)"`, the `if` is `:62-65`, the three-line comment is `:58-60` |
| `bundle.sh:93` `( cd "$scan" && sha256sum image.tar )` | same | same | unchanged |
| `bundle.sh:107` `du -h … \| cut -f1` | `:107` | `:107` | **changed**: gained `\|\| true` for SC2312. The only diff in the file across the range, `1 ++--`, and it does not move a line |
| `image-scan.sh:34` `rm -f "$out"` in `save_archive` | same | same | unchanged. The one place `.cache/scan` is cleared |
| `image-scan.sh:71` `out="$REPO/.cache/scan"; mkdir -p "$out"` | same | same | unchanged |
| `image-scan.sh:77 / :80-81` archive, report, SBOM written | same | same | unchanged. All three land before the baseline is read at `:113` |
| `image-scan.sh:93-102` `--write-baseline`, returning at `:101` | same | same | four `\|\| true` added, lines unmoved |
| `image-scan.sh:123` `die "… new finding(s)"` | same | same | gained `\|\| true` inside the `$(…)`; behaviour unchanged |
| `image-scan.sh:141` the last line of a passing run | same | same | unchanged |
| `README.md:259-267` the documented delivery procedure | same | same | file unchanged across the range. The three-line block is `:262-264`; `:267` still says "`just bundle` is what produces the artifact that goes on the medium" |
| `.github/workflows/image.yml:79-80` `just scan` then `just bundle` | `:79-80` | `:79-80` | unchanged, file unchanged. `upload-artifact` at `:90` still carries no `if: always()` |
| `.gitlab-ci.yml` the same pair, twice | `:122-127`, `:153-158` | **`:160-161`, `:190-191`** | **moved.** The file gained 34 lines between the two commits (the TCG smoke job from PR #73). Both jobs still run the two recipes consecutively |

`git diff --stat 672a500 aed962d -- scripts/bundle.sh` → `1 +-`. `.gitlab-ci.yml` → `34 ++++`.

## 3. Corrections to the issue body

**The mechanism, the reach and the reproduction all hold.** Three corrections, one of which matters
for anyone reusing the Phase 0 artefacts.

**C1 — the cached archive is not the one the review used, and its revision is not `672a500`.**
`/home/ssullivan/vcows-deploy/.cache/scan/image.tar` carries
`org.opencontainers.image.revision = bbd96bab86fdf4badeb6072c43218b953f56fe31`, created
`2026-08-30T23:40:29Z`. `bbd96ba` is "Copy the entrypoint with --chmod instead of a following RUN
chmod", on branch `issues-32-28-44`, and `git merge-base --is-ancestor bbd96ba 672a500` exits 1 —
it is not an ancestor of the review's pin at all. `.cache/scan` was regenerated after the review.
Consequences:

* `git diff --name-only bbd96ba aed962d -- orchestrator container licenses Containerfile
  .containerignore` lists **eight** files (`container/entrypoint.py`, `orchestrator/cli.py`,
  `orchestrator/qcow2.py`, `orchestrator/tofu.py`, and four under `orchestrator/backends/libvirt/`).
  The same diff for `672a500 aed962d` is empty. So the "no image-shipped path changed, therefore the
  artefacts are byte-valid" argument is true of `672a500` and **false of the commit the archive was
  actually built from.**
* It does not change this reverification. `bundle.sh` has no scan-verdict input regardless of which
  commit produced the archive, and the archive is a genuine docker-archive with readable labels,
  which is all the reproduction needs. It is why the transcript contains two runs: `e6b` had to be
  detached at `bbd96ba` to reproduce the review's silent-warning case.
* Anyone who wants a bundle that matches HEAD must rebuild. That is not needed to fix this issue.

**C2 — the sizes.** The issue says "a correctly named 144M bundle". The `.tar.gz` is
**150,814,968 B** (144 MiB, 151 MB) and the whole `.cache/delivery` is **160,203,516 B** (153 MiB).
Same numbers `verify/E-lows.md` §RX-E10 recorded. "144M" is `du -h` on the tarball only, which is
what `bundle.sh:107` prints.

**C3 — "the steps are consecutive and there is no `if: always()`" is still true, at moved lines.**
Re-read at HEAD: `image.yml:79-80` and `:90`; `.gitlab-ci.yml:160-161` and `:190-191`. CI remains
safe. The exposure is the hand-run path and only the hand-run path.

## 4. The defect

`bundle.sh:50-52` is the entire precondition:

```sh
for f in "$archive" "$sbom" "$report"; do
    [ -f "$f" ] || die "no $(basename "$f") -- run 'just scan' first, which writes it"
done
```

It asks whether `just scan` *ran*. It has no way to ask whether `just scan` *passed*, because
`image-scan.sh` records that answer only in its own exit status, and an exit status does not survive
the process. `README.md:262-264` documents the two as separate commands, so the second one routinely
runs in a different shell — often a different day.

The ordering is what makes the window total rather than partial. `image-scan.sh` writes the archive
at `:77` and the report and SBOM at `:80-81`. The baseline is not read until `:113`, and the `die`
for a new finding is `:123`. Every file `bundle.sh` requires is on disk, complete and current,
*before the gate is capable of rejecting anything*. There is no partial-write state and no cleanup
path: a passing `.cache/scan` and a failing one are byte-identical apart from the contents of
`trivy.json`, and nothing reads `trivy.json` for a verdict — `bundle.sh` only copies it at `:97`.

The artefact that results is this repo's second failure class, not the first: not visibly broken,
but plausible and wrong. Its filename claims the right version and a clean 40-hex revision, its
`SHA256SUMS` verifies on all four files, and the only record of the rejection is one id inside a
2.4 MB report that nothing at the receiving site reads.

## 5. The fix

**`image-scan.sh` writes a verdict file into `.cache/scan/` only on a full pass; `bundle.sh`
requires it and requires it to describe the archive it is about to ship.**

The constraint the issue states — `.cache/scan` is deliberately durable, deleting it on failure is
wrong, re-scanning inside `bundle.sh` doubles a slow step, and whatever is recorded has to survive
the bundle running in a separate shell — is satisfied by a file in the directory that is already the
interface between the two scripts. A file survives a separate shell; an exit status, an environment
variable and a shell variable do not.

### The shape

**`.cache/scan/PASSED`, holding exactly `sha256sum image.tar` output.** One line, one existing
format, no new parser, no `jq`.

In `image-scan.sh`:

* **Clear** at the top of `main()`, immediately after `out="$REPO/.cache/scan"; mkdir -p "$out"`
  (`:71`) and before `save_archive` at `:77`. One line, `rm -f "$out/PASSED"`. Placing it there
  rather than inside `save_archive` means a crash anywhere after that point — including inside
  `podman save`, `trivy`, `syft`, `scan_floor`, or either baseline check — leaves no stamp.
* **Write** as the last act of a passing run, after `:141`. One line,
  `( cd "$out" && sha256sum image.tar ) > "$out/PASSED"`.
* The `--write-baseline` branch returns at `:101`, after the clear and before the write, so a
  `--write-baseline` run never authorises a bundle. That is correct and worth keeping deliberate:
  recording what is there now is not the same as accepting it.

In `bundle.sh`, in the `:50-52` loop's own block:

```sh
[ -f "$scan/PASSED" ] ||
    die "no PASSED stamp in .cache/scan -- 'just scan' has not accepted this archive. Run it and read what it says before bundling."
( cd "$scan" && sha256sum -c --status PASSED ) ||
    die "the PASSED stamp in .cache/scan does not describe image.tar -- re-run 'just scan'"
```

### Cost, measured

`sha256sum` over the 444 MB archive is **2.0s** (2.02s, 2.26s, 2.34s across three runs, warm and
cold). `gzip -9` over the same archive is **86-89s**, measured in both transcript runs. So the check
adds ~2.3% to `just bundle` and ~2s to `just scan`. Two extra hashes total.

`bundle.sh:93` already computes the identical digest for `image.tar.sha256`. It could be replaced
with `cp "$scan/PASSED" "$out/image.tar.sha256"`, making the net cost zero. **Do not.** The two files
have different jobs — one is a delivery artefact a site checks, one is an internal verdict — and
coupling them means any later change to the stamp's format silently changes the shape of a file
`README.md:269-272` describes to sites. 2 seconds is the right price for keeping them independent.

### How a stale verdict is handled, and what happens to the existing warning

The brief's case — "scan passed yesterday, image rebuilt today" — has two readings, and they resolve
differently.

**Reading A: `just image` reruns, `just scan` does not.** `just image` builds a podman image; it does
not touch `.cache/scan`. The archive, report, SBOM and stamp all remain yesterday's and remain
mutually consistent, so `bundle.sh` proceeds and ships yesterday's image — **correctly.**
`bundle.sh:14-19` is explicit that the bundle is named from the archive precisely so this case
cannot lie, and `:58-60` is explicit that "delivering an older image on purpose is legitimate". The
signal for it already exists, already fires, and was measured firing in `e6a`. **Reuse it. Do not
replace it, and do not make it fatal.**

The two checks answer different questions and must have different severities:

| | question | legitimate answer "no"? | severity |
|---|---|---|---|
| `bundle.sh:62-65` | is this archive the current tree? | **yes**, deliberately | warn, non-fatal |
| the `PASSED` check | did the CVE gate accept this archive? | never | `die` |

Merging them gives one of two wrong outcomes: a fatal revision check breaks the deliberate
older-delivery path `:58-60` defends, or a non-fatal verdict check leaves exactly this issue open
with an extra line of log. Measured evidence that the existing warning cannot stand in for the new
one: in `e6b` it printed nothing at all while a rejected image was bundled.

**Reading B: `.cache/scan` becomes internally inconsistent.** A second `image-scan.sh` run that
clears the stamp, rewrites `image.tar`, and then dies; an `image.tar` copied in by hand; a partially
restored cache. The digest binding is what covers these — the stamp is bound to the bytes it
approved, not to a clock, so it cannot be inherited by a different archive. This is the only reading
where "stale" is a defect, and it is the reason the stamp holds a digest rather than being empty.

Deliberately **not** recorded in the stamp: a timestamp, the tag, or the baseline's `generated`
field. An age limit would fail a legitimate older delivery for a reason unrelated to the CVE
verdict, and every extra field is a format to keep true. `stat .cache/scan/PASSED` answers "when" for
a human who asks.

### Rejected

* **Delete `.cache/scan` when the scan fails.** The issue rejects it and is right for a reason worth
  restating: the report a failing scan wrote is the artefact a maintainer reads to decide whether to
  accept the new finding into the baseline. Deleting it forces a second slow scan to look at the
  thing that just failed.
* **Re-scan inside `bundle.sh`.** Doubles `podman save` + `trivy` + `syft` over 444 MB, and makes
  `just bundle` fail for a reason that is not about bundling. This is the option the issue's
  "Validate before working" section exists to exclude.
* **`bundle: scan` in the `justfile`.** Same doubling as above on every bundle, and it does not close
  the hole: `README.md` documents three separate commands and `scripts/bundle.sh` remains directly
  runnable.
* **Rely on `bundle.sh:62-65`.** Measured silent in the dangerous case. Covered above.
* **Rely on CI ordering.** CI is safe and was re-verified safe. The documented hand-run path is not,
  and `README.md:267` calls it the thing that produces the artifact that goes on the medium.
* **An empty `PASSED` marker with no digest.** ~4 lines lighter and cannot tell a pass for one
  archive from a bundle of another. The gap costs 2.0s to close.
* **`[ "$scan/PASSED" -nt "$archive" ]`.** O(1) instead of 2s, but mtimes survive `cp -a`, `rsync -a`
  and cache restores, so it binds to nothing durable. A weaker guarantee for a saving that is 2% of
  one step.
* **Recording the verdict inside `trivy.json`.** The report is a shipped artefact produced by a
  third-party tool. Adding a vcows key to it is claiming ownership of a format this project does not
  own, and it would travel to sites.

## 6. Surface cost

Two files, three lines of code plus their comments.

* `image-scan.sh`: `rm -f "$out/PASSED"` after `:71`, and one `sha256sum` redirect after `:141`.
* `bundle.sh`: two `|| die` clauses inside the existing `:50-52` block.

One new file in a directory that is already the interface between the two scripts, in the same
format as a file `bundle.sh` already writes. No new dependency (`sha256sum` is coreutils and
`bundle.sh:93` already calls it), no new function, no new `justfile` recipe, no schema, no change to
what ships. Roughly +12/−0 including comments. This matches the finder's own estimate of "about four
lines across the two files"; the extra is the digest binding and the two comments.

## 7. The failing test

**Nothing in the suite executes `scripts/*.sh`.** Measured: `grep -rn "scripts/" tests/
--include=*.py` gives three hits. `conftest.py:84` is a message string, `test_image.py:266` is a
docstring, and `test_image.py:274` — `subprocess.run(["bash", "-c", f"source {REPO}/scripts/lib.sh
&& source_revision"])` — is the only shell the suite runs, inside a module gated on `image` that the
default `pytest -q` skips (10 of the 25 skips).

**What that costs here.** `bundle.sh` is the script that produces the only artifact this project
delivers, and its behaviour is asserted by nothing. `shellcheck` reads it and would not notice the
precondition being deleted. The 411 tests would all pass with `bundle.sh:50-52` removed entirely.
This defect went unnoticed through the whole life of the script for that reason, and the fix would
regress the same way.

**Proposed: `tests/test_scripts.py`, one new file, shared with #83.**

`bundle.sh` needs a docker-archive, but not a real one — `archive_label` (`:32-39`) reads
`manifest.json` and one config blob, both plain JSON inside a tar. A 2 KB fixture is enough:

```
def _fake_archive(path, version, revision):
    """manifest.json + a config blob carrying the two OCI labels bundle.sh reads."""
```

The rig is a `tmp_path` repo holding `scripts/` (copied), a `Containerfile` with one
`ARG VCOWS_VERSION=`, `orchestrator/backends/libvirt/tofu/main.tf` with the pinned provider line
`lib.sh:109` parses, an empty `docs/provider-0.9.8.lock.hcl`, and `git init` + one empty commit so
`source_revision` returns a real SHA rather than exercising RX-E9's empty-string path.

```
def test_bundle_refuses_a_scan_directory_no_scan_accepted(tmp_path):
    repo = _shell_repo(tmp_path)
    _scan_dir(repo, passed=False)              # the three files, no PASSED
    r = _run(repo / "scripts/bundle.sh")
    assert r.returncode == 1
    assert "has not accepted this archive" in r.stderr
    assert not (repo / ".cache/delivery").exists()

def test_bundle_refuses_a_stamp_that_describes_a_different_archive(tmp_path): ...
def test_bundle_proceeds_when_the_stamp_matches(tmp_path): ...
```

The third is the regression guard that matters most: a precondition that also refuses valid input is
worse than none.

**No new `VCOWS_GATES` name and no skip.** `tests/test_gates.py:28` holds `KNOWN = {"tofu", "image",
"rig", "pycdlib", "libvirt"}` as a closed set and AST-walks the suite for bare `pytest.skip`,
`pytest.importorskip` and `pytest.mark.skip`. These tests need `bash`, `jq`, `gzip`, `tar`,
`sha256sum` and `git` — no podman, no image, no network, no 444 MB. `jq` and `git` come from
`scripts/os-deps.sh:30,32`, which every job running the suite executes first
(`.github/workflows/ci.yml:49`, `.gitlab-ci.yml:36-38`). They run unconditionally.

Cost: about 90 lines with the fixture, of which `_shell_repo` is shared with #83's threshold tests.
**The fixture belongs in the new file, not in `conftest.py`** — `conftest.py` is the gate mechanism
and `test_gates.py` treats it as the one privileged implementation file. If #79 and #83 land as one
branch, which `CLAUDE.md` prefers for issues touching the same file, the file is written once.

## 8. Verification

For the implementer. Nothing below has been run against a fix; this plan changes nothing.

1. `just check` — expect the `aed962d` baseline plus the new tests: six lint gates ok, `ty` clean,
   `411 passed, 25 skipped`. Confirmed green at `aed962d` today, unpatched.
2. **The negative, from `RX-E6.txt`.** Re-run the harness unchanged. `image-scan.sh` must still exit
   1 with `error: 1 new finding(s)`; `ls -A .cache/scan` must now show three files and no `PASSED`;
   `bundle.sh` must exit **1** naming the missing stamp, and `.cache/delivery` must not exist.
3. **The positive, and it is the one that can regress.** Remove the injected finding so the scan
   passes. `image-scan.sh` exits 0 and `.cache/scan/PASSED` appears. `bundle.sh` exits 0 and produces
   a `.tar.gz` whose sha256 is
   `d6250b3f32c6f7b32b31572b7df2b0d3b834e819fb3424c29179b04d966ee7bc` — the digest both transcript
   runs produced from this archive. The gzip payload does not depend on the report, so this is an
   exact byte-level assertion that the fix changed nothing on the passing path.
4. `./scripts/image-scan.sh --write-baseline` in a scratch copy with a scratch `BASELINE`, then
   confirm no `PASSED` was written and `bundle.sh` still refuses.
5. Kill `image-scan.sh` mid-run (or make the fake `trivy` exit 1) after a previous passing run, and
   confirm the old `PASSED` is gone. This is what the clear at `:71` is for.
6. `shellcheck -x -s bash` with the four optional checks from `lint.sh:183-187` on both scripts.
   `( cd … && sha256sum -c --status PASSED ) || die …` is a compound command in a condition, so
   `check-extra-masked-returns` is the one to watch.
7. Do **not** run `scripts/image-scan.sh --write-baseline` against `docs/cve-baseline.json`. It
   regenerates the object with only `image`, `generated`, `note` and `accepted`, destroying every
   `why` and `recheck`.

## 9. Non-goals

* Rebuilding the image. C1 records that `.cache/scan` is from `bbd96ba` and that eight image-shipped
  files have changed since. Nothing in this fix depends on the archive matching HEAD.
* Signing the bundle. Removed deliberately, recorded in `docs/ci.md` and issue #6.
* The GPL source medium no script produces (D22).
* `image.tar.sha256`'s "usable directly" claim and the receipt block (RX-E12, a nit, explicitly left
  to ride along with some other `bundle.sh` edit — this is not that edit).
* The GitLab artifact size cap (RX-E10) and the `.gitlab-ci.yml:10-18` comment it wants.
* `bundle.sh:62-65`. It stays exactly as it is, non-fatal, for the reason in section 5.
* The scan's own threshold guard. That is #83.
