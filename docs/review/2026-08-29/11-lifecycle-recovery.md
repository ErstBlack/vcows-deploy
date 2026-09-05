# Lifecycle and recovery — review

Agent: 11-lifecycle-recovery · Scope: `orchestrator/cli.py`,
`orchestrator/backends/libvirt/{destroy,preflight}.py`, `docs/findings.md` §2, R5
· Date: 2026-08-29

## Summary

* **Create-side crash recovery is genuinely good.** Nothing before `apply` touches the host; a partial
  apply leaves marked domains the next preflight SKIPs and unmarked volumes it REFUSES on. Teardown
  ordering is correct as documented.
* **Recovery is one-directional.** Destroy's partial outcomes are discarded, and the residue resurfaces
  on a later deploy misdiagnosed as an interrupted *create*.
* **The concrete upgrade breakage is the base volume, not the marker.** Marker format, `schema_version`
  and the provider pin all move cleanly or break loudly. A new golden qcow2 under the README's
  `base_volume_name: golden.qcow2` gives a D30 error whose only remedy destroys every VM's backing chain
  on that host.
* **Twenty VMs is not a scaling problem.** Measured: `.terraform` is 12.5 KB of symlinks per run in the
  image (26.7 MB without the cache), seed ISOs 69,632 bytes, one base upload, one provider connection.
  What grows without bound is plaintext `user_data`.
* `marker.v` is stamped into every domain on the host and read by nothing.

## Findings

### F-LIFE-01 — a leaked volume comes back as "a previous create was interrupted"
- **Severity:** S1 · **Confidence:** high (mechanism), medium (frequency)
- **Location:** `destroy.py:233-258`, `preflight.py:315-340`, `cli.py:294`
- **What:** `Outcome.skipped` — every volume path that would not resolve — and every WARNING collected,
  `_refresh_pools`' "disks in it may not resolve" included, die when `destroy()` returns: only
  `out.failed` (fatal-only) raises. The operator sees `destroyed N VM(s)`, exit 0, and `run.json`
  records the *intended* target list. Later, the surviving `<name>.qcow2` or `<name>-seed.iso` is
  unclaimed and `orphan_volumes` refuses the next deploy with *"A previous create was interrupted."*
- **Why it matters here:** a disk on an inactive pool, on one whose refresh warned, or outside every
  pool is skipped silently — the conditions D35 exists for. The blocked deploy is the only signal the
  teardown was incomplete, and it names the wrong event on the wrong verb, weeks later. `destroy.py:24`
  claims "Every object's outcome is reported" — untrue at the CLI boundary.
- **Evidence:** `cli.py:294` return value unused; `destroy.py:190` `out.skipped.append` then `return`;
  `destroy.py:257` `if out.failed:`, `failed` being `any(p.fatal ...)`.
- **Fix / cost:** the discard itself is another agent's. Mine: whatever surfaces the `Outcome` must
  reach `run.json` too, and `orphan_volumes` must stop asserting one cause. Two lists into the existing
  `extra` dict, one reworded string.

### F-LIFE-02 — the golden-image refresh path says to delete the volume every VM depends on
- **Severity:** S2 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/preflight.py:301-311`
- **What:** D30's size mismatch emits a fatal *"Delete it on the hypervisor and re-run."* Correct for
  the truncated upload it was written for. It is also where an operator lands when a release ships a new
  golden image, since `base_volume_name` is host-scoped and the README names it `golden.qcow2` — no
  version, no image identity. It never says every overlay backs onto that volume, and `vol.delete`
  removes it under running VMs without complaint (`destroy.py:166-179`).
- **Why it matters here:** R5's "no air-gapped update path" made concrete. Refreshing the golden image
  presents as an authoritative error with one remedy that corrupts every VM on that hypervisor, across
  every deployment sharing it. The correct procedure — a new `base_volume_name` per image — appears
  nowhere.
- **Evidence:** `README.md:93` `base_volume_name: golden.qcow2   # shared per host, uploaded once`;
  `main.tf:20-31`, base created once, destroyed by neither path.
- **Fix / cost:** put the consequence and the alternative in the message — name a new `base_volume_name`
  for a new image; deleting this one breaks every overlay backing onto it. Two sentences and a README
  line; a backing-chain check is the sprawl §5 forbids.

### F-LIFE-03 — the build manifest is never copied into the run directory
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:46-53`, `cli.py:94-124`
- **What:** `manifest()` has one caller, `cmd_version` (`cli.py:338`). Three documents say it also lands
  in the run directory: `orchestrator/__init__.py:11` lists it among the five VERSION consumers
  `tests/test_version.py` keeps in agreement, `README.md:146` puts `manifest.json` in the run
  directory's file table, and R5 specifies "copy into every run directory". `run.json` carries `vcows`
  and the tofu version — not the provider version, base-image digest, or git SHA.
- **Why it matters here:** the first question of any N→N+1 move, and of any support request from an
  air-gapped site, is which image produced this VM. The artifact built to answer it never reaches the
  record that leaves the site, and the drift guard misses it because the consumer does not exist.
- **Evidence:** `grep -rn manifest --include=*.py orchestrator/` returns only the constant,
  `cmd_version` and two docstring mentions.
- **Fix / cost:** `shutil.copy(MANIFEST, run / "manifest.json")` in `_run_dir`, or fold the dict into
  `run.json` under a `build` key — smaller, one file to read. One line.

### F-LIFE-04 — no run record survives any failure path
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:173-233`, `cli.py:254-305`
- **What:** `_record`'s six call sites (`cli.py:186, 192, 224, 286, 291, 296`) are all on non-exception
  paths. Any `TofuError`, `TimeoutExpired` or `DestroyError` reaches `main` (`cli.py:393`), which prints
  one line and returns 1 — leaving a chmod'd run directory with seed ISOs, a tofu tree and no
  `run.json`. That absence is the only way to tell a crashed run from a refused one, documented nowhere.
- **Why it matters here:** partial apply and partial destroy most need a record and produce none. At
  twenty VMs a `DestroyError` after fourteen successes leaves scrollback as the sole account of which
  fourteen — `DestroyError.__init__` joins only the fatal problems.
- **Fix / cost:** wrap each command body so `_record(..., "failed", extra={"error": str(exc)})` runs
  before re-raising. About eight lines and no new concept — `outcome` is already a free-form string with
  four values.

### F-LIFE-05 — destroy reports success when it can no longer recognise its own VMs
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:264-288`
- **What:** `cmd_destroy` filters to `marker is not None` (`cli.py:264`), then to the deployment.
  Unmarked domains are never mentioned even when their names match `cfg["vms"]`. With no targets it
  prints "no VMs marked for deployment 'lab-a' on this target" and exits 0.
- **Why it matters here:** that is the exact output of a marker-format break — a `MARKER_XMLNS` bump, or
  a payload change making `marker_of` return `None` under D12; the rig carries a fixture already in that
  state (`vcows-spike-probe01`). Deploy handles it, since `decide()` REFUSEs the name clash; destroy
  exits 0, so the evidence that an upgrade abandoned the operator's VMs is a success message.
- **Fix / cost:** in the `not targets` branch, name any unmarked domain whose name is in `vm_names(cfg)`
  — already imported, four lines. The report alone, keeping exit 0, carries most of the value; changing
  the exit code is a contract change.

### F-LIFE-06 — both marker docstrings describe a v0.1 that no longer exists
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/marker.py:61-68`, `marker.py:73-74`, `findings.md` §2
- **What:** two false claims about the fields destroy identity depends on. (1) `v` is called "the format
  discriminator" in both the docstring and `findings.md`, but no code path compares it to anything. (2)
  `deployment` says "v0.1 destroy scope stays host-wide, so nothing reads this for a destroy decision
  yet"; `cmd_destroy` filters on it (`cli.py:266-269`), recorded as D36.
- **Why it matters here:** these docstrings are where someone checks whether destroy is host-wide and
  what a version bump gates; the wrong belief on either is data-loss-shaped. The real contract is
  narrower than advertised — the parser takes any `v` with the three required keys and ignores unknown
  ones, so a semantic change to an existing field has no gate. The namespace break (`urn:vcows:1`) is
  the only real discriminator.
- **Evidence:** `grep -rn '\.v\b' --include=*.py orchestrator/` finds only `marker.py:73` and `:110`;
  every other hit is in `tests/`.
- **Fix / cost:** say `v` is provenance, not a runtime check; replace `deployment`'s second paragraph
  with D36's scope rule. One paragraph and two sentences, no code — a version gate with one version to
  compare against is speculative surface.

### F-LIFE-07 — the README's documented deploy invocation works exactly once
- **Severity:** S5 · **Confidence:** high
- **Location:** `README.md:66`, `orchestrator/cli.py:72-80`
- **What:** "Then `deploy /config.yaml --run-dir /runs/lab-a`" is the only deploy invocation given, and
  the second run hits the reused-`--run-dir` crash another agent reports. Two lifecycle notes are mine:
  it lands at `seed.mkdir()` (`cli.py:203`) *after* a full connected preflight, so the operator pays the
  round trip for an errno; and `_run_dir`'s `exist_ok=True` (`cli.py:76`) gives the default path the
  same collision when two runs share a second, `_timestamp()` being second-resolution.
- **Why it matters here:** the documented site workflow is a repeated deploy against a bind-mounted
  `/runs`, so the example changes whatever the code fix turns out to be.
- **Fix / cost:** show the default (`-v ./runs:/runs`, no `--run-dir`) and describe `--run-dir` as the
  one-off it is. One README line.

### F-LIFE-08 — run directories accumulate plaintext `user_data` forever
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:72-80`, `README.md:134-152`
- **What:** nothing ever removes a run directory. Every deploy (refused included) and every destroy
  ("nothing to destroy" included) creates one, and each deploy's `seed/` holds one ISO per created VM
  containing `user_data` verbatim. The README warns they carry secrets and says nothing about lifetime.
- **Why it matters here:** disk is not the cost — a twenty-VM deploy writes about 1.4 MB of ISOs plus a
  12.5 KB `.terraform` and a state file, so a weekly deploy-and-destroy site stays under 200 MB a year.
  The cost is that the SSH keys and passwords readable under `runs/` only accumulate, including for VMs
  destroyed months ago and credentials since rotated. 0700 protects them from other users, not from next
  year.
- **Evidence:** `grep -in 'runs/\|retention\|prune\|clean' README.md` finds nothing about lifetime; no
  code path deletes a run directory.
- **Fix / cost:** a README sentence stating the retention the operator owns, with the `find runs/ -mtime
  +N -delete` that implements it. Three lines; a `--keep` flag belongs with the deferred `prune` verb.

## Checked and sound

* **Per-stage crash residue.** `validate`, `preflight`, `prepare`, `init` and `plan` leave nothing on
  the hypervisor, and the connection closes before the apply, so an interrupted deploy cannot leave a
  half-defined domain. A crash between `apply` and `outputs` leaves marked domains the next deploy SKIPs
  into "nothing to create", exit 0.
* **The partial-apply story holds at scale.** `depends_on = [libvirt_volume.base]` on the seeds
  (`main.tf:81`) does make a partial apply a no-op apply for the independent branch; marked domains plus
  `orphan_volumes` cover both residues. The only gap is the orphan message's asserted cause (F-LIFE-01).
* **Teardown ordering.** Destroy-then-undefine, the undroppable `FLOOR` and the single retry to `FLOOR`
  on `INVALID_ARG` are correct, and the between-calls window really is recoverable by re-running.
  `lookupByUUIDString` failing is treated as already-gone while disks are still resolved — right for a
  resumed teardown.
* **Twenty VMs.** `for_each` scales linearly to 61 resources; the only large upload is the base, once
  per host; seed ISOs measured at 69,632 bytes, so twenty is 1.4 MB. The provider is configured once per
  run (`internal/libvirt/client.go` in the 0.9.8 binary), so parallelism does not open ten SSH sessions
  at the target.
* **The 26 MB plugin cache does not recur.** In a scratch copy of the module against
  `.tools/tofu-mirror`, `du -sb .terraform` is 26,745,773 without `TF_PLUGIN_CACHE_DIR` and 12,505 with
  it — the difference is a symlink at `.../libvirt/0.9.8/linux_amd64`; the image sets it
  (`Containerfile:122`). Know rather than fix: those symlinks point at `/opt/tofu/plugin-cache`, so a
  run directory copied off the host has dangling links.
* **Provider and OpenTofu upgrades are the easy half of N→N+1.** The pin is in `main.tf`, mirror and
  lock are baked into the image, the state is never read back, so a new image has nothing to migrate. A
  `schema_version` bump breaks loudly and by name (`config.py:68`, `tests/test_cli.py:110-116`) — right
  direction; it only means every site config is hand-edited, with no version-1 acceptance window.

## Not checked

* `decide()` in `backends/base.py` — another agent's scope; I relied on its four documented outcomes.
* Anything needing a live hypervisor. No connection was opened; the plugin-cache and seed-ISO
  measurements ran offline in the scratchpad against a copy.
* `container/entrypoint.py` restart behaviour, and what a killed container leaves in `~/.ssh`.

## Deserves its own agent

* **NVRAM varstores are the one object class nothing tracks.** `main.tf:126` writes
  `/var/lib/libvirt/qemu/nvram/<name>_VARS.<fmt>`, outside every pool, so `disks_of` cannot see it and
  `_delete_volume` could not resolve it. Whether `UNDEFINE_NVRAM` removes it in every supported case,
  and what a flag-shed retry to `FLOOR` leaves on RHEL 9 EUS.
* **Provider behaviour at a twenty-key `for_each`.** `main.tf`'s `depends_on` reasoning covers the
  base-volume branch only. Whether one failing `libvirt_domain.vm` leaves the other nineteen's volumes
  written and reachable needs `volume_resource.go` read against that shape.
