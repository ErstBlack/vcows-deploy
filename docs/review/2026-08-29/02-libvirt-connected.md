# The connected half — review

Agent: 02-libvirt-connected · Scope: `orchestrator/backends/libvirt/preflight.py`, `orchestrator/backends/libvirt/destroy.py` · Date: 2026-08-29

## Summary

* The `<backingStore>` exclusion holds on every path I could construct; the NVRAM floor cannot be shed; `pool.refresh(0)` precedes every lookup on active pools.
* **`Outcome` is computed per object and then thrown away.** `cmd_destroy` ignores what `backend.destroy` produced, so every skip and WARNING is invisible and `run.json` records the *requested* targets as destroyed.
* Preflight's "delete it on the hypervisor and re-run" points at the shared golden image without saying every other deployment's overlays back onto it.
* Destroy's per-object accounting has holes where a raw `libvirtError` escapes the loop; preflight has one that aborts the whole walk. Both reproduced.
* Destroy matches libvirt errors numerically everywhere. **Preflight does not** — it infers "does not exist" from any `libvirtError` on two paths.

## Findings

### F-LVC-01 — destroy's per-object outcome is discarded by its only caller
- **Severity:** S2
- **Confidence:** high
- **Location:** `orchestrator/cli.py:294`, `:296-304`; `destroy.py:257-258`
- **What:** `destroy()` fills `Outcome.destroyed`, `.skipped` and non-fatal `problems`, then raises only if something was fatal. `cmd_destroy` calls it as a statement, prints `destroyed {len(targets)} VM(s)` and records `extra={"destroyed": sorted(e.name for e in targets)}` — the list it *asked* to destroy. A disk that hit `NO_STORAGE_VOL` and went to `skipped` (`destroy.py:186-190`), a pool whose refresh failed (`:222-230`), and a daemon that made the mask shed bits (`:145-151`) all give a clean exit-0 run with no output at all.
- **Why it matters here:** the module docstring states the guarantee this file exists for — "Every object's outcome is reported… Silent partial success is the specific defect findings.md §1 rejects `tofu destroy` for." Half of it is not implemented. A pool inactive at teardown time means the domain is destroyed and undefined, its marker gone, both volumes still on disk, reported as success — and nothing can find them by marker again.
- **Evidence:** with `FakePool("images", {"app01.qcow2":"", "app01-seed.iso":""}, active=False)` and one target carrying both paths, `d.destroy(...)` raises nothing, `pool.deleted == []`, both volumes remain, and the domain log is `['destroy', 'undefine:55']`. `_refresh_pools` skips inactive pools (`:218-219`), so "after a refresh this genuinely means gone" (`:187-189`) does not hold there.
- **Fix:** have `destroy()` print `out.skipped` and the non-fatal `out.problems` before returning, or return `Outcome` for `cmd_destroy` to report and record. Separately, an inactive pool holding a target's disk should be an ERROR, not a silent skip.
- **Cost of the fix:** printing from the backend is four lines and no new surface. Returning `Outcome` changes the ABC signature and leaks a backend type through the seam — prefer the first.

### F-LVC-02 — preflight instructs the operator to delete the shared golden image
- **Severity:** S2
- **Confidence:** high
- **Location:** `preflight.py:301-311`
- **What:** when `<physical>` disagrees with the local file, the ERROR ends "Delete it on the hypervisor and re-run." `README.md:93` calls that volume "shared per host, uploaded once" and `main.tf:49-52` makes every overlay a `backing_store` on it. Deleting it removes the backing file for every VM on that host, in every deployment.
- **Why it matters here:** the routine trigger is not a truncated upload, it is an operator pointing `source_qcow2` at a newer golden image while leaving `base_volume_name` alone — nothing couples the two (`config.py:39-48`). They follow the tool's instruction and break every running VM's disk chain. `vol.delete()` unlinks; guests survive on their open descriptors and fail at the next boot, which is the S1 shape. This is the hazard `destroy.py:170-176` is architected around, reached from the other side.
- **Evidence:** the message text at `preflight.py:305-309`, against `findings.md` §2 ("shared across deployments on that host") and `main.tf:20-23`.
- **Fix:** append one clause naming the consequence — any overlay already backing onto it breaks, so check the backing chain first, or use a new `base_volume_name` for a new image.
- **Cost of the fix:** one sentence in one f-string. F-LVC-07 makes it more urgent: a failed refresh can produce this instruction *falsely*, from a stale cached size.

### F-LVC-03 — an uncaught `libvirtError` aborts the destroy loop mid-teardown
- **Severity:** S3
- **Confidence:** high
- **Location:** `destroy.py:103`, `:110`; also `:217-219`, `:238`
- **What:** `destroy()` guards `lookupByUUIDString` for the vanished-domain race (`:243-246`), then calls `dom.isActive()` one line later unguarded. The same race one instruction wider escapes as a raw `libvirtError`: every remaining target is untouched, `DestroyError` is never raised, the accumulated `Outcome` is lost, and `cli.main`'s `except Exception` prints `error: libvirtError: …`. `listAllStoragePools`, `pool.isActive` and `getLibVersion` are unguarded too.
- **Why it matters here:** the operator gets a UUID, no statement of which of five VMs were torn down and which were not, and no run record. Re-running is safe but they cannot tell that from the output.
- **Evidence:** a fake domain whose `isActive()` raises code 42, placed first in a two-target list → `RAISED libvirtError Domain not found`, and the second domain's call log is empty.
- **Fix:** move the `isActive()` calls inside `_stop`'s existing `try`, or wrap the per-target body in one `except libvirt.libvirtError` that records a `Problem` and continues.
- **Cost of the fix:** three lines in a function that already has the catch shape.

### F-LVC-04 — a domain vanishing mid-enumeration aborts the whole preflight
- **Severity:** S3
- **Confidence:** high
- **Location:** `preflight.py:129-142`
- **What:** `_domains` calls `dom.XMLDesc()`, `dom.name()` and `dom.UUIDString()` inside the `listAllDomains` loop with no handler. A domain undefined by someone else between the list and the describe kills preflight, and so kills `deploy` and `destroy`. `walk()` (`:235-243`) catches exactly this race for volumes and explains why; `_domains` does not.
- **Why it matters here:** the rig hosts four VMs belonging to someone else. A teardown of *your* deployment is blocked by their churn, with their UUID in the message.
- **Evidence:** one domain whose `XMLDesc` raises code 42 plus one healthy domain → `preflight(cfg, conn)` raises `libvirtError Domain not found`; the healthy domain is never recorded.
- **Fix:** wrap the loop body in the same `except (libvirt.libvirtError, ET.ParseError): continue` that `walk` already uses. A vanished domain cannot be a name clash or a destroy target, so dropping it is correct.
- **Cost of the fix:** two lines, mirroring an existing pattern in the same file.

### F-LVC-05 — preflight infers "does not exist" from any `libvirtError`
- **Severity:** S3
- **Confidence:** high
- **Location:** `preflight.py:189-199`, `:359-368`, `:376-382`
- **What:** destroy.py matches numerically throughout, pinned against the binding by `tests/test_libvirt_destroy.py:42`. Preflight matches on the exception *type* and asserts a cause: any error from `storagePoolLookupByName` becomes "storage pool 'X' does not exist on this host. vcows never creates a pool"; any error from `networkLookupByName` becomes "network 'X' does not exist on this host". A transport drop, auth failure or permission denial over the `qemu+ssh` link presents as a confident, wrong, actionable instruction. The third case is worse in kind: `net.DHCPLeases()` swallows every `libvirtError` with a bare `pass`, so the lease half of the address-collision check silently degrades to reservations-only and a real IP collision goes unreported.
- **Why it matters here:** the operator goes to the hypervisor, finds the pool present and active, and has nothing to work from. For the leases case, deploy proceeds and two guests land on one address — the acceptance run's S1 shape.
- **Evidence:** `tests/fake_libvirt.py:152` and `:171` already raise codes 49 (`NO_STORAGE_POOL`) and 43 (`NO_NETWORK`) specifically, so the fixture already supports the narrower match; no test asserts on the broad one.
- **Fix:** compare `exc.get_error_code()` against `VIR_ERR_NO_STORAGE_POOL` / `VIR_ERR_NO_NETWORK` and re-raise otherwise; give `DHCPLeases` a WARNING `Problem` instead of a bare `pass` when the code is not the expected one.
- **Cost of the fix:** two numeric constants beside the three destroy.py already declares, and one WARNING string.

### F-LVC-06 — the orphan-volume refusal is keyed on basenames, host-wide
- **Severity:** S3
- **Confidence:** medium
- **Location:** `preflight.py:444`, consumed at `:315-340`
- **What:** `claimed = {os.path.basename(path) for e in vms for path in e.disks}` collapses every disk of every domain on the host to a filename, while `walk()` already carries each volume's full `path` (`:161`) which `orphan_volumes` never reads. An unrelated domain — another pool, another deployment, a hand-made VM — holding a disk called `app01.qcow2` marks our genuinely orphaned `app01.qcow2` as claimed, and findings.md §2's refusal does not fire.
- **Why it matters here:** that refusal is the only thing between a mid-create crash and an apply dying on a raw "storage volume exists already". Volume names are the undecorated logical name (D16, `render.py:42-47`), so short names like `app01` collide easily.
- **Evidence:** `preflight.py:444` against the unused `"path"` key in `volume_facts`.
- **Fix:** build `claimed` from full paths and test `volumes[volume]["path"] in claimed`.
- **Cost of the fix:** one comprehension and one membership test, on data already in hand.

### F-LVC-07 — a failed pool refresh is a WARNING, and deploy proceeds on stale data
- **Severity:** S3
- **Confidence:** medium
- **Location:** `preflight.py:210-221`
- **What:** when `pool.refresh(0)` fails, `open_pool` returns the pool with a WARNING and `preflight` walks the stale cache anyway. `cmd_deploy` refuses only on `p.fatal` (`cli.py:183-185`), so the deploy runs on exactly the cache state D35 exists to prevent. The warning names only the "present image looks absent" consequence — not that `orphan_volumes` has also stopped working, nor that a stale cached `<physical>` triggers F-LVC-02's delete instruction against a healthy image.
- **Why it matters here:** every storage decision downstream of that line reads the cache, and `open_pool`'s own docstring calls the refresh "required for correctness, not defensive". A WARNING is the defensive treatment.
- **Evidence:** `open_pool` returns `(pool, [Problem(Severity.WARNING, …)])` at `:213-221`; `preflight()` at `:442` branches only on `pool is not None`.
- **Fix:** make it `Severity.ERROR`. Deploy then refuses; destroy still proceeds, because `cmd_destroy` treats every problem as advisory (`cli.py:272-275`) — the asymmetry that exists for exactly this reason.
- **Cost of the fix:** one enum member.

### F-LVC-08 — the NVRAM floor comment contradicts the constant four lines above
- **Severity:** S5
- **Confidence:** high
- **Location:** `destroy.py:45-47` against `:41`
- **What:** `UNDEFINE_NVRAM = 4  # since 1.2.9`, then "All three predate libvirt 1.2.9, so no supported target rejects them". NVRAM was introduced *in* 1.2.9. The conclusion still holds (RHEL 9.0 EUS ships 8.0.0) but the stated reason is wrong, and this comment justifies the one flag the file is built around never dropping.
- **Why it matters here:** someone re-deriving the floor for an older target reads this and concludes no version check is needed anywhere.
- **Evidence:** the two lines, quoted above.
- **Fix:** "All three exist from 1.2.9, and the oldest supported target ships 8.0.0."
- **Cost of the fix:** one line.

## Checked and sound

* **`<backingStore>` exclusion holds on every path.** `disk.find("source")` matches direct children only; a two-level backing chain yields only the disk's own source. Block (`<source dev=>`), volume-type (`<source pool= volume=>`) and device-less disks yield nothing rather than a wrong path — a leak, the safe direction. Verified against a hand-built six-disk domain.
* **The undefine floor cannot shed NVRAM.** `_undefine` retries at `FLOOR` once, `FLOOR` includes bit 4 unconditionally, a `FLOOR` rejection reports rather than looping, and `INVALID_ARG` is the only code that triggers the retry — so a real `OPERATION_INVALID` refusal cannot be swallowed.
* **Destroy-then-undefine ordering**, and `_stop`/`_undefine` returning `False` so a domain that would not stop never has its disks deleted.
* **`pool.refresh(0)` precedes every lookup** in preflight (`open_pool` → `walk`) and destroy (`_refresh_pools` → `storageVolLookupByPath`), for every *active* pool. The inactive-pool hole is F-LVC-01.
* **Numeric error-code matching in destroy.py**, pinned against the installed binding.
* **Every XML parser guards the elements it indexes** — `volume_facts`, `macs_of`, `disks_of`, `marker_of`; `nic["ip_cidr"]` is safe because `NIC_SCHEMA` requires it (`schema.py:52`).
* **`preflight()` with `pool is None`** leaves `artifacts["base_volume"]` unset but emits a fatal ERROR on the same branch, so `cmd_deploy` refuses before `prepare` reads it. No `KeyError` path.
* **TOCTOU behaves as findings.md §2 claims — hard error, not corruption.** Two operators racing with `base.create = true`: the loser's `StorageVolCreateXML` fails and `depends_on = [libvirt_volume.base]` (`main.tf:81`) makes the rest a no-op apply. A foreign domain defined between preflight and apply: the overlay and seed are written, `DomainDefineXML` fails on name uniqueness, state is discarded (D23), and the next run refuses twice — `decide()` on the unmarked name clash, and `orphan_volumes` naming the two leaked volumes. Racing *destroys* are idempotent: the loser takes `lookupByUUIDString` and `NO_STORAGE_VOL` misses and exits 0. F-LVC-06 is the one way the second refusal can fail to fire.

## Not checked

* No live-hypervisor calls, per the brief. Everything is `tests/fake_libvirt.py` and hand-built XML, so RHEL 9's actual codes on these paths come from the binding rather than observation.
* `schema.connection_uri`, `derive_mac`, `mac_of`, `prepare.py` — the offline half.
* `marker.py`'s parser, `decide()`, and the tofu module beyond the two lines F-LVC-02 rests on.
* Whether `registerErrorHandler`'s process-global silencing hides anything from `container/entrypoint.py` or the provider subprocess.

## Deserves its own agent

* **`cmd_destroy`/`cmd_deploy` as reporting surfaces.** F-LVC-01 is one instance of a general question: which non-fatal signals does the CLI compute and then not print? `run.json` recording intent rather than outcome is the same shape.
* **The base image lifecycle across deployments.** Nothing in the tool knows how many overlays back onto a given base, and three documents call it shared. `prune` is cut; the *warning* is not the same thing as the feature.
