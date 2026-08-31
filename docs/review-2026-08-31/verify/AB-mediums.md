# Phase 3 verify — the four A/B mediums

Verifier: adversarial, default REFUTED. Date 2026-08-31. Everything below was driven at
`origin/master` `672a500` in the detached worktree, with reproductions run from a
`mktemp -d` copy of `orchestrator/` + `tests/`. No tracked file was written; the rig was
not touched at all by this pass.

| finding | verdict | severity |
|---|---|---|
| `RX-A1` `destroy.py:553-557` | **CONFIRMED** | medium (as filed) |
| `RX-A2` `destroy.py:462-464` | **CONFIRMED as a documentation defect, downgraded** | low (filed medium) |
| `RX-B1` `cli.py:411`, `tofu.py:91` | **CONFIRMED, downgraded** | low (filed medium) |
| `RX-B2` `cli.py:552` | **CONFIRMED** | low (filed medium by B, low by A) |

Every `file:line` in the four findings was re-read at `672a500`. All of them hold:
`destroy.py:553-557` is the `for path in target.disks` loop; `:462-464` is the
`df60f74` sentence; `cli.py:411` is `_note_warnings(run, applied)`; `cli.py:552` is
`except Exception as exc:`; `tofu.py:91` is `self.result = result`. Two supporting
citations also hold: `cli.py:251` is the `BaseException` docstring line and
`cli.py:571` is the literal-`20` print. `test_libvirt_destroy.py:174` and `:209` are
the two `#9` tests.

---

## RX-A1 — the vanished branch warns "was deleted" before the delete is attempted

### Lens 1 — reproduce

Driven through the real `destroy.destroy()` against `tests/fake_libvirt`, both shapes the
finder claims.

**Shape 1 — the ordinary vanished case, where nothing was deleted.** Ghost target
(`lookupByUUIDString` → `NO_DOMAIN` 42), active pool holding neither volume, so both
paths resolve `NO_STORAGE_VOL` (50) and land in `out.skipped`:

```
$ PYTHONPATH=. .venv/bin/python repro/a1.py
== shape 1: operator B already removed domain AND both volumes ==
pool.deleted = []
destroyed    = []
skipped      = ['app01', '/pool/app01.qcow2', '/pool/app01-seed.iso']
problem      = warning [app01]: /pool/app01.qcow2 was deleted on its name alone: domain 'app01' was
               already gone, so this path came from the preflight snapshot and nothing re-read the
               domain to confirm it still owns it
problem      = warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
failed       = False
```

`pool.deleted == []` and `destroyed == []` — nothing was removed — beside two warnings in
the past tense saying two files were deleted.

**Shape 2 — the delete is attempted and fails.** `vol.delete` raising code 38
(non-`NO_STORAGE_VOL`), so `_fail` fires. `str(DestroyError)`, which is what `main` prints
and what `run.json["error"]` carries, self-contradicts:

```
error [app01]: could not delete /pool/app01.qcow2: cannot unlink file '/pool/app01.qcow2': Permission denied
error [app01]: could not delete /pool/app01-seed.iso: ...
  skipped: app01
  warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
  warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
```

**The record.** Feeding shape 1's `Outcome` through `cli._destroy`'s record writer
(fake backend, `cli.main(["destroy", …, "--yes"])`) shows the contradiction inside one
`run.json`:

```
RX-A1 record destroyed = []
RX-A1 record skipped   = ['/pool/app01-seed.iso', '/pool/app01.qcow2', 'app01']
RX-A1 record problem   = warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
RX-A1 record problem   = warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
```

Shape 1 is not the exotic case. The vanished branch fires when a domain disappears
between preflight and the lookup, and the ordinary way a domain disappears is a teardown
that takes its disks with it — so `NO_STORAGE_VOL` on every path is the *expected*
pairing with `NO_DOMAIN`, and it is exactly the pairing that produces two false
"was deleted" lines.

### Lens 2 — already handled or deliberate

Not documented anywhere, and the commit that introduced the warning shows the opposite
intent. `2f8ebe2` ("Say when a vanished target's disks are deleted on their name alone",
`#9`) reads: "the branch is tracked and **every delete taken in it** carries a warning
naming the path, the domain, and what the evidence actually was." The warning was designed
to accompany a delete that happened; it is emitted unconditionally for all three
`_delete_volume` outcomes. `docs/findings.md`, `docs/review-2026-08-30/REVIEW.md` and its
finders carry nothing on this. The two `#9` tests (`test_libvirt_destroy.py:174`, `:209`)
both stock the pool, so neither can see it.

**Verdict: CONFIRMED, medium.** A false statement in the artifact an air-gapped site
ships back, in an active path, contradicting `destroyed: []` in the same file. Not data
loss, so not high.

---

## RX-A2 — the vanished branch's recorded justification is false

### Lens 1 — reproduce

The claim is that a *re-run* cannot reach the vanished branch, so the branch is not what
makes an interrupted teardown finishable. Built the world `destroy.py:462-464` names — the
teardown was interrupted after `undefineFlags` and before the disks went, so the domain is
undefined and the overlay is still in the pool — and ran the real `preflight()`:

```
$ PYTHONPATH=. .venv/bin/python repro_a2.py
preflight vms       = ()
preflight problem   = error [app01]: volume 'app01.qcow2' exists but no domain on this host
                      references it. A create interrupted before its domain was defined leaves
                      exactly this ...
destroy targets     = []
```

The mechanism is `preflight._domains` (`preflight.py:144-186`), which builds `Existing`
only from `conn.listAllDomains(0)`; an undefined domain is not returned, so no target
exists and the vanished branch is never entered. `cmd_destroy`'s targets come from
`discovered.vms` (`cli.py:505-510`), so there is no second source. The leaked file is
still named, by `orphan_volumes` (`preflight.py:390`, reached at `:564`).

The module docstring's own crash window (`destroy.py:17-19`: the domain is off, **still
defined, still marked**, "and a re-run finishes it") is the window between `destroyFlags`
and `undefineFlags`, and it is resumed through the live `else` branch, not the vanished
one. `:462-464` names a different window — after the undefine — and after the undefine
there is no target at all.

### Lens 2 — already handled or deliberate

This is the lens that decides the verdict. `df60f74`'s message says: "The crash-window
resume is deliberately preserved. A domain that is gone still has its disks collected,
which is what makes a teardown interrupted between undefine and delete finishable by
re-running." `2f8ebe2` repeats it verbatim. So the branch **is** deliberate, and
`:462-464` is that decision written down.

The finding does not ask for the branch to be removed — the finder says so explicitly
("Fix: correct the two docstrings. No code change, no surface"). What it establishes is
that the sentence justifying the branch is false as written, and that
`test_a_domain_already_gone_still_has_its_disks_collected`'s docstring
(`test_libvirt_destroy.py:160`, "The crash window between destroy and undefine, resumed")
names a window the test does not exercise — it exercises a domain vanishing inside the
preflight → confirm → destroy window instead. Both statements are wrong; the code is not.

**Verdict: CONFIRMED as a documentation defect, DOWNGRADED to low.** The branch stays, the
runtime behaviour is unaffected, and the whole fix is two docstrings plus the test
docstring. Filed at medium; the 08-31 scale turns severity on reachability and
consequence, and a false comment has no runtime consequence. It is above `RX-A4`'s nit
because it is a substantively wrong justification for a delete taken on the weakest
evidence in the module, not a drifted line number.

---

## RX-B1 — a failing tofu step's own warnings are dropped

### Lens 1 — reproduce

`init` warns, `plan` warns, `apply` raises a `TofuError` carrying a `Result` with one
warning and one error, through `cli.main(["deploy", cfg])`:

```
RX-B1 outcome        = failed
RX-B1 tofu_warnings  = ['warning: init warned', 'warning: plan warned']
RX-B1 error          = TofuError: tofu apply failed (exit 1): error: apply blew up
```

`apply warned about a deprecated argument` appears nowhere in `run.json` (asserted:
the string is absent from the whole serialised record). The warning rode in on
`TofuError.result` and was discarded.

Correction to the finder's transcript: it printed
`P1 tofu_warnings= ['warning: init warned']`, missing `plan`'s. Both earlier steps' warnings
*are* recorded — that half of RW-B4 works. The finding is unaffected: only the raising
step's own warnings are lost.

The dead-reader half also holds:

```
$ grep -rn "\.result\b" --include=*.py .   (worktree, .venv excluded)
orchestrator/tofu.py:91:        self.result = result
tests/test_tofu_driver.py:186:    assert caught.value.result is not None
tests/test_tofu_driver.py:187:    (error,) = caught.value.result.errors
```

One assignment, one test reader, no production reader.

### Lens 2 — already handled or deliberate

Not handled and not deliberate, but it is the residual of a **low**. RW-B4
(`docs/review-2026-08-30/REVIEW.md:149`, finder `:291`) was filed at *low* severity and its
remedy was written as "Accumulate into `run.extra["tofu_warnings"]` after each of `init`,
`plan` and `apply` **returns**". `2b20608`/`4cc9d35` applied exactly that, and the comment
at `cli.py:385-391` records the reasoning. The remedy as written stops at the return, so
the raising step was never in scope. Nothing in `docs/findings.md` covers it.

**Verdict: CONFIRMED, DOWNGRADED to low.** The defect is real and reproduced, but it is a
missing datum rather than a wrong one — the error itself is recorded, the run is correctly
`failed`, and this is strictly narrower than RW-B4, which the last review rated low.
Filing it as medium would rank it above the finding it is a remainder of.

---

## RX-B2 — an interrupted destroy records nothing it removed

### Lens 1 — reproduce

Both halves, actually raising `KeyboardInterrupt` at the claimed point.

**Core half**, `backend.destroy` raising a `KeyboardInterrupt` subclass carrying a
populated `Outcome`, against a plain `Exception` carrying the identical outcome:

```
RX-B2 KI  rc= 1
RX-B2 KI  outcome= failed destroyed= <ABSENT> skipped= <ABSENT> error= Interrupted:
RX-B2 Exc outcome= failed destroyed= ['app01'] skipped= ['/pool/app02-seed.iso']
```

`except Exception` at `cli.py:552` does not run for the interrupt; `_guard` writes
`outcome`/`error` and nothing else.

**Libvirt half**, `KeyboardInterrupt` raised from inside `vol.delete` after the domain was
already destroyed and undefined:

```
raised            = KeyboardInterrupt
getattr(exc,'outcome') = <ABSENT>
domain log        = ['destroy', 'undefine:55']   (the undefine already happened)
```

So real work was done, `out` is a live populated local, and it is attached to nothing —
`destroy()` attaches it only via `DestroyError(out)` (`destroy.py:508`, `:521`, `:560`).
Changing `cli.py:552` to `except BaseException` alone would therefore capture nothing from
this backend; both halves are needed, which is the point dimension A made independently.

### Lens 2 — already handled or deliberate

Not handled. `#8`/RW-A2 (`docs/review-2026-08-30/REVIEW.md:54`) is "a destroy that raises
records nothing it removed", and its stated remedy — `_destroy` wraps the call, reads
`getattr(exc, "outcome", None)`, re-raises — is what landed. The interrupt was never in
that scope, and nothing in `docs/findings.md` or the 08-30 finders mentions it.

**One part of B's framing does not survive.** B titles this "`except Exception`, where
`_guard` says `BaseException`". `_guard`'s docstring (`cli.py:251-253`) promises only that
a Ctrl-C "writes one too" — a record — and it does; `_guard` itself catches
`BaseException` at `:258` and behaves as documented. The comment at `cli.py:553-558`
governs the narrower block and names "the teardown with a fatal problem", not the
interrupt. There is no docstring contradiction; there is a gap neither comment claims to
cover.

**Verdict: CONFIRMED, low.** Reproduced end to end and the consequence is real — after a
Ctrl-C the operator cannot tell from the shipped directory which domains were already
undefined, and (per RX-A2's mechanism) a re-run's preflight cannot see them either, so
they surface only as `orphan_volumes` errors. But the record still exists, still says
`failed`, and still names the interrupt: nothing is silently wrong. Dimension A reached
low independently and B reached medium; the corroboration is not evidence, and the
weaker of the two ratings is the defensible one. Low.
