# Issue #87 — gate machinery: seven test-surface gaps found by mutation

Lane `lane/tofu-module`. Reverified at `aed962d`. Transcripts:
`docs/review-tofu-module/reverify/RX-D{1,2,5,6,7,8,10}.txt`.

Numbering follows the issue body and keeps its stated order: **RX-D2 before
RX-D1**, then RX-D5, RX-D8, RX-D6, RX-D7, RX-D10.

Bench for every measurement below: a `cp -a` copy of the worktree under
`mktemp`-style scratch, its own `.venv` with the editable install removed so
`import orchestrator` resolves to the copy (verified), its own real
`.tools/tofu-mirror`. Baselines in the copy, unmutated:

```
$ .venv/bin/python -m pytest -q                     411 passed, 25 skipped   exit=0
$ VCOWS_GATES=all .venv/bin/python -m pytest -q     411 passed, 25 errors    exit=1
$ VCOWS_GATES=tofu ... tests/test_tofu_module.py     10 passed               exit=0
```

Identical to the 2026-08-31 numbers at `672a500`.

---

## 1. Reverification verdict

### 1.1 RX-D2 — `tests/conftest.py:37` · **reproduced, unchanged**

Mutation A, `GATES: set = set()`:

```
$ .venv/bin/python -m pytest -q                  411 passed, 25 skipped   exit=0
$ VCOWS_GATES=all .venv/bin/python -m pytest -q  411 passed, 25 skipped   exit=0
$ VCOWS_GATES=tofu ... tests/test_gates.py       16 passed                exit=0
```

against the `VCOWS_GATES=all` control of `411 passed, 25 errors, exit 1`.
`VCOWS_GATES=all` stops meaning anything and nothing in the suite notices.

Mutation B, the same line gaining `.strip()`:

```
$ .venv/bin/python -m pytest -q tests/test_gates.py         16 passed                            exit=0
$ VCOWS_GATES="tofu, image" .venv/bin/python -m pytest -q   411 passed, 15 skipped, 10 errors    exit=1
```

with the unmutated control for that same command being `411 passed, 25 skipped,
exit 0`. The dedicated gate file is fully green while the documented trap at
`CLAUDE.md:55-56` has been silently removed.

### 1.2 RX-D1 — `tests/test_gates.py:195-198` · **reproduced, and narrower than the issue says**

Deleting `conftest.py:65-66` so `require()` always skips:

```
$ .venv/bin/python -m pytest -q                        410 passed, 26 skipped   exit=0
$ .venv/bin/python -m pytest -q tests/test_gates.py -rs
SKIPPED [1] tests/conftest.py:65: needs a thing this runner does not have
15 passed, 1 skipped                                                            exit=0
```

against the 411/25 control. The delta is one test moving from passed to skipped —
its own. `test_a_demanded_require_that_is_missing_fails` cannot fail.

The narrowing claim needed correcting; see §3.2.

### 1.3 RX-D5 — `tests/test_gates.py:76-83` · **reproduced, four for four**

Each appended to `tests/test_version.py` in the copy, `tests/test_gates.py` run
alongside:

| appended | scanner | what it let through |
|---|---|---|
| `import pytest` + `pytestmark = pytest.mark.skip` | **16 passed** | `4 skipped` — the whole module |
| `import pytest as _pt` + `pytestmark = _pt.mark.skip` | **16 passed** | `4 skipped` |
| `from pytest import skip as _s` then `_s(...)` | **16 passed** | `1 skipped` |
| `raise unittest.SkipTest(...)` | **16 passed** | `1 skipped` |
| `pytest.param(1, marks=pytest.mark.skip)` | **16 passed** | `1 skipped` |

Control: `pytest.skip(...)` in a test body → `1 failed, 15 passed`, the scanner
naming `test_version.py:58: pytest.skip`. The harness is live.

### 1.4 RX-D8 — `tests/golden/libvirt.tfvars.json:3,15,23` · **half closed on master already**

This is the one finding the pin has outrun. `454ee7c` added 88 lines and two
`run` blocks to `tests/libvirt-module.tftest.hcl` after the review's pin at
`672a500`. Re-running both mutations at `aed962d`:

| mutation | at `672a500` | at `aed962d` |
|---|---|---|
| `main.tf:34` `local.base_path` false branch → `"/nowhere"` | 10 passed | **1 failed, 9 passed, exit 1 — CLOSED** |
| `main.tf:116` `firmware = … ? "efi" : null` → bare `"efi"` | 10 passed | 10 passed, exit 0 — **still open** |

The catcher is `run "a_prebuilt_base_volume_is_used_in_place"`
(`tests/libvirt-module.tftest.hcl:274-297`), failing at `:291-296` with *"the
overlay does not back onto the pool's existing image when create is false:
main.tf:34's fallback is not reached"*. The second new block,
`a_bridged_nic_renders_source_bridge` (`:299-339`), closes the bridged-NIC branch
that the finding deliberately did not file.

### 1.5 RX-D6 — nine `main.tf` attributes · **9/9 still green, none closed**

All nine line citations re-read at `aed962d` and exact. All nine mutations
re-run under `VCOWS_GATES=tofu … tests/test_tofu_module.py`:

| line | mutation | result at `aed962d` |
|---|---|---|
| `:27` base `name` | `"wrongname.qcow2"` | 10 passed |
| `:28` base `pool` | `"nowhere"` | 10 passed |
| `:118` `loader` | `"/nowhere/OVMF.fd"` | 10 passed |
| `:120` `loader_type` | `null` | 10 passed |
| `:136` nvram `format` | `"raw"` | 10 passed |
| `:175` `device = "disk"` | `"cdrom"` | 10 passed |
| `:181` `driver.name` | `"bogus"` | 10 passed |
| `:201` nic `model` | `{ type = "e1000" }` | 10 passed |
| `:203` nic `source.network` | `null` | 10 passed |

Control, `discard = "unmap"` removed from the same `driver` object:
`1 failed, 9 passed, exit 1`. The harness is live and the nine are gaps.
**`454ee7c` closed none of them** — its two blocks assert
`length(libvirt_volume.base) == 0`, the overlay's backing path, and a bridged
NIC's `source.bridge`/`source.network`, none of which is any of the nine.

### 1.6 RX-D7 — `orchestrator/tofu.py:256` · **reproduced**

```
$ .venv/bin/python -m pytest -q                        411 passed, 25 skipped   exit=0
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q       411 passed, 25 skipped   exit=0
$ .venv/bin/python -m pytest -q tests/test_tofu_driver.py   20 passed           exit=0
```

with `timeout=SHORT_TIMEOUT` replaced by `timeout=None`.

### 1.7 RX-D10 — `tests/libvirt-module.tftest.hcl:39` · **reproduced, and the second half has drifted again**

`:39` still reads `(destroy.py:456-461)`. At `aed962d`, `destroy.py` is unchanged
since the pin and the true lines are:

```
422: def _deletable(path, target, claimed, out) -> bool:
440:     owned = (
441:         {overlay_name(target.marker.name), seed_name(target.marker.name)}
442:         if target.marker is not None
443:         else set()
444:     )
445:     if PurePosixPath(path).name not in owned:
456:     return True                                 # _deletable's own close
459: def _deleted_on_name_alone(out, target, path) -> None:   # a different function
```

Correct citation: **`destroy.py:440-445`**. The commit-message half needed
re-measuring; see §3.7.

---

## 2. Anchor table

All re-read at `aed962d`.

| # | anchor | state |
|---|---|---|
| D2 | `tests/conftest.py:36-37` `GATES = {g for g in os.environ.get("VCOWS_GATES", "").split(",") if g}` | ok, exact |
| D2 | `tests/test_gates.py:130-140` `test_gates_is_parsed_without_whitespace_stripping` | ok — monkeypatches `GATES`, never performs the parse |
| D2 | `CLAUDE.md:55-56` — the documented no-strip trap | ok, and true today (measured) |
| D1 | `tests/conftest.py:61-67` `require()` | ok, exact |
| D1 | `tests/test_gates.py:195-198` `test_a_demanded_require_that_is_missing_fails` | ok, exact |
| D1 | `tests/fake_libvirt.py:25` `import libvirt` | ok — imported by `test_libvirt_destroy.py:23` and `test_libvirt_preflight.py:21` |
| D1 | `tests/test_seed_iso.py:22-26` — the only `require("pycdlib", …)` | ok, **module scope** |
| D1 | `tests/test_tofu_module.py:54`, `:176` — the two `require("tofu", …)` | ok; all 7 consumers carry `@needs_tofu` (AST-verified) |
| D5 | `tests/test_gates.py:38-54` `_references`, `:68-69` `_sources`, `:76-83` `BANNED` | ok, exact; `BANNED` holds six forms |
| D5 | `find tests -name '*.py' -mindepth 2` | empty — the non-recursive glob is inert today |
| D8 | `tests/golden/libvirt.tfvars.json:3` `"create": true`, `:15` `"firmware": "efi"`, `:23` `"bridge": null` | ok, exact |
| D8 | `main.tf:34`, `:116` | ok, exact |
| D8 | `tests/libvirt-module.tftest.hcl:13-17` — scopes out `depends_on` only | ok |
| D8 | `tests/libvirt-module.tftest.hcl:253-272` — the new header naming three uncovered expressions | ok; **it does not name `:116`** |
| D6 | `main.tf:27, 28, 118, 120, 136, 175, 181, 201, 203` | ok, all nine exact |
| D7 | `orchestrator/tofu.py:44` `SHORT_TIMEOUT = 120`, `:176`, `:256` | ok, exact |
| D7 | `tests/test_tofu_driver.py:300-317` — `#17`'s pin on `_run` | ok |
| D7 | `tests/test_tofu_driver.py` — no assertion reaches `_capture`'s timeout | ok |
| D10 | `tests/libvirt-module.tftest.hcl:39` | ok, still `456-461` |
| D10 | `orchestrator/backends/libvirt/destroy.py:422, 440-445, 456, 459` | ok |

---

## 3. Corrections to the issue body

### 3.1 RX-D2 — none

Every number reproduced exactly, including the `.strip()` variant's
`411 passed, 15 skipped, 10 errors`.

### 3.2 RX-D1 — **the narrowing is total, not partial**

The issue says `require()` "uniquely guards only `pycdlib`". Measured, `pycdlib`
is not guarded by it either, because `tests/test_seed_iso.py:26` calls
`require()` at **module scope**, and `pytest.skip()` at module scope is a
collection error, not a skip. With `pycdlib` made unimportable:

```
-- intact require(), VCOWS_GATES=pycdlib --
ERROR tests/test_seed_iso.py - Failed: needs pycdlib; it is what builds the s...
1 error       exit=2

-- require() mutated, same command --
ERROR collecting tests/test_seed_iso.py
Using pytest.skip outside of a test will skip the entire module. …
1 error       exit=2

-- require() mutated, gate NOT demanded, whole suite --
ERROR tests/test_seed_iso.py
1 error       exit=2
```

Both states are exit 2. The mutation degrades the *message* — the intact form
carries "needs pycdlib; it is what builds the seed ISO", the mutated form carries
pytest's generic advice — and changes nothing about the exit code.

The `libvirt` half is confirmed as the issue states, measured on a second venv
built without `--system-site-packages` from the same `uv.lock` export, i.e. a
runner genuinely missing the `python3-libvirt` RPM:

```
$ VCOWS_GATES=libvirt $NOLV/bin/python -m pytest -q
E   ModuleNotFoundError: No module named 'libvirt'
ERROR tests/test_libvirt_destroy.py
ERROR tests/test_libvirt_preflight.py
2 errors      exit=2      (identical intact and mutated, demanded and not)
```

One refinement: `tests/test_libvirt_errors.py` does **not** import
`fake_libvirt`, so run in isolation it does skip
(`SKIPPED [1] tests/conftest.py:67`). It never runs in isolation in CI, and the
whole-suite run errors at collection first.

The `tofu` half is confirmed. AST walk over `tests/test_tofu_module.py`: all
seven tests taking `initialised` or `mocked` carry `@needs_tofu`.

**Consequence for the fix: RX-D1's production reach is zero, not small.** The
fix is worth taking against `conftest.py:7` — the file that AST-walks the suite
for skips that bypass the mechanism contains a test that turns itself into a skip
— and for nothing else. It stays below RX-D2, and it should not be sized as if a
runner somewhere could go quietly green because of it.

### 3.3 RX-D5 — **the bypass is the *uncalled* attribute; the called form is caught**

The issue and the verify record both write `pytestmark = pytest.mark.skip` and
`pytest.param(1, marks=pytest.mark.skip)`. Those are exact, and the distinction
is load-carrying in a way neither states: adding `(reason=…)` to either turns it
into an `ast.Call`, which `_references` collects and `BANNED` matches. Measured:

```
pytestmark = pytest.mark.skip                        16 passed  (bypass)
pytestmark = pytest.mark.skip(reason="…")            1 failed   (caught)
pytest.param(1, marks=pytest.mark.skip)              16 passed  (bypass)
pytest.param(1, marks=pytest.mark.skip(reason="…"))  1 failed   (caught)
```

Two spellings of one edit disagreeing, which is the finding's real shape.

**The "~6 lines" estimate holds only as a net count.** A working candidate was
built and measured: **+22 / −16 in `tests/test_gates.py`**, net +6. See §5.3.

### 3.4 RX-D8 — **half of it is already closed; file it that way**

See §1.4. The `main.tf:34` half is closed by `454ee7c`. Only the
`firmware … : null` half is open, and the issue's citation list should shrink to
`tests/golden/libvirt.tfvars.json:15` — `:3` (`"create": true`) and `:23`
(`"bridge": null`) are both now exercised by an overriding `variables` stanza.

One thing `454ee7c`'s own header does not say: it names three expressions the
golden fixture never evaluates — `main.tf:25`'s zero arm, `:34`'s fallback and
`:205`'s bridge arm — and does not name `:116`'s null arm, which is the one it
left open.

### 3.5 RX-D6 — none

Nine of nine reproduced, control fails, all citations exact.

### 3.6 RX-D7 — none

Reproduced under both the default and `VCOWS_GATES=tofu`. `#17` is closed and
pinned the `_run` half at `tofu.py:176` (`tests/test_tofu_driver.py:313,317`);
`_capture` at `:256` reaches the same constant and is asserted by nothing. The
issue's "one assertion" is right, and a working one was measured (§5.6).

### 3.7 RX-D10 — **the commit-message half has moved again since the pin**

The 2026-08-31 record corrected `3be2c28`'s citations to
`.github/workflows/image.yml:22-23` and `.gitlab-ci.yml:102-103`. At `aed962d`
the GitHub half is still `:22-23`; the GitLab half is **`:136-137`**, because
`a3068e3` added 34 lines to `.gitlab-ci.yml` above the filter. A commit message
cannot be edited, so this stays record-only — but the number in the verify
document should not be copied forward.

`scripts/lib.sh:124-125`'s own sentence about the two filters carries no line
numbers and is still correct. Leave it.

---

## 4. The defect

### 4.1 RX-D2 — the one line that turns `VCOWS_GATES` into `GATES` is exercised by nothing

Every test in `tests/test_gates.py` monkeypatches `tests.conftest.GATES`, so the
env-var → set step is never performed under test.
`test_gates_is_parsed_without_whitespace_stripping` (`:130-140`) is named after
that step and monkeypatches instead of performing it. `all` travels through this
line, so the failure mode is `RW-E1`'s: `just test-tofu` (`justfile:90`,
`VCOWS_GATES=tofu`) reports green on a runner with no `tofu` and no mirror,
having exercised the module not at all. It silences all five gate names at once,
which is why it outranks RX-D1.

### 4.2 RX-D1 — the gate-of-gates contains a test that cannot fail

`pytest.skip` raised inside `pytest.raises(pytest.fail.Exception)` propagates and
skips the enclosing test. So `test_a_demanded_require_that_is_missing_fails`
converts itself from a failure into a skip when the code it guards is broken —
the exact shape `conftest.py:7` exists to prevent, inside the file that enforces
it. No production reach (§3.2).

### 4.3 RX-D5 — the scanner matches a spelling, not a behaviour

`_references` collects `ast.Call` funcs and bare decorators; `BANNED` matches the
full dotted name against six literals beginning `pytest.`. So an alias
(`import pytest as _pt`), a from-import (`from pytest import skip as _s`), a
module-scope assignment of an uncalled `pytest.mark.skip`, an uncalled mark in
`pytest.param(marks=…)`, and `unittest.SkipTest` all walk past. The
`pytestmark` one is the one that matters: one module-scope line silences a whole
file, and `CLAUDE.md:49-51` says the scanner "fails on any bare `pytest.skip`,
`pytest.importorskip` or `pytest.mark.skip`". As written, that sentence is false.

### 4.4 RX-D8 — one fixture, one side of the remaining branch

`tests/golden/libvirt.tfvars.json` gives both VMs `"firmware": "efi"`, so
`main.tf:116`'s null arm is never evaluated. `firmware` is a real config enum —
`schema.py:129` `{"enum": ["efi", "bios"]}` — so `bios` is a value an operator can
write today and the module's handling of it is asserted by nothing.

**File it alongside #75, not as an independent discovery.** #75 — the RHEL `.fd`
firmware path dying after the volumes are written — is what was hiding in this
gap and names the same root cause: `mock_provider` satisfies the schema with
generated values and never performs a post-apply read. What RX-D8 can close on
its own is *evaluation* coverage: that `main.tf` produces the right value on the
BIOS branch. What it **cannot** close is read-back: whether libvirt echoes that
value back the way the provider planned it. That needs a real libvirtd, which is
why **#75 is not in this lane** and is deferred to a lane with CI hardware. A
`bios` run block here would not have caught #75 and must not be presented as
having done so.

### 4.5 RX-D6 — nine values with no assertion

Three carry a real failure. nvram `format` (`:136`): libvirt reads the *declared*
format, not the extension, so a wrong declared format is the non-booting
inversion of acceptance defect S6 — and it is the same attribute #75 dies on. nic
`source.network` (`:203`): a NIC with no source gets no network. The base
volume's `pool` (`:28`): asserted for the overlay and the seed at
`.tftest.hcl:69-76`, not for the golden image itself, so it alone can land in a
pool the config never named and preflight never checked. The other six are
cheaper but sit in the same two comprehensions.

Note `:118`, `:120` and `:136` land only on `app02` — the fixture's one VM with a
pinned loader — so their assertions must name `app02` explicitly, not iterate
`var.vms`.

### 4.6 RX-D7 — the output/version half of `#17`

`SHORT_TIMEOUT`'s docstring (`tofu.py:40-43`) scopes it to "init/output/version
only". `#17` pinned `init`. `outputs()` (`:267-270`) and `version()` (`:273-281`)
both reach the same constant through `_capture` (`:238-256`) and are asserted by
nothing. Losing it turns a stuck `tofu output` at a site into a CLI that never
returns and never writes its record.

### 4.7 RX-D10 — a comment pointing at the wrong function

`.tftest.hcl:39` sends a reader to `destroy.py:456-461`, which is `_deletable`'s
closing `return True` and the opening of `_deleted_on_name_alone`. The claim the
comment makes is true; only the numbers moved (`c854e49`, `2f8ebe2`).

---

## 5. The fix

Seven independent changes, all test-only except RX-D10, which is a comment.
**No file under `orchestrator/` is edited by any of them.**

### 5.1 RX-D2 — perform the parse

Extract the parse into a helper and call it with the string the trap is about:

```python
def _parse(raw: str) -> set[str]:
    """Comma-separated names, no stripping. `tofu, image` demands `tofu` only."""
    return {g for g in raw.split(",") if g}

GATES = _parse(os.environ.get("VCOWS_GATES", ""))
```

and in `tests/test_gates.py`, replace the monkeypatching body of
`test_gates_is_parsed_without_whitespace_stripping` with the parse it is named
after: `_parse("tofu, image") == {"tofu", " image"}` and `_parse("") == set()`.
Mutation A (`GATES: set = set()`) then fails on `_parse("")`'s call site being
gone; mutation B (`.strip()`) fails on the first assertion.

Roughly 4 lines in `conftest.py` and 6 in `test_gates.py`. Do **not** "fix" the
stripping: `CLAUDE.md:53-56` and `test_gates.py:131-133` both record it as
documented rather than fixed, and both CI files are written without spaces
because of it.

### 5.2 RX-D1 — make an escaping `Skipped` a failure

Wrap the call so a `Skipped` cannot masquerade as a pass:

```python
def test_a_demanded_require_that_is_missing_fails(monkeypatch):
    monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
    try:
        with pytest.raises(pytest.fail.Exception, match=REASON):
            require("tofu", False, REASON)
    except pytest.skip.Exception:  # pragma: no cover -- this is the assertion
        raise AssertionError("require() skipped where it was demanded to fail") from None
```

Test-only, about 5 lines, in `tests/test_gates.py`. Nothing in `conftest.py`
changes.

### 5.3 RX-D5 — match the trailing attribute path, and widen what is collected

A candidate was built and measured rather than estimated. In
`tests/test_gates.py`:

* `_references` collects every **outermost** `ast.Attribute` (not just decorators
  and call funcs) and, additionally, the names of any `from pytest import …`;
* `BANNED` becomes suffixes — `skip`, `importorskip`, `xfail`, `mark.skip`,
  `mark.skipif`, `mark.xfail`, `SkipTest`;
* a three-line `_is_banned(name)` tests every trailing sub-path.

Outermost-only is not optional. Collecting every `ast.Attribute` flags
`test_gates.py`'s own `pytest.raises(pytest.skip.Exception, …)` at `:203` — a
legitimate reference to the exception type. Measured: the naive version gives
`1 failed, 410 passed`; the outermost-only version gives `411 passed, 25 skipped`.

Cost: **+22 / −16**, one file, no production surface.

### 5.4 RX-D8 — one more `run` block, for the BIOS branch

A third `run` block beside the two `454ee7c` added, overriding `vms` with a
single VM carrying `firmware = "bios"`, `loader = null`, `nvram_template = null`,
asserting `libvirt_domain.vm[k].os.firmware == null` and
`os.nv_ram == null`. ~25 lines, same shape as the two blocks already there.

It goes in the same file and the same style, and its header comment must repeat
what the existing one at `:253-272` says: a run-level `variables` block overrides
wholesale rather than merging, so these blocks are hand-written and nothing
asserts they still resemble what `render.py` emits.

### 5.5 RX-D6 — extend two comprehensions and the firmware block

No new `run` block, no new file:

* into the sizing `alltrue` at `:58-66` (or a sibling with its own message):
  `devices.disks[0].device == "disk"`, `devices.disks[0].driver.name == "qemu"`,
  `devices.interfaces[0].model.type == v.nics[0].model`,
  `devices.interfaces[0].source.network.network == v.nics[0].network`;
* into the pool `alltrue` at `:69-76`: `libvirt_volume.base[0].pool == var.pool`
  and `libvirt_volume.base[0].name == var.base_volume.name`;
* into the firmware block at `:118-146`, naming `app02` explicitly:
  `os.loader == var.vms["app02"].loader`, `os.loader_type == "pflash"` (with
  `app01`'s `== null`), `os.nv_ram.format == var.vms["app02"].loader_format` and
  `os.nv_ram.template_format` likewise.

Around 20 lines of HCL. `:136` is the highest-value one: it is the attribute #75
dies on, and pinning the *declared* value here is the offline half of that
problem even though it cannot be the whole of it.

### 5.6 RX-D7 — one assertion in `tests/test_tofu_driver.py`

**This does not edit `orchestrator/tofu.py`.** Another lane owns that file and a
conflict here would be avoidable. `_capture` uses `subprocess.run`, so the
recording goes on the call rather than on a fake process:

```python
def test_output_and_version_run_on_the_short_clock(fake_tofu, workdir, monkeypatch):
    seen: list[float | None] = []
    real = tofu.subprocess.run

    def recording(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    monkeypatch.setattr(tofu.subprocess, "run", recording)
    monkeypatch.setenv("FAKE_TOFU_STDOUT", json.dumps({"terraform_version": "1.12.6"}))
    tofu.version()
    monkeypatch.setenv("FAKE_TOFU_STDOUT", json.dumps({"vms": {"value": {}}}))
    tofu.outputs(workdir)
    assert seen == [tofu.SHORT_TIMEOUT, tofu.SHORT_TIMEOUT], (
        "a capture with no clock is a CLI that hangs and never writes its record"
    )
```

Measured: +21 lines, one test, suite goes 411 → 412.

### 5.7 RX-D10 — one number

`tests/libvirt-module.tftest.hcl:39`: `destroy.py:456-461` → `destroy.py:440-445`.
One line. The `3be2c28` half is record-only.

### Rejected

* Fixing the `VCOWS_GATES` whitespace behaviour (§5.1) — recorded as deliberate.
* Appending `,pycdlib` to `justfile:90` — RX-D9, refuted against
  `docs/ci.md:52-58`; not reopened here.
* Making `require("pycdlib", …)` a module-level skip with
  `allow_module_level=True`. It would convert today's loud collection error into
  a quiet skip, which is the wrong direction, and it is not what any of these
  seven findings asks for.
* Recursing `_sources()` over `tests/**/*.py`. Measured inert: `find tests -name
  '*.py' -mindepth 2` is empty. Mention in the commit body, do not implement.

---

## 6. Surface cost

| # | files | measured cost |
|---|---|---|
| D2 | `tests/conftest.py`, `tests/test_gates.py` | ~+10 / −4, test-only |
| D1 | `tests/test_gates.py` | ~+5, test-only |
| D5 | `tests/test_gates.py` | **+22 / −16 measured**, test-only |
| D8 | `tests/libvirt-module.tftest.hcl` | ~+25, one `run` block |
| D6 | `tests/libvirt-module.tftest.hcl` | ~+20, no new `run` block |
| D7 | `tests/test_tofu_driver.py` | **+21 measured**, one test |
| D10 | `tests/libvirt-module.tftest.hcl` | 1 line |

Total: four files, no new file, nothing under `orchestrator/` or `scripts/`. The
suite count moves 411 → 412 (RX-D7's one new test); RX-D8's and RX-D6's
assertions live inside existing pytest tests and do not move it.

---

## 7. The failing test

Each item states the mutation its fix must turn red. RX-D5 and RX-D7 were built
and proved; the rest name the mutation already measured in §1.

### 7.1 RX-D2
`GATES: set = set()` must fail (today: `411 passed, 25 skipped`, exit 0 under
both default and `all`). `.strip()` added must fail
`test_gates_is_parsed_without_whitespace_stripping` (today: 16 passed).

### 7.2 RX-D1
Deleting `conftest.py:65-66` must give `1 failed`, not `1 skipped`. Today:
`410 passed, 26 skipped, exit 0`.

### 7.3 RX-D5 — **built and proved**

Against the candidate, all four bypasses plus the two called forms:

```
bare pytestmark = pytest.mark.skip           E  test_version.py:56: pytest.mark.skip     1 failed
aliased _pt.mark.skip                        E  test_version.py:56: _pt.mark.skip        1 failed
from pytest import skip as _s                E  test_version.py:54: skip                 1 failed
raise unittest.SkipTest                      E  test_version.py:58: unittest.SkipTest    1 failed
pytest.param(marks=pytest.mark.skip)         E  test_version.py:57: pytest.mark.skip     1 failed
control: pytest.importorskip                 E  test_version.py:58: pytest.importorskip  1 failed
```

and green where it must be:

```
$ .venv/bin/python -m pytest -q tests/test_gates.py   16 passed               exit=0
$ .venv/bin/python -m pytest -q                       411 passed, 25 skipped  exit=0
```

### 7.4 RX-D8
`main.tf:116` → bare `"efi"` must fail. Today: 10 passed.
The `main.tf:34` mutation is already red and stays a regression check.

### 7.5 RX-D6
Each of the nine mutations in §1.5 must fail, run one at a time.
The `discard = "unmap"` control must stay red.

### 7.6 RX-D7 — **built and proved**

```
-- against the shipped tofu.py --
21 passed (tests/test_tofu_driver.py)     412 passed, 25 skipped (suite)     exit=0
-- against timeout=None --
At index 0 diff: None != 120
FAILED tests/test_tofu_driver.py::test_output_and_version_run_on_the_short_clock
1 failed, 20 passed                                                          exit=1
```

### 7.7 RX-D10
No test. The evidence is the four line numbers printed in §1.7.

---

## 8. Verification

Whole-issue gate, run once at the end:

1. `just check` → six lint gates ok, `ty` clean, **`412 passed, 25 skipped`**
   (411 + RX-D7's one new test).
2. `just test-tofu` (`VCOWS_GATES=tofu`) → the module tests demanded rather than
   skipped; `tests/test_tofu_module.py` `10 passed`.
3. `VCOWS_GATES=all .venv/bin/python -m pytest -q` → `411 passed, 25 errors,
   exit 1` becomes `412 passed, 25 errors, exit 1` on a box with no rig and no
   image. Any other shape means RX-D2's fix changed what `all` means.
4. Per-item green→red, each mutation applied alone in a scratch copy and
   reverted (§7.1–§7.6).
5. `just lint`'s `tofu fmt` gate for the new HCL.
6. **Not** `just smoke-libvirt`, and no connection to
   `qemu+ssh://vcows@vcows/system`. Nothing here touches either.

---

## 9. Non-goals

* **#75.** Not this lane. RX-D8's BIOS block is evaluation coverage; #75 is a
  post-apply read-back failure that needs a real libvirtd. Do not claim the one
  closes the other (§4.4).
* **RX-D3 / issue #78**, the domain type. Same lane, its own plan
  (`docs/plans/issue-78.md`), because it is a medium and these seven are lows.
* **RX-D9**, the three undemanded gate names. Refuted against `docs/ci.md:52-58`
  and `CLAUDE.md:110`; settled.
* **`VCOWS_GATES` whitespace semantics.** Documented, not a defect.
* **`orchestrator/tofu.py`.** RX-D7 is a test. Another lane owns that file.
* **`_sources()` recursion.** Measured inert.
* **`mutmut`.** Still does not complete; unchanged by any of this.
* **The `3be2c28` commit message.** Cannot be edited.
