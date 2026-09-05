# Phase 3 confirm — dimension A and B lows and nits

Confirmer, 2026-08-31. Target `origin/master` = `672a500`, read in the detached
worktree. Every `file:line` below was re-opened, including the ones the findings
assert are wrong. No tracked file was modified; the rig was not touched.

---

## RX-A3 — Ctrl-C mid-teardown records no `destroyed`/`skipped`

**DUPLICATE OF RX-B2.** Not re-verified independently, per the task instruction.

Recorded so the arbitration is reconstructable: the structural claim holds at
`672a500`. `orchestrator/cli.py:552` is `except Exception as exc:` (confirmed by
grep), which cannot see `KeyboardInterrupt`, so the `run.extra["destroyed"]` /
`["skipped"]` assignment at `:553-556` is bypassed. `_guard` does catch it —
`except BaseException as exc:` at `cli.py:258` — and `_record` (`:209-243`)
splats `run.extra`, which on that path holds only `problems` (set at `:527`).
The record therefore carries `outcome`, `error`, `decisions: []` and `problems`,
and no `destroyed`/`skipped`. The finder's own conclusion — no cheap fix, and
`except BaseException` buys nothing because the `Outcome` is a local inside
`destroy()` — is the right one. Whatever RX-B2's verifier concludes governs.

## RX-A4 — `destroy.py:474` cites the import block

**CONFIRMED · nit.**

`orchestrator/backends/libvirt/destroy.py:474` reads "``preflight.py:39-42``
requires a skip to name the object and what the skip cost".
`preflight.py:39-42` is four import lines:

```
39  from collections.abc import Iterator
40  from contextlib import contextmanager
41  from typing import Any
42  from xml.etree import ElementTree as ET
```

The rule is in the module docstring at `preflight.py:21-26`; the sentence itself
spans `:23-24` ("A skip is therefore always a ``Problem`` naming the object and
what the skip cost"). **Corrected citation: `preflight.py:23-24`** (`:21-26` for
the whole paragraph). The finder's replacement number is right.

Not recorded as deliberate. It is worse than the finder said: `39-42` was
*already* the import block when the citation was written. Both `2f8ebe2` and
`e4371ff` carry `preflight.py:39-42` in their commit bodies for this same rule,
and `git show <c>:orchestrator/backends/libvirt/preflight.py | sed -n '39,42p'`
at each returns the same four imports. This was never correct, so it is not
drift — it is a wrong number repeated three times, in a range whose `c124ffe`
closed `#28` ("drop the stale line numbers") for exactly this class.

Fix cost: one number in one docstring. File it.

## RX-B3 — a destroy never records the VMs it deliberately left alone

**CONFIRMED · low.**

`orchestrator/cli.py:528-537` is the `for e in others:` loop; it prints a `skip`
row via `_row` and writes nothing. The only `run.extra` keys on the destroy path
are `problems` (`:527`) and, on the raising branch only, `destroyed`/`skipped`
(`:554-556`). None of the three `_record` calls (`:542`, `:548`, `:575-581`)
mentions `others`. So a marked VM belonging to another deployment appears in no
shipped artifact.

Not recorded as deliberate. `docs/findings.md:121` establishes the *reporting*
obligation — "Marked VMs from other deployments are reported as found and
skipped, with their deployment names" — and is satisfied by the stdout row, but
it says nothing about the record, and it is the same argument `tofu.py:77-84`
makes for `Result.warnings`: the run directory is the copy that outlives the
terminal.

Fix cost: one key, `left_alone=[e.name for e in others]`, into `run.extra`
alongside the `problems` assignment already at `:527`. One line, no new type, no
new call site. Worth it. Low, not medium: `skipped: []` is not a false statement
in the record's own sense, so nothing currently misleads.

## RX-B4 — `cli.py:571`'s literal width 20

**REFUTED — recorded as deliberate in the commit that closed `#35`.**

The measurement is true. `_NAME_W = 20` at `cli.py:175`, `_row` at `:179-183`
covers `:188`, `:531` and `:539`, and `cli.py:571` is
`print(f"  {name:<20} skipped, not removed by this run")` — the last literal
width in the file, and it does have to line up under the `skip` rows.

But the `#35` commit body decides it explicitly, naming the line:

> `cli.py:567` is deliberately not folded in. `f"  {name:<20} skipped, not
> removed by this run"` looks like a fourth site but is not: `"skipped,"` is
> eight characters and does not fit the seven-wide verb column, so it is a name
> plus a sentence -- a two-column line -- and a shared helper would change its
> output. It is now the only literal width left in the file, which is correct.

(The commit's own `:567` has since drifted to `:571`; that is a commit message,
not code.) A documented deviation is refuted, not a nit.

Residual, recorded and not filed: the commit's stated reason answers *folding it
into `_row`*, which would change the output, and does not answer the finder's
narrower fix — `f"  {name:<{_NAME_W}} skipped, ..."`, which is byte-identical
today and keeps the two sites coupled. That is a real gap in the recorded
reasoning, but "it is now the only literal width left in the file, which is
correct" is a direct decision about this exact line, and re-opening it is
re-deriving settled work over one interpolation.

## RX-B5 — `entrypoint.py:189` cites `cli.py:670` for the umask

**DUPLICATE OF RX-G8 · confirmed true, low.**

Confirmed once, here, so G's verifier does not repeat it.
`container/entrypoint.py:189` reads "``orchestrator/cli.py:670``'s
`os.umask(0o077)` cannot close that". `os.umask(0o077)` is at
**`cli.py:705`**. The finder's replacement number is right.

Two corrections to the finding's own text:

* `cli.py:670` is `parser.add_argument(` inside `_parser()`, not
  `argparse.ArgumentParser(...)` — that is `:669`. Immaterial to the defect.
* This one *is* drift, unlike RX-A4. At `8b24bfb`, the commit that wrote the
  comment, `git show 8b24bfb:orchestrator/cli.py | grep -n 'os.umask'` returns
  `670`. It was correct when written and moved afterwards.

Fix cost: one number. File it with RX-G8, not separately.

## RX-B6 — `"tofu": null` with the reason on stderr only

**CONFIRMED · low, with the finder's proposed fix rejected.**

`cli.py:434` is `tofu=_tofu_version(workdir),` inside the `"ok"` `_record`.
`_tofu_version` (`:322-337`) prints `vcows: cannot record the tofu version
({exc})` to stderr at `:336` and returns `None` at `:337`.
`tests/test_cli.py:297-300` asserts `record["tofu"] is None` against the record
and the message against `capsys.readouterr().err` — the two halves, on two
streams, exactly as the finder said. A `null` in the shipped directory reads as
"vcows did not try" and means "tried and could not".

Not recorded as deliberate anywhere: the docstring at `:323-332` explains why the
failure is tolerated, not why its reason is absent from the record.

**The proposed fix is wrong and must not be filed as written.** `tofu.version`
returns `dict` (`tofu.py:273-281`), so `_tofu_version` is `dict | None`;
returning the message instead of `None` makes the field `dict | str` and a
consumer reading `record["tofu"]["terraform_version"]` gets a sentence. The
cheap correct fix is a `Problem.warning` appended to `run.extra["problems"]`,
which already exists on this path from `cli.py:350`. Still small — two lines,
no type change, no new key — but the issue text has to say so.

Low, not medium: it only fires on a path where the apply already succeeded and
`inventory.json` is on disk, and nothing in the record is false.

---

## Method

* Read at `672a500` in the detached worktree; every cited line re-opened with
  `sed -n` and cross-checked with `grep -n`.
* Deliberate-record check ran against `docs/findings.md`,
  `docs/review/2026-08-30/`, and the full commit bodies of
  `4eb378b..672a500`. It changed one verdict (RX-B4) and hardened one (RX-A4).
* Historical line numbers checked with `git show <commit>:<path>` at the commit
  that wrote each citation, which is what separates RX-A4 (never correct) from
  RX-B5 (drifted).
* Not touched: the rig, `--write-baseline`, any tracked file.
