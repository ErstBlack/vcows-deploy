# Issue #89 — five records or messages that lose something

> **SCOPE CHANGE, after this plan was written and before it was implemented.**
> **RX-B2 was split out of #89 and filed as its own issue, `#96`.** #89 now
> carries five findings: RX-B6, RX-B1, RX-B3, RX-C1, RX-C2. Everything below
> that concerns RX-B2 — §1.2, §2's four RX-B2 rows, §3.2, §4.2, §5.2, §6's
> `destroy.py` row, §7.2 and §8.2 — is **out of scope here and is `#96`'s**, and
> is kept only so `#96` inherits the measurements rather than re-deriving them.
> Two consequences the rest of this document still asserts and should not:
> `orchestrator/backends/libvirt/destroy.py` is **not** edited by #89 and neither
> is the `except Exception` at the destroy call site (widening it alone was
> measured to capture nothing); and the `# ty: ignore[unresolved-attribute]` §3.2
> and §6 budget for was RX-B2's alone, so **#89's patch adds no type suppression
> at all**. The counts in §6 and §8 are RX-B2-inclusive and are therefore wrong
> for #89 as landed; the landed figures are in the commit body.

Lane L4. Reverified at `aed962d`. Raw output:
`docs/review-cli-records/reverify/RX-B1.txt`, `RX-B2.txt`, `RX-B3.txt`,
`RX-B6.txt`, `RX-C1.txt`, `RX-C2.txt`, and the prototype measurement in
`prototypes-89.txt`.

All six are low. None is a wrong answer in an active path. They land as one
branch and one commit because five of the six are in `orchestrator/cli.py` and
the sixth pair is in `orchestrator/backends/libvirt/schema.py` — the repo's rule
is that related work in the same file lands together rather than piecemeal.
(Written when there were six. RX-B2 is now `#96`; the four that stayed in
`cli.py` are RX-B6, RX-B1, RX-B3, and the `schema.py` pair is RX-C1/RX-C2.)

---

## 1. Reverification verdict

**All six REPRODUCE at `aed962d`.** Harness for the four `cli.py` findings: an
unmodified copy of `orchestrator/` + `tests/` in a scratch directory (`diff -r -x
__pycache__` against the worktree returns nothing), driven with the worktree's
`.venv`. The two `schema.py` findings were driven through the real verb,
`python -m orchestrator.cli validate <cfg>`, on configs built from
`tests/conftest.CONFIG` with the URI rewritten to `hypervisor.invalid`. No
hypervisor was contacted at any point.

### 1.1 RX-B6 — `cli.py:434`, `"tofu": null` with the reason on stderr only

```
RX-B6 rc            = 0
RX-B6 record[tofu]  = None
RX-B6 record[problems] = []
RX-B6 stderr        = vcows: cannot record the tofu version (tofu version failed (exit 1))
RX-B6 reason in record?  False
```

The record says `null` and carries the reason nowhere. `problems` is present and
empty on the same record, which is the key the fix uses.

**The finding's own suggested fix is confirmed wrong.** Read at test entry,
before any patching:

```
RX-B6 tofu.version def line   = def version(workdir: Path | None = None) -> dict:
RX-B6 tofu.version return ann = dict
RX-B6 _tofu_version return ann = dict | None
```

Putting a sentence in the `tofu` field makes it `dict | str`, and a consumer
reading `record["tofu"]["terraform_version"]` gets a string index error.

### 1.2 RX-B2 — MOVED TO `#96`. `cli.py:552`, Ctrl-C during destroy records no `destroyed`/`skipped`

Core half, a `KeyboardInterrupt` subclass carrying a populated `Outcome` against
a plain `Exception` carrying the identical one:

```
RX-B2 KI  outcome= failed destroyed= <ABSENT> skipped= <ABSENT> error= Interrupted:
RX-B2 Exc outcome= failed destroyed= ['app01'] skipped= ['/pool/app02-seed.iso']
```

Libvirt half, a `KeyboardInterrupt` raised inside `vol.delete` after the domain
was already destroyed and undefined, through the real `destroy.destroy()` against
`tests/fake_libvirt`:

```
RX-B2 libvirt raised                 = KeyboardInterrupt
RX-B2 libvirt getattr(exc,'outcome') = <ABSENT>
RX-B2 libvirt domain log             = ['destroy', 'undefine:55']
```

**Warning 1 of 3 — HOLDS, and was tested rather than trusted.** Mutating
`cli.py:552` to `except BaseException` *alone*, then re-running both arms:

```
arm A (an exception that carries .outcome)  -> destroyed= ['app01'] skipped= ['/pool/app02-seed.iso']
arm B (a plain KeyboardInterrupt, no .outcome -- what destroy.py leaves)
                                            -> destroyed= <ABSENT> skipped= <ABSENT>
```

Arm B is the real backend's shape. The widened catch captures nothing from it.
Both halves are needed.

**The "docstring contradiction" framing is FALSE — confirmed.** `_guard`'s source,
printed verbatim in `RX-B2.txt`, catches `BaseException` at `cli.py:258` and
writes the record its docstring promises. There is no contradiction; there is a
gap that neither comment claims to cover.

### 1.3 RX-B1 — `cli.py:411`, `tofu.py:91`, a failing step's own warnings are dropped

```
RX-B1 outcome        = failed
RX-B1 tofu_warnings  = ['warning: init warned', 'warning: plan warned']
RX-B1 error          = TofuError: tofu apply failed (exit 1): error: apply blew up
RX-B1 apply's own warning present in the whole record?  False
```

The dead-reader half holds. `grep -rn "\.result\b" --include=*.py` over
`orchestrator/` and `tests/` returns three hits: the assignment at `tofu.py:91`
and two reads in `tests/test_tofu_driver.py:186-187`. No production reader.

### 1.4 RX-B3 — `cli.py:528-537`, the destroy record never names the VMs left alone

```
stdout:   elsewhere            skip    belongs to deployment 'lab-b', not 'lab-a'
run.json: {"destroyed": ["app01"], "skipped": [], "problems": [], …}
'elsewhere' anywhere in run.json?  False
'lab-b' anywhere in run.json?      False
```

### 1.5 RX-C1 — `schema.py:243-249`, a structural VM error suppresses `_check_nics`

Four configs, all with `vms[1]` reusing `vms[0]`'s `192.168.122.60/24`:

```
vms[0].vcpus = 0     → error [vms[0].vcpus]: 0 is less than the minimum of 1
                       (no duplicate reported)
vms[0].cpus = 2      → error [vms[0]]: Additional properties are not allowed ('cpus' …)
                       (no duplicate reported)
control              → error [vms[1].nics[0].ip_cidr]: address … already used by vms[0].nics[0]
#27's own case       → BOTH reported
```

**Warning 3 of 3 — the `#27 PARTIAL` claim, settled.** `gh issue view 27` scopes
itself to `schema.py:509`, the `seen_ips.setdefault` nested inside the
`gateway is not None` half of the guard. That line was hoisted; the landed code is
`schema.py:539-554` and the `#27` regression case reports both problems in one
pass, measured above. **So #27 is DONE against what #27 asked for, and PARTIAL is
the wrong label for the issue.** What is partial is the *principle* #27's own
comment states at `schema.py:534-538` — "the round trip `validate` exists to
avoid" — which the `continue` at `:247-249` breaks one level up, for a commoner
class of trigger. That is the same relationship RX-B1 has to RW-B4: a residual of
a rationale, not an unfinished fix. **Recommendation: do not reopen #27.** Record
in this commit's body that RX-C1 is the residual of #27's stated reason and that
#27's own scope is closed.

### 1.6 RX-C2 — `schema.py:350`, `urlsplit`'s ValueError escapes `_check_target`

```
RAISE 'qemu+ssh://[2001:db8::1/system' -> Invalid IPv6 URL
RAISE 'qemu+ssh://2001:db8::1]/system' -> Invalid IPv6 URL
RAISE 'qemu+ssh://h＃x/system'          -> netloc 'h＃x' contains invalid characters under NFKC normalization
ok    'qemu+ssh://[2001:db8::1]/system'

$ validate  (config also carries vms[0].vcpus: 0 and two warning paths)
error: ValueError: Invalid IPv6 URL
exit=1
```

Every other problem in the document is lost. `deploy` with the same config exits 1
with an empty run directory — identical to the control with a well-formed URI, so
this is a message-quality defect on a loud, non-destructive path and not a
lost-record one.

### 1.7 The three refuted items — re-checked, still refuted

Named here so nobody re-derives them.

| item | why it stays refuted |
|---|---|
| `schema.py:296-303`, the unreadable-image digest warning filed at `image.sha256` | Re-read. The `where` names the check that could not run, and the message names the unreadable path (`f"cannot read {source} …"`). Correct as written. |
| `schema.py:558-563`, duplicate MAC reported at the NIC rather than `.mac` | Re-read. `mac_of` (`schema.py:186-187`) is `nic.get("mac") or derive_mac(...)`, so the MAC is often derived and `.mac` would name a key the config does not contain. `where=at` is the deepest key that exists. |
| `cli.py:571`'s literal width 20 | `_NAME_W = 20` at `:175` and `_row` at `:179-183` confirmed. `01a513c` (the commit closing `#33`/`#34`/`#35`/`#36`) decides this exact line: "It is now the only literal width left in the file, which is correct." A documented deviation. |

---

## 2. Anchor table

Every line re-read at `aed962d`. `orchestrator/` is byte-identical to the
review's pin `672a500`, so the recorded numbers are still exact — verified, not
assumed.

| finding | anchor | state |
|---|---|---|
| RX-B6 | `cli.py:322-337` `_tofu_version`; `:335` the four-exception tuple; `:336` the stderr print; `:337` `return None` | ok |
| RX-B6 | `cli.py:434` `tofu=_tofu_version(workdir),` inside the `"ok"` record | ok |
| RX-B6 | `cli.py:350` `run.extra["problems"] = [str(p) for p in problems]` | ok, and present in the `ok` record |
| RX-B6 | `tofu.py:273` `def version(workdir: Path \| None = None) -> dict:` | ok |
| RX-B6 | `tests/test_cli.py:297-300` the two-stream assertion | ok |
| RX-B2 (now `#96`) | `cli.py:552` `except Exception as exc:` | ok |
| RX-B2 (now `#96`) | `cli.py:553-558` the comment, and `:559` `getattr(exc, "outcome", None)` | ok |
| RX-B2 (now `#96`) | `cli.py:258` `except BaseException as exc:` in `_guard` | ok — the refutation's anchor |
| RX-B2 (now `#96`) | `destroy.py:487-561` `destroy()`; `:508`, `:521`, `:560` the three `DestroyError(out)` raises | ok |
| RX-B1 | `cli.py:391-412` the three tofu steps and their `_note_warnings` | ok |
| RX-B1 | `tofu.py:90-92` `TofuError.__init__`, `self.result = result` | ok |
| RX-B3 | `cli.py:527` the `problems` assignment; `:528-537` the `others` loop | ok |
| RX-B3 | `cli.py:542`, `:547`, `:575-581` the three destroy `_record` calls | ok |
| RX-C1 | `schema.py:243-251` the loop and the `continue` | ok |
| RX-C1 | `schema.py:425-427` `_check_vm_structure`; `base.py:60-63` `Problem.fatal` | ok |
| RX-C1 | `schema.py:533-554` the `#27` fix as landed, with its comment at `:534-538` | ok |
| RX-C2 | `schema.py:346-350` `_check_target` and the bare `urlsplit` | ok |
| RX-C2 | `config.py:117-119` "every problem rather than the first" | ok |
| refuted | `schema.py:296-303`, `schema.py:558-563`, `cli.py:175`/`:571` | ok |

---

## 3. Corrections to the issue body

### 3.1 RX-B6
None. Everything the issue says, including its rejection of its own finder's fix,
was re-measured and held.

### 3.2 RX-B2 — MOVED TO `#96`
None to the technical claims. One addition the issue does not make: the fix needs
a `# ty: ignore[unresolved-attribute]`, because `# type: ignore[attr-defined]` is
**not honoured by `ty`** — measured, `ty check` reports
`error[unresolved-attribute]: Unresolved attribute 'outcome' on type
'BaseException'` through the `type:` form and passes through the `ty:` form. Say
so in the issue so the implementer does not discover it at `just typecheck`.

### 3.3 RX-B1
The issue is exact. Recorded so it is not re-derived: the finder's original
transcript said `tofu_warnings=['init']`, missing `plan`'s; the verifier corrected
it to `['init','plan']` and this pass reproduces `['init','plan']`. Both earlier
steps *are* recorded. Only the raising step's own warnings are lost.

### 3.4 RX-B3
The issue proposes `left_alone=[e.name for e in others]`. A bare name list drops
the one datum that makes the row meaningful — *whose* deployment it belongs to,
which the stdout row at `:534-535` does carry. See §5.4.

### 3.5 RX-C1
**The `#27 PARTIAL` claim is wrong as filed.** #27 is complete against its own
stated scope; the residual is of its *rationale*, one level up. §1.5 settles it.
Change the issue's sentence, and do not reopen #27.

Second correction: the issue calls this a `continue` that "suppresses
`_check_nics`". It suppresses `_check_firmware` too (`schema.py:250`), which was
not measured by anyone. That check reads `vm` only through `.get` and `in`, so it
is safe under strictly weaker conditions than `_check_nics` — which matters for
where the new guard goes.

### 3.6 RX-C2
None. The three raising netloc classes, the lost problems, and the empty run
directory all reproduce verbatim.

---

## 4. The defect

### 4.1 RX-B6
`_tofu_version` (`cli.py:322-337`) is right to tolerate the failure — its
docstring explains why letting it raise would write `outcome: "failed"` over a
deploy that created every VM. But it splits the fact in two: the *value* goes into
the record as `null` and the *reason* goes to a terminal that a shipped run
directory does not include. A site reading `"tofu": null` cannot distinguish
"vcows did not ask" from "vcows asked and could not parse the answer".

### 4.2 RX-B2 — MOVED TO `#96`
Two independent gaps that only close together.

`destroy.py`'s `out` is a local. It reaches the caller by exactly two routes:
`return out` on success, and `DestroyError(out)` at `:508`, `:521` and `:560`. A
`KeyboardInterrupt` between the undefine and the volume deletes takes neither, so
the populated `Outcome` is discarded — measured, with `domain log = ['destroy',
'undefine:55']` proving the work was real.

`cli.py:552` is `except Exception`, which cannot see a `KeyboardInterrupt` even if
one carried an outcome. So `run.extra["destroyed"]`/`["skipped"]` at `:561-563`
never run, and `_guard` writes a record with `outcome`, `error`, empty
`decisions` and `problems`.

The operator after a Ctrl-C cannot tell from the shipped directory which domains
were already undefined. Per RX-A2's mechanism a re-run's preflight cannot see them
either, so they resurface only as `orphan_volumes` errors.

### 4.3 RX-B1
`_note_warnings` runs after each step *returns* (`cli.py:392`, `:394`, `:411`).
RW-B4's remedy was written that way and `2b20608`/`4cc9d35` implemented it
exactly, so the raising step was never in scope. `TofuError` already carries the
warnings — `tofu.py:90-92` stores the whole `Result` — and nothing in production
reads it. The run whose warnings are worth most keeps every step's but its own.

### 4.4 RX-B3
`others` is computed, printed, and dropped. `docs/findings.md:121` establishes the
*reporting* obligation and the `skip` row satisfies it. Nothing establishes the
recording obligation, but `tofu.py:77-84` makes the argument for exactly this case
about `Result.warnings`: "the run directory can record them, which is the copy
that outlives the terminal."

### 4.5 RX-C1
The `continue` at `:247-249` is justified by one true sentence — "the checks below
index into fields the schema just rejected" — applied to a set it does not
describe. It is true when the structural problem is *in* `nics` or `name`. It is
false for `vcpus: 0` and for an unexpected top-level key, which are the commoner
triggers, and there the VM's addresses and MACs are never registered in `seen_ips`
/ `seen_macs`. A later VM reusing one is not reported.

Bounded: `_check_vm_structure` is `problems_from(...)`, which builds `Problem.error`
and nothing else (`base.py:70-89`), and `Problem.fatal` is `severity is ERROR`
(`:60-63`). So every structural problem is fatal, `config.load` raises, and no verb
proceeds. The cost is exactly one extra edit round trip — the one
`config.py:117-119` and `#27`'s own comment rule out.

### 4.6 RX-C2
`_check_target` calls `urlsplit(uri)` bare at `:350`. Three netloc classes make it
raise `ValueError`, and `_check_target` is the *first* thing `validate` runs
(`schema.py:239`), so the exception unwinds past every other check and past
`config.load`'s "every problem rather than the first" contract into `main`'s
catch-all. The operator loses the `[target.libvirt.uri]` location, the `  ` indent
that marks a Problem line, and every other problem in the document.

---

## 5. The fix

Every patch below was prototyped in a scratch copy and measured — full diff,
lint, typecheck and behaviour in
`docs/review-cli-records/reverify/prototypes-89.txt`. Combined:
`ruff check` clean, `ruff format --check` clean, `ty check` clean, scratch suite
unchanged at 401 passed / 35 skipped.

### 5.1 RX-B6 — a `Problem.warning` into `run.extra["problems"]`

`_tofu_version` takes the run, and files the reason where the record already has
a place for it:

```python
def _tofu_version(run: _Run, workdir: Path) -> dict | None:
    ...
    except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:
        # Both halves, not just stderr: `tofu: null` in the shipped record reads
        # as "vcows did not try" and means "tried and could not". The field stays
        # `dict | None` -- a sentence in it would make it `dict | str` and break
        # a consumer reading `record["tofu"]["terraform_version"]`.
        problem = Problem.warning(
            f"cannot record the tofu version ({exc})", where="tofu"
        )
        print(f"vcows: {problem.message}", file=sys.stderr)
        run.extra["problems"].append(str(problem))
        return None
```

`Problem` is already imported (`cli.py:47`). Measured after:

```
RX-B6 record[problems] = ['warning [tofu]: cannot record the tofu version (tofu version failed (exit 1))']
RX-B6 record[tofu]     = None
RX-B6 stderr           = vcows: cannot record the tofu version (tofu version failed (exit 1))
```

Both streams, one sentence, field type unchanged.

**Rejected:** the finder's own fix, putting the message in the `tofu` field —
§1.1 measures why. **Rejected:** a new `tofu_error` key — a second key for a
datum `problems` already models, and `problems` is what every other advisory on
this path uses.

### 5.2 RX-B2 — MOVED TO `#96`. Both halves, in the order that makes each meaningful

**Half 1, `destroy.py`.** Attach `out` to whatever leaves the function:

```python
    except DestroyError:
        raise
    except BaseException as exc:
        # A Ctrl-C between the undefine and the volume deletes leaves `out`
        # populated and attached to nothing. `DestroyError` is the only path
        # that carries it today, and an interrupt does not take that path.
        exc.outcome = out  # ty: ignore[unresolved-attribute]
        raise
```

**Half 2, `cli.py:552`** → `except BaseException as exc:`. `getattr(exc,
"outcome", None)` at `:559` needs no change; it is already generic, and
`cli.py:556-558` records that generality as deliberate.

Measured after both:

```
RX-B2 libvirt getattr(exc,'outcome') = Outcome(destroyed=['app01'], skipped=[], problems=[])
RX-B2 KI  outcome= failed destroyed= ['app01'] skipped= ['/pool/app02-seed.iso']
```

**Scope note on half 1.** The prototype wraps the whole body of `destroy()`,
which costs 67 added lines of which most is reindentation. Only the `for target
in targets:` loop can populate `out.destroyed`, so wrapping just that loop is
sufficient and about half the churn. Take the smaller one.

**Rejected:** `except BaseException` at `cli.py:552` alone — measured in §1.2,
captures nothing from this backend. **Rejected:** converting the interrupt to
`DestroyError(out)` — that hides a Ctrl-C from the interpreter and from `main`,
and turns "the operator interrupted" into "the backend failed". **Rejected:**
adding an `out` parameter to `Backend.destroy` so the caller owns the accumulator
— no `ty: ignore`, but it changes the protocol in `backends/base.py` and every
implementation including `tests/fake_backend.py`, for one interrupt path. The
`exc.outcome` convention is the one this codebase already chose.

### 5.3 RX-B1 — one `except` around the three steps

```python
        try:
            inited = tofu.init(workdir)
            _note_warnings(run, inited)
            ...
            raw = tofu.outputs(workdir)
        except tofu.TofuError as exc:
            # The step that raised warned too, and `TofuError.result` is the only
            # thing carrying those warnings. Without this they die with the
            # exception -- the run whose warnings are worth most keeps none of
            # its own.
            if exc.result is not None:
                _note_warnings(run, exc.result)
            raise
```

Measured after:

```
RX-B1 tofu_warnings = ['warning: init warned', 'warning: plan warned',
                       'warning: apply warned about a deprecated argument']
```

This gives `TofuError.result` its first production reader.

**One concrete cost, measured:** the reindentation pushes `f"plan proposes no
creates for {len(creating)} VM(s); refusing to apply"` past 88 columns and `ruff`
fails E501. It has to be rewrapped in the same commit.

**Rejected:** accumulating inside `tofu._run` — `tofu.py` knows nothing about
`_Run` and must not. **Rejected:** reading `exc.result` in `_guard` — `_guard` is
driver-agnostic and this is a tofu-specific attribute; `_deploy` is where the
tofu steps live.

### 5.4 RX-B3 — one key, carrying the deployment name

```python
        # The same argument `tofu.py:77-84` makes for `Result.warnings`: the run
        # directory is the copy that outlives the terminal. `findings.md:121`
        # mandates the report, and the `skip` row below satisfies it -- but a
        # marked VM this teardown deliberately left alone appeared in no shipped
        # artifact at all.
        run.extra["left_alone"] = {
            e.name: e.marker.deployment or "<unset>"
            for e in others
            if e.marker is not None
        }
```

Beside the `problems` assignment already at `:527`, so every one of the three
`_record` calls carries it. `"<unset>"` matches the rendering already at `:535`.

**Rejected:** the issue's `[e.name for e in others]` — §3.4. A name with no
deployment is the half of the row that does not explain itself. **Rejected:** a
list of dicts — a mapping is the same information in fewer bytes and sorts
stably in `_write_json`'s `sort_keys=True`.

### 5.5 RX-C1 — a shape predicate instead of an unconditional `continue`

```python
        structural = _check_vm_structure(vm, where)
        problems += structural
        if structural and not _nic_checks_are_safe(vm):
            continue
```

```python
def _nic_checks_are_safe(vm: dict) -> bool:
    """Whether `_check_firmware` and `_check_nics` can read this VM unguarded.

    Normally `_check_vm_structure` passing is what makes that safe. When it did
    not pass, the question is narrower: are the fields *these* checks index still
    the right shape. `_check_nics` reads `vm["nics"]` and, through `mac_of`,
    `vm["name"]`; `_check_firmware` uses `.get` throughout. A `vcpus` out of range
    or an unexpected key says nothing about any of them, and skipping anyway costs
    the operator the edit round trip `config.py:117-119` rules out.
    """
    return (
        isinstance(vm, dict)
        and isinstance(vm.get("name"), str)
        and isinstance(vm.get("nics"), list)
        and all(isinstance(nic, dict) for nic in vm["nics"])
    )
```

The three `isinstance` clauses are not defensive padding — they are exactly the
reads `_check_nics` and `mac_of` perform, established by reading both functions.
Measured after:

```
vms[0].vcpus = 0  → both the vcpus error and the duplicate-address error
vms[0].cpus = 2   → both the additional-properties error and the duplicate
control           → unchanged
#27's case        → unchanged
```

**Two edge cases the implementer must test** (they are why the predicate is not
two clauses): a `vms[0]` that is not a mapping at all, and a `nics` list holding a
non-mapping element. Both must still `continue`, and both must be regression
tests — see §7.5.

> **Correction, from building it.** The signature here is wrong: `vm: dict` with
> `isinstance(vm, dict)` as the first clause is self-contradictory, and `ty`
> rejects a test that passes a non-mapping. It landed as `vm: object`.

**Rejected:** removing the `continue` entirely — `_check_nics` would `KeyError`
on a VM with no `nics`, turning a config error into a crash. **Rejected:**
deciding from each `Problem.where` whether it lands inside `nics` — an error
whose `where` is exactly `vms[0]` can be either "unexpected key" (harmless) or
"'nics' is a required property" (fatal to the check), so the decision would rest
on parsing the message. **Rejected:** validating `nics` against a sub-schema
separately — a second `jsonschema` validator for a shape test three `isinstance`
calls answer.

### 5.6 RX-C2 — catch the parse and return a Problem

```python
    uri = target["uri"]
    try:
        parts = urlsplit(uri)
    except ValueError as exc:
        return [
            Problem.error(
                f"{uri!r} is not a URL ({exc}); vcows assembles the connection "
                f"URI from these fields and cannot parse this one",
                where=where,
            )
        ]
```

Returning early is correct: every remaining check in `_check_target` reads
`parts`, so there is nothing left to say about this URI — and `validate` carries
on with every other problem in the document. Measured after:

```
error [target.libvirt.uri]: 'qemu+ssh://[2001:db8::1/system' is not a URL (Invalid IPv6 URL); …
error [vms[0].vcpus]: 0 is less than the minimum of 1
error [vms[1].nics[0].ip_cidr]: address 192.168.122.60 is already used by vms[0].nics[0]
```

**Rejected:** catching in `main` — the location, the indent, and the other
problems are already gone by then. **Rejected:** a regex pre-check on the URI —
duplicates `urlsplit`'s grammar and will drift from it.

---

## 6. Surface cost

Measured on the combined prototype, added lines only, comments included:

| file | added | what |
|---|---|---|
| `orchestrator/cli.py` | 54 | RX-B6 (11), RX-B1 (10 + one rewrap), RX-B3 (11), and the reindentation RX-B1 forces (RX-B2 half 2's one word is `#96`'s) |
| `orchestrator/backends/libvirt/schema.py` | 26 | RX-C2 (10), RX-C1 (16, of which 12 are one helper and its docstring) |
| `orchestrator/backends/libvirt/destroy.py` | 0 (`#96`'s, not #89's) | RX-B2 half 1 — 8 lines of logic, the rest reindentation. Roughly halved by wrapping only the `for target in targets:` loop |

No new module, no new type, no new dependency, no new config field, no new
`Backend` protocol member. Two new record keys (`left_alone`, and one more string
in the existing `problems` list). One new private helper
(`schema._nic_checks_are_safe`). One `# ty: ignore` — the only suppression the
patch adds, and §5.2 says why the alternative costs more.

---

## 7. The failing tests

Six tests as written; **five for #89**, since 7.2 is `#96`'s. **None needs a
conditional skip** — every one is `monkeypatch`, the
fake backend, `tests/fake_libvirt`, or a config on `tmp_path`. `tests/test_gates.py`
AST-walks the suite and fails on a bare `pytest.skip`, `pytest.importorskip` or
`pytest.mark.skip`; this work introduces none. If any of these ever needs a gate,
it goes through `conftest.gate()` (`tests/conftest.py:44`) or `conftest.require()`
(`:61`).

One caveat for 7.2's libvirt half: `tests/fake_libvirt.py:25` imports `libvirt` at
module scope, so it belongs in `tests/test_libvirt_destroy.py`, which already
carries that dependency through `conftest.require("libvirt", …)` at `:36`.

### 7.1 RX-B6 — `tests/test_cli.py`

Extend `test_a_deploy_that_worked_is_not_failed_by_the_version_it_records`
(`:270-300`) rather than adding a test. It already drives the exact scenario and
already asserts both `record["tofu"] is None` and the stderr message. Add:

```python
    assert any("cannot record the tofu version" in p for p in record["problems"])
```

Fails today: `record["problems"]` is `[]`.

### 7.2 RX-B2 — MOVED TO `#96`. Two tests, one per half

`tests/test_libvirt_destroy.py`:

```python
def test_an_interrupted_teardown_still_carries_what_it_removed():
    """`out` reaches the caller only by `return` or `DestroyError`. A Ctrl-C
    between the undefine and the volume deletes takes neither, so the domains
    already undefined were recorded nowhere -- and a re-run's preflight cannot
    see them either."""
    ...  # fake_libvirt conn whose storageVolLookupByPath(...).delete raises
    with pytest.raises(KeyboardInterrupt) as caught:
        d.destroy({}, conn, [target(dom)])
    assert getattr(caught.value, "outcome", None) is not None
    assert caught.value.outcome.destroyed == ["app01"]
```

Fails today on `is not None`.

`tests/test_cli.py`: extend `test_an_interrupted_destroy_still_leaves_a_run_record`
(`:338-353`) so the interrupt carries an outcome, and add
`assert record["destroyed"] == ["app01"]`. Fails today — the key is absent.

### 7.3 RX-B1 — `tests/test_cli.py`

Beside `test_a_failed_apply_records_the_warnings_that_came_before_it` (`:303`),
which is the half that works:

```python
def test_a_failing_tofu_step_records_its_own_warnings_too(
    backend, config, tmp_path, monkeypatch
):
    """`TofuError` already carries the whole `Result` (`tofu.py:90-92`) and
    nothing in production read it. `_note_warnings` runs after each step
    *returns*, so the step that raised kept none of its own."""
    ...  # apply raises TofuError(msg, Result(1, diagnostics=(warning, error)))
    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["tofu_warnings"] == [
        "warning: init warned",
        "warning: plan warned",
        "warning: apply warned about a deprecated argument",
    ]
```

Fails today on the third element.

### 7.4 RX-B3 — `tests/test_cli.py`

Extend `test_destroy_takes_only_this_deployment` (`:517-532`), which already
builds the two-deployment world and already asserts the stdout row:

```python
    assert record["left_alone"] == {"elsewhere": "lab-b"}
```

Fails today: `KeyError: 'left_alone'`.

### 7.5 RX-C1 — `tests/test_libvirt_schema.py`, three cases

```python
def test_a_structural_error_outside_nics_does_not_hide_a_duplicate_address():
    """`#27` hoisted the address registration out of the gateway guard for this
    reason (`schema.py:534-538`); the same round trip survived one level up, for
    the commoner triggers."""
    # vms[0].vcpus = 0, vms[1] reuses vms[0]'s ip_cidr
    # assert BOTH the vcpus error and the duplicate-address error are present
```

plus the two edge cases §5.5 names, which pin the predicate rather than the
behaviour:

```python
def test_a_vm_that_is_not_a_mapping_still_skips_the_nic_checks(): ...
def test_a_nic_that_is_not_a_mapping_still_skips_the_nic_checks(): ...
```

Both must assert no exception escapes and the structural error is reported. The
first two fail today (no duplicate reported); the last two pass today and are the
guard against a fix that removes the `continue` outright.

> **Correction, from building it.** "The last two pass today" is false for the
> non-mapping VM. `schema.validate` with a string VM raises `TypeError` out of
> `_check_volume_names` — on `origin/master` too, downstream of this loop and
> unrelated to the guard — and the case is unreachable through `config.load`,
> which returns the core schema's `'app01' is not of type 'object'` without ever
> asking the backend. It landed pinned on the predicate directly, clause by
> clause. The `nics`-element case is reachable and stayed a behaviour test.

### 7.6 RX-C2 — `tests/test_libvirt_schema.py:143-161`

Three rows added to the existing `test_bad_uris_are_rejected` parametrize, which
is already `(uri, expect)` against `messages(schema.validate(cfg))`:

```python
        # `urlsplit` raises on these rather than returning an unusable split, so
        # the ValueError unwound past every other problem in the document and
        # past `config.load`'s "every problem rather than the first".
        ("qemu+ssh://[2001:db8::1/system", "is not a URL"),
        ("qemu+ssh://2001:db8::1]/system", "is not a URL"),
        ("qemu+ssh://h＃x/system", "is not a URL"),
```

All three fail today, not on the assertion but by raising `ValueError: Invalid
IPv6 URL` out of `schema.validate` inside the test — which is exactly the defect.

> **Correction, from building it.** The third row as written fails `ruff`
> RUF001: a literal FULLWIDTH NUMBER SIGN in source. It landed as the escape
> `\uff03` — the same string, and the same NFKC failure out of `urlsplit`.

One more test, because three rows in a parametrize do not prove the *other*
problems survived:

```python
def test_a_uri_that_will_not_parse_loses_no_other_problem(cfg):
    """`config.py:117-119`: every problem rather than the first."""
    cfg["target"]["libvirt"]["uri"] = "qemu+ssh://[2001:db8::1/system"
    cfg["vms"][0]["vcpus"] = 0
    problems = schema.validate(cfg)
    assert any(p.where == "target.libvirt.uri" for p in problems)
    assert any(p.where == "vms[0].vcpus" for p in problems)
```

---

## 8. Verification

**Whole-branch gate:** `just check` — six lint gates, `ty` clean, and **420
passed, 25 skipped** (baseline at `aed962d` is 411/25, measured in the worktree).
That is nine new ids: RX-B6 and RX-B3 extend existing tests and add none, RX-B2
adds one (its `test_cli.py` half extends `:338`), RX-B1 one, RX-C1 three, RX-C2
four — three parametrize rows plus one test. The three lint facts that will
bite are already measured on the prototype: `ruff` E501 on the rewrapped
`plan proposes no creates` message, `ty`'s rejection of `# type: ignore` in favour
of `# ty: ignore[unresolved-attribute]`, and `ruff format` clean after both.

**Both figures above are RX-B2-inclusive and so are wrong for #89 as landed.**
Landed: nine new ids, but not this section's nine — RX-B2's one is gone and
RX-C1's structural-error case is parametrised over its two triggers rather than
written once, so RX-C1 contributes four rather than three. The branch baseline
moved as well; the landed numbers are in the commit body. Two of the three lint facts
held: E501 on the rewrap did, `ruff format` did. The `# ty: ignore` one did not
arise — that suppression was RX-B2's, and #89 adds none. A fourth and a fifth
that this section did not predict did arise, and are recorded in the commit
body: `ruff` RUF001 on §7.6's third parametrize row, and `ty` rejecting §5.5's
`vm: dict` annotation against a test that passes a non-mapping.

Per finding, the teeth check is the same shape: revert that finding's hunk alone,
keep its test, confirm the named assertion fails, restore.

| | revert this | this assertion must fail |
|---|---|---|
| 8.1 RX-B6 | the `Problem.warning` append | `any("cannot record the tofu version" in p for p in record["problems"])` |
| 8.2 RX-B2 (now `#96`) | `destroy.py`'s `except BaseException` **only** | `getattr(caught.value, "outcome", None) is not None` — and this is the check that proves half 2 alone is insufficient |
| 8.2 RX-B2 (now `#96`) | `cli.py:552`'s widening **only** | `record["destroyed"] == ["app01"]` |
| 8.3 RX-B1 | the `except tofu.TofuError` block | the third element of `tofu_warnings` |
| 8.4 RX-B3 | the `left_alone` assignment | `KeyError: 'left_alone'` |
| 8.5 RX-C1 | `_nic_checks_are_safe`, restoring the bare `continue` | the duplicate-address error is absent in both triggers |
| 8.6 RX-C2 | the `try`/`except ValueError` | `ValueError` escapes `schema.validate` |

**8.7 — the two `_check_target` regressions RX-C2 must not cause.** The existing
scheme/query/password checks in `_check_target` must still fire for a well-formed
URI. `tests/test_libvirt_schema.py` already covers them; they must stay green
unchanged, and if any goes red the early `return` is swallowing more than the
unparseable case.

**8.8 — no rig, no image.** Nothing in this issue is reachable only through a
container or a hypervisor. Every one of the nine tests runs at default gates.

---

## 9. Non-goals

* **Reopening `#27`.** §1.5. It is closed correctly; RX-C1 is the residual of its
  rationale and belongs here.
* **The three refuted items** (`schema.py:296-303`, `schema.py:558-563`,
  `cli.py:571`). §1.7. Re-checked, still refuted, recorded so the next reviewer
  does not spend a pass on them.
* **`RW-B4` and `#8`/`RW-A2`.** RX-B1 and RX-B2 are their residuals, not
  reopenings. Neither prior fix is wrong.
* **The `setdefault`-as-ownership-test idiom** in `_check_nics`. `#27`'s issue
  body forbids touching it and names the completed remediation that adopted it.
* **`docs/findings.md:121`.** RX-B3 adds a record key; it does not change the
  reporting obligation or the wording that states it.
* **`Outcome`'s shape, and `Backend.destroy`'s signature.** §5.2 rejects the
  protocol change.
* **`orphan_volumes` and RX-A2's re-run mechanism.** RX-B2 makes the interrupted
  teardown legible in the record; it does not make a re-run able to finish it.
  Different finding, different lane.
* **Making any of these six a `medium`.** All were verified and, where they were
  filed higher, downgraded with measurements. Nothing here re-litigates that.
