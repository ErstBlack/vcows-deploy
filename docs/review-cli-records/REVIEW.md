# Lane L4 — scoped review of `lane/cli-records`

Input: `git diff origin/master...lane/cli-records` and nothing else. Five commits
on top of `411c12d`:

| | subject |
|---|---|
| `8322c17` | `#85` — the unguarded `_run_dir` mkdir, and `resolve()` moved above it |
| `345eee5` | issue 90's two `cli.py` items — the chmod comment, and `cmd_version`'s tuple |
| `a29a984` | `#89` — five of the six findings filed under it |
| `18a43cb` | `#80` — the suppressed run record, reported rather than un-suppressed |
| `44dda5f` | the citations this branch moved, and one assertion a new test was missing |

Four files, `+326 / −40`: `orchestrator/cli.py`, `orchestrator/backends/libvirt/schema.py`,
`tests/test_cli.py`, `tests/test_libvirt_schema.py`. `git diff --name-only` returns
those four and nothing else.

---

## Lens 1 — did it do what the plan said

### 1.1 Scope. No RX-B2 content leaked in — checked three ways, not asserted

RX-B2 was split out of `#89` into `#96` after the plan was written. It had two
halves and the branch carries neither.

* **`orchestrator/backends/libvirt/destroy.py` is untouched.**
  `git diff origin/master...HEAD --name-only` returns four files and that is not
  one of them; the same command restricted to `destroy.py`, `tofu.py`,
  `backends/base.py`, `scripts/`, `README.md`, `tests/conftest.py` and
  `container/` produces zero lines of output.
* **`cli.py`'s destroy call site was not widened.** `except Exception as exc:` is
  at `:605` on this branch and `:552` on `origin/master` — the same two
  occurrences in each tree (the second is `main`'s catch-all), moved only by the
  lines above them.
* **No suppression was added.** `git diff origin/master...HEAD | grep -E
  '^\+.*(BaseException|exc\.outcome|ty: ignore|type: ignore)'` returns nothing.
  The `# ty: ignore[unresolved-attribute]` the plan budgeted for was RX-B2's; the
  patch as landed needs none, and `ty check` passes without one.

`docs/plans/issue-89.md` was amended rather than left stale: a scope block at the
top, `MOVED TO #96` on §1.2, §3.2, §4.2, §5.2 and §7.2, `(now #96)` on the four
§2 anchor rows and the two §8.2 rows, `destroy.py`'s §6 row zeroed, and §7's and
§8's counts corrected.

`#90` is **not** closed by anything here. `345eee5`'s body names the nine items
and the stale baseline row it does not touch, and uses no closing keyword
anywhere near a `#NN`.

### 1.2 Fix-by-fix, against §5 of each plan

| finding | plan §5 said | landed | same? |
|---|---|---|---|
| `#85` | `.resolve()` onto the assignment; `try`/`except OSError` round the mkdir raising `UsageError` | both, verbatim | yes |
| issue 90, `cli.py:148` | comment only, both errno families named, `--user` sentences kept | +8 comment lines, `except PermissionError` unchanged | yes |
| issue 90, `cli.py:658` | the four-class tuple from `_tofu_version` | that tuple, plus a comment saying why | yes, +comment |
| RX-B6 | `Problem.warning` into `run.extra["problems"]`, signature takes the run | verbatim | yes |
| RX-B1 | one `except tofu.TofuError` round the three steps, re-raising | verbatim, plus the forced E501 rewrap | yes |
| RX-B3 | `left_alone` as a name→deployment mapping, beside the `problems` assignment | verbatim | yes |
| RX-C1 | `_nic_checks_are_safe` predicate, four clauses | verbatim except the annotation — see 3.1 | one change |
| RX-C2 | `try`/`except ValueError` returning one `Problem.error`, early | verbatim, plus a comment saying why early | yes, +comment |
| `#80` | `try`/`except OSError` round `_record`, report nested inside `contextlib.suppress(OSError)` | verbatim | yes |

Three plan defects surfaced by building it, all recorded in `a29a984`'s body and
annotated in the plan file:

* **`#89` §5.5 annotates the predicate `vm: dict` while its first clause is
  `isinstance(vm, dict)`.** `ty` rejects the test that passes a string. Landed as
  `vm: object`. This is the only deviation from a plan's §5.
* **`#89` §7.5's claim that the two edge cases "pass today" is false for the
  non-mapping VM.** `schema.validate` with a string VM raises `TypeError` out of
  `_check_volume_names` on `origin/master` as well, downstream of the loop and
  unrelated to the guard; and the case is unreachable through `config.load`,
  which returns the core schema's `'app01' is not of type 'object'` without ever
  asking the backend (`config.py:182-185`). Landed pinned on the predicate
  directly.
* **`#89` §7.6's third parametrize row fails `ruff` RUF001** as written — a
  literal FULLWIDTH NUMBER SIGN. Landed as the escape `\uff03`.

The lint fact `#89` §5.3 *did* predict — E501 on `plan proposes no creates` after
the reindentation — held, and the rewrap is in the same hunk.

### 1.3 What the plans asked for and this lane could not do

`#85` §8.5 and `#80` §8.3 both end in an end-to-end arm against a rebuilt image.
The lane was instructed not to rebuild; the pinned `localhost/vcows-deploy:0.1.0.0`
carries `672a500` and can only show the before-state. Both plan files now say so
at the step, and `18a43cb`'s body says it in as many words rather than implying
the arm was run. **`#80`'s container arm is still owed against the next image
build.**

---

## Lens 2 — do the new tests have teeth

Nine new ids on `#89`, two on `#85`, two on issue 90, one on `#80`; two existing
tests extended in place. Every assertion below was made to fail and then made to
pass again, on this tree. Nothing here is cited from the reverification.

| assertion | revert this | observed red |
|---|---|---|
| `'PermissionError' not in err` (×2 verbs) | `_run_dir`'s `try`/`except` | `assert 'PermissionError' not in "error: Perm...table/run'"` |
| `cli.main(["version"]) == 0` (×2 classes) | `:658` back to `(TofuError, OSError)` | `assert 1 == 0`, stderr `error: JSONDecodeError` / `error: TimeoutExpired` |
| `any("cannot record the tofu version" in p for p in record["problems"])` | the `run.extra["problems"].append` | `assert False` |
| `record["tofu_warnings"][2]` | the `except tofu.TofuError` block | `['warning: in... plan warned'] == ['warning: in...` |
| `record["left_alone"] == {"elsewhere": "lab-b"}` | the `left_alone` assignment | `KeyError: 'left_alone'` |
| `"already used by" in out` (×2 triggers) | `_nic_checks_are_safe`, bare `continue` | absent in both |
| `"is not a URL"` (×3 rows) + the two-`where` test | the `try`/`except ValueError` | `ValueError: Invalid IPv6 URL` escapes `schema.validate`, all four ids |
| `"left no record" in err` | `_guard` back to `with contextlib.suppress` | `assert ('left no record' in 'error: RuntimeError: the conne...` |
| the predicate's four `is False` clauses | drop `all(isinstance(nic, dict) ...)` | `assert True is False`, **and** the sibling test red with `AttributeError: 'str' object has no attribute 'get'` out of `mac_of` |
| same | drop `isinstance(vm.get("name"), str)` | `assert True is False` |

**The `resolve()` half of `#85` needs its own check and got one**, because the
`#85` test passes an absolute `--run-dir` and would pass without it. Default
layout, the cwd's `runs/` at mode 0555, three arms measured:

```
origin/master              error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
try/except only, :135 resolve  vcows: cannot create the run directory runs/lab-a/20260831T073049Z: ...
this branch                vcows: cannot create the run directory /tmp/tmpni1ti2l8/runs/lab-a/20260831T073040Z: ...
```

**The `cli.py:148` comment has no test, deliberately, and was held honest by
re-running the behaviour instead.** A 0555 run directory with `cli.os.chmod`
stubbed to `OSError(EROFS)`, two VMs already existing, both arms byte-identical
to `reverify/issue-90-cli-148.txt`:

```
as shipped: error: OSError: [Errno 30] Read-only file system: '/tmp/.../runs-ro'
widened:    vcows: cannot make /tmp/.../runs-ro 0700; it stays 0555. ...
            error: PermissionError: [Errno 13] Permission denied: '/tmp/.../runs-ro/run.json'
              app01                skip    exists as 'app01' (not compared)
              app02                skip    exists as 'app02' (not compared)
```

**Weaknesses found and fixed during the review**, rather than shipped:

* `test_a_nic_that_is_not_a_mapping_still_skips_the_nic_checks` set up a
  duplicate address on `vms[1]` and asserted nothing about it. It now asserts
  `"already used by" not in messages(problems)` — the observable consequence of
  the skip, and the round trip that is the price of not crashing. `44dda5f`.

**Weaknesses acknowledged and left**:

* `test_a_vm_that_is_not_a_mapping_still_skips_the_nic_checks` tests a private
  helper rather than behaviour. That is the honest instrument here (1.2 above),
  and there is repo precedent (`tests/test_cli.py` calls `cli._stage_module`),
  but it is a weaker test than its siblings and would not notice the `continue`
  being removed outright. Its sibling would, and does — the `AttributeError` row
  in the table above is exactly that.
* No test asserts the `#80` report survives a closed stderr. The nested
  `contextlib.suppress(OSError)` is the original invariant restated and is
  reviewed rather than tested; testing it means monkeypatching `print`, which
  pins the implementation and not the promise.

**No conditional skip is introduced.** `tests/test_gates.py` AST-walks the suite
for a bare `pytest.skip`, `pytest.importorskip` or `pytest.mark.skip` and is
green. Nothing here needs the rig, an image, or `tofu`: the skipped count is
unmoved at 25 across all five commits.

---

## Lens 3 — what moved, and who else points at it

### 3.1 Line-number shifts

`orchestrator/cli.py` grew in five places, so everything below `:121` moved.
`orchestrator/backends/libvirt/schema.py` gained `_nic_checks_are_safe` at `:258`
and eleven lines in `_check_target`, so everything below `:247` moved.

Anchors named in the plans, `origin/master` → this branch:

| anchor | was | is |
|---|---|---|
| `_run_dir`'s mkdir | `cli.py:134` | `:134` (now the `try`) |
| the chmod guard's comment | `cli.py:148-152` | `:153-164` (five lines became twelve) |
| `_guard`'s suppression | `cli.py:258-262` | `:270-285` |
| `_record`'s manifest suppression (`#80`'s second, unfixed) | `cli.py:221-227` | `:233-239` — unchanged, but moved |
| `_tofu_version`'s exception tuple | `cli.py:335` | `:359` |
| the destroy call site's `except Exception` | `cli.py:552` | `:605` |
| `cmd_version`'s tuple | `cli.py:658` | `:716` |
| `main`'s `os.umask(0o077)` | `cli.py:705` | `:763` |
| the four `load(args.config, REGISTRY)` | `cli.py:271`, `:301`, `:312`, `:485` | `:295`, `:325`, `:336`, `:531` |
| the `continue` | `schema.py:247-249` | `:247-248` |
| `_check_target`'s `urlsplit` | `schema.py:350` | `:371` |

### 3.2 Citations that went stale, and what was done about each

`grep -rEn 'cli\.py:[0-9]+|schema\.py:[0-9]+|config\.py:[0-9]+|tofu\.py:[0-9]+|base\.py:[0-9]+|findings\.md:[0-9]+|conftest\.py:[0-9]+'`
over `orchestrator/ tests/ scripts/ container/ CLAUDE.md README.md` — the live
files, excluding the dated archives under `docs/review-*`.

* **`orchestrator/backends/libvirt/schema.py:293`** cited `cli.py:271`, `:301`,
  `:312`, `:485`. Correct at `origin/master`, broken by this branch. **Fixed**
  in `44dda5f` to `:295`, `:325`, `:336`, `:531`, measured with
  `grep -n "load(args.config, REGISTRY)"` against both trees.
* **`container/entrypoint.py:189`** cites `cli.py:670` for `os.umask(0o077)`.
  Already wrong before this branch and already one of issue 90's eleven items,
  which records the right number as `:705` — true at `672a500` and at
  `origin/master`, measured. **After this branch it is `:763`.** This lane is
  scoped out of `container/`, so it is flagged and not touched. **Whoever lands
  that item must re-measure rather than copy `:705` out of the issue body.**
* `orchestrator/backends/libvirt/tofu/variables.tf:10` cites `schema.py:198-211`
  and `tests/libvirt-module.tftest.hcl:401` cites `schema.py:129` — both above
  `:244`, both unmoved. `schema.py:283`'s `config.py:57`, `schema.py:266`'s
  `config.py:117-119`, `CLAUDE.md`'s `conftest.py:7`/`:37`, and
  `scripts/lint.sh:176`'s `conftest.py:7` are in files this branch does not
  touch. All verified unmoved.

The archived reviews under `docs/review-2026-08-29/`, `-08-30/` and `-08-31/`
carry dozens more of these and are deliberately left alone: they are evidence
pinned to the revision they were taken at, which is the same reason
`pyproject.toml` excludes `docs/` from `ruff`.

### 3.3 Citations this branch introduced, all verified against the current tree

| new citation | at | checked |
|---|---|---|
| `tofu.py:77-84` for `Result.warnings` | `cli.py:570` | `Result.warnings`' docstring, "the copy that outlives the terminal" |
| `findings.md:121` for the reporting obligation | `cli.py:571` | "reported as found and skipped, **with their deployment names**" — which is also the argument for the mapping over a bare name list |
| `config.py:117-119` | `schema.py:266`, `tests/test_libvirt_schema.py:174` | "every problem rather than the first" |
| `config.py:182-185` | `tests/test_libvirt_schema.py:369` | the early return that never asks the backend |
| `UsageError:66-69` | `tests/test_cli.py:620` | unmoved; `class UsageError` is still at `cli.py:63` and its docstring at `:64-70` |

### 3.4 Comments and record keys

Comments: five amended or added in `cli.py` (the chmod guard, `_guard`,
`_tofu_version`, the tofu-step `except`, `left_alone`, `cmd_version`'s tuple),
two in `schema.py` (`_nic_checks_are_safe`'s docstring, `_check_target`'s
early return). One comment **deleted**: `schema.py:248`'s "The checks below index
into fields the schema just rejected", which the predicate replaces and which was
the sentence the finding is about.

Record keys: one new (`left_alone`, on the three destroy `_record` calls) and one
new string in the existing `problems` list. `run.json`'s `tofu` field keeps its
`dict | None` type, which is the whole reason `#89`'s own suggested fix was
rejected. No new config field, no new `Backend` protocol member, no new module,
no new dependency, no new import.

---

## Ledger

| | finding | severity | state |
|---|---|---|---|
| L1 | No RX-B2 content on the branch. `destroy.py` untouched, `except Exception` unwidened, no `ty: ignore` added. | — | verified three ways |
| L2 | `#90` not closed, no closing keyword near a `#NN` it does not close. | — | verified |
| L3 | Every §5 landed as written, one deviation: `_nic_checks_are_safe(vm: object)` not `vm: dict`. | low | deliberate, `ty` forced it, recorded |
| L4 | `#89` §7.5's "the last two pass today" is false for the non-mapping VM — pre-existing `TypeError` from `_check_volume_names`, and the case is unreachable through `config.load`. | low | plan annotated, test re-aimed at the predicate |
| L5 | `#89` §7.6's third parametrize row fails `ruff` RUF001 as written. | low | escaped as `\uff03`, plan annotated |
| L6 | `#85` §8.5 and `#80` §8.3's image arms were not run — the lane may not rebuild. | medium | plans annotated, commit bodies say so; **`#80`'s is owed against the next image build** |
| L7 | `container/entrypoint.py:189`'s `cli.py:670` citation is now `:763`. Already an issue-90 item; this branch moved it again and the issue body's `:705` is now also wrong. | low | flagged, out of this lane's scope |
| L8 | `schema.py:293`'s four `cli.py` citations broken by this branch. | low | fixed in `44dda5f` |
| L9 | One new test asserted nothing about its own setup. | low | fixed in `44dda5f`, teeth measured |
| L10 | `test_a_vm_that_is_not_a_mapping_...` tests a private helper, not behaviour. | low | accepted with the reason; its sibling covers the behaviour |
| L11 | `#80`'s nested `contextlib.suppress(OSError)` is untested. | low | accepted; the alternative pins `print` rather than the promise |
| L12 | `#80`'s second suppression (`_record`'s manifest copy at `cli.py:220-227`) is unfixed and the file is still absent under arm A. | low | deliberate, in `18a43cb`'s body; file separately if it is worth filing |
| L13 | `#27` was **not** reopened. Its own scope is complete; RX-C1 closes the residual of its rationale. | — | measured, in `a29a984`'s body |

**Gate:** `just check` green at every one of the five commits — six lint gates,
`ty` clean, and `412 → 414 → 416 → 425 → 426 passed, 25 skipped` throughout.
Branch baseline was `412 passed, 25 skipped` at `411c12d`.
