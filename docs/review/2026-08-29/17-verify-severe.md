# S1/S2 verification — review

Agent: 17-verify-severe · Scope: every S1/S2 claim in 01–16 and 18 · Date: 2026-08-29

23 claims (6 filed S1, 17 filed S2). **CONFIRMED 21 · REFUTED 0 · NEEDS-EVIDENCE 2**;
six confirmed as mechanisms at a lower severity than filed. Duplicate claims across files
are merged. Commands ran from `/home/ssullivan/vcows-deploy`; scripts and the scratch
repo are under `…/scratchpad/`.

---

## The `destroy.py:244` chain — adjudicated

**16's F-WARN-02 trigger is real, reachable, and reproduced. But 16's framing of it is
wrong, and that changes the fix.**

`Existing.id` is `dom.UUIDString()` (`preflight.py:135`), not the marker id;
`main.tf:84-96` sets no domain uuid and `outputs.tf:11-14` says so explicitly. Overlay
and seed names are `f"{name}.qcow2"` / `f"{name}-seed.iso"` (`render.py:42-47`).
`cmd_destroy` computes `targets` at `cli.py:262-268`, blocks on an unbounded `input()`
at `cli.py:321`, and calls `backend.destroy(cfg, session, targets)` from that snapshot
with no re-read of any marker or UUID. Reproduced end to end:

```
$ .venv/bin/python …/scratchpad/r_warn02.py
destroy() returned normally
new domain still active: True
new domain touched: []
volumes deleted: ['app01-seed.iso', 'app01.qcow2']
```

The correction: **the stale UUID misses with code 42 — the "already gone" branch is
*correct* here.** So this is not a trigger for the bare `except libvirt.libvirtError`
at `:244` at all, and 14's fix (narrow to `ERR_NO_DOMAIN`) does not remove it. 14's fix
also cannot be widened to skip disks on 42 without breaking
`tests/test_libvirt_destroy.py:130` (`test_a_domain_already_gone_still_has_its_disks_collected`),
which pins exactly that deletion.

Verdict, in three parts:
1. **`:244`'s bare catch (08 F-SILENT-01, 14 F-DSK-01) is S3.** 14 is right: the
   realistic non-42 codes kill the storage driver too and the run is loud. The residual
   is polkit `ACCESS_DENIED` scoped to the domain driver. Fix it anyway — one constant,
   one `if`.
2. **Stale targets across the confirm pause is a separate defect, and it is S1** by the
   brief's definition: silent data loss under a running guest, `destroyed 1 VM(s)`, exit
   0. High confidence on mechanism, medium on frequency — it needs a concurrent
   destroy+redeploy. No test touches `_confirm` (`grep -rn "_confirm\|input(" tests/` →
   nothing).
3. **The two fixes are independent.** 16's "the missing trigger for 14's F-DSK-01" would
   make the orchestrator ship one fix and believe both are closed.

---

## Verdicts, by source file

### 02 F-LVC-01 (S2) / 08 F-SILENT-02 (S2) / 16 F-WARN-01 (S1) — one defect
**All three CONFIRMED; F-WARN-01's severity is S2, not S1.** `destroy()` is `-> None`;
`cli.py:294` calls it as a statement and `:296-304` records
`sorted(e.name for e in targets)` — intent, not result. `_refresh_pools` `continue`s on
`not pool.isActive()` (`:218`) and `_delete_volume` files `NO_STORAGE_VOL` under
`out.skipped`, whose comment claims a refresh ran. With the pool inactive the domain is
destroyed and undefined (marker gone) and both volumes survive, silently. Not S1 because
the next deploy of those names hits the orphan refusal and stops loudly — one run later,
at the wrong verb. Fix this first: six findings reduce to this file's return type plus
F-WARN-02's recheck loop.
`$ .venv/bin/python …/scratchpad/r_warn01.py` → `returned normally; stdout='' stderr=''` /
`pool refreshed: 0 deleted: []` / `domain log: ['destroy', 'undefine:55']` /
`volumes still on disk: ['app01-seed.iso', 'app01.qcow2']`

### 03 · F-LVOFF-01 — a password in the URI validates and is written to disk — S2
**CONFIRMED, with 13's narrowing.** The password validates and reaches
`main.auto.tfvars.json` verbatim; `variable "uri"` is not `sensitive`. The state claim
is **not** part of the verified finding — 13 showed `var.uri` reaches only the provider
block, which OpenTofu does not persist.
`$ .venv/bin/python …/scratchpad/r_offline.py` → `_check_target: []`,
`connection_uri: qemu+sshcmd://user:hunter2@vhost/system`; render →
`tfvars uri: qemu+sshcmd://vcows:sup3rs3cret@vcows/system`.

### 03 F-LVOFF-02 / 15 F-XDEP-03 — derived MAC ignores `deployment` — S2
**CONFIRMED (derivation).** `derive_mac('app01',0) = 52:54:00:ee:77:63` and
`Marker.for_vm('app01','lab-a').id == …('lab-b').id` byte for byte; `address_conflicts`
builds `by_mac` from `_domains(conn)`, this host only (`preflight.py:128-142`). The
collision needs two hosts bridged to one L2 with the same VM name in two deployments;
`network:` (the README example) is immune. Same command as above.

### 04 · F-TOFU-01 — `firmware = "efi"` emitted beside a pinned loader — S2
**NEEDS-EVIDENCE.** The co-emission is a code fact (`main.tf:108-131`; the ternary is
unconditional). The harm is not: `docs/archive/acceptance.md:137-139` records that on libvirt
12.0.0 the pin was honoured and the pinned-loader VM booted, and nothing establishes what
8.0.0/11.10.0 does. Settle it as 04 says — define the domain on a Rocky 9 host and diff
`virsh dumpxml`. Do not ship a fix on this alone.
`no command — reasoning only` (read `main.tf:108-131`, `docs/archive/acceptance.md:130-140`).

### 05 · F-DRV-01 — `--run-dir` at a stable path fails the second deploy — S2
**CONFIRMED.** `_run_dir` is `exist_ok=True`; `seed.mkdir()` / `workdir.mkdir()`
(`cli.py:203`, `:205`) are not. It fails *after* `_look()` connected and printed clean
decisions, and the message names neither `--run-dir` nor a remedy.
`$ .venv/bin/python -m pytest -q -s -p no:cacheprovider …/scratchpad/test_verify.py` →
`error: FileExistsError: [Errno 17] File exists: '…/shared/seed'`, rc 1.

### 08 · F-SILENT-01 — non-`NO_DOMAIN` lookup failure deletes a live VM's disks — S1
**NEEDS-EVIDENCE at S1; mechanism CONFIRMED, correct severity S3.** Every injected code
takes the "already gone" branch and deletes both volumes with the domain still running.
But 08 states its own trigger was not seen, and 14 showed the realistic codes (6/38/1)
kill the storage driver too and raise `DestroyError` with `deleted=[]`. The S1 *outcome*
is reachable by the pause route, not this one.
`$ .venv/bin/python …/scratchpad/r_silent01.py` → codes 42/45/6/88/38 all
`raised=- dom_active=True dom_log=[] deleted=['app01-seed.iso','app01.qcow2']`.

### 11 · F-LIFE-01 — a leaked volume returns as "a previous create was interrupted" — S1
**CONFIRMED as a chain; severity S2, not S1.** Both halves reproduce: the silent leak
(F-LVC-01's run) and the misattributing refusal. The refusal is a loud fatal on a later
run — S2's shape, not S1's.
`$ .venv/bin/python -c "from orchestrator.backends.libvirt.preflight import orphan_volumes; …"` →
`error [app01]: volume 'app01.qcow2' exists but no domain references it. A previous create was interrupted; delete it on the hypervisor and re-run.` (`fatal = True`)

### 02 F-LVC-02 / 11 F-LIFE-02 / 15 F-XDEP-01 — D30's remedy destroys the backing file
**CONFIRMED, severity S2 — 15's S1 is not earned.** The message text, the reachability
(a new golden image is a different size, so `physical != local` fires every time), and
the non-destructive alternative all hold: `base_volume` keys on `base_volume_name` and
returns `create: True` when the name is absent (`preflight.py:264-266`), so a new name
uploads alongside. But the tool refuses loudly and changes nothing — the destruction
needs a human to act on the instruction. 15's added value over 11 and 02 (the working
alternative, and that `walk()` already holds the `<backingStore>` data) is real.
`$ sed -n '255,312p' orchestrator/backends/libvirt/preflight.py`

### 12 · F-TEETH-01 (S1), F-TEETH-02 (S2), F-TEETH-03 (S2) — mutations survive
**All three CONFIRMED.** F-TEETH-01's severity is S2, not S1: no current defect is
silent, so it is a gap admitting an S1. Six mutations in the scratch repo, each reverted,
each `218 passed, 25 skipped` — the whole suite, gates included:
`$ cd …/scratchpad/repo && sed -i '<mutation>' <file> && /home/ssullivan/vcows-deploy/.venv/bin/python -m pytest -q -p no:cacheprovider`
1. `main.tf`: drop `metadata = { xml = each.value.marker_xml }` (destroys identity).
2. `main.tf`: seed `type = "iso"` → `"raw"` — acceptance defect 3, verbatim.
3. `main.tf`: delete the whole `features = { acpi … }` block — acceptance defect 4.
4. `preflight.py:61`: `connection_uri(…)` → `connection_uri(…, "sshcmd")` — defect 1.
5. `entrypoint.py:79`: `StrictHostKeyChecking yes` → `no`.

### 13 · F-RUNDIR-01 — the failed apply is the one outcome with no record — S2
**CONFIRMED.** `_record` sits after `tofu.outputs`, so any `TofuError` reaches
`main`'s `except Exception` unrecorded.
`$ .venv/bin/python -m pytest -q -s -p no:cacheprovider …/scratchpad/test_v2.py` →
`rc: 1 contents: ['seed', 'tofu']` / `run.json present: False` / `inventory.json present: False`.

### 13 · F-RUNDIR-03 — reusing a run directory: deploy fails, destroy rewrites — S2
**CONFIRMED.** Deploy half is F-DRV-01's run. Destroy half follows from `cmd_destroy`
creating no subdirectories and `_record`'s unconditional `path.write_text` (`cli.py:84`).

### 15 · F-XDEP-02 — the orphan refusal names another deployment's disk — S2
**CONFIRMED.** `orphan_volumes` takes no deployment name, sets `where=vm["name"]`, asserts
one cause in fixed text. Same command as F-LIFE-01.

### 16 · F-WARN-02 — the confirmation pause is the missing trigger — S1
**CONFIRMED as a defect at S1; its stated relationship to 14's F-DSK-01 is REFUTED.**
Adjudicated above; the trigger fires on `ERR_NO_DOMAIN`, so 14's fix does not close it.
`$ .venv/bin/python …/scratchpad/r_warn02.py`

### 16 · F-WARN-03 — a failed or interrupted destroy writes nothing at all — S2
**CONFIRMED.** The run directory is created, the teardown partially runs, and it is left
empty. `$ .venv/bin/python -m pytest -q -s -p no:cacheprovider …/scratchpad/test_verify.py`
→ `destroy rc: 1 run dir contents: ['<empty>']`.

### 18 · F-SEC-01 — `ssh_keyfile`/`known_hosts` inject OpenSSH directives — S1
**CONFIRMED, including the first-value precedence claim, against real OpenSSH.** Neither
field carries a pattern (`schema.py:103-104`) and `_check_target` never reads them.
`ssh_config()` emits the injected `ProxyCommand`; OpenSSH 9.9p1 then resolves the injected
`no` over the tool's own `yes` two lines later — bypassing the refusal `schema.py:222`
calls the most important check the file makes. `install()` runs on `validate` too and
returns early if `~/.ssh/config` exists, so one poisoned file survives later clean runs.
`$ .venv/bin/python -c "…; import entrypoint; print(entrypoint.ssh_config('/k/id\n  ProxyCommand /bin/sh -c \'id>/tmp/pwned\'', '/kh\n  StrictHostKeyChecking no'))"`
reproduced the emitted file; then, decisively, feeding that file to real ssh:
`$ ssh -G -F …/scratchpad/ssh/cfg vhost | grep -iE "stricthostkeychecking|proxycommand"`
→ `stricthostkeychecking false` / `proxycommand /bin/sh -c 'id>/tmp/pwned'`

### 18 · F-SEC-02 — `source_qcow2` validated as a path, consumed as a URL — S2
**CONFIRMED offline, independently.** An unreadable `source_qcow2` degrades to a WARNING
(`schema.py:404-415`), and when the base volume is absent from the host `base_volume`
returns `create: True` with no stat at all (`preflight.py:264-266`) — nothing is fatal on
either pass. The value reaches `create = { content = { url = … } }` (`main.tf:30`), which
the pinned schema calls "URL to download content from" / "Upload content from a URL or
local file". The fetch itself was not executed.
`$ .venv/bin/python -c "…load(cfg with source_qcow2: file:///run/secrets/id_ed25519)…"` →
`LOADED OK (validate did not refuse)`, one `warning [image.source_qcow2]`, and
`tfvars base_volume: {…, 'source': 'file:///run/secrets/id_ed25519'}`

---

## For phase D — verify these personally

* **F-TOFU-01** is the only claim I could not settle either way; its fix is one
  expression in the file that produced three of five acceptance defects. It needs a
  RHEL/Rocky 9 host, not more reading.
* **F-WARN-02's severity.** Mechanism and reachability are confirmed. Whether a
  single-user tool warrants S1 for a two-operator race is your judgement, not the code's.
* **F-SEC-02's second half.** Whether the provider resolves `file://` / `http://` in
  `create.content.url` needs one execution against the rig; I did not do it.

## Not checked

Every S3–S6 finding, by scope. The destroy half of F-RUNDIR-03 (read, not executed).
Nothing ran against `qemu+ssh://vcows@vcows/system`; no tracked file was modified.
