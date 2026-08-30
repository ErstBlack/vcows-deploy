# Dimension E — test teeth

Agent: E · Scope: the ~2,300 new test lines in `da3f45c..HEAD` · Date: 2026-08-30
Method: run the suite, then mutate production code in scratch copies and count
what the suite notices. 53 `main.tf`/`outputs.tf` mutations against
`tofu test`, 29 Python mutations against `pytest`.

## Summary

The remediation closed almost everything 12-test-teeth found, and closed it
properly rather than decoratively.

* **`libvirt-module.tftest.hcl` works.** 36 of 53 module mutations are caught,
  including all twelve the last review recorded as surviving. `mock_provider`
  reaches the computed attributes, so `.path` → `.name` on the disk source is
  caught, which is the assertion that needed the mock to exist.
* **The new Python tests have real teeth.** 16 of 29 Python mutations caught,
  and every survivor is low-consequence except the three in `conftest.py`. Both
  files the last review called uncovered — `container/entrypoint.py` and
  `container/manifest.py` — now catch every mutation of the behaviour they were
  written for, including the `\Z`→`$` regex weakening in `SSH_PATH` that
  re-opens the newline-injection hole.
* **`VCOWS_GATES=all` genuinely works today.** Verified: `379 passed, 25
  skipped` becomes `379 passed, 25 errors`, all 25 named, exit non-zero.
* **`fake_libvirt.py` did not regress.** `storageVolLookupByPath` now keys on
  the whole path (`fake_libvirt.py:235`), every error code matches the installed
  binding (`errors.py` vs `libvirt.VIR_ERR_*`, checked), and no new method
  models something libvirt does not do. Nothing to file here.
* **The seed-ISO reproducibility test was fixed the honest way** — it now
  compares the three files rather than the bytes, and says why
  (`test_seed_iso.py:81-95`).

What is left is five gaps, not five broken tests: three attribute classes the
module gate does not read, and the gate mechanism's own failure path, which no
test exercises.

---

## 1. The gate mechanism — RW-E1, RW-E2

### RW-E1 — nothing tests that a demanded gate actually fails

`conftest.gate()` has two branches (`conftest.py:53-58`): available → a no-op
`skipif`, unavailable → `gate_missing` if demanded else `skip`. `require()` has
the same shape (`conftest.py:61-67`). `pytest_runtest_setup` (`conftest.py:76`)
turns `gate_missing` into a failure.

**Only the first branch ever runs in any CI configuration.** `just test-tofu`
sets `VCOWS_GATES=tofu` on a runner where `tofu` and the mirror are present, so
`gate("tofu", True, …)` is what is evaluated. The demanded-and-absent branch is
by construction never taken on a green run.

Mutating it out is invisible:

```
# gate() -> always pytest.mark.skip ; require() -> always pytest.skip
default                     369 passed, 35 skipped   exit 0
VCOWS_GATES=tofu            369 passed, 35 skipped   exit 0   <- what `just test-tofu` runs
VCOWS_GATES=all             369 passed, 35 skipped   exit 0
```

(35 rather than 25 because the scratch copy has no `.tools/tofu-mirror`; the
point is the exit code and that `VCOWS_GATES=all` stopped meaning anything.)

`test_gates.py` tests `demanded()` — the predicate — and AST-scans for stray
skips. It never calls `gate()` or `require()` with `available=False`. So the
mechanism the whole file exists to protect is the one thing it does not touch,
and F-TEETH-05's shape ("a gate that quietly passes because it did not run")
comes back one level up.

Cheap to close: `gate("tofu", False, "r").mark.name == "gate_missing"` under a
monkeypatched `GATES`, `"skip"` without, and a `pytester` case for
`pytest_runtest_setup`. ~12 lines.

### RW-E2 — the skip scanner's `banned` set misses the common idioms

`test_gates.py:55`:

```python
banned = {"pytest.skip", "pytest.importorskip", "pytest.mark.skip"}
```

Confirmed against the same `_dotted`/`_calls` walk the test uses:

| written in a test file | `_dotted` yields | flagged? |
|---|---|---|
| `@pytest.mark.skipif(cond, reason=…)` | `pytest.mark.skipif` | **no** |
| `@pytest.mark.skip` (bare, no call) | not a `Call` at all | **no** |
| `@pytest.mark.xfail` / `pytest.xfail(…)` | not in `banned` | **no** |
| `pytest.skip(…)` | `pytest.skip` | yes |

`skipif` is the idiom `gate()` itself returns (`conftest.py:53`), so it is the
form a developer copying the house style is most likely to write, and it is
exactly the form the guard cannot see. `xfail` is worse than a skip: a
non-strict `xfail` reports success for a test that fails.

Nothing in the suite uses any of these today — the finding is that the guard
does not stop the next one. Fix is the literal set plus checking decorator
`ast.Attribute` nodes, not only `ast.Call`.

---

## 2. The module gate — RW-E3, RW-E4, RW-E5

`tofu test` with `mock_provider` was the right call and it earns its keep. Full
sweep, in a scratch copy of the module initialised exactly as the `mocked`
fixture does it (`tofu test -no-color`, baseline `1 passed, 0 failed`):

**Caught (36).** drop `metadata`; drop `backing_store`; drop
`capacity`/`capacity_unit`; `capacity_unit` → `KiB`; seed `iso`→`raw`; base
`qcow2`→`raw`; overlay `qcow2`→`raw`; backing-store format → `raw`; drop
`features`; hardcode nvram `.fd`; `running = false`; `boot_devices` `hd`→`cdrom`;
drop `serials`/`consoles`; cdrom `read_only = false`; cdrom driver
`raw`→`qcow2`; `sda/sata` → `hdc/ide`; `vda/virtio` → `hda/ide`; disk source
`.path`→`.name`; seed source `.path`→`.name`; `host-passthrough`→`host-model`;
`autostart = false`; drop `discard`; drop `rngs`; rng backend
`/dev/urandom`→`/dev/random`; `clock.offset` → `localtime`; hpet `present`
`no`→`yes`; timer order; `loader_readonly` string→boolean; drop nic `mac`;
nvram `template` wrong; base `create.content.url` wrong; seed
`create.content.url` wrong; `local.base_path` ignoring the created base; output
`configured_address` wrong; output re-adding `address`; and `main.tf` deleted
`metadata` (the in-suite teeth test).

**Survived (17).**

| Mutation | What it means in production |
|---|---|
| drop `depends_on = [libvirt_volume.base]` | documented as uncatchable in the file's own header; not a finding |
| `driver.type` `qcow2`→`raw` on the root disk | **RW-E3** |
| overlay `name` → `each.key`; seed `name` → `each.key` | **RW-E4** |
| `vcpu`, `memory`, `memory_unit`, `type_machine`, `type_arch`, `os.type`, `pool`, `os.loader`, nic `model`, nic `source.network` all replaced by constants | **RW-E5** |
| domain `name` → `each.key` | equivalent on this data (`domain_name == each.key` in the golden) |
| output `disks` dropping the seed path | the record `outputs.tf:26-29` says a teardown can reconcile against; destroy does not read it |
| `tick_policy` dropped from the rtc timer | timer *names* are pinned, policies are not |

### RW-E3 — the cdrom's driver type is pinned, the root disk's is not

`tests/libvirt-module.tftest.hcl:145-148` asserts
`devices.disks[1].driver.type == "raw"` for the seed cdrom, and
`:167-170` asserts `devices.disks[0].driver.discard == "unmap"` for the root
disk — but never `disks[0].driver.type`. So:

```hcl
driver = { name = "qemu", type = "qcow2", discard = "unmap" }
->      { name = "qemu", type = "raw",   discard = "unmap" }
```

passes `tofu test`, `tofu validate` and the whole Python suite. qemu then
presents the qcow2 container — header, L1 table and all — to the guest as its
raw root disk. Every VM in the deployment fails to boot, after a run that
reported success and an inventory that looks correct. The volume's *own* format
is asserted (`:53-56`), which is what makes the omission read as an oversight
rather than a decision: the two have to agree and only one of them is checked.

One line, beside the `discard` assertion.

### RW-E4 — the two volume names destroy matches on are unpinned

`:31-34` pins the domain name against `var.vms["app01"].domain_name`. Nothing
pins `libvirt_volume.overlay["app01"].name` against
`var.vms["app01"].overlay_name`, or the seed's against `seed_name`. Both
survive being replaced with `each.key`.

The domain name is the one of the three that destroy does *not* use — discovery
is by marker and UUID (`destroy.py:516`, `preflight.py:174-181`). The two that
are unpinned are the only per-disk guard there is:

```python
# destroy.py:456-461
owned = {overlay_name(target.marker.name), seed_name(target.marker.name)} …
if PurePosixPath(path).name not in owned:
```

A module that names the overlay `app01` instead of `app01.qcow2` deploys
cleanly, and then every teardown refuses every disk with "is not one of the
names this VM owns" and raises `DestroyError` — reported, so not silent, but
unrecoverable without hand-deleting files on the hypervisor. `orphan_volumes`
(`preflight.py:417`) looks up the same two names and would also stop seeing
them.

Two lines, beside the domain-name assertion.

### RW-E5 — of the config values that reach the domain, only `disk_bytes` is asserted

`:45-48` pins `overlay.capacity == var.vms["app01"].disk_bytes`. Nothing pins
the rest of the same class. All of these pass:

| in `main.tf` | mutated to | consequence |
|---|---|---|
| `vcpu = each.value.vcpus` | `1` | every VM gets one CPU |
| `memory = each.value.memory_mib` | `512` | every VM gets 512 MiB |
| `memory_unit = "MiB"` | `"KiB"` | 4096 KiB; the domain will not start |
| `type_machine = each.value.machine` | `"pc"` | q35 config on i440fx |
| `type_arch = "x86_64"` | `"i686"` | wrong arch for the golden image |
| `pool = var.pool` | `"default"` | every volume created in a pool the config did not name and preflight never checked |
| `loader = each.value.loader` | `null` | app02's explicitly pinned OVMF path is dropped and libvirt picks its own — the exact branch `loader`/`loader_format` exist for, given RHEL ships a raw `.fd` elsewhere |
| `mac = { address = n.mac }`… `source.network` | `null` | NIC attached to no network |
| `model = { type = n.model }` | `"e1000"` | config's virtio ignored |

The firmware branch is well covered (`nv_ram`, `template`, `loader_readonly`,
`firmware` — four assertions), which makes `loader` itself being unread the
odd one out. `pool` and `memory_unit` are the two worth closing first: the
first puts files on somebody else's hypervisor in a place neither preflight nor
`_refresh_pools`' fatal check was aiming at, the second is a one-token edit with
a total failure.

A `for`-comprehension over `var.vms` asserting the five scalar passthroughs
would cover most of this in about eight lines and would also cover app02, which
today is read by four assertions out of thirty-eight.

---

## 3. Smaller — RW-E6, RW-E7, RW-E8

### RW-E6 — `test_gates_is_parsed_without_whitespace_stripping` asserts nothing

`test_gates.py:99-103`. The docstring documents that `VCOWS_GATES` splits on
`,` without stripping, so `"tofu, image"` demands `tofu` and a gate named
`" image"` that does not exist, and says both CI files are written without
spaces because of it. The body is:

```python
assert isinstance(GATES, set)
```

`GATES` is a set comprehension (`conftest.py:37`); this cannot fail. If someone
"fixes" `demanded()` to strip, the test still passes and the reason the CI files
avoid spaces goes stale with nothing to notice. `assert demanded(" tofu") is
False` — or the opposite, if stripping is wanted — is the assertion the
docstring describes.

### RW-E7 — the no-timeout-on-apply decision (D42) is unpinned

`tofu.py:176`:

```python
proc.wait(timeout=SHORT_TIMEOUT if cmd == "init" else None)
```

Replacing that with `timeout=SHORT_TIMEOUT` passes the entire suite. The
`except BaseException` below then kills tofu 120 s into an apply — in the middle
of the multi-GB `vol-upload` that has no resume, which is the specific thing
`SHORT_TIMEOUT`'s docstring says must never be put on a clock.

`test_tofu_driver.py` pins thirteen properties of the child invocation (flags,
env, stream-vs-exit-code authority, both Ctrl-C paths). This is the fourteenth
and it is the one with the worst failure. `Stubborn.wait` already records its
`timeout` argument's absence for free — assert `wait` was called with
`timeout=None` for `apply` and `120` for `init`, ~6 lines.

Also survived, and not filed: `workdir.resolve()` removed from `_run` (every
test passes an absolute `tmp_path`); `_read_stream`'s missing-file branch
(the fake always writes the file, so only the *truncated* case is covered);
`ssh_dir.mkdir(mode=0o700)` → `0o777` (the written file's `0o600` is asserted,
the directory's mode is not); `install()` writing a credential-less config when
neither `ssh_keyfile` nor `known_hosts` is set.

### RW-E8 — a property test whose property is false

`test_properties.py:61-71`:

```python
assert (first.id == second.id) == (a == b)
```

`derive_id` is `uuid5(VCOWS_NS, f"{deployment}/{name}")` (`marker.py:168`), so
the separator is ambiguous:

```
Marker.for_vm("b/c", "a").id  == Marker.for_vm("c", "a/b").id
099c2014-c2b5-5886-ad57-47f66be02f86  (both)
```

`a != b`, ids equal, so the universal the test states does not hold over
`st.text()`. Hypothesis will not construct that structured pair by chance, so it
passes — a test quantified over all strings that only ever sees the inputs where
the claim is true.

Not reachable from a validated config: `DEPLOYMENT_PATTERN` (`config.py:39`) and
`NAME_PATTERN` (`schema.py:44`) both exclude `/`. So this is a test-scope
defect, not a production one — either bound the strategy to the two patterns
(which also makes the test say what it means) or use a separator neither pattern
admits.

---

## Checked and sound — nothing to file

* **`fake_libvirt.py`, all +87 lines.** The `*_error` attributes are raised at
  the top of the method they belong to and nowhere else; `FakePool.XMLDesc`
  returns a real `<pool type='dir'>` shape that `_pool_holds` parses the same
  way it parses libvirt's; `storageVolLookupByPath` matches
  `f"{pool._path}/{name}"` in full and only over `pool.visible`, which is the
  post-refresh cache semantic the D35 tests depend on; `FakeVolume.delete`
  asserts `flags == 0`, matching `virCheckFlags(0, -1)` in the dir/fs backend.
  Every error code the fake raises equals the installed binding's constant (42
  `NO_DOMAIN`, 43 `NO_NETWORK`, 49 `NO_STORAGE_POOL`, 50 `NO_STORAGE_VOL`, 55
  `OPERATION_INVALID`, 8 `INVALID_ARG`), and `errors.py` agrees.
  The one divergence: `XMLDesc(_flags)` ignores its flag on all four fakes, so
  nothing would notice `VIR_DOMAIN_XML_INACTIVE` being dropped. Traced it —
  `disks_of` reads only `devices/disk/source`, never a nested `<backingStore>`,
  so live-vs-inactive XML yields the same disks and the same marker, and a
  transient domain (the one case where the flag errors) is already handled by
  the `libvirtError` catch in `_domains`. No bug behind it; not filed.
* **`VCOWS_GATES=all` covers every skip.** All 25 skips route through
  `gate()`/`require()`; `VCOWS_GATES=all` turns all 25 into named errors. No
  `pytest.skip`, `importorskip` or `skipif` exists outside `conftest.py`, and
  every `gate`/`require` name is in `KNOWN`. The scan's non-recursive
  `TESTS.glob("*.py")` is complete today — every test module is directly under
  `tests/`.
* **`test_entrypoint.py`** catches seven of the eleven mutations aimed at it, including
  `SSH_PATH`'s `\Z`→`$` (which re-admits `"/run/secrets/k\n"` and with it the
  appended-directive injection), `StrictHostKeyChecking yes`→`no`, dropping
  `IdentitiesOnly`, `0o600`→`0o644`, and overwriting a mounted `~/.ssh/config`.
  The refuse-before-write ordering is pinned on the file's absence, which is the
  right assertion.
* **`test_manifest.py`** catches the mutations that matter: `git_sha` returning
  an unvalidated value, `PROVIDER_SHA256` ignored, and `or`→`and` in the
  lock-parse refusal. The `\Z`→`$` weakening of `SHA_PATTERN` survives (a
  trailing newline would be recorded verbatim) — noted, not filed; `$(git
  rev-parse HEAD)` cannot produce one.
* **`test_tofu_driver.py`** is the strongest of the new files: six of the nine
  driver mutations were caught, including dropping `-json-into`, adding
  `-auto-approve`, hardcoding diagnostic severity, treating a non-zero exit as
  success, and both halves of the Ctrl-C behaviour. Faking the binary rather
  than gating on a real one is the right trade and it is not a mock asserting
  its own return: the fake records argv and env to a file the test reads back.
* **`test_tofu_module.py`'s `test_the_gate_has_teeth`** is a real teeth test —
  four tfvars mutations, each with a distinct expected diagnostic, and
  `diagnostics()` is not vacuous because `test_golden_tfvars_satisfy_the_variable_types`
  checks `returncode == 0` alongside `diagnostics(r) == ""`.
* **`tofu_env` now reads `container/tofurc`** and substitutes the mirror path,
  with an assertion that the substitution found something (`conftest.py:117`).
  F-TEETH-06 closed.
* **`docs/spikes.md` and `docs/spikes/*`** are a record of four measurements
  (A1–A4), not tests, and are not claimed to be. A4's `vol-upload` result is
  what `main.tf:37-39` cites for putting `capacity` on the overlay only, and
  A2's namespace disagreement is what `preflight.py:54-56` cites. Both citations
  are accurate against the recorded output.

## Not checked

* `tests/test_libvirt_rig.py` and `tests/test_image.py` bodies were read but not
  executed — no rig, no built image.
* Mutation coverage of `cli.py`, `destroy.py`, `preflight.py`, `schema.py` and
  `render.py` beyond what the module and driver sweeps touched. The last review
  covered those and its "Checked and sound" list still holds against HEAD; I did
  not re-sweep them.
* `tests/test_cli.py` (+376), `tests/test_libvirt_destroy.py` (+334) and
  `tests/test_libvirt_preflight.py` (+266) were read for vacuous assertions and
  none were found, but they were not mutation-tested.
* `.github/`, `.gitlab-ci.yml`, `justfile` and `scripts/` — dimension G.
