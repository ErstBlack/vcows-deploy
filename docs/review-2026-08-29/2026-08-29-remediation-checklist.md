# vcows-deploy v0.1.0.0 — remediation checklist

Derived from `docs/2026-08-29-review.md`. Every finding in that review appears here
exactly once. IDs in **bold** are the review's section-2 numbering; bracketed IDs are
the originating agent finding in `docs/review-2026-08-29/`.

## How to use this

Twelve sessions, each sized to one working sitting and one mental model. Each states
its **goal**, its **exit criteria**, and what it **depends on**. Sessions are ordered
so that dependencies come first, but S3–S12 are otherwise independent and can be
reordered or parallelised across people.

Two rules that come out of the review itself:

- **S1 and S2 before anyone else depends on this build.** Everything after is quality.
- **S3 is deadline-bound.** It is free until first ship and impossible afterwards.

Estimated sizes assume one person who has read the review. `(S)` ≈ under an hour,
`(M)` ≈ half a day, `(L)` ≈ a full day.

---

## S1 — Stop the two S1s `(M)` · depends on nothing

**Goal:** close the injection hole and the stale-teardown window. Nothing else in this
list matters until these land.

- [ ] **2.1** Add a path pattern to `ssh_keyfile` and `known_hosts` — `^/[^\s]*$` — in `TARGET_SCHEMA`. `backends/libvirt/schema.py:103-104` [18 F-SEC-01]
- [ ] **2.1** Add a regression test: a newline in either field is rejected by `validate`. Assert on the schema, not on `ssh_config()` output.
- [ ] **2.1** Decide whether `entrypoint.install()` should still run on `validate`. It currently writes `~/.ssh/config` before any schema check, contradicting `cmd_validate`'s "nothing is written". [18]
- [ ] **2.2** After `_confirm` returns true, re-read each target's `XMLDesc`; drop any whose UUID no longer resolves or whose marker no longer matches. `cli.py:258-294`, `destroy.py:243-255` [16 F-WARN-02]
- [ ] **2.2** Add a regression test against `fake_libvirt`: a target whose UUID no longer resolves must never reach `_delete_volume`. ~10 lines.
- [ ] **2.9** Add `ERR_NO_DOMAIN = 42` beside the three existing constants; make any other code from `lookupByUUIDString` a fatal `Problem` that skips the disk loop. `destroy.py:243-246` [08 F-SILENT-01, 14 F-DSK-01]
- [ ] **2.9** Pin `ERR_NO_DOMAIN` in `test_error_codes_match_the_installed_binding`.

**Exit criteria:** the two regression tests fail on the unfixed code and pass on the
fixed code. **Do not** treat 2.2 and 2.9 as one fix — the review's section 5 records
why that mistake is available.

---

## S2 — The reporting spine `(L)` · depends on S1

**Goal:** one refactor closing theme 7.1. This is the highest-leverage session in the
list: it resolves four filings, supplies the second half of 2.11, and makes three
false docstrings true for free.

- [ ] **2.3** Change `Backend.destroy` to return `Outcome`; update the ABC, the libvirt backend, and `fake_backend`. `base.py:309`, `backends/libvirt/__init__.py:52`, `destroy.py:233` [02 F-LVC-01, 08 F-SILENT-02, 11 F-LIFE-01, 16 F-WARN-01]
- [ ] **2.3** Have `cmd_destroy` print `out.skipped` and every non-fatal `Problem`. `cli.py:294-304`
- [ ] **2.3** Make a non-empty `skipped` a non-zero exit.
- [ ] **2.3** Record the real outcome in `run.json`, not `sorted(e.name for e in targets)`.
- [ ] **2.3** Make an inactive pool holding a target's disk an ERROR rather than a bare `continue`. `destroy.py:218`
- [ ] **2.5** Wrap each verb's body from `_run_dir` onward in `try/except BaseException`, record `outcome="failed"` with the exception text, re-raise. Must cover `cmd_destroy`, not only `cmd_deploy`. `cli.py:173-233`, `:254-305` [05 F-DRV-03, 11 F-LIFE-04, 13 F-RUNDIR-01, 16 F-WARN-03]
- [ ] **2.6** Refuse a *non-empty* `--run-dir` in `_run_dir`, before `_look()` spends a connection. Keep the D23/D40 guarantee — an empty directory must still work for a bind-mounted mountpoint. `cli.py:71-81` [05 F-DRV-01, 13 F-RUNDIR-03]
- [ ] **2.6** Update `README.md:66` to show the default (`-v ./runs:/runs`, no `--run-dir`) and describe `--run-dir` as the one-off it is. [11 F-LIFE-07]
- [ ] Add `"problems": [str(p) for p in problems]` to `_record` at both deploy call sites. `cli.py:94-124` [05 F-DRV-04]
- [ ] Have `config.load` return non-fatal problems alongside the dict; feed them into the three connected verbs' stderr loop and drop `cmd_validate`'s second `validate()` call. `config.py:132` [01 F-CORE-03, 16 F-WARN-04]
- [ ] Raise if `vms` is absent from `parse_outputs`' raw input, or assert `len(inventory.vms) == len(creating)` before recording. `backends/libvirt/__init__.py:91` [08 F-SILENT-03]
- [ ] Decide and record: `Outcome`, `Discovered.problems`, `Result.diagnostics` and `ConfigError.problems` are four result carriers, three lossy. §5 argues against unifying them, so "print each at its own consumer" is probably right — say so out loud. [16, 19 G13]
- [ ] Print `tofu.py`'s `Result.diagnostics` warning-severity entries, which nothing currently reads. `tofu.py` [16]

**Exit criteria:** a destroy against an inactive pool exits non-zero and names every
leaked volume; a failed apply and a failed destroy both leave a `run.json` saying so.

---

## S3 — One-way doors, before first ship `(S)` · depends on nothing

**Goal:** settle the derivations that cannot change after the first VM exists. Small
session, hard deadline.

- [ ] **2.10** Decide: fold `deployment` into `derive_mac`'s uuid5 input. `backends/libvirt/schema.py:122` [03 F-LVOFF-02, 15 F-XDEP-03]
- [ ] **2.10** Decide the same for `derive_id`. `marker.py` [15]
- [ ] Decide whether the seed ISO's `instance-id` / `local-hostname` carry deployment identity — currently two deployments with one VM name give byte-identical seeds. `prepare.seed_files` [15, 19 G16]
- [ ] Decide whether volume names get decorated. **Not** a one-way door — destroy resolves disks from domain XML rather than re-deriving — but it trades away D16's "predictable for hand-debugging". Decide it *with* the above, not separately. [15 F-XDEP-04]
- [ ] Re-pin the affected tests and record the decision in `findings.md` §2.
- [ ] Document the per-NIC `mac:` override, which is currently the only escape and is undocumented. [03]

**Exit criteria:** a written decision for all four names, applied or explicitly
deferred with the reason. Do not leave this session half-decided.

---

## S4 — Messages that instruct destruction `(S)` · depends on nothing

**Goal:** three message fixes, each cheap, one of them the review's cheapest
high-consequence item.

- [ ] **2.4** Rewrite D30's size-mismatch message: name the non-destructive procedure (a new `base_volume_name` sets `create = True` and uploads alongside). `preflight.py:300-309` [02 F-LVC-02, 11 F-LIFE-02, 15 F-XDEP-01]
- [ ] **2.4** Optionally count the overlays that would break — `walk()` already holds the `<backingStore><path>` data. One `findtext` and a counter.
- [ ] **2.11** Reword `orphan_volumes`' refusal: state the cause as a possibility, and say the volume may belong to another deployment on this shared pool. `preflight.py:315-341` [15 F-XDEP-02]
- [ ] Add a semantic check refusing a config whose `base_volume_name` equals any `overlay_name(vm)` or `seed_name(vm)`. ~6 lines. [03 F-LVOFF-09, 04 F-TOFU-02]
- [ ] Add a README line on refreshing the golden image — the procedure exists in code and is documented nowhere. [15]
- [ ] Filter deploy-oriented `problems` out of `cmd_destroy`'s output, or scope them: D30's message currently prints *during a destroy*, to an operator already in a destructive frame of mind. `cli.py:274` [10, 19 G10]

**Exit criteria:** no message in the tree instructs an operator to delete a shared
object without naming what breaks.

---

## S5 — Input validation `(M)` · depends on nothing

**Goal:** close what gets past `validate`. `target.libvirt` is the one-way door (F11),
so tightenings are cheaper now than ever again.

- [ ] **2.12** `"pattern": "^/"` on `source_qcow2`. `config.py:42` [06 F-SUPPLY-02, 18 F-SEC-02]
- [ ] **2.13** Reject a set `parts.password` in `_check_target`. `schema.py:193-237` [03 F-LVOFF-01, 13 F-RUNDIR-04, 18 F-SEC-04]
- [ ] Replace `$` with `\Z` in the name and MAC patterns, and in `config.py`'s patterns including `image.sha256`. `schema.py:37,39`, `config.py:33` [03 F-LVOFF-04]
- [ ] Require `loader_format` once `loader` is set. `schema.py:258` [03 F-LVOFF-05]
- [ ] Add `maximum` to `vcpus`, `memory_mib`, `disk_gb`. `schema.py:73-75` [03 F-LVOFF-06]
- [ ] Reject an `ip_cidr` that is its own network or broadcast address; skip for /31 and /32. `schema.py:332` [03 F-LVOFF-08]
- [ ] WARN when `ssh_keyfile` / `known_hosts` do not exist — not an error, since `validate` runs anywhere. `schema.py:103` [03 F-LVOFF-10]
- [ ] Emit the default route only for `primary_index(vm)`, not every NIC. `prepare.py:196` [03 F-LVOFF-03]
- [ ] Replace `Marker.from_json`'s `str()` coercion with an `isinstance` test raising `MarkerError`. `marker.py:125` [01 F-CORE-04]
- [ ] Blame the filename, not the `deployment` key, when the defaulted stem fails its pattern. `config.py:130` [01 F-CORE-07]

**Exit criteria:** each item has a test asserting the *rejection*, not just the fix.

---

## S6 — libvirt error handling `(M)` · depends on nothing

**Goal:** make `preflight` match error codes the way `destroy` already does.

- [ ] Match `VIR_ERR_NO_STORAGE_POOL` / `VIR_ERR_NO_NETWORK` numerically and re-raise otherwise. `preflight.py:189-199`, `:359-368` [02 F-LVC-05]
- [ ] Replace `DHCPLeases()`'s bare `pass` with a match on `OPERATION_INVALID`/`NO_SUPPORT` and a WARNING for anything else. `preflight.py:376-382` [08 F-SILENT-04]
- [ ] Wrap `_domains`' loop body in the `except (libvirtError, ET.ParseError): continue` that `walk` already uses. `preflight.py:129-142` [02 F-LVC-04]
- [ ] Move the `dom.isActive()` calls inside `_stop`'s existing `try`, or wrap the per-target body so one raise cannot abort the loop. `destroy.py:103`, `:110` [02 F-LVC-03]
- [ ] Raise a failed `pool.refresh(0)` from WARNING to ERROR. Deploy then refuses; destroy still proceeds through the documented asymmetry. `preflight.py:210-221` [02 F-LVC-07]
- [ ] Build `orphan_volumes`' `claimed` set from full paths, not basenames — `walk()` already carries `path`. `preflight.py:444` [02 F-LVC-06]
- [ ] Give `walk` a `-> tuple[dict, list[Problem]]` shape and emit a WARNING for a volume whose XML will not parse. `preflight.py:235-242` [08 F-SILENT-06]
- [ ] Restrict `destroy` to deleting paths whose basename is in `{overlay_name, seed_name}` for that target's marker; report anything else as skipped. `destroy.py:184` [18 F-SEC-03]
- [ ] Guard `getLibVersion()`, `listAllStoragePools()`, `pool.isActive()`/`pool.name()`. `destroy.py:217`, `:226`, `:238` [14]

**Exit criteria:** every `libvirtError` catch in `orchestrator/` either matches a
numeric code or has every branch fatal.

---

## S7 — Test teeth `(L)` · depends on S1, S2 (so the new behaviour gets pinned too)

**Goal:** make the suite fail when the code is wrong. Do the flaky-test item **first**
— it is a prerequisite for the mutation tooling in S12.

- [ ] Pin the dates `pycdlib` writes, or drop both the reproducibility claim and its test. Flaky at 1-in-25 today. `prepare.py:96`, `test_seed_iso.py:77` [12 F-TEETH-04]
- [ ] **2.7** Add behavioural assertions on the rendered module via `tofu console` against the golden tfvars: `metadata.xml`, seed `format`, `backing_store`, overlay `capacity`, `features`, `nv_ram`, `boot_devices`, `running`. ~60 lines inside the existing `needs_tofu` gate. [12 F-TEETH-01]
- [ ] Add `tests/test_entrypoint.py` — ungated, imports nothing from libvirt. Call `ssh_config()` against a `tmp_path` HOME, assert the five directives, assert newline rejection. ~30 lines. [12 F-TEETH-03, 18, 19 G1]
- [ ] **2.14** Monkeypatch `libvirt.open` and assert `preflight` is given `qemu+ssh`, not `sshcmd`. ~6 lines. [12 F-TEETH-02]
- [ ] Add `VCOWS_GATES=all` to `conftest.py`, turning each `skipif` into a failure. Three lines. [12 F-TEETH-05]
- [ ] Have `tofu_env` read `container/tofurc` and substitute the mirror path, so the shipped air-gap config is in the default suite. [09 F-CMT-06, 12 F-TEETH-06]
- [ ] Feed `test_credentials_never_reach_the_uri` a `?no_verify=1` input so it can fail. `test_libvirt_schema.py:303` [12 F-TEETH-07]
- [ ] Add a `test_cli.py` case for the "plan proposes no creates" branch. ~8 lines. [12 F-TEETH-08]
- [ ] Assert `returncode == 0` alongside `diagnostics(...) == ""`, or use `validate -json`. `test_tofu_module.py:66` [08 F-SILENT-09]
- [ ] Key `fake_libvirt`'s `storageVolLookupByPath` on the full path, not the basename suffix. `fake_libvirt.py:161` [14 F-DSK-02]
- [ ] Add a duplicate-logical-name case to `test_policy.py`. [01 F-CORE-01]

**Exit criteria:** re-run agent 12's fourteen surviving mutations; at least the two
Python ones and the eight `main.tf` value mutations are now caught.

---

## S8 — Provenance and supply chain `(M)` · depends on nothing

**Goal:** make the image able to say truthfully what it is.

- [ ] **2.8** Add two asserts to `test_the_build_manifest_records_what_shipped`: `git_sha` is 40 hex, and equals `git rev-parse HEAD` when `git status --porcelain` is empty. `test_image.py:241` [19 G2]
- [ ] **2.8** Add a build-time guard recording a `-dirty` suffix rather than a clean SHA. `Containerfile` / `container/manifest.py`
- [ ] **2.8** Rebuild the image and confirm the manifest names `da3f45c` or later.
- [ ] Copy `MANIFEST` into the run directory in `_record`, guarded by `MANIFEST.is_file()`. Two lines. [05 F-DRV-06, 06 F-SUPPLY-01, 07 F-DEC-01, 11 F-LIFE-03, 13 F-RUNDIR-05]
- [ ] Add a root `LICENSE`, `project.license` in `pyproject.toml`, and that identifier to `IMAGE_LICENSES`. [06 F-SUPPLY-04]
- [ ] Append `%{VENDOR}` to `manifest.py`'s `QUERY` and carry it into each record, so the EPEL entries are identifiable for the GPL sidecar. `manifest.py:22` [06 F-SUPPLY-05]
- [ ] Add one `sha256sum -c` on the mirrored provider zip after the COPY; have `manifest.py` read version and lock hash from the committed lock rather than ARGs. [06 F-SUPPLY-06]
- [ ] Change README's three read-only mounts from `:Z` to `:z`; keep `:Z` on `./runs`. `README.md:59-62` [06 F-SUPPLY-07, 07 F-DEC-06]
- [ ] Add an ungated four-line test parsing `ARG VCOWS_VERSION=` out of `Containerfile` against `VERSION`. [09 F-CMT-03, 13, 19 G12]
- [ ] Move `cmd_version`'s manifest block above the `tofu.version()` call, and warn when the file exists but will not parse. `cli.py:50`, `:330-345` [08 F-SILENT-08]
- [ ] Add `isinstance` checks (or one wider `except`) around `entrypoint.install()`'s parse and extraction, so a malformed config reaches `validate`'s report rather than a traceback. `entrypoint.py:88-97` [06 F-SUPPLY-03]
- [ ] Print one stderr line when the entrypoint defers to a pre-existing `~/.ssh/config`. `entrypoint.py:112` [08 F-SILENT-07]

**Exit criteria:** `podman run --entrypoint cat IMAGE /opt/vcows/manifest.json` names
the commit that built it, and a run directory carries a copy.

---

## S9 — Run directory as a secret store `(S)` · depends on S2

- [ ] `os.umask(0o077)` once at the top of `main()` — it covers what tofu writes, which per-file chmods cannot. `cli.py` [13 F-RUNDIR-02]
- [ ] Skip the `chmod` when `stat().st_mode & 0o077 == 0`; on `PermissionError`, say which mode was wanted and why. `cli.py:79` [13 F-RUNDIR-06]
- [ ] Add a README sentence: run directories hold secrets indefinitely and are the operator's to delete, with the `find runs/ -mtime +N -delete` that implements it. [11 F-LIFE-08, 13 F-RUNDIR-07]
- [ ] Add `ServerAliveInterval 30` / `ServerAliveCountMax 6` to `ssh_config`, bounding a wedged tunnel without touching D42. `entrypoint.py` [05 F-DRV-05]
- [ ] Use `Popen` + `wait()` under `try/except KeyboardInterrupt` so Ctrl-C does not SIGKILL tofu 0.25 s in; a second interrupt escalates. `tofu.py:157-164` [05 F-DRV-02]
- [ ] Branch on `planned.changes` being empty versus `add == 0`, naming the stream path in the first case. `cli.py:214` [05 F-DRV-07]
- [ ] Call `traceback.print_exc()` when `VCOWS_TRACEBACK` is set. `cli.py:393-399` [05 F-DRV-08]

---

## S10 — The module `(M)` · depends on S7 (so the changes get pinned)

- [ ] Set `autostart = true`, or record in §3 that it stays off. One line either way, but an operator must know before the first host reboot. `main.tf:84-92` [04 F-TOFU-03]
- [ ] Add `discard = "unmap"` to the overlay disk driver. Do not add `cache`/`io` with it. `main.tf:151-160` [04 F-TOFU-04]
- [ ] Decide `<rng>` and the clock timers — add three lines, or record the omission beside the serial-console reasoning. `main.tf:149-188` [04 F-TOFU-05]
- [ ] Delete `output "base_volume_path"`, or have `parse_outputs` read it and say so in the inventory contract. `outputs.tf:31-34` [04 F-TOFU-06]
- [ ] Rename `inventory.json`'s `address` to `configured_address` in `outputs.tf` and `parse_outputs`, plus a README sentence. It is the config echoed back, never observed. [08 F-SILENT-05]
- [ ] Delete `to_text_field` and `TEXT_FIELD_PREFIX`, or amend `marker.py:56` to say the reader is still owed. [10 F-SEAM-04]

---

## S11 — Core seam `(M)` · depends on nothing

- [ ] Build `by_logical` with an explicit loop collecting a list per name; emit an ERROR when any logical name has more than one holder. `base.py:173` [01 F-CORE-01, 10 F-SEAM-02]
- [ ] Build the clash lookup from every `Existing.name`, not only unmarked ones, and consult it after the `by_logical` miss. `base.py:176`, `:208` [01 F-CORE-02]
- [ ] Change `Discovered`/`Prepared`'s `list`/`dict` fields to `tuple[...]`, matching `Existing.disks`; leave `artifacts` opaque. `base.py:94-118` [01 F-CORE-08]
- [ ] Add a docstring sentence on `Existing.name`: core compares it against the config's logical name, so a backend that transforms names must return the transformed form. `base.py:163-167` [10 F-SEAM-01]
- [ ] Fix `base.module_dir`'s docstring clause — it resolves beside the file defining the class, not `<pkg>/tofu/`. [10]
- [ ] Widen `_stage_module` beyond top-level `*.tf`, or fail loudly when the module directory holds anything it will not copy. `cli.py:244` [10, 19 G10]

---

## S12 — Documentation drift, in one pass `(S)` · do last, after the code settles

Every row of the review's section 4. Batch them — they are all prose, and doing them
before the code lands means doing them twice.

- [ ] `marker.py:63-68` — `deployment` is read by `decide()` and `cmd_destroy` (D36). The highest-cost false comment in the tree. [01, 07, 09, 11]
- [ ] `marker.py:90` — `v` is provenance, not a discriminator; `MARKER_XMLNS` is the real one. [01 F-CORE-06, 11 F-LIFE-06]
- [ ] `findings.md:87` — disk paths are read **at discovery time**, not immediately before undefining. This sentence authorises 2.2. [19 G5]
- [ ] `schema.py:214-230` and `variables.tf:8` — vcows assembles nothing into the URI; credentials travel via `~/.ssh/config`. Fix the *emitted message*, not just the comment. [07 F-DEC-03, 09 F-CMT-02]
- [ ] `cli.py:239-243`, `tofu.py:176-184` — D48 decided against the pre-initialised tree; point at the plugin cache. [06 F-SUPPLY-08, 07 F-DEC-04, 09 F-CMT-05]
- [ ] `main.tf:181-185` — record what the acceptance run found about the pty console. [07 F-DEC-05, 09 F-CMT-04]
- [ ] `orchestrator/__init__.py:4-12` — say where each version consumer is asserted and behind which gate; drop "cannot drift". [09 F-CMT-03]
- [ ] `cli.py:16`, `README.md:75` — argparse exits 2. [05, 16 F-WARN-06]
- [ ] `destroy.py:45-47` — `UNDEFINE_NVRAM` was introduced *in* 1.2.9. [02 F-LVC-08, 09]
- [ ] `findings.md` §3, `base.py:3-4`, `config.py:5-6` — `IMAGE_SCHEMA` is the one core block a second backend must open; 9 touch points across 4 layers. [10 F-SEAM-03, F-SEAM-05]
- [ ] `manifest.py:44`, `Containerfile:57` — 160 packages, 116 source RPMs. Write "roughly". [09 F-CMT-07]
- [ ] `preflight.py:88` — `disks_of` collects file-backed sources only. [09 F-CMT-07]
- [ ] `destroy.py:24-26`, `:176-179`, `:203-214` — three reporting claims. **Free if S2 landed**; correct the prose if it did not. [16 F-WARN-05]
- [ ] `findings.md` §2 — record the `orphan_volumes` bound (config VMs only) and the NVRAM varstore gap. [15 F-XDEP-05, F-XDEP-06]
- [ ] README — cloud-init renames the guest's NICs to `nic0`/`nic1`. [03, 19 G15]
- [ ] `prepare.py:193` — one sentence saying the v6 half is not configured. [03 F-LVOFF-07]

---

## Blocked — needs hardware, not a session

These cannot be closed by reading or by a fix on this machine. Schedule the host.

**The host was scheduled on 2026-08-29.** Six of these nine are now closed and one
is narrowed; each row below carries its own evidence. What survived the session
splits cleanly in two, and neither half is about access:

* **Needs an *old* libvirt.** The rig is Fedora 44 / libvirt 12.0.0, newer than any
  target vcows ships against, so the firmware pin on old libvirt, the raw `.fd`
  varstore, and the flag-shed to `FLOOR` cannot be asked here at any privilege
  level.
* **Needs an artifact or an image nobody has staged.** D3's real golden image, and
  a Rocky 9.0–9.3 cloud image for the old-cloud-init question.

Root on the rig was available and used, so nothing below is waiting on permission.

- [ ] **2.15** `<os firmware='efi'>` beside a pinned loader. **Still blocked, and narrower now.** Run on the rig 2026-08-29: libvirt **12.0.0** honours the pin exactly — `app02` came back with its configured `OVMF_CODE_4M.qcow2` and named template, `secure-boot` and `enrolled-keys` both `no`, while the autoselected `app01` got `OVMF_CODE_4M.secboot.qcow2` with both `yes`. So the construct is not wrong in principle. The open question is unchanged: whether **old** libvirt honours it. Needs a Rocky 8 or Rocky 9 host, or a nested one. `main.tf:109` [04 F-TOFU-01, 17 NEEDS-EVIDENCE]
- [x] **2.12** **Closed 2026-08-29 — it resolves, and the threat was real.** Three applies of a one-resource module against the rig: a bare path, `file:///…` and `http://127.0.0.1:18080/…` all created the volume. The HTTP fetch was served by an http server bound to the *client's* loopback, which the hypervisor cannot reach, so the provider resolves the URL **client-side** — inside the container, over whatever egress it has. S5's `"pattern": "^/"` on `source_qcow2` was closing a real path to the network, not a footgun. Recorded at `config.py`. [18 F-SEC-02, 17]
- [x] **Closed 2026-08-29 — it cannot, so 2.9 does not rise.** Tested in the equivalent shape, since this rig runs no `virtproxyd`: `virtqemud` and its sockets stopped, `virtstoraged` left running. `virtstoraged-sock` stays present and listening and is unreachable anyway — every client enters through `virtqemud-sock`, so the connection never opens. `virsh` on the rig itself gets the same refusal vcows does, before any driver call. The stale-target window `_reverify` closes is a race against another operator, not a driver asymmetry. [14]
- [ ] **D3** — the real golden artifact is still unverified; the acceptance run used the `Rocky-9-GenericCloud-Base` stand-in, and so did the 2026-08-29 rig session. **Blocked on the artifact, not on hardware** — no amount of rig time substitutes for it. [acceptance.md]
- [ ] cloud-init 22.1 / 23.1 on RHEL 9.0–9.3 EUS, and the `sysconfig` renderer path. **Still open and worth scheduling first of what is left**: it is the same class of failure as the acceptance run's defect 5, which was the worst of the five — a guest that boots healthy on an address nobody asked for. Needs a Rocky 9.0–9.3 image, which is a download rather than a hypervisor. [03, 19 G3]
- [x] **Closed 2026-08-29.** The rig gate ran for the first time: **15 passed**. With `VCOWS_GATES=all` plus the rig and image gates supplied, the full suite is **390 passed, 0 skipped** — the first run in which no gate reported itself unlooked-at. [19 G9]
- [x] **Closed 2026-08-29.** `virtqemud` restarted; `vcows-probe02`'s payload came back **byte-identical and un-reindented**, still found by marker rather than by name. That also closes §6 spike item 2's round trip, which the same session exercised through the OpenTofu create path. Host reboot rests on the same persistence and stays inferred. [19 G6]
- [x] **Closed 2026-08-29, and it corrected the README.** `--user 4242` hits **two** walls, not the one README named. First: podman synthesises a passwd entry whose home is `/`, unwritable, and `entrypoint.home()` reads the passwd entry rather than `HOME` — deliberately, because that is what `ssh` does — so `~/.ssh/config` cannot be written and the connection dies with `Host key verification failed`. Setting `HOME` is not a lever. Second, and only then: the 0600 key is owned by the mapped host UID and uid 4242 cannot read it. With `--passwd-entry` giving a writable home and `:U` on the key mount, `preflight` runs clean. A `--run-dir` on a foreign-UID mount stays `0755` and S9's warning names exactly what that costs. [06, 13 F-RUNDIR-06, 19 G11]
- [x] **Half closed 2026-08-29.** Two EFI domains — one autoselected, one with a pinned loader — wrote `app01_VARS.qcow2` and `app02_VARS.qcow2` at define time, and `destroy` removed both, watched at two-second resolution from a root shell. `acceptance.md`'s claim, which had no evidence behind it, is confirmed and `findings.md` §2 corrected. **Still open:** this is the qcow2 template path on libvirt 12.0.0. The raw `.fd` templates Rocky 9 and 10 ship, and what a flag-shed retry to `FLOOR` leaves on 9.0/9.1 EUS, need an old-libvirt target. [11]

## Not scheduled — decisions, not tasks

- [ ] D26: whether `<log file=…/>` replaces the pty console, and who owns the host path it writes to. `acceptance.md` leaves it open. [09, 12, 19 G14]
- [ ] Whether `tests/test_tofu_driver.py` (250 lines, read and mutated by nobody) and the four unread fixtures need an agent of their own. [19 G7, G8]
- [ ] `docs/spikes.md` and `docs/spikes/*` — ~420 lines read by no agent this round. [19 G6]
