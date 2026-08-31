# Dimension A — destroy and preflight remediation

Agent: A-destroy-path · Range: `4eb378b..672a500` · Read at `origin/master` in the
detached worktree · Date: 2026-08-31

Scope: `destroy.py`, `preflight.py`, `cli.py:_destroy`/`_guard`/`_record`, and their
tests. Local baseline: 133 passed on `test_libvirt_destroy.py test_cli.py
test_libvirt_preflight.py`.

## Summary

* **RW-A2 is closed.** `cli.py:552-564` catches the raising destroy, reads
  `getattr(exc, "outcome", None)`, and hangs `destroyed`/`skipped`/`problems` on
  `run.extra` before re-raising (`tests/test_cli.py:625`). It misses a Ctrl-C — RX-A3.
* **RW-A1's silence is closed on the shape that deletes and opened on the shape that
  does not.** `_deleted_on_name_alone` (`destroy.py:459`) fires *before*
  `_delete_volume`, so both vanished-branch runs where nothing is deleted ship a
  record asserting a delete — RX-A1.
* **#31 changed no behaviour, and added no path to a delete.** All five collapsed
  blocks keep their exact message text and `Severity.ERROR`; `_delete_volume` still
  has exactly one caller (`destroy.py:557`) and one `.delete(` (`destroy.py:202`).
* **The PARTIAL item is still PARTIAL, and its recorded reason is false** — RX-A2.

## RX-A1 — the vanished-branch warning claims a delete that did not happen

**Severity:** medium
**Location:** `orchestrator/backends/libvirt/destroy.py:553-557`; message at `:477-484`

```python
        for path in target.disks:
            if _deletable(path, target, claimed, out):
                if vanished:
                    _deleted_on_name_alone(out, target, path)
                _delete_volume(session, path, target.name, out)
```

`_delete_volume` has three outcomes: delete (`out.destroyed`), `NO_STORAGE_VOL`
(`out.skipped`), any other code (fatal `Problem`). The warning fires for all three, in
the past tense: *"X was deleted on its name alone"*.

**Reproduction 1 — the ordinary vanished shape, and nothing was deleted.** Operator B
completes a destroy of `app01`, domain *and* both volumes, inside operator A's confirm
prompt; A then types `yes`. Against `tests/fake_libvirt`, `pool.deleted` and
`destroyed` are both `[]`, `skipped` is `['app01', '/pool/app01.qcow2',
'/pool/app01-seed.iso']`, and `out.problems` holds two warnings reading
`/pool/app01.qcow2 was deleted on its name alone: domain 'app01' was already gone …`.
One `run.json` says both that nothing was removed and that two files were deleted.
This is the *common* vanished case — a teardown removes the disks with the domain, so
`NO_STORAGE_VOL` on both paths is the expected pairing. The scenario the warning was
written for (`#9`: B re-*deploys* mid-prompt) is the rarer of the two.

**Reproduction 2 — the delete is attempted and fails.** Same ghost target, volume
present, `vol.delete` raising a non-`NO_STORAGE_VOL` code. `str(DestroyError)` — what
`main` prints and `run.json["error"]` carries — becomes self-contradicting:
`error [app01]: could not delete /pool/app01.qcow2: cannot unlink: Permission denied`
followed by `warning [app01]: /pool/app01.qcow2 was deleted on its name alone: …`.

**Why it matters here.** The run directory is what an air-gapped site ships back
(`cli.py:216`), and #9 existed to give the operator in the race something to correlate
against a broken concurrent apply. A false "was deleted" sends them after a file vcows
never touched, and contradicts `destroyed: []` in the same artifact. `destroy.py:24`:
"Every object's outcome is reported." Neither new test catches it —
`test_libvirt_destroy.py:174` and `:209` both put the volume in the pool.

**Fix, and its cost.** Emit the warning only when the delete happened — five lines, no
new surface. Measured in a scratch copy: record `len(out.destroyed)` before the
`_delete_volume` call and call `_deleted_on_name_alone` after it only if the count
rose. `79 passed` there (3 errors are the missing `tofu/` module files, not the
change), both `#9` tests unchanged, and reproduction 1 then reports three skips and no
warning.

## RX-A2 — the vanished branch is kept for a reason the code does not support

**Severity:** medium
**Location:** `orchestrator/backends/libvirt/destroy.py:462-464`; echoed at
`tests/test_libvirt_destroy.py:160-161` and in issue `#9`'s "Held at medium"

`destroy.py:462-464`:

> The vanished branch is deliberate and stays: a domain gone between preflight and
> teardown still has its disks collected, **which is what makes a teardown interrupted
> between undefine and delete finishable by re-running (df60f74).**

A re-run cannot reach that branch. Targets come from `preflight._domains`
(`preflight.py:162-186`), which appends an `Existing` only for domains
`conn.listAllDomains(0)` returns; an undefined domain is not returned, so it is never
a target and its recorded disks are never resolved. Measured — world after an
interrupted teardown, domain undefined, overlay still on disk — `preflight` returns
`vms: ()` and the destroy target list is `[]`. The same run *does* name the leaked
file, through `orphan_volumes` (`preflight.py:564`, advisory on destroy, fatal on
deploy): `volume 'app01.qcow2' exists but no domain on this host references it`.

Two consequences.

1. The window the module docstring describes (`destroy.py:17-19`: the domain is off
   but **still defined, still marked**, "and a re-run finishes it") is resumed through
   the `else` branch. `test_a_domain_already_gone_still_has_its_disks_collected`'s
   docstring at `:160` — "The crash window between destroy and undefine, resumed" —
   names that window, so the test does not exercise what it says.
2. The branch's **only** reachable trigger is a third party undefining our domain
   inside this run's preflight → confirm → destroy window: the same race that makes
   the delete unsafe. Its benefit and its risk have identical triggers, and the
   alternative's stated cost is a leak `orphan_volumes` reports by name.

I am not re-opening the severity call `#9` made. The finding is that the recorded
justification for a delete taken on the weakest evidence here is verifiably false, and
it is why the next reader will not re-examine it. Fix: correct the two docstrings. No
code change, no surface.

## RX-A3 — a Ctrl-C mid-teardown still records nothing it removed

**Severity:** low
**Location:** `orchestrator/cli.py:552` (`except Exception as exc:`) against
`cli.py:258-262`

`KeyboardInterrupt` inherits `BaseException`, so it passes the RW-A2 fix untouched and
`_guard` writes the pre-fix record. Measured through `cli.main(["destroy", …])` with
`backend.destroy` raising `KeyboardInterrupt`: `run.json` carries `"outcome":
"failed"`, `"error": "KeyboardInterrupt: "`, and **no `destroyed` and no `skipped`
key** — the shape RW-A2 named. `_guard`'s docstring (`cli.py:251-252`) calls this "the
run with the most to say and the least chance of saying it";
`tests/test_cli.py:338` drives the path and asserts only `outcome` and `error`.

**There is no cheap fix and I am not proposing the expensive one.**
`except BaseException` buys nothing — `KeyboardInterrupt` carries no `.outcome`, and
the `Outcome` is a local inside `destroy()`. Closing it means `Backend.destroy`
populating a caller-supplied record: an ABC signature change across both backends,
more surface than the defect warrants. Filed so the gap is recorded, not believed
closed.

## RX-A4 — a stale `file:line` shipped in the range that removed stale `file:line`s

**Severity:** nit
**Location:** `orchestrator/backends/libvirt/destroy.py:474`

The docstring cites "``preflight.py:39-42`` requires a skip to name the object and
what the skip cost". Those lines are the import block; the rule is `preflight.py:21-26`.
It is the only `file:line` added to `destroy.py`, `preflight.py` or `cli.py` in the
range, and `#28` ("drop the stale line numbers", `c124ffe`) closed in that same range
for this class.

## Settled by observation — the libvirt error codes, on the rig

The claim carried since 2026-08-29
(`docs/review-2026-08-29/14-destroy-disk-safety.md:15,171`) is that
`virDomainLookupByUUID` reaches "exactly two codes: `NO_DOMAIN` (42) and
`ACCESS_DENIED` (88)". Measured read-only on `qemu+ssh://vcows@vcows/system`, daemon
12000000, client 11010000. Nothing was defined, started, stopped, undefined or deleted.

| call | code | name |
|---|---|---|
| `lookupByUUIDString`, well-formed absent UUID | **42** | `VIR_ERR_NO_DOMAIN` |
| `lookupByUUIDString`, malformed UUID string | **8** | `VIR_ERR_INVALID_ARG`, client-side |
| `lookupByUUIDString`, closed connection | **6** | `VIR_ERR_INVALID_CONN`, client-side |
| `undefineFlags` on a read-only connection | **29** | `VIR_ERR_OPERATION_DENIED` |
| `storageVolLookupByPath` — absent, outside every pool, relative, a directory | **50** ×4 | `VIR_ERR_NO_STORAGE_VOL` |

The two-code enumeration is wrong; `errors.py:23-27` is not — it says only that 42
means gone, and `destroy.py:531` treats every other code as fatal, so 8, 6, 29 and 88
all land in the branch that touches no disk. Not a finding. The storage row confirms
`_delete_volume`'s bound (`destroy.py:194-197`): a path outside every pool returns 50
on a real host, so it is skipped, never unlinked. `/etc/shadow` returned 50.

## Checked and sound

* **`#31` is a pure de-duplication.** Five sites, all `Severity.ERROR`, all messages
  verbatim including `f"delete {path}"`'s embedded path; `_fail`'s two discard sites
  are the two its docstring names; `_reverify`'s "could not re-read" correctly stays
  outside `_fail`, since it also catches `ET.ParseError`. `#30` flipped no severity in
  `destroy.py`, `preflight.py` or `base.py:294`.
* **RW-A2's return and raise paths now write the same keys**; `test_cli.py:660-664`
  pins `destroyed`/`skipped`/`problems` on the raise.
* **`Existing.id` is the libvirt domain UUID** (`preflight.py:166`), not the marker
  `id`, and `main.tf` sets no `uuid` — a redeployed VM gets a new domain UUID, so the
  old target genuinely resolves `NO_DOMAIN`. The 2026-08-30 finder's scenarios A and B
  rest on the right assumption.
* **`_deletable`, `_claimed_elsewhere`, `_pool_holds`, `_refresh_pools`, the undefine
  floor and the destroy-then-undefine order** are unchanged and re-read; the
  2026-08-30 analysis of each still matches. `preflight.py`'s only changes here are
  the `Problem` collapse and two comment fixes (`#41`, `#42`).

## Not checked

* The rig gate's 15 tests (Phase 0 ran them: 15 passed). Every reproduction above is
  `tests/fake_libvirt`; only the error-code table is live, and read-only.
* `_claimed_elsewhere` is computed once, before the target loop, so `claimed` carries a
  millisecond-scale staleness window against each delete. Inherent, far narrower than
  the confirm prompt, not filed.
* `walk` keys its dict on the volume XML's `<name>` (`preflight.py:310`) rather than
  `vol.name()`; a volume whose XML omitted `<name>` would drop out of all three
  consumers with no `Problem`. libvirt always emits it, and this is preflight's
  non-destructive half (dimension G).
* `cli.py` outside `_destroy`/`_guard`/`_record`; `preflight` outside the destructive
  halves.
