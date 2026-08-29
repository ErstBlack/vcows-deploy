# Cross-deployment collision — review

Agent: 15-cross-deployment-collision · Scope: `preflight.py`, `render.py`, `schema.py`,
`marker.py`, `tofu/main.tf`, `README.md` · Date: 2026-08-29

## Summary

* The marker carries `deployment` (findings.md:119) and **nothing else does**. Every derived
  name — domain, overlay, seed, MAC, instance-id, NVRAM path — is keyed on the VM's logical
  name alone, so `deployment` is enforced exactly where a marker can be read (domains, by
  `decide()`) and nowhere else. Where it reaches, the refusals are correct; all the damage is
  in the markerless objects.
* Verified the D30 remedy directly, and it is the highest-value item here: it instructs the
  operator to delete the one object every deployment on that host depends on, and the routine
  golden-image refresh is its trigger.
* The base volume's protection is structural and holds; `readonly` is the wrong lever. The gap
  is the sentence vcows prints. `derive_mac` and `derive_id` are the only one-way doors.

## Findings

### F-XDEP-01 — D30's remedy tells the operator to delete every VM's backing file
- **Severity:** S1
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:300-309`
- **What:** when the host's `<physical>` disagrees with the local golden image, `base_volume`
  emits an ERROR ending `"Delete it on the hypervisor and re-run."` The named volume is
  `libvirt_volume.base` — the backing file of every overlay vcows has created on that host,
  across every deployment. `virsh vol-delete` has no backing-chain awareness
  (`destroy.py:_delete_volume`: `in_use` is only set by the storage driver's own transient
  operations), so the unlink succeeds. Running guests hold the deleted inode open and keep
  working; the loss surfaces at each unrelated VM's next shutdown as `Could not open backing
  file`, the deltas unrecoverable.
- **Why it matters here:** the trigger is the intended workflow, not corruption. `README.md:93`
  names one host-scoped volume (`base_volume_name: golden.qcow2  # shared per host, uploaded
  once`). An operator refreshing the golden image drops a new qcow2 at `source_qcow2`; a new
  image is a different size, so D30 fires *every time* and hands out the destructive remedy as
  the routine one. Air-gapped, there is no re-pull.
- **Evidence:** `preflight.py:304-308` — `f"volume {name!r} is {physical} bytes on the host
  but {local} bytes locally. That is either a truncated upload or a different image under the
  same name; either way every overlay would back onto it. Delete it on the hypervisor and
  re-run."` The message states the shared-backing fact in the clause before the instruction,
  then instructs against it.
- **Fix:** name the non-destructive procedure, because it already works: `base_volume` keys on
  `cfg["image"]["base_volume_name"]` (`preflight.py:264`), so a **new** `base_volume_name` for
  a new image sets `create = True`, uploads alongside, and leaves existing chains intact. Say
  "if you are replacing the image, set a new `base_volume_name`; deleting this volume breaks
  every existing overlay on this host." Second, `walk()` already reads each volume's `XMLDesc`,
  which for a qcow2 overlay carries `<backingStore><path>`, so counting the volumes backing
  onto this one is one `findtext` on data in hand.
- **Cost of the fix:** two sentences in one f-string, plus one `findtext` and a counter. No
  config field, no verb. Justified because the tool's own docstrings call this volume the thing
  "every other deployment's overlays depend on" and then print the opposite. (Confirms 11's
  F-LIFE-02 independently; the count and the working alternative procedure are new.)

### F-XDEP-02 — the orphan refusal names another deployment's disk and asserts one cause
- **Severity:** S2
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:315-341`
- **What:** volume names are undecorated (`overlay_name("app01") == "app01.qcow2"`) in one
  flat pool. `orphan_volumes` reports any volume matching a *configured* VM's derived name
  that no domain claims, with `where=vm["name"]` and the fixed text "A previous create was
  interrupted; delete it on the hypervisor and re-run." If deployment `lab-a` has an `app01`
  whose domain is gone but whose disk was kept — a hand `virsh undefine` preserving storage,
  or the partial destroy in 02's F-LVC-01 — then `lab-b`'s deploy of its own `app01` is
  refused, blamed on `lab-b`'s VM, given one wrong cause, and told to delete `lab-a`'s data.
- **Why it matters here:** that message is the tool's only statement about the volume and is
  wrong in both directions. §2's accepted gap assumes the orphan belongs to the caller.
- **Evidence:** `orphan_volumes` takes and reads no deployment name;
  `orphan_volumes({"vms":[{"name":"app01"}]}, {"app01.qcow2":{}}, set())` → `error [app01]:
  volume 'app01.qcow2' exists but no domain references it. A previous create was interrupted…`
- **Fix / cost:** one f-string — state the cause as a possibility and say the volume may belong
  to another deployment on this shared pool, because vcows cannot tell. Decorating volume names
  removes the collision outright but is a design change: decide it once, with F-XDEP-04.

### F-XDEP-03 — the derived MAC's collision domain is the L2 segment; the only check is per-host
- **Severity:** S2
- **Confidence:** high on the derivation, medium on the trigger
- **Location:** `orchestrator/backends/libvirt/schema.py:122`, `preflight.py:396-415`
- **What:** `derive_mac` is `uuid5(VCOWS_NS, f"{name}#nic{index}")` — no deployment, no host.
  Two deployments each containing `app01` derive the identical MAC. On one host `decide()`
  refuses on the domain name first, so it never matters. On **two** hosts bridged to the same
  VLAN it does, and `address_conflicts` builds `by_mac` from `_domains(conn)` — this host
  only. Nothing looks wider.
- **Why it matters here:** both guests boot, cloud-init matches its interface by that MAC,
  applies its static address, and both report `cloud-init status: done` on different IPs with
  one MAC — the calibration section's S1 shape, delivered as intermittent L2 reachability no
  vcows output mentions. `network: default` (the README example) is per-host NAT and immune;
  `bridge:` is not.
- **Evidence:** `derive_mac("app01", 0)` → `52:54:00:ee:77:63` for any deployment;
  `derive_id("app01")` → `9af67253-…` likewise. `Marker.for_vm("app01","lab-a")` and
  `…("lab-b")` differ only in the `deployment` key — the `id` is byte-identical.
- **Fix / cost:** fold `deployment` into the uuid5 input (`f"{deployment}/{name}#nic{index}"`)
  — one f-string, free **now** and impossible later, since `derive_mac`'s docstring is
  explicit ("This derivation is permanent. Changing it renames the interface every running VM's
  guest configuration is keyed to") and the same holds for `derive_id`. Documenting that a
  shared L2 needs an explicit per-VM `mac:` leaves the failure available.

### F-XDEP-04 — the flat namespace, enumerated
- **Severity:** S3
- **Confidence:** high
- **Location:** `render.py:31-45`, `schema.py:122`, `marker.py:derive_id`, `main.tf:126`
- **What:** every derived name, its scope, and what a second deployment sees. Only the domain
  name is protected, and only because it is the one object carrying a marker. `deployment`
  bought the destroy side (D36) and the create side and nothing for storage or addressing —
  while findings.md:119 explicitly rejected "one deployment per hypervisor" as an assumption.

  | name | from | scope | second deployment, same VM name |
  |---|---|---|---|
  | domain | `name` | host | REFUSE by `decide()`, owner named. Correct. |
  | overlay `<name>.qcow2` | `name` | pool | unreachable while the domain exists; F-XDEP-02 when it does not |
  | seed `<name>-seed.iso` | `name` | pool | as overlay |
  | MAC | `name#nicN` | **L2 network** | undetected — F-XDEP-03 |
  | instance-id | `name` | guest-local | harmless; cloud-init state is per-guest |
  | NVRAM `<name>_VARS.<fmt>` | `domain_name` | host, outside every pool | F-XDEP-06 |
- **Fix / cost:** decide once, before ship, which get `deployment` folded in. MAC and
  instance-id are the one-way doors and must be settled now (one f-string). The volume and
  domain names are D16 and can change after ship without breaking destroy, which resolves
  disks from domain XML rather than by re-deriving names — but decorating them trades away
  D16's stated benefit ("maximally predictable for hand-debugging at a site").

### F-XDEP-05 — `orphan_volumes` iterates `cfg["vms"]`, so §2's refusal has a path that does not fire
- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:328`
- **What / why:** the loop is over the *current* config's VMs, so a volume orphaned for a VM
  since removed from `config.yaml` — or belonging to a deployment whose config is not the one
  being run — is never reported by any invocation and is never destroyable, since destroy only
  resolves paths from domain XML. §2 records the gap as bounded ("Preflight refuses and names
  the file for the operator to delete"); that holds only while the VM stays in the config.
- **Evidence:** `orphan_volumes({"vms":[{"name":"app01"}]}, {"app01.qcow2":{},
  "app02.qcow2":{}}, set())` returns one problem, for `app01.qcow2`. The other is silent.
- **Fix / cost:** one sentence in findings.md §2 and one in the docstring, stating the bound.
  Do **not** widen the scan: classifying `app02.qcow2` means deciding which volumes in someone
  else's pool vcows has an opinion about, which is the `prune` feature §5 cut.

### F-XDEP-06 — a stale NVRAM varstore from another deployment's same-named VM is adopted silently
- **Severity:** S3
- **Confidence:** medium — mechanism from libvirt's documented behaviour, not reproduced (the
  rig was read-only for me)
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:126`
- **What:** the varstore path is `/var/lib/libvirt/qemu/nvram/<domain_name>_VARS.<fmt>`, keyed
  on the VM name and outside every storage pool. libvirt copies `template` into it only when
  the file is absent; an existing file is used as-is. If a previous deployment's `app01` was
  undefined with `--keep-nvram`, or defined out of band, vcows' new `app01` boots on the old
  deployment's EFI variables — boot entries, and Secure Boot state if the loader is a
  `.secboot` build.
- **Why it matters here:** it is the only name in F-XDEP-04's table that neither the marker,
  `disks_of`, nor `orphan_volumes` can see — `walk()` covers the pool, and this is not in one.
  The domain comes up, so it presents as success.
- **Evidence:** `main.tf:126` interpolates `each.value.domain_name` == `name`; nothing in
  `preflight.py` reads that path. Settled by defining a domain against a pre-existing varstore.
- **Fix / cost:** one paragraph in findings.md §2 beside the orphan-volume gap. Nothing more:
  a varstore check is a second read against a path outside the pool, exactly the host-level
  reach D29 refuses.

## Checked and sound

* **`decide()`'s cross-deployment refusal.** Marker-keyed, exact equality on `deployment`,
  refuses naming the owning deployment, before anything is written (`cli.py:181-189`). Two
  deployments cannot both create one logical name on a host. The create side is closed by this.
* **`destroy` scoping (D36).** `cmd_destroy` filters `marker.deployment == deployment` and
  prints the rest with their deployment names. B cannot tear down A.
* **Base volume lifecycle.** Creation: `count = var.base_volume.create ? 1 : 0`
  (`main.tf:20-31`) from `preflight.base_volume`, so one deployment creates it and every later
  one sets `create = false` and reads `var.base_volume.path`. Verification: D30's `<physical>`
  comparison, per deploy. Reuse: every overlay's `backing_store.path` (`main.tf:47-52`).
  Upgrade: no path, and none should exist — a new `base_volume_name` is the working procedure
  (F-XDEP-01). Deletion: no vcows path reaches it; Python destroy cannot (volumes carry no
  marker, `disks_of` never follows `<backingStore>`) and `tofu destroy` is unused
  (findings.md:56). **Structural, and it holds.**
* **`readonly` is the wrong lever — do not add it.** The provider exposes
  `libvirt_volume.target.permissions` (`docs/provider-schema-0.9.8.json`), but 0444 does not
  prevent `virsh vol-delete`: libvirtd unlinks as root, and unlink is governed by the
  directory, not the file. Qemu already opens backing files read-only, so it would defend a
  write nobody makes while leaving the real deletion path open. Fix the message instead.
* **The orphan-refusal window, timed.** `open_pool` refreshes once, before `walk` — the right
  placement for D35, since everything `orphan_volumes` and `base_volume` read comes from that
  one listing. Between a destroy that skipped a volume and the next preflight, the volume
  **blocks** redeployment when the VM is still in the config and in the configured pool, and
  **slips through** in four cases: the VM was removed from the config (F-XDEP-05); the volume
  is in a pool other than `target.libvirt.pool`, which `walk` never sees; another domain holds
  a disk with the same basename (02's F-LVC-06); or `pool.refresh(0)` failed and only warned
  (02's F-LVC-07). Only the first is mine. The placement is right; the defect is that a
  refresh failure is advisory while everything downstream assumes success.

## Not checked

* Provider behaviour when `create.content.url` names an existing volume — I traced only the
  `count` guard that prevents the question arising.
* Any live confirmation. `virsh` against `qemu+ssh://vcows@vcows/system` was blocked in my
  environment, so `<backingStore>` in volume XML (F-XDEP-01's fix) and varstore reuse
  (F-XDEP-06) come from libvirt's documented formats, not observation.

## Deserves its own agent

* **The seed ISO's `deployment` blindness.** `prepare.seed_files` writes `instance-id` and
  `local-hostname` from the VM name and passes `user_data` verbatim, so two deployments with
  one VM name produce byte-identical seeds apart from `user_data`. The run directory is
  `runs/<deployment>/…` while the ISO inside identifies no deployment, so a seed copied off one
  host to debug another is indistinguishable. Whoever holds the run directory should judge it.
