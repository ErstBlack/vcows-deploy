# Scoped review — lane `lane/tofu-module`

**Input: `git diff origin/master...lane/tofu-module` and nothing else.** No
repo-wide sweep, no adjacent findings. Every line number below was read at
`e9c9230` (branch tip) or `d9d9252` (`origin/master`).

Three commits:

| | commit | closes | files | +/− |
|---|---|---|---|---|
| C1 | `ba59fa1` Correct variables.tf's uri description | **one of #90's eleven items; #90 stays open** | `variables.tf` | +1 / −1 |
| C2 | `28d9c53` Assert the domain asks for KVM | `#78` | `libvirt-module.tftest.hcl` | +15 / −0 |
| C3 | `e9c9230` Close the seven gate-machinery gaps | `#87` | `conftest.py`, `libvirt-module.tftest.hcl`, `test_gates.py`, `test_tofu_driver.py` | +194 / −33 |

Whole branch: 5 files, +210 / −34.

**Bench.** `just check` on `origin/master` before any change: six lint gates ok,
`ty` clean, `411 passed, 25 skipped`, exit 0. After all three: six lint gates ok,
`ty` clean, **`412 passed, 25 skipped`**, exit 0. `just test-tofu`: `412 passed,
25 skipped`. `VCOWS_GATES=all`: `412 passed, 25 errors`, exit 1 — same shape as
before, so `all` still means what it meant. `just smoke-libvirt` was not run, the
rig at `qemu+ssh://vcows@vcows/system` was not contacted, and
`scripts/image-scan.sh --write-baseline` was not run.

**The plans' pin is this tree.** The plans were reverified at `aed962d`;
`origin/master` here is `d9d9252`. `git rev-parse aed962d^{tree}` and
`d9d9252^{tree}` are both `08b71654b20d2978664e610a701e020add799f1d`, so every
line number and every measurement in the plans applies to this branch's base
byte-for-byte.

---

## C1 — `ba59fa1`, `variables.tf:10`

### Lens 1 — did it do what the plan said?

`docs/archive/plans/issue-90-variables-tf.md` §5 asks for the `qemu+ssh://` →
`qemu+sshcmd://` correction plus one clause naming preflight and citing
`schema.py:198-211`. The diff is that one line and nothing else. Nothing in the
diff the plan does not describe.

Re-measured before writing it, all four anchors exact:

```
render.py:61                  "uri": connection_uri(target, "sshcmd"),
tests/golden/libvirt.tfvars.json:9    "uri": "qemu+sshcmd://vcows@vcows/system",
preflight.py:74               conn = libvirt.open(connection_uri(cfg["target"]["libvirt"]))
grep -rn '"uri"' orchestrator/ -> the declaration, schema.py:147,150,229,349, and
                                 render.py:61. One writer.
```

§8's own check passes: `grep -n 'qemu+ssh' variables.tf` returns one line, on
which the only bare `qemu+ssh://` is inside the new preflight clause.

The `schema.py:198-211` citation is right for what the sentence claims. `:204-207`
is the C-client half, `:208-211` the provider half. `:212-214` describes what
`sshcmd` does instead, which the sentence does not assert.

### Lens 2 — does the new test have teeth?

**There is no new test, by design** (plan §7: "None, and none should be added").
The teeth are pre-existing, and they are live. Reverting the *fact* the
description now states — `render.py:61` `connection_uri(target, "sshcmd")` →
`connection_uri(target)` — is red:

```
$ .venv/bin/python -m pytest -q tests/test_libvirt_render.py
FAILED tests/test_libvirt_render.py::test_matches_the_golden_file
FAILED tests/test_libvirt_render.py::test_the_provider_is_given_the_transport_that_can_reach_the_host
2 failed, 11 passed
$ # reverted
13 passed
```

So the fact the old description got wrong is enforced twice — by
`test_libvirt_render.py:159-161` and by the golden pin — and a test that grepped
the description string would add surface for nothing.

### Lens 3 — what moved?

Nothing. The line is replaced in place; `variables.tf` is the same length. No
file in the repo cites `variables.tf:10` outside `docs/review/2026-08-31/` and
this lane's own plan, and both are archived evidence describing the pre-change
state.

---

## C2 — `28d9c53`, `#78`

### Lens 1 — did it do what the plan said?

`docs/archive/plans/issue-78.md` §5 asks for one `assert` block inside
`run "the_module_renders_what_the_acceptance_run_settled"`, immediately after the
sizing block that ends at `:68`, plus a comment saying why it is its own block
and why the smoke gate cannot carry it. Measured cost in the plan: **+15 / −0**.

The diff is `@@ -68,0 +69,15 @@` — one hunk, +15 / −0, at exactly that
insertion point. The `condition` and `error_message` are the plan's verbatim.
The comment is eight lines plus a blank separator rather than the plan's "nine
lines"; the total is the +15 the plan measured. Nothing else in the diff.

One thing the plan does not describe, worth naming: the block now sits between
the sizing `alltrue` and the pool `alltrue`, which were adjacent under the
`// -- the config values that reach the domain` header. The plan chose that
position explicitly ("immediately after the sizing block that ends at `:68`") and
it was followed rather than second-guessed.

### Lens 2 — does the new test have teeth?

There is no production hunk to revert — the assertion *is* the change — so the
equivalent proof is run both ways, and the second half is what shows the gap was
real.

**Without the new block** (`git stash` on the tftest, `main.tf:88` → `"qemu"`):

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
10 passed in 24.09s          exit=0
```

**With the new block**, same mutation:

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
  on libvirt-module.tftest.hcl line 79, in run "the_module_renders_what_the_acceptance_run_settled":
  79:     condition = alltrue([
  80:       for k, v in var.vms : libvirt_domain.vm[k].type == "kvm"
  81:     ])
a domain is not asking for KVM: it boots under TCG emulation and the deploy
still reports success
FAILED tests/test_tofu_module.py::test_the_module_renders_what_the_acceptance_run_settled
1 failed, 9 passed in 23.86s exit=1
```

Reverted: `10 passed`, exit 0. This reproduces the plan's §7 exactly, including
the `79-81` line numbers.

### Lens 3 — what moved?

`tests/libvirt-module.tftest.hcl` gained 15 lines after `:68`, so **every anchor
below `:68` in that file moved +15 in this commit** (and further in C3 — the
combined figures are in C3's lens 3, which is the one to read).

Nothing outside `docs/` cites a line number in that file. `grep -rn
'tftest\.hcl:[0-9]'` over live files returns nothing: `tests/test_tofu_module.py:165`
names the path, not a line, and `scripts/smoke-libvirt.sh:6` names the file in
prose. The hits are all in `docs/review/2026-08-30/`, `docs/review/2026-08-31/`
and this lane's plans, which are archived evidence pinned to the commits they
describe and are deliberately not rewritten.

The smoke assertion the commit body cites is at `scripts/smoke-libvirt.sh:391`,
re-measured here (`grep -n "domain type='qemu'"` → `391`). `#78`'s body says
`:389`, which was right at `a3068e3`; `d9d9252` added two lines above it inside
`assert_domain`. That correction is carried in the commit body.

---

## C3 — `e9c9230`, `#87`

### Lens 1 — did it do what the plan said?

`docs/archive/plans/issue-87.md` §5, item by item. Everything in the diff maps to a
planned item, but **five items cost more than the plan estimated and one moved a
clause the plan placed differently.** Both are listed here rather than buried.

| item | plan §5 | landed | verdict |
|---|---|---|---|
| RX-D2 | `_parse()` helper in `conftest.py`; test calls it with `"tofu, image"` and `""`. "~4 lines in conftest.py and 6 in test_gates.py" | `conftest.py` +7/−1; the test also gains `import os`, `from tests import conftest`, loses its `monkeypatch` parameter, and carries a **third assertion** | **diff carries more than the plan describes — see below** |
| RX-D1 | the `try/except pytest.skip.Exception` wrapper, "about 5 lines" | verbatim, plus a five-line docstring saying what the gap was | as planned + docstring |
| RX-D5 | the measured `_references` / `BANNED` / `_is_banned` diff, **+22 / −16** | that diff verbatim; total for the file is +66/−31 because of docstrings and two citation fixes | as planned, larger comment |
| RX-D8 | a third `run` block, "~25 lines" | **+54 lines** | as planned, cost understated — see below |
| RX-D6 | clauses into the sizing `alltrue` at `:58-66` "(or a sibling with its own message)", into the pool `alltrue` at `:69-76`, and into the firmware block; "around 20 lines" | **+39 lines** across three hunks; the base-volume pair is a **sibling assert, not a clause inside the pool `alltrue`** | one placement deviation, stated below |
| RX-D7 | the measured test, `tests/test_tofu_driver.py` only, **+21** | that test verbatim, plus an eight-line docstring: **+27**; placed after `test_init_runs_on_a_clock_and_apply_does_not` rather than appended at EOF | as planned, relocated |
| RX-D10 | one number, `destroy.py:456-461` → `:440-445` | exactly that | as planned |

**RX-D2's third assertion.** §5.1 gives two assertions and says mutation A
(`GATES: set = set()`) "then fails on `_parse("")`'s call site being gone". It
does not: `_parse` is still defined under that mutation and `_parse("")` still
returns `set()`, so neither of the plan's two assertions can see it. The third
assertion — `conftest.GATES == conftest._parse(os.environ.get("VCOWS_GATES", ""))`
— is the minimum that ties the constant to the function, and with it mutation A
is red under `VCOWS_GATES=tofu` and `=all`. **It is still green under a bare
`pytest -q`, and that is not fixable:** with nothing demanded, `set()` and the
correct parse of an unset variable are the same value, so the mutation has no
observable behaviour there. §7.1's "must fail (today: 411 passed, 25 skipped,
exit 0 under both default and `all`)" overstates what a behavioural test can
reach. The commit body says so in those terms.

**RX-D6's base-volume placement.** §5.5 asks for `libvirt_volume.base[0].pool ==
var.pool` and `.name == var.base_volume.name` "into the pool `alltrue` at
`:69-76`". That comprehension is `for k, v in var.vms`, and
`libvirt_volume.base[0]` is not per-VM: folding it in would evaluate the same
constant once per VM and, more to the point, report "a volume lands in a pool the
config never named" for a failure about the golden image's *name*. It landed as a
sibling `assert` with its own message instead — the same structure §5.5 itself
offers for the parallel sizing case ("or a sibling with its own message"). Same
two conditions, same file, same commit.

**RX-D8's cost.** 54 lines, not ~25. Fourteen are the header comment §5.4
explicitly requires (repeating the wholesale-override caveat at `:307-326` and
saying the block closes nothing of `#75`); twenty-six are the `vms` object, which
must carry every attribute of the variable's object type — the existing
`a_bridged_nic_renders_source_bridge` block spends the same twenty-six on the
same thing. The four remaining are the two asserts. The estimate was low; the
shape is the one the plan asked for.

**Two things in the diff no plan describes, both introduced by this commit.**
`tests/test_gates.py:81` cited `conftest.py:53` for the `skipif` that `gate()`
returns; adding `_parse` moved that line to `:59`. And the new outermost-only
comment names the lines it is about, which are `:230` and `:238` after RX-D1
added an `except`. Both are drift this commit created inside a file it owns, so
both are corrected in it.

**What the plan told the lane not to do, and which was not done.** No coverage
was added for `main.tf:34`'s false branch — `454ee7c`'s
`a_prebuilt_base_volume_is_used_in_place` already catches it, re-measured below.
`orchestrator/tofu.py` is not in the diff. `_sources()` is still non-recursive.
The `VCOWS_GATES` whitespace behaviour is unchanged. `require("pycdlib", …)` did
not gain `allow_module_level=True`.

### Lens 2 — does the new test have teeth?

Every assertion in this commit was run against the mutation it targets, then
reverted. Twenty-one mutations in all.

**RX-D2 / RX-D1 — `tests/conftest.py` mutated, `tests/test_gates.py` as landed:**

```
=== RX-D2 MUTATION A: GATES: set = set() ===
  <none>                 16 passed
  VCOWS_GATES=tofu       1 failed, 15 passed
  VCOWS_GATES=all        1 failed, 15 passed
  whole suite, all       1 failed, 410 passed, 25 skipped     (was: 411 passed, 25 skipped, exit 0)
  whole suite, default   411 passed, 25 skipped               (unobservable there -- see lens 1)
=== RX-D2 MUTATION B: _parse gains .strip() ===
  test_gates.py          1 failed, 15 passed                  (was: 16 passed)
=== RX-D1 MUTATION: delete require()'s demanded branch ===
  test_gates.py -rs      1 failed, 15 passed                  (was: 15 passed, 1 skipped)
  whole suite            1 failed, 410 passed, 25 skipped     (was: 410 passed, 26 skipped, exit 0)
=== RESTORED ===
  test_gates.py          16 passed
```

**RX-D5 — each bypass appended to `tests/test_version.py`, scanner run before and
after.** "before" is `origin/master`'s `test_gates.py` and `conftest.py`, restored
via `git stash`; the file was restored byte-identical both times
(`git status --porcelain tests/test_version.py` empty).

```
spelling                                  before                 after
pytestmark = pytest.mark.skip             16 passed              1 failed, 15 passed  test_version.py:56: pytest.mark.skip
import pytest as _pt; _pt.mark.skip       16 passed              1 failed, 15 passed  :56: _pt.mark.skip
from pytest import skip as _s; _s(...)    16 passed              1 failed, 15 passed  :54: skip
raise unittest.SkipTest(...)              16 passed              1 failed, 15 passed  :58: unittest.SkipTest
pytest.param(1, marks=pytest.mark.skip)   16 passed              1 failed, 15 passed  :57: pytest.mark.skip
control: pytest.importorskip              1 failed, 15 passed    1 failed, 15 passed  :58: pytest.importorskip
control: pytest.skip in a body            --                     1 failed, 15 passed  :58: pytest.skip
```

The outermost-only rule was also proved necessary rather than assumed. Replacing
`inner = {id(n.value) for n in ...}` with `inner = set()` — i.e. collecting every
`ast.Attribute` — gives:

```
E   test_gates.py:230: pytest.skip
E   test_gates.py:238: pytest.skip
1 failed, 15 passed
```

Both are `pytest.skip.Exception`, a legitimate reference to the exception type.
Reverted: `16 passed`.

**RX-D6 / RX-D8 — eleven `main.tf` mutations, one at a time, each reverted.**
Command in every row: `VCOWS_GATES=tofu .venv/bin/python -m pytest -q
tests/test_tofu_module.py`. The "before" column is the plan's §1.5/§1.4 figures,
re-measured at `d9d9252`.

```
                                          before   after
:27  base name -> "wrongname.qcow2"        10 pass  1 failed, 9 passed
:28  base pool -> "nowhere"                10 pass  1 failed, 9 passed
:118 loader -> "/nowhere/OVMF.fd"          10 pass  1 failed, 9 passed
:120 loader_type -> null                   10 pass  1 failed, 9 passed
:136 nvram format -> "raw"                 10 pass  1 failed, 9 passed
:175 device -> "cdrom"                     10 pass  1 failed, 9 passed
:181 driver.name -> "bogus"                10 pass  1 failed, 9 passed
:201 nic model -> "e1000"                  10 pass  1 failed, 9 passed
:203 nic source.network -> null            10 pass  1 failed, 9 passed
:116 firmware ternary -> bare "efi"        10 pass  1 failed, 9 passed   (RX-D8)
:34  base_path fallback -> "/nowhere"      1 failed 1 failed, 9 passed   (regression check)
unmutated control                          --       10 passed
```

`:34` is the half `454ee7c` already closed. It was red before this commit and is
red after; no coverage was added for it, and it is listed here only to show it
stays red.

**RX-D7 — `orchestrator/tofu.py:256` mutated and reverted; the file is not in the
diff.**

```
$ sed -i '256s/timeout=SHORT_TIMEOUT,/timeout=None,/' orchestrator/tofu.py
$ .venv/bin/python -m pytest -q tests/test_tofu_driver.py
E   AssertionError: a capture with no clock is a CLI that hangs and never writes its record
E   assert [None, None] == [120, 120]
E     At index 0 diff: None != 120
FAILED tests/test_tofu_driver.py::test_output_and_version_run_on_the_short_clock
1 failed, 20 passed
$ # reverted; git status --porcelain orchestrator/ empty
21 passed
```

**RX-D10** has no test. Its evidence is `destroy.py` read at `e9c9230`: `:422`
`def _deletable`, `:440-444` the `owned` set the sentence describes, `:445` the
`not in owned` guard, `:456` `_deletable`'s closing `return True`, `:459`
`def _deleted_on_name_alone` — a different function, which is what the old
citation pointed at.

### Lens 3 — what moved?

**`tests/conftest.py`** — `_parse` inserted above `GATES`, +6 net:

| anchor | `d9d9252` | `e9c9230` |
|---|---|---|
| the docstring `conftest.py:7` quote | `:7` | `:7` — **unmoved** |
| `GATES = …` | `:37` | **`:43`** |
| `def demanded` | `:40` | `:46` |
| `def gate` | `:44` | **`:50`** |
| the `skipif` `gate()` returns | `:53` | `:59` |
| `def require` | `:61` | **`:67`** |
| `require`'s `pytest.skip(reason)` | `:67` | `:73` |

**`tests/libvirt-module.tftest.hcl`** — five insertions, +109 net across C2 and
C3:

| anchor | `d9d9252` | `e9c9230` |
|---|---|---|
| `run "the_module_renders_what_the_acceptance_run_settled"` | `:21` | `:21` |
| the `destroy.py` citation | `:39` | `:39` |
| the sizing `alltrue` | `:58-66` | `:58-66` |
| `// -- the seed` | `:108` | `:131` |
| `// -- firmware` | `:118` | `:141` |
| `// -- the domain` | `:148` | `:189` |
| `cpu.mode == "host-passthrough"` | `:154` | `:195` |
| `// -- devices` | `:166` | `:207` |
| `// -- what libvirt does not supply` | `:200` | `:254` |
| `// -- the inventory's half` | `:242` | `:296` |
| `// -- the two branches the golden tfvars does not take` | `:253` | `:307` |
| `run "a_prebuilt_base_volume_is_used_in_place"` | `:274` | `:328` |
| `run "a_bridged_nic_renders_source_bridge"` | `:299` | `:353` |
| `run "a_bios_domain_is_given_no_firmware_and_no_varstore"` | — | `:394` (new) |

**`tests/test_gates.py`** — `_references` grew, `_is_banned` is new, RX-D1's test
gained a wrapper: `BANNED` `:76-83` → `:87-95`,
`test_gates_is_parsed_without_whitespace_stripping` `:130` → `:148`,
`test_a_demanded_require_that_is_missing_fails` `:195-198` → `:220-233`, the
`pytest.skip.Exception` reference `:203` → `:238` (with a second at `:230`).

**`tests/test_tofu_driver.py`** — +27 after `:318`, so
`test_a_missing_binary_says_so` `:320` → `:347` and
`test_warnings_are_the_half_that_did_not_stop_the_run` `:326` → `:353`. `#17`'s
pin at `:313`/`:317` is above the insertion and unmoved.

**Who points at these — grepped, not assumed.**

`grep -rn 'conftest\.py:[0-9]' 'tftest\.hcl:[0-9]' 'test_gates\.py:[0-9]'
'test_tofu_driver\.py:[0-9]' 'variables\.tf:[0-9]'` over the whole repo, then
filtered to live files:

* **`CLAUDE.md:49` and `:53` are now stale, and this lane did not fix them.**
  `:49` says every conditional skip goes through `conftest.gate()` (`:44`) or
  `conftest.require()` (`:61`); those are `:50` and `:67`. `:53` says
  `VCOWS_GATES` (`conftest.py:37`); that is `:43`. `CLAUDE.md` is outside this
  lane's ownership, so this is recorded rather than edited. **It is the one live
  action this branch leaves behind.**
* `CLAUDE.md:47` quotes `tests/conftest.py:7` and `scripts/lint.sh:166` and
  `.claude/hooks/static-gate.sh:20` both cite `conftest.py:7`. That line did not
  move; all three stay correct.
* `CLAUDE.md:49-51`'s sentence — the scanner "fails on any bare `pytest.skip`,
  `pytest.importorskip` or `pytest.mark.skip`" — was **false** before this branch
  for the uncalled `pytest.mark.skip` spelling and is **true** after it. No edit
  needed; the code moved to meet the document.
* `tests/test_gates.py:81` and `:55` cited lines this commit moved and are
  corrected in the same commit.
* Everything else is `docs/review/2026-08-29/`, `docs/review/2026-08-30/`,
  `docs/review/2026-08-31/` and this lane's own `docs/archive/plans/`. Those are archived
  evidence pinned to the commits they describe — `ruff`'s `extend-exclude` and
  `ty`'s exclude both name `docs/` for exactly that reason — and rewriting them
  would make each document disagree with the code it quotes. Left alone
  deliberately.
* `.claude/hooks/static-gate.sh`'s content signature covers
  `.py|.sh|.tf|.ya?ml|Containerfile` and not `.hcl`. That gap is `#57`'s and is
  untouched here; it is named only because two of this branch's five files are
  `.hcl` and `.tf` respectively, so a reader checking whether the hook saw this
  work should know the answer is "the `.tf` yes, the `.hcl` no".

---

## Ledger

### Raised

* **L-R1 — `CLAUDE.md:49` and `:53` cite `conftest.py:44`, `:61` and `:37`; the
  targets are now `:50`, `:67` and `:43`.** Created by C3's `_parse` extraction.
  Outside this lane's ownership, so recorded, not fixed. One-line fix in the lane
  that owns `CLAUDE.md`; it belongs with `#90`, which is the documentation-drift
  issue and stays open.
* **L-R2 — `#87` §7.1 asks for a test that cannot exist.** `GATES: set = set()`
  has no observable behaviour under a bare `pytest -q`, so no behavioural
  assertion can turn it red there. It is caught under any non-empty
  `VCOWS_GATES`. Recorded so the next reader does not go looking for the missing
  assertion.
* **L-R3 — `#87` §5.1's stated failure mode for mutation A is wrong.** "fails on
  `_parse("")`'s call site being gone" describes coverage, not a pytest failure;
  `_parse("")` returns `set()` whether or not `GATES` calls it. The third
  assertion in the landed test is what makes the mutation red at all.
* **L-R5 — `#87` §5's and §9's citation for RX-D9's refutation is wrong.**
  Both give `docs/ci.md:52-58`; at `d9d9252` that is the smoke job's
  `mock_provider` paragraph. The `VCOWS_GATES` table the refutation actually
  rests on is `docs/ci.md:86-97`. RX-D9 is a non-goal for this lane, so the
  refutation was not re-litigated — only the pointer is corrected, so the next
  reader does not conclude the refutation is missing.
* **L-R4 — `#87`'s cost estimates for RX-D6 and RX-D8 are low by roughly 2×**
  (~20 → 39, ~25 → 54). The overrun is comment and the `vms` object the variable
  type forces, not extra assertions. Named so the surface figure in the plan is
  not carried forward as if it were measured.

### Confirmed

* **L-C1 — RX-D8 is half closed on master.** `main.tf:34`'s false branch is red
  at `d9d9252` before any change here (`1 failed, 9 passed`), caught by
  `454ee7c`'s `a_prebuilt_base_volume_is_used_in_place`. No coverage was added
  for it. Only the `firmware … : null` half was open.
* **L-C2 — RX-D1's production reach is zero.** `require("pycdlib", …)` at
  `tests/test_seed_iso.py:26` is at module scope and `conftest.require` calls
  `pytest.skip()` without `allow_module_level=True`, so an env without `pycdlib`
  is a collection error either way. The fix is taken against `conftest.py:7` and
  the commit body says so rather than implying a live defect closed.
* **L-C3 — RX-D5's bypass is the uncalled attribute, and outermost-only is
  required.** Both halves re-measured here: the called `pytest.mark.skip(reason=…)`
  forms were already caught, and the naive every-`ast.Attribute` collection
  false-positives on `test_gates.py:230` and `:238`.
* **L-C4 — the smoke assertion is at `scripts/smoke-libvirt.sh:391`.** `grep -n
  "domain type='qemu'"` → `391`. `#78`'s `:389` was right at `a3068e3` only.
* **L-C5 — `#78`'s assertion cannot collide with the smoke override.** The script
  copies `"$MODULE"/*.tf` (`:135`) and the tftest lives in `tests/`; its four
  `tofu -chdir` calls are `init`, `validate`, `apply`, `destroy` and there is no
  `tofu test` anywhere in it. Independently re-read at `d9d9252`.

### Refuted — kept so they are not re-derived

* **L-F1 — "assert `kvm` in `scripts/smoke-libvirt.sh`."** That job runs TCG
  deliberately and writes `type = "qemu"` into its own copy of the module
  (`:148-160`); the assertion would be false by construction, and its existing
  `<domain type='qemu'` check passes whether `main.tf` says `kvm` or `qemu`.
  Settled twice in `docs/archive/plans/issue-78.md` §5 and not reopened.
* **L-F2 — "read the domain type back through `outputs.tf`."** Puts a value in
  the inventory contract that nothing consumes, to buy an assertion the tftest
  gives for free.
* **L-F3 — "make the domain type a variable so `render.py` writes it."** New
  surface across the config schema, `render.py`, `variables.tf` and the golden
  fixture, for a value with one correct answer at every site vcows ships to.
* **L-F4 — "fold the KVM check into the sizing `alltrue` at `:58-66`."** That
  block's message is about a domain not carrying the sizing its tfvars asked for.
  This is the one failure in that run block whose consequence is silent-wrong
  rather than a wrong number, so it gets its own message.
  `docs/review/2026-08-31/verify/CD-mediums.md:398` proposes the fold; it is
  superseded by `docs/archive/plans/issue-78.md` §5.
* **L-F5 — "change `render.py:61` to emit `qemu+ssh://`."** That is the
  acceptance-run defect the `sshcmd` decision exists to fix, measured on the rig
  and recorded at `schema.py:198-214` and `docs/findings.md:410`.
* **L-F6 — "fix the `VCOWS_GATES` whitespace behaviour."** Documented rather than
  fixed at `CLAUDE.md:53-56`; both CI files are written without spaces because of
  it. `test_gates.py` still pins the no-strip behaviour, now by performing the
  parse instead of asserting about a monkeypatched set.
* **L-F7 — "recurse `_sources()` over `tests/**/*.py`."** Measured inert: `find
  tests -name '*.py' -mindepth 2` is empty.
* **L-F8 — "give `require("pycdlib", …)` `allow_module_level=True`."** It would
  convert today's loud collection error into a quiet skip, which is the wrong
  direction against `conftest.py:7`.
* **L-F9 — "append `,pycdlib` to `justfile:90`."** RX-D9, refuted before this
  lane and not reopened. The refutation is the `VCOWS_GATES` table at
  **`docs/ci.md:86-97`** — "`VCOWS_GATES=all` is never set", `pycdlib` listed as
  satisfied by `just dev-env` — plus `CLAUDE.md:110`. Not the `docs/ci.md:52-58`
  the plan cites; see L-R5.

### Downgraded

* **L-D1 — RX-D8, from "one fixture, every branch on the same side" to one open
  branch.** Two of its three cited fixture lines (`:3` `"create": true`, `:23`
  `"bridge": null`) are exercised by `454ee7c`'s overriding `variables` stanzas.
  Only `:15` `"firmware": "efi"` was open.
* **L-D2 — RX-D8's BIOS block is evaluation coverage, not `#75`.** It proves
  `main.tf` emits the right value on the BIOS branch. It cannot prove libvirt
  echoes that value back the way the provider planned it, which needs a real
  libvirtd. The block's own header comment says so, and the commit body repeats
  it, so a green run here is not read as having caught the RHEL `.fd` failure.
* **L-D3 — RX-D1, from "uniquely guards `pycdlib`" to "guards nothing in
  production."** See L-C2.
* **L-D4 — RX-D10's second half is record-only.** `3be2c28`'s commit message
  carries the same citation drift and a message cannot be edited. For the record,
  at `d9d9252` the two paths filters are `.github/workflows/image.yml:22-23` and
  `.gitlab-ci.yml:136-137` — the GitLab half moved again when `a3068e3` added 34
  lines above it, so the `:102-103` in the 2026-08-31 verify record should not be
  copied forward.
