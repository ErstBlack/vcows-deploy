# Issue #78 — the domain type is unasserted, so an edit to `qemu` boots under TCG and reports success

Lane `lane/tofu-module`. Reverified at `aed962d`. Transcript:
`docs/review-tofu-module/reverify/RX-D3.txt`.

## 1. Reverification verdict

**Reproduced at `aed962d`, unchanged from the pin, and the issue's own
"re-checked against `a3068e3`" half is confirmed with one corrected line
number.**

`orchestrator/backends/libvirt/tofu/main.tf:88` `type = "kvm"` → `"qemu"`:

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
10 passed in 23.45s          exit=0
$ .venv/bin/python -m pytest -q
411 passed, 25 skipped       exit=0
```

Both identical to the 2026-08-31 measurement at `672a500`. The module directory
is byte-identical to the pin (`git diff 672a500..HEAD -- .../tofu/` is empty), and
the 88 lines `454ee7c` added to `tests/libvirt-module.tftest.hcl` do not touch
the domain type: `grep -n 'libvirt_domain\.vm\[…\]\.type'` over the file at HEAD
returns nothing across 47 `assert` blocks.

The smoke gate makes it worse exactly as the issue says.
`scripts/smoke-libvirt.sh` is the first thing anywhere to assert a domain type
and it asserts `<domain type='qemu'`, so the mutant now passes the tftest suite
**and** the smoke gate.

## 2. Anchor table

| anchor | state at `aed962d` |
|---|---|
| `orchestrator/backends/libvirt/tofu/main.tf:88` `type = "kvm"` | ok, exact |
| `tests/libvirt-module.tftest.hcl` — 47 `assert` blocks, none reads `.type` | ok |
| `tests/libvirt-module.tftest.hcl:58-66` — the sizing `alltrue`, where `os.type == "hvm"` lives at `:64` | ok |
| `scripts/smoke-libvirt.sh:391` `contains "$xml" "<domain type='qemu'"` | **moved** — the issue says `:389` |
| `scripts/smoke-libvirt.sh:390` `check "the domain runs under TCG, not KVM"` | ok |
| `scripts/smoke-libvirt.sh:148-160` — writes `$WORK/smoke_override.tf` | ok |
| `scripts/smoke-libvirt.sh:135` `cp "$MODULE"/*.tf "$WORK/"` | ok |
| `scripts/smoke-libvirt.sh:211,212,474,481` — `init`, `validate`, `apply`, `destroy` | ok; **no `tofu test`** |
| repo-wide `kvm`: `main.tf:88`, three XML fixtures, `test_libvirt_destroy.py:94`, two comment lines in `smoke-libvirt.sh` | ok |

## 3. Corrections to the issue body

**C1 — the smoke assertion is at `scripts/smoke-libvirt.sh:391`, not `:389`.**
`:389` was right at `a3068e3`, which is the commit the issue re-checked against.
`aed962d` (the eight masked returns) added two lines above it inside
`assert_domain`. Measured both ways:

```
$ grep -n "domain type='qemu'" scripts/smoke-libvirt.sh                       391
$ git show a3068e3:scripts/smoke-libvirt.sh | grep -n "domain type='qemu'"    389
```

**C2 — the reproduction report's "43 `assert` blocks" is now 47.**
`docs/review-2026-08-31/verify/CD-mediums.md` counted 43 at `672a500`; `454ee7c`
added four. The claim that matters — *none* of them reads
`libvirt_domain.vm[*].type` — still holds at 47.

Nothing else in the body needed changing. The provider still marks `type`
required, so deletion is still loud and only a value edit is silent.

## 4. The defect

One hardcoded constant, asserted by nothing, whose wrong value is silent at
every later stage.

`main.tf:88` is the only place the domain type is decided. There is no config
field for it (`config.py` and `backends/libvirt/schema.py` have no key), it is
absent from `render.py`, from `outputs.tf`'s inventory contract
(`name`, `uuid`, `configured_address`, `disks`), and from the XML parsing in
`preflight.py` and `destroy.py`, which read the marker, the disks and the name.
`<domain type='qemu'>` is TCG: every VM defines, boots, completes cloud-init and
the deploy reports success — unaccelerated, with nothing anywhere saying so.

`a3068e3` closed the gap in the wrong direction. The smoke job deliberately runs
without `/dev/kvm`, so it writes `smoke_override.tf` into a temp copy of the
module setting `type = "qemu"` and `cpu = null`, and then asserts the overridden
value. That assertion is correct for what that job is testing and is useless as a
guard on the shipped value: it passes whether `main.tf` says `kvm` or `qemu`.

## 5. The fix

**One `assert` block in `tests/libvirt-module.tftest.hcl`, inside the existing
`run "the_module_renders_what_the_acceptance_run_settled"`, immediately after the
sizing block that ends at `:68`.**

```hcl
  assert {
    condition = alltrue([
      for k, v in var.vms : libvirt_domain.vm[k].type == "kvm"
    ])
    error_message = "a domain is not asking for KVM: it boots under TCG emulation and the deploy still reports success"
  }
```

plus a nine-line comment saying why it is its own block and why the smoke gate
cannot carry it. Measured: **+15 lines, one file, no new `run` block, no new
file, no production change.**

Its own block rather than a clause appended to the `alltrue` at `:58-66`: that
block's message is "a domain does not carry the sizing, machine type or arch its
tfvars asked for", and folding a TCG-vs-KVM failure into it would report the
wrong sentence for the one failure here whose consequence is silent-wrong rather
than a wrong number.

### Why this survives the override path — measured, not assumed

The issue asks for this explicitly, and the answer has two parts.

**Part 1: the smoke gate never evaluates the tftest.** It builds `$WORK` with
`cp "$MODULE"/*.tf "$WORK/"` (`:135`), the committed lock, a substituted
`tofurc` and a hand-written `main.auto.tfvars.json`, then writes
`smoke_override.tf` beside them (`:148-160`). `tests/libvirt-module.tftest.hcl`
lives in `tests/`, not in the module directory, so `*.tf` does not match it and
nothing copies it. `grep -n tftest scripts/smoke-libvirt.sh` returns one hit and
it is a comment in the header. The four `tofu -chdir="$WORK"` invocations are
`init` (`:211`), `validate` (`:212`), `apply` (`:474`) and `destroy` (`:481`).
There is no `tofu test` anywhere in the script. The new assertion is therefore
not evaluated on the override path at all, which is the reason it is safe.

**Part 2: the counterfactual, run anyway.** Assembling the hypothetical directory
by hand — module `.tf` files, the lock, the golden tfvars, the tftest **and**
`smoke_override.tf` — and running `tofu test` there:

```
run "the_module_renders_what_the_acceptance_run_settled"... fail
  line 71: libvirt_domain.vm[k].type == "kvm"      -> Test assertion failed
  line 176: libvirt_domain.vm["app01"].cpu.mode    -> Attempt to get attribute from null value
```

The tftest and the override are already mutually exclusive **before** this change:
`smoke_override.tf` sets `cpu = null`, and `:154` asserts
`cpu.mode == "host-passthrough"`. So there is no world in which the two are run
together today, and this change does not create one. If a future change ever
wanted to run the tftest inside the smoke gate's copy, it would have to reconcile
`cpu` first and would notice `type` in the same breath.

### Rejected

* **Asserting `kvm` in `scripts/smoke-libvirt.sh`.** That job runs TCG on
  purpose; the assertion would be false by construction. The issue says this and
  it is right.
* **Reading `type` back through `outputs.tf`.** It would put a value in the
  inventory contract that nothing consumes, to buy an assertion the tftest
  already gives for free.
* **Making `type` a variable so `render.py` writes it.** New surface across four
  files (config schema, `render.py`, `variables.tf`, the golden fixture) for a
  value that has exactly one correct answer at every site vcows ships to.

## 6. Surface cost

`tests/libvirt-module.tftest.hcl`, +15 / −0. Six lines of HCL, nine of comment.
No new file, no new `run` block, no change to `orchestrator/`, `scripts/` or any
gate definition. This is the smallest change that closes the finding, and it is
in the file whose entire job is reading the module's values.

## 7. The failing test

The assertion **is** the test, and it was proved live in both directions.

Green on the shipped module:

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
10 passed in 23.17s     exit=0
$ .venv/bin/python -m pytest -q
411 passed, 25 skipped  exit=0
```

Red on the mutation it exists to catch (`main.tf:88` → `"qemu"`):

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
  on libvirt-module.tftest.hcl line 79, in run "the_module_renders_what_the_acceptance_run_settled":
  79:     condition = alltrue([
  80:       for k, v in var.vms : libvirt_domain.vm[k].type == "kvm"
  81:     ])
a domain is not asking for KVM: it boots under TCG emulation and the deploy
still reports success
FAILED tests/test_tofu_module.py::test_the_module_renders_what_the_acceptance_run_settled
1 failed, 9 passed      exit=1
```

## 8. Verification

1. `VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py`
   → `10 passed`.
2. `just check` → six lint gates ok, `ty` clean, `411 passed, 25 skipped`.
   The assertion adds no test, so the count does not move.
3. The green→red proof in §7, re-run on the branch before pushing.
4. `just lint`'s `tofu fmt` gate covers the new HCL formatting.
5. **Not** `just smoke-libvirt`. CI's `smoke` job is what runs it; it is
   unchanged by construction (§5, part 1) and must stay green.

## 9. Non-goals

* The smoke gate's `smoke_override.tf`, its TCG choice, and its
  `<domain type='qemu'` assertion. All correct for what that job tests.
* `cpu = { mode = "host-passthrough" }` — already asserted at `.tftest.hcl:154`.
* Making the domain type configurable.
* The other fifteen `main.tf` attributes and branches that survive mutation.
  They are #87's RX-D6 and RX-D8, planned separately in the same lane.
* `#75`, the RHEL `.fd` firmware path. Different root cause surface, deferred to
  a lane with a real libvirtd.
