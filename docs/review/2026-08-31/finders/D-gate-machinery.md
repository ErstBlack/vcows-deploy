# D — gate machinery and property tests

Dimension D · range `4eb378b..672a500`, read at `672a500` · 2026-08-31

Method: every gate and new assertion here was mutated in a `cp -a` copy of the worktree (own
`.venv`, own gitdir) and the suite re-run there — 31 Python mutations, 50 through `tofu
test`. No tracked file was edited; the rig was not touched.

## Summary

* **`#16`, `#17`, `#63`, `RW-E2`–`RW-E4`, `RW-E6`, `RW-C2`, `RW-C3`, `RW-E8` are genuinely
  fixed** — every mutation those fixes claim to catch was applied and observed to fail, and
  `#63`'s "do not make this correction" trap was avoided correctly.
* **`#14` / `RW-E1` is `PARTIAL`:** `gate()` is pinned in all three branches, `require()` is
  not. **And the line that reads `VCOWS_GATES` is pinned by nothing.**
* The `.tftest.hcl` is strong — 27 of 28 mutations of settled behaviour caught, the miss
  being the `depends_on` edge its own header calls uncatchable. 15 further attributes
  survive, one with the silent-success shape. `RW-E5` is `PARTIAL`: seven of nine named
  passthroughs asserted, nic `model` and nic `source.network` not.

## Findings

### RX-D1 — `require()`'s demanded-to-failure branch is still unpinned
**medium** · `tests/test_gates.py:195-198`, `tests/conftest.py:61-67`

Mutation: delete `require()`'s `if demanded(name): pytest.fail(...)` so it always skips.
Observed: `test_a_demanded_require_that_is_missing_fails` **skips** rather than fails — the
`pytest.skip` raised inside `pytest.raises(pytest.fail.Exception)` propagates and skips the
test itself. Full suite, `VCOWS_GATES=all` + rig + image: **435 passed, 1 skipped, exit 0**
against the 436/0 baseline; the delta is the mutation's own test skipping itself.
`require()` is the sole guard for the `libvirt` and `pycdlib` gates
(`tests/test_libvirt_errors.py:20`, `tests/test_libvirt_destroy.py:36`,
`tests/test_seed_iso.py:26`); the `tofu` call sites also carry `@needs_tofu`. End to end:
with `find_spec("libvirt")` forced `False` and `VCOWS_GATES=libvirt`, intact `require()`
gives `1 failed`, mutated gives `1 skipped, exit 0`. That is the defect `#14` was filed for,
surviving in the half the fix did not reach. Fix: the same twelve lines the other branches
got, written so an escaping `Skipped` is a failure. No production surface.

### RX-D2 — nothing pins the line that turns `VCOWS_GATES` into `GATES`
**medium** · `tests/conftest.py:37`, `tests/test_gates.py:130-140`

Mutation: `GATES: set = set()` — the variable read and discarded. Observed: **411 passed,
25 skipped, exit 0** under a bare `pytest`, and *identically* under `VCOWS_GATES=all`.
Control (unmutated, `VCOWS_GATES=all`, no rig/image env): 411 passed, **25 errors**. Adding
`.strip()`, and lowercasing plus splitting on whitespace, also leave the suite green.
Every test in `test_gates.py` monkeypatches `tests.conftest.GATES`, so the env-var → set
step is exercised by nothing. `test_gates_is_parsed_without_whitespace_stripping` is named
after that step and does not perform it, so the trap `CLAUDE.md:53-56` documents could be
removed or widened with this file still green. `all` travels through this line, so the
failure mode is `RW-E1`'s: `VCOWS_GATES=all` stops meaning anything and nothing says so.
Fix, two lines: `monkeypatch.setenv` plus a reload, or a `_parse(raw)` helper called with
`"tofu, image"`.

### RX-D3 — the domain's `type` is unasserted: `kvm` → `qemu` passes every gate
**medium** · `orchestrator/backends/libvirt/tofu/main.tf:88`

Mutation: `type = "kvm"` → `"qemu"`. Observed: `tofu test` green (`1 passed, 0 failed`),
whole Python suite green. No assertion reads `libvirt_domain.vm[*].type`; `.tftest.hcl:64`
asserts `os.type == "hvm"`, a different attribute. `<domain type='qemu'>` is TCG emulation:
every VM defines, boots, passes cloud-init and reports success, running unaccelerated — the
S1 shape the brief calibrates on. Fix: one assertion beside the existing sizing block.

### RX-D4 — the marker's XML-safety invariant is pinned by one constant pair
**medium** · `tests/test_marker.py:113-115`, `tests/test_properties.py:56-60`

Mutation: widen `NAME_PATTERN` (`orchestrator/backends/libvirt/schema.py:45`) to admit `<`,
`>`, `&`. Observed: **411 passed, 25 skipped, exit 0**, while
`Marker.for_vm("a<b&c","lab-a").to_xml()` gives
`<vcows xmlns="urn:vcows:1">{…"name":"a<b&c"…}</vcows>` and `ET.fromstring` raises
`not well-formed (invalid token): line 1, column 74`.
`test_xml_payload_needs_no_escaping` asserts `not (set("<>&") & set(payload))` for the one
hardcoded pair `("app01","lab-a")` — an input for which it cannot fail whatever `to_json`
does. The property test `#16` rewrote claims in its docstring to generalise that case
("round-trips through JSON and then into XML") and never calls `to_xml`. The invariant
therefore rests entirely on the two name patterns, and `#16`'s new guard asserts they
exclude `/`, not `<>&`. `render.py` feeds `marker_xml` into the domain XML unescaped: a
widened pattern is either a define libvirt refuses or a sibling element injected into
`<metadata>`. Fix: two characters in the `#16` guard, or one line asserting
`ET.fromstring(m.to_xml()).text == m.to_json()`.

### RX-D5 — the skip scanner matches literal `pytest.` spellings only
**low** · `tests/test_gates.py:38-54`, `:76-83`

All five `BANNED` forms are caught — each appended to `tests/test_version.py` and observed
to fail the scanner. Four bypass it, scanner green in every case:
`from pytest import skip as _s; _s(...)` (1 skipped); `import pytest as _pt; pytestmark =
_pt.mark.skip` (**4 skipped — the whole module**); `pytest.param(1, marks=pytest.mark.skip)`
(1 skipped); `raise unittest.SkipTest(...)` (1 skipped).
`pytestmark = pytest.mark.skip` is one module-scope line that silences a whole file, while
the *called* form `pytest.mark.skip(reason=…)` **is** caught — two spellings of one edit
disagreeing. Aliasing `import pytest as p` defeats every entry in `BANNED`. No test file
uses `from pytest import` or an alias today, which keeps this low. `_sources()` also globs
`tests/*.py` non-recursively, so a `tests/<subdir>/test_x.py` would be collected and not
scanned (none exists today). Fix, ~6 lines: match the trailing attribute path rather than
the full dotted name, and collect `ast.Attribute` in assignment and keyword position.

### RX-D6 — nine more module attributes survive replacement by a constant
**low** · firmware: `main.tf:118` (`loader`), `:120` (`loader_type`), `:136` (nvram
`format`) · devices: `:201` (nic `model`), `:203` (nic `source.network`), `:175`
(`device = "disk"`), `:181` (`driver.name`) · base volume: `:27` (`name`), `:28` (`pool`)

Each replaced with a constant or nulled; `tofu test` green in all nine. Three matter. nvram `format = "raw"` on a qcow2 varstore is the harmful inversion of
acceptance defect S6 — S6 was cosmetic *because libvirt reads the declared format, not the
extension*, which makes a wrong declared format the version that does not boot; the block
asserts `nv_ram.nv_ram`, `nv_ram.template` and `loader_readonly` and nothing else, and both
firmware attributes land only on `app02`, the VM the fixture reads least. nic `model` and
`source.network` are two of the nine passthroughs `RW-E5` named and the only two its fix did
not cover — a nic with no `source` gets no network. The base volume's `pool` is asserted for
the overlay and seed (`.tftest.hcl:69-76`) but not for itself, so the golden image alone can
land in a pool the config never named and preflight never checked. Fix: extend the two
existing `alltrue([for k, v in var.vms : …])` comprehensions and the firmware block. No new
blocks.

### RX-D7 — `_capture`'s timeout is the unpinned half of the pin `#17` added
**low** · `orchestrator/tofu.py:256`

Mutation: `timeout=SHORT_TIMEOUT` → `None`. Observed: whole suite green, 411/25.
`SHORT_TIMEOUT`'s docstring (`tofu.py:40-43`) says "init/output/version only"; `#17` pinned
the `init` half at `tofu.py:176` and left `outputs()` and `version()` — same constant, via
`_capture` — asserted by nothing. Losing it turns a stuck `tofu output` at a site into a CLI
that never returns and never writes its record. Fix: one assertion in the capture tests.

### RX-D8 — one fixture, every branch on the same side
**low** · `tests/golden/libvirt.tfvars.json:3`, `:15`, `:23`

`local.base_path`'s false branch replaced with `"/nowhere"` — green. `firmware = … ?
"efi" : null` replaced with a bare `"efi"` — green. The golden tfvars has
`base_volume.create: true`, `firmware: "efi"` on both VMs and `bridge: null` on both nics,
so `tofu test` never evaluates the base-already-present path — every deploy after the first
on a host — nor the BIOS branch, nor a bridged nic. A coverage statement, not a module
defect; it is what makes several survivors above unreachable by any assertion. Fix: a second
`run` block with an overriding `variables` stanza (~15 lines) buys the base-present path.

### RX-D9 — three of the five gate names are demanded by nothing
**low** · `tests/test_gates.py:27`, `justfile:88-90`, `scripts/test-image.sh:16`

`grep -rn VCOWS_GATES` over both pipelines, the `justfile` and `scripts/` returns exactly two
setters: `VCOWS_GATES=tofu` (`justfile:90`) and `VCOWS_GATES=image` (`test-image.sh:16`), so
`rig`, `libvirt` and `pycdlib` skip silently on a runner missing one and CI is green. `libvirt` is protected by accident —
`tests/fake_libvirt.py:25` imports it at module scope, so absence is a collection error.
`pycdlib` is not: `prepare.py:126` imports inside the function, so a CI image without it
gives a green `just check` with the seed-ISO tests skipped — and the seed ISO carries every
guest's network config. Fix: one token appended to `justfile:90`; `rig` stays undemanded.

### RX-D10 — two citation drifts inside the range
**nit** · `tests/libvirt-module.tftest.hcl:39`; the `#63` commit message in `3be2c28`

`.tftest.hcl:39` cites `destroy.py:456-461` for the `{overlay_name(...), seed_name(...)}`
match; that code is `destroy.py:440-446`, `_deletable` spans `:422-456`, and `:459` begins
`_deleted_on_name_alone`. The comment entered at `491d465`; `c854e49` and `2f8ebe2` moved
the code after. Separately, `3be2c28`'s message cites the CI path filters as
`image.yml:21-22` and `.gitlab-ci.yml:96-97`; they are `:22-23` and `:102-103`. Both claims
are correct — only the line numbers moved.

## The mutation table

| Mutated | Failed as it should |
|---|---|
| `gate()`: no `gate_missing`; `demanded()` pinned False and True; hook made a no-op; `skipif(True)`; a typo'd gate name | yes — all six |
| `require()` never fails when demanded | **no — skipped, exit 0 (RX-D1)** |
| `VCOWS_GATES` parse: strip / lowercase / discarded | **no — 411 passed (RX-D2)** |
| the five `BANNED` skip spellings | yes — all five |
| alias, bare `pytestmark`, `param(marks=)`, `unittest.SkipTest` | **no — all four bypass (RX-D5)** |
| an unused name added to `KNOWN` | no — accepted; nit only |
| `_run` timeout made unconditional, both directions | yes — each direction |
| `_capture` loses its timeout | **no — 411 passed (RX-D7)** |
| `NAME_PATTERN` / `DEPLOYMENT_PATTERN` admit `/` | yes |
| `NAME_PATTERN` admits `<>&` | **no — 411 passed (RX-D4)** |
| `virtual_size` 4 bytes / little-endian; `_parse_interface` returns a network / takes a bare address; `to_json` truncates the name to 2 | yes — all five (name truncation missed at `[:32]`, a draw-distribution artefact) |
| `Containerfile` edited: current `ship=` vs the pre-`#63` four-path set | yes on `-dirty`; **pre-fix set returns a clean SHA and the gate passes** |
| 28 settled `main.tf` / `outputs.tf` values | yes — 27 of 28 |
| `depends_on` on the seed volume | no — documented uncatchable, `.tftest.hcl:13-17` |
| 16 further attributes and branches | **no — RX-D3, D6, D8** |

## Checked and sound

* `#17` — both directions of `tofu.py:176` fail their test; `plan` needs no case, `_run`
  branches on `cmd == "init"` alone. `#16` — all four rewritten property tests are falsifiable, proved by mutating the code
  under each. `test_derived_ids_separate_deployments`' biconditional is still structurally
  unfalsifiable (the two pairs are drawn independently), but its new guard assertions carry
  the claim, and dropping the separator from `derive_id` is caught by
  `tests/test_marker.py:34`'s golden UUID.
* `#63` — verified both directions, and the correction the issue asked for was correctly
  **not** made: `scripts/lib.sh:124-125`'s sentence about the two CI path filters is true
  (`.github/workflows/image.yml:22-23`, `.gitlab-ci.yml:102-103`). `ship=` covers every
  trackable `COPY` source in the `Containerfile`.
* `RW-E2`, `RW-E3`, `RW-E4`, `RW-E6`, `RW-E7`, `RW-C2`, `RW-C3`, `RW-E8` — fixed as stated.
  `test_the_module_gate_has_teeth` reproduces green→red on the marker deletion, and
  `conftest.tofu_env`'s `assert IMAGE_MIRROR in shipped` guard and the `_umask` autouse
  fixture both do what their docstrings say.

## Not checked

* `mutmut` — deferred by the plan. `scripts/lint.sh`'s `workflows_carry_no_logic` — E's.
  The `rig` gate's 15 tests were read, not mutated: mutating them means running against a
  hypervisor that is not ours.
* Whether `tofu test` can reach provider *configuration* (`main.tf:17`, `uri = var.uri`).
  Replacing it with a constant is not caught, but `mock_provider` replaces the provider
  block, so I could not establish whether it is catchable at all — treated like the
  `depends_on` edge rather than reported as a gap.
