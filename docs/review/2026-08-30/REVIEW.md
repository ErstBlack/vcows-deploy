# Merge decision — PR #1, `feature/scaffold` → `master`, HEAD `6497f30`

**MERGE.** No confirmed finding in dimensions A–E reaches high or critical: the highest
severity anywhere in this review is medium, and every medium is either a narrow
multi-actor race, a record-keeping gap, a latent second-backend seam, or a missing test
assertion — none of them a wrong answer this branch produces on its own paths today.

Phase 0 stands as evidence: 379 passed / 25 skipped with all 25 skips routed through the
`gate()`/`require()` mechanism (`tests/conftest.py:53-67`), `just lint` and `just typecheck`
green, PR #1 MERGEABLE with all three checks SUCCESS on both platforms.

The 2026-08-29 remediation is real: 104 of 109 checklist items DONE, 1 PARTIAL with a
documented substitution, 2 SUPERSEDED with recorded reasons, 2 NOT DONE (§3). Neither
NOT-DONE item changes this decision; both are issues, and one of them (the missing
`LICENSE`) must close before an external delivery, not before this merge.

---

## 1. BLOCKS THE MERGE

Empty. No dimension A–E finding was confirmed at high or critical severity.

Two arbitrations that could have changed that, stated because they were close:

- **RW-A1** (`orchestrator/backends/libvirt/destroy.py:545`) deletes disks and therefore
  reads like the critical bar. I hold it at medium. The deletion is confined to paths
  matching `{overlay_name(marker.name), seed_name(marker.name)}` for a deployment the
  operator is tearing down (`destroy.py:456-472`), requires a second operator to complete
  a destroy *and* start a deploy inside the first operator's `input()` pause, and the
  alternative — dropping a vanished target's disks — is a guaranteed leak. The defect is
  that the weaker evidence is silent, not that the deletion is unbounded.
- **RW-C1** (`orchestrator/config.py:57`) is false assurance from a schema-enforced field,
  not a wrong computation: nothing in vcows reads `image.sha256`, so nothing downstream is
  corrupted by it. Medium.

---

## 2. MERGE, TRACK AS AN ISSUE

### RW-A1 — a vanished destroy target's disks are deleted on name-pattern evidence alone, silently

`orchestrator/backends/libvirt/destroy.py:514-547`, decided by `_deletable` at `:437-473`.
When `lookupByUUIDString` returns `ERR_NO_DOMAIN` the target is recorded as skipped, but
its **preflight-snapshot** disk paths still enter the delete loop with no live document to
re-read; `_deletable`'s two remaining guards are "no existing domain claims this path" and
a basename match, both of which a volume created after preflight ran satisfies by
construction. Reproduced against `tests/fake_libvirt`: operator A pauses at the confirm
prompt, operator B destroys and re-deploys, B's freshly created `/pool/app01.qcow2` and
`/pool/app01-seed.iso` are unlinked with `out.problems == []` and reported as `destroyed`.
Cheapest honest fix is a non-fatal `Problem` on every delete taken in the vanished branch,
naming that the domain was gone and the path matched by name only; the stronger fix is to
compare the volume's backing store or creation time against what preflight resolved.

### RW-A2 — a destroy that raises records nothing it removed in `run.json`

`orchestrator/cli.py:237` versus `orchestrator/cli.py:501-507`. The structured record with
`destroyed`/`skipped`/`problems` is built only when `destroy()` returns; when it raises —
every teardown with a fatal problem, which is the run with the most to record — `_guard`
writes `outcome: "failed"` and `error=<message>` and nothing else. Reproduced through
`cli.main(["destroy", ...])` with a backend raising a `DestroyError` carrying three
destroyed objects: the resulting `run.json` has no `destroyed` key, no `skipped` key, and
`problems: []`. The run directory is what an air-gapped site ships back (`cli.py:187-194`),
and `_Run`'s own docstring at `cli.py:170-176` says the failure path must write the same
record as the success path. Smallest seam-safe fix: `_destroy` wraps the call, writes
`run.extra["destroyed"]`/`["skipped"]` from `getattr(exc, "outcome", None)`, and re-raises.

### RW-C1 — `image.sha256` is schema-validated, documented, and never verified

`orchestrator/config.py:57` declares `"sha256": {"type": "string", "pattern":
r"^[0-9a-fA-F]{64}\Z"}` under an `additionalProperties: False` schema, and S5 tightened its
anchor with two new tests, all of which read as enforcement. Nothing computes or compares
the digest: the only other `sha256` in the non-doc tree is `container/manifest.py:100`,
which is the provider zip. `_check_disk_capacity` (`orchestrator/backends/libvirt/schema.py:569-583`)
already opens `image.source_qcow2` and reads its qcow2 header — the natural place for the
check — and does not do it. Either verify the digest there (it already degrades to a
WARNING on an unreadable image) or delete the field and let `additionalProperties: False`
say so; a corrupted or substituted golden image currently deploys with no signal.

### RW-E1 — the gate mechanism's demanded-to-failure branch is exercised by no test

`tests/conftest.py:53-67`. `gate()` and `require()` each have two branches; only the
"available" one is ever taken on a green run, because `just test-tofu` sets
`VCOWS_GATES=tofu` on a runner that has tofu. Mutating both functions to always skip leaves
`369 passed, 35 skipped, exit 0` under default, `VCOWS_GATES=tofu` and `VCOWS_GATES=all`
alike — `VCOWS_GATES=all` silently stops meaning anything. `tests/test_gates.py` covers
`demanded()` and AST-scans for stray skips but never calls `gate()`/`require()` with
`available=False`. Roughly twelve lines closes it: assert `gate("tofu", False,
"r").mark.name == "gate_missing"` under a monkeypatched `GATES`, `"skip"` without, and a
`pytester` case for `pytest_runtest_setup`.

### RW-E2 — the skip scanner's banned set misses `skipif`, `xfail` and bare `skip`

`tests/test_gates.py:55`: `banned = {"pytest.skip", "pytest.importorskip",
"pytest.mark.skip"}`. `@pytest.mark.skipif(...)` yields the dotted name `pytest.mark.skipif`
and is not in the set — and `skipif` is the exact idiom `gate()` itself returns
(`tests/conftest.py:53`), so it is the form a developer copying house style writes. A bare
`@pytest.mark.skip` is an `ast.Attribute`, not an `ast.Call`, so the walk never sees it at
all, and `xfail` in either spelling is absent from the set while being worse than a skip.
Nothing in the suite uses any of these today; the fix is the literal set plus checking
decorator `ast.Attribute` nodes.

### RW-E3 — the root disk's `driver.type` is unpinned while the cdrom's is asserted

`tests/libvirt-module.tftest.hcl:145-148` asserts `devices.disks[1].driver.type == "raw"`
for the seed cdrom and `:167-170` asserts `disks[0].driver.discard == "unmap"` for the root
disk, but never `disks[0].driver.type`. Mutating `type = "qcow2"` → `"raw"` on the root
disk in `main.tf` passes `tofu test`, `tofu validate` and the whole Python suite; qemu then
presents the qcow2 container to the guest as a raw disk and every VM in the deployment
fails to boot after a run that reported success. The volume's own format *is* asserted at
`:53-56`, and the two have to agree, which is what makes the omission read as an oversight.
One line beside the `discard` assertion.

### RW-E4 — `overlay_name`/`seed_name` are unpinned, and they are destroy's only per-disk guard

`tests/libvirt-module.tftest.hcl:31-34` pins the domain name against
`var.vms["app01"].domain_name`; nothing pins `libvirt_volume.overlay["app01"].name` against
`overlay_name` or the seed's against `seed_name`, and both survive being replaced with
`each.key`. Those two names are exactly what `orchestrator/backends/libvirt/destroy.py:456-461`
matches on (`owned = {overlay_name(...), seed_name(...)}`), and the domain name — the one
the test does pin — is the one destroy does not use, since discovery is by marker and UUID
(`destroy.py:516`, `preflight.py:174-181`). A module naming the overlay `app01` instead of
`app01.qcow2` deploys cleanly and then makes every teardown refuse every disk with
`DestroyError`, unrecoverable without hand-deleting on the hypervisor. Two lines beside the
domain-name assertion.

### RW-E5 — of the config values reaching the domain, only `disk_bytes` is asserted

`tests/libvirt-module.tftest.hcl:45-48` pins `overlay.capacity ==
var.vms["app01"].disk_bytes`. Replacing `vcpu`, `memory`, `memory_unit`, `type_machine`,
`type_arch`, `os.type`, `pool`, `os.loader`, nic `model` and nic `source.network` with
constants in `main.tf` all pass the gate. Two are worth closing first: `pool = var.pool` →
`"default"` puts every volume in a pool the config never named and preflight never checked,
and `memory_unit = "MiB"` → `"KiB"` is a one-token edit that gives every domain 4096 KiB
and stops it starting. A `for`-comprehension over `var.vms` asserting the five scalar
passthroughs covers most of this in about eight lines, and would also cover `app02`, which
today is read by four assertions out of thirty-eight.

### RW-B3 — the `ok` record is written after `inventory.json` and after a call that can raise

`orchestrator/cli.py:378`. `tofu.version(workdir)` is evaluated as an argument to
`_record`, so the subprocess runs *between* `_write_json(run.path / "inventory.json", ...)`
and the `ok` record; it can raise `TofuError` (`tofu.py:279`, `tofu.py:98`),
`TimeoutExpired` at 120 s, or `JSONDecodeError`. Any of those reaches `_guard`, which
writes `outcome: "failed"` over a deploy whose apply succeeded and whose `inventory.json` is
already on disk — measured: `rc=1`, `outcome=failed`, `inventory exists=True` with two VMs.
Compute the version before writing `inventory.json`, or tolerate its failure the way
`_print_manifest` tolerates a broken manifest.

### RW-B4 — `tofu_warnings` is assigned only after apply, so no failure record carries them

`orchestrator/cli.py:358`. `_deploy` deliberately attaches `problems` to the `_Run` at
`:302-303` with the reason spelled out — "as soon as they exist, so that every record from
here on carries them, including the failure one" — and then does not follow that rule for
the tofu warnings. Measured with `init` and `plan` each returning a warning diagnostic and
`apply` raising: the record has no `tofu_warnings` key at all. `Result.warnings`' own
docstring (`orchestrator/tofu.py:80-84`) says the warnings exist so the run directory can
record them, "the copy that outlives the terminal", and the failed run is where that copy
matters most. Accumulate after each of `init`, `plan` and `apply` rather than once after
all three.

### RW-B5 — `--run-dir` on an existing regular file gives the raw `FileExistsError`

`orchestrator/cli.py:124`. `_run_dir` calls `path.mkdir(parents=True, exist_ok=True)`
before it can classify anything, and `exist_ok=True` suppresses `FileExistsError` only for
an existing directory; for a regular file it propagates to `main`'s catch-all, printing
`error: FileExistsError: [Errno 17] File exists: '.../notadir'`. `UsageError`'s docstring
twelve lines above at `cli.py:64-67` names that exact output as the thing it was added to
replace. Two lines: `if path.exists() and not path.is_dir(): raise UsageError(...)` before
the `mkdir`.

### RW-D1 / ledger 2.1 — `validate` writes `~/.ssh/config`, contradicting "nothing is written"

`orchestrator/cli.py:245` documents `cmd_validate` as "Offline only. No connection is
opened and nothing is written." Inside the image that is false: `container/entrypoint.py:199`
calls `install()` unconditionally for every verb, so `vcows validate` writes
`~/.ssh/config` before any schema check runs. Checklist item 2.1 asked for a *decision* on
this and no decision exists in `container/entrypoint.py`, `docs/findings.md`, `README.md`,
or the S1 commit; the comments at `entrypoint.py:40-44` and `tests/test_entrypoint.py:161`
acknowledge the ordering without resolving it. Either move `install()` behind the verbs
that connect, or amend the `cmd_validate` docstring to say what the image actually does.

### RW-C2 — a property test asserting an injectivity claim Hypothesis can never falsify

`tests/test_properties.py:61-71` asserts `(first.id == second.id) == (a == b)` over
`st.text()` pairs. `derive_id` is `uuid5(VCOWS_NS, f"{deployment}/{name}")`
(`orchestrator/backends/libvirt/marker.py:168`) with an unescaped separator, so
`Marker.for_vm("b/c", "a")` and `Marker.for_vm("c", "a/b")` produce the same id from
different inputs and the stated biconditional is false. Hypothesis draws the two pairs
independently and will never construct the colliding pair, so the test passes on every run
while reading as a proof that the S3 deployment/name separation is injective. The collision
is unreachable from a validated config (`DEPLOYMENT_PATTERN` at `orchestrator/config.py:39`
and `NAME_PATTERN` at `schema.py:44` both forbid `/`), so this is a test-scope defect —
bound the strategies to those patterns, or assert the weaker true property.

### RW-C3 — the CIDR property strategy collapses to two prefix lengths and never a host address

`tests/test_properties.py:74-87`. The docstring claims "anything the stdlib will render,
the parser will read back identically", but the strategy as written generates only /8 and
/120 and never a host address, so the round-trip it proves is far narrower than the one it
states. Widen the prefix strategy across the legal range for each family and include host
addresses, or narrow the docstring to what is actually covered.

### RW-E6 — `test_gates_is_parsed_without_whitespace_stripping` cannot fail

`tests/test_gates.py:99-103`. The docstring documents that `VCOWS_GATES` splits on `,`
without stripping — so `"tofu, image"` demands a gate named `" image"` that does not exist
— and explains that both CI files are written without spaces because of it. The body is
`assert isinstance(GATES, set)`, and `GATES` is a set comprehension at
`tests/conftest.py:37`, so the assertion is unfalsifiable. `assert demanded(" tofu") is
False` is the assertion the docstring describes.

### RW-E7 — nothing pins that plan and apply run with no timeout (D42)

`orchestrator/tofu.py:176`: `proc.wait(timeout=SHORT_TIMEOUT if cmd == "init" else None)`.
Replacing that with an unconditional `timeout=SHORT_TIMEOUT` passes the entire suite; the
`except BaseException` below would then kill tofu 120 s into an apply, in the middle of the
multi-GB `vol-upload` that has no resume — the specific thing `SHORT_TIMEOUT`'s docstring
says must never be put on a clock. `tests/test_tofu_driver.py` already pins thirteen
properties of the child invocation and `Stubborn.wait` already records its `timeout`
argument, so asserting `timeout=None` for apply and `120` for init is about six lines.

### RW-E8 — a property test whose universal is false for inputs containing `/`

`tests/test_properties.py:66`. Same root cause as RW-C2 and reported independently by
dimension E's mutation sweep: the property is quantified over all strings but only ever
sees the inputs where it holds. Track with RW-C2 and fix both with one change to the
strategies.

### RW-B1 — module/asked reconciliation compares counts, not names (verification split 1/2)

`orchestrator/cli.py:363` raises when `len(inventory.vms) != len(creating)`, while the
message it builds computes `set(creating) - set(inventory.vms)` and carries an
`or 'names differ'` fallback — the intent is a set comparison and the condition is a length
comparison. Measured with a two-VM config and `tofu.outputs` stubbed to return
`{"app01": {}, "ghost": {}}`: exit 0, `outcome: "ok"`, and `run.json` and `inventory.json`
in the same directory disagreeing about which VMs exist. **Arbitration:** verification
refuted this 1/2 on reachability and I agree it is unreachable through the libvirt backend
today (`orchestrator/backends/libvirt/tofu/outputs.tf:8` keys `vms` off
`libvirt_domain.vm`, whose `for_each` is `var.vms`), so it is not a shipped defect — but
the one-line fix `if set(inventory.vms) != set(creating):` is worth taking, because this
seam exists for the second backend and `Existing.name`'s docstring (`base.py:69-77`) warns
about exactly the namespaced-name case that triggers it.

### RW-B2 — core never reads `Outcome.failed` (verification split 0/2)

`orchestrator/cli.py:503` records `"partial" if out.skipped else "ok"` and never consults
`Outcome.failed` (`orchestrator/backends/base.py:186-188`); `grep -rn "\.failed\b"
--include=*.py` finds one consumer in the whole tree, the libvirt backend's own
`destroy.py:549`. A backend that takes `Backend.destroy`'s docstring at its word
(`base.py:426-427`: a backend is free to return rather than raise) and returns a fatal
`Problem` with an empty `skipped` gets `outcome: "ok"`, the success line and exit 0 —
measured. **Arbitration:** verification refuted this 0/2 and I agree it is not reachable at
HEAD, since the only backend that exists raises on `out.failed`. I keep it as an issue
because `Outcome`'s own docstring (`base.py:163-164`) says a returned `Outcome` "without its
consumer reading it reproduces that defect exactly", and the three-line fix removes the
trap before the second backend finds it.

### RW-G1 — the image workflow's path filter omits the CVE baseline and the gate's own machinery

`.github/workflows/image.yml:21-28`, mirrored at `.gitlab-ci.yml:95-102`. The filter lists
`Containerfile`, `.containerignore`, `container/**`, the tofu module, the provider lock,
`licenses/**` and `scripts/image-*.sh`, and omits `docs/cve-baseline.json` and the image
gate's own test machinery. A PR that edits the baseline — adding an accepted CVE — or the
gate's fixtures therefore does not run the job that would exercise the change. Add the
baseline and the gate's paths to both filters.

### RW-G2 — the scan gate passes green when trivy reports zero vulnerabilities of any kind

`scripts/image-scan.sh:65` and `:83-91`. The gate asserts `found ⊆ accepted` and never
asserts that anything was found, so a scan that analysed nothing — a broken DB download, a
changed report schema, a `jq` path that stops matching — produces an empty `found`, an
empty `comm -13`, and `log "no findings outside the baseline"`. Reproduced with the exact
pipeline against the real 99-id baseline. Add a floor: fail when `found` is empty, or when
the report contains no `Results` entry for the image's own layer.

### RW-G3 — a modified Containerfile still records a clean 40-hex git SHA in the manifest

`scripts/image-build.sh:39-45`. `ship=(orchestrator container licenses
"docs/provider-${provider}.lock.hcl")` is the set of paths `git status --porcelain` is asked
about, and it is complete for the seven `COPY` lines — but the `Containerfile` itself is not
in it, and it decides the base image and digest, the OpenTofu version and RPM digest, the
provider version and digest, the whole `dnf install` list, and every OCI label. An image
built from an edited Containerfile records an unqualified SHA and no `-dirty` suffix, so the
manifest names a commit that does not describe the image. Add `Containerfile` and
`.containerignore` to `ship`.

### RW-G4 — the "workflows carry no logic" assertion has three bypasses

`scripts/lint.sh:34-77`. The assertion is the only thing keeping the claim at `justfile:3-6`,
`.github/workflows/ci.yml:1-4` and `.gitlab-ci.yml:6-8` true, and it does parse YAML rather
than grep — but it misses `.yaml`-suffixed workflow files, does not inspect `uses:` steps at
all, and does not catch same-line chaining such as `run: just check || true`. Verified by
running the embedded Python verbatim against a synthetic workflow directory containing all
three. Widen the glob to `*.yaml`, reject `uses:` outside an allowlist, and reject `||`/`&&`
inside a `run:`.

### RW-G5 — `image_tag()` swallows `containerfile_arg`'s `die` and returns an empty version tag

`scripts/lib.sh:58-60`. `containerfile_arg` is built to fail loudly (`:45-52`), but its
`die`'s `exit 1` runs inside a command substitution, which exits only the subshell; the
substitution is an argument to `printf`, so the enclosing status is `printf`'s 0 and
`set -e` never fires. Reproduced with the function bodies verbatim against a `$REPO` with no
Containerfile: the caller gets `localhost/vcows-deploy:` with an empty version. Assign the
substitution to a local and test it, or `local v; v="$(containerfile_arg ...)" || exit`.

### RW-G6 — any system copy of a tool beats the pinned digest, at any version, unreported

`scripts/install-tools.sh:88-93`. The early return is guarded by `have "$tool"`, which is
`command -v` and says nothing about version, and it applies to all seven tools rather than
the two the comment names (`just` from EPEL, `tofu`). On any machine with a distro `tofu`,
`just`, `hadolint`, `trivy`, `syft` or `cosign`, the pinned versions and SHA256 digests at
`:25-44` are advisory; the log line prints the path but not the version, so the divergence
is invisible in CI output. Print the found version beside the path and warn when it differs
from the pin.

### RW-F1 — the CI migration runbook tells you to schedule a mutation job that does not exist

`docs/ci.md:145-146` instructs the reader to create two pipeline schedules, "monthly with
`REBUILD_SCAN=1`, weekly with `MUTANTS=1`". No job in `.gitlab-ci.yml`, `.github/workflows/*`
or anywhere else reads `$MUTANTS`, and the same document at `:31-33` says there is
deliberately no mutation-testing job — a position `justfile:94`, `pyproject.toml:106` and
`.github/workflows/scheduled.yml:3` all repeat. Drop the `MUTANTS` schedule from the runbook
or restore it when the mutmut baseline is fixed.

### RW-F2 — `check` and `tofu` are documented as running on every push; GitHub runs them on master only

`docs/ci.md:26-27` says both jobs run on "every push and PR/MR". `.github/workflows/ci.yml`
scopes its `push` trigger to `branches: [master]`, so a push to a feature branch runs
neither job, and `workflow_dispatch` (added in `cb52cec`) is the only branch-side trigger —
which the table does not mention. Correct the table and add the `workflow_dispatch` row.

### RW-F5 — the README's `install-tools.sh` tool list omits cosign

`README.md:276` lists the pinned downloads as "uv, tofu, just, hadolint, trivy, syft".
`scripts/install-tools.sh:30` and `:145` also install `cosign`, added by `6497f30`, which
touched `install-tools.sh` and the `justfile` and not the README. `justfile:28` carries the
same stale list.

### RW-F6 — "The libvirt backend: seven methods" undercounts by one

`orchestrator/backends/libvirt/__init__.py:1`. `LibvirtBackend` implements eight:
`config_schema`, `validate`, `connect`, `preflight`, `destroy`, `prepare`, `render`,
`parse_outputs`, matching the eight `@abstractmethod`s at `orchestrator/backends/base.py:356-427`.
The next two sentences ("four of them delegate… the three that hold a connection…") sum to
seven, so `parse_outputs` is the one dropped. Predates this diff.

### RW-F3 — `pycdlib` is listed as satisfied "because it is in the dev group"; it is a runtime dependency

`docs/ci.md:51`. `pycdlib>=1.16` is in `[project].dependencies` (`pyproject.toml:38`), not
`[dependency-groups].dev` (`:45-57`). The gate is genuinely satisfied in CI — via
`uv pip install -e .` inside `just dev-env` — so only the stated reason is wrong. Nit.

### RW-F4 — "the full suite is 390 passed, 0 skipped" is now stale (verification refuted 0/1)

`docs/findings.md:404`. The tracked suite collects 404 tests at HEAD; the sentence was
written at `cfa3044` and `583b655`, `0132fd2` and `2c90b02` added tests afterwards.
**Arbitration:** verification refuted this and I agree the sentence is not a false claim —
it is a dated record of a specific run ("the rig gate ran for the first time on 2026-08-29
… with `VCOWS_GATES=all` and the rig and image gates both supplied"), and dated records are
allowed to go stale. Logged as a nit only because a reader may take the number as current;
if it is touched at all, date it rather than update it.

### Mutation testing: mutmut's baseline does not complete

`pyproject.toml:74-85` configures `source_paths = ["orchestrator", "container"]` and
`justfile:91-96` documents that `just mutants` "does not currently complete" because
mutmut's clean baseline fails on its `sys.path` inside the copied tree. Deferred
post-merge by decision, not re-argued here. It is the reason dimension E measured mutation
coverage by hand (53 module mutations, 29 Python) rather than mechanically, and the reason
`cli.py`, `destroy.py`, `preflight.py`, `schema.py` and `render.py` were not re-swept.

---

## 3. RECONCILED LEDGER

`docs/review/2026-08-29/2026-08-29-remediation-checklist.md`, sessions S1–S12, verified
against `da3f45c..6497f30`. Reports: `/home/ssullivan/vcows-deploy/docs/review/2026-08-30/ledger/s1-s6.md`
and `/home/ssullivan/vcows-deploy/docs/review/2026-08-30/ledger/s7-s12.md`.

| range | items | DONE | PARTIAL | NOT DONE | SUPERSEDED |
|---|---|---|---|---|---|
| S1–S6 | 51 | 47 | 1 | 1 | 2 |
| S7–S12 | 58 | 57 | 0 | 1 | 0 |
| **total** | **109** | **104** | **1** | **2** | **2** |

One overclaim was filed, against item 2.1 (below).

### NOT DONE — 2.1, decide whether `entrypoint.install()` should still run on `validate`

`container/entrypoint.py:198-203`, `orchestrator/cli.py:245`. `main()` calls `install()`
unconditionally for every verb, so `~/.ssh/config` is still written during `validate`, and
`cmd_validate`'s docstring still says "No connection is opened and nothing is written." No
decision is recorded in `entrypoint.py`, `docs/findings.md`, `README.md` or the S1 commit
message; `entrypoint.py:40-44` and `tests/test_entrypoint.py:161` acknowledge the ordering
without deciding it. **Overclaim:** commit `df60f74` presents S1 as landed while covering
only two of item 2.1's three bullets. Tracked as RW-D1. **Does not change the merge
decision** — the write is a 0600 SSH config the entrypoint owns, not a destructive or
credential-leaking action, and the defect is a docstring that contradicts the image.

### NOT DONE — S8, root `LICENSE`, `project.license`, and the identifier in `IMAGE_LICENSES`

No `LICENSE` or `COPYING` in the repo root (`git ls-files` shows only
`licenses/dmacvicar-libvirt/LICENSE`); `pyproject.toml:5-9` declares no `license`;
`Containerfile:78` `IMAGE_LICENSES` carries no project identifier. All three halves are
absent. Commit `2e41112` states the omission is deliberate and calls the identifier "an
open decision, not an oversight", and `docs/research/tooling-2026-08-30.md:98-109` re-files it as an
open finding — so it is recorded, but the item asked for the file and the fields, not for a
decision. **Does not change the merge decision** for an internal merge; it must close
before any external delivery of the image or the tarball.

### PARTIAL — 2.2, re-read each target's `XMLDesc` after `_confirm`

`orchestrator/backends/libvirt/destroy.py:404-434` and `:535-538`; gap at `:518-533` and
`:545-547`. The marker half landed: `_reverify` re-reads `XMLDesc` after `_confirm`, drops
the target on a marker change or an unreadable document, and `destroy` acts on
`disks_of(root)` from the fresh read. The UUID half did not: a target whose lookup returns
`ERR_NO_DOMAIN` is recorded as skipped and its preflight-snapshot disks still enter the
delete loop. The deviation is deliberate and documented at `:530-533` and
`docs/findings.md:87`, and is mitigated by `_claimed_elsewhere` and `_deletable` rather than
by dropping the target. This is the same gap as RW-A1; the residual is the silence, not the
substitution.

### SUPERSEDED — 2.2's regression test

`tests/test_libvirt_destroy.py:159-172` asserts the opposite of what the checklist asked
for, and legitimately: the crash-window resume was preserved on purpose (commit `df60f74`
— "a domain that is gone still has its disks collected, which is what makes a teardown
interrupted between undefine and delete finishable by re-running"), so the test as written
could not be added. The two guards it was replaced by are stronger than a UUID check and
are themselves tested at `:347-366` and `:368-383`, and the substitution is recorded in
`docs/findings.md:87`.

### SUPERSEDED — S2's "print `Result.diagnostics` warning-severity entries"

`orchestrator/tofu.py:77-84`, `orchestrator/cli.py:355-360`, `docs/findings.md:305-308`.
Recorded into `run.json` as `tofu_warnings` rather than printed, because `_run` inherits
stdout and OpenTofu has already rendered them live; re-printing would duplicate. The
deviation is stated in the `Result.warnings` docstring, in findings.md, and explicitly in
commit `4cc9d35`. The defect the item names — nothing reads them — is closed. (RW-B4 is the
residual: the recording misses the failure path.)

---

## 4. ACCEPTED AT MERGE, VERIFIED ELSEWHERE

### Knowingly shipped unverified

**2.15 — `<os firmware='efi'>` beside a pinned loader, on old libvirt.**
`orchestrator/backends/libvirt/tofu/main.tf:114-125` emits `firmware = "efi"` *and* a pinned
`loader`/`loader_format`/`loader_readonly` together. libvirt 12.0.0 honours the pin exactly
(measured 2026-08-29: `app02` came back with its configured `OVMF_CODE_4M.qcow2` and named
template, `secure-boot` and `enrolled-keys` both `no`). What is unverified is whether
libvirt 8.0.0 / 8.5.0 on RHEL 9.0/9.1 EUS lets autoselection *override* the pin. Work order:
`docs/rhel9-target.md:47-60` (C1), `virsh define` plus `virsh dumpxml` on a box that only
has libvirt installed. **Failure mode:** a VM boots from a firmware the config did not
choose, most visibly with secure boot enrolled when the config said no; the module would
have to stop emitting `firmware = "efi"` whenever a loader is set, and 2.15 becomes a schema
fix. Related and equally unrun: the raw `.fd` varstore branch of `main.tf:133`
(`docs/rhel9-target.md:62-70`, C2), never rendered against a real `.fd` template because
this rig ships only qcow2.

**D3 — the real golden artifact.** Both runs to date used the stock
`Rocky-9-GenericCloud-Base` stand-in, so `cloud-init` and `growpart` behaviour is confirmed
for that image and nothing else (`docs/rhel9-target.md:157-161`). No hypervisor time
substitutes for the artifact. **Failure mode:** the delivered golden image lacks or
differently configures cloud-init or growpart, and VMs come up without their configured
addresses or without a grown root filesystem — the deploy reports success either way.

**Cloud-init 22.1 / 23.1 and the `sysconfig` renderer, RHEL 9.0–9.3.**
`docs/rhel9-target.md:91-105` (C5). vcows writes network-config v2 keyed on `nic0`/`nic1`,
matched by MAC, with the default route as `0.0.0.0/0`; old cloud-init on RHEL renders
through `sysconfig` rather than netplan and that path has never run. This is the same shape
as the acceptance run's worst defect: cloud-init accepted the document, threw inside its own
normaliser, applied nothing, fell back to DHCP, and both guests came up healthy on addresses
nobody asked for with `cloud-init status: done`. **Failure mode:** a silently wrong network
— the check is `ip -4 addr` inside the guest matching `configured_address` exactly and the
interfaces actually being named `nic0`/`nic1`, not "did it boot". This one is a download
rather than a hypervisor and `docs/rhel9-target.md:93` says to schedule it first.

### What this review did not read or could not run

- **No live libvirt and no rig.** Every dimension-A reproduction is against
  `tests/fake_libvirt`; the claim that `virDomainLookupByUUID` reaches only `NO_DOMAIN` and
  `ACCESS_DENIED` server-side is inherited from the 2026-08-29 review, not re-observed. The
  rig gate (15 tests) and image gate (10) skipped. The new network/broadcast and firmware
  schema rules were checked against `schema.validate` only, not against what libvirt accepts.
  `Outcome` was exercised only through the fake backend, so RW-B2's "latent for libvirt"
  rests on reading `destroy.py:549` rather than running it.
- **No built image and no live scan.** Dimension G traced the scan and image-gate findings
  through the scripts' logic with synthetic trivy JSON; `docs/cve-baseline.json`'s per-CVE
  rationales are unchecked. The seven pinned SHA256 digests in `scripts/install-tools.sh`
  were not verified against upstream checksum files and no release-asset URL was resolved.
  `.gitlab-ci.yml` has never been executed and its runner executor is unknown, so its
  `image:`/`tags:` interaction is unverified. GitHub branch-protection settings — whether
  `image.yml` is a required check — were not inspected.
- **No rootless-podman matrix.** The `--run-dir` / `--user` / bind-mount combination the
  2026-08-29 review flagged as deserving its own agent still has not been run.
- **Provider internals not in tree.** The go-libvirt `sshcmd` dialer's argv construction was
  not verified, so whether an unchecked URI username beginning with `-o` reaches `ssh` as an
  option is neither confirmed nor ruled out (the previous review left the username
  deliberately unconstrained — `18-security-adversary` F-SEC-04). The dmacvicar/libvirt
  0.9.8 provider was not executed, so an empty-scheme `//host/path` in `source_qcow2` — which
  passes the `^/` pattern — was reasoned to be a local read on standard Go URL semantics
  rather than observed. Injection, `source_qcow2` scheme, and URI-password vectors were
  verified against OpenSSH 9.9p1 and libvirt 11.10.0 on this host.
- **Not mutation-tested.** `orchestrator/cli.py`, `destroy.py`, `preflight.py`, `schema.py`,
  `render.py`, and the +376/+334/+266 lines in `tests/test_cli.py`,
  `tests/test_libvirt_destroy.py`, `tests/test_libvirt_preflight.py` — read for vacuous
  assertions, none found, but not measured. `tests/test_libvirt_rig.py` and
  `tests/test_image.py` bodies were read, not executed.
- **Not read.** `orchestrator/backends/libvirt/tofu/` beyond `main.tf`/`outputs.tf` assertions;
  `preflight.py` outside `address_conflicts`, `_network_claims`, `connect` and the
  destructive halves; `cli.py` outside the destroy and reporting paths for dimension A;
  `docs/archive/orchestrator-architecture.md` in full, `docs/research/tooling-2026-08-29.md`,
  `docs/research/future-backends.md`, `docs/spikes/README.md`; the nineteen agent reports under
  `docs/review/2026-08-29/` beyond the rows and references cited.
