# Scoped review — lane `destroy-warning`

**Input: `git diff origin/master...lane/destroy-warning` and nothing else.** One
commit, `08073ff`, three files:

```
 orchestrator/backends/libvirt/destroy.py | 12 +  6 -
 tests/fake_libvirt.py                    |  5 +  0 -
 tests/test_libvirt_destroy.py            | 52 +  3 -
```

It carries `#81` and two of the eleven items in the comment-drift issue, 90. The
plans it was written against are `docs/archive/plans/issue-81.md` and
`docs/archive/plans/issue-90-destroy-comments.md`; the raw reverification is
`reverify/RX-A1.txt` and `reverify/RX-A2-and-90.txt`.

Everything below was run in the worktree against `tests/fake_libvirt`. No libvirt
connection was opened by anything in this review, and no rig, image or smoke gate
was touched.

---

## Lens 1 — did it do what the plan said

Four hunks, matched line for line against the plans.

| hunk | plan | verdict |
|---|---|---|
| `destroy.py` loop, `:555-563` | `issue-81.md` §5, including its three-line comment | verbatim |
| `destroy.py:462-466`, the vanished-branch justification | `issue-90-destroy-comments.md` item 2 fix A | verbatim |
| `destroy.py:476`, `preflight.py:39-42` → `:23-24` | item 1 fix | verbatim |
| `tests/fake_libvirt.py`, `FakePool.volume_delete_error` | `issue-81.md` §7 and `RX-A1.txt` §5 | verbatim |
| `tests/test_libvirt_destroy.py:160-164` docstring | item 2 fix B | verbatim |
| `tests/test_libvirt_destroy.py:178` docstring | item 2 fix C | verbatim |
| the two new tests, `:227-270` | `RX-A1.txt` §5 | verbatim |

**Nothing in the diff is undescribed by a plan.** No file outside the three the
plans name is touched — in particular not `orchestrator/cli.py`,
`orchestrator/backends/base.py`, `scripts/` or `tests/conftest.py`, all of which
belong to other lanes, and not `docs/cve-baseline.json`.

Re-derived rather than trusted, in the worktree:

* **The recommended patch, all three vanished shapes**, before against after.
  Ghost with both volumes already gone: `destroyed=[]`, two name-alone warnings →
  zero. Ghost with both volumes present: deletes both, two warnings → two
  warnings, unchanged. Ghost whose `vol.delete` raises code 38: two fatal errors,
  two warnings → zero, and `str(DestroyError)` no longer contains "name alone".
* **End to end.** `cli.main(["destroy", …, "--yes"])` on shape 1, real
  `destroy.destroy()` behind a fake connection: `outcome "partial"`,
  `destroyed []`, three objects in `skipped`, exit 1 before and after. `run.json`
  carried two false warnings before and carries none after.
* **The rejected alternative was built, not argued.** `_delete_volume -> bool`
  with `if _delete_volume(...) and vanished:` is identical on all three shapes and
  gives `84 passed` across `tests/test_libvirt_destroy.py` and
  `tests/test_cli.py`. Against `origin/master` it is `+11/−9` on `destroy.py`
  where the shipped patch is `+12/−6`; net of the two `#90` docstring hunks
  (`+6/−4`, common to both) the code is `+5/−5` across two functions versus
  `+6/−2` in one. The plan's figure holds.
* **The crash-window resume is not regressed, proved not asserted.**
  `destroy.py:17-19`'s window — `destroyFlags` ran, the process died,
  `undefineFlags` did not, so the domain is off, still defined, still marked —
  resumes through the live `else` branch where `vanished` is `False`, which the
  new condition cannot reach. Driven end to end: `dom.log == ['undefine:55']`
  with no `destroy` entry, `deleted=['app01.qcow2']`,
  `destroyed=['app01', '/pool/app01.qcow2']`, no problems, byte-identical before
  and after. The live-target path is likewise identical.
* **The `#90` item 2 unreachability is a construction.** Built the
  post-undefine world — domain undefined, overlay still in the pool — and ran the
  real `preflight()`: `preflight vms = ()`, `destroy targets = []`, and the run
  reports `error [app01]: volume 'app01.qcow2' exists but no domain on this host
  references it`. The corrected docstring says exactly this.
* **`#90` item 1 is a wrong number, not drift.** `git log -S'preflight.py:39-42'`
  on `destroy.py` returns exactly `2f8ebe2`, and
  `git show 2f8ebe2:…/preflight.py` gives the same four import lines at `:39-42`
  and the same rule sentence at `:23-24`. Nothing moved under the citation.

### L-R1 — the plan's surface cost for the test file is low by 8

`issue-81.md` §6 predicts `tests/test_libvirt_destroy.py +44/−3`. Measured on the
landed diff it is **`+52/−3`**. The other two files match exactly (`destroy.py`
`+12/−6`, `tests/fake_libvirt.py` `+5/−0`).

Cause, measured: `RX-A1.txt` counted added lines with `grep -c '^+[^+]'`, which
does not match an added blank line (`^+$`). On this diff that command returns
`44` and `grep -c '^+$'` returns `8`. The eight are the blank lines separating and
inside the two new tests. Not a scope change and not extra code — a counting
method that undercounts every diff it is applied to. Recorded so the next lane
does not read `+44` as a budget the implementation overran.

---

## Lens 2 — do the new tests have teeth

Two new assertions ship. Both were proved able to fail by reverting the
production change alone and leaving the tests in place:

```
$ git checkout origin/master -- orchestrator/backends/libvirt/destroy.py
$ pytest -q tests/test_libvirt_destroy.py
FAILED …::test_a_vanished_target_whose_disks_are_already_gone_is_not_said_to_be_deleted
FAILED …::test_a_vanished_targets_failed_delete_is_not_also_reported_as_a_delete
2 failed, 39 passed
$ pytest -q                      # the whole suite
2 failed, 412 passed, 25 skipped
$ git checkout HEAD -- orchestrator/backends/libvirt/destroy.py
41 passed
```

Four mutations of the patched line, each re-run against
`tests/test_libvirt_destroy.py`:

| mutation | result | which tests |
|---|---|---|
| `if vanished:` (the old order) | `2 failed, 39 passed` | both new |
| `len(out.destroyed) >= before` (always true) | `2 failed, 39 passed` | both new |
| `len(out.destroyed) == before` (inverted) | `3 failed, 38 passed` | both new **and `#9`'s own** |
| `vanished and path not in out.skipped` (M4) | `1 failed, 40 passed` | the second only |
| restored | `41 passed` | — |

Two of these carry the argument.

**The inverted mutation is the control that must fail `#9`'s own test, and it
does.** `test_a_vanished_targets_disks_are_deleted_but_the_weaker_evidence_is_said`
fails with `assert 0 == 2` (`+ where 0 = len([])`) — the same failure `2f8ebe2`
recorded when it proved that test. So the fix demonstrably cannot silence the
legitimate warning without `#9` noticing.

**M4 is why the second test exists.** `if vanished and path not in out.skipped:`
is the plausible alternative reading of the defect — "warn unless the path
resolved as already gone" — and it passes every pre-existing test **and the
primary new test**. Only
`test_a_vanished_targets_failed_delete_is_not_also_reported_as_a_delete` catches
it, because M4 still warns on the `_fail` exit. Without the second test the shape
`#81` names as the self-contradicting `DestroyError` would be unpinned. The second
test earns its cost by measurement, not by symmetry.

**The two docstring corrections have no test and should not.** They are
documentation with no runtime consequence, which is the ground
`docs/review/2026-08-31/verify/AB-mediums.md:11` downgraded the finding on. A test
that greps a docstring would be surface for nothing. Their check is that the
statements are true, which Lens 1 measured.

**One thing the new tests do not cover, stated so it is not mistaken for
coverage.** Neither asserts against a real libvirtd. The change is a conditional
on a Python-side counter and `tests/fake_libvirt` is the surface `#9` itself was
measured on, so this is the right level — but a green run here says nothing about
`vol.delete`'s behaviour on the rig.

---

## Lens 3 — what moved

### Line numbers

| anchor | before | after |
|---|---|---|
| `destroy.py` `_deleted_on_name_alone` def | `:459` | `:459` (unmoved) |
| `destroy.py` vanished-branch sentence | `:462-464` | `:462-466` |
| `destroy.py` `preflight.py` citation | `:474` | `:476` |
| `destroy.py` warning message text | `:477-484` | `:479-486` |
| `destroy.py` `def destroy(` | `:487` | `:489` |
| `destroy.py` `out.skipped.append(target.name)` | `:541` | `:543` |
| `destroy.py` `out.destroyed.append(target.name)` | `:551` | `:553` |
| `destroy.py` `for path in target.disks` loop | `:553-557` | `:555-563` |
| `test_libvirt_destroy.py` `…_still_has_its_disks_collected` | `:159` | `:159` (unmoved) |
| its docstring | `:160-161` | `:160-164` |
| `test_libvirt_destroy.py` `#9` warning test | `:174` | `:177` |
| `test_libvirt_destroy.py` `#9` live-target test | `:209` | `:212` |
| `test_libvirt_destroy.py` `…_lookup_that_failed_…` | `:224` | `:273` |
| `fake_libvirt.py` `class FakePool` | `:56` | `:58` |
| `fake_libvirt.py` `class FakeDomain` | `:115` | `:120` |
| `fake_libvirt.py` `class FakeConnection` | `:168` | `:173` |

Everything at or below `destroy.py:462` shifts `+2`; everything at or below the
loop shifts `+4` more.

### Comments and citations this change rewrote

* `destroy.py:474` → `:476`: `preflight.py:39-42` → `preflight.py:23-24`. The
  same wrong number remains in the `2f8ebe2` and `e4371ff` commit bodies. History
  is not rewritten; `docs/review/2026-08-30/REVIEW.md:353-360` arbitrates that
  dated records may go stale.
* `destroy.py:462-464` → `:462-466`: the `df60f74` post-undefine justification
  replaced by the leak argument, which `df60f74` and `2f8ebe2` also gave.
* `test_libvirt_destroy.py:160-161` → `:160-164` and `:175` → `:178`: both
  crash-window claims removed.

### Does anything else point at what moved

Swept the repo for every `destroy.py:NN`, `test_libvirt_destroy.py:NN` and
`fake_libvirt.py:NN` citation at or below the first moved line, excluding this
lane's own plans and transcripts.

**No code, and no live document, cites a line this change moved.**

* The only `file:line` citation of `destroy.py` outside `docs/` is
  `tests/libvirt-module.tftest.hcl:39`, which names `:440-445` — the `owned` set
  in `_deletable`, above the first hunk and **unmoved**.
* `CLAUDE.md` cites `tests/fake_libvirt.py:25` (the module-scope
  `import libvirt`), which is above every hunk and unmoved. It cites no line of
  `destroy.py`.
* `docs/findings.md` cites no `destroy.py` line number at all. `:87` describes
  what the vanished branch *checks*, not what it *reports*, and is still true.
* Every remaining hit is inside `docs/review/2026-08-29/`,
  `docs/review/2026-08-30/` or `docs/review/2026-08-31/` — dated records, stale by
  the same arbitration above. The ones a later lane will meet first:
  `docs/review/2026-08-31/verify/AB-mediums.md:10,16` and
  `finders/A-destroy-path.md:27` (`:553-557`), `AB-lows.md:24,28` and
  `finders/A-destroy-path.md:134` (`:474`),
  `ledger/a-issues-8-40.md:18` (`:459-483`, called at `:556`),
  `docs/review/2026-08-30/ledger/s1-s6.md:11,56` (`:516-529`, `:486-498`,
  `test_libvirt_destroy.py:174-194`, `:466-478`).

### L-R2 — `base.py:211` now contradicts the corrected docstring

`orchestrator/backends/base.py:211`, in `Outcome.skipped`'s docstring, still
reads "A domain already gone is a crash-window resume; a volume that would not
resolve is a leak." That first clause is `destroy.py:543`, this branch's
`out.skipped.append(target.name)`, and by the construction in Lens 1 it is not a
crash-window resume. `base.py` is another lane's file and this is filed as issue
94, so it is **not fixed here** — recorded because the corrected
`destroy.py:462-466` and the uncorrected `base.py:211` now say opposite things one
directory apart.

---

## Ledger

| id | item | status |
|---|---|---|
| L-R1 | `issue-81.md` §6's `tests/test_libvirt_destroy.py +44/−3` is low by 8; landed at `+52/−3`, because the transcript counted with `grep -c '^+[^+]'` | **raised**, cause measured |
| L-R2 | `base.py:211` carries the claim `destroy.py:462-466` just corrected | **raised**, out of lane, filed as issue 94 |
| L-C1 | The defect is ordering in two lines; the warning fires on all three `_delete_volume` exits | **confirmed** — three shapes, before and after |
| L-C2 | The crash-window resume is not regressed; `vanished` is `False` on that path | **confirmed** — driven end to end, byte-identical |
| L-C3 | The second test earns its cost: M4 passes every other test including the primary new one | **confirmed** — `1 failed, 40 passed` |
| L-C4 | The inverted mutation fails `#9`'s own test with `assert 0 == 2` | **confirmed** — same failure `2f8ebe2` recorded |
| L-C5 | `#90` item 1 is a wrong number, not drift | **confirmed** — `git log -S` returns only `2f8ebe2`; `:39-42` was the imports then too |
| L-C6 | `#90` item 2's window is unreachable; the branch itself is still right | **confirmed** — `preflight vms = ()`, `destroy targets = []`; the leak argument stands |
| L-F1 | `#90`'s "two docstrings plus a test docstring" | **refuted** — one production docstring, two test docstrings; the three came from `AB-mediums.md:145` paraphrasing `finders/A-destroy-path.md:107` |
| L-F2 | "Any live document points at a line this change moved" | **refuted** — the only non-`docs/` citation is `tests/libvirt-module.tftest.hcl:39`, at `:440-445`, unmoved |
| L-F3 | `_delete_volume -> bool` as the fix | **refuted** — identical behaviour on three shapes and `84 passed`, at `+5/−5` across two functions instead of `+6/−2` in one |
| L-D1 | The two docstring corrections as a testable change | **downgraded** — documentation with no runtime consequence, which is the ground `AB-mediums.md:11` downgraded the finding on; no test is added and none should be |
| L-D2 | The new tests as evidence about a real libvirtd | **downgraded** — `tests/fake_libvirt` only; a green run here says nothing about `vol.delete` on the rig |

`just check` on the landed branch: six lint gates ok, `ty` clean,
**414 passed, 25 skipped**, up from `412 passed, 25 skipped` on `origin/master` by
the two new tests and nothing else.
