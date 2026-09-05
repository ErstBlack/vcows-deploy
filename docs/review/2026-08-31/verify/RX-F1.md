# RX-F1 — the static gate's signature omits files the six gates read

Verifier, Phase 3. Target `672a500`. Default verdict REFUTED; this one was reproduced.

Scratch: `git clone` of the `rv3` worktree into `mktemp -d`, with `.venv` and `.tools`
symlinked to the real repo so `ty`, `hadolint`, `tofu` and `shellcheck` resolve. No
tracked file in either checkout was touched; `.claude/` was read only.

Hook invoked as `.claude/settings.json:8` invokes it — `CLAUDE_PROJECT_DIR` set, a
synthetic `Stop` payload on stdin:

```json
{"session_id":"rxf1","transcript_path":"/dev/null","cwd":"<scratch>","hook_event_name":"Stop","stop_hook_active":false}
```

Cited lines re-verified at `672a500`:

| cite | text |
|---|---|
| `static-gate.sh:54` | `\| grep -zE '\.(py\|sh\|tf\|ya?ml)$\|(^\|/)Containerfile$' \` |
| `static-gate.sh:52-59` | `signature()` |
| `static-gate.sh:20-21` | "this hashes the contents of / the files the six gates actually read" |
| `static-gate.sh:47-49` | "Every file the six gates read: ruff and ty over *.py, tofu fmt over *.tf, …" |
| `static-gate.sh:61-63` | "Failing open means running the gate, never skipping it: **if git is unavailable / or a file vanishes mid-hash** …" |
| `scripts/lint.sh:126,127,150` | `ruff check`, `ruff format`, `tofu fmt -check -recursive` |
| `.github/workflows/ci.yml:52`, `.gitlab-ci.yml:50` | `just check` |

---

## Lens 1 — Reproduce

### Baseline

```
$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m3.577s   exit=0
state: pass 1d0b99d380ac76e11ec0bdd4c01c26139ddd871092052f21c1bf5e5f658103a6
```

`git ls-files -z | grep -zE '<the regex>' | wc -l` → **70**, matching the hook's own
`:31-33` comment. The complement is 78 tracked files (listed under Lens 2).

### 1a — `pyproject.toml`, ruff's config (the finder's case)

```
$ sed -i 's/^line-length = 88$/line-length = 40/' pyproject.toml   # pyproject.toml:124
$ ./scripts/lint.sh ; echo $?
  ...
  error: 2 gate(s) failed: ruff check
  ruff format
1                                                     (3.919s)

$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m0.040s   exit=0
state: pass 1d0b99d380ac76e11ec0bdd4c01c26139ddd871092052f21c1bf5e5f658103a6
```

Byte-identical state, same signature as the clean tree. `just lint` exits 1; the hook
returns a pass in 0.040s.

### 1b — `pyproject.toml`, ty's config

```
$ sed -i '168s|exclude = \["docs"\]|exclude = []|' pyproject.toml   # [tool.ty.src], :166-168
$ just typecheck ; echo $?
Found 1 diagnostic
error: Recipe `typecheck` failed on line 63 with exit code 1
1
$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m0.028s   exit=0   state unchanged
```

Both halves of `just check`'s static side are reachable through one unhashed file.

### 1c — `tests/libvirt-module.tftest.hcl`

```
$ tofu fmt -check -recursive .            # clean tree
exit=0
$ # prepend an unformatted `variable "rxf1_probe" { ... }` block
$ tofu fmt -check -recursive .
tests/libvirt-module.tftest.hcl
exit=3
$ ./scripts/lint.sh ; echo $?
  FAIL  tofu fmt
  error: 1 gate(s) failed: tofu fmt
1
$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m0.030s   exit=0   state unchanged
```

Independently reproduces Phase 1's ledger claim from `#57`'s side: `tofu fmt` exit 3 on
a scratch misformat of a file outside the 70.

### 1d — `justfile`

```
$ sed -i '55s|.*|    ./scripts/lint-typo.sh|' justfile
$ just lint ; echo $?
127
$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m0.036s   exit=0   state unchanged
```

The hook's own invocation path is inside its blind spot.

### Cached verdict, or never hashed? — both, and the order matters

Never hashed is the cause; the cached verdict is the mechanism. With the state file
removed on the *same* broken tree of 1a:

```
$ rm -f .cache/static-gate.state
$ time CLAUDE_PROJECT_DIR=$W bash .claude/hooks/static-gate.sh < payload.json
real 0m5.097s   exit=2
  E501 Line too long (80 > 40) --> container/entrypoint.py:2:41  ...
state: block 1d0b99d380ac76e11ec0bdd4c01c26139ddd871092052f21c1bf5e5f658103a6
```

Note the recorded signature is **the same `1d0b99d3…` as the clean tree** — proof the
file is not hashed. So the hook is not blind in the absolute sense: on the first `Stop`
of a session with no `.cache/static-gate.state` it does run the gates and does block.
It is blind to a *change* confined to those files while a `pass` is already recorded —
which is the normal state of any session past its first turn, since `.cache/` persists
across sessions.

The fix therefore has to be the alternation at `:54`. `record pass` is doing exactly
what it is documented to do; there is no second defect in the cache.

### One correction to the finder

`:61-63` does **not** assert the opposite. It scopes its claim to the empty-`cur` case
("if git is unavailable or a file vanishes mid-hash"), and that claim is true — an empty
`cur` does fall through to running lint (`:66`). The comments that are false are
`:20-21` ("the files the six gates actually read") and `:47-49` ("Every file the six
gates read"), both of which `tofu fmt` reading `.tftest.hcl` and ruff/ty reading
`pyproject.toml` contradict.

---

## Lens 2 — Reachability

78 tracked files fall outside the signature. Of those, the ones a `just lint` /
`just typecheck` gate actually reads:

| file | gate that reads it | reproduced |
|---|---|---|
| `pyproject.toml` `:123-164` | `ruff check`, `ruff format` (`lint.sh:126-127`) | 1a |
| `pyproject.toml` `:166-171` | `ty check` (`justfile:63`) | 1b |
| `tests/libvirt-module.tftest.hcl` | `tofu fmt -check -recursive` (`lint.sh:150`) | 1c |
| `justfile` | none — but it *is* how the hook runs both gates | 1d |

Measured negatives, correcting the finder's wording:

* **`docs/provider-0.9.8.lock.hcl` is not in the blind spot.** Appending a misformatted
  `provider` block leaves `tofu fmt -check -recursive .` at exit 0. `tofu fmt` handles
  `.tf`, `.tfvars`, `.tofu` and `.tftest.hcl`, not lock files. The finder's proposed
  `\.(toml|hcl)$` over-includes; `.tftest.hcl` is the only `.hcl` that matters.
* `tests/golden/libvirt.tfvars.json`, `docs/cve-baseline.json`, `container/tofurc`,
  `uv.lock`, `.claude/settings.json`, all `docs/**.md`, `licenses/**`, `tests/fixtures/**.xml`
  — no `just lint` gate reads any of them. Correctly outside.
* `.github/workflows/*.yml` and `.gitlab-ci.yml` are `ya?ml` and are **inside** the
  signature, so `workflows_carry_no_logic` is covered.

**Frequency.** Over the review range `4eb378b..672a500` (21 commits), **5 touch a
blind-spot file a gate reads**: `672a500`, `e4371ff`, `491d465`, `950ca7e`, `282cb7b`
(`pyproject.toml` 3, `justfile` 2, `tests/libvirt-module.tftest.hcl` 1). `672a500` —
HEAD, and the tree this review pins to — is itself a `justfile` + `pyproject.toml`
commit. Not hypothetical, but not the common turn either: ~24% of commits, and only the
subset of those turns that break a gate.

The finder's "a session is editing `tests/libvirt-module.tftest.hcl` right now" is not
currently observable: on branch `issue-21`, `git status --porcelain` shows only the
untracked `docs/review/2026-08-31/`. `#21`'s tftest work is deferred by `_PLAN.md:39-41`.

**Does it ever block when it should not?** No. `fail()` (`:90-95`) is called only after
`just lint` or `just typecheck` has actually exited non-zero, so a block is always a real
gate failure. Measured negatives: a doc-only edit (`CLAUDE.md`) with `pass` recorded →
exit 0 in 0.034s. An untracked scratch `probe_unused.py` with an unused import → hook
exit 2, but `./scripts/lint.sh` also exits 1 on the same tree, because `ruff check "$REPO"`
reads untracked files too. Consistent, not a false positive.

---

## Lens 3 — Already handled

**CI is the real gate, and it catches all four cases.** `.github/workflows/ci.yml:52` and
`.gitlab-ci.yml:50` both run `just check` (`justfile:70` = lint + typecheck + test), from
a clean checkout with no `.cache/static-gate.state`. Every reproduction above fails
`just check`. Nothing in the blind spot can reach `master`: the repo squash-merges through
PRs, and `CLAUDE.md` requires `just check` before pushing.

**The extension set is the spec, not a slip.** `#46` specified the filter as
"exit 0 unless it ends in `.py`/`.sh`/`.tf` or is `Containerfile`". The hook's regex is
that set plus `ya?ml`. `pyproject.toml`, `justfile` and `.hcl` were never in scope of
either issue.

**`#57`'s "no coverage gap by construction" is about a different axis, and is true on its
own axis.** It argues that `Stop` closes the hole `PostToolUse` had — *which tool wrote the
file*. It does. RX-F1 is the orthogonal axis — *which file was written* — and `#57` never
claimed to close it. The finder's "same hole, different axis" is fair as a characterisation
of the defect class, but it is not a case of a fix that failed to do what it said.

**`docs/research/tooling-2026-08-30.md` §5.3** ranked "`just lint` on edits to
`.py`/`.sh`/`.tf`/`Containerfile`" second and rejected a `Stop` hook running `just check`
on cost (24s suite, two tests shelling out to real `tofu`). That rejection stands; the hook
runs `just lint` + `just typecheck`, not `just check`. §5.3's top item (a `PreToolUse` deny
on `.cache/cosign.key`) was already retired by `#46` as stale — signing removed in `950ca7e`.

So the consequence is a delayed local feedback loop, not an escape. What survives Lens 3 is
narrower than the finder's framing and is still real: **two comments assert coverage the code
does not have** (`:20-21`, `:47-49`), in a file whose reason for existing is
`conftest.py:7` — "a gate that quietly passes because it did not run is worse than no gate."
A reader of those comments will believe a `pyproject.toml` turn was checked. That is the
defect, and it is a comment-accuracy defect with a one-alternation remedy, not a hole in the
project's gating.

---

## Verdict

**RX-F1 DOWNGRADED — high → medium.**

Reproduced four ways, exactly as claimed, including the 0.03–0.04s pass against a tree where
`just lint` exits 1 and the byte-identical `pass 1d0b99d3…` state. The blind spot is real and
`672a500` sits in it.

Downgraded because `just check` runs in both pipelines (`ci.yml:52`, `.gitlab-ci.yml:50`) and
catches every reproduced case, and because the extension set is `#46`'s written spec rather
than a regression. The residue is the false coverage claim at `:20-21` and `:47-49` — a
`.tftest.hcl` and a `pyproject.toml` that the six gates do read and the signature does not
hash.

Two corrections to carry into the issue: `:61-63` is scoped to the empty-`cur` path and is
true as written; and `docs/provider-0.9.8.lock.hcl` is **not** in the blind spot — `tofu fmt`
ignores lock files (measured, exit 0), so the fix is `\.toml$|\.tftest\.hcl$|(^|/)justfile$`,
not `\.(toml|hcl)$`.
