# Scoped review — lane `lane/fix-112`

**Input: `git diff origin/master...lane/fix-112` and nothing else.** One code
commit, `6507552`, two files:

```
 orchestrator/backends/libvirt/schema.py | 19 +  2 -
 tests/test_libvirt_schema.py            | 60 +  5 -
```

It carries `#112` and nothing else. The plan it was written against is
`docs/plans/issue-112.md`.

Everything below was run in the worktree. No libvirt connection was opened by
anything in this review — the repro configs point at `hypervisor.invalid`, and
`validate` is offline by construction. No rig, image, smoke or `--write-baseline`
command was run.

---

## Lens 1 — did it do what the plan said?

Four hunks, matched against `docs/plans/issue-112.md` §5.

| hunk | plan | verdict |
|---|---|---|
| `schema.py:247` call site, `_nic_checks_are_safe(vm, structural)` | §5, second block | verbatim |
| `schema.py:258` signature, `structural: list[Problem]` | §5, first block | verbatim |
| `schema.py:286-287` the `any(...)` guard | §5, first block, minus the `or ""` | verbatim, and §5 declares the deviation |
| `schema.py:268-282` docstring paragraph | §5 last line ("The docstring is rewritten") | present |
| `tests/test_libvirt_schema.py` — 8-case parametrised test, 1 predicate test, 1 signature update, 1 import | §7 | verbatim |

**Nothing in the diff is undescribed by the plan.** No file outside the two is
touched: not `orchestrator/config.py`, not `orchestrator/backends/base.py`, not
`tests/conftest.py`, not `NIC_SCHEMA` or `VM_SCHEMA`, not `.claude/`, not
`docs/cve-baseline.json`, not any `scripts/` or pipeline file. The three crash
sites the issue forbids touching — now `schema.py:607`, `:610`, `:622` — are
byte-identical to `origin/master`.

Re-derived rather than trusted:

* **The `or ""` deviation is sound.** `Problem` is a frozen dataclass with
  `where: str = ""` (`base.py:47`). `grep -rn "where=None" orchestrator/` returns
  nothing, `problems_from` builds `where` as `(at + err.json_path[1:]).removeprefix(".") or root`
  with `root: str = ""`, and `base.py:66` already indexes it as a plain `str`.
  `ty check` is clean with the plain `p.where`.
* **The issue's forbidden alternative was not taken by accident either.** Nothing
  in `_check_nics`, `_parse_interface`, `_parse_address` or `mac_of` gained an
  `isinstance`, a `try`, or a default. Confirmed by diffing those functions
  against `origin/master`: zero changed lines.
* **The plan's `+19/−2` and `+60/−5` are the landed numbers**, counted with a
  blank-line-aware `grep -c '^+[^+]\|^+$'` rather than the pattern that silently
  drops added blank lines. Of the 19 added lines in `schema.py`, 15 are the
  docstring and 4 are code (the call-site argument, the parameter, the `any(...)`
  and the `return False`). The plan says exactly this.

## Lens 2 — do the new tests have teeth?

Nine assertions ship. All nine were proved able to fail by reverting the
production file alone and leaving the tests in place:

```
$ git checkout origin/master -- orchestrator/backends/libvirt/schema.py
$ pytest -q tests/test_libvirt_schema.py -k "wrongly_typed_nic_field or guard_refuses or not_a_mapping"
10 failed, 1 passed, 85 deselected
```

The eight parametrised cases fail with the eight distinct exceptions the plan's
§1 lists — six `TypeError`, two `AttributeError` — and the two predicate tests
fail with `TypeError: _nic_checks_are_safe() takes 1 positional argument but 2
were given`. Restoring the file: `11 passed`.

Three things about the assertions themselves, checked rather than assumed:

* **They assert the whole fatal list, not a substring.** `assert [(p.where,
  p.message) for p in errors(problems)] == expected`. This is the shape the
  defect demanded: the defect was the *loss* of every other problem, so a
  `"is not of type" in messages(...)` would have passed on a tree that reported
  the right error and dropped the rest.
* **They exercise the composed path too.** Each case runs `schema.validate(cfg)`
  and then `core_validate(cfg, registry)`, the path `config.load` takes for all
  four verbs. Measured that reusing one `cfg` across both calls is safe:
  `core_validate` does not mutate it (JSON round-trip compared before and after,
  identical).
* **`test_gates.py` is satisfied.** No conditional skip was introduced, so
  neither `conftest.gate()` nor `conftest.require()` was needed; the AST walk
  passes (`112 passed` for `test_libvirt_schema.py` and `test_gates.py`
  together).

**What the tests do not cover, stated:** the two non-crash behaviour changes
(§Lens 3, R1). Neither is asserted anywhere. They are measured in the plan and
in this review and are not pinned by a test.

## Lens 3 — what moved

### Line numbers

`schema.py` grows 17 net lines inside one function. Old `:268` → `:283` (+15,
the rest of the docstring); old `:272` → `:289` (+17, the body and everything
below it). The three crash sites move `:590`→`:607`, `:593`→`:610`,
`:605`→`:622` without changing content.

Citations into the moved range, from everything that is not archived evidence:

```
$ grep -rn "schema\.py:[0-9]" . --exclude-dir=.venv --exclude-dir=.git \
      --exclude-dir=docs --exclude-dir=__pycache__ --exclude-dir=.pytest_cache
orchestrator/backends/libvirt/tofu/variables.tf:10   (schema.py:198-211)
tests/libvirt-module.tftest.hcl:416                  (schema.py:129)
```

Both are above the change and both still land on what they name. `CLAUDE.md`,
`README.md`, `scripts/`, `container/`, `.github/` and `.gitlab-ci.yml` carry no
`schema.py:NNN` citation at all. Nine of the fifteen `schema.py:NNN`
citations in `docs/plans/issue-89.md` fall in the moved range (`:296-303`,
`:346-350`, `:509`, `:533-554`, `:534-538`, `:539-554`, `:558-563` among them)
and now point 17 lines low; they are
dated records of the tree they were written against and are not rewritten, which
is the convention `pyproject.toml`'s `extend-exclude = ["docs"]` comment states
for everything under `docs/`.

### The docstring's own claims

Each verified against the tree it now ships in:

| claim | check |
|---|---|
| `config.py:117-119` rules out the edit round trip | `:117-119` is "Raises `ConfigError` carrying *every* problem rather than the first: an operator editing a config at a site should not have to round-trip once per typo." ok |
| `_check_target` wraps `urlsplit` against the same class of unwind | `schema.py:388-402`, and its comment says "unwound past every other check and past `config.load`'s 'every problem rather than the first'". ok |
| added by the same commit that added this guard | `git show e555fe9 -- orchestrator/backends/libvirt/schema.py` carries both `+def _nic_checks_are_safe` and `+        parts = urlsplit(uri)`. ok |
| `ip_cidr:` blank in YAML is `None` | measured through the CLI, not asserted: `error [vms[0].nics[0].ip_cidr]: None is not of type 'string'`. ok |
| `problems_from` puts the failing path in `where` | `base.py:84-89`. ok |

### R1 — the plan's §6 names one instance of the cost, and there are more

`docs/plans/issue-112.md` §6 records that a nic whose schema failure is harmless
to index now also skips its VM's nic checks, and gives `mac: None` as the case.
Measured, there is at least one more, and it is likelier than `mac: None`:

| case | `586e192` | this branch |
|---|---|---|
| `nics[0]` carries an unexpected key (`mtu: 9000`), duplicate `ip_cidr` on `vms[1]` | 2 fatal — the unexpected key **and** the duplicate | 1 fatal — the unexpected key only |
| `mac: None`, duplicate `ip_cidr` on `vms[1]` | 2 fatal | 1 fatal |

Both are the same mechanism: a schema failure inside a nic that `_check_nics`
could have survived. The guard cannot tell them apart from a blank `ip_cidr`
without per-field type knowledge, which is the duplication §5 rejects. **Not a
defect and not a reason to change the fix** — but §6's single example understates
how wide the class is, and a reader could take "`mac: None`" for the whole of it.
Recorded here rather than by editing the plan, which is the record of what was
decided.

**Checked and clear:** the same skip does *not* lose a `_check_firmware` error
that `586e192` reported. On `586e192` a VM with both a blank `ip_cidr` and a
`loader` without `loader_format` crashed, so the firmware error was not reported
there either. Nothing regresses against the tip.

### R2 — one container clause is now shadowed on the path through `validate`

`all(isinstance(nic, dict) for nic in vm["nics"])` is the fourth clause. A
non-dict nic always produces a structural problem at `vms[i].nics[j]`, so the new
`.nics` test returns `False` before that clause is evaluated. The same is true of
`isinstance(vm.get("nics"), list)` when `nics` is present but not a list
(`where = vms[0].nics`); it stays live when `nics` is absent, where the `required`
error sits at `vms[0]`.

**Leave it, with the reason.** `_nic_checks_are_safe` is also called directly —
`test_a_vm_that_is_not_a_mapping_still_skips_the_nic_checks` passes `[]` — and
removing the clause would make the function's safety depend on `structural` being
complete rather than on what it can see for itself. Two lines to keep a predicate
total is the right side of this repo's surface rule. The behaviour is unchanged
either way: `test_a_nic_that_is_not_a_mapping_still_skips_the_nic_checks` passes
before and after, by the new clause instead of the old one.

### R3 — the issue's `tests/test_libvirt_schema.py:363` anchor is wrong

At `586e192`, `:363` is `assert "already used by" in out` — the closing line of
RX-C1's own parametrised test. The non-dict **nic** case the issue means is
`:380`; the non-dict **VM** predicate test is `:366`. The issue's argument is
unaffected: neither test supplies a nic that is a dict with a wrongly-typed
field, which is why nothing caught this. Already recorded in the plan §3 as C2;
repeated here because the issue body is not edited by this branch.

---

## Ledger

### Raised

| id | finding | severity | disposition |
|---|---|---|---|
| R1 | plan §6 gives one example of a class with at least two members; an unexpected nic key is the likelier one | low | recorded, not fixed — the plan is a record of a decision |
| R2 | the fourth container clause is shadowed through `validate` | nit | leave it, reason above |
| R3 | the issue's `:363` anchor names the wrong test | nit | corrected in plan §3 C2 |

### Confirmed

* The regression, on three trees, through the CLI, with the exact strings the
  issue records for `b58f924` and `586e192`.
* Eight triggers, not one, and two of them raise `AttributeError` rather than
  `TypeError` — the issue's body says `TypeError` throughout.
* RX-C1's case still reports both problems: the existing parametrised test
  (`2 passed`, both parameters) and end to end through
  `python -m orchestrator.cli validate`.
* A VM key literally containing `.nics` does not fool the substring test:
  jsonschema locates an `additionalProperties` error at the parent object, so
  `where` is `vms[0]` and the duplicate on the next VM is still reported.
* A bare-string `nameservers` improves from 14 fatal problems to 1.
* The forbidden remedy was not taken: the three crash sites are byte-identical
  to `origin/master`.

### Refuted

* Nothing. Every claim in the issue body was reproduced or corrected, and both
  corrections (C1, C2/R3) leave the diagnosis and the recommended fix standing.

### Not closed by this branch

* `#89`/`#102` are untouched. This branch neither reopens nor re-decides RX-C1;
  it makes RX-C1's improvement survive a nic-internal schema failure.
* The general question of whether `_check_nics` should be able to report anything
  once its VM's nics fail the schema. §9 of the plan lists it as a non-goal, and
  R1 is the measured cost of leaving it there.

### Verification, on the landed tree

`just check`: six lint gates ok, `ty` clean, `448 passed, 25 skipped` — from
`439 passed, 25 skipped` at `586e192`, +9 for the nine assertions added.
