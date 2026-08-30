# Severity.WARNING end to end, and the exception chain — review

Agent: 16-warning-severity · Scope: `orchestrator/cli.py`, `config.py`,
`backends/base.py`, `backends/libvirt/{preflight,destroy,schema}.py` · Date: 2026-08-29

## Summary

* **Seven `Severity.WARNING` sites; two can never reach the operator, both in
  `destroy.py`.** `Outcome` is built, filled, and dropped — `destroy()` returns
  `None`, `cli.py:294` ignores it, and `DestroyError` filters to `p.fatal`.
* **The same discarded `Outcome` carries `skipped`: the disks that would not
  resolve.** A destroy against an inactive pool leaks every per-VM volume, prints
  nothing, writes `"outcome": "ok"`, exits 0. Reproduced end to end.
* **14 filed `destroy.py:244` S3 for want of a trigger. The confirmation prompt is
  the trigger.** Unbounded `input()`, libvirt-assigned domain UUIDs, deterministic
  disk paths: a destroy confirmed after a redeploy deletes the *new* running VM's
  overlay and seed ISO. Reproduced.
* **A failed or interrupted `destroy` leaves an empty run directory**, not even
  `run.json`. Config-level WARNINGs are computed by `load()` on every verb, shown by one.

| # | site | reaches the operator on |
|---|---|---|
| W1 | `base.py:228` marked VM not in this config | preflight ✓ deploy ✓ destroy n/a |
| W2 | `schema.py:409` image unreadable, `disk_gb` unchecked | validate ✓ · **others ✗** |
| W3-W5 | `preflight.py:215` pool refresh failed · `:284` image unreadable, host copy unverified · `:295` volume reports no physical size | preflight ✓ deploy ✓ destroy ✓ |
| W6 | `destroy.py:147` daemon rejected undefine flags, retrying | **never** |
| W7 | `destroy.py:225` could not refresh pool; disks may not resolve | **never** |

## Findings

### F-WARN-01 — destroy discards its own `Outcome`; leaked disks report as success
- **Severity:** S1 · **Confidence:** high
- **Location:** `backends/libvirt/destroy.py:233-258`, `orchestrator/cli.py:294-304`
- **What:** `destroy()` returns `None`, so `Outcome.destroyed`, `Outcome.skipped` and
  every non-fatal `Problem` are unreachable after the call. `DestroyError` joins only
  `p.fatal`, so W6 and W7 are dropped on the failure path too. `_delete_volume` puts
  an unresolvable path in `out.skipped` and returns; nothing prints it. `cli.py:304`
  prints `destroyed {len(targets)} VM(s)` from the *target list*, and `_record`'s
  `extra={"destroyed": …}` is built the same way — intent, never result.
- **Why it matters here:** the trigger needs no race. A hypervisor rebooted with the
  pool's `autostart` off leaves domains defined and the pool inactive; `_refresh_pools`
  skips inactive pools with a bare `continue` (`:218`), so no path resolves, every
  overlay and seed ISO stays on disk, and the operator is told the teardown succeeded.
  The next deploy of those names hits preflight's orphan-volume refusal
  (`preflight.py:143`) with no record of where the files came from. This is the defect
  the file's own docstring claims to prevent: "Silent partial success is the specific
  defect findings.md §1 rejects `tofu destroy` for."
- **Evidence:** `cli.main(["destroy", cfg, "--yes"])` against `tests/fake_libvirt`
  with the configured pool inactive (scripts in the session scratchpad):

  ```
    error [target.libvirt.pool]: storage pool 'images' exists but is not active.
  destroyed 1 VM(s)
  ---- exit code: 0
  leaked volumes still on the host: ['app01-seed.iso', 'app01.qcow2', 'golden.qcow2']
  run.json: ['app01'] ok
  ```

  Direct call with a second pool inactive, both streams captured:
  `stdout from destroy(): ''`, `stderr from destroy(): ''`,
  `volume still present on the host: True`.
- **Fix:** return the `Outcome` from `destroy()`, have `cmd_destroy` print `skipped`
  and every non-fatal `Problem`, and pass both into `_record`. A non-empty `skipped`
  should not be exit 0.
- **Cost of the fix:** one changed return type on `Backend.destroy`, ~8 lines in
  `cmd_destroy`, two `run.json` keys. Less surface than the docstring already promises.

### F-WARN-02 — the confirmation pause is the missing trigger for 14's F-DSK-01
- **Severity:** S1 · **Confidence:** high on the mechanism; medium that an operator
  enters the window
- **Location:** `cli.py:261-294` (session and `targets` span `_confirm` at `:308-324`),
  acting through `destroy.py:243-255`
- **What:** `cmd_destroy` collects `targets` — names, UUIDs, disk paths — from one
  `preflight` walk, blocks on `input()` with no timeout, then destroys from that
  snapshot. **Nothing re-validates.** `destroy()` re-reads only storage
  (`_refresh_pools`); no marker is re-read, no domain re-identified, and
  `_delete_volume` deletes by path without checking the path still belongs to the
  domain it just undefined. Three facts combine: the domain UUID is assigned by
  libvirt at define time (`main.tf:84-96` sets none; `outputs.tf:11-14` says so),
  overlay and seed names are deterministic from the logical name (`render.py:42-47`),
  and `destroy.py:244` is a bare `except libvirt.libvirtError` falling through to the
  disk loop. So if the deployment is destroyed and redeployed during the pause, the
  stale UUID misses, the "already gone" branch is taken, and the *new* VM's disks are
  deleted underneath it — `vol.delete` has no in-use protection, as that file states.
- **Why it matters here:** this is the case 14 could not find a trigger for. It needs
  no unusual configuration and no microsecond race — a prompt left open while a
  colleague redeploys the same lab. A running guest loses its root overlay and its
  seed ISO; the tool prints `destroyed 1 VM(s)`, exit 0.
- **Evidence:** stale target (uuid `1111…`), host redeployed (same name, uuid `2222…`,
  same overlay path): `new domain still running: True`, `new domain still defined:
  True`, `its disks, deleted: ['app01.qcow2', 'app01-seed.iso']`.
- **Fix:** bound the staleness rather than re-architect it — after `_confirm` returns
  true, re-read each target's `XMLDesc` and drop any whose UUID no longer resolves or
  whose marker no longer matches. One loop over `targets` reusing `marker_of` and
  `disks_of`. Narrowing `:244` to `ERR_NO_DOMAIN` (14's fix) removes the fall-through
  but not the staleness; both are wanted, and the recheck also catches a
  renamed-and-remarked domain.
- **Cost of the fix:** ~12 lines in `destroy.py`, one extra `XMLDesc` per target. It
  shortens the window to the length of the teardown itself, which is findings.md §2's
  accepted TOCTOU rather than an unbounded one.

### F-WARN-03 — a failed or interrupted destroy writes nothing at all
- **Severity:** S2 · **Confidence:** high
- **Location:** `cli.py:258` (`_run_dir`), `:296` (`_record`), `:382-399`
- **What:** any exception between `_run_dir` and `_record` skips the record. For
  `deploy`, 13 showed `seed/` and `tofu/` survive. For `destroy` there is nothing else
  to hold: the directory is created, the teardown partially runs, and it is left **empty**.
- **Why it matters here:** a partial teardown is the run where the record matters most
  — some domains undefined, some disks deleted — and `DestroyError`'s message
  enumerates only fatal problems, never `out.destroyed`. Nothing on disk and nothing on
  stdout says which of twenty objects went. Every exception type reaching `main()`:

  | arrives at `main()` | printed | rc | run dir |
  |---|---|---|---|
  | `ConfigError` | every problem | 1 | not created (`load` precedes `_run_dir`) |
  | `DestroyError` | `error: DestroyError: <fatal only>` | 1 | **empty** |
  | `KeyboardInterrupt` (at the prompt or mid-teardown) | `interrupted` | 1 | **empty** |
  | `libvirtError` (transport drop across the pause; `getLibVersion` at `:238`) | `error: libvirtError: …` | 1 | **empty** |
  | `TofuError` / `TimeoutExpired` / `OSError` | `error: <Type>: <msg>` | 1 | deploy only: `seed/`+`tofu/`, no `run.json` |
  | argparse failure | usage | **2** | not created |
- **Evidence:** three injected failures through `cli.main(["destroy", …, "--yes"])`:
  `rc=1 run dir contents=['<empty>']` for `DestroyError`, `KeyboardInterrupt` and
  `libvirtError` alike.
- **Fix / cost:** 13's F-RUNDIR-01 fix (`try/except BaseException` writing
  `outcome: "failed"` and re-raising) covers this cell, provided it wraps `cmd_destroy`
  and not only `cmd_deploy`. Pairing it with F-WARN-01 — recording the `Outcome` rather
  than the target list — is what makes the record worth having. No extra cost.

### F-WARN-04 — `config.load` computes config warnings on every verb and drops them
- **Severity:** S3 · **Confidence:** high
- **Location:** `config.py:132-135`, `cli.py:132-136`
- **What:** `load()` calls `validate()`, raises on `any(p.fatal)`, returns the dict —
  non-fatal problems have no channel out. `cmd_validate` recovers them only by calling
  `validate()` a second time. The three connected verbs never see them. Today that
  hides W2: an unreadable `source_qcow2` means `_check_disk_capacity` silently does not
  run, so a `disk_gb` below the image's virtual size is not caught offline.
- **Why it matters here:** the golden image is bind-mounted at run time, so an operator
  who mistypes the `-v` target gets no warning from `deploy` that the capacity check was
  skipped. On a *first* deploy preflight's W4 does not fire either (`base_volume`
  returns early when the volume is absent), so nothing mentions the image until the
  provider fails on the upload. Loud, but the message written to explain it is never shown.
- **Evidence:** `C.validate(CONFIG, REGISTRY)` returns W2; `cli.main(["validate", p])`
  prints it; the same config through `destroy` prints only preflight's problems.
- **Fix / cost:** have `load` return the non-fatal problems alongside the dict and feed
  them into the three verbs' existing stderr loop — one changed return type, three call
  sites. Justified because a whole severity level otherwise has one visible consumer.

### F-WARN-05 — three docstrings describe reporting that does not happen
- **Severity:** S5 · **Confidence:** high
- **Location:** `destroy.py:24-26`, `:176-179`, `:203-214`
- **What:** (a) "Every object's outcome is reported, and any failure is fatal." Only
  fatal problems are reported, and only when something fails. (b) "A path that will not
  resolve is **reported** and skipped." It is skipped; nothing reports it. (c)
  `_refresh_pools`: "Skipping it would turn 'report and skip what does not resolve' into
  'silently leak every overlay'." The leak is silent either way — the refresh changes
  how often it happens, not whether anyone is told. This is the reasoning D35 and
  findings.md §1 rest on; anyone reading it concludes the accounting exists.
- **Evidence:** quoted above; `grep -n "out.skipped\|out.destroyed" orchestrator/`
  returns only writes, no reads outside `Outcome`.
- **Fix / cost:** fixing F-WARN-01 makes all three true, at no extra cost. If it is
  deferred, the three sentences must be corrected rather than left.

### F-WARN-06 — "Exit codes are 0 and 1" is contradicted by argparse
- **Severity:** S6 · **Confidence:** high
- **Location:** `cli.py:16`
- **What / why:** an unknown verb, missing `config` argument or bad flag exits 2 —
  `parse_args` raises `SystemExit`, which `except Exception` does not catch. A script
  treating "not 0 or 1" as an internal error mis-reports a typo. Named only because
  the docstring states it as a contract.
- **Evidence:** `cli.main(["bogus"])` → `SystemExit(2)`.
- **Fix / cost:** one docstring line.

## Checked and sound

* **W1's path is correct and matches README:17.** It reaches stderr on `preflight` and
  `deploy` through `_report`. `destroy` not calling `decide()` is right, not an
  omission: D36 filters on `marker.deployment`, so a VM dropped from the config is
  still a destroy target and a deploy-time warning would be wrong there.
* **W3/W4/W5 reach all three connected verbs.** `cmd_destroy:274` prints
  `discovered.problems` before acting, so preflight's warnings survive the
  ERROR-advisory asymmetry. Verified in the end-to-end run above.
* **D33 does not assume a WARNING is seen.** `address_conflicts` records a decision
  *not* to warn about a static inside a DHCP range; everything it emits is `ERROR`.
* **D36's skip report is seen.** `cmd_destroy:278-281` prints other-deployment skips to
  stdout unconditionally, before the prompt.
* **`Problem.fatal` and the two-value `Severity` are consistent** at every consumer; no
  path treats a WARNING as fatal or an ERROR as advisory by accident.
* **`main()`'s handler order is correct.** `KeyboardInterrupt` is not an `Exception`, so
  its clause cannot be shadowed; `ConfigError` prints every problem, not the join.

## Not checked

* The rig. Every reproduction is against `tests/fake_libvirt`, which models the pool
  cache and the error codes the code matches on. No live hypervisor was touched.
* `tofu.py`'s `Result.diagnostics` carries OpenTofu's own `severity == "warning"`
  entries that nothing prints either. Adjacent to my scope, plausibly 05's; not filed.

## Deserves its own agent

* **`Outcome`, `Discovered.problems`, `Result.diagnostics` and `ConfigError.problems`
  are four independently-invented result carriers, and three lose data at their
  consumer.** Whether that is one defect or four needs deciding — §5's anti-sprawl rule
  argues against unifying them, which makes the answer less obvious than it looks.
* **A "what does the operator actually see" pass over all five verbs.** `cmd_destroy`
  prints from three sources in three formats, `cmd_deploy` from two, and nothing
  defines the output contract.
