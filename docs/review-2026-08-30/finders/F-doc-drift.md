# Dimension F — documentation and decision drift, second pass

Branch `feature/scaffold`, HEAD `6497f30`. Scope: `da3f45c..HEAD` (22 commits), with
particular attention to whether S12's sixteen comment fixes are still true after the
remediation (`df60f74..b6ec3f6`) and the tooling layer (`cfa3044..6497f30`) both moved
the code underneath them.

## Summary

S12's sixteen rows all landed and all fifteen of the assertions I could check are still
true at HEAD. The eleven commits after S12 barely touched `orchestrator/` — the diff
`b6ec3f6..HEAD` over `orchestrator/` and `container/` is fourteen `# noqa` suffixes, one
rewritten `entrypoint.install()` error message and one rewritten `IMAGE_SCHEMA` comment,
so S12's work did not rot. Nothing in `findings.md` §2 or §3 is contradicted by the code
at HEAD: I checked every anchor those two sections make and each one still points at what
it says.

The drift that exists is all in the **new** layer — `docs/ci.md`, written in `a7be59b`
and then contradicted by three later commits that changed the pipelines and the tool set
without coming back to it — plus one stale evidence figure in `findings.md` §6 and one
docstring miscount in `orchestrator/` that predates this review entirely.

No type (b) findings. There is no decision in `findings.md` that the code no longer
honours. Findings 1, 2 and 5 are type (c) — the code changed and the record did not.
Findings 3, 4 and 6 are type (a) — a claim that is simply false.

---

## Drift table

| # | Claim | Where written | What the code at HEAD does | Type | Fix |
|---|---|---|---|---|---|
| RW-F1 | "Create two pipeline schedules: monthly with `REBUILD_SCAN=1`, weekly with `MUTANTS=1`." | `docs/ci.md:145-146` | No job in `.gitlab-ci.yml`, `.github/workflows/*` or anywhere else reads `$MUTANTS`. The same document at `:31-33` says there is deliberately no mutation-testing job, and `justfile:94`, `pyproject.toml:106` and `scheduled.yml:3` all say the same. | (c) | prose |
| RW-F2 | `check` and `tofu` run on "every push and PR/MR" | `docs/ci.md:26-27` | GitHub's `ci.yml` scopes `push` to `branches: [master]`. A push to a feature branch runs neither job. `workflow_dispatch` (added `cb52cec`) is the only branch-side trigger and the table does not mention it. | (c) | prose |
| RW-F3 | `pycdlib` "is in the dev group" | `docs/ci.md:51` | `pycdlib>=1.16` is in `[project].dependencies` (`pyproject.toml:38`), not `[dependency-groups].dev` (`:45-57`). The gate *is* satisfied in CI — via `uv pip install -e .` inside `just dev-env` — so only the stated reason is wrong. | (a) | prose |
| RW-F4 | "the full suite is **390 passed, 0 skipped**" | `docs/findings.md:404` | The tracked suite collects **404** tests at HEAD. Written at `cfa3044`; `583b655`, `0132fd2` and `2c90b02` added `test_gates.py` (8), `test_properties.py` (5) and others afterwards. | (c) | prose |
| RW-F5 | `install-tools.sh` downloads "pinned uv, tofu, just, hadolint, trivy, syft" | `README.md:276` | It also installs `cosign` (`scripts/install-tools.sh:30,145`), added in `6497f30`, which touched `install-tools.sh` and the `justfile` and not the README. `justfile:28` carries the same stale list, but the justfile is dimension G's. | (c) | prose |
| RW-F6 | "The libvirt backend: seven methods" | `orchestrator/backends/libvirt/__init__.py:1` | `LibvirtBackend` implements eight: `config_schema`, `validate`, `connect`, `preflight`, `destroy`, `prepare`, `render`, `parse_outputs`. The ABC has eight `@abstractmethod`s (`base.py:356-427`), and had eight at `da3f45c` too — this one predates the diff under review and was missed by agent 09. The next two sentences ("four of them delegate… the three that hold a connection…") add to seven, so `parse_outputs` is the one dropped. | (a) | prose |

---

## S12's sixteen rows, verified at HEAD

Each row of the checklist's S12, checked against the code rather than against the commit.

| S12 row | Verdict at HEAD | Evidence |
|---|---|---|
| `marker.py:63-68` — `deployment` is read by `decide()` and `cmd_destroy` | **true** | `marker.py:57-70` names both consumers. `base.py:280-300` is the `decide()` refusal; `cli.py:443-448,465-475` is the destroy filter and the skip report. |
| `marker.py:90` — `v` is provenance, `MARKER_XMLNS` is the discriminator | **true** | `marker.py:74-84`. Nothing in `orchestrator/` branches on `.v`; `test_marker.test_parser_ignores_unknown_keys` pins a `0.9.9.9` marker parsing cleanly. |
| `findings.md:87` — disk paths read at discovery time | **true** | The paragraph now says destroy "re-reads the same domain immediately before undefining", which is `_reverify` (`destroy.py:404-434`), called at `destroy.py:535` after the confirm. The "already gone" half — recorded paths checked against every other domain's claims and the two owned names — is `_claimed_elsewhere` + `_deletable` (`destroy.py:436-500`). |
| `schema.py:214-230`, `variables.tf:8` — credentials travel via `~/.ssh/config` | **true**, and the *message* was fixed, not only the comment | `schema.py:315-333` is the emitted refusal; `variables.tf:9` no longer claims SSH options ride in the URI. `connection_uri` (`schema.py:228-229`) replaces scheme and clears query only, as claimed. |
| `cli.py:239-243`, `tofu.py:176-184` — D48, point at the plugin cache | **true** | `_stage_module`'s docstring (`cli.py:385-410`) and `tofu.init`'s (`tofu.py:210-218`) both name D48 and `TF_PLUGIN_CACHE_DIR=/opt/tofu/plugin-cache`, which `Containerfile:144,150` really does set and warm. The lock claim is also true: `Containerfile:123-124` copies `docs/provider-0.9.8.lock.hcl` in as the module's `.terraform.lock.hcl`, and the module dir holds exactly three `.tf` files, so "three in a checkout, four in the image" holds. |
| `main.tf:181-185` — record what acceptance found about the pty console | **true** | `main.tf:209-225`. |
| `orchestrator/__init__.py:4-12` — name each consumer and its gate | **true** | All seven named tests exist and assert against `VERSION`; the two marked "image gate" are `test_image.py`, whose `pytestmark` is `gate("image", …)` (`test_image.py:45`). |
| `cli.py:16`, `README.md:75` — argparse exits 2 | **true** | `cli.py:16-20`, `README.md:104-105`. |
| `destroy.py:45-47` — `UNDEFINE_NVRAM` arrived *in* 1.2.9 | **true** | `destroy.py:47,57-60`. |
| `findings.md` §3, `base.py:3-4`, `config.py:5-6` — the `IMAGE_SCHEMA` exception | **true** | `findings.md:169-171`, `base.py:6-14`, `config.py:5-9`. All three name `IMAGE_SCHEMA` and `orchestrator/qcow2.py` as the two core sites. |
| `manifest.py:44`, `Containerfile:57` — write "roughly" | **true** | `Containerfile:72`, `container/manifest.py:131`. (`scripts/lint.sh:104` says "~161" — hedged the same way, not a contradiction.) |
| `preflight.py:88` — `disks_of` collects file-backed sources only | **true** | `preflight.py:100-116`, and the body at `:117-130` only reads `source/@file`. |
| `destroy.py:24-26`, `:176-179`, `:203-214` — three reporting claims | **true** | Checked all three regions at HEAD (`destroy.py:24-26`, `:188-203`, `:229-241`). Each describes what the code does. |
| `findings.md` §2 — `orphan_volumes` bound, NVRAM varstore gap | **true** | `findings.md:140` and `:144`. The NVRAM entry was rewritten again in `cfa3044` from "unverified" to "observed, 2026-08-29" and scoped to the qcow2 template path. |
| README — cloud-init renames NICs to `nic0`/`nic1` | **true** | `README.md:160-165`; `prepare.py:112` emits `ethernets[f"nic{i}"]`. |
| `prepare.py:193` — the v6 half is not configured | **true** | `prepare.py:86-93`; `dhcp6: False` at `:105` and `to: 0.0.0.0/0` at `:109`. `README.md:167-170` carries the same. |

Two rows S12 recorded as "checked, needed nothing" — `findings.md`'s disk-path paragraph
and `destroy.py`'s three reporting claims — I re-checked independently rather than taking
the commit message's word, and both read true against the code at HEAD.

---

## What else I checked and found clean

**`findings.md` §2 and §3 anchors.** Every falsifiable claim in both sections still holds:

- `Marker` payload table (`:78-85`) matches `marker.py:53-84` field for field.
- `derive_id` = `uuid5(VCOWS_NS, f"{deployment}/{name}")` — `marker.py:168`.
- `derive_mac` = the same with `#nic{index}` appended — `schema.py:181`.
- "Ceilings 512, 4 TiB, 64 TiB, read from `VCOWS_MAX_*` at import, the same shape as
  `cli.MANIFEST`" — `schema.py:95-97`, `cli.py:55`.
- "`cmd_deploy` treats `Discovered.problems` as fatal and `cmd_destroy` prints them as
  advisory" — `cli.py:450-462`.
- "`pool.refresh(0)` before any volume is enumerated or resolved, in preflight and in
  destroy alike" — `preflight.py` header, `destroy.py:_refresh_pools`.
- "Report and skip anything that will not resolve… never add an `os.unlink` fallback" —
  `destroy.py:199-217`.
- "exists (not compared)" — `base.py:289`, pinned by `test_policy.py:29`.
- The `Backend` ABC block (`:196-217`) matches `base.py` exactly, including
  `destroy(...) -> Outcome` after the S2 change. `fake_backend.py` and
  `LibvirtBackend.destroy` both return `Outcome`.
- "Each is printed where it arrives… `Result.warnings` is recorded into `run.json`" —
  `cli.py:358-360`, `tofu.py:76-84`.
- "core… never reads `artifacts`" — the only `artifacts` references outside the libvirt
  package are the dataclass fields and their docstrings.
- The §3 module tree omits `orchestrator/qcow2.py` and `backends/libvirt/errors.py`, but
  `qcow2.py:3-5` explicitly says the tree "enumerated the significant modules rather than
  forbidding others", so that is covered rather than drifted.

**The `Backend.destroy` signature change (S2).** This was the change most likely to have
left false docstrings behind. It did not. `base.py:420-427` documents the `Outcome` return
and why it is not `None`; `libvirt/__init__.py:60-61` and `fake_backend.py` both carry the
new annotation; `findings.md`'s interface block was updated; `destroy.py`'s module
docstring's third bullet ("Every object's outcome is reported, and any failure is fatal")
is what the code now does.

**`docs/rhel9-target.md`.** Every `main.tf` anchor it makes is exact at HEAD: `:114-125`
(firmware + pinned loader), `:133` (the `_VARS.${…}` suffix ternary), `:112`
(`type_machine`), `:181` (`discard = "unmap"`), `:232` (`rngs`). Its `_GATED` quotation
matches `destroy.py:62` verbatim, and the rig-fixture warning matches
`test_libvirt_rig.py`.

**`docs/acceptance.md`.** Defect 4's `features = { acpi = true, apic = {} }` is
`main.tf:151-153`; the fixture it cites exists; defect 5's `0.0.0.0/0` is `prepare.py:109`.

**`README.md`.** The `:z`/`:Z` mount split (S8) landed at `:74-78` and the rationale at
`:82-86` is correct. Exit codes, the run-directory `find` retention line, the ceilings and
their env vars, the gate table (all five names match `test_gates.py:27` and the four
`gate()`/`require()` call sites), the `VCOWS_GATES` whitespace note (verified against
`conftest.py:37,40-41`), the air-gap gate description (matches `test_image.py:133-149`),
the autostart claim (`main.tf:99`) and the plugin-cache claim all check out.

**`orchestrator/tofu.py`'s cross-reference** to `orchestrator-architecture.md:226` points
at the right paragraph.

---

## Out of scope, recorded rather than filed

Per the brief, defects in `scripts/*.sh`, the `justfile` and the pipeline files are
post-merge and dimension G's. Two of them are documentation drift in the same shape as the
findings above, so they are recorded here for whoever picks up G:

- `scripts/lint.sh:7` — "**Runs all five and reports all five.**" `main()` at `:89-113`
  runs six gates: ruff check, ruff format, hadolint, `tofu fmt`, shellcheck, and
  `workflows carry no logic`. The sixth was added in the same commit that wrote the
  comment. `docs/ci.md:26` lists the same five (plus `ty` and `pytest` from the other two
  recipes) and also omits the workflow thinness check.
- `justfile:28` — "(uv, tofu, just, hadolint, trivy, syft)", the same stale list as
  RW-F5.

One more, which is a test-teeth question rather than doc drift, so it is noted and not
filed: `tests/test_gates.py:99-103`,
`test_gates_is_parsed_without_whitespace_stripping`, asserts only
`isinstance(GATES, set)`. Its name and docstring describe the `tofu, image` parsing
behaviour and the body never constructs that input, so the behaviour the README and
`docs/ci.md` both document is not actually pinned by anything. The parsing claim itself is
true — I verified `{g for g in "tofu, image".split(",") if g}` yields `{"tofu", " image"}`
and `demanded("image")` is then false — so this is a vacuous gate, not a false document.
The same file's docstring at `:10` says "These two tests are the thing that notices" while
the file holds four.

---

## Coverage

Read in full: `docs/findings.md`, `README.md`, `docs/ci.md`, `docs/acceptance.md`,
`docs/rhel9-target.md`, `justfile`, `.gitlab-ci.yml`, all three GitHub workflows,
`.pre-commit-config.yaml`, `.gitignore`, `scripts/lint.sh`, `scripts/os-deps.sh`,
`tests/test_gates.py`, and every module docstring in `orchestrator/`.

Not read in full: `docs/orchestrator-architecture.md` (523 lines) beyond the errata
anchors — `findings.md`'s appendix declares it archived background whose concrete claims
are wrong, so drift in it is not a defect; `docs/tooling-2026-08-29.md` (512 lines, a
survey rather than a record); `docs/future-backends.md`; `docs/spikes.md`; the nineteen
agent reports under `docs/review-2026-08-29/` (read only the checklist and the S12 rows).
Not checked: `docs/cve-baseline.json`'s per-CVE rationales, which need trivy output I did
not run; the `scripts/*.sh` bodies beyond `lint.sh`, `os-deps.sh` and
`install-tools.sh`'s tool list; and anything requiring a live rig, a built image or a
network.
