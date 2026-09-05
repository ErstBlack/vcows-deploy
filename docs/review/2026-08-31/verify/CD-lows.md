# Phase 3 — C and D lows and nits, confirmed

Confirmer · findings `RX-C3`–`RX-C5`, `RX-D5`–`RX-D10` · read and run at `672a500`
in a detached worktree · 2026-08-31. Every mutation ran in a `cp -a` copy under
`mktemp -d`; no tracked file was edited, the rig was not touched, and
`--write-baseline` was not run.

Baseline in the worktree: `411 passed, 25 skipped` (default gates);
`10 passed` for `tests/test_tofu_module.py` under `VCOWS_GATES=tofu`.

| | verdict |
|---|---|
| `RX-C3` | DOWNGRADED to nit — true, deliberate, record-only |
| `RX-C4` | REFUTED — the `where` is not wrong; leave it |
| `RX-C5` | REFUTED — the `where` is the deepest key that exists; leave it |
| `RX-D5` | CONFIRMED low |
| `RX-D6` | CONFIRMED low — 9/9 reproduced |
| `RX-D7` | CONFIRMED low |
| `RX-D8` | CONFIRMED low |
| `RX-D9` | REFUTED — recorded design (`docs/ci.md:53-58`) |
| `RX-D10` | CONFIRMED nit — corrected to `destroy.py:440-445` |

---

## RX-C3 — `image.sha256` verifies the local file — DOWNGRADED to nit

Factually true. `grep -n sha256 orchestrator/backends/libvirt/preflight.py` returns
nothing; the only host-side comparison is `preflight.py:366` `if physical != local:`
against `os.stat(source).st_size`.

Already deliberate at both ends, and recorded in code rather than only in a plan.
`_check_image_digest`'s docstring (`schema.py:275-279`) states the alternative was
considered and rejected: verifying in `preflight` "puts an offline check in the
connected phase, so `vcows validate` would keep reporting a corrupt image as
valid." D30 separately settled `<physical>` size as the host-side check. The finder
proposes no fix, and the only fix there is — a digest over a multi-GB volume across
the SSH tunnel inside the connected phase — costs far more surface than the gap.

**Leave it.** One residual worth a line if anything is filed at all: the
`base_volume` docstring at `preflight.py:326-327` says the size check "catches a
*different* image under the same name as well", which holds only when the lengths
differ. That is an S5-shaped overclaim in one sentence, not a behaviour defect.

## RX-C4 — unreadable-image digest warning filed at `image.sha256` — REFUTED

Citation exact: the warning's `where="image.sha256"` is `schema.py:301`, inside the
`except OSError` block spanning `:296-303`. `_check_disk_capacity`'s counterpart is
`where="image.source_qcow2"` at `:611`. Reproduced against `tests/conftest.CONFIG`
with `source_qcow2` pointed at a missing path:

```
warning [image.source_qcow2]: cannot read /nonexistent/golden.qcow2 to check disk_gb …
warning [image.sha256]:       cannot read /nonexistent/golden.qcow2 to check its sha256 …
```

Not a defect. Each `where` names the check that could not run, which is the
information the field is carrying, and **both messages name the unreadable path in
full**, so the operator is not sent to the wrong place by either. Rewriting both to
`image.source_qcow2` would make them indistinguishable at the key while telling the
operator nothing new. Leave it.

## RX-C5 — duplicate MAC filed at the NIC, duplicate IP at the field — REFUTED

Citations exact: IP collision `where=f"{at}.ip_cidr"` at `schema.py:552`; MAC
collision `where=at` at `schema.py:562`. Reproduced:

```
vms[1].nics[0].ip_cidr | address 192.168.122.60 is already used by vms[0].nics[0]
vms[1].nics[0]         | MAC 52:54:00:be:a8:60 is already used by vms[0].nics[0]
```

The asymmetry is correct, not accidental. `mac_of` derives a MAC when the config
declares none, so `where=f"{at}.mac"` would name a key that does not exist in the
document the operator is about to open. `where=at` is the deepest key that always
exists. Making it conditional on an explicit `mac:` adds a branch to buy a string.
Leave it.

## RX-D5 — the skip scanner matches literal `pytest.` spellings only — CONFIRMED low

Citations exact: `_references` at `tests/test_gates.py:38-54`, `BANNED` at `:76-83`.
All four bypasses reproduced by appending to `tests/test_version.py` in a scratch
copy and running `tests/test_gates.py` (16 passed, i.e. scanner green) alongside
`tests/test_version.py`:

| appended | scanner | effect |
|---|---|---|
| `import pytest` + `pytestmark = pytest.mark.skip` | **16 passed** | `4 skipped` — whole module, no alias needed |
| `import pytest as _pt` + `pytestmark = _pt.mark.skip(reason=…)` | **16 passed** | `4 skipped` |
| `from pytest import skip as _s` then `_s(...)` | **16 passed** | 1 skipped |
| `raise unittest.SkipTest(...)` | **16 passed** | 1 skipped |
| `pytest.param(1, marks=pytest.mark.skip)` | **16 passed** | 1 skipped |

The unaliased `pytestmark = pytest.mark.skip` is the one that matters: it is a
module-scope assignment, so `_references` — which collects `ast.Call` funcs and
bare decorators only — never sees it, while the *called* decorator form of the same
attribute **is** caught. `CLAUDE.md:49-51` states the scanner "fails on any bare
`pytest.skip`, `pytest.importorskip` or `pytest.mark.skip`"; the assignment form
falsifies that sentence as written.

Not recorded anywhere as scoped-out — `test_gates.py`'s own docstring (`:1-11`)
claims the general property. Fix as the finder wrote it: match the trailing
attribute path rather than the full dotted name, and collect `ast.Attribute` in
assignment and keyword position. ~6 lines, test-only, no production surface, in the
one file whose entire purpose is closing this class. Worth filing.

`_sources()`'s non-recursive `TESTS.glob("*.py")` is real but currently inert:
`find tests -name '*.py' -mindepth 2` returns nothing. Mention in the issue body,
do not file separately.

## RX-D6 — nine `main.tf` attributes survive constant substitution — CONFIRMED low

All nine line citations verified at `672a500` and all nine mutations reproduced.
Each was applied alone to `orchestrator/backends/libvirt/tofu/main.tf` in a scratch
copy and `VCOWS_GATES=tofu … pytest -q tests/test_tofu_module.py` re-run:

| line | mutation | result |
|---|---|---|
| `:27` `name = var.base_volume.name` | `"wrongname.qcow2"` | 10 passed |
| `:28` `pool = var.pool` | `"nowhere"` | 10 passed |
| `:118` `loader` | `"/nowhere/OVMF.fd"` | 10 passed |
| `:120` `loader_type` | `null` | 10 passed |
| `:136` nvram `format` | `"raw"` | 10 passed |
| `:175` `device = "disk"` | `"cdrom"` | 10 passed |
| `:181` `driver.name` | `"bogus"` | 10 passed |
| `:201` nic `model` | `{ type = "e1000" }` | 10 passed |
| `:203` nic `source.network` | `null` | 10 passed |

Control: the same `driver` object with `discard = "unmap"` removed **fails**
(`test_the_module_renders_what_the_acceptance_run_settled`, `.tftest.hcl:210-211`),
so the harness is live and the survivals are gaps rather than a broken run. The
finder's scoping of `:181` to `driver.name` specifically is therefore right.

Confirmed at low. The three that carry a real failure are nvram `format` (a wrong
declared varstore format is the non-booting inversion of acceptance defect S6, since
libvirt reads the declared format), nic `source.network` (a nic with no source gets
no network), and base-volume `pool` (asserted for the overlay and seed at
`.tftest.hcl:69-76`, not for the golden image itself). Fix extends two existing
`alltrue([for k, v in var.vms : …])` comprehensions and the firmware block — no new
`run` blocks, no new files.

## RX-D7 — `_capture`'s timeout is unpinned — CONFIRMED low

Citation exact: `timeout=SHORT_TIMEOUT` is `orchestrator/tofu.py:256`. Mutated to
`timeout=None` in a scratch copy:

```
baseline                411 passed, 25 skipped
timeout=None            411 passed, 25 skipped
timeout=None, GATES=tofu 411 passed, 25 skipped
```

`SHORT_TIMEOUT`'s docstring (`tofu.py:40-43`) scopes it to "init/output/version
only". `#17` pinned the `init` half at `tofu.py:176`
(`tests/test_tofu_driver.py:313,317` assert both directions); `outputs()` and
`version()` reach the same constant through `_capture` and are asserted by nothing.
Not recorded as deliberate anywhere. Fix is one assertion in the capture tests,
test-only. Worth filing, and it pairs naturally with `#17` in one issue.

## RX-D8 — one fixture, every branch on the same side — CONFIRMED low

Citations exact: `tests/golden/libvirt.tfvars.json:3` `"create": true`, `:15`
`"firmware": "efi"`, `:23` `"bridge": null`. Both mutations reproduced:

| mutation | result |
|---|---|
| `main.tf:34` `local.base_path` false branch → `"/nowhere"` | 10 passed |
| `main.tf:116` `firmware = each.value.firmware == "efi" ? "efi" : null` → `"efi"` | 10 passed |

Not a recorded scope decision. `.tftest.hcl:13-17` names exactly one thing as
uncatchable here — the seed volume's `depends_on` — and says nothing about the
variables set; no `docs/findings.md` entry covers it either. The base-already-present
path is every deploy after the first on a host, and nothing evaluates it.

Confirmed at low, as a coverage statement rather than a module defect: it is what
makes several `RX-D6` survivors unreachable by any assertion. Fix is one further
`run` block with an overriding `variables` stanza (~15 lines) in a file that already
has the shape; that is proportionate. Do not file the BIOS and bridged-nic branches
as separate items.

## RX-D9 — three of five gate names demanded by nothing — REFUTED

The measurement is right and the citations are exact. `grep -rn VCOWS_GATES` over
both pipelines, the `justfile` and `scripts/` returns two setters: `justfile:90`
(`VCOWS_GATES=tofu`, under `test-tofu:` at `:89`) and `scripts/test-image.sh:16`
(`export VCOWS_GATES=image`). `rig`, `libvirt` and `pycdlib` are demanded by
nothing.

**This is the recorded design, not a gap.** `docs/ci.md:52-58` is a table of exactly
this question: `tofu` demanded, `image` demanded in the image job, `pycdlib`
"satisfied — a runtime dependency, installed by `just dev-env`", `rig` "**never** —
needs a reachable hypervisor". `docs/ci.md:60-62` and `docs/research/tooling-2026-08-29.md`
§5.8 (`:487-497`) both record why `VCOWS_GATES=all` is never set, and `CLAUDE.md:110`
lists it among the settled rejections.

The pycdlib half is also close to unreachable. `pycdlib>=1.16` is a hard
`[project] dependencies` entry (`pyproject.toml:37-41`), and every CI job in
`.github/workflows/{ci,image,scheduled}.yml` and `.gitlab-ci.yml` runs `just dev-env`
before `just check`, which installs the `uv.lock` export. A runner reaching
`just check` with `pycdlib` absent means `just dev-env` silently dropped a declared
dependency — a louder failure than the one the escalation would catch. `libvirt` is
covered by `tests/fake_libvirt.py:25`'s module-scope import, which `CLAUDE.md:22-24`
already records as the backstop.

Appending `,pycdlib` to `justfile:90` is one token, but it buys a demand against a
condition that cannot arise while `just dev-env` is the only correct venv. **Leave
it.** If anything is filed here it is a documentation nit: `docs/ci.md:53-58`'s table
lists four of the five gate names and omits `libvirt`.

## RX-D10 — citation drift at `tests/libvirt-module.tftest.hcl:39` — CONFIRMED nit

`.tftest.hcl:39` reads `(destroy.py:456-461)`. Verified at `672a500`:

* `_deletable` is `destroy.py:422-456`; `:456` is its closing `return True`.
* the `{overlay_name(target.marker.name), seed_name(target.marker.name)}` set is
  built at `:440-444` and tested at `:445` (`if PurePosixPath(path).name not in
  owned:`).
* `:459` begins `_deleted_on_name_alone`, a different function.

**Corrected citation: `destroy.py:440-445`** — one line tighter than the finder's
`:440-446`, which reaches into the reporting body. The comment's claim is true; only
the numbers moved (`c854e49`, `2f8ebe2`).

The finding's second half also holds, though it is outside this batch's scope:
`3be2c28`'s message cites the CI path filters as `image.yml:21-22` and
`.gitlab-ci.yml:96-97`; they are `.github/workflows/image.yml:22-23` and
`.gitlab-ci.yml:102-103`. `scripts/lib.sh:124-125`'s own sentence about those filters
is correct and was rightly left alone.

File the `.tftest.hcl:39` half as a one-line nit; a commit message cannot be edited,
so the `3be2c28` half is record-only.
