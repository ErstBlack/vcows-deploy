# Orientation — what this codebase is and where things are

Read this after `_BRIEF.md`. It exists so that eighteen agents do not each spend
their context rediscovering the same architecture.

## The pipeline

One `config.yaml` describes a **deployment**: a target hypervisor, a golden
qcow2 image, and a list of VMs. Five verbs act on it.

```
validate    offline only. schema + semantic checks. no connection.
preflight   connect → enumerate by marker → decide → report. changes nothing.
deploy      preflight → decide → drop the ones that exist → build seed ISOs →
            render tfvars → tofu init/plan/apply → outputs → inventory.json
destroy     connect → enumerate by marker → filter to this deployment →
            stop, undefine, delete disks. Python and libvirt. No OpenTofu.
version     version, tofu version, build manifest
```

The two halves are deliberately separated. **`preflight` is the only method that
holds a live connection during a deploy**; everything downstream runs on data it
returned, and the connection is closed before the apply begins. That is why
`prepare` takes a `Discovered` record rather than a session — it *cannot* reach
the hypervisor, which is a guarantee rather than a rule.

## Identity is the marker, never the name

Every domain vcows creates carries a JSON payload in its `<metadata>`:

```xml
<vcows xmlns="urn:vcows:1">{"v":"0.1.0.0","deployment":"lab-a","name":"app01","id":"…"}</vcows>
```

`id` is `uuid5(VCOWS_NS, logical_name)`, so it regenerates with no state file. A
renamed VM is still ours and still destroyable. A VM vcows did not create is
never adopted. `destroy` finds its targets by marker and filters to the config's
`deployment`; the OpenTofu state is written to the run directory and **never
read back**.

## Module map

Line counts are current. Nothing here is large; the density is in the
docstrings, which carry the reasoning for almost every decision.

### Core — no libvirt anywhere in the call path

| file | lines | what |
|---|---|---|
| `orchestrator/__init__.py` | 16 | `VERSION`, the single definition |
| `orchestrator/marker.py` | 139 | the marker: serialize, parse, `derive_id`, `VCOWS_NS` |
| `orchestrator/qcow2.py` | 48 | one pure function: `virtual_size`, replacing `qemu-img` |
| `orchestrator/config.py` | 172 | schema composed from the registry; `load` reports every problem at once |
| `orchestrator/backends/base.py` | 310 | the ABC, the records that cross the seam, and **`decide()`** |
| `orchestrator/backends/__init__.py` | 23 | `REGISTRY` |
| `orchestrator/tofu.py` | 246 | the OpenTofu driver: `subprocess.run` + `-json-into` |
| `orchestrator/cli.py` | 403 | five verbs, the run directory, the top-level error handling |

`decide()` in `base.py` is the ownership policy — absent → create, ours → skip,
other deployment → refuse, unmarked name clash → refuse. Its docstring calls it
"the dangerous logic, written once". Everything else trusts it.

### The libvirt backend

| file | lines | half |
|---|---|---|
| `backends/libvirt/schema.py` | 432 | offline — the `target.libvirt` schema (F11, a one-way door), semantic checks, `derive_mac`, `connection_uri` |
| `backends/libvirt/render.py` | 112 | offline, pure — config + `Prepared` → tfvars dict |
| `backends/libvirt/prepare.py` | 135 | offline — builds the cloud-init seed ISO with `pycdlib` |
| `backends/libvirt/preflight.py` | 451 | **connected** — connect, enumerate, parse markers and disks, walk the pool, check addresses |
| `backends/libvirt/destroy.py` | 258 | **connected** — stop, undefine with a version-gated flag mask, delete disks |
| `backends/libvirt/__init__.py` | 91 | the class binding all seven ABC methods |
| `backends/libvirt/tofu/main.tf` | 189 | the static module: base volume, overlay, seed, domain |
| `…/variables.tf` `…/outputs.tf` | 81 / 34 | every value arrives as `.auto.tfvars.json` |

### The container

| file | lines | what |
|---|---|---|
| `Containerfile` | 164 | Rocky 10 base pinned by digest, RPMs only, no pip, no venv |
| `container/entrypoint.py` | 130 | writes `~/.ssh/config` from the config's credentials, then `exec`s |
| `container/manifest.py` | 80 | the R5 build manifest, generated inside the build |
| `container/tofurc` | 18 | filesystem mirror, **no `direct` block** |

### Tests — 3,600 lines, 235 tests

`tests/test_seam.py` is the architectural test: core completes a whole
deploy/destroy cycle with `import libvirt` broken. `tests/fake_backend.py` has no
hypervisor semantics at all; `tests/fake_libvirt.py` deliberately does.
`tests/golden/libvirt.tfvars.json` is compared byte for byte.

Three gates are opt-in and skip with an explicit reason: `VCOWS_RIG_URI` (real
hypervisor), `VCOWS_IMAGE` (the built container), and the presence of
`.tools/tofu-mirror` (the OpenTofu module gates).

## The commits

| | |
|---|---|
| `45d5b92` | Initial Commit — docs only, the baseline for a full diff |
| `55cbfee` | Stage 1 — core, no libvirt in the call path |
| `63c204c` | Stage 2 — the backend's offline half, answering F11 |
| `66adac2` | findings.md §3 realigned — **and part of this was wrong, corrected next** |
| `ced2f7c` | Stage 2.5 — `preflight` carries what it found; `prepare` takes data |
| `9159255` | Stage 3 — the connected half |
| `c989a89` | Stage 4 — the driver and the CLI |
| `f3f12f7` | Stage 5A — the image, proved offline |
| `a74537f` | Stage 5B — footprint measured, plugin cache |
| `e5d5a2c` | two fixes the acceptance run would have failed on |
| `da3f45c` | the acceptance run: five defects, definition of done met |

## What the acceptance run changed, on 2026-08-29

This is the most recently disturbed ground and therefore the most likely to hold
a second defect of the same kind. Full detail in `docs/acceptance.md`.

1. **Two SSH transports, not one.** `preflight` uses `qemu+ssh` (libvirt does not
   recognise `sshcmd` at all); the provider gets `qemu+sshcmd` (its `qemu+ssh`
   dials a hardcoded monolithic socket a split-daemon host lacks, through a
   forward SELinux refuses). One config, two schemes, derived in
   `schema.connection_uri`.
2. **Credentials cannot travel in the URI.** No spelling reaches both clients.
   Both run `ssh`, so `container/entrypoint.py` writes `~/.ssh/config` instead.
3. **The seed volume declared `raw`**; libvirt reports what it detects, so the
   provider's post-apply read disagreed with its own plan and failed *after*
   writing four volumes. Now `iso`.
4. **The module emitted no `<acpi/>`**, so no EFI domain could be defined at all.
   Now `features = { acpi = true, apic = {} }`.
5. **`routes: [{to: default}]`** is a netplan idiom cloud-init does not
   implement. It applied nothing and fell back to DHCP; both guests booted
   healthy on the wrong addresses. Now `0.0.0.0/0`.

Defects 3, 4 and 5 are all the same shape: **the tool emitted a document that
the consumer accepted and then did not honour.** If you are looking for a second
one, look there.

## Where the bodies are likely buried

Not findings — leads, from someone who watched this get built.

* **The run directory holds secrets.** The seed ISOs contain `user_data`
  verbatim and are kept deliberately, so a VM that will not boot can be debugged
  from the media it was given. Mode 0700 is the only thing protecting them.
* **`Discovered.problems` is advisory; the verb decides what is fatal.** Deploy
  treats an ERROR as fatal, destroy prints and proceeds. That asymmetry is
  deliberate (a size mismatch must not block a teardown) and easy to get wrong.
* **The undefine flag mask has an undroppable floor.** Shedding `NVRAM` turns a
  diagnosable flag error into an undiagnosable undefine failure.
* **`pool.refresh(0)` before every volume lookup (D35)** is mandatory, not
  defensive: libvirt's APIs read an in-memory cache, and on the rig three of four
  domains' disks do not resolve without it.
* **`<backingStore>` is never followed.** It is the only thing between destroy
  and the shared golden image, which every other deployment's overlays depend on.
* **The top-level `except Exception` in `cli.main`** exists because §3 forbids a
  shared backend exception hierarchy, so there is nothing narrower to catch.
* **`tofu` runs with `-chdir`**, which resolves relative paths against the module
  directory. Every path handed to it is resolved first, for that reason.
* **Two probe domains on the rig are fixtures**: `vcows-probe02` carries a
  current marker with deployment `spike`; `vcows-spike-probe01` carries a
  superseded namespace and therefore reads as *unmarked*.

## Accepted gaps — already known, not findings unless the analysis is wrong

`findings.md` §2 records these deliberately:

* **Orphan volume on a mid-create crash.** Volumes cannot carry markers.
  Preflight refuses and names the file for the operator to delete. The
  acceptance run exercised this for real.
* **Preflight-then-create is TOCTOU.** Two operators racing get a hard error from
  libvirt's own name uniqueness, not corruption.
* **The base image is never cleaned up.** Shared across deployments; sweeping it
  is a `prune` concern that does not exist yet.
* **`destroy` is scoped to the config's `deployment`** (D36), with other
  deployments reported and skipped. `--all` is deliberately deferred.

Report these only if you can show the recorded analysis is *wrong* — for
instance that the TOCTOU can corrupt rather than error, or that the orphan
refusal has a path that does not fire.
