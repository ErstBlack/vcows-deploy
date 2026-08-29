# The OpenTofu module — review

Agent: 04-tofu-module · Scope: `orchestrator/backends/libvirt/tofu/*.tf` · Date: 2026-08-29

## Summary

* The "module emits no XML libvirt needs" class is **mostly closed**. The `vcows-probe02`
  fixture is a minimal define against this rig and proves libvirt supplies `<clock
  offset='utc'/>`, `on_poweroff`/`on_reboot`/`on_crash`, the emulator, controllers, inputs,
  audio, watchdog and `<memballoon model='virtio'/>` itself. Only `<rng>` and the clock timers
  are absent.
* The one real member of that class left is the **firmware element**: the module emits `<os
  firmware='efi'>` *and* an explicit `<loader>`/`<nvram>` together. Fedora 44's libvirt honoured
  the pin; on the older libvirt Rocky/RHEL 9 ships, autoselection fills loader and nvram itself
  — on exactly the hosts needing it.
* `for_each` keys, the `count` guard, D31's `depends_on` and D23's fresh state are all sound,
  and D23 is guaranteed **by construction**, not convention. `outputs.tf` cannot produce a null
  or unknown map, but half of it has no reader.

## Findings

### F-TOFU-01 — `firmware = "efi"` is emitted together with a pinned loader and NVRAM

- **Severity:** S2
- **Confidence:** medium
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:109-132`
- **What:** `firmware = each.value.firmware == "efi" ? "efi" : null` is unconditional, so when
  the operator pins `loader`/`loader_format`/`nvram_template` the module writes `<os
  firmware='efi'>` *and* `<loader>` and `<nvram>`. These are two alternative mechanisms: with
  `firmware` set, libvirt runs autoselection and fills loader and nvram from the descriptor it
  picks. Treating a supplied loader path as a *filter* on that selection is a later addition; on
  the libvirt Rocky/RHEL 9 ships the pin may not survive.
- **Why it matters here:** RHEL 9 is precisely the case the pin exists for — raw `.fd` OVMF
  where Fedora ships qcow2, different paths, and on early 9.x possibly no usable descriptors.
  Two outcomes: the define fails with "Unable to find any firmware to satisfy 'efi'", pointing
  at nothing the operator wrote; or the domain is built on a firmware and NVRAM template the
  operator did not choose — accepted, not honoured, and `_VARS.<fmt>` seeded from the wrong
  varstore. Related: on the pinned path libvirt does not add `<smm state='on'/>`, which
  autoselection does (`tests/fixtures/libvirt/domain-unmarked-running.xml:25`), so a pinned
  `*.secboot.*` loader gets Secure Boot firmware with no SMM.
- **Evidence:** `docs/acceptance.md:137-139` — "With no `loader` configured, libvirt selected
  `OVMF_CODE_4M.secboot.qcow2` ... The pinned-loader VM got the non-secboot build." That is
  libvirt 12.0.0 filtering by path. `schema.py:278` already treats `loader` + `nvram_template`
  as an all-or-nothing pair, so the module knows which of the two modes it is in and does not
  use the knowledge.
- **Fix / cost:** make the ternary exclusive — `firmware = each.value.loader == null &&
  each.value.firmware == "efi" ? "efi" : null` — so the manual path stands on
  `loader`/`loader_type`/`loader_readonly`/`nv_ram` alone. One expression, no new variable or
  config field. To settle it first: define the module's XML for `app02` on a Rocky 9 host and
  diff `virsh dumpxml` against the tfvars. If old libvirt honours the pin, this drops to S5 —
  redundant attribute.

### F-TOFU-02 — `base_volume_name` colliding with a derived volume name makes preflight tell the operator to delete the golden image

- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:40-53` (D16 naming),
  `orchestrator/config.py:47`, `orchestrator/backends/libvirt/preflight.py:328-330`
- **What:** D16 derives volume names from the logical name — `app01.qcow2`, `app01-seed.iso`. No
  two VMs can collide, but `image.base_volume_name` is validated only as `{"type": "string",
  "minLength": 1}` and is never compared against them. Set it to `app01.qcow2` alongside a VM
  named `app01` and `orphan_volumes` sees a pool volume matching `overlay_name("app01")` that no
  domain claims — because the base never is claimed.
- **Why it matters here:** the refusal message is `volume 'app01.qcow2' exists but no domain
  references it. A previous create was interrupted; delete it on the hypervisor and re-run.` An
  operator who follows it deletes the shared golden image every other deployment's overlays back
  onto. `<backingStore>` protection lives in destroy; this routes around it via a human.
- **Evidence:** `preflight.py:326-330` iterates `overlay_name`/`seed_name` against the pool
  listing with `claimed` built only from domain disks (`:444`). Nothing compares
  `base_volume_name` against either.
- **Fix / cost:** one semantic check refusing a config whose `base_volume_name` equals any
  `overlay_name(vm)` or `seed_name(vm)` — about six lines in the existing offline block, no new
  schema field. Justified because the failure is not the collision but the destructive advice it
  produces.

### F-TOFU-03 — no `autostart`, and no record that this was a choice

- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:84-92`
- **What:** `libvirt_domain` exposes `autostart` (bool, optional). The module never sets it, so
  every domain vcows defines has autostart off.
- **Why it matters here:** after a hypervisor reboot or power event at an air-gapped site, every
  VM vcows deployed stays down. `preflight` calls `conn.listAllDomains(0)`, which returns
  inactive domains too, so `decide()` still says "ours, skip" and a re-run prints `nothing to
  create` and exits 0 with every guest powered off. There is no `start` verb; recovery is `virsh
  start` per VM by hand.
- **Evidence:** `grep -rn -i autostart` over the repo returns only the provider schema. Neither
  §5 ("Cut from v0.1") nor §3 ("Explicitly not built") mentions it, so this is an omission
  rather than a recorded cut.
- **Fix / cost:** `autostart = true` on `libvirt_domain.vm` — one line, no variable. A per-VM
  config field would be new schema surface on a one-way door (F11) and is not warranted. If it
  stays off, record it in §3; an operator needs to know before the first host reboot, not after.

### F-TOFU-04 — the overlay disk has no `discard`, so a 40 GiB overlay only ever grows

- **Severity:** S3
- **Confidence:** medium
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:151-160`
- **What:** the disk driver is `{ name = "qemu", type = "qcow2" }` and nothing else. Without
  `discard = "unmap"`, guest `fstrim` and deletes never return blocks to the qcow2, so each
  overlay ratchets toward its declared `disk_gb`.
- **Why it matters here:** `disk_gb: 40` against a 10 GiB golden image is the documented shape
  (`tests/golden/libvirt.tfvars.json`), the pool belongs to someone else (D29), and there is no
  prune — §2's "the base image is never cleaned up" covers the base, not the overlays. It lands
  months later as a full pool on a host vcows does not manage.
- **Evidence:** the provider's disk `driver` exposes `discard`, `discard_no_unref` and
  `detect_zeros`; none appear in `main.tf`.
- **Fix / cost:** `discard = "unmap"` on the existing driver object — one attribute. virtio-blk
  discard needs QEMU 4.0+; RHEL 9 ships 7.2 and RHEL 10 ships 9.x. Do not add `cache`/`io` with
  it; no failure is attached to those.

### F-TOFU-05 — `<rng>` and the clock timers are the only devices libvirt does not default, and neither was decided

- **Severity:** S3
- **Confidence:** high on the fact, low on the impact
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:149-188`
- **What:** the comment at line 181 argues serial and console must be emitted "because nothing
  adds one automatically". The same is true of `devices.rngs` and `clock.timer`; neither is
  emitted. Everything else asked about libvirt does supply — memballoon, the `on_*` triple,
  machine-type canonicalisation, the emulator, controllers, inputs.
- **Why it matters here:** a Rocky 9 guest's first boot generates sshd host keys and seeds its
  CRNG; with no virtio-rng that comes from RDRAND alone. Every host is confirmed Haswell or
  newer (`main.tf:98`), which is why this is not higher. `<timer name='hpet' present='no'/>` is
  what RHEL's guest tuning sets; its absence has no observed symptom here.
- **Evidence:** `tests/fixtures/libvirt/domain-marked.xml` is `vcows-probe02`, a
  minimally-defined domain on this rig: `<clock offset='utc'/>` with no timers and no `<rng>`.
  `domain-unmarked-running.xml` (virt-install-built, same host) has `<rng model='virtio'>` and
  the rtc/pit/hpet set. libvirt filled in memballoon, watchdog, audio, inputs and the `on_*`
  triple for both.
- **Fix / cost:** `devices.rngs = [{ model = "virtio", backend = { model = "random" } }]` is
  three lines; `clock = { offset = "utc", timer = [...] }` is eight. No variables. I would not
  spend the timers without a reproduction. The real defect is that neither choice is written
  down — if they stay out, that belongs in §3 beside the serial-console reasoning already there.

### F-TOFU-06 — `output "base_volume_path"` has no reader

- **Severity:** S4
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/outputs.tf:31-34`
- **What / why:** `parse_outputs` reads `raw["vms"]["value"]` and nothing else
  (`backends/libvirt/__init__.py:91`), so `base_volume_path` is computed and never consumed. §5
  makes unjustified surface a defect, and an output nothing parses is what the next person keeps
  assuming something reads it.
- **Evidence / fix:** `grep -rn base_volume_path .` returns one hit, `outputs.tf:31`. Delete the
  block — four lines removed. Keep it only if `inventory.json` should carry the base path, in
  which case `parse_outputs` must read it and the inventory contract must say so.

## Checked and sound

* **`for_each` keys against D16.** Keys are the logical name, matching `NAME_PATTERN =
  ^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$`, all legal HCL map keys. No two VMs can collide in the
  volume namespace: `overlay(x) == seed(y)` is unsatisfiable across the two suffixes and
  `overlay(x) == overlay(y)` implies `x == y`. The only collision available is
  `base_volume_name` — F-TOFU-02.
* **D31's `depends_on`.** Correct and complete. With `create = true` the overlays carry an
  implicit edge through `libvirt_volume.base[0].path`, the seeds get the explicit one, and the
  domains descend from both; with `create = false` the base has no vertex to fail. A failed base
  is a no-op apply either way.
* **The `count` guard.** `var.base_volume.create` is a plain input, known at plan time, so
  `count` never hits "not known until apply", and `local.base_path` selects on the same input —
  `base[0]` is only read where the instance exists.
* **`outputs.tf` cannot produce null or partially-known.** `preflight.base_volume` returns
  `path` as `""` or a real string, never `None` (`preflight.py:266,268`); `output "vms"`
  iterates `libvirt_domain.vm` and indexes `var.vms` by the same `for_each` key, so the two maps
  cannot disagree, and the CLI returns early on an empty create set.
* **Re-apply against non-empty state is prevented by construction.** The default run dir is
  `runs/<deployment>/<UTC timestamp>`, and `seed.mkdir()` / `workdir.mkdir()` (`cli.py:203,205`)
  are called *without* `exist_ok`, so a `--run-dir` pointing at any previous run that reached
  staging dies with `error: FileExistsError: ... /seed` before `tofu init`. `terraform.tfstate`
  is always absent when the apply starts. D40 is real.
* **Overlay capacity below the backing image** is caught offline as an ERROR by
  `_check_disk_capacity` (`schema.py:395-432`), so it cannot reach the module.
* **CPU and machine defaults.** libvirt fills `check='none' migratable='on'` onto
  `host-passthrough`; `type_machine = "q35"` is a QEMU alias canonicalised at define time on
  every target version. No `cpu.topology` means N sockets × 1 core, cosmetic for Rocky guests.
  `tofu fmt -check -recursive`: clean, exit 0.

## Not checked

* Whether older libvirt actually discards a pinned loader under autoselection. Settling it needs
  a Rocky 9 host; F-TOFU-01 names the test.
* `libvirt_volume`'s `create.content.url` upload path — spike A4 / acceptance A2.
* `render.py` beyond the fields the module consumes (agent 03's scope).
* The provider's Go source; the pinned schema JSON was ground truth for attribute existence and
  spelling only.

## Deserves its own agent

* **`orphan_volumes` only checks VMs still in the config.** `preflight.py:326` iterates
  `cfg["vms"]`, so a volume orphaned by a crashed create for a VM later removed from
  `config.yaml` is never reported and never destroyable. §2 records the orphan gap assuming
  preflight names the file; here it does not.
* **The per-VM partial apply.** A failed overlay for VM A leaves A's seed volume written and in
  state while VM B completes — one leaked seed ISO per failed VM. D31 closed only the
  base-failure case. The orphan gap catches it next run, but this run errors with no statement
  of what was left behind.
