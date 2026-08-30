# B — Reporting spine and run directory

Agent: `B-reporting-spine` · Date: 2026-08-30 · Branch `feature/scaffold` at `6497f30`
Scope: `orchestrator/cli.py` (full, plus `git diff da3f45c..HEAD`), `orchestrator/backends/base.py`
result carriers, `orchestrator/tofu.py`, `orchestrator/config.py`, `orchestrator/backends/libvirt/destroy.py`,
`tests/test_cli.py`, `tests/fake_backend.py`.

## Summary

S2 landed. Measured against the S2/S9 checklist rows, every one is present and works:
`Backend.destroy` returns `Outcome`; `cmd_destroy` prints `skipped` and every `Problem`;
a non-empty `skipped` exits 1; `run.json` records `destroyed`/`skipped` rather than the
target list; `_guard` catches `BaseException` on both `deploy` and `destroy`; `_run_dir`
refuses a non-empty directory before a session is opened and still accepts an empty
mountpoint; `config.load` returns warnings and all three connected verbs print them;
`parse_outputs` raises on a missing `vms` output; `os.umask(0o077)` covers what OpenTofu
writes. The four carriers are documented as four rather than unified, which was the
decision the checklist asked for.

What is left is five residual holes, all in the *seams around* the new spine rather than
in the spine itself:

* Two are in the reconciliation step that decides whether a run is a success — a
  count-only comparison (**RW-B1**) and an unread `Outcome.failed` (**RW-B2**). Both are
  latent for the libvirt backend and both are exactly the second-backend case the seam
  exists for.
* One is an ordering hazard: the "ok" record is written after `inventory.json` and after a
  subprocess call that can raise (**RW-B3**).
* One is an asymmetry with the file's own stated principle: `problems` is attached to the
  `_Run` as soon as it exists so the failure record carries it, `tofu_warnings` is not
  (**RW-B4**).
* One is a message the file's own docstring says it exists to prevent (**RW-B5**).

---

## The six questions

### 1. Does every verb write a `run.json` that tells the truth on every exit path?

**deploy** — five terminal paths, five records:

| path | `outcome` | verified |
|---|---|---|
| refusal or fatal problem | `refused` | `cli.py:309`, `tests/test_cli.py:228` |
| nothing to create | `nothing-to-create` | `cli.py:315`, `tests/test_cli.py:198` |
| success | `ok` | `cli.py:374`, `tests/test_cli.py:164` |
| exception mid-apply | `failed` + `error` | `cli.py:237`, `tests/test_cli.py:250` |
| Ctrl-C mid-apply | `failed` + `error` | measured below |

**destroy** — four terminal paths, four records: `nothing-to-destroy` (`:482`),
`cancelled` (`:487`), `ok`/`partial` (`:501`), `failed` via `_guard`. `tests/test_cli.py:270`
pins the interrupted destroy.

Ctrl-C mid-apply, measured by monkeypatching `tofu.apply` to raise `KeyboardInterrupt`:

```
E7 rc= 1 outcome= failed error= KeyboardInterrupt:
E7 keys= ['backend','command','decisions','deployment','error','finished',
          'outcome','problems','started','vcows']
```

`_guard` catches `BaseException` (`cli.py:233`), so the interrupt is recorded and then
re-raised into `main`'s `except KeyboardInterrupt` (`:629`). `decisions` and `problems` are
present because `_deploy` attaches them to the `_Run` at `:302-303` before anything is
touched. That is the right shape and it works.

**validate** writes no `run.json` and has no `--run-dir`. That is deliberate and documented
at `cli.py:245` ("Offline only. No connection is opened and nothing is written"). Not filed.

The one thing a `run.json` still cannot say is *interrupted* as distinct from *crashed* —
both are `outcome: "failed"`, separated only by the `KeyboardInterrupt:` prefix inside
`error`. That is legible enough for a human and I am not filing it.

### 2. Does a non-empty `skipped` produce a non-zero exit, everywhere?

Yes, on every path that exists today.

* Backend returns an `Outcome` with `skipped` and no fatal problem → `cli.py:509-520`,
  `return 1`, `outcome: "partial"`. Pinned by `tests/test_cli.py:450`.
* Backend raises (`destroy.py:550`, `if out.failed`) → `DestroyError` carries the whole
  `Outcome` and `DestroyError.__init__` (`destroy.py:92-97`) writes every skipped name into
  the message → `main` prints it, exit 1.

There is no path where `skipped` is non-empty and the exit is 0.

Two notes that are not findings. First, on the `DestroyError` path the *`destroyed`* list is
dropped: the message enumerates fatal problems, skipped objects and warnings, but not what
was successfully removed, and `_guard` records only `error=<message>`. The docstring at
`destroy.py:88-89` shows this was considered and scoped to the leak side deliberately.
Second, `run.json` for a destroy always carries `decisions: []` and never names the VMs that
were skipped because their marker belongs to another deployment — those are printed to
stdout at `cli.py:472-477` and nowhere else. Both are gaps in the shipped-back artifact; both
are small, and neither is a false statement.

### 3. Can the success line, `run.json` and `inventory.json` still contradict each other?

Yes, twice. See **RW-B1** (they can disagree while the run exits 0) and **RW-B3** (they can
disagree while the run exits 1).

### 4. Do any of the four carriers still drop a problem with no consumer?

Three of the four are clean:

* `ConfigError.problems` — `main` prints every one (`cli.py:622-625`).
* `Discovered.problems` — `_look` merges them into the returned list (`cli.py:272`);
  `_report` prints them on preflight and deploy; `_destroy` prints them under an explicit
  "these were computed for a deploy" banner (`cli.py:462-471`) and records them.
  `config.load`'s warnings ride the same path — `load` raises on anything fatal
  (`config.py:155`), so what reaches `config_problems` is warnings by construction and the
  fatality checks at `cli.py:280` and `:306` losing sight of them costs nothing.
* `Outcome.problems` — printed at `cli.py:498-499` and recorded at `:506`. Its sibling
  `Outcome.failed` is the one that is dropped: **RW-B2**.

The fourth, `Result.diagnostics`, is partly dropped: **RW-B4**. Errors always survive — they
are joined into the `TofuError` message at `tofu.py:200-205`. Warnings survive only a
successful apply.

One smaller leak, noted and not filed: `tofu.outputs` (`tofu.py:241`) runs with
`capture_output=True` and discards `stderr` on success, so a warning from `tofu output -json`
reaches neither the terminal nor the record. `tofu output` on an applied plan emits
diagnostics rarely enough that I would not spend a change on it.

### 5. Does the run directory leak credentials at a readable mode?

No. `os.umask(0o077)` at `cli.py:618` is set before `parse_args`, so it is in force for
everything — including OpenTofu, which is the half a per-file `chmod` cannot reach. Measured
on a full deploy against a real `tofu` binary
(`tests/test_cli.py:570`, which ran rather than skipped in this environment):

```
$ ./.venv/bin/pytest tests/test_cli.py -k "readable or whole_pipeline" -v
3 passed
```

and on a stubbed deploy, every entry:

```
0o600 inventory.json      0o600 run.json
0o700 seed                0o600 seed/fake-artifact
0o700 tofu                0o600 tofu/main.auto.tfvars.json
0o600 tofu/main.tf
```

`_stage_module` uses `copyfile` rather than `copy` (`cli.py:426`) so a world-readable module
file in a checkout or an image layer does not carry its mode across; `_record` does the same
for `manifest.json` (`cli.py:202`). The `chmod` on an operator-supplied directory is now
conditional (`cli.py:143`) and downgrades a `PermissionError` to a warning that names the
mode and the reason (`:146-153`) — the `--user` bind-mount case from F-RUNDIR-06. That
warning leaves the run going in a directory that may be group- or world-*traversable*, but
every file inside is 0600 and traversal alone does not grant read, so the residual exposure
is the file names, not `user_data`.

### 6. `--run-dir` on a non-empty directory

Refused at `cli.py:126-137`, which runs in `cmd_deploy`/`cmd_destroy` before `_look` and
before `backend.connect`. `tests/test_cli.py:398` asserts `backend.sessions == []` after the
refusal, for both verbs, and asserts the pre-existing `run.json` was not overwritten.
`tests/test_cli.py:418` asserts an empty existing directory still works. Both pass.

The one rough edge is `--run-dir` naming an existing *regular file*: **RW-B5**.

---

## Findings

### RW-B1 — the module/asked reconciliation compares counts, not names

* **Severity:** medium · **Location:** `orchestrator/cli.py:363`

```python
inventory = backend.parse_outputs(raw)
if len(inventory.vms) != len(creating):
    raise tofu.TofuError(
        f"the module reported {len(inventory.vms)} VM(s) for the "
        f"{len(creating)} it was asked to create: "
        f"{', '.join(sorted(set(creating) - set(inventory.vms))) or 'names differ'}"
    )
```

The guard's own message computes `set(creating) - set(inventory.vms)` and carries an
`or 'names differ'` fallback, so the intent is a set comparison. The condition is a length
comparison. When the module reports the right *number* of VMs under the wrong *names*, the
comment two lines above — "the run has two artifacts that contradict each other, and
recording either as the truth is worse than refusing to record" — describes exactly what
happens, and nothing refuses.

**Measured.** Two-VM config (`app01`, `app02`), `tofu.outputs` stubbed to return
`{"app01": {}, "ghost": {}}`:

```
E2 rc= 0
E2 stdout= "created 2 VM(s); run directory .../runs/lab-a/20260830T033906Z"
E2 run.json created= ['app01', 'app02']  outcome= ok
E2 inventory vms= ['app01', 'ghost']
```

Exit 0, `outcome: "ok"`, and the two artifacts in one directory disagree about which VMs
exist.

**Reachability.** Not reachable through the libvirt backend at HEAD: `outputs.tf:8` keys the
`vms` output off `libvirt_domain.vm`, whose `for_each` is `var.vms`, whose keys are the
logical names `render` wrote. It is reachable for any backend whose module keys its outputs
on anything else — a namespaced or prefixed name, a vSphere folder path — which is precisely
the case `Existing.name`'s own docstring (`base.py:69-77`) warns about for the clash check.
Filed at medium rather than high for that reason.

**Fix.** `if set(inventory.vms) != set(creating):` — the message already reads correctly for
it. One line.

### RW-B2 — core never reads `Outcome.failed`; a fatal problem in a returned Outcome exits 0

* **Severity:** medium · **Location:** `orchestrator/cli.py:503`

```python
_record(
    run,
    "partial" if out.skipped else "ok",
    ...
```

`Outcome.failed` (`base.py:186-188`) is `any(p.fatal for p in self.problems)`. Its only
consumer in the tree is the libvirt backend's own `destroy.py:549`:

```
$ grep -rn "\.failed\b" --include=*.py .
orchestrator/backends/libvirt/destroy.py:549:    if out.failed:
```

Core reads `destroyed`, `skipped` and `problems`, and never `failed`. The ABC explicitly
allows a backend not to raise — `Backend.destroy`'s docstring (`base.py:426-427`): "A backend
is free to raise as well — and the libvirt one does, for anything fatal — but everything it
could not do must be in here whether it raises or not." A backend that takes that at its word
and returns a fatal `Problem` with an empty `skipped` list gets reported as a clean success.

**Measured.** Fake backend returning
`Outcome(destroyed=["app01"], skipped=[], problems=[Problem(ERROR, "could not delete /pool/app01.qcow2", "app01")])`:

```
E5 rc= 0
E5 stdout=  '  app01                destroy app01\ndestroyed 1 object(s)\n'
E5 stderr=  '  error [app01]: could not delete /pool/app01.qcow2\n'
E5 run.json outcome= ok  problems= ['error [app01]: could not delete /pool/app01.qcow2']
```

The problem is printed and recorded — but `outcome` is `"ok"`, the success line is printed,
and the exit code is 0. `Outcome`'s own docstring (`base.py:163-164`) calls this out: "a
backend that returns this without its consumer reading it reproduces that defect exactly."

`tests/test_cli.py:450` is the closest existing test and it uses `Severity.WARNING`, so no
test covers a fatal returned problem.

**Fix.** Treat `out.failed` as an outcome in `_destroy`: record `"failed"` and return 1, or
raise. Three lines.

### RW-B3 — the `ok` record is written after `inventory.json` and after a call that can raise

* **Severity:** low · **Location:** `orchestrator/cli.py:378`

```python
_write_json(run.path / "inventory.json", {"vms": inventory.vms})
_record(
    run,
    "ok",
    created=sorted(creating),
    tofu=tofu.version(workdir),      # <- subprocess, raises TofuError / OSError / TimeoutExpired
)
```

`tofu.version` is evaluated as an argument, so it runs *between* the two writes.
`tofu.version` can raise `TofuError` on a non-zero exit (`tofu.py:279`) or on `tofu` having
left `PATH` (`tofu.py:98`), `subprocess.TimeoutExpired` at 120 s, or `json.JSONDecodeError`.
Any of those reaches `_guard`, which writes `outcome: "failed"` — over a deploy whose apply
succeeded and whose `inventory.json` is already on disk.

**Measured**, `tofu.version` stubbed to raise `TofuError`:

```
E1 rc= 1
E1 run.json outcome= failed  error= TofuError: tofu version failed (exit 1)
E1 inventory exists= True  {"vms": {"app01": {}, "app02": {}}}
E1 stdout= '  app01 create ...\n  app02 create ...\n'   (no success line)
```

The run directory an air-gapped site ships back then says the deploy failed, beside an
inventory of two VMs that exist. Narrow trigger, which is why this is low, but the fix is
free.

**Fix.** Compute the version before writing `inventory.json`, or tolerate its failure the way
`_print_manifest` tolerates a broken manifest.

### RW-B4 — `tofu_warnings` never reaches the record of a failed run

* **Severity:** low · **Location:** `orchestrator/cli.py:358`

`run.extra["tofu_warnings"]` is assigned after `apply` returns. `_deploy` deliberately
attaches `problems` early, at `:302-303`, with the reason spelled out — "As soon as they
exist, so that every record from here on carries them — including the failure one." The tofu
warnings do not follow that rule, so `init`'s and `plan`'s warnings are absent from every
failure record, including the `TofuError`s `_deploy` raises itself at `:344`, `:349` and
`:368`.

**Measured**, `init` and `plan` returning one warning diagnostic each and `apply` raising:

```
E9 rc= 1
E9 keys= ['backend','command','decisions','deployment','error','finished',
          'outcome','problems','started','vcows']
E9 tofu_warnings= <ABSENT>
```

`Result.warnings`' docstring (`tofu.py:80-84`) states the whole justification for keeping
them: "They are here so the run directory can record them, which is the copy that outlives
the terminal." The failed run is the one where that copy matters most, and it is the one that
does not get it.

**Fix.** Accumulate into `run.extra["tofu_warnings"]` after each of `init`, `plan` and
`apply` returns, rather than once after all three.

### RW-B5 — `--run-dir` naming an existing file gives the bare `FileExistsError` `UsageError` exists to prevent

* **Severity:** low · **Location:** `orchestrator/cli.py:124`

`_run_dir` calls `path.mkdir(parents=True, exist_ok=True)` before it can classify anything.
`exist_ok=True` suppresses `FileExistsError` only when the existing entry is a directory; for
a regular file it propagates to `main`'s catch-all.

**Measured**, `--run-dir` pointing at a regular file:

```
E3 rc= 1
E3 stderr= "error: FileExistsError: [Errno 17] File exists: '.../notadir'"
```

`UsageError`'s docstring, twelve lines up at `cli.py:64-67`, describes this exact output as
the thing it was added to replace: "deliberately not a raw `OSError` reaching `main`'s
catch-all, which would print `error: FileExistsError: /runs/lab-a` and leave the operator to
work out which of the two paths they passed it means."

**Fix.** `if path.exists() and not path.is_dir(): raise UsageError(...)` before the `mkdir`.
Two lines.

---

## Checked and sound

* **`_guard`'s suppression is correctly narrow.** `contextlib.suppress(OSError)` around
  `_record` (`cli.py:236`) cannot swallow the original exception, and `_record`'s payload is
  strings, lists of strings and the JSON-decoded `tofu version` dict, so `json.dumps` cannot
  raise a `TypeError` that would replace the real error.
* **`_record`'s `**run.extra, **extra` merge.** `_destroy` passes
  `problems=run.extra["problems"] + [...]` at `:506`; the later `**extra` wins, so the
  accumulated list is what lands. `run.extra["problems"]` is assigned at `:471`, before every
  `_record` call site in `_destroy`.
* **`refused` and `nothing-to-create` create no subdirectories**, so a refusal cannot be
  mistaken for a partial deploy. `seed.mkdir()`/`workdir.mkdir()` are below both returns.
* **`_stage_module` refuses rather than skips** what it will not copy (`cli.py:416-421`), and
  `copyfile` keeps the umask authoritative for module content.
* **`manifest.json` is copied into every record**, and its absence outside the image is
  silent (`cli.py:195`). `tests/test_cli.py:500` and `:515` pin both halves.
* **`config.load` returning `(cfg, problems)`** removed `cmd_validate`'s second `validate()`
  call, and all three connected verbs print the warnings. `tests/test_cli.py:115` and `:130`
  pin the deploy and destroy faces.
* **`parse_outputs` raises on a missing `vms` output** rather than reading it as an empty
  inventory (`backends/libvirt/__init__.py:102-106`), which is the other half of RW-B1's
  guard and is correct.
* **`_run_dir` ordering** — `mkdir` → `resolve` → emptiness → conditional `chmod`. The
  resolve is after creation so a relative `--run-dir` cannot escape, and the emptiness
  refusal precedes the `chmod` so a directory that is going to be rejected is never modified.
* **`Popen` + `wait()` in `tofu._run`** (`tofu.py:174-195`) lets Ctrl-C reach tofu so it
  releases the state lock, with a second interrupt escalating to `kill`. That is S9's row and
  it landed as written.
* **`VCOWS_TRACEBACK`** prints the traceback at `cli.py:638-641`.

## Coverage note

Read in full: `orchestrator/cli.py`, `orchestrator/backends/base.py`, `orchestrator/config.py`,
`orchestrator/tofu.py`, `orchestrator/backends/libvirt/destroy.py`,
`orchestrator/backends/libvirt/__init__.py`, `orchestrator/backends/libvirt/tofu/outputs.tf`,
`container/entrypoint.py`, `tests/fake_backend.py`, `tests/test_cli.py`,
`docs/review-2026-08-29/13-run-dir-artifact.md`, the S2/S9 rows of the remediation checklist.

Not read or not checked:

* `orchestrator/backends/libvirt/{preflight,prepare,render,schema}.py` beyond what `_look`
  and `Discovered` needed — other dimensions own those.
* No live libvirt and no rig, so `Outcome` was exercised only through the fake backend. The
  claim that RW-B2 is latent for libvirt rests on reading `destroy.py:549`, not on running it.
* Real-`tofu` coverage came from the existing gated tests in `tests/test_cli.py`; my own
  probes stubbed `orchestrator.tofu` except for `test_E8_modes`, which is why the mode
  evidence is quoted from `tests/test_cli.py:570` as well.
* The `--run-dir` / `--user` / bind-mount matrix under a real rootless podman — the prior
  review flagged it as deserving its own agent and it still has not been run.
* Nothing under `scripts/`, `justfile`, `.github/` or `.gitlab-ci.yml`; post-merge by
  instruction.
