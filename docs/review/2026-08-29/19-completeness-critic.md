# Coverage honesty — what nobody looked at

Agent: 19-completeness-critic · Scope: `01–18`, the tree, `45d5b92..HEAD` · 2026-08-29

Seventeen agent files exist (01–16, 18; no `17-verify-severe.md` yet, so no severity
arbitration is reflected below). Coverage of `orchestrator/` is dense — every module
sits in three or more agents' scopes. The gaps are all outside it: the container, the
fixtures, the documents, and the one target platform nobody has.

## High

### G1 — `container/entrypoint.py`: 130 lines, zero tests, no owning agent
No agent had it in scope. Three findings were filed against it from outside, each by
an agent working a different lens: 18's F-SEC-01 (S1 — a newline in `ssh_keyfile`
injects `ProxyCommand`), 06's F-SUPPLY-03 (tracebacks on three malformed configs),
08's F-SILENT-07 (defers to a pre-existing `~/.ssh/config` silently). 12 confirmed no
test imports it — `StrictHostKeyChecking yes → no` passes, and so does a syntax
error; `grep -rn "IdentityFile\|StrictHostKey" tests/` returns nothing. I ran the
image gate (`VCOWS_IMAGE=…`, `220 passed, 15 skipped`): every image test overrides
`--entrypoint`, so the default entrypoint still never executes. It is the whole of
acceptance defect 2's fix — credentials cannot travel in the URI, so this file is the
only thing that gets them to either SSH client — and the only thing keeping host-key
verification on after R-D refused `no_verify=1`. It also runs on `validate`, before
any schema check.
**Closes it:** `tests/test_entrypoint.py`, ~30 lines, ungated — it imports nothing
from libvirt, so call `ssh_config()` against a `tmp_path` HOME, assert the five
directives, then assert a newline in either field is rejected. Plus one agent scoped
to `container/entrypoint.py` + `Containerfile`'s `ENTRYPOINT` + README's `podman run`.

### G2 — the shipped image's `manifest.json` names a commit it does not contain
Nobody checked the built image against the repository. `podman run --entrypoint cat
…:0.1.0.0 /opt/vcows/manifest.json` reports
`"git_sha": "e5d5a2cdd394efd0fd08a3554df7af121fe730a9"`. That commit has no
`container/entrypoint.py` (`git cat-file -e e5d5a2c:container/entrypoint.py` →
absent), yet the image ships one byte-identical to HEAD's, and its `schema.py`
matches HEAD's and differs from e5d5a2c's. The build ran from a dirty tree carrying
`da3f45c`'s changes. R5 exists to answer one question — which build produced this —
and the only artefact that exists answers it wrongly. 07 filed R5 VIOLATED for the
never-copied-into-the-run-dir half; this half is worse, because the file *is* written
and is false. `test_the_build_manifest_records_what_shipped`
(`tests/test_image.py:241`) asserts six manifest keys and not `git_sha`.
**Closes it:** two asserts in that existing test — `git_sha` is 40 hex, and equals
`git rev-parse HEAD` when `git status --porcelain` is empty — plus a build-time guard
recording `-dirty` rather than a clean SHA.

### G3 — RHEL 9 / RHEL 10 behaviour has no owner at all
The brief opens with "the test bed lies in one direction" and no agent was
commissioned to discharge it. Everyone who reached it deferred: 03 (cloud-init
22.1/23.1 on 9.0–9.3 EUS, and the `sysconfig` renderer naming interfaces from the
`ethernets` keys), 04 (F-TOFU-01 — `firmware = "efi"` emitted alongside a pinned
loader; S2, "settling it needs a Rocky 9 host"), 12 (`loader_readonly`, the nvram
path, `q35`, `features.apic = {}`, all Fedora-measured), 09 (every comment in the
tree is a Fedora 44 measurement), 02 and 14 (libvirt error codes from the binding,
not observed). findings.md §6 adds raw `.fd` vs qcow2 varstores and the TPM undefine
bit exercised on the rig only by accident. F-TOFU-01 is the review's only S2 whose
failure is "the domain is built on firmware the operator did not choose" — the
acceptance run's accepted-but-not-honoured shape, on the platform the tool is for.
**Closes it:** not an agent, a machine. One Rocky/RHEL 9 host: define the module's XML
for one pinned-loader VM and one `firmware = "efi"` VM, `virsh dumpxml` both and diff
against the tfvars; boot one guest and capture `cloud-init status --long`, `ip -o
addr`, `nmcli con show`. That settles F-TOFU-01, F-LVOFF-03/-07, G15 and D3 together.

### G4 — `main.tf` is unpinned by any behavioural test and unread against RHEL 9
12's F-TEETH-01 (S1): twelve `main.tf` mutations, twelve survivors, including deleting
`metadata = { xml = … }` — the tool's only identity — and re-introducing three of the
five acceptance defects. 04 reviewed the file against the provider schema and the
Fedora rig. Nothing joins the two. **Closes it:** 12 already named the cheapest S1
remedy in the review — ~60 lines inside the existing `needs_tofu` gate, using `tofu
console` against the golden tfvars to assert values for `metadata.xml`, seed `format`,
`backing_store`, overlay `capacity`, `features`, `nv_ram`, `boot_devices`, `running`.

### G5 — findings.md §2's disk-freshness claim is false, and it authorises the review's worst finding
findings.md:87 — disk paths "are read from the live domain XML … **immediately before
undefining**, which is correct even for a VM whose disks changed after creation." They
are read once, at `preflight.py:137`, into `Existing.disks`; `destroy.py:254` iterates
that snapshot, and the file contains no `XMLDesc` call at all. §3:219 states the true,
weaker version ("at discovery time"). No agent traced the defect to the document that
authorises it. The false half is exactly the premise 16's F-WARN-02 (S1) exploits: the
unbounded `input()` pause, after which a stale UUID misses, `destroy.py:244`'s bare
catch reads "already gone", and the *new* VM's disks are unlinked.
**Closes it:** correct findings.md:87 to "at discovery time", paired with 16's
re-read loop. The regression test is ~10 lines against `fake_libvirt`: a target whose
UUID no longer resolves must never reach `_delete_volume`.

## Medium

### G6 — `docs/spikes/README.md` and `docs/spikes/*` were read by no agent
Five files, ~420 lines, from `55cbfee`. Grepping all seventeen agent files for
"spikes" returns three incidental mentions of A4 and A7. Two claims live only there:
A2's "**Not yet verified:** persistence across a `virtqemud` restart" (it says
`vcows-spike-probe01` was left defined for exactly that test, never run), and A3's
"UNVERIFIED against the real golden artifact" (D3). Against the first, findings.md:109
asserts flatly that the marker survives "libvirtd restart and host reboot". That is
the findings.md claim verified by nothing — not a test, not an agent, not the
acceptance run — and the spike file says so in writing. The marker is the tool's only
identity, and `virtqemud` is socket-activated with an idle timeout on the RHEL
targets, so the restart is routine. **Closes it:** one command on the rig with no
running guests — `systemctl restart virtqemud`, then re-read that probe's metadata.

### G7 — `tests/test_tofu_driver.py`, 250 lines, read by nobody and mutated by nobody
It is the largest test file for `orchestrator/tofu.py`. 12's 40 mutations touched
`cli.py`, `schema.py`, `preflight.py`, `destroy.py`, `render.py`, `prepare.py`,
`marker.py`, `base.py` and `main.tf`; its own "Not checked" names `tofu.py`. 05
reviewed the driver's source, not its tests, so the teeth of the module that runs the
subprocess creating every VM are measured nowhere. **Closes it:** extend 12's method —
mutate `_read_stream`'s `except OSError` to a re-raise, drop `-no-color`, drop
`SHORT_TIMEOUT` from `init`, change `-json-into` to `-json`, record the survivors.

### G8 — the fixtures every unrun test asserts against
Four of six `tests/fixtures/libvirt/*.xml` appear in no agent file
(`domain-old-namespace.xml`, `network-default.xml`, `volume-base-image.xml`,
`volume-dir-entry.xml`); so do `tests/test_qcow2.py`, `tests/test_libvirt_render.py`
and `tests/tofu/main.tf`. 14 found the one fixture defect anybody looked for —
`FakeConnection.storageVolLookupByPath` matching by basename across every pool — while
looking for something else. 12's "Not checked" states the risk exactly: "a fake wrong
the same way as the code hides a defect my mutations cannot see." **Closes it:** one
agent over `tests/fake_libvirt.py`, `tests/fixtures/libvirt/*` and `tests/tofu/main.tf`,
diffing each fixture against real `virsh dumpxml` / `vol-dumpxml` from the rig.

### G9 — the rig gate was read by two agents and executed by none
`tests/test_libvirt_rig.py` is 216 lines and 15 tests, the only thing in the tree
asserting against real libvirt; the brief forbade `VCOWS_RIG_URI`, so its assertions
were reviewed as prose. I ran the other gate instead:
`VCOWS_IMAGE=localhost/vcows-deploy:0.1.0.0 pytest -q` → `220 passed, 15 skipped`.
**Closes it:** run the rig gate once with `VCOWS_RIG_URI` set. Every test enumerates
domains, opens pools and reads leases — nothing defines, starts, undefines or
deletes, so it is safe against the shared rig.

### G10 — `cmd_destroy` builds a deploy-shaped `Discovered`; `_stage_module` copies only top-level `*.tf`
Both raised by 10, never pursued (the derived roster capped at four). The first is the
interesting one: `cmd_destroy` calls the full `preflight`, so at teardown it runs the
pool walk, the orphan-volume refusal and D30's size comparison — meaning 15's
F-XDEP-01 message ("Delete it on the hypervisor and re-run", against the volume every
overlay backs onto) is printed **during a destroy**, to an operator already in a
destructive frame of mind. **Closes it:** a `problems` filter at `cli.py:274` and a
paragraph in §3 on what `destroy` needs from `preflight`.

### G11 — the `--run-dir` / `--user` / bind-mount matrix, untested against real podman
13's F-RUNDIR-06 is one cell; 06's "Not checked" names another — whether podman's
`--passwd` writes an `/etc/passwd` entry for a `--user` UID, which decides both whether
`entrypoint.home()`'s `None` branch is reachable and whether `ssh` aborts with "no user
exists for uid", and so whether README:48's "supported" is true. Nobody ran it,
including me. **Closes it:** `podman run --rm --user 4242 …:0.1.0.0 id`, plus one
deploy with `--run-dir` on a bind mount owned by a different UID.

### G12 — `tests/test_version.py` and its five claimed consumers
Confirmed, and G2 is the live proof. `orchestrator/__init__.py:11` names five consumers
the test "asserts … so they cannot drift": `--version`, the marker's `v`, the image
tag, the OCI label, the build manifest. The file has three tests — the format regex,
the marker, and `pyproject.toml`, which is not on the list. The image tag is asserted
nowhere; label and manifest only behind `VCOWS_IMAGE`. **Closes it:** one ungated
four-line test parsing `ARG VCOWS_VERSION=` out of `Containerfile` against `VERSION`,
plus G2's `git_sha` assertion.

## Low

* **G13 — four result carriers, three lossy.** 16 raised `Outcome`,
  `Discovered.problems`, `Result.diagnostics`, `ConfigError.problems` as "one defect
  or four" and nobody answered. `tofu.py`'s `Result.diagnostics` is unfiled: one
  write, no read — an S4 by the brief's own rule. **Closes it:** a decision in the
  review. §5 argues against unifying them, so "print each at its own consumer" is
  probably right and should be said out loud.
* **G14 — D26, the serial console.** 09 and 12 filed the comment half (S5); whether
  `<log file=…/>` replaces the pty, and who owns the host path it writes to, is left
  open by `docs/archive/acceptance.md` and owned by no scope. **Closes it:** a recorded decision.
* **G15 — cloud-init renames the operator's NICs to `nic0`/`nic1`** (03), a
  guest-visible effect of the `ethernets` keys `prepare.py` chooses, documented nowhere.
  **Closes it:** one README sentence, confirmed during G3's run.
* **G16 — the seed ISO carries no deployment identity** (15): two deployments with one
  VM name give byte-identical seeds apart from `user_data`. **Closes it:** one line in
  `seed_files`, decided with F-XDEP-03's MAC — both one-way doors, settle both or neither.
* **G17 — unread, and correctly so:** `.gitignore`, `.python-version`, `pyproject.toml`
  beyond a licence grep. Named so the coverage claim is exact.

## The eleven commits

Nine are covered. Uncovered: `55cbfee`'s `docs/spikes/README.md` and `docs/spikes/a1–a4`
(G6); `9159255`'s four unread fixtures and unexecuted `test_libvirt_rig.py` (G8, G9);
`c989a89`'s `tests/test_tofu_driver.py` and `tests/tofu/main.tf` (G7, G8); and
`da3f45c`'s `container/entrypoint.py` (G1) — the commit's largest new file, owned by
nobody.

`66adac2`, the docs-only commit `_ORIENTATION` flags as "part of this was wrong,
corrected next", was examined by nobody: 10 verified §3's *config composition* claim
and 07 the decisions, but neither read the ABC signature table that commit rewrote. I
checked it — `config_schema`, `validate`, `connect`, `preflight → Discovered`,
`prepare(cfg, workdir, discovered)`, `render`, `parse_outputs`, `destroy → None` all
match `backends/base.py:246-309`, so it is now correct. §2's disk claim in the same
document is not (G5).

## One "Checked and sound" that did not hold up

08 lists "Reusing a `--run-dir` fails loudly (`seed.mkdir()`/`workdir.mkdir()` have no
`exist_ok`) rather than planning against stale state", and 04 lists the same mechanism
as a guarantee ("Re-apply against non-empty state is prevented by construction"). 05
(F-DRV-01) and 13 (F-RUNDIR-03) file it as S2. Checking `cli.py`: `cmd_destroy` at
`:258` calls `_run_dir` (`exist_ok=True`) then `_record`, and creates no subdirectory
at all — so **destroy reuses a run directory silently and overwrites the earlier
deploy's `run.json`**. 08's bullet is true of deploy and false of destroy, and the
deploy half is the README's documented invocation. It is a defect, not a property.
