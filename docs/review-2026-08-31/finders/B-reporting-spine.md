# B — Reporting spine and record completeness

Dimension B · 2026-08-31 · pinned worktree at `origin/master` `672a500`. Scope: `cli.py`,
`backends/base.py`, `tofu.py`, `container/entrypoint.py`, their tests, and
`backends/libvirt/destroy.py:495-561`. Probes ran in a `mktemp -d` copy; nothing tracked was
touched.

## Verdict on the inherited items

| item | status | evidence |
|---|---|---|
| RW-B3 `ok` record after a call that can raise | **DONE** | `_tofu_version` (`cli.py:323-337`) swallows `TofuError`/`SubprocessError`/`ValueError`/`OSError`; `tests/test_cli.py:270` |
| RW-B4 `tofu_warnings` only after apply | **PARTIAL** | `cli.py:318-320`, `:392`, `:394`, `:411` — **RX-B1** |
| RW-B5 `--run-dir` on a regular file | **DONE** | `cli.py:128-133`; `tests/test_cli.py:486` |
| #10 three deploy-record defects | RW-B3 + RW-B4 (partial) + RW-B5 | above |
| #11 `Outcome.failed` unread; count-not-name reconcile | **DONE** | `cli.py:419` set comparison; `:577`/`:583` read `out.failed`; `tests/test_cli.py:565`, `:593` |
| #13 `install()` on `validate` | **DONE, behaviour changed** | `entrypoint.py:59`, `:251`; measured against the image below |

**#13 is a real fix, not a docstring edit.** `OFFLINE = frozenset({"validate", "version"})`
(`entrypoint.py:59`); `main` gates `install` on `verb()` (`:251`). Measured against the image
Phase 0 built, `localhost/vcows-deploy:0.1.0.0`:

```
$ podman run --name probeB1 ... validate  /work/lab.yaml ; podman diff probeB1 | grep -i ssh
(no output)
$ podman run --name probeB2 ... preflight /work/lab.yaml ; podman diff probeB2 | grep -i ssh
A /root/.ssh
A /root/.ssh/config
```

`cmd_validate`'s docstring (`cli.py:270`) is now true of the image, and the connecting
verbs still get their config. The one comment that did not keep up is **RX-B5**.

## Does every failure path write a complete `run.json`?

| path | run directory afterwards |
|---|---|
| `load` raises `ConfigError` | **no run directory at all** — `_run_dir` needs `cfg["deployment"]` so it runs after `load` (`cli.py:312-314`, `:489-491`); even an explicit `--run-dir` gets nothing. Measured. By design, not filed |
| `--run-dir` refused (file, or non-empty) | `UsageError`, nothing written, no session opened (`tests/test_cli.py:486`, `:466`) |
| `_look` raises (connect / preflight) | `failed`, `error`, `decisions: []`; no `problems`/`tofu_warnings` — neither exists yet. Correct . A raise in `prepare`/`render`/`_stage_module` adds `decisions` + `problems` |
| refusal, or nothing to create | `refused` / `nothing-to-create` + `decisions` + `problems` + `tofu_warnings: []` (`cli.py:359`, `:365`) |
| `init`/`plan`/`apply` raise | `failed` + `error` + warnings **of the earlier steps only** — **RX-B1** |
| plan creates nothing, or names disagree | `failed`, `error` names the count or the set difference, `inventory.json` correctly absent (`cli.py:402`, `:407`, `:424`; `tests/test_cli.py:593`) |
| `tofu version` fails after a good apply | `ok`, `tofu: null`, `inventory.json` present — **RX-B6** on the missing reason |
| destroy: nothing to destroy / cancelled | recorded — but *inside* the `with connect` block, so a raising session close overwrites them with `failed`. Measured; nit, not filed |
| destroy returns, or raises an `Exception` carrying `.outcome` | `failed`/`partial`/`ok` + `destroyed` + `skipped` + `problems` (`cli.py:552-564`, `:575-581`; `tests/test_cli.py:625`) |
| destroy: **Ctrl-C mid-teardown** | `failed` + `error` only. **No `destroyed`, no `skipped`** — **RX-B2** |
| any destroy | VMs skipped because their marker names another deployment are never recorded — **RX-B3** |

## Findings

### RX-B1 — the tofu step that *fails* still loses its own warnings; `TofuError.result` has no reader

* **Severity:** medium · **Location:** `orchestrator/cli.py:411`, `orchestrator/tofu.py:91`

`_note_warnings` (`cli.py:318-320`) is called on the *return* of `init`, `plan` and `apply`
(`:392`, `:394`, `:411`), so a step that raises loses its own warnings. `_run` has them: it
builds the full `Result` at `tofu.py:198` and hands it to `TofuError` at `:204`, and
`TofuError.result` (`tofu.py:91`) exists for this. It has **no production consumer**:

```
$ grep -rn "\.result\b" --include=*.py .    ->  tofu.py:91 (the assignment) and
                                                 tests/test_tofu_driver.py:186 only
```

**Reproduction**: `init` warns, `plan` warns, `apply` raises a `TofuError` carrying one
warning and one error.

```
P1 outcome= failed   tofu_warnings= ['warning: init warned']
P1 error= TofuError: tofu apply failed (exit 1): error: apply blew up
```

`apply warned about deprecated arg` rode in on the exception and is in no artifact.

**Why it matters.** `Result.warnings`' docstring (`tofu.py:77-84`) gives the whole reason
these are kept: "so the run directory can record them, which is the copy that outlives the
terminal." The comment the fix added at `cli.py:387-391` names the same case — "a plan that
warns and an apply that raises is exactly the run whose warnings are worth keeping" — and
the half it still drops is the failing step's own. A provider deprecation warning beside the
error that killed the apply is what a site would most want out of that directory.
**Fix:** read `getattr(exc, "result", None)` in `_guard` and append its warnings, the shape
`cli.py:559` already uses for `.outcome` — three lines, no new surface.

### RX-B2 — an interrupted destroy records nothing it removed; `except Exception`, where `_guard` says `BaseException`

* **Severity:** medium · **Location:** `orchestrator/cli.py:552`

`_guard`'s docstring (`cli.py:251-253`) is explicit: `BaseException`, "so a Ctrl-C mid-teardown
-- the run with the most to say and the least chance of saying it -- writes one too." The block
that captures *what the teardown got through* is narrower — `except Exception as exc:` at `:552`
— so a `KeyboardInterrupt` skips it, `run.extra["destroyed"]`/`["skipped"]` are never set, and
`_guard` writes `error` alone.

**Reproduction**, the backend raising a `KeyboardInterrupt` carrying the partial outcome,
and the same outcome on a plain `Exception` for contrast:

```
P6 outcome= failed destroyed= <ABSENT>   skipped= <ABSENT>
P7 outcome= failed destroyed= ['app01']  skipped= ['/pool/app02-seed.iso']
```

The libvirt backend is the other half: `destroy` accumulates into a local `out` from
`destroy.py:496` and attaches it to nothing but its own `DestroyError` (`:508`, `:521`,
`:560`), so a `KeyboardInterrupt` out of `_stop`, `_undefine` or `_delete_volume` discards a
live, populated `Outcome`. Ctrl-C during a teardown deleting multi-GB volumes over SSH is
the ordinary way this happens.

**Why it matters.** `#8` was "a destroy that raises records nothing it removed." Fixed for
`Exception`, still true for the interrupt: three domains undefined, and the shipped-back
directory says only `KeyboardInterrupt:`. `tests/test_cli.py:338` pins the interrupted
destroy but raises before any work, so it asserts only `outcome` and `error`.
**Fix:** `except BaseException` at `:552` (it re-raises unchanged), plus
`except BaseException as exc: exc.outcome = out; raise` around `destroy.py`'s loop — two
lines each, and without the second the first captures nothing.

### RX-B3 — a destroy never records the VMs it deliberately left alone

* **Severity:** low · **Location:** `orchestrator/cli.py:528-537`

A marked VM whose marker names another deployment is printed as a `skip` row on stdout and
appears in no artifact. Measured, one target plus one foreign-marker VM `app99`:

```
P2 record = {"command":"destroy","decisions":[],"destroyed":["app01"],"outcome":"ok",
             "problems":[],"skipped":[]}
```

`skipped: []` is true in the record's own sense — nothing was left behind by the teardown —
and `app99`, a marked VM on the same host that vcows identified and chose not to touch, is
nowhere. A destroy record also always carries `decisions: []`; `run.decisions` is set only on
the deploy path (`cli.py:349`).

**Why it matters.** Same argument as `Result.warnings`: stdout is the copy that does not
survive, and "did vcows see the other lab's VMs and leave them?" is unanswerable from the
shipped directory. **Fix:** one key, `left_alone=[e.name for e in others]`, into `run.extra`.
Not a false statement today, which is why it is low.

### RX-B4 — `#35` left one of the four report sites on a literal width

* **Severity:** low · **Location:** `orchestrator/cli.py:571`

`_NAME_W`/`_VERB_W` (`cli.py:175-176`) exist because "the widths were previously repeated as
literal padding ... so changing either width silently misaligned two of the three sites"
(`:171-174`). `_row` covers `:188`, `:531`, `:539`. The fourth did not move:
`print(f"  {name:<20} skipped, not removed by this run")`. It is a two-column line so `_row`
does not fit, but the `20` is `_NAME_W` spelled again, in the same function whose `skip` rows
it must line up under. Changing `_NAME_W` still silently misaligns one site. **Fix:**
`f"  {name:<{_NAME_W}} skipped, ..."`. One line.

### RX-B5 — `entrypoint.py` cites a `cli.py` line that is 35 lines out

* **Severity:** low · **Location:** `container/entrypoint.py:189`

The comment reads ``` `orchestrator/cli.py:670`'s `os.umask(0o077)` cannot close that ```.
`os.umask(0o077)` is at `cli.py:705`; `cli.py:670` is `argparse.ArgumentParser(...)`. It is the
argument for the `O_EXCL` mode-on-create shape — reasoning right, citation dead. `_PLAN.md:166`
makes drifted citations a named class for this review, and `#19` closed a batch of them in this
same range. **Fix:** one number. The same class survives at `backends/libvirt/schema.py:272`,
which cites `cli.py:485` for `cmd_destroy`'s `load` — that is at `:489`, and `:485` is the
`# -- destroy ---` banner. (C's file; the citation points into mine.)

### RX-B6 — `run.json` says `"tofu": null` and never says why

* **Severity:** low · **Location:** `orchestrator/cli.py:434`

`_tofu_version` is the right fix for RW-B3, but its explanation goes to stderr only (`cli.py:336`)
while the null lands in the record — `tests/test_cli.py:297` asserts the two halves against
different streams. Same shape at `cli.py:157-163`, where the "cannot make this directory 0700"
warning, the one telling a site its `user_data` is readable, is stderr-only because it fires before
`_Run` exists. A `null` reads as "vcows did not try" and means "tried and could not".
**Fix:** `_tofu_version` returns the message instead of `None`. One line.

## Checked and sound

* **`_record`'s merge** `**run.extra, **extra` (`cli.py:242-243`) — the explicit `problems=`
  at `:580` wins, and `run.extra["problems"]` is assigned at `:527`, above every `_record`
  call site in `_destroy`.
* **`_guard`'s `contextlib.suppress(OSError)`** cannot swallow the original exception, and
  its payload is strings and a decoded dict, so `json.dumps` cannot raise over it either.
* **`refused` and `nothing-to-create` create no subdirectories** — `seed.mkdir()`/
  `workdir.mkdir()` are below both returns (`cli.py:375-378`).
* **Reconciliation compares names** (`:419`) and `inventory.json` is written only after it
  passes (`:429`); **`out.failed` is read** at `:577`/`:583` for `failed`, exit 1 and a line
  naming `run.json`.
* **`Problem.error`/`.warning`** (`base.py:50-58`) and `problems_from` (`base.py:70-89`) are
  pure de-duplication; `config._blame_the_filename`'s three-argument form is preserved.
* **`_capture`** (`tofu.py:238-264`) merges `outputs` and `version` with no behaviour change
  (both already raised `TofuError`/`JSONDecodeError`/`TimeoutExpired`) and `version` now
  carries `stderr`; **`module_dir`** (`cli.py:89-101`) is `inspect.getfile`, `assert` gone,
  `#34`'s submodule caveat in the docstring. `#33`/`#34` landed as written.
* **Entrypoint `verb()`** takes the first non-flag argument by position, so a config named
  `validate` cannot suppress the install for a `deploy` (`tests/test_entrypoint.py:260`), and
  `OFFLINE` defaults to installing, so a verb added later fails safe.

## Not checked

* No rig call from this dimension; RX-B2's libvirt half rests on reading `destroy.py:495-561`,
  not on interrupting a live teardown. `preflight.py`, `prepare.py`, `render.py` and
  `schema.py` beyond what `_look` needed, and the rootless-podman matrix (G), were left.
