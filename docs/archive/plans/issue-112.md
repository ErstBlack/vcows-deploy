# Issue #112 — a blank nic field crashes `validate` and loses every other problem

## 1. Reverification verdict

**Reproduced, and bisected to the three trees the issue names.** One config, one
blank YAML value (`ip_cidr:` with nothing after it, which parses as `None`),
`python -m orchestrator.cli validate`:

| tree | output |
|---|---|
| `b58f924` (before the guard) | `error [vms[0].nics[0].ip_cidr]: None is not of type 'string'` + the image warning |
| `586e192` (this campaign's tip) | `error: TypeError: argument of type 'NoneType' is not iterable` |
| this branch | `error [vms[0].nics[0].ip_cidr]: None is not of type 'string'` + the image warning |

RX-C1's own case, `vcpus: 0` on `vms[0]` plus a duplicate `ip_cidr` on `vms[1]`,
through the same CLI on the same three trees:

| tree | `vms[0].vcpus` | duplicate address |
|---|---|---|
| `b58f924` | reported | **not reported** |
| `586e192` | reported | reported |
| this branch | reported | reported |

So the two behaviours are not in tension and this branch is the first tree to
have both. `b58f924` is not a fallback.

The crash is wider than the issue's single example. All eight triggers its table
implies, driven through `schema.validate` on `586e192`:

```
ip_cidr=None       TypeError: argument of type 'NoneType' is not iterable
ip_cidr=5          TypeError: argument of type 'int' is not iterable
ip_cidr=True       TypeError: argument of type 'bool' is not iterable
nameservers=None   TypeError: 'NoneType' object is not iterable
nameservers=5      TypeError: 'int' object is not iterable
nameservers=True   TypeError: 'bool' object is not iterable
mac=5              AttributeError: 'int' object has no attribute 'lower'
mac=True           AttributeError: 'bool' object has no attribute 'lower'
```

## 2. Anchor table

All at `586e192`, all confirmed by reading the file or running the code.

| anchor | state |
|---|---|
| `schema.py:247` — `if structural and not _nic_checks_are_safe(vm): continue` | ok |
| `schema.py:258-277` — `_nic_checks_are_safe`, four container-shape clauses | ok |
| `schema.py:605` — `if "/" not in raw:` in `_parse_interface` | ok |
| `schema.py:590` — `for j, ns in enumerate(nic.get("nameservers", []))` | ok |
| `schema.py:593` — `mac = mac_of(vm, i, deployment).lower()` | ok |
| `schema.py:186-187` — `mac_of` is `nic.get("mac") or derive_mac(...)` | ok, and it is why `mac: None` is *not* one of the crash triggers |
| `schema.py:367-401` — `_check_target` wraps `urlsplit`, RX-C2 | ok |
| `base.py:47` — `Problem.where: str = ""` | ok |
| `base.py:84-89` — `problems_from` builds `where` from `err.json_path` | ok |
| `e555fe9` added the guard **and** the `urlsplit` wrap in one diff | ok — `git show e555fe9` carries `+def _nic_checks_are_safe` and `+        parts = urlsplit(uri)` |

## 3. Corrections to the issue body

Three, all small, none of which changes the diagnosis or the fix.

**C1 — not every crash is a `TypeError`.** The issue's opening sentence and its
`586e192` transcript both say `TypeError`. Two of the eight triggers raise
`AttributeError`: `mac` as `int` or `bool` is truthy, so `mac_of` returns it
unchanged and `.lower()` is called on a non-string. Same consequence — an
uncaught exception past `config.load` — but a test asserting on `TypeError`
alone would have missed a quarter of the surface. The tests added here assert on
the *result*, not the exception type.

**C2 — `tests/test_libvirt_schema.py:363` is the wrong anchor.** The issue cites
it as pinning the non-dict nic case. At `586e192` line 363 is
`assert "already used by" in out`, the last line of RX-C1's own parametrised
test. The non-dict **nic** case is `:380`; the non-dict **VM** predicate test is
`:366`. The issue's point stands unchanged: neither of those supplies a nic that
is a dict with a wrongly-typed field.

**C3 — the crash sites are not the whole behaviour change.** Two more cases move,
neither a crash, both measured in §8: a bare-string `nameservers` went from 14
fatal problems to 1, and `mac: None` loses a duplicate-address report it used to
produce. §6 carries the second as a cost rather than a win.

## 4. The defect

`e555fe9` (PR #102, closing #89's RX-C1) changed `schema.py:247` from
`if structural: continue` to `if structural and not _nic_checks_are_safe(vm):`.
The predicate it introduced asks one question — **is the container the right
shape**: `vm` a dict, `name` a str, `nics` a list, every nic a dict.

A nic that is a mapping with one wrongly-typed *field* answers yes to all four.
`_check_nics` then runs against data the JSON schema has already rejected, and
its contract is that the schema passed:

* `_parse_interface`'s `if "/" not in raw` — `raw` is `None`, an `int` or a `bool`
* `enumerate(nic.get("nameservers", []))` — the `.get` default only covers absence
* `mac_of(...).lower()` — `mac_of` returns whatever truthy value the nic holds

All three are reachable from `config.load`, which every verb calls
(`cli.py:295`, `:325`, `:336`, `:531`), so this is not a `validate`-only defect.

The irony the issue records is real and was verified: `git show e555fe9` adds
`_nic_checks_are_safe` and the `try: parts = urlsplit(uri)` of RX-C2 in the same
diff, and RX-C2's comment in the file states the exact failure mode this defect
reintroduced — "an unhandled `ValueError` here unwound past every other check and
past `config.load`'s 'every problem rather than the first'".

## 5. The fix

The information the guard needs is already computed. `_check_vm_structure`
returns `problems_from(validator.iter_errors(vm), at=where)`, and `problems_from`
puts the failing JSON path in `where` — `vms[0].nics[0].ip_cidr`. `structural`
is scoped to one VM at the call site. So the guard asks the schema's verdict
before it asks about shape:

```python
def _nic_checks_are_safe(vm: object, structural: list[Problem]) -> bool:
    if any(".nics" in p.where for p in structural):
        return False
    return (
        isinstance(vm, dict)
        and isinstance(vm.get("name"), str)
        and isinstance(vm.get("nics"), list)
        and all(isinstance(nic, dict) for nic in vm["nics"])
    )
```

and the call site passes what it already has:

```python
if structural and not _nic_checks_are_safe(vm, structural):
```

The docstring is rewritten: it argued the container-shape rationale alone, which
is now half the function.

**One deviation from the issue's sketch, deliberate.** The issue writes
`".nics" in (p.where or "")`. `Problem.where` is `str` with default `""`
(`base.py:47`); no construction site in `orchestrator/` passes `None`, and
`base.py:66` already treats it as a plain `str`. The `or ""` is unreachable, so
it is not carried.

**Rejected — making the three crash sites defensive.** This is the issue's own
instruction and it is right. `_check_nics` runs after `_check_vm_structure` by
construction; type-checking `ip_cidr`, `nameservers` and `mac` inside it
duplicates the JSON schema in Python, at three sites, and grows with every field
added to `NIC_SCHEMA`. RX-C1's argument was that the guard is where the question
belongs, and that argument survives the regression.

**Rejected — a per-field predicate.** Teaching the guard which nic fields are
safe to index (`isinstance(nic.get("ip_cidr"), str)` and so on) is the same
duplication moved one level up, and it drifts the moment `NIC_SCHEMA` changes.
`where` is derived from the schema, so consulting it cannot drift.

## 6. Surface cost

Two files.

```
 orchestrator/backends/libvirt/schema.py | 19 +  2 -
 tests/test_libvirt_schema.py            | 60 +  5 -
```

`schema.py` is **two lines of code and one signature**: the `any(...)` guard, the
`return False`, the parameter, and the argument at the call site. The other 15
added lines are the docstring paragraph the fix required. No new function, no new
file, no change to `NIC_SCHEMA` or `VM_SCHEMA`, no change to any gate, and no
change outside this one predicate.

Line numbers move below `schema.py:267`: +15 through the rest of the docstring
(old `:268` -> `:283`) and +17 from the function body down (old `:272` -> `:289`,
old `:280` -> `:297`). Grepped for citations into that range from anything live — `orchestrator/`,
`tests/`, `scripts/`, `container/`, `CLAUDE.md`, `README.md`, both pipelines. The
only two live `schema.py:NNN` citations are `variables.tf:10` (`:198-211`) and
`libvirt-module.tftest.hcl:416` (`:129`), both above the change and both
unmoved. The citations under `docs/` are dated records of the tree they were
written against and are not rewritten, per the convention `pyproject.toml`'s
`extend-exclude = ["docs"]` comment states.

**The behavioural cost, stated rather than buried.** A nic whose schema failure
is *harmless* to index now also skips its VM's nic checks. Measured case:
`mac: None` on `vms[0]` with a duplicate `ip_cidr` on `vms[1]` reported both the
schema error and the duplicate before, and reports only the schema error now —
one round trip for the operator. `mac: None` is safe only because `mac_of`'s
`or` treats it as absent; telling it apart from `mac: 5` needs the per-field
predicate §5 rejects. This is the same trade
`test_a_nic_that_is_not_a_mapping_still_skips_the_nic_checks` already records for
a non-dict nic, and the alternative on the other side of it is a crash.

## 7. The failing test

Nine assertions ship, in two tests, both new, plus one existing test updated for
the signature.

* `test_a_wrongly_typed_nic_field_reports_the_schema_error_rather_than_crashing`
  — parametrised over the eight triggers (`ip_cidr` and `mac` as the values
  `NIC_SCHEMA` types as `string`, `nameservers` as the values it types as
  `array`). Each asserts the **whole** fatal list equals one named problem at the
  right `where`, through `schema.validate` and again through
  `core_validate(cfg, registry)`, because the defect was the loss of everything
  else and because `config.load` runs the composed path for all four verbs.
* `test_the_guard_refuses_when_the_schema_failure_is_inside_a_nic` — the new
  clause pinned directly, accepting and rejecting, in the style the file's
  header demands and the sibling predicate test already uses.
* `test_a_vm_that_is_not_a_mapping_still_skips_the_nic_checks` — unchanged in
  substance, updated to pass the new second argument.

Proved able to fail by reverting `schema.py` alone and leaving the tests:

```
$ git checkout -- orchestrator/backends/libvirt/schema.py
$ pytest -q tests/test_libvirt_schema.py -k "wrongly_typed_nic_field or guard_refuses or not_a_mapping"
10 failed, 1 passed, 85 deselected
```

The eight parametrised cases fail with the eight exceptions listed in §1; the two
predicate tests fail with
`TypeError: _nic_checks_are_safe() takes 1 positional argument but 2 were given`.
Restoring `schema.py`: `11 passed`.

## 8. Verification

`just check` on this branch: six lint gates ok, `ty` clean,
**`448 passed, 25 skipped`**, from `439 passed, 25 skipped` at `586e192`. +9,
which is the nine assertions §7 names.

RX-C1's existing test, by name, on the landed tree:

```
$ pytest -q tests/test_libvirt_schema.py -k structural_error_outside_nics
2 passed, 94 deselected
```

Both parameters — `vcpus: 0` and the unexpected key `cpus` — still report the
structural problem *and* the duplicate address. End to end through the CLI, the
same case:

```
$ python -m orchestrator.cli validate rxc1.yaml
  error [vms[0].vcpus]: 0 is less than the minimum of 1
  error [vms[1].nics[0].ip_cidr]: address 192.168.122.60 is already used by vms[0].nics[0]
  warning [image.source_qcow2]: cannot read /images/golden.qcow2 ...
exit=1
```

The two non-crash behaviour changes, `586e192` against this branch, through
`schema.validate`:

| case | `586e192` | here |
|---|---|---|
| `nameservers: "192.168.122.1"` (a bare string) | 14 fatal — the schema error plus one bogus "does not appear to be an IPv4 or IPv6 address" per character | 1 fatal, the schema error |
| `mac: None` on `vms[0]`, duplicate `ip_cidr` on `vms[1]` | 2 fatal — the schema error and the duplicate | 1 fatal, the schema error only |

The first is a win the issue did not claim. The second is §6's cost.

No libvirt connection was opened by anything in this work. The repro configs
point at `hypervisor.invalid`; `validate` is offline by construction and
`test_validate_is_offline` pins it. No rig, image or smoke gate was run.

## 9. Non-goals

* **Making `_parse_interface`, the `nameservers` loop or `mac_of` defensive.**
  §5 rejects it, following the issue.
* **Reporting more than one problem per VM once its nics are unreadable.** Doing
  that means running `_check_nics` against data the schema rejected, which is the
  defect. The round trip is the price and §6 records it.
* **Hardening the substring test into a path parse.** Measured instead: a VM
  key literally named `x.nics` or `a.nics.b` produces
  `where = "vms[0]"`, because jsonschema locates an `additionalProperties` error
  at the parent object rather than at the offending key, and the duplicate
  address on the next VM is still reported. `where` is otherwise built from
  indices (`vms[0]`), never from operator-supplied names.
* **`b58f924`'s behaviour as a target.** It is the tree that loses RX-C1's
  duplicate report. Restoring it would close this issue by reopening #89.
* **Any other lane's files.** Two files changed, plus this plan and the review.
