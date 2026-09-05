# Issue #77 — `lib.sh` sets no `inherit_errexit`, so a `die` two levels inside `$()` is swallowed

Reverified at `aed962d` on `lane/shell-errexit`. Raw transcripts:
`docs/review/shell-errexit/reverify/RX-E2.txt` (this issue),
`RX-E3.txt` (the `lib.sh:88-93` comment, #90's item for this lane),
`RX-E9.txt` (`source_revision`, and what `454ee7c` did and did not do).

## 1. Reverification verdict

**Reproduced in full at `aed962d`.** All six call sites still fire; nothing the
issue claims about behaviour has gone stale.

The headline case, in a fresh clone of the worktree at `aed962d` with
`Containerfile:89` emptied and a fake `podman` on PATH:

```
$ sed -i 's/^ARG VCOWS_VERSION=0.1.0.0$/ARG VCOWS_VERSION=/' Containerfile
$ env PATH=$FAKE:$PATH bash scripts/image-build.sh
error: no 'ARG VCOWS_VERSION=' in Containerfile
warning: shipped paths are modified; recording aed962d48ee85641ef9580515b698c50881a271d-dirty
building localhost/vcows-deploy:
FAKE-PODMAN ARGS: build -t localhost/vcows-deploy: --build-arg GIT_SHA=aed962d…-dirty --build-arg BUILD_DATE=2026-08-31T05:57:18Z …
built localhost/vcows-deploy:
run 'just test-image' to exercise the offline gate
EXIT=0
```

Same tree, one line added beside `lib.sh:16`:

```
$ sed -n '16,18p' scripts/lib.sh
set -euo pipefail
shopt -s inherit_errexit
IFS=$'\n\t'
$ env PATH=$FAKE:$PATH bash scripts/image-build.sh
error: no 'ARG VCOWS_VERSION=' in Containerfile
EXIT=1
```

`grep -rn 'inherit_errexit' scripts/ .claude/ tests/ justfile Containerfile` at
`aed962d` → exit 1, no matches. `bash 5.2.26`, `shellcheck 0.10.0`.

## 2. Anchor table

`lib.sh` did change between the review's pin `672a500` and `aed962d`, but only
inside `source_revision`: `454ee7c` added `dirt` to the `local` list and five
comment lines, `+6` net, all at or after line 135. Every anchor at or below
`:134` is therefore unmoved, and `:139` is one of the new lines. Measured:
`git diff 672a500..aed962d -- scripts/lib.sh` is that one hunk and nothing else.

| anchor | state at `aed962d` |
|---|---|
| `scripts/lib.sh:16` `set -euo pipefail` | ok |
| `scripts/lib.sh:81` `[ -n "$value" ] \|\| die "no 'ARG ${name}=' …"` | ok |
| `scripts/lib.sh:88-93` the argument-position comment | ok, and wrong as written — §3, §5 |
| `scripts/lib.sh:100` `version="$(containerfile_arg VCOWS_VERSION)"` | ok |
| `scripts/lib.sh:110` `[ -n "$version" ] \|\| die "no pinned provider version …"` | ok |
| `scripts/lib.sh:131` `provider="$(provider_version)"` | ok |
| `scripts/lib.sh:134` `sha="$(git -C "$REPO" rev-parse HEAD)"` | ok |
| `scripts/lib.sh:139` "…reaches the `set -e` in this file instead. SC2312." | ok as a citation, **false as a claim** — §3 |
| `scripts/image-build.sh:23` `tag="$(image_tag)"` | ok |
| `scripts/image-build.sh:38` `sha="$(source_revision)"` | ok |
| `scripts/image-scan.sh:70` `tag="$(image_tag)"` | ok |
| `scripts/test-image.sh:14` `VCOWS_IMAGE="$(image_tag)"` | ok |
| `scripts/bundle.sh:32-39` `archive_label()` | ok, still safe (its `die`s are direct) |
| `scripts/bundle.sh:61` `worktree="$(source_revision)"` | ok |
| `scripts/install-tools.sh:40` `die "no pinned digest for $1 …"` | ok |
| `scripts/install-tools.sh:63` `want="$(digest "${tool}:${version}")"` | ok |
| `scripts/install-tools.sh:67` `curl -fsSL --retry 3 -o "$file" "$src"` | ok |
| `scripts/install-tools.sh:68` `… \| sha256sum -c - >/dev/null` | ok |
| `scripts/install-tools.sh:115` `file="$(fetch …)"` | ok |
| `scripts/install-tools.sh:157` `tofu_version="$(containerfile_arg TOFU_VERSION)"` | ok |
| `Containerfile:89` `ARG VCOWS_VERSION=0.1.0.0` | ok |
| **`scripts/lint.sh:159`** "shellcheck exits 0" | **wrong as written** — moved to `:183-188` |
| `454ee7c` | ok — `git merge-base --is-ancestor 454ee7c HEAD` succeeds |

`scripts/lint.sh` grew 31 lines between `672a500` and `aed962d`. `:159` is now a
bare `#`; the gate the issue means is

```
183  gate "shellcheck"        shellcheck -x -s bash \
184                               -o check-extra-masked-returns \
185                               -o check-unassigned-uppercase \
186                               -o quote-safe-variables \
187                               -o avoid-nullary-conditions \
188                               "$REPO"/scripts/*.sh "$REPO"/.claude/hooks/*.sh
```

The substance survives the move: run at `aed962d` over an unmodified tree, that
gate prints `ok shellcheck`.

## 3. Corrections to the issue body

**C1 — `lint.sh:159` is stale.** See the table. The claim it carries is still
true, at `lint.sh:183-188`.

**C2 — "six outer call sites and five distinct inert guards" miscounts its own
table.** Re-enumerated at `aed962d`, the six sites are right and the swallowed
set is **six lines, not five**, of two kinds:

*Three `die` guards:* `lib.sh:81` (via `image_tag:100`), `lib.sh:110` (via
`source_revision:131`), `install-tools.sh:40` (via `fetch:63`).
*Three unchecked commands:* `lib.sh:134` (`git rev-parse`), `install-tools.sh:67`
(`curl`), `install-tools.sh:68` (`sha256sum -c`).

Calling all six "guards" is what produced the "five": `curl` and `sha256sum` were
collapsed into one. The distinction matters for §5, because only the three `die`s
are guards the file promises will fire.

**C3 — the `454ee7c` claim is right, and the consequence is worse than the issue
says.** The issue's evidence is the `.git`-absent case, which gives
`--build-arg GIT_SHA=` at exit 0. Re-measured at `aed962d` with `454ee7c` in
history: byte-identical to the issue's account (`RX-E9.txt`). But `454ee7c`'s
subject was the `dirt=` line, not the `sha=` line, and isolating it with a `git`
shim that succeeds for `rev-parse` and fails for `status` shows the real cost:

```
$ env PATH=$GITSHIM:$PATH bash -c 'source scripts/lib.sh; r="$(source_revision)"; echo "rev=[$r] CONTINUED"'
fake-git: status refused
rev=[1111111111111111111111111111111111111111] CONTINUED
EXIT=0

$ env PATH=$GITSHIM:$FAKE:$PATH bash scripts/image-build.sh
fake-git: status refused
building localhost/vcows-deploy:0.1.0.0
FAKE-PODMAN ARGS: build -t … --build-arg GIT_SHA=1111111111111111111111111111111111111111 …
EXIT=0
```

A clean 40-hex SHA is recorded for a tree whose dirty state was never
determined — precisely the failure `lib.sh:135-139` says it prevents. With the
one-line fix, both commands exit 42. So `lib.sh:139` and `lint.sh:174-177` are
not merely "recording the wrong cause": they assert something measurably false
today, and the fix in §5 is what makes them true. Neither needs an edit
afterwards.

**C4 — the shellcheck claim needs one qualification, and it changes the fix.**
The issue says shellcheck emits no SC2311/SC2312 for this pattern. True for the
*nested* shape and confirmed at `aed962d` under `-o all`. It is not true of the
argument-position shape, and `-o check-extra-masked-returns` is already on:

| shape | `shellcheck` (the four flags at `lint.sh:184-187`) | `inherit_errexit` |
|---|---|---|
| B — `t="$(outer)"`, guard two levels down | silent (exit 0) | **fixes it** (exit 1) |
| C — `printf … "$(inner)"`, guard one level down, argument position | **SC2312** (exit 1) | does not fix it (still exit 0) |
| D — `v="$(inner)"`, guard one level down, assignment | silent | already correct |

Measured, both directions. The two mechanisms are complementary and neither
subsumes the other. §5 must not claim `inherit_errexit` retires SC2312, and the
assignment form installed by `950ca7e` must stay.

**C5 — everything else re-read and correct.** All other cited lines read exactly
as the issue quotes them, including `install-tools.sh:157` as the single-level
boundary (measured `exit 1`) and `archive_label` as the safe helper.

## 4. The defect

`lib.sh:16` sets `set -e`, and bash does not propagate `-e` into the subshell a
command substitution creates unless `inherit_errexit` is on. Every helper in
`lib.sh` is invoked as `x="$(helper)"`, so the moment a helper's own guard lives
one call level further down — `image_tag` → `containerfile_arg` → `die`,
`source_revision` → `provider_version` → `die`, `fetch` → `digest` → `die` — the
`die` exits only the innermost subshell, the enclosing helper carries on and
returns whatever `printf` last wrote at status 0, and the caller's `set -e` sees
success. The result is not an empty string a later step catches: it is
`localhost/vcows-deploy:` handed to the builder, `--build-arg GIT_SHA=` overriding
`ARG GIT_SHA=unknown`, a `ship` array watching `docs/provider-.lock.hcl`, and a
tool binary installed without its digest ever being compared.

## 5. The fix

**The edit.** One option and its reason, at `scripts/lib.sh:17`:

```sh
set -euo pipefail
# A `die` inside `$(helper)` has to stop the caller. Command substitutions do not
# inherit errexit, so without this every guard one call level down is inert:
# containerfile_arg's die exited only its own subshell and `tag="$(image_tag)"`
# carried on with `localhost/vcows-deploy:` at status 0.
shopt -s inherit_errexit
IFS=$'\n\t'
```

**And the #90 item, which lands with it, not on its own.** `#90` says
`lib.sh:88-93` diagnoses the empty tag as an argument-position problem and that
this is wrong. Verified, with the qualification C4 forces. All four shapes were
run against the same emptied `Containerfile` (`RX-E3.txt`):

```
A  image_tag as a plain command                        EXIT=1
B  tag="$(image_tag)"            (the real shape)      tag=[localhost/vcows-deploy:] CONTINUED   EXIT=0
C  "…:$(containerfile_arg VCOWS_VERSION)"  (argument)  tag=[localhost/vcows-deploy:] CONTINUED   EXIT=0
D  v="$(containerfile_arg VCOWS_VERSION)"  (assignment) EXIT=1
```

C and D differ, so argument position is a real mechanism — the comment is not
inventing one. It is simply not the mechanism at `image_tag`'s call sites, which
are shape B: two levels, where assignment buys nothing. `git log -L 88,93` shows
`950ca7e` installed the comment in the same commit that rewrote `image_tag` from
the inline form to the assignment form, so the comment is that remedy's own
record of what it thought it had done. Replacement, same length:

```sh
# The version is assigned before it is used rather than interpolated inline.
# Argument position is a real mechanism -- measured, a substitution in an
# argument fails open where the same call in an assignment does not -- but it is
# not the one that produced `localhost/vcows-deploy:` here. containerfile_arg's
# die is two levels down, and at two levels both forms fail open; `shopt -s
# inherit_errexit` (:17) is what closes it. The assignment stays because
# check-extra-masked-returns still flags the argument form, which that option
# does not cover.
```

**What else the option changes, measured rather than reasoned.** It alters
behaviour only inside command substitutions, and only when a *non-final* command
fails — a substitution whose last command fails already propagates. Enumerated at
`aed962d`: 60 assignment-form substitutions across the ten scripts that source
`lib.sh`, of which 35 invoke a repo function. `smoke-libvirt.sh` holds 15 of
those 35, and every one either terminates in `|| true` — so its substitution
status is 0 either way — or wraps a single `vsh` call, `vsh` being the one-line
`virsh` wrapper at `:98`. `lib.sh:19` `REPO="$(cd … && pwd)"` short-circuits on
`&&`, so its status was already `cd`'s.

Runs, same tree, option off then on:

| | off | on |
|---|---|---|
| `just check` | six gates ok, `ty` clean, **411 passed, 25 skipped** | identical |
| `just image`, `VCOWS_IMAGE_TAG=localhost/vcows-deploy-l1:0.1.0.0` | — | **exit 0 in 43.3s**, `org.opencontainers.image.revision` a clean 40-hex `aed962d…` |
| `just verify-provider` | 6 ok, exit 0 | byte-identical |
| `just test-image` against that image | 421 passed, 15 skipped | 421 passed, 15 skipped |
| `bash scripts/bundle.sh` with no scan cache | `error: no image.tar…` exit 1 | identical |
| `install_one just` where the PATH copy prints no dotted version | **exit 1, silent** | **exit 1, silent** |

The last row is a pre-existing defect this fix neither causes nor cures:
`found="$(version_of "$path")"` (`install-tools.sh:107`) fails whenever the
grep at `:84` matches nothing, because `pipefail` is already inherited, so
`install_one` aborts at status 1 with no message — contradicting `:102`'s "A
warning, not a failure". Identical on both sides. Not this issue's; worth its own.

Two paths were **not** exercised and this is the limit of the measurement:
`scripts/os-deps.sh` (sudo-installs packages) and `scripts/smoke-libvirt.sh`
(sudo, starts a system daemon). Both were read instead. `os-deps.sh` has one
`|| true` and no function-call substitution; `smoke-libvirt.sh` is covered by the
`|| true` enumeration above. CI's `smoke` job is the only thing that can settle
the second, and it must be green before this merges.

**The one-line spelling of the tag in the assignment for this lane,
`localhost/vcows-deploy-L1:0.1.0.0`, is not a valid image reference** — podman:
`Error: tag localhost/vcows-deploy-L1:0.1.0.0: invalid reference format:
repository name must be lowercase`, exit 125. Measured with the lowercase
spelling instead.

### Rejected

* **`|| exit` or `|| die` at each of the six sites.** Four files, six edits, and
  every helper written afterwards has to remember. The defect is one shell option
  that is off, not six missing tests. It is also what RW-G5 proposed and half-did.
* **A per-script `shopt`.** Ten files instead of one, and a new script forgets.
* **Restructuring the helpers to return through a nameref instead of `$()`.** A
  rewrite of every caller for a defect a one-line option closes.
* **Leaning harder on shellcheck.** Measured: the four flags at `lint.sh:184-187`
  are silent on the nested shape, and so is `-o all`. `require-variable-braces`
  is already recorded as rejected at `lint.sh:180-182` with a count.
* **Dropping the assignment form now that `inherit_errexit` is on.** C4: SC2312
  still flags the argument form, which the option does not fix.

## 6. Surface cost

Two files.

* `scripts/lib.sh` — `+5` (one option, four comment lines) and a `6 → 8` line
  comment replacement at `:88-93`. No new function, no new file, no gate change.
* `tests/test_shell_errexit.py` — new, 64 lines. §7.

That is the minimum for the behaviour: `inherit_errexit` is per-shell state, and
`lib.sh` is the one file all ten scripts source, so anywhere else multiplies the
edit by ten. The comment replacement is not optional surface — it is the file
arguing, in the file, against the fix, and #90 assigns it to this change
specifically so the correction is measured rather than asserted.

The test file is the part that has to earn itself, and this repo's rule is that
unjustified surface is a defect. It earns itself on the record: this defect
survived the 2026-08-29 review, the 2026-08-30 review (which found one symptom
and mis-diagnosed it), a fix aimed at that symptom, and `454ee7c`, which enabled
four optional shellcheck checks specifically to catch this class. Every one of
those looked at the scripts; none ran them. A one-line option with nothing
executing it is a change the next `|| true` silently undoes.

## 7. The failing test

**Nothing in the suite executes `scripts/*.sh`.** Measured at `aed962d`:
`grep -rn 'install-tools\|install_tools' tests/` returns one hit, a string inside
a skip reason at `conftest.py:84`. The six gates in `lint.sh` read the scripts;
`tests/test_version.py:41` asserts the *Containerfile* still declares
`ARG VCOWS_VERSION`, which is a partial compensating control for one of six
sites and lives in pytest rather than the build path. That gap is the reason
this issue exists twice over.

Proposed: **`tests/test_shell_errexit.py`**, one file, two tests, no gate.

```python
def test_a_guard_two_levels_inside_a_substitution_stops_the_caller(tmp_path):
    tree = _tree(tmp_path, "ARG VCOWS_VERSION=")
    done = _run(tree, 'tag="$(image_tag)"\necho "REACHED tag=[$tag]"')
    assert "no 'ARG VCOWS_VERSION=' in Containerfile" in done.stderr
    assert done.returncode != 0, f"the die did not stop the caller: {done.stdout!r}"
    assert "REACHED" not in done.stdout
```

plus a vacuity guard asserting the same call *succeeds* and returns
`localhost/vcows-deploy:9.9.9.9` when the ARG is present.

`_tree` copies `scripts/lib.sh` into `tmp_path` and writes a two-line
Containerfile there. The copy is not decoration: `REPO` (`lib.sh:19-20`) is
derived from `BASH_SOURCE` and `readonly`, so relocating `lib.sh` is the only way
to point `containerfile_arg` at a Containerfile the test controls.

No `conftest.gate()`. `bash` is not an optional dependency — `test_image.py` and
`test_seed_iso.py` already shell out — and `test_gates.py:27`'s `KNOWN` is a
closed set of five names, so a gate here would be a skip nothing could ever
demand. Unconditional is what gives it teeth.

Proved, in the scratch tree, all three directions:

```
BEFORE (no inherit_errexit):
E       AssertionError: the die did not stop the caller: 'REACHED tag=[localhost/vcows-deploy:]\n'
1 failed, 1 passed in 1.01s

AFTER (the one-line fix):
2 passed in 0.37s

VACUITY MUTATION (harness broken: ARG renamed to NOT_THE_ARG):
E       AssertionError: error: no 'ARG VCOWS_VERSION=' in Containerfile
1 failed, 1 passed in 0.75s

WHOLE SUITE, fix reverted, test file present:
1 failed, 412 passed, 25 skipped in 33.44s
```

## 8. Verification

In the branch worktree, after the edit:

```
$ just check
  ok    ruff check / ruff format / hadolint / tofu fmt / shellcheck
  ok    workflows carry no logic
all gates pass
All checks passed!                       # ty
413 passed, 25 skipped
```

`413` is the current `411` plus the two new tests. Measured in the scratch tree
carrying exactly this change and exactly this test file.

```
$ VCOWS_IMAGE_TAG=localhost/vcows-deploy-l1:0.1.0.0 just image
building localhost/vcows-deploy-l1:0.1.0.0
…
Successfully tagged localhost/vcows-deploy-l1:0.1.0.0
built localhost/vcows-deploy-l1:0.1.0.0
EXIT=0
$ podman inspect … --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
aed962d48ee85641ef9580515b698c50881a271d
```

```
$ just verify-provider     # 6 ok, exit 0, byte-identical to before
$ just test-image          # 421 passed, 15 skipped
```

And the counterfactual, which is the assertion the issue turns on:

```
$ sed -i 's/^ARG VCOWS_VERSION=0.1.0.0$/ARG VCOWS_VERSION=/' Containerfile
$ bash scripts/image-build.sh ; echo $?
error: no 'ARG VCOWS_VERSION=' in Containerfile
1
```

CI's `smoke` job must be green on the PR. It is the only thing that exercises
`scripts/smoke-libvirt.sh`, which this change reaches and which cannot be run on
a developer box.

## 9. Non-goals

* **`install-tools.sh:107`'s silent exit 1** when a PATH tool prints no dotted
  version. Measured identical on both sides of this change; a separate defect,
  and folding it in would make this fix answerable for something it did not
  cause.
* **`lib.sh:139` and `lint.sh:174-177`.** Both assert something false today and
  both become true with this change. Editing them as well would be surface for
  no behaviour.
* **The remaining SC2312 argument-position sites.** `check-extra-masked-returns`
  already covers them and the gate is green; `inherit_errexit` is not a
  replacement for it.
* **Turning on more shellcheck optional checks.** `require-variable-braces` is
  recorded as rejected with a count at `lint.sh:180-182`.
* **`docs/cve-baseline.json`, the stale CVE row, and every other #90 item.** Only
  the `lib.sh:88-93` item is assigned to this lane.
