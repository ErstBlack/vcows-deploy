# Silent failure — review

Agent: 08-silent-failure · Scope: orchestrator/, container/, tests/, Containerfile, tofu/ · Date: 2026-08-29

## Summary

* Disciplined ground — every libvirt catch in `destroy.py` matches a numeric code,
  `_read_stream` fails closed — with two exceptions, both in `destroy`, and both ending in
  `destroyed N VM(s)`, exit 0.
* **F-SILENT-01:** an unqualified `except libvirt.libvirtError` around
  `lookupByUUIDString` marks a live domain "skipped" and deletes its disks.
  **F-SILENT-02:** a disk in an **inactive** pool never resolves, `NO_STORAGE_VOL` reads
  as "already gone", and the volumes leak silently. Both reproduced.
* On deploy, the residue of acceptance defect 5 remains — `inventory.json` states an
  address the tool never observed — and `parse_outputs`' chained `.get` turns a renamed
  module output into `created 0 VM(s)` with `outcome: ok`.

## Findings

### F-SILENT-01 — a non-`NO_DOMAIN` lookup failure deletes a live VM's disks and reports success
- **Severity:** S1 · **Confidence:** high (reproduced; trigger not seen on the rig)
- **Location:** `orchestrator/backends/libvirt/destroy.py:243-255`
- **What:** `except libvirt.libvirtError: out.skipped.append(target.name)` catches *any*
  error from `lookupByUUIDString`, comments it "Already gone", then falls through to `for
  path in target.disks: _delete_volume(...)`. Nothing fatal is appended, so `destroy()`
  returns normally. Only `VIR_ERR_NO_DOMAIN` (42) means gone; `ACCESS_DENIED`,
  `INVALID_CONN` (the daemon restarted mid-teardown — `virtqemud` is socket-activated with
  an idle timeout on the RHEL targets), `SYSTEM_ERROR` and `INTERNAL_ERROR` all take the
  same branch.
- **Why it matters here:** the file's own docstring says `vol.delete` "offers no
  protection whatsoever ... libvirt will delete a running VM's disk without complaint".
  The domain stays defined and running while its overlay and seed ISO are unlinked
  underneath it; the operator sees `destroyed 1 VM(s)`, exit 0.
- **Evidence:** `lookupByUUIDString` raising 45 rather than 42 against `fake_libvirt`:
  `destroy() returned normally`, `domain still running: True`, `calls: []`, `volumes
  deleted: ['app01.qcow2']`.
- **Fix / cost:** add `ERR_NO_DOMAIN = 42` beside the three constants already there and
  append a fatal `Problem` for any other code; the disk loop must not run for a target
  whose domain could not be resolved. One constant and one `if` — no new surface, it makes
  this catch match the three above it.

### F-SILENT-02 — a disk in an inactive pool is leaked and counted as torn down
- **Severity:** S2 · **Confidence:** high on the mechanism, medium on the trigger
- **Location:** `orchestrator/backends/libvirt/destroy.py:203-231`, `:186-191`
- **What:** `_refresh_pools` does `if not pool.isActive(): continue`. A disk in an
  inactive pool never enters libvirt's cache, `storageVolLookupByPath` returns
  `NO_STORAGE_VOL`, and `_delete_volume` files it under `out.skipped` — whose comment
  reads "After a refresh this genuinely means gone". That refresh never ran for that pool;
  one that *fails* is likewise only a WARNING.
- **Why it matters here:** the domain is destroyed and undefined, so the marker — the only
  durable record — is gone while the overlay and seed ISO remain, and in a pool other than
  the config's no future preflight walks them either. `out.skipped` is never printed, so
  the operator sees only `destroyed 1 VM(s)`. An inactive pool is not exotic: no autostart
  after a host reboot, an unmounted NFS export.
- **Evidence:** same harness, disks in `FakePool(..., active=False)` — destroy returned
  normally, `pool refreshed: 0`, `volumes deleted: []`.
- **Fix / cost:** record an ERROR `Problem` per pool skipped as inactive instead of
  `continue`; `_delete_volume` can then keep reading `NO_STORAGE_VOL` as success, because
  the precondition its comment claims becomes enforced. One append. It makes a disk in a
  dormant pool a hard destroy failure — correct, since the alternative is deleting the
  marker and leaking the file.

### F-SILENT-03 — a renamed module output yields "created 0 VM(s)" and `outcome: ok`
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/__init__.py:91`,
  `orchestrator/cli.py:224-234`
- **What:** `Inventory(vms=raw.get("vms", {}).get("value", {}))` cannot fail. Any output
  not carrying exactly `vms.value` — a renamed output, an `outputs.tf` edit, empty stdout
  — yields an empty inventory. `cmd_deploy` then writes `inventory.json` as `{"vms": {}}`,
  records `outcome: ok` with `created: [app01, app02]`, prints `created 0 VM(s)` and exits
  0, *after* the apply really created them — so the run's two artifacts contradict each
  other.
- **Why it matters here:** this is the seam `parse_outputs` protects (`outputs.tf:1` —
  "This block is NOT the public API") and nothing pins the ends together: no test compares
  `outputs.tf`'s names against it, and the module gates are plan-only.
- **Evidence:** `parse_outputs({'virtual_machines': {'value': {'a': 1}}})` →
  `Inventory(vms={})`; likewise for `{}`.
- **Fix / cost:** raise if `vms` is absent from `raw` (present-but-empty stays legal), or
  assert `len(inventory.vms) == len(creating)` before recording. Two lines: the count in
  the success message is a claim about the hypervisor.

### F-SILENT-04 — `DHCPLeases()` failure silently downgrades the address-conflict check
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:376-382`
- **What:** `except libvirt.libvirtError: pass`. The comment names two benign causes (no
  DHCP, network not running) but the catch is unqualified; `INTERNAL_ERROR` from an
  unreadable `/var/lib/libvirt/dnsmasq/*.leases` and `NO_SUPPORT` take the same path —
  the one libvirt catch in this codebase that matches no error code.
- **Why it matters here:** the check narrows from "reservations plus active leases" to
  "reservations only" and preflight reports clean. The deploy then puts a static address
  on a NIC a live guest holds a lease for; both work intermittently, and duplicate IP is
  among the hardest faults to diagnose at an air-gapped site.
- **Fix / cost:** match `OPERATION_INVALID`/`NO_SUPPORT` and keep the `pass`; WARN, naming
  the network, for anything else. Two constants and a branch, in a function already
  returning `list[Problem]`.

### F-SILENT-05 — `inventory.json` states an address the tool never observed
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/outputs.tf:17`, `README.md:145`
- **What:** the inventory's `address` is `var.vms[key].address` — the config's own
  `ip_cidr` echoed back, never round-tripped through libvirt. `render.py:99-101` says so;
  nothing downstream repeats it, and the README says "name -> address".
- **Why it matters here:** this is what let acceptance defect 5 pass. The netplan idiom is
  fixed; the reporting property that hid it is not. Any future failure of cloud-init to
  apply `network-config` (an older cloud-init on RHEL 9, a MAC match that does not fire)
  gives a healthy guest on a DHCP address and an inventory asserting the configured one.
  `docs/archive/acceptance.md:108` — "nothing short of checking the address would have caught it".
  Nothing does.
- **Fix / cost:** name it honestly — `configured_address` in `outputs.tf` and
  `parse_outputs`, plus a README sentence: a rename and one line. The check that would
  actually catch it (after apply, a DHCP lease on one of our derived MACs is the
  fingerprint of the fallback) needs a second connected phase and breaks "preflight is the
  only connected method", so it is not recommended.

### F-SILENT-06 — a volume whose XML will not parse vanishes from preflight with no `Problem`
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:235-242`
- **What / why it matters here:** `walk()` swallows `libvirtError` and `ET.ParseError` per
  volume and `continue`s, with no `Problem` channel at all — unlike `open_pool` and
  `base_volume` beside it. The comment justifies the skip as "a volume that vanished
  between listing and describing", but the branch absorbs a describe that failed for any
  reason, and the volume is then invisible to the orphan refusal, the base-volume lookup
  and D30's size comparison. An orphaned overlay goes unreported and the apply dies
  mid-create on a raw provider error — the exact failure §2's orphan refusal pre-empts. S3
  because what is lost is the diagnosis, not the safety.
- **Fix / cost:** give `walk` the `-> tuple[dict, list[Problem]]` shape its neighbours
  have and emit one WARNING. A signature change and one call site.

### F-SILENT-07 — the entrypoint defers to a pre-existing `~/.ssh/config` without saying so
- **Severity:** S3 · **Confidence:** high on the path, low on the trigger
- **Location:** `container/entrypoint.py:112-114`
- **What / why it matters here:** `if destination.exists(): return` — "Theirs wins",
  silently, so the config's `ssh_keyfile` and `known_hosts` are ignored with no output,
  where the sibling failure at `:103-107` does print. The symptom is `Host key
  verification failed` with a `known_hosts` sitting in the config — the S2 the acceptance
  run spent a day on — and this function holds both halves of the explanation. S3 not S2:
  the documented `podman run` mounts individual files, so the trigger needs an operator
  who mounts `~/.ssh`.
- **Fix / cost:** one stderr line naming the file that won and the keys ignored. Three
  lines, in a file that already prints two diagnostics.

### F-SILENT-08 — an unreadable build manifest is indistinguishable from a source checkout
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:50-53`, `:330-345`
- **What / why it matters here:** `manifest()` returns `None` on both `OSError` and
  `JSONDecodeError` and the build block prints only when it is not None, so a truncated
  `/opt/vcows/manifest.json` inside the image produces exactly the output of a developer
  checkout, where absence is correct and documented — while `Containerfile:119` sets
  `VCOWS_MANIFEST` to that path, so inside the image absence is always a fault.
  Separately, a failure from `tofu.version()` prints "tofu: unavailable" and `return 0`
  *before* the manifest is read, so an unrelated fault suppresses R5's provenance too.
- **Fix / cost:** move the manifest block above the `tofu.version()` call and warn when
  the file exists but will not parse. A reorder and one branch.

### F-SILENT-09 — a module test's error check goes vacuous if OpenTofu rewords "Error:"
- **Severity:** S3 · **Confidence:** medium
- **Location:** `tests/test_tofu_module.py:66-69`, used at `:103`, `:127`
- **What / why it matters here:** `diagnostics()` returns output only when it contains the
  literal `"Error:"` or `"undeclared variable"`, and two tests assert `diagnostics(...) ==
  ""` — text is read at all because `tofu console` reports problems and still exits 0.
  Reword that prefix upstream and the two tests proving the module accepts the golden
  tfvars pass against a module that errors. `tofu.py`'s docstring argues exactly this
  against parsing tofu's human output ("free to reword it in any release"); the tests do
  not follow it.
- **Fix / cost:** assert `returncode == 0` alongside, or use `validate -json` for the
  positive cases. One added assertion.

## Checked and sound

* `tofu._read_stream` fails safe: a missing or truncated stream gives `changes == {}`, so
  `cmd_deploy`'s `if not planned.changes.get("add")` refuses rather than applying blind.
  Exit code stays the authority on success.
* `marker_of` reading a damaged marker as *unmarked* has a real second line of defence:
  such a domain stays out of preflight's `ours` set, so its MACs stay in `by_mac` and
  `address_conflicts` refuses on the derived MAC even when it was renamed too and the
  name-clash check cannot fire.
* `open_pool` makes a missing or inactive pool an ERROR, so the only path where
  `Discovered.artifacts` lacks `base_volume` is one deploy already refused —
  `LibvirtBackend.prepare`'s unguarded subscript is unreachable today.
* `_undefine`'s retry is bounded to one attempt and sheds only bits above `FLOOR`;
  `_delete_volume` has no `os.unlink` fallback; `_stop` re-checks `isActive()` before
  accepting `OPERATION_INVALID`.
* Reusing a `--run-dir` fails loudly (`seed.mkdir()`/`workdir.mkdir()` have no `exist_ok`)
  rather than planning against stale state, and `entrypoint.install`'s swallow of a broken
  config YAML is correct.
* The opt-in test gates skip with explicit reasons and none passes vacuously;
  `container/tofurc` has no `direct` block. `210 passed, 25 skipped` offline.

## Not checked

* The live rig: no libvirt calls against it. Settling F-SILENT-01's trigger means watching
  a `virtqemud` idle-timeout restart land between preflight and the destroy loop. Also
  unrun: the `VCOWS_IMAGE` and tofu module gates.

## Deserves its own agent

* **The run directory as a record.** `run.json` records `created` from the decisions while
  the success line counts the inventory, and neither is reconciled against the hypervisor.
  Whether they can disagree in a way an operator reads as success wants its own pass.
