# Issue #90 — the two items in `destroy.py`

**This lane owns two of `#90`'s eleven items.** `#90` collects comment, citation
and note drift across all seven dimensions of the 2026-08-31 review; the other
nine live in `docs/cve-baseline.json`, `scripts/lib.sh`,
`.github/workflows/image.yml`, `.gitlab-ci.yml`, `container/entrypoint.py`,
`orchestrator/backends/libvirt/tofu/variables.tf`, `orchestrator/cli.py` (twice)
and `docs/findings.md`, plus the stale baseline row. Nothing below touches any of
them, and `#90` cannot be closed by this branch.

The two here are `destroy.py:474` and `destroy.py:462-464`. Both are docstring
text in the same function, `_deleted_on_name_alone` (`destroy.py:459-484`) — the
same function `#81` changes the call site of — so per `CLAUDE.md` ("Related
issues that touch the same file land as one branch rather than piecemeal") these
land with `docs/archive/plans/issue-81.md` as one branch and one commit.

Reverified at `aed962d`. `orchestrator/` is unchanged since the review pin
`672a500` (`git diff --stat 672a500 HEAD -- orchestrator/` is empty). Raw
transcript: `docs/review/destroy-warning/reverify/RX-A2-and-90.txt`.

---

## Item 1 — `destroy.py:474` cites the import block

### Verdict: CONFIRMED, and `#90`'s "wrong number, not drift" is correct.

`destroy.py:473-475` reads:

```
473:     A warning rather than an error: this must not stop the teardown, and
474:     ``preflight.py:39-42`` requires a skip to name the object and what the skip
475:     cost. This is the same obligation for a delete taken on less.
```

`preflight.py:39-42` at HEAD:

```
39: from contextlib import contextmanager
40: from typing import Any
41: from xml.etree import ElementTree as ET
42:
```

The rule is in the module docstring. `preflight.py:23-24` is the sentence — "A
skip is therefore always a ``Problem`` naming the object and what the skip cost"
— inside the paragraph at `:21-26`.

**Not drift, verified three ways.**

1. `git log -S'preflight.py:39-42' -- orchestrator/backends/libvirt/destroy.py`
   returns exactly one commit: `2f8ebe2`, which introduced the citation.
2. `git show 2f8ebe2:orchestrator/backends/libvirt/preflight.py | sed -n '36,45p'`
   returns the same four import lines. So does
   `git show 2f8ebe2^:…` — the parent. The number was already the import block
   when it was written, and the file did not move under it.
3. `git show 2f8ebe2:…preflight.py | sed -n '20,27p'` returns the rule paragraph
   at exactly `:21-26`, as it is today. Nothing shifted.

The same wrong number is in two commit bodies:

* `2f8ebe2`, line 64 of the body: "preflight.py:39-42 already requires a skip to
  name the object and what the skip cost"
* `e4371ff`, line 76 of the body: "preflight.py:39-42 governs here and requires
  the opposite: a skip must name…"

`df60f74`'s body cites no `preflight.py:` line at all (`grep -c` → 0), so it is
two bodies plus the code: three occurrences of one number that was never right.
`e4371ff` cites `preflight.py:28-33` correctly in the same body for the D13
paragraph, so this is one wrong number rather than a habit.

Commit bodies are history and are not edited (`docs/review/2026-08-30/REVIEW.md:353-360`
arbitrates that dated records may go stale). Only the code changes.

### The fix

One number in one docstring:

```diff
-    ``preflight.py:39-42`` requires a skip to name the object and what the skip
+    ``preflight.py:23-24`` requires a skip to name the object and what the skip
```

`+1 / −1`. `:23-24` rather than `:21-26` because the sentence is what is being
cited, and it is what `#90` and `docs/review/2026-08-31/verify/AB-lows.md`
independently settled on. (Finder A wrote `:21-26`, the whole paragraph;
`AB-lows.md` reconciled the two and chose `:23-24`. Both are true statements
about the file; the tighter one is the citation.)

---

## Item 2 — `destroy.py:462-464` justifies the branch with a window it cannot reach

### Verdict: CONFIRMED. The sentence is false; the branch it defends is not.

`destroy.py:462-464`:

> The vanished branch is deliberate and stays: a domain gone between preflight
> and teardown still has its disks collected, **which is what makes a teardown
> interrupted between undefine and delete finishable by re-running (df60f74).**

**The unreachability, by construction rather than by argument.** Built the world
the sentence names — a teardown interrupted after `undefineFlags` and before the
disks went, so the domain is undefined and the overlay is still in the pool — and
ran the real `preflight()` against `tests/fake_libvirt`:

```
preflight vms       = ()
preflight problem   = error [app01]: volume 'app01.qcow2' exists but no domain on this
                      host references it. A create interrupted before its domain was
                      defined leaves exactly this, ...
destroy targets     = []
```

The mechanism: `preflight._domains` (`preflight.py:144-186`) builds every
`Existing` from `conn.listAllDomains(0)` (`:162`), and an undefined domain is not
returned. `cmd_destroy`'s targets come from `discovered.vms` and nowhere else
(`cli.py:505-510`), so with no `Existing` there is no target, the `for target in
targets` loop never runs, and the vanished branch is never entered. The leaked
overlay is still named, by `orphan_volumes`, in the same run.

**But the branch is deliberate, and the decision stands.** `df60f74`'s body:
"The crash-window resume is deliberately preserved. A domain that is gone still
has its disks collected, which is what makes a teardown interrupted between
undefine and delete finishable by re-running." `2f8ebe2` repeats it and adds the
reason that *is* sound: "Dropping those disks is a guaranteed leak, which is
worse than the race it would close." Nothing here reopens that. What changes is
the recorded justification: the leak argument is the real one, and the
post-undefine window is not.

**Which window each thing actually describes.** The module docstring
(`destroy.py:17-19`) names the destroy→undefine window: the domain is off, still
defined, still marked. That one is real and is resumed through the live `else`
branch — measured, a domain off but still defined goes `_reverify` → `_stop`
(short-circuits at `:124-125` on `isActive() == False`) → `_undefine`, with
`dom.log == ['undefine:55']` and no `destroy` entry. `:17-19` is correct and
needs no change.

### The fix

Two statements in this lane's files, plus one back-reference.

**A. `destroy.py:462-464`.** Replace the false justification with the true one
and name what the branch is not.

```
    The vanished branch is deliberate and stays: dropping a gone domain's
    recorded disks is a guaranteed leak, which is worse than the race collecting
    them opens (df60f74, 2f8ebe2). It is not the post-undefine crash window --
    an undefined domain is in no ``listAllDomains``, so ``preflight`` yields no
    target for it and ``orphan_volumes`` names that leak instead.
```

`+5 / −3`.

**B. `tests/test_libvirt_destroy.py:160-161`.**
`test_a_domain_already_gone_still_has_its_disks_collected` builds a ghost
(`domains=[]`) and asserts the disk is deleted. Its docstring says "The crash
window between destroy and undefine, resumed", which is the window the test does
*not* exercise — that window leaves the domain defined, so the test's own fixture
could not produce it.

```
    """A domain that vanished between preflight and this teardown still has its
    recorded disks collected: another operator's destroy inside our confirm
    window, or somebody who undefined it by hand and left the qcow2. Not the
    destroy/undefine crash window -- that leaves the domain defined, and it
    resumes through the live branch."""
```

`+5 / −2`.

**C. `tests/test_libvirt_destroy.py:175`.** The `#9` test's docstring opens "The
deletion stays -- it is the crash-window resume above", inheriting B's wrong
claim by reference. "it is the same vanished target as above" — `+1 / −1`.

Total for item 2: `+11 / −6`, no code change, no new surface, no behaviour
change. Both items together, applied on top of the `#81` patch and measured:
`destroy.py` `+6 / −4`, `tests/test_libvirt_destroy.py` `+6 / −3`.

### Correction to `#90`'s own wording, and one finding it missed

`#90` says the fix is "Two docstrings plus a test docstring." Measured, in this
lane's two files it is **one production docstring and two test docstrings**. The
count came from `docs/review/2026-08-31/verify/AB-mediums.md:145`, which
paraphrased finder A's "correct the two docstrings"
(`finders/A-destroy-path.md:107` — meaning `destroy.py:462-464` and
`test_libvirt_destroy.py:160`) as "two docstrings plus the test docstring". An
off-by-one introduced in transcription, not a missing target.

**Separately: a fourth statement carries the same false claim, and no review
named it.** `orchestrator/backends/base.py:211`, in `Outcome.skipped`'s
docstring:

> A domain already gone is a crash-window resume; a volume that would not
> resolve is a leak.

"A domain already gone" is `destroy.py:541`, the vanished branch's
`out.skipped.append(target.name)`. By the same construction above it is not a
crash-window resume: the destroy→undefine window leaves the domain defined, so it
never reaches this appender, and the post-undefine window produces no target at
all. `grep -rn "base.py:211\|crash-window resume" docs/review/2026-08-31/`
returns nothing — neither the finder nor the verifier looked at `base.py`.

`orchestrator/backends/base.py` is not this lane's file. **Filed here, not fixed
here.** It belongs either to whichever lane owns `base.py` or to a follow-up on
`#90`. If it is left, the corrected `destroy.py:462-464` and the uncorrected
`base.py:211` will contradict each other one directory apart.

---

## Verification

Nothing in this plan changes behaviour, so the check is that the numbers and
quotes are true and that the text still lints.

* Every `file:line` above re-read at `aed962d`; `git show` output for `2f8ebe2`,
  `2f8ebe2^`, `e4371ff` and `df60f74` captured verbatim in
  `docs/review/destroy-warning/reverify/RX-A2-and-90.txt`.
* The unreachability is a construction, not a claim: the transcript carries the
  script's output, `preflight vms = ()` and `destroy targets = []`.
* The replacement text above was applied in the scratch copy on top of the `#81`
  patch and gated: `ruff check` — All checks passed; `ruff format --check` — 2
  files already formatted; `ty check` — All checks passed; `pytest -q` —
  403 passed, 35 skipped, i.e. unchanged from `#81` alone. No new line exceeds
  88 columns; the three that do in `destroy.py` are the pre-existing
  `noqa: S314` pragmas ruff exempts.

## Non-goals

* The other nine `#90` items, and the stale `CVE-2026-58055` baseline row.
  `#90` stays open after this branch.
* `orchestrator/backends/base.py:211`. Found and recorded above; not this lane's
  file.
* `destroy.py:17-19`. Re-measured and correct.
* Removing or narrowing the vanished branch. `df60f74` and `2f8ebe2` decided it
  stays, and the measurement here supports the leak argument they gave —
  only not the window they named.
* Editing commit bodies. History is not rewritten to match a corrected number.
