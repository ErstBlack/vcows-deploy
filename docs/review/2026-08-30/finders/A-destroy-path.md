# Dimension A — destroy-path regression

Agent: A-destroy-path · Scope: `orchestrator/backends/libvirt/destroy.py` (full),
`orchestrator/backends/libvirt/preflight.py` (destructive halves),
`orchestrator/cli.py:_destroy` / `_guard` / `_record`, `tests/test_libvirt_destroy.py`,
`tests/fake_libvirt.py` · Range reviewed: `da3f45c..HEAD` · Date: 2026-08-30

## Summary

The rewrite landed. `destroy.py` grew from 258 to 551 lines and every structural
demand the 2026-08-29 arbitration chain made is present in some form:

* `ERR_NO_DOMAIN = 42` exists in `errors.py:27` and `destroy.py:518` matches it
  numerically. F-DSK-01's bare `except libvirt.libvirtError:` is gone.
* `_reverify` (`destroy.py:404`) re-reads `XMLDesc` after `_confirm` and refuses a
  target whose marker changed. The disks acted on are the live document's, not
  preflight's snapshot.
* `_deletable` (`destroy.py:437`) is the S6 allowlist: two guards, `claimed` and a
  basename check against `{overlay_name(marker.name), seed_name(marker.name)}`.
* `_claimed_elsewhere` (`destroy.py:354`) builds the cross-domain guard and is
  fatal when the host cannot be asked.
* `_pool_holds` / `_refresh_pools` make an inactive pool holding a target's disk a
  fatal `Problem` rather than the old bare `continue`.
* `fake_libvirt.storageVolLookupByPath` now keys on the whole path
  (`tests/fake_libvirt.py:235`), closing F-DSK-02.
* `Backend.destroy` returns `Outcome`, `cmd_destroy` prints `skipped` and every
  `Problem`, and a non-empty `skipped` is a non-zero exit (`cli.py:496-520`).

Two things survive. One is a residual data-loss race that the redesign narrowed but
did not close (RW-A1); the other is a hole in the destroy record on exactly the run
that has the most to record (RW-A2).

---

## Q1 — can anything still reach `_delete_volume` for a volume that is not ours?

### The 08 → 14 → 16 trigger is closed in its primary shape

Reproduced against `tests/fake_libvirt` at HEAD. Setup in all three cases: preflight
snapshot `Existing(name="app01", id="OLD-UUID", marker=lab-a/app01,
disks=("/pool/app01.qcow2", "/pool/app01-seed.iso"))`, the operator pauses at
`input()`, the world changes, the operator then types `yes`.

**A — destroyed and redeployed, new domain defined and readable.** The new domain's
UUID is not in `ours`, so `_claimed_elsewhere` puts both paths into `claimed` and
`_deletable` refuses both:

```
DestroyError
error [app01]: /pool/app01.qcow2 is claimed by another domain on this host; leaving it
error [app01]: /pool/app01-seed.iso is claimed by another domain on this host; leaving it
  skipped: app01
  skipped: /pool/app01.qcow2
  skipped: /pool/app01-seed.iso
deleted: []
```

That is the exact chain agents 08/14/16 arbitrated, and it now fails closed, loudly,
and non-zero. Confirmed closed.

**B — volumes recreated, new domain not yet defined.** Same snapshot, but the world
is a pool holding freshly created `app01.qcow2` and `app01-seed.iso` and *no* domain
— the state a concurrent `tofu apply` occupies between `libvirt_volume` and
`libvirt_domain`:

```
returned destroyed= ['/pool/app01.qcow2', '/pool/app01-seed.iso'] skipped= ['app01'] problems= []
deleted: ['app01.qcow2', 'app01-seed.iso']
```

Both new files are deleted, **no `Problem` of any severity is raised**, and they are
reported as `destroyed`. This is RW-A1.

**C — redeployed, new domain present but unreadable.** `XMLDesc` on the foreign
domain raises; `_claimed_elsewhere` degrades to a `WARNING` and the live
deployment's disks are deleted anyway:

```
warning [storage]: domain 'app01' could not be read (internal error), so any disk it
claims was not checked against the ones being deleted.
deleted: ['app01.qcow2', 'app01-seed.iso']
```

C is documented in the code (`destroy.py:385-390`), argued for out loud, and pinned
by `test_a_domain_whose_disks_could_not_be_read_narrows_the_guard_out_loud`. It is a
recorded severity decision, not a defect, and I am not filing it. I record it here
because it is the same residual shape as RW-A1 and the two are worth reading
together: the guard that replaced the old snapshot-trust is `claimed`, and `claimed`
is only as good as the host's willingness to describe its domains.

### RW-A1 — a vanished target's recorded disks are deleted on name-pattern evidence alone

**Severity:** medium
**Location:** `orchestrator/backends/libvirt/destroy.py:530-547`, decided by
`_deletable` at `:437-473`

`destroy.py:514-547`:

```python
    for target in targets:
        try:
            dom = session.lookupByUUIDString(target.id)
        except libvirt.libvirtError as exc:
            if exc.get_error_code() != ERR_NO_DOMAIN:
                ...
                continue
            # Already gone. Its disks may not be, so they are still resolved
            # below -- from the preflight snapshot, which is why `_deletable`
            # rather than the snapshot decides what may go.
            out.skipped.append(target.name)
        else:
            fresh = _reverify(dom, target, out)
            ...
        for path in target.disks:
            if _deletable(path, target, claimed, out):
                _delete_volume(session, path, target.name, out)
```

The `else` branch has live evidence — `_reverify` re-read the domain and confirmed
the marker. The `except ERR_NO_DOMAIN` branch has none: the domain is gone, so there
is nothing left to re-read, and `target.disks` is the preflight snapshot from before
the unbounded `input()` pause. What decides deletion in that branch is `_deletable`,
whose two guards are:

1. `path in claimed` — only populated by domains that currently exist and can be
   read. A volume created seconds ago by an apply that has not yet defined its
   domain is claimed by nobody.
2. `PurePosixPath(path).name in {f"{marker.name}.qcow2", f"{marker.name}-seed.iso"}`
   — a *name pattern*, which a freshly created volume for the same logical name
   matches by construction.

So in the vanished-domain branch, "this path is safe to delete" reduces to "no live
domain names it, and it is spelled the way this deployment spells its volumes". A
new volume at the old path satisfies both.

Scenario B above is the concrete trigger, and it needs three actors and a window of
seconds to minutes:

1. Operator A runs `vcows destroy lab-a.yaml`. Preflight records app01's UUID and
   its two disk paths. A is at the `type 'yes'` prompt.
2. Operator B runs `vcows destroy lab-a.yaml --yes` to completion. app01 and both
   volumes are gone.
3. Operator B runs `vcows deploy lab-a.yaml`. `tofu apply` creates
   `/pool/app01.qcow2` and `/pool/app01-seed.iso` and is partway to defining the
   domain.
4. Operator A types `yes`. `lookupByUUIDString(OLD-UUID)` raises `NO_DOMAIN`;
   `claimed` is empty because B's domain does not exist yet; both basenames match;
   both of B's new volumes are unlinked.

B's apply then defines a domain whose overlay and seed ISO do not exist, or dies on
a raw provider error. A's terminal says `destroyed 2 object(s)` and — because
`out.skipped` holds the domain name — `1 object(s) were not removed by this run`.
Nothing in A's output or run record says a volume was deleted without ownership
evidence.

**Why this is a deviation, not just a residual race.** The remediation checklist is
explicit at `docs/review/2026-08-29/2026-08-29-remediation-checklist.md:32-33`:

> **2.2** After `_confirm` returns true, re-read each target's `XMLDesc`; drop any
> whose UUID no longer resolves or whose marker no longer matches.
>
> **2.2** Add a regression test against `fake_libvirt`: a target whose UUID no
> longer resolves must never reach `_delete_volume`.

The marker half was implemented (`_reverify`). The UUID half was not: a target whose
UUID no longer resolves *does* reach `_delete_volume`, and the test at
`tests/test_libvirt_destroy.py:157-171`
(`test_a_domain_already_gone_still_has_its_disks_collected`) asserts the opposite of
what the checklist asked for — `assert pool.deleted == ["app01.qcow2"]`. (The
checklist itself is unmaintained — 6 of 121 boxes ticked — so an unticked box proves
nothing; the deviation is visible in the code and the test.)

**The deviation has a real reason and I am not arguing it away.** Dropping a
vanished target's disks entirely guarantees a leak: destroy enumerates by marker, the
marker died with the domain XML, and nothing will ever find those two files again
except `orphan_volumes` on the next deploy of the same config. Trading a guaranteed
leak for a narrow race is a defensible call. What is not defensible is that the trade
is **silent**: scenario B produces `out.problems == []`. The vanished branch is
deleting on materially weaker evidence than the `else` branch and says so nowhere.

**What would close it.** Either of:

* Re-derive the evidence instead of trusting the snapshot: before deleting in the
  vanished branch, resolve the path through the pool and compare the volume's
  `<backingStore><path>` against the base volume `preflight` already resolved, or
  compare its creation time against the preflight timestamp. A volume created after
  preflight ran is not the one preflight saw.
* Keep the deletion but make it loud: a non-fatal `Problem` on every delete taken in
  the vanished branch, naming that the domain was gone and the path was matched by
  name only. One line, no behaviour change, and the operator in scenario B has
  something to correlate against B's broken apply.

The cheapest honest version is the second.

### Every other path into `_delete_volume`, enumerated

| line | construct | matches | on failure |
|---|---|---|---|
| `:487` `getLibVersion` | `except libvirtError` | by type | fatal `Problem`, `raise DestroyError` before any deletion |
| `:282` `listAllStoragePools` | `except libvirtError` | by type | fatal `Problem`, `return`; every path then reads as gone and is `skipped` |
| `:306` `pool.name`/`isActive` | `except libvirtError` | by type | fatal `Problem`, `continue` to the next pool |
| `:246` `_pool_holds` | `except (libvirtError, ParseError)` | by type | `None` → fatal `Problem`, never collapses to `[]` |
| `:343` `pool.refresh` | `except libvirtError` | by type | `WARNING` only |
| `:375` `listAllDomains` | `except libvirtError` | by type | `None` → fatal `Problem`, `raise` before the loop |
| `:384` per-domain read | `except (libvirtError, ParseError)` | by type | `WARNING`, guard narrows — scenario C |
| `:517` `lookupByUUIDString` | `except libvirtError` | **numeric** (`ERR_NO_DOMAIN`) | fatal + `continue` for every other code |
| `:415` `_reverify` XMLDesc | `except (libvirtError, ParseError)` | by type | fatal `Problem`, `None` → `continue`, disks untouched |
| `:114` `_stop` | `except libvirtError` | **numeric** (`ERR_OPERATION_INVALID` + `_is_off`) | fatal, `False` → `continue` |
| `:155`/`:176` `_undefine` | `except libvirtError` | **numeric** (`ERR_INVALID_ARG`, `mask == FLOOR`) | fatal, `False` → `continue` |
| `:208` `_delete_volume` | `except libvirtError` | **numeric** (`ERR_NO_STORAGE_VOL`) | `skipped` for 50, fatal for everything else |

Every by-type catch is either fatal in every branch or is the documented
`WARNING`-narrowing at `:384`. The unqualified catch F-DSK-01 named is gone. The
answer to "does each match a numeric code or is every branch fatal" is yes, with the
one deliberate exception at `:384`.

Verified against the constants: `ERR_NO_DOMAIN = 42`, `ERR_NO_STORAGE_VOL = 50`,
`ERR_INVALID_ARG = 8`, `ERR_OPERATION_INVALID = 55` (`errors.py:20-32`), pinned
against the installed binding by `tests/test_libvirt_errors.py` under the `require`
gate rather than `importorskip`.

---

## Q2 — does a partially-failed destroy exit non-zero and name every leaked volume?

**Exit code: yes, on both shapes.**

* `destroy()` raises `DestroyError` when `out.failed` (`destroy.py:549`). `_guard`
  records and re-raises; `main`'s generic handler prints
  `error: DestroyError: <message>` and returns 1 (`cli.py:632-642`).
* No fatal problems but a non-empty `skipped` returns 1 explicitly
  (`cli.py:509-520`) with `run.json` naming them.

**Naming: yes on stderr.** `DestroyError.__init__` (`destroy.py:92-97`) deliberately
carries the whole `Outcome`:

```python
lines = [str(p) for p in outcome.problems if p.fatal]
lines += [f"  skipped: {name}" for name in outcome.skipped]
lines += [f"  {p}" for p in outcome.problems if not p.fatal]
```

Pinned by `test_the_error_names_everything_left_behind_not_only_what_failed`.

**But the run record loses half of it.** See RW-A2.

### RW-A2 — `run.json` for a partially-failed destroy records nothing it removed

**Severity:** medium
**Location:** `orchestrator/cli.py:237` (`_guard`'s `_record(run, "failed", ...)`),
against `cli.py:501-507` on the non-raising path

`_destroy` builds the structured record from the returned `Outcome`:

```python
    _record(
        run,
        "partial" if out.skipped else "ok",
        destroyed=sorted(out.destroyed),
        skipped=sorted(out.skipped),
        problems=run.extra["problems"] + [str(p) for p in out.problems],
    )
```

That code is only reached when `destroy()` **returns**. When it raises — which is
every teardown with a fatal problem, the runs with the most to record — control
leaves `_destroy` before line 501 and `_guard` writes instead:

```python
        with contextlib.suppress(OSError):
            _record(run, "failed", error=f"{type(exc).__name__}: {exc}")
```

Reproduced through `cli.main(["destroy", ...])` with a backend whose `destroy`
raises carrying `destroyed=["app01", "/pool/app01.qcow2", "/pool/app01-seed.iso"]`,
`skipped=["/pool/app02-seed.iso"]` and one fatal `Problem`:

```json
{
  "backend": "fake",
  "command": "destroy",
  "decisions": [],
  "deployment": "lab-a",
  "error": "DestroyError: error [app02]: could not undefine: internal error\n  skipped: /pool/app02-seed.iso",
  "finished": "20260830T034052Z",
  "outcome": "failed",
  "problems": [],
  "started": "20260830T034052Z",
  "vcows": "0.1.0.0"
}
```

No `destroyed` key, no `skipped` key. The three objects this run actually removed —
a domain and two volumes — are recorded nowhere. `skipped` survives only as prose
inside `error`, and `problems` is `[]` because `run.extra["problems"]` holds the
preflight advisory list and was never updated with the teardown's own.

Why it matters here specifically: the run directory is what an air-gapped site ships
back (`_record`'s own docstring, `cli.py:187-194`), stderr is not. After a partial
teardown the two questions an operator has are "what is gone" and "what is left", and
this artifact answers only the second, in prose. `destroy.py`'s module docstring
states the contract this breaks: "Every object's outcome is reported". Checklist item
2.3 — "Record the real outcome in `run.json`" — was implemented on the return path
and missed on the raise path. `_Run`'s own docstring at `cli.py:170-176` says the
failure path "has to write the same record as the success path"; here it does not.

No test covers it. `test_a_destroy_that_could_not_finish_says_what_it_left`
(`tests/test_cli.py:450-477`) asserts `record["destroyed"] == ["app01"]` but drives
the *returning* path. `test_a_backend_that_raises_is_reported_without_a_traceback`
(`:605-620`) drives the raising path and asserts only on stderr.

**Fix:** hang the `Outcome` on `run.extra` as soon as `destroy()` produces one. The
libvirt backend already carries it on the exception (`DestroyError.outcome`), but
core cannot import that type — findings.md §3 rules out a shared hierarchy — so the
seam-safe version is for `_destroy` to catch nothing and instead for `Backend.destroy`
to be documented as populating a caller-supplied record, or more cheaply for
`_destroy` to wrap the call in its own `try/except BaseException`, write
`run.extra["destroyed"]`/`["skipped"]` from whatever it can reach, and re-raise. The
smallest correct change is probably the latter with `getattr(exc, "outcome", None)`,
which keeps core ignorant of the type while still reading the record when it is there.

---

## Q3 — is the base image / backing chain still protected?

Yes, by four independent mechanisms, three of them re-verified here.

1. **`disks_of` never follows a backing chain** (`preflight.py:100-126`).
   `disk.find("source")` matches direct children only; `<backingStore>` is a direct
   child of `<disk>` and its own `<source>` is a grandchild, so element order is
   irrelevant and nested chains are unreachable. `source/@file` only, so block,
   network and volume-pool disks yield nothing.
2. **The tofu module never gives a domain the base as a `source.file`** — it is
   reached only through `backing_store`. Re-confirmed by agent 14 on 2026-08-29
   against `main.tf`; unchanged in this range apart from the outputs.
3. **`_deletable`'s basename allowlist** (`destroy.py:456-472`) — new in this range,
   and the belt to the above braces. A domain of ours that has been hand-given the
   golden image as a second disk is refused with `is not one of the names this VM
   owns`, fatal. Pinned by
   `test_a_recorded_path_outside_this_vms_two_names_is_not_deleted`.
4. **`schema._check_base_name`** (`schema.py:267-284`) refuses a config whose
   `image.base_volume_name` collides with any configured VM's derived overlay or seed
   name.

The one gap I looked for and could not turn into a trigger: guard 4 is scoped to VMs
in the *current* config, while `_deletable`'s allowlist derives from
`target.marker.name`, which can be a VM since removed from the config. Constructing a
case where those disagree requires the base volume and an overlay to share a name in
one pool (impossible — libvirt volume names are unique per pool) or the base to live
in a different pool *and* be attached to one of our domains as a direct `source.file`
(guard 2 says vcows never does this, and if an operator did, they attached the shared
golden image to a VM they are tearing down). Not a finding.

`_delete_volume` still resolves strictly through `conn.storageVolLookupByPath` with
no `os.unlink` fallback (`destroy.py:207`), so the blast radius stays inside storage
libvirt manages.

---

## Q4 — do the new tests fail when the code is broken?

`tests/test_libvirt_destroy.py` went 23 → 37 tests. I mutated the five load-bearing
predicates in a scratch copy of the tree and re-ran
`tests/test_libvirt_destroy.py tests/test_cli.py` (baseline 73 passed). Each mutation
was reverted before the next.

| # | mutation | result |
|---|---|---|
| M1 | `_deletable`: `if PurePosixPath(path).name not in owned:` → `if False:` | 1 failed — `test_a_recorded_path_outside_this_vms_two_names_is_not_deleted` |
| M2 | `_deletable`: `if path in claimed:` → `if False:` | 1 failed — `test_a_vanished_targets_disk_claimed_by_another_domain_is_left_alone` |
| M3 | `_reverify`: `return replace(target, disks=disks_of(root))` → `return target` | 1 failed — `test_the_disks_deleted_are_the_ones_the_domain_names_now` |
| M4 | destroy loop: `if exc.get_error_code() != ERR_NO_DOMAIN:` → `if False:` | 1 failed — `test_a_lookup_that_failed_for_any_other_reason_is_fatal_and_touches_no_disk` |
| M5 | `_reverify`: `if marker != target.marker:` → `if False:` | 1 failed — `test_a_domain_whose_marker_changed_since_preflight_is_left_alone` |

Every one bit, and each bit exactly once — no shotgun failures masking a weak
assertion. Reading the three most important:

* **`test_the_disks_deleted_are_the_ones_the_domain_names_now`** builds a domain
  naming `/pool/app01.qcow2` and a snapshot naming `/pool/app01-seed.iso`, puts both
  volumes in the pool, and asserts `pool.deleted == ["app01.qcow2"]`. It pins the
  *direction* of the disagreement, not just that a re-read happened: the live
  document wins and the snapshot's path is untouched. That is the right assertion —
  a weaker `assert "app01.qcow2" in pool.deleted` would pass on `return target`
  wherever the two lists overlap. Real teeth.
* **`test_a_recorded_path_outside_this_vms_two_names_is_not_deleted`** gives an
  owned, marked, correctly-UUID'd domain a disk at `/pool/golden.qcow2`, puts that
  volume in the pool so it *would* resolve, and asserts both `DestroyError` and
  `pool.deleted == []`. It proves the allowlist rejects a path the domain genuinely
  names — the case where every other guard says yes. This is the S6 test and it
  earns its place.
* **`test_a_vanished_targets_disk_claimed_by_another_domain_is_left_alone`** uses a
  ghost `Existing` with a UUID no domain has and a live `app99` naming
  `/pool/app01.qcow2`, and asserts the deletion is refused. It is the only test on
  the vanished branch's guard — and note what it does *not* cover: the same branch
  with nothing claiming the path, which is RW-A1's scenario B.

Two supporting fixture improvements are worth crediting because they are what let
these tests be written at all: `domain_xml`/`target` in the test file now produce a
real marked document parsed by the real `marker_of`/`disks_of` rather than
`<domain/>` plus a hand-built `Existing`, and `FakeConnection.storageVolLookupByPath`
now keys on the whole path (`tests/fake_libvirt.py:222-237`), closing F-DSK-02. With
the old suffix match, `test_an_inactive_pool_holding_nothing_of_ours_is_left_alone`
would have passed vacuously.

The one gap in the new suite: no test drives the vanished branch with an empty
`claimed` set and a *resolvable* volume — i.e. no test states, either way, what
should happen in scenario B. `test_a_domain_already_gone_still_has_its_disks_collected`
comes closest and asserts the current behaviour is correct.

---

## Checked and sound — not filed

* **`_stop`/`_undefine` fail closed.** Both return `False`, both call sites
  `continue`, disks untouched. `dom.isActive()` is inside the `try` now, so a raise
  there is a fatal `Problem` for that target rather than an escape from the loop —
  `test_a_domain_that_cannot_be_asked_whether_it_is_running_is_reported` proves the
  next target is still torn down.
* **`_is_off` reads an unanswerable question as "not off"** (`destroy.py:126-138`).
  Right direction; pinned.
* **NVRAM cannot be shed.** `FLOOR` ORs bit 4 unconditionally, `undefine_mask` only
  ORs onto `FLOOR`, the single retry passes `FLOOR` literally, and the retry is
  entered only on `ERR_INVALID_ARG` with `mask != FLOOR`.
* **Destroy-then-undefine ordering** is unchanged and correct.
* **`_pool_holds` returns `None` for "could not tell"** and `destroy` treats it as
  fatal rather than collapsing it into the empty list. Pinned by
  `test_an_inactive_pool_that_will_not_describe_itself_is_not_read_as_empty`.
* **`preflight.orphan_volumes` compares whole paths**, not basenames
  (`preflight.py:436`, `claimed` built at `preflight.py:571`). The 2026-08-29 item
  at checklist line 131 landed. It only reports; it deletes nothing.
* **The size-mismatch path** (`base_volume`, `preflight.py:370-391`) is non-destructive
  and offers the non-destructive procedure — a new `base_volume_name` uploads
  alongside. It counts and names the overlays that would break. Advisory on destroy
  (`cli.py:457-471`), fatal on deploy.
* **`walk()`** drops an unreadable volume with a `WARNING` naming what the skip cost;
  a dropped volume is neither a golden-image candidate nor an orphan, which is the
  safe direction for both consumers.
* **`_claimed_elsewhere` narrowing to a `WARNING`** (scenario C above) is an argued
  severity decision with a docstring and a test. Recorded, not filed.
* **`_refresh_pools` tests preflight's paths, not `_reverify`'s.** Documented at
  `destroy.py:273-276`. The consequence when they disagree is a `NO_STORAGE_VOL`
  skip and a non-zero exit, not a silent leak.

## Not checked

* No live libvirt. Every reproduction above is against `tests/fake_libvirt`. The
  claim that `virDomainLookupByUUID` reaches only `NO_DOMAIN` and `ACCESS_DENIED`
  server-side is inherited from the 2026-08-29 review, not re-observed.
* The rig gate (15 tests) and image gate (10) skipped, as Phase 0 recorded.
* `orchestrator/backends/libvirt/tofu/` — the module's own destroy semantics are
  irrelevant here (teardown is not `tofu destroy`) and belong to dimension D.
* `cli.py` outside `cmd_destroy`/`_destroy`/`_guard`/`_record`/`_confirm`.
* `preflight` outside the destructive halves named in the brief — `address_conflicts`,
  `_network_claims`, `connect`.
* Everything under `scripts/`, `justfile`, `.github/`, `.gitlab-ci.yml` — dimension G.

## Findings

| id | severity | file:line | claim |
|---|---|---|---|
| RW-A1 | medium | `orchestrator/backends/libvirt/destroy.py:545` | Vanished target's disks deleted on name-pattern evidence alone, silently |
| RW-A2 | medium | `orchestrator/cli.py:237` | A raised destroy records nothing it removed in `run.json` |
