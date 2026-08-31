# E — low and nit findings, confirmed

Confirmer, Phase 3 · `RX-E7`–`RX-E12` · verified at `672a500` in the detached worktree, against
`.cache/scan/trivy.json` and `.cache/delivery/` as Phase 0 wrote them. No tracked file was edited;
every mutation ran in a `mktemp -d` copy. `--write-baseline` was not run.

## RX-E7 — the baseline `note`'s overlap arithmetic — CONFIRMED, low

`docs/cve-baseline.json:4` re-verified: the `note` is on that line and reads "37 + 55 + 18 findings
across three targets are 100 distinct ids, because CVE-2026-56854 and GO-2026-5932 are reported
against both Go binaries."

Measured with `jq` over `.cache/scan/trivy.json`, unique `VulnerabilityID` per target:

| | |
|---|---|
| rocky / provider / tofu | 55 / 37 / 18 — the note's three counts, exact |
| ids on more than one target | **11** |
| distinct | 110 − 11 = **99** |

The eleven: `CVE-2026-25680`, `-25681`, `-27136`, `-39821`, `-42502`, `-42506`, `-46600`, `-56852`,
`-56854`, `GHSA-hrxh-6v49-42gf`, `GO-2026-5932`.

Two separate errors, and the finder conflates them slightly:

* **The reason clause was never right.** It names 2 shared ids where 11 exist, and its own
  arithmetic (110 − 2 = 108) does not reach the 100 it asserts. Wrong when written, wrong now.
* **"100 distinct" was right when written and is stale now.** `jq '.accepted|length'` is 100, and
  `accepted − live = ["CVE-2026-58055"]`, `live − accepted = []`. So the file's own accepted set was
  the distinct set at `generated: 2026-08-30T02:35:19Z`; one stale row makes it 99 today. The
  consistent reconstruction is that rocky carried 56 ids then (56+37+18 = 111, − 11 = 100), so the
  `55` in the note was the count that was one low at generation and is exact now.

Correct sentence, post-trim: 37 + 55 + 18 across three targets are **99** distinct ids, because
**11** ids are reported against both Go binaries. Fold it into the `CVE-2026-58055` hand-edit as the
finder says; it is the sentence a maintainer reads before editing this file.

## RX-E8 — `accepted` carries no group membership — DOWNGRADED to nit, leave it

The structural claim is true and re-verified. `docs/cve-baseline.json:5` opens `rationale` with six
groups carrying `count`, `why`, `recheck` and sometimes `sources` and no id list; `:48` opens
`accepted` as a flat sorted array of 100 strings. Nothing in the file maps an id to a `why`.

But this is the recorded design, not a gap. `.claude/skills/cve-triage/SKILL.md:67-69` prescribes
exactly this shape as the accepting hand-edit — "Add the ID to `accepted`, and either extend an
existing `rationale` group or add a new one carrying `count`, `why` and `recheck`" — and
`docs/ci.md:114` and `CLAUDE.md:78` describe the file the same way. Two of the six groups
(`CVE-2026-56854`, `CVE-2026-11979`) are single-id and self-attributing; the other four are
attributable from any scan report by target and package, which is how RX-E1's own author placed
`CVE-2026-58055` in `rocky-base`.

Against that, the fix is the largest in dimension E: an `ids` array per group, `accepted` derived,
and `image-scan.sh:113-117`'s single-read `jq` rewritten to build the set difference from a nested
shape. That is new schema plus new coupling for a lookup that has been needed once and took a
`jq | sort` to answer. Leave it. If anything is done, it is one sentence in the `note` saying that
group membership is read off the scan report, not the file.

## RX-E9 — `source_revision` returns "" at status 0 — CONFIRMED, low, but it is RX-E2

`scripts/lib.sh:134` re-verified as `sha="$(git -C "$REPO" rev-parse HEAD)"`, inside
`source_revision` (`:129-140`). Reproduced on a full `tar`-copy of the tree with `.git` excluded and
nothing else changed:

```
$ bash -c 'source scripts/lib.sh; rev="$(source_revision)"; echo "rev=[$rev] rc=$?"'
fatal: not a git repository (or any of the parent directories): .git
fatal: not a git repository (or any of the parent directories): .git
rev=[] rc=0
```

Downstream, verified rather than assumed. `--build-arg GIT_SHA=""` does override the Containerfile
default — a two-line probe image (`ARG GIT_SHA=unknown` / `LABEL rev="${GIT_SHA}"`) built with
`--build-arg GIT_SHA=""` inspects as `"rev":""`. So `Containerfile:205`'s
`org.opencontainers.image.revision` ships empty. `container/manifest.py:79-92` is the half that
holds: `git_sha()` pattern-matches and degrades to `unknown`, exactly as its docstring says. And
`bundle.sh:55` does die — `archive_label` (`bundle.sh:32-39`) calls `die` directly, one level, so the
substitution's own `exit` propagates — with "image in …/image.tar carries no
org.opencontainers.image.revision label", naming the archive rather than the missing repo.

Real but narrow: the trigger is a build from an exported tree with no `.git`, which no pipeline path
takes. File it as a named instance under RX-E2 — `shopt -s inherit_errexit` turns it into a stop at
`image-build.sh:38` — not as a second issue with a second fix.

## RX-E10 — the GitLab delivery artifact exceeds the default cap — CONFIRMED, low

Two halves, and only one is measurable here.

**Verified.** `.gitlab-ci.yml:131-135` and `:161-165` both declare `artifacts: paths: [.cache/delivery/]`
with `expire_in: 90 days` and no `artifacts:exclude`. `.cache/delivery/` as Phase 0 left it is
**160,212,457 bytes (153 MiB)** — `vcows-deploy-0.1.0.0-672a500….tar.gz` at 150,790,660 B (144 MiB),
`sbom.spdx.json` 7.1 MB, `trivy.json` 2.4 MB, plus two small text files. The finder's "156 MB" is
neither the MiB nor the MB total; the tarball alone is 151 MB and the directory is 160 MB. Already
gzip -9, so the runner's zip will not shrink it.

**From documentation, not observed.** GitLab's default maximum artifacts size is **100 MB**, applied
per artifact file, settable per instance, group and project; GitLab.com raises it. This review cannot
see the target instance — `.gitlab-ci.yml:1-4` says the instance does not exist yet and the file has
never been executed. Both readings of the cap (per file or per job) reject this upload, since the
tarball alone is over.

The finder's fix is right and is the whole of it: name the setting in the runner assumptions at
`.gitlab-ci.yml:10-18`, beside the tag assumptions that are already there for the same reason. One
comment block. Do not add `artifacts:exclude` or a split — the bundle is the deliverable and
splitting it is the packaging bug `bundle.sh:10-19` exists to have stopped.

## RX-E11 — `fetch-depth: 0` justified by a claim that does not need it — CONFIRMED, nit

`.github/workflows/image.yml:60-64` re-verified verbatim: the comment "Full history: image-build.sh
computes the -dirty suffix from `git status --porcelain` over the shipped paths." sits directly above
`fetch-depth: 0`. `.github/workflows/scheduled.yml:47-48` carries the same `fetch-depth: 0` with no
comment.

`grep -rno 'git [a-z-]*' scripts/ container/ orchestrator/ justfile` finds exactly two git calls in
the whole build path: `lib.sh:134` `rev-parse HEAD` and `lib.sh:135` `status --porcelain`. Neither
reads history. Measured in a `--depth 1` clone of `master`:

```
commits: 1     rev-parse: 672a500a5f3db394e91a3b91fb383517e504246d
status rc: 0   shallow: true
```

Full 40-hex SHA, working porcelain. The setting is harmless on a repo this size; the comment states a
requirement that does not exist, which is the S5 shape — a reason that has quietly become false is
what gets copied into the next workflow. Fix is the comment, not the setting: either drop
`fetch-depth: 0` or say what actually wants it.

## RX-E12 — `image.tar.sha256`'s "after decompressing" check — CONFIRMED, nit, leave it

`scripts/bundle.sh:90-93` re-verified: the comment claims the digest is "written in `sha256sum -c`
format so it is usable directly rather than read by eye", and `:93` writes it as
`( cd "$scan" && sha256sum image.tar )`. The receipt block at `:110-111` prints two commands,
`sha256sum -c SHA256SUMS` and `gunzip -c $name | podman load`, and `README.md:277-280` prints the same
two. Neither materialises `image.tar`, so `sha256sum -c image.tar.sha256` has nothing to check
against and the "usable directly" claim is unreachable from the documented procedure.

The file itself is correct — the finder verified `gunzip -c ….tar.gz | sha256sum` equals it — and one
undocumented pipe recovers the check. Nothing is wrong, only unstated. If it is touched at all it is
one line in the receipt block; it does not need its own issue and can ride along with any other edit
to `bundle.sh`.

## Method

Every `file:line` above was re-read at `672a500`. `jq` counts are from
`.cache/scan/trivy.json` and `docs/cve-baseline.json`, read only. The two reproductions
(`source_revision` without `.git`, the empty-build-arg probe image) ran in `mktemp -d` copies and the
probe image was removed. `just image`, `just scan` and `--write-baseline` were not run, and the rig
was not touched.
