# RX-E1 — verification

Finding: `scripts/install-tools.sh:68` — "tool-binary sha256 check fails open: a mismatched
download installs anyway, exit 0." Claimed **high**.

Verified at `672a500` in the detached worktree
`…/scratchpad/rv3`; all mutation in a `cp -a` copy
(`…/scratchpad/rxe1.wEre`). No tracked file was touched in either checkout,
nothing was downloaded from an upstream host, `--write-baseline` was not run, the rig was not
contacted.

## Line re-verification

Every citation checked against `672a500`, `cat -n`:

| cite | line at `672a500` |
|---|---|
| `install-tools.sh:68` | `echo "${want}  ${file}" \| sha256sum -c - >/dev/null` |
| `install-tools.sh:115` | `file="$(fetch "$tool" "$version" "$tmp")"` |
| `install-tools.sh:40` | `*) die "no pinned digest for $1 -- add it from the project's published checksums file" ;;` |
| `install-tools.sh:140-151` | `expose_on_path()` … `$sudo ln -sf "$tool" "$dir/$(basename "$tool")"`, `dir=/usr/local/bin` |
| `install-tools.sh:19-20` | "Bumping the Containerfile without adding a digest below is a hard failure" |
| `install-tools.sh:58-60` | "The digest check is `sha256sum -c -` … so a truncated download fails here" |
| `lib.sh:16` | `set -euo pipefail` |

```
$ grep -rn 'inherit_errexit' scripts/ .claude/ ; echo rc=$?
rc=1
$ bash --version | head -1
GNU bash, version 5.2.26(1)-release (x86_64-redhat-linux-gnu)
```

## Lens 1 — Reproduce

Harness: `sed '$d' scripts/install-tools.sh > scripts/_it.sh` (drops only the trailing
`main "$@"` so the file can be sourced), then two `url()` arms repointed at local `file://`
payloads. That is the whole diff — every function body, including `fetch`, is verbatim:

```
$ diff scripts/install-tools.sh scripts/_it.sh
48c48
<         hadolint)   echo "https://github.com/hadolint/hadolint/releases/download/v${2}/hadolint-linux-x86_64" ;;
---
>         hadolint)   echo "file:///…/rxe1.wEre/serve/hadolint-linux-x86_64" ;;
54c54
<         tofu)       echo "https://github.com/opentofu/opentofu/releases/download/v${2}/tofu_${2}_linux_amd64.zip" ;;
---
>         tofu)       echo "file:///…/rxe1.wEre/serve/tofu_1.12.6_linux_amd64.zip" ;;
169d168
< main "$@"
```

Payloads, both deliberately mismatching the pinned digests at `:36` and `:39`:

```
$ sha256sum serve/hadolint-linux-x86_64
ca23ad4b14078ab2366def0e5e35bb9ef2c3ba78e7c9b1eab76779cf40b8e400   (pinned: c7187db94eee…)
zip sha256 7d55841170a94cb2f7375491dd763388df6acbd715267dcb08734bf2db10b342   (pinned: 5dc43da4f750…)
```

Driver replicating `main()` (`:153-167`) for two tools, `FORCE=1` to reach the download arm:

```
$ ./drive.sh; echo "SCRIPT EXIT=$?"
  hadolint 2.15.1: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
install_one hadolint rc=0
  tofu 1.12.6: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
install_one tofu rc=0
--- .tools/bin ---
-rwxr-xr-x. 1 ssullivan ssullivan 34 Aug 31 00:35 hadolint
-rwxr-xr-x. 1 ssullivan ssullivan 30 Jan  1  1980 tofu
SCRIPT EXIT=0

$ sha256sum .tools/bin/*
ca23ad4b1407…  .tools/bin/hadolint      # the mismatched bytes, byte-identical
4bfbbc32d222…  .tools/bin/tofu          # extracted from the mismatched zip
$ .tools/bin/hadolint ; .tools/bin/tofu
MALICIOUS-HADOLINT
MALICIOUS-TOFU
```

**Both mismatched binaries are installed. The script exits 0.** `expose_on_path` (`:140-151`)
would then `ln -sf` them into `/usr/local/bin`; not run here, since it writes outside the copy.

Mechanism, and the unpinned-version arm (`digest`'s `die` at `:40`):

```
$ ./drive2.sh; echo "EXIT=$?"
sha256sum: WARNING: 1 computed checksum did NOT match
fetch rc=0 file=[/tmp/tmp.jtaVBQbHu4/hadolint-linux-x86_64]
error: no pinned digest for tofu:9.9.9 -- add it from the project's published checksums file
sha256sum: 'standard input': no properly formatted checksum lines found
unpinned fetch rc=0 file=[/tmp/tmp.jtaVBQbHu4/tofu_1.12.6_linux_amd64.zip]
reached end of script
EXIT=0
```

Both guards inside `fetch` are inert and `fetch` returns the path at status 0 either way.
Counterfactual — same script, one line added:

```
$ ./drive3.sh; echo "EXIT=$?"      # identical, plus `shopt -s inherit_errexit`
sha256sum: WARNING: 1 computed checksum did NOT match
EXIT=1
```

Cause is exactly as the finding states: `file="$(fetch …)"` at `:115` runs `fetch` in a
command-substitution subshell, which without `inherit_errexit` does not inherit `set -e`
from `lib.sh:16`. The subshell's exit status is `printf`'s (`:69`), which is 0.

**Lens 1: reproduced.**

## Lens 2 — Reachability

Callers of `scripts/install-tools.sh` at `672a500`:

* `justfile:51` — `just tools`.
* `.github/workflows/ci.yml:50` and `:79` — both CI jobs, every PR and every push to master.
* `.github/workflows/image.yml:70` — the image build.
* `.github/workflows/scheduled.yml:62` — the monthly rebuild.
* `.gitlab-ci.yml:38` — the `&bootstrap` anchor, spliced into all four GitLab jobs.
* `README.md:306` — the documented fresh-clone bootstrap.

No upstream guard exists. Nothing re-verifies the binaries after install; `lib.sh:63` only
checks that a tool is *on PATH*. The GitHub cache (`ci.yml:43`, `:62`, keyed on
`hashFiles('scripts/install-tools.sh', 'Containerfile')`) makes the download run less often,
which lowers frequency and adds nothing to verification.

Two triggers, and the first needs no attacker at all:

1. **A `TOFU_VERSION` bump in the `Containerfile` with no digest arm added.** `main:157` reads
   the version out of the Containerfile (`containerfile_arg TOFU_VERSION`), `digest` has no
   arm, `die` fires into the void, `want` is empty, and the binary installs unverified —
   measured above, `unpinned fetch rc=0`, exit 0. `install-tools.sh:19-20` calls this exact
   case "a hard failure, which is the intended way to find out". Measured, it is a stderr line
   and exit 0.
2. A replaced release asset, a corrupted or truncated body that survives `curl -f --retry 3`,
   or a runner whose egress is not what it thinks. `:58-60` says the `sha256sum -c -` form was
   chosen "so a truncated download fails here instead of unpacking into something surprising".

The claim this defect falsifies is load-bearing for the project's own supply-chain argument:
`.github/workflows/ci.yml:7-9` — "No third-party actions: the pinned tool downloads in
`scripts/install-tools.sh` verify their own digests, which is the standard the Containerfile
already holds itself to." The Containerfile's checks are `RUN` lines whose failure stops the
build; these are not, so the two standards are not the same. The finder's reading of
`:6-20` is accurate.

**Lens 2: reachable on every CI run and every fresh clone, with a non-adversarial trigger.**

## Lens 3 — Already handled

Not covered anywhere.

```
$ grep -rn "install-tools\|install_tools" tests/
tests/conftest.py:84:    "run `just mirror` to build one, or `scripts/install-tools.sh` first "
```

No test exercises this script. `grep -rni "inherit_errexit|fails open|fail open|sha256sum -c|
digest check"` over `docs/findings.md`, `docs/tooling-*.md`, `docs/review-2026-08-29/` and
`docs/review-2026-08-30/` returns nothing about this call site — only the *Containerfile's*
checks (`docs/review-2026-08-29/06-container-supplychain.md:114,149`;
`docs/review-2026-08-30/evidence/03-image-build.txt:233,241`), which do work.

The two adjacent prior findings are distinct:

* **RW-G5** (`docs/review-2026-08-30/REVIEW.md:297-306`) — the same swallowed-`die` class, but
  at `lib.sh`'s `image_tag`, with a reporting consequence. Its remedy ("assign the
  substitution to a local") was applied to `image_tag` only and does not reach `fetch`. Its
  diagnosis, now written into `lib.sh:88-93`, blames argument position rather than
  `inherit_errexit`; that is RX-E3's business, not this one's.
* **RW-G6** (`REVIEW.md:308-317`) — the `have "$tool"` early return. Fixed at
  `install-tools.sh:104-113`, and about a *different* way the pins go unused.

No recorded decision accepts an unverified tool install. `docs/findings.md` has no entry.

**Lens 3: new.**

## Verdict

**CONFIRMED — high.**

Reproduced on all three lenses. Mismatched bytes install and the script exits 0; the path is
reached by `just tools`, three GitHub workflows, all four GitLab jobs and the README's
fresh-clone procedure; and no test, gate or recorded decision covers it. Severity stays at
**high** rather than critical: the failure needs a bad artifact to arrive, but the
unpinned-`TOFU_VERSION` trigger is a maintainer error rather than an attacker, and the file's
own comment promises that case is a hard failure. Not critical, because nothing here is
silently wrong *against a target* — the blast radius is the build host and the runner.

The finder's proposed fix (RX-E2's `shopt -s inherit_errexit` in `lib.sh`, or `|| die` on
`:68`) is consistent with what the counterfactual measured. Adopting the one-line `lib.sh`
change turns several currently-silent paths into stops, so it needs a `just check` after it —
which the finder also says.
