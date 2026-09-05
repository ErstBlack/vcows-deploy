# Test teeth — review

Agent: 12-test-teeth · Scope: `tests/` (+ whatever source it took to break) · Date: 2026-08-29

## Summary

* 40 mutations in a scratch copy: **26 caught, 14 survived.** Twelve survivors are in `main.tf`, which has no behavioural test: `tofu validate` + `tofu console` check types only, so every module mutation passed — deleting the marker `metadata`, deleting `backing_store`, re-introducing three of the five acceptance defects.
* Acceptance defects 1 and 2 can return unnoticed: `preflight` can be handed `sshcmd` and stay green; `container/entrypoint.py` is imported by no test.
* All three gates report success by skipping: `210 passed, 25 skipped`, exit 0.
* `test_the_build_is_reproducible` is timing-flaky (1 in 25) and the property it names is false: seed ISOs embed wall-clock timestamps.

## Mutation table

Scratch at `…/scratchpad/mutate`; `orchestrator.__file__` confirmed under the
copy. Baseline `210 passed, 25 skipped`; every survivor reproduced that line
exactly, exit 0. **Caught (26):** `decide()` inverted and its clash branch
skipped; `pool.refresh(0)` deleted; `NVRAM` shed from `FLOOR`; `MARKER_XMLNS`
and `VCOWS_NS` changed; base-size `ERROR`→`WARNING`; `render` returning the raw
URI; `routes: [{to: default}]`; `<backingStore>` followed in `disks_of`;
destroy's deployment filter dropped; run dir `0700`→`0755`; deploy ignoring
`p.fatal`; destroy skipping disk deletion; undefine-before-stop; an `os.unlink`
fallback; a shared seed name.

**Survived (14), caught by nothing.**

| Mutation | What it breaks in production |
|---|---|
| `main.tf`: delete `metadata = { xml = … }` | domains carry no marker; `destroy` can never find them |
| `main.tf`: delete `backing_store`; delete overlay `capacity`/`capacity_unit` | full copies not overlays; every guest gets the base image's size (A4) |
| `main.tf`: seed `format` `iso`→`raw`; delete `features`; hardcode `_VARS.fd`; remove `depends_on = [libvirt_volume.base]` | acceptance defects 3 and 4; the S6 fixed in `da3f45c`; a partial apply leaving unreachable seeds |
| `main.tf`: `running = false`; `boot_devices` `hd`→`cdrom`; delete `serials`/`consoles` | never started; boots the seed ISO; D26 console gone |
| `main.tf`: overlay `qcow2`→`raw`; cdrom `read_only=false`; `sda/sata`→`hdc/ide`; `host-passthrough`→`host-model`; disk source `.path`→`.name` | five device/format regressions |
| `preflight.py:61`: `connection_uri(…, "sshcmd")` | acceptance defect 1 — `transport in URL not recognised` |
| `schema.py`: `connection_uri` stops clearing `query`; `cli.py:216` `if not planned.changes.get("add")` → `if False` | an operator query string reaches the client; a plan that creates nothing is applied |
| `entrypoint.py`: `StrictHostKeyChecking no`; and a syntax error | host-key checking off; container cannot start |

## Findings

### F-TEETH-01 — `main.tf` has no behavioural test; every module mutation survives
- **Severity:** S1
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf`, `tests/test_tofu_module.py`
- **What:** the only tests touching the module run `tofu validate` (module
  type-check) and `tofu console` (tfvars type-check). Neither reads an attribute
  *value*. The test file's docstring names that limit; nothing covers it.
- **Why it matters here:** the marker in `<metadata>` is the only identity this
  tool has. A module edit dropping it produces VMs that boot correctly, report
  success, and can never be torn down by `vcows destroy`. Three of the five
  acceptance defects lived in this file, none of the three fixes is pinned.
- **Evidence:** twelve `main.tf` mutations, each `rc=0 210 passed, 25 skipped`;
  `tofu validate` accepts `format = { type = "raw" }` on the seed volume, and a
  domain with no `features` block at all.
- **Fix:** assert on the rendered module offline. `tofu console` already loads
  the golden tfvars into the initialised module, so evaluating a few expressions
  (seed format, `nv_ram` path, `features`, `backing_store`, `capacity`,
  `metadata`) pins them with no hypervisor.
- **Cost of the fix:** ~60 lines inside the existing `needs_tofu` gate, no
  runtime surface. Justified by this file producing 3 of the 5 defects.

### F-TEETH-02 — `preflight`'s transport is unasserted; acceptance defect 1 can return
- **Severity:** S2
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:61`, `tests/test_libvirt_schema.py:293`
- **What:** `test_preflight_and_the_provider_are_given_different_transports`
  tests `connection_uri` in isolation; nothing tests the call site. Changing line
  61 to `connection_uri(cfg["target"]["libvirt"], "sshcmd")` passes. `render`'s
  call site is pinned twice; `preflight`'s not at all.
- **Why it matters here:** half of the defect the acceptance run found. At a
  site it presents as `remote_open: transport in URL not recognised` — loud,
  fatal, not obviously about a URI scheme.
- **Evidence / fix:** the mutation, `rc=0 210 passed, 25 skipped`. Monkeypatch
  `libvirt.open` in `test_libvirt_preflight.py` and assert the URI is `qemu+ssh`,
  not `sshcmd` — ~6 lines, in a file that already fakes libvirt.

### F-TEETH-03 — `container/entrypoint.py` is covered by no test
- **Severity:** S2
- **Confidence:** high
- **Location:** `container/entrypoint.py`, `tests/`
- **What:** no test imports or executes it. Flipping `StrictHostKeyChecking yes`
  to `no` passes; so does a syntax error. The image gate runs the container only
  with `--entrypoint python3` / `sh`, so it never reaches the default entrypoint
  either, even with `VCOWS_IMAGE` set.
- **Why it matters here:** this file is the whole of acceptance defect 2's fix —
  credentials that cannot travel in the URI reach both clients only through the
  `~/.ssh/config` it writes. It is also the last thing keeping host-key checking
  on after R-D refused `no_verify=1`; that failure is silent.
- **Evidence:** both mutations `rc=0 210 passed, 25 skipped`; `grep -rn
  "IdentityFile\|StrictHostKey" tests/` returns nothing.
- **Fix / cost:** it imports nothing from libvirt — call its config-writing
  function against a `tmp_path` HOME and assert the lines. ~30 lines.

### F-TEETH-04 — seed ISOs are not reproducible, and the test that says so is flaky
- **Severity:** S3
- **Confidence:** high
- **Location:** `tests/test_seed_iso.py:77`, `orchestrator/backends/libvirt/prepare.py:96`
- **What:** `build_seed_iso` calls `pycdlib.new(**ISO_ARGS)` with no pinned
  dates, so the ISO embeds wall-clock timestamps. The test builds both ISOs back
  to back and passes only because they usually land in one clock tick.
- **Why it matters here:** the stated purpose — "a run directory kept for
  debugging can be compared against a rebuild" — never holds, since the rebuild
  is minutes or days later.
- **Evidence:** two ISOs built 1.2 s apart from identical inputs differ in 33
  bytes (offsets 32947, 33594, …). Run 25 times: 1 failure, 24 passes; it also
  failed spuriously during two unrelated mutation runs.
- **Fix / cost:** either pin the dates pycdlib writes (a few lines in
  `prepare.py`) or drop both the claim and the test.

### F-TEETH-05 — all three gates report success by skipping
- **Severity:** S3
- **Confidence:** high
- **Location:** `tests/conftest.py:22-38`, `tests/test_image.py:35`, `tests/test_libvirt_rig.py:56`
- **What:** `pytest -q` with no environment exits 0 with `210 passed, 25
  skipped`. Renaming `.tools/tofu-mirror` gives `202 passed, 33 skipped`, also
  exit 0. Nothing asserts a gate ran.
- **Why it matters here:** `test_image.py`'s docstring says "a gate that quietly
  passes because it did not run is worse than no gate" — which is what the suite
  does in aggregate. Without `tofu` the module gate, the only thing looking at
  the HCL at all, disappears silently and F-TEETH-01 becomes total.
- **Evidence / fix:** the runs above. An opt-in `VCOWS_GATES=all` in
  `conftest.py` turning each `skipif` into a failure — three lines.

### F-TEETH-06 — the test tofurc permits egress the shipped one forbids
- **Severity:** S3
- **Confidence:** high
- **Location:** `tests/conftest.py:47-56` vs `container/tofurc:9-18`
- **What:** `tofu_env` writes `filesystem_mirror { include =
  ["registry.opentofu.org/dmacvicar/libvirt"] }` **plus** `direct { exclude =
  [same] }`. The shipped file has `include = [".../*/*"]` and no `direct` block,
  deliberately. Opposite fallback for any other provider: the test config reaches
  the registry, the image fails fast.
- **Why it matters here:** the shipped file is asserted only by
  `test_the_provider_installs_from_the_baked_mirror_offline`, behind the
  `VCOWS_IMAGE` gate, which skips by default (F-TEETH-05). If the module gains a
  second provider, `test_module_validates` on a connected dev box installs it
  from the registry and passes green while the image fails at a site. Latent
  today: one provider is declared.
- **Evidence / fix:** the two files as quoted. Have `tofu_env` read
  `container/tofurc` and substitute the mirror path — removes surface, and puts
  the shipped file in the default suite.

### F-TEETH-07 — `test_credentials_never_reach_the_uri` cannot fail
- **Severity:** S5
- **Confidence:** high
- **Location:** `tests/test_libvirt_schema.py:303`
- **What:** the test's input `uri` carries no query string, so its `"?" not in
  uri` assertion cannot fail: `connection_uri` never *adds* credentials, and the
  only thing `query=""` does is strip an operator-supplied query. Removing
  `query=""` passes (`rc=0 210 passed, 25 skipped`).
- **Why it matters here:** the schema validator does reject a query string
  (`test_libvirt_schema.py:86-87`), so the defence is real but single-layered,
  and the test claiming to be the second layer is not one.
- **Evidence / fix:** the mutation above; feed it `?no_verify=1` instead. One
  changed literal.

### F-TEETH-08 — the "plan proposes no creates" guard is untested
- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/cli.py:216`
- **What:** replacing `if not planned.changes.get("add"):` with `if False:`
  passes. Nothing exercises the branch refusing a plan that creates nothing.
- **Why it matters here:** it is the last check between a mis-rendered tfvars and
  an apply that reports success having done nothing — a silent-success shape.
- **Evidence / fix:** the mutation, `rc=0 210 passed, 25 skipped`. One
  `test_cli.py` case with a fake plan returning `{"add": 0}`, ~8 lines.

## Checked and sound

* **The golden file is load-bearing, not self-asserting.** It is committed and nothing writes it (`grep` finds only `.read_text()`); four independent mutations were caught by `test_matches_the_golden_file` alone.
* **`decide()` is the best-tested thing here** — both mutations caught by 4-7 tests each across `test_policy.py`, `test_cli.py`, `test_seam.py`. Inverting it broke `test_full_pipeline_without_libvirt`, so the seam test executes the policy rather than only importing it.
* **`destroy.py` and `preflight.py` are well pinned**: stop-before-undefine, the NVRAM floor, the no-`os.unlink` rule, disks collected for an already-gone domain, the pool refresh, orphan refusal, the `<backingStore>` exclusion, the base-size `ERROR` severity — each by a named test that states the reason.
* **`test_tofu_cli_gate.py` pins genuinely fragile CLI behaviour** (`-json-into` coexistence, self-contained saved plans, stale-plan refusal) against the real binary with an empty mirror.

## Not checked

* The rig and image gates were read but never executed — the brief forbids setting `VCOWS_RIG_URI` / `VCOWS_IMAGE`.
* `tests/fake_libvirt.py`'s fidelity to real libvirt error codes and `undefineFlags` semantics — a fake wrong the same way as the code hides a defect my mutations cannot see.
* `orchestrator/tofu.py` and `config.py` beyond the mutations above.

## Deserves its own agent

* **`main.tf` read against libvirt's domain XML rules on RHEL 9.** Nothing checks the module, so someone should read it. `loader_readonly = "yes"`, the `/var/lib/libvirt/qemu/nvram/` path, `q35` and `features.apic = {}` are all Fedora-measured.
* **`container/entrypoint.py` on its own** — 130 lines, zero coverage, and it writes the file that decides whether host-key checking happens.
* **`docs/archive/acceptance.md:141-149` "still open"** — D3's unverified artefact and the unusable serial console.
