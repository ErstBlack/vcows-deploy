# Issue #81 — `destroy` warns "was deleted" for disks it did not delete

Reverified at `aed962d` on branch `lane/destroy-warning`. Raw transcripts:
`docs/review-destroy-warning/reverify/RX-A1.txt` (the defect and the fix) and
`RX-A2-and-90.txt` (the two `#90` items this lane also owns).

Everything below ran against `tests/fake_libvirt` in a `mktemp`-style scratch copy
outside the worktree. **No connection was opened to the rig or to any other
libvirt host.** `.tools/` was excluded from the copy; it is a symlink into the
main checkout.

## 1. Reverification verdict

**CONFIRMED, unchanged from the review, at HEAD rather than at the review's pin.**

`orchestrator/` is byte-identical to the review pin — `git diff --stat 672a500
HEAD -- orchestrator/` is empty — so `destroy.py:553-557` is still the loop the
issue names, and the recorded reproduction was re-driven rather than trusted.

Shape 1, the ghost target whose volumes are already gone, through the real
`destroy.destroy()`:

```
pool.deleted = []
destroyed    = []
skipped      = ['app01', '/pool/app01.qcow2', '/pool/app01-seed.iso']
problem      = warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
problem      = warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
failed       = False
```

Nothing was unlinked and two warnings say two files were.

Shape 2, `vol.delete` raising code 38 so `_fail` fires. `str(DestroyError)` is
what `main` prints and what `run.json["error"]` carries, and it contradicts
itself about the same path:

```
error [app01]: could not delete /pool/app01.qcow2: cannot unlink file '/pool/app01.qcow2': Permission denied
  warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
```

End to end, `cli.main(["destroy", …, "--yes"])` with shape 1's `Outcome`, one
`run.json`:

```
outcome   = 'partial'
destroyed = []
problem   = warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
problem   = warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
```

The other direction also reproduces, and it is what stops this from being a
"delete the warning" fix. A ghost target whose volumes **are** still present
deletes both and the two warnings are true:

```
== shape 1-ordinary: ghost target, both volumes STILL PRESENT ==
pool.deleted = ['app01.qcow2', 'app01-seed.iso']
destroyed    = ['/pool/app01.qcow2', '/pool/app01-seed.iso']
problem      = warning [app01]: /pool/app01.qcow2 was deleted on its name alone: ...
problem      = warning [app01]: /pool/app01-seed.iso was deleted on its name alone: ...
```

## 2. Anchor table

All re-read at `aed962d`. All hold.

| anchor | state |
|---|---|
| `destroy.py:553-557` — the `for path in target.disks` loop | ok, exact |
| `:554` `if _deletable(...)`, `:555` `if vanished:`, `:556` `_deleted_on_name_alone(...)`, `:557` `_delete_volume(...)` | ok |
| `destroy.py:459-484` — `_deleted_on_name_alone`, message at `:477-484` | ok |
| `destroy.py:462-464` — the `df60f74` sentence | ok (`#90`, section 2 of the companion plan) |
| `destroy.py:474` — the `preflight.py:39-42` citation | ok (`#90`) |
| `destroy.py:183-215` — `_delete_volume`, three outcomes, one caller | ok |
| `destroy.py:17-19` — the module docstring's crash window | ok |
| `tests/test_libvirt_destroy.py:159-171` — `..._still_has_its_disks_collected` | ok |
| `tests/test_libvirt_destroy.py:174-206` — the `#9` warning test | ok |
| `tests/test_libvirt_destroy.py:209-221` — the `#9` live-target test | ok |
| `orchestrator/cli.py:505-510` — targets come from `discovered.vms` only | ok |
| `orchestrator/backends/base.py:211` — "a domain already gone is a crash-window resume" | ok |
| `2f8ebe2` body: "every delete taken in it carries a warning" | ok, quoted verbatim below |
| `git diff 672a500 HEAD -- orchestrator/` | empty |

## 3. Corrections to the issue body

Nothing in `#81` is wrong. Three things it compresses, which the fix has to
handle and the issue does not say:

1. **"Both shapes reproduced"** is followed by shape 1's numbers only. Shape 2 —
   the delete that is *attempted and refused* — has a different consequence: the
   run is fatal, and the contradiction lands in `str(DestroyError)` and
   `run.json["error"]` rather than in the problems list. It is why the fix cannot
   be "warn unless the path was skipped"; see section 7.
2. **The legitimate case is not named.** A ghost target whose volumes are still
   present is exactly what `#9` was written for, and there the warning is true
   and must survive. Measured above and pinned by
   `test_libvirt_destroy.py:174`, which `#9` proved can fail (`assert 0 == 2`)
   and which still fails that way under an over-broad fix (mutation M3).
3. **`#9`'s intent, verified rather than asserted.** `2f8ebe2` — the commit that
   closed `#9` — reads: *"So the branch is tracked and **every delete taken in
   it** carries a warning naming the path, the domain, and what the evidence
   actually was."* The warning was specified to accompany a delete. Emitting it
   for the two `_delete_volume` outcomes that are not deletes is a gap in the
   implementation, not a design choice, and no commit or doc records the
   contrary.

## 4. The defect (mechanism)

`_delete_volume` (`destroy.py:183-215`) has three exits and only one of them is a
delete:

* `vol.delete(0)` returns → `out.destroyed.append(path)` (`:215`)
* `libvirtError` with `ERR_NO_STORAGE_VOL` → `out.skipped.append(path)` (`:211`)
* any other code → `_fail(...)`, a fatal `Problem` (`:213`)

The vanished branch calls the warning **before** that call, so it fires for all
three:

```python
        for path in target.disks:
            if _deletable(path, target, claimed, out):
                if vanished:
                    _deleted_on_name_alone(out, target, path)     # :555-556
                _delete_volume(session, path, target.name, out)   # :557
```

The message is past tense and unconditional: `"{path} was deleted on its name
alone: domain {target.name!r} was already gone …"` (`:479-481`).

**The pairing that produces the false line is the ordinary one, not the exotic
one.** `vanished` is set when `lookupByUUIDString` raises `ERR_NO_DOMAIN`. The
commonest way a domain stops existing between preflight and teardown is a
teardown by somebody else, and a teardown takes the disks with the domain — so
`NO_DOMAIN` on the target and `NO_STORAGE_VOL` on every one of its paths is the
*expected* combination. `#9`'s own scenario, a second operator who destroys and
then re-*deploys* inside the first operator's `input()` pause, is the rarer of
the two.

Consequence, and why it is worth a code change rather than a note: the run
directory is what an air-gapped site ships back (`cli.py:216`), `#9` existed to
give the operator in that race something to correlate against, and one `run.json`
now says `destroyed: []` and "two files were deleted" at once. `destroy.py:24`
states the rule the artifact breaks: "Every object's outcome is reported."

Neither `#9` test can see it. `test_libvirt_destroy.py:174` and `:209` both stock
the pool, so every path they drive resolves and deletes.

## 5. The fix

Emit the warning only when the delete happened. This is the finder's recorded
candidate (`docs/review-2026-08-31/finders/A-destroy-path.md` §RX-A1, "record
`len(out.destroyed)` before the `_delete_volume` call and call
`_deleted_on_name_alone` after it only if the count rose"), rebuilt and
re-measured here rather than taken on trust.

```python
        for path in target.disks:
            if _deletable(path, target, claimed, out):
                # After the call, not before it. `_delete_volume` has three
                # outcomes and only one of them is a delete; the warning is a
                # report of the delete, so it has to know which one happened.
                before = len(out.destroyed)
                _delete_volume(session, path, target.name, out)
                if vanished and len(out.destroyed) > before:
                    _deleted_on_name_alone(out, target, path)
```

`+6 / −2` in one function. Nothing else in `orchestrator/` changes: no signature,
no new helper, no new module, no message text, no severity.

Inside the loop the only writer of `out.destroyed` is `_delete_volume`, so the
delta across that one call means exactly "this volume was unlinked". The domain's
own `out.destroyed.append(target.name)` is at `:551`, above the loop.

**It does not regress the crash-window resume `#9` and `df60f74` deliberately
preserved.** Three ways I know:

1. **What the resume is.** `df60f74`'s body: *"The crash-window resume is
   deliberately preserved. A domain that is gone still has its disks collected,
   which is what makes a teardown interrupted between undefine and delete
   finishable by re-running."* `2f8ebe2` repeats it verbatim.
   `orchestrator/backends/base.py:211` carries the same statement in the
   `Outcome.skipped` docstring, and `docs/findings.md:87` states the mechanism —
   a vanished target's recorded paths are checked against every path the host's
   other domains claim and against the two names its marker entitles it to own,
   instead of being re-read from a live document. Nothing in that chain mentions
   the warning.
2. **What the patch touches.** Only whether a `Problem.warning` is appended.
   `_deletable` still decides, `_delete_volume` is still called for every path
   the vanished branch would have called it for, and `out.destroyed`,
   `out.skipped`, `out.failed` and the control flow are untouched.
3. **Measured, both windows.** Ghost target with both volumes present (the
   resume): `pool.deleted == ['app01.qcow2', 'app01-seed.iso']` and both warnings
   still emitted, byte-identical before and after. The module docstring's own
   crash window (`destroy.py:17-19` — `destroyFlags` ran, the process died,
   `undefineFlags` did not; the domain is off, still defined, still marked) is
   resumed through the live `else` branch where `vanished` is `False`:
   identical before and after, `dom.log == ['undefine:55']` with no `destroy`
   entry because `_stop` short-circuits on `isActive() == False` (`destroy.py:124`).
   `test_a_domain_already_gone_still_has_its_disks_collected` — the test that
   pins the resume — passes unchanged.

### Rejected: `_delete_volume` returns `bool`

Built and measured, not argued about.

```python
                if _delete_volume(session, path, target.name, out) and vanished:
                    _deleted_on_name_alone(out, target, path)
```

with `-> bool`, two `return False`, one `return True`. **Behaviourally
identical**: 84 passed on the destroy and CLI suites, and all five shapes produce
the same output as the recommended patch. It costs `+5 / −5` across two
functions, changes a contract with one caller, and would need a docstring
sentence in `_delete_volume` saying what the bool means. Same behaviour, more
surface, so it loses.

### Rejected: deleting or rewording the warning

`#9` is closed and the warning is right in the case it was written for; section 1
shows that case still fires. Rewording the past tense ("may have been deleted")
would make every line vague to fix the third of them that are wrong.

## 6. Surface cost

| file | change | lines |
|---|---|---|
| `orchestrator/backends/libvirt/destroy.py` | the loop above, plus its three-line comment | +6 / −2 |
| `tests/test_libvirt_destroy.py` | two tests, section 7 | +38 / −0 |
| `tests/fake_libvirt.py` | one `*_error` hook, section 7 | +5 / −0 |

No new file, no new function, no new module-level name, no gate change, no
`justfile` change, no doc outside `docs/plans/` and `docs/review-destroy-warning/`.

This lane's two `#90` items correct the docstring of `_deleted_on_name_alone` —
the same function whose call site this patch moves — and one docstring in
`tests/test_libvirt_destroy.py`. Per `CLAUDE.md` ("Related issues that touch the
same file land as one branch rather than piecemeal") they ship as one branch and
one commit with this. Measured together on top of this patch, the branch is
`destroy.py` `+12 / −6`, `tests/test_libvirt_destroy.py` `+44 / −3`,
`tests/fake_libvirt.py` `+5 / −0`. See
`docs/plans/issue-90-destroy-comments.md`; `#90` itself stays open, since nine of
its eleven items are outside this lane.

## 7. The failing test

There is no existing test that fails before and passes after: `#9`'s two tests
both stock the pool, so neither reaches the outcome the fix is about. Two are
added.

**Primary — `test_a_vanished_target_whose_disks_are_already_gone_is_not_said_to_be_deleted`**,
placed after `test_a_live_target_deletes_its_disks_without_the_name_alone_warning`
(`:209-221`) so the three vanished-branch tests read as a set.

```python
def test_a_vanished_target_whose_disks_are_already_gone_is_not_said_to_be_deleted():
    """#81. The other half of the pair above, and the commoner one: the domain
    went and took its disks with it, so every path resolves NO_STORAGE_VOL and
    nothing is unlinked. The name-alone warning reports a delete, so with no
    delete there is nothing to report and `destroyed` and `problems` must agree."""
    pool = FakePool("images", {})
    conn = FakeConnection(domains=[], pools=[pool])
    ghost = Existing(
        name="app01",
        id="00000000-0000-0000-0000-000000000000",
        marker=Marker.for_vm("app01", "lab-a"),
        disks=("/pool/app01.qcow2", "/pool/app01-seed.iso"),
    )

    out = d.destroy({}, conn, [ghost])

    assert pool.deleted == []
    assert out.destroyed == []
    assert out.skipped == ["app01", "/pool/app01.qcow2", "/pool/app01-seed.iso"]
    assert out.problems == []
    assert not out.failed
```

Against the unfixed line: `FAILED … - assert [Problem(seve...here='app01')] == []`.

**Second — `test_a_vanished_targets_failed_delete_is_not_also_reported_as_a_delete`**,
covering shape 2. It asserts on `str(DestroyError)` because that string is what
`main` prints and what `run.json["error"]` carries:

```python
    said = str(caught.value)
    assert "could not delete /pool/app01.qcow2" in said
    assert "name alone" not in said
```

Against the unfixed line: `FAILED … - AssertionError: assert 'name alone' not in
'error [app0...till owns it'`.

It needs one hook the fake does not have: `FakeVolume.delete` is the only call in
`tests/fake_libvirt.py` with no settable error, against that file's own stated
convention ("Every call a caller might have to survive carries a settable
`*_error` attribute"). Added as `FakePool.volume_delete_error`, exactly parallel
to the existing `volume_xml_error` ("Raised by every volume this pool hands out,
not by the pool itself") — `+5 / −0`.

**It earns its cost, measured.** Mutation M4 is the plausible alternative fix,
"warn unless the path resolved as already gone":

```python
                if vanished and path not in out.skipped:
```

M4 passes every pre-existing test **and the primary new test**, and only the
second one catches it: `1 failed, 40 passed`. Without it, the shape the issue
calls out as the self-contradicting `DestroyError` is unpinned.

**Proved they can fail, by mutating the production line.** Four mutations of the
patched `destroy.py`, each re-run against the full destroy suite:

| mutation | result |
|---|---|
| `if vanished:` (i.e. HEAD) | both new tests fail — `2 failed, 39 passed` |
| `len(out.destroyed) >= before` (always true) | both new tests fail — `2 failed, 39 passed` |
| `len(out.destroyed) == before` (inverted) | **`#9`'s own test fails too**: `test_a_vanished_targets_disks_are_deleted_but_the_weaker_evidence_is_said - assert 0 == 2` — the same failure `2f8ebe2` recorded proving it. `3 failed, 38 passed` |
| `path not in out.skipped` (M4) | second new test only — `1 failed, 40 passed` |
| patch restored | `41 passed` |

The inverted mutation is the one that matters for section 5: it shows the fix
cannot silence the *legitimate* warning without `#9`'s test noticing.

## 8. Verification

Scratch copy of `orchestrator/ tests/ container/` + root files, `.tools/`
excluded, driven with the worktree's `.venv`. The scratch tree skips the ten
`test_tofu_module.py` tests that need `.tools/tofu-mirror`, so its numbers sit ten
below the worktree's — established by running the unmodified copy first rather
than inferred:

| tree | pytest |
|---|---|
| scratch, unmodified (= `aed962d`) | **401 passed, 35 skipped** |
| scratch, patch + two new tests | **403 passed, 35 skipped** |
| worktree baseline at `aed962d` (given) | 411 passed, 25 skipped |

`401 + 2 = 403`, and `411 + 25 = 436`, `403 + 35 = 438`: the delta is the two new
tests and nothing else.

On the patched three files: `ruff check` — All checks passed; `ruff format
--check` — 3 files already formatted; `ty check` — All checks passed;
`tests/test_gates.py` (the AST walk that rejects a bare skip) — 16 passed.

Behaviour, before against after, all five shapes:

| shape | before | after |
|---|---|---|
| ghost, both volumes gone | `destroyed=[]`, **2 warnings** | `destroyed=[]`, **0 warnings** |
| ghost, both volumes present | deletes both, 2 warnings | deletes both, **2 warnings** — unchanged |
| ghost, `vol.delete` → code 38 | 2 errors **+ 2 warnings** | 2 errors, **0 warnings** |
| crash-window resume (off, still defined) | deletes, 0 warnings | identical |
| live target | deletes, 0 warnings | identical |

`cli.main(["destroy", …, "--yes"])` on shape 1 after the patch: `outcome:
"partial"`, `destroyed: []`, `skipped` the three objects, and **no problems**.
Exit code 1 both before and after — three objects vcows was asked to remove are
still unaccounted for, which `base.py:209-215` says must be non-zero, and the fix
does not change that.

Full `just check` on the assembled branch is the merge gate, not something this
plan ran: the plan is not allowed to modify `orchestrator/` or `tests/` in the
worktree.

## 9. Non-goals

* **`vcows destroy` against the rig.** Nothing here opened a libvirt connection,
  and nothing here needs one: the change is a conditional on a Python-side
  counter, and `tests/fake_libvirt` is the surface `#9` itself was measured on.
* **`#9`'s stronger fix** — comparing a volume's backing store or creation time
  against what preflight resolved. `#9` files it as the alternative and
  `2f8ebe2` declines it explicitly; it is a different change with its own design.
* **Removing the vanished branch.** It is deliberate (`df60f74`), and dropping a
  vanished target's disks is a guaranteed leak.
* **RX-A3, the `KeyboardInterrupt` gap** (`cli.py:552`). Different file,
  different lane, and the finder's own conclusion is that the cheap fix buys
  nothing.
* **`docs/findings.md`.** `:87` describes what the vanished branch checks, not
  what it reports, and is still true after the patch. Nothing outside
  `docs/review-2026-08-31/` mentions the warning at all — measured by grep.
* **Widening the fake.** One `*_error` hook on the one call that lacked it. No
  other change to `tests/fake_libvirt.py`.
