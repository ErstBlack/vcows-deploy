# RX-E2 — verify

`scripts/lib.sh:16` sets `set -euo pipefail` and not `shopt -s inherit_errexit`, so any guard
one call-level inside a `$(helper)` is inert.

Verified at `672a500` in the detached worktree
(`scratchpad/rv3`, `git rev-parse HEAD` = `672a500a5f3db394e91a3b91fb383517e504246d`). Every
mutation was made in `cp -a` copies (`scratchpad/rxe2.SP0z`, `scratchpad/rxe2.b21`). No tracked
file in either checkout was touched. `--write-baseline` was not run; the rig was not touched.

---

## Lens 1 — Reproduce

Real scripts, real functions, no toy. Fakes are only `podman`/`trivy`/`syft` on PATH
(`$S/fakebin`), and `$S/.tools/bin` was replaced with an empty directory because in the worktree
it is a symlink into the live repo's `.tools/bin`, which `lib.sh:26` prepends to PATH.

### 1a. `image-build.sh:23` → `image_tag` → `containerfile_arg` (die at `lib.sh:81`)

```
$ sed -i 's/^ARG VCOWS_VERSION=0.1.0.0$/ARG VCOWS_VERSION=/' Containerfile   # line 89
$ env PATH="$S/fakebin:$PATH" bash scripts/image-build.sh; echo "SCRIPT EXIT=$?"
error: no 'ARG VCOWS_VERSION=' in Containerfile
warning: shipped paths are modified; recording 672a500a5f3d...-dirty
building localhost/vcows-deploy:
FAKE-PODMAN ARGS: build -t localhost/vcows-deploy: --build-arg GIT_SHA=672a500a5f3d...-dirty --build-arg BUILD_DATE=2026-08-31T04:35:08Z /…/rxe2.SP0z
built localhost/vcows-deploy:
run 'just test-image' to exercise the offline gate
SCRIPT EXIT=0
```

The `die` prints, and the script runs to completion at status 0 having invoked the builder with
an empty tag. `containerfile_arg`'s documented promise (`lib.sh:74-77`, "Fails loudly rather than
returning empty") does not hold at this call site.

The one-line fix, in the same copy:

```
$ sed -i "s/^set -euo pipefail$/set -euo pipefail\nshopt -s inherit_errexit/" scripts/lib.sh
$ env PATH="$S/fakebin:$PATH" bash scripts/image-build.sh; echo "SCRIPT EXIT=$?"
error: no 'ARG VCOWS_VERSION=' in Containerfile
SCRIPT EXIT=1
```

`lib.sh` was then restored from a pre-edit copy.

### 1b. `image-build.sh:38` → `source_revision` → `provider_version` (die at `lib.sh:110`)

Containerfile restored; `main.tf:11` `version = "= 0.9.8"` changed to `version = "0.9.8"`:

```
error: no pinned provider version in …/orchestrator/backends/libvirt/tofu/main.tf
warning: shipped paths are modified; recording 672a500a5f3d…-dirty
building localhost/vcows-deploy:0.1.0.0
built localhost/vcows-deploy:0.1.0.0
SCRIPT EXIT=0
```

`provider` is empty, so `ship` (`lib.sh:132-133`) carries `docs/provider-.lock.hcl` — a path that
cannot exist, so the lock file silently stops being watched by the `-dirty` check.

### 1c. `image-build.sh:38` → `source_revision` → `git rev-parse HEAD` (`lib.sh:134`)

`main.tf` restored, `.git` moved aside:

```
fatal: not a git repository (or any of the parent directories): .git
fatal: not a git repository (or any of the parent directories): .git
building localhost/vcows-deploy:0.1.0.0
FAKE-PODMAN ARGS: build -t localhost/vcows-deploy:0.1.0.0 --build-arg GIT_SHA= --build-arg BUILD_DATE=… /…/rxe2.SP0z
SCRIPT EXIT=0
```

`--build-arg GIT_SHA=` — empty, overriding `ARG GIT_SHA=unknown`. This is RX-E9's mechanism,
reproduced here as an E2 call site.

### 1d. `install-tools.sh:115` → `fetch` → `digest` (die at `:40`), `curl` (`:67`), `sha256sum` (`:68`)

`scripts/install-tools.sh` copied with only its last line (`main "$@"`) replaced by
`TMP="$(mktemp -d)"; mkdir -p "$TOOLS_BIN"; FORCE=1 install_one tofu 9.9.9` — every function body
verbatim:

```
  tofu 9.9.9: downloading
error: no pinned digest for tofu:9.9.9 -- add it from the project's published checksums file
curl: (22) The requested URL returned error: 404
sha256sum: 'standard input': no properly formatted checksum lines found
unzip:  cannot find or open /tmp/tmp.fW4TbluxWX/tofu/tofu_9.9.9_linux_amd64.zip, …
SCRIPT EXIT=9
```

Three guards fire and none stops anything: execution continues past the `die`, past the failed
download and past the failed checksum. The run only ends because `unzip` fails in `install_one`,
which runs in the main shell. This is RX-E1's finding; here it is E2's fourth call site.

### 1e. Boundary — single-level calls do fail correctly

`ARG TOFU_VERSION=` emptied, `install-tools.sh:157` (`tofu_version="$(containerfile_arg TOFU_VERSION)"`):

```
error: no 'ARG TOFU_VERSION=' in Containerfile
SCRIPT EXIT=1
```

A `die` executed *directly* inside a `$()`-invoked function exits that subshell non-zero, and the
caller's `set -e` sees it. Only a level deeper is swallowed. The finder's claim about the
single-level sites is correct.

## Lens 2 — Reachability

Every `$(...)` in `scripts/` that invokes a repo function was enumerated
(`grep -rn '\$(' scripts/*.sh`). Four helpers are invoked inside `$()` **and** contain a nested
substitution or an unchecked command: `image_tag`, `source_revision`, `fetch`, `archive_label`.
`archive_label` (`bundle.sh:32-39`) is safe — both its inner substitutions are `[ -n … ] || die`
tested, and that `die` is direct, so it propagates.

Six reachable outer call sites, five distinct swallowed guards:

| # | outer `$()` site | swallowed guard | what it produces |
|---|---|---|---|
| 1 | `scripts/image-build.sh:23` | `lib.sh:81` via `:100` | tag `localhost/vcows-deploy:` — wrong value, passed to the builder |
| 2 | `scripts/image-scan.sh:70` | `lib.sh:81` via `:100` | `saving localhost/vcows-deploy:`, then `podman save … -o .cache/scan/image.tar localhost/vcows-deploy:` |
| 3 | `scripts/test-image.sh:14` | `lib.sh:81` via `:100` | `exercising localhost/vcows-deploy:`; exported `VCOWS_IMAGE` reaches every `podman run` in `tests/test_image.py` |
| 4 | `scripts/image-build.sh:38` | `lib.sh:110` via `:131`; `git rev-parse` at `:134` | wrong `ship` array (lock file unwatched); `--build-arg GIT_SHA=` |
| 5 | `scripts/bundle.sh:61` | same two | `worktree=""`, so the "archive built at X but tree is at Y" warning misfires on every build |
| 6 | `scripts/install-tools.sh:115` | `install-tools.sh:40`, `:67`, `:68` | `fetch` returns a path at rc 0 with no digest check — RX-E1 |

Sites 2 and 3 were run and produce exactly the strings above; 1, 4, 5 and 6 are shown in Lens 1
(site 5 shares `source_revision` with site 4 verbatim).

Not sites, confirmed single-level and correct: `verify-provider.sh:43,47,48`, `mirror.sh:57`,
`bundle.sh:54,55`, `install-tools.sh:157`.

Each guard needs a second fault to matter — that is what a guard is for. The consequential one is
site 6: the sha256 check on every downloaded tool binary, including the `tofu` that builds the
provider mirror baked into the delivered image.

## Lens 3 — Already handled?

**`set -euo pipefail` (`lib.sh:16`)** — this is the defect, not the cure. `-u` and `-o pipefail`
are inherited by the subshell; `-e` is not.

**shellcheck — no, at any setting.** The `672a500` gate (`lint.sh:159`,
`shellcheck -x -s bash …`) exits 0. Nothing in shellcheck 0.10.0 flags it: the minimal
two-level pattern (`die` inside `inner`, `inner` inside `outer`, `outer` inside `$()`, with
`set -euo pipefail`) run under `-o all` — every optional check — emits only SC2250/SC2292 style
notes, no SC2311 and no SC2312. On the real scripts `-o check-extra-masked-returns` flags only
argument-position substitutions (`lib.sh:135`, `image-build.sh:43`), never the assignments that
carry this defect.

**The in-flight `#21` work does not close it, and states the opposite.** The working tree of
`/home/ssullivan/vcows-deploy` (branch `issue-21`, uncommitted) turns on four optional checks at
`scripts/lint.sh:183-188` and rewrites `source_revision` to assign `dirt="$(git … status …)"` on
its own line, commenting: "As a bare assignment the failure reaches the `set -e` in this file
instead. SC2312." Copying those `scripts/*.sh` over a `672a500` tree and re-running case 1c:

```
fatal: not a git repository (or any of the parent directories): .git
fatal: not a git repository (or any of the parent directories): .git
building localhost/vcows-deploy:0.1.0.0
FAKE-PODMAN ARGS: build -t localhost/vcows-deploy:0.1.0.0 --build-arg GIT_SHA= …
SCRIPT EXIT=0
```

Byte-identical to `672a500`. `set -e` "in this file" is off inside `$(source_revision)`, which is
how both of its call sites invoke it. Context, not a refutation — and it is a second comment
recording the wrong cause, alongside `lib.sh:88-93` (RX-E3).

**Tests — none.** No test in `tests/` executes `scripts/*.sh`; `grep -rn 'inherit_errexit\|errexit'
tests/ docs/*.md` returns nothing outside this review's own directory. `tests/test_version.py:51`
(`test_the_image_tag_agrees`) asserts the Containerfile declares `ARG VCOWS_VERSION` — a partial
compensating control for guard 1 only, in pytest, not in the build path. Observed failing during
case 3.

**Prior reviews — the symptom, once, with the wrong cause.** `docs/review-2026-08-30/REVIEW.md:299-306`
(RW-G5) found site 1's empty tag and attributed it to argument position; `finders/G-build-pipeline.md:316`
says the same. Its suggested remedy — "assign the substitution to a local **and test it**, or
`v="$(…)" || exit`" — would have worked; the remedy that landed kept the assignment and dropped
the test. `docs/tooling-2026-08-{29,30}.md` discuss shellcheck optional checks and never reach
this. Nothing in `docs/findings.md` covers it.

---

**Verdict: CONFIRMED, high.** Six reachable call sites, five inert guards, one line to fix.

Severity note, for the record: taken alone — with RX-E1's site 6 filed separately at high — the
remaining five sites are individually medium (a wrong tag, an empty revision label, an unwatched
lock file). `high` holds because this is the shared root cause of RX-E1, RX-E3 and RX-E9, and
because an inert guard is the defect class `tests/conftest.py:7` names as worse than no gate.
`shopt -s inherit_errexit` at `lib.sh:16` closes all four; it turns currently-silent paths into
stops, so `just check` has to be re-run after it.
