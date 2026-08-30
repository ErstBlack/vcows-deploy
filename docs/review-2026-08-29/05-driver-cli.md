# The driver and the command line — review

Agent: 05-driver-cli · Scope: `orchestrator/tofu.py`, `orchestrator/cli.py` · Date: 2026-08-29

## Summary

* `--run-dir <stable path>` — the invocation the README documents — crashes the second deploy
  with a bare `FileExistsError`, after the connected preflight.
* Ctrl-C does not let tofu shut down gracefully: `subprocess.run` SIGKILLs it 0.25 s after the
  SIGINT, which is what D42's comment claims is handled.
* `run.json` is written only when nothing went wrong; every failure path leaves the run
  directory with no record. On a refusal it records the decisions but never the `Problem` that
  caused the refusal.
* `manifest.json` is documented in three places as copied into every run directory. Nothing
  copies it.

## Findings

### F-DRV-01 — `--run-dir` at a stable path fails the second deploy
- **Severity:** S2 · **Confidence:** high · **Location:** `orchestrator/cli.py:72-80`,
  `:202-205`; `README.md:62`
- **What:** `_run_dir` uses `mkdir(parents=True, exist_ok=True)`, so an existing `--run-dir`
  is accepted. `cmd_deploy` then calls `seed.mkdir()` and `workdir.mkdir()` with no
  `exist_ok`, which raises — after `_look()` has connected and printed a clean preflight.
- **Why it matters here:** README's only worked example is `deploy /config.yaml --run-dir
  /runs/lab-a`, a stable path, and deploy is designed to be re-run (D23/D24; A7 calls the
  second deploy "the only case a site will ever see after day one"). Day two of the documented
  workflow ends in a message naming neither `--run-dir` nor a remedy. Two lesser faces: a
  second deploy with nothing to create exits 0 having overwritten the first run's records, and
  `deploy` then `destroy` into one `--run-dir` leaves only the destroy record.
- **Evidence:** `cli.main` driven twice with one `--run-dir` (fake backend, real tofu):
  `error: FileExistsError: [Errno 17] File exists: '.../runs-lab-a/seed'`.
- **Fix / cost:** refuse a non-empty `--run-dir` in `_run_dir`, naming the path — one
  conditional and one string; an empty one is still allowed so a bind-mounted mountpoint
  works.

### F-DRV-02 — Ctrl-C SIGKILLs tofu 0.25 s in; the comment and D42 say otherwise
- **Severity:** S3 · **Confidence:** high · **Location:** `orchestrator/tofu.py:157-164`
- **What:** the comment reads "Not `start_new_session`: Ctrl-C must reach tofu so it shuts
  down the way it would if the operator had run it themselves." The SIGINT does reach tofu,
  but `subprocess.run` then waits `Popen._sigint_wait_secs` = 0.25 s and its bare `except:`
  calls `process.kill()` — a SIGKILL a quarter-second into a graceful shutdown that, mid
  `vol-upload`, cannot have finished.
- **Why it matters here:** under the shutdown the comment describes, tofu finishes the
  in-flight operation and persists state, so a re-run finds a complete base volume and
  proceeds. Under SIGKILL the pool keeps a truncated qcow2 whose header still reports the full
  virtual size, and the next deploy hits D30's `<physical>` mismatch and refuses until someone
  deletes the volume by hand on the hypervisor, at an air-gapped site, over the hop that just
  failed. Tofu's second-Ctrl-C escalation also does not exist: the first is fatal.
- **Evidence:** CPython 3.12.14 `subprocess.py:882` (`_sigint_wait_secs = 0.25`) and `run()`'s
  `except: process.kill()`. Reproduced with a child trapping SIGINT that needs 3 s, invoked as
  `_run` invokes tofu: `parent exited 0.26s after SIGINT`, and the child's post-shutdown write
  never happened.
- **Fix / cost:** in `_run`, use `Popen` and `wait()` inside `try/except KeyboardInterrupt`
  that waits again rather than killing, a second interrupt escalating to `terminate()`. ~8
  lines, no pipes added, so D21 survives. The comment's reasoning is sound; the wrapper breaks
  it.

### F-DRV-03 — `run.json` exists only for runs that did not fail
- **Severity:** S3 · **Confidence:** high · **Location:** `orchestrator/cli.py:173-233`,
  `:254-305`
- **What:** `_record` is reached on exactly five paths — `refused`, `nothing-to-create`, `ok`,
  `nothing-to-destroy`, `cancelled`. A `TofuError` from `init`/`plan`/`apply`/`outputs`, a
  `DestroyError` from `backend.destroy`, a `KeyboardInterrupt`, or anything from
  `prepare`/`render` propagates past it to `main`, which prints one line and returns 1;
  `outcome` has no `failed` value, and `_run_dir` runs before `_look()`, so a refused
  connection leaves an empty dir.
- **Why it matters here:** the run directory is what an air-gapped site ships back for
  support, and `run.json` says what happened. It is present for every run where nothing
  happened and absent for every run where something did. Acceptance defect 3 is the case: an
  apply that failed after writing four volumes left a directory accounted for by one stderr
  line.
- **Evidence:** a deploy whose `tofu.apply` raises: `rc: 1`, `run dir contents: ['seed',
  'tofu']`, `run.json present: False`.
- **Fix / cost:** wrap each verb's body from `_run_dir` onward in `try/except BaseException`,
  record `outcome="failed"` with the exception text and whether the apply had begun, re-raise;
  move `_run_dir` after `_look`. One `try/except/raise` per verb and one `outcome` value.

### F-DRV-04 — `run.json` records decisions but never problems
- **Severity:** S3 · **Confidence:** high · **Location:** `orchestrator/cli.py:94-124`,
  `:183-188`
- **What:** `_record` serialises `decisions` only. `Discovered.problems` — missing pool,
  orphaned volume, base-image size mismatch — reaches stderr through `_report` and nowhere
  else. A deploy refused by a fatal `Problem` records `outcome: "refused"` with every VM
  marked `create` and no reason anywhere.
- **Why it matters here:** the two refusal routes are indistinguishable in the record and the
  informative one vanishes — a `REFUSE` decision at least carries its reason. Advisory
  warnings on a *successful* deploy go the same way.
- **Evidence:** deploy against a backend reporting one fatal problem produced `{"outcome":
  "refused", "decisions": [{"action": "create",...} × 2]}`, while `error: volume 'app01.qcow2'
  exists in pool 'images' but no VM owns it` was on stderr alone.
- **Fix / cost:** add `"problems": [str(p) for p in problems]` to `_record`, passed at both
  deploy call sites — one parameter and one comprehension; `Problem.__str__` already exists.

### F-DRV-05 — nothing bounds a wedged SSH tunnel
- **Severity:** S3 · **Confidence:** medium · **Location:** `orchestrator/tofu.py:35-39`,
  `:162`; `container/entrypoint.py:ssh_config`
- **What:** D42's reasoning against a `plan`/`apply` timeout is correct, but it leaves the
  deploy with no bound of any kind, and the other place one could live is empty: the
  `~/.ssh/config` the entrypoint writes sets five directives and no `ServerAliveInterval`,
  which OpenSSH defaults to 0.
- **Why it matters here:** a tunnel that *resets* surfaces as a provider error and a non-zero
  exit. One that *wedges* — a stateful firewall holding the flow, a path dropping silently —
  produces no error at either end: the deploy waits forever, the one failure an operator
  cannot tell from a slow 4 GB transfer.
- **Evidence:** `ssh_config()` emits `BatchMode`, `IdentityFile`, `IdentitiesOnly`,
  `UserKnownHostsFile` and `StrictHostKeyChecking`, nothing more; `SHORT_TIMEOUT` is applied
  only when `cmd == "init"`. I could not reproduce a wedge.
- **Fix / cost:** `ServerAliveInterval 30` and `ServerAliveCountMax 6` in `ssh_config` — a
  three-minute bound on a dead path, two directives in a file already writing five, skipped
  when the operator mounts their own config. `tofu.py` is untouched and D42 stands; it
  correctly does not bound a wedged libvirtd behind a live sshd.

### F-DRV-06 — the build manifest is never copied into the run directory
- **Severity:** S5 · **Confidence:** high · **Location:** `orchestrator/cli.py:46-53`;
  `README.md:147` and `:181`, `orchestrator/__init__.py:11`, `docs/findings.md:308`
- **What:** `MANIFEST` is read only by `cmd_version`. README's run-directory listing includes
  `manifest.json which build produced this run`, its Licensing section says "The same file is
  copied into every run directory", `orchestrator/__init__.py` lists it as one of five
  consumers of `VERSION`, and R5 asks for it.
- **Why it matters here:** a run directory returned from a site should identify the build that
  produced it, and `run.json` records only `vcows` and the running tofu version — not the git
  SHA, base-image digest or provider lock hash.
- **Evidence:** `grep -rn manifest orchestrator/` matches only `cli.py:46`, `:49` and `:338`;
  a successful deploy's run directory holds `seed/ tofu/ inventory.json run.json`.
- **Also here (S6):** "Exit codes are 0 and 1" (`cli.py:16`, `README.md:75`) is false —
  argparse exits 2 on a usage error and that `SystemExit` bypasses `main`'s handlers.
  `cli.main(['bogus'])` → 2. Matters only to a wrapper branching on the code, but it is stated
  as a contract twice.
- **Fix / cost:** copy `MANIFEST` into `run` inside `_record` when it exists — a `shutil.copy`
  guarded by `MANIFEST.is_file()`. Two sentences for the exit codes; not an `exit_on_error`
  dance to make the original claim true.

### F-DRV-07 — "plan proposes no creates" also fires on an unreadable JSON stream
- **Severity:** S3 · **Confidence:** medium · **Location:** `orchestrator/cli.py:214-217`,
  `orchestrator/tofu.py:101-132`
- **What / why:** `_read_stream` deliberately returns empty changes when `-json-into` is
  missing or unreadable, since the exit code is the authority. But `cmd_deploy` reads
  `planned.changes.get("add")` and treats falsy as proof the plan creates nothing, so an
  unreadable `plan.json` — a full `/runs` mount, a bind-mount permission problem — turns a
  good plan into `plan proposes no creates for 2 VM(s); refusing to apply`, a message pointing
  at the config and at `decide()` when the fault is the run directory. An air-gapped site is
  where a small `/runs` fills up.
- **Evidence:** `tofu.py:110-111` returns `(), {}` on `OSError`. A real plan stream carries
  exactly one `change_summary` — `{'add': 2, 'change': 0,...}` — so the key is present
  whenever the file is readable.
- **Fix / cost:** branch on `planned.changes` being empty versus `add == 0`, naming the stream
  path in the first case. One branch and one string.

### F-DRV-08 — an internal error leaves no way to get a traceback
- **Severity:** S4 · **Confidence:** high · **Location:** `orchestrator/cli.py:393-399`
- **What:** the catch-all is justified — §3 rules out a shared backend hierarchy — but it
  discards the traceback with no flag or variable to recover it.
- **Why it matters here:** for a backend exception the one-line form is right;
  `DestroyError.__str__` carries the detail. For a genuine bug (`KeyError`, `AttributeError`,
  `JSONDecodeError` out of `tofu.outputs`) the operator gets `error: KeyError: 'vms'` with no
  file and no line, and at an air-gapped site there is no second attempt with a debugger.
  `cmd_version` is adjacent: it catches `(TofuError, OSError)`, so a `TimeoutExpired` or a
  `KeyError` from a reshaped manifest lands here instead of on its "tofu: unavailable" path.
- **Evidence / fix / cost:** the handler is `print(f"error: {type(exc).__name__}: {exc}")`.
  Call `traceback.print_exc()` when `VCOWS_TRACEBACK` is set — one `os.environ.get` and one
  import.

## Checked and sound

* **The `-json-into` parse.** Absent file → `OSError` → empty; truncated final line →
  `JSONDecodeError` → skipped; the exit code stays the authority. "Enormous" is not a real
  case: a two-resource plan stream is 1.4 KB over five lines and `apply` adds an
  `apply_progress` line per resource per ten seconds, so a six-hour upload is a few hundred
  KB.
* **`SHORT_TIMEOUT` placement** — `init`, `output`, `version` get 120 s; `plan` and `apply`
  none, matching D42 exactly. Every path is resolved before `-chdir` for the stated reason,
  `-no-color` is applied on a non-tty, and `TF_CLI_CONFIG_FILE` is passed through rather than
  invented, so the air-gap mirror reaches the child.
* **Run-directory naming cannot escape.** `DEPLOYMENT_PATTERN` is
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$` and the filename-stem default (`config.py:130`) is
  applied *before* validation, so `../evil` is rejected. Mode 0700 is set with `os.chmod`
  rather than `mkdir`'s mode, so umask cannot widen it, and two runs in the same second
  collide without corrupting — the second raises at `seed.mkdir()`.
* **`main`'s exception ordering** — `KeyboardInterrupt` is caught before and separately from
  `except Exception`. `_confirm` refuses on a non-tty naming `--yes` and requires the literal
  `yes`. `module_dir` reads the tofu directory off the backend's own module rather than adding
  an eighth ABC method.

## Not checked

* Anything needing a live hypervisor: no `deploy`, `destroy` or connection was run against the
  rig, per the brief.
* `preflight.py`'s message wording — F-DRV-02 assumes D30's mismatch message names the volume;
  I read that it does but did not audit it. Also `cmd_destroy` discarding `destroy()`'s
  `Outcome` (reported elsewhere) and `container/entrypoint.py` beyond F-DRV-05's directives.

## Deserves its own agent

* **The run directory as a delivered artifact.** F-DRV-01, -03, -04 and -06 are one shape: it
  is documented as the record a site returns for support, and four things it is promised to
  contain are missing or overwritten.
* **SSH transport robustness end to end.** The generated `~/.ssh/config` is the one point
  where both clients' behaviour is set, and no test exercises it on anything but a healthy
  path.
