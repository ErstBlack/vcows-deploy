# Issue #76 — `install-tools.sh` installs a binary whose sha256 did not match, and exits 0

Reverified at `aed962d` on `lane/shell-errexit`. Raw transcript:
`docs/review/shell-errexit/reverify/RX-E1.txt`.

**Headline: reproduced, and subsumed. #76 needs no fix of its own — the one-line
change in `docs/archive/plans/issue-77.md` closes it, measured on the identical harness.**

## 1. Reverification verdict

**Reproduced at `aed962d`.** `install-tools.sh` has not changed since the review's
pin — `git diff 672a500..aed962d -- scripts/install-tools.sh` is empty — so every
line the issue cites is where it says.

Harness: a fresh clone of the worktree at `aed962d`; `scripts/_it.sh` is
`install-tools.sh` with its trailing `main "$@"` dropped so it can be sourced, and
two `url()` arms repointed at local `file://` payloads. That is the entire diff:

```
$ diff scripts/install-tools.sh scripts/_it.sh
48c48
<         hadolint)   echo "https://github.com/hadolint/hadolint/releases/download/v${2}/hadolint-linux-x86_64" ;;
---
>         hadolint)   echo "file:///…/serve/hadolint-linux-x86_64" ;;
54c54
<         tofu)       echo "https://github.com/opentofu/opentofu/releases/download/v${2}/tofu_${2}_linux_amd64.zip" ;;
---
>         tofu)       echo "file:///…/serve/tofu_1.12.6_linux_amd64.zip" ;;
169d168
< main "$@"
```

Payloads deliberately mismatch the pins at `:36` (`c7187db94eee…`) and `:39`
(`5dc43da4f750…`): `f26d56084497…` and `d1aed8a6775d…`.

```
$ bash drive.sh                      # replicates main() for two tools, FORCE=1
  hadolint 2.15.1: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
install_one hadolint rc=0
  tofu 1.12.6: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
install_one tofu rc=0
--- .tools/bin ---
-rwxr-xr-x. 1 ssullivan ssullivan 44 Aug 31 01:59 hadolint
-rwxr-xr-x. 1 ssullivan ssullivan 40 Jan  1  1980 tofu
EXIT=0

$ sha256sum .tools/bin/hadolint .tools/bin/tofu
f26d56084497e876c92dc0e1ca20aaa48ca5e8390d87be521cbc9bb2776c0858  .tools/bin/hadolint
2fac844f17d8e5bab9b61cfbb56133796075fd9ceef480b0aba47cdde1e117bc  .tools/bin/tofu
$ ./.tools/bin/hadolint; ./.tools/bin/tofu
MALICIOUS-HADOLINT
MALICIOUS-TOFU
```

The mismatched bytes are installed, they execute, and the script exits 0. The
unpinned arm behaves the same way:

```
$ bash drive2.sh
sha256sum: WARNING: 1 computed checksum did NOT match
fetch rc=0 file=[/tmp/tmp.wYAjlUQOsa/hadolint-linux-x86_64]
error: no pinned digest for tofu:9.9.9 -- add it from the project's published checksums file
sha256sum: 'standard input': no properly formatted checksum lines found
unpinned fetch rc=0 file=[/tmp/tmp.wYAjlUQOsa/tofu_1.12.6_linux_amd64.zip]
reached end of script
EXIT=0
```

## 2. Anchor table

| anchor | state at `aed962d` |
|---|---|
| `scripts/install-tools.sh:68` `echo "${want}  ${file}" \| sha256sum -c - >/dev/null` | ok |
| `scripts/install-tools.sh:115` `file="$(fetch "$tool" "$version" "$tmp")"` | ok |
| `scripts/install-tools.sh:40` `die "no pinned digest for $1 …"` | ok |
| `scripts/install-tools.sh:19-20` "…is a hard failure, which is the intended way to find out" | ok |
| `scripts/install-tools.sh:58-60` "so a truncated download fails here" | ok |
| `scripts/install-tools.sh:140-151` `expose_on_path()`, `dir=/usr/local/bin` | ok |
| `justfile:51` `./scripts/install-tools.sh` | ok |
| `.github/workflows/image.yml:70` | ok |
| `.github/workflows/scheduled.yml:62` | ok |
| `README.md:306` | ok |
| `.github/workflows/ci.yml:7-9` "the pinned tool downloads … verify their own digests" | ok |
| **`.github/workflows/ci.yml:50,79`** | **incomplete** — a third at `:114` |
| **`.gitlab-ci.yml:38` "(all four jobs)"** | **stale twice** — see C2 |
| **`scripts/lib.sh:63`** "checks PATH presence only" | true in substance, wrong line — C3 |

`scripts/install-tools.sh` is byte-identical to `672a500`. The drift is entirely
in the callers: `a3068e3` added a `smoke` job to both pipelines.

## 3. Corrections to the issue body

**C1 — GitHub: three jobs, not two.** `ci.yml` at `aed962d` defines `check` (`:30`),
`tofu` (`:54`) and `smoke` (`:93`), and each runs the script — `:50`, `:79`, `:114`.

**C2 — GitLab: five jobs, and two routes, not one.** `.gitlab-ci.yml` defines
`check` (`:41`), `tofu` (`:52`), `smoke` (`:87`), `image` (`:116`) and
`rebuild-scan` (`:175`). The `&bootstrap` anchor at `:36` is spliced into four of
them (`:49`, `:74`, `:156`, `:187`). `smoke` deliberately does not use it —
`:106-109` explains why — and calls the script directly at `:112`. So the reach is
wider than the issue states, by both counts.

**C3 — `lib.sh:63` is the `die`, not the check.** The PATH check is `have()`
(`lib.sh:43`), reached from `need()` at `:58`; `:63` is the message arm for
`uv|just|tofu|hadolint|trivy|syft`. The claim it supports — that nothing upstream
verifies the bytes, only presence — holds exactly as written.

**C4 — "Fixing #77 may close this one" is now settled: it does.** §4.

**C5 — one thing the issue does not say, found while measuring.** `install_one`'s
early return at `:104-113` means that on any box already carrying the tool, `fetch`
never runs, so the digest path is never reached at all. That lowers the frequency
on a developer box and not at all on a fresh runner, which is where the callers in
C1 and C2 live. Adjacent and out of scope: `found="$(version_of "$path")"` at
`:107` makes `install_one` abort at status 1 with no message whenever the PATH
copy's `--version` carries no dotted version, because `pipefail` is already
inherited and `:84`'s `grep -oE` returns 1. Measured identical before and after
the #77 fix, so neither issue causes it.

## 4. The defect, and why it is #77's

`fetch` is invoked as `file="$(fetch "$tool" "$version" "$tmp")"` at `:115`. That
command substitution runs `fetch` in a subshell which, without
`shopt -s inherit_errexit`, does not inherit `set -e` from `lib.sh:16`. Inside it,
`digest`'s `die` at `:40` exits only its own inner subshell, `curl`'s failure at
`:67` is ignored, and `sha256sum -c -`'s non-zero at `:68` is ignored; execution
reaches `printf '%s\n' "$file"` at `:69`, whose status is 0, which becomes the
substitution's status. `install_one` then unpacks and installs whatever arrived.
Nothing about this is specific to `install-tools.sh`: it is the same shape as
`image_tag` and `source_revision`, and it is site 6 of the six in #77.

## 5. The fix

**None of its own. Close it against #77's `shopt -s inherit_errexit` in
`scripts/lib.sh`.**

The measurement that settles it — the identical harness, identical payloads,
identical driver, one line added to `lib.sh` and nothing else:

```
$ sed -n '16,18p' scripts/lib.sh
set -euo pipefail
shopt -s inherit_errexit
IFS=$'\n\t'

$ rm -rf .tools/bin && mkdir -p .tools/bin && bash drive.sh
  hadolint 2.15.1: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
EXIT=1

$ ls -la .tools/bin
total 0
drwxr-xr-x. 2 ssullivan ssullivan  6 Aug 31 01:59 .
drwxr-xr-x. 4 ssullivan ssullivan 36 Aug 31 01:59 ..

$ bash drive2.sh                      # the unpinned arm
sha256sum: WARNING: 1 computed checksum did NOT match
EXIT=1
```

Nothing is installed, the second tool is never reached, and the exit is 1. Run
once more against the **unmodified** `install-tools.sh` — only `url()`'s hadolint
arm repointed, no `_it.sh`, functions sourced out of the real file:

```
with    shopt -s inherit_errexit:   sha256sum: WARNING: … did NOT match      EXIT=1   .tools/bin empty
without shopt -s inherit_errexit:   sha256sum: WARNING: … did NOT match
                                    REACHED THE LINE AFTER install_one       EXIT=0   MALICIOUS-HADOLINT
```

Both triggers the issue names are covered. The non-adversarial one — a
`TOFU_VERSION` bump with no digest arm, which `:19-20` promises is "a hard
failure" — becomes one:

```
$ bash drive-it.sh                    # FORCE=1 install_one tofu 9.9.9
  tofu 9.9.9: downloading
error: no pinned digest for tofu:9.9.9 -- add it from the project's published checksums file
EXIT=1
```

Against `aed962d` the same command prints that `die`, then a `curl` 404, then a
`sha256sum` parse error, and only stops because `unzip` fails in the parent shell
at exit 9.

### Rejected

* **`|| die "$tool: sha256 mismatch"` on `:68`.** Measured redundant once #77
  lands: the exit is 1 at `:68` either way, and `sha256sum`'s own
  `WARNING: 1 computed checksum did NOT match` already names the file and the
  mechanism. A line whose only effect is a different wording is surface this
  repo treats as a defect.
* **A post-install re-verification pass over `.tools/bin`.** New function, new
  loop, and it recomputes a digest the download path just computed. It would only
  add information if the install path could be reached without `fetch`, which
  `:104-113`'s early return does — and that arm installs nothing, so there is
  nothing to verify.
* **Fixing #76 first, on its own, so it can close independently.** Two fixes for
  one mechanism, and the second would be dead code the moment #77 merges. The two
  issues are one branch and one commit; #76 closes from the body.
* **Signing or attestation on the downloads.** `docs/ci.md` records why signing
  was removed; re-deriving it is settled work.

## 6. Surface cost

**Zero lines.** The fix is `docs/archive/plans/issue-77.md` §6: `scripts/lib.sh` `+5` and a
comment replacement, plus `tests/test_shell_errexit.py`. Nothing in
`scripts/install-tools.sh` changes.

This is the minimum by construction — it is less than the issue itself proposes,
and it is only available because the two issues were reverified together on one
harness rather than fixed in sequence.

## 7. The failing test

Nothing in the suite executes `scripts/*.sh` — measured, `grep -rn
'install-tools\|install_tools' tests/` returns one string inside a skip reason at
`conftest.py:84`. For this issue specifically that costs the strongest claim the
project makes about itself: `ci.yml:7-9` justifies using no marketplace actions on
the grounds that "the pinned tool downloads in `scripts/install-tools.sh` verify
their own digests, which is the standard the Containerfile already holds itself
to". The Containerfile's checks are `RUN` lines whose failure stops the build.
These were not, and no gate could tell.

**Proposed: the same single test in `tests/test_shell_errexit.py`
(`docs/archive/plans/issue-77.md` §7), and no second one here.** Its assertion —
`x="$(helper)"` must stop the caller when a guard two levels down fires — is the
exact property `:115` depends on, and it fails before the fix and passes after,
proved on this branch.

Considered and rejected, with the cost stated rather than hidden: a test driving
`fetch` directly against a `file://` payload. It would need the same
`sed '$d' install-tools.sh > _it.sh` harness the reproduction uses, in the test
file, because `install-tools.sh:169` runs `main "$@"` at source time. That is
harness surface inside `tests/` for a mechanism the one-line test already covers,
and it turns on the same edit. **What it costs to leave out:** if someone later
adds `|| true` to `:68` specifically, the generic test still passes. That is a
real gap and it is accepted knowingly — the alternative buys one line of coverage
for roughly forty lines of scaffolding.

## 8. Verification

The issue closes on #77's verification (`docs/archive/plans/issue-77.md` §8) plus the
reproduction above re-run against the merged tree:

```
# harness: fresh clone, url() hadolint arm -> file:// with mismatched bytes
$ bash one.sh ; echo $?
  hadolint 2.15.1: downloading
sha256sum: WARNING: 1 computed checksum did NOT match
1
$ ls .tools/bin
(empty)
```

and the unpinned arm:

```
$ FORCE=1 install_one tofu 9.9.9 ; echo $?
  tofu 9.9.9: downloading
error: no pinned digest for tofu:9.9.9 -- add it from the project's published checksums file
1
```

`just tools` on a real box must still install all six and exit 0. Not run here:
it writes into `.tools/bin`, which in this worktree is a symlink into the live
repository.

## 9. Non-goals

* **`install-tools.sh:107`'s silent exit 1** on a tool whose `--version` carries
  no dotted version. Real, pre-existing, unchanged by this fix, and not what #76
  reports.
* **`expose_on_path`'s `sudo ln -sf` into `/usr/local/bin` (`:140-151`).** Named
  by the issue as the blast radius, not as a defect. Unchanged.
* **Re-opening the marketplace-actions decision at `ci.yml:7-9`.** The claim in
  that comment becomes true with this fix; it needs no edit and no revisiting.
* **The GitHub tool cache (`ci.yml:43,62,106`).** It lowers how often the download
  runs and verifies nothing. Not a control, and not being made into one here.
