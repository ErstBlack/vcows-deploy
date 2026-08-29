# The backend seam, walked with a second backend — review

Agent: 10-seam-second-backend · Scope: `backends/base.py`, `backends/__init__.py`,
`config.py`, `tests/fake_backend.py`, `tests/test_seam.py`, `docs/future-backends.md`,
with `cli.py` and `tofu.py` read for coupling · Date: 2026-08-29

## Summary

* **The claim is false by one core file, and it fires on backend two, not backend
  four.** `config.py`'s `IMAGE_SCHEMA` is libvirt-shaped, required and closed, so no
  backend whose artifact is not a qcow2 plus a storage-volume name can load a config
  without editing it. Nothing in core reads `image`; only libvirt does.
* **Touch points now: 9 files across 4 layers**, of which **2 are core Python** —
  `config.py` always, `marker.py` for any text-field backend (table below).
* `Discovered.artifacts` **is** genuinely opaque to core on every path, and the
  ownership policy **does** survive a non-UUID identity — `decide()` never reads
  `Existing.id` except to print it. But two pieces of libvirt semantics do live inside
  `decide()`: the unmarked-name-clash rule assumes hypervisor name == logical name, and
  `by_logical` silently collapses two VMs carrying the same marker.
* The seam test is stronger than expected — `test_cli.py` drives `main()` against the
  fake backend and a real `tofu`. What it cannot prove is a backend with a *different
  artifact model*: both seam configs carry `source_qcow2`, because core requires it.

## Findings

### F-SEAM-01 — the unmarked-name-clash refusal silently stops firing off libvirt
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/base.py:163-167`, `:176-178`, `:208-219`
- **What:** `decide()` matches the config's *logical* names against `Existing.name`, the
  *hypervisor* name. That holds only because libvirt's render names the domain after the
  logical name; a backend that prefixes or namespaces (`lab-a-app01`, a vSphere folder
  path) never trips the check. Its stated justification — "libvirt would reject the
  duplicate itself" — is hypervisor-specific too: vSphere names are unique only within a
  folder, Proxmox keys on vmid and does not require a unique name at all.
- **Why it matters here:** the one core safety check whose mechanism is not
  backend-neutral, inside the function whose docstring calls itself "the dangerous logic,
  written once". An author cannot implement the refusal wrongly, but can make it a no-op
  by choosing a naming scheme, unwarned.
- **Evidence:** `decide(["app01"], [Existing(name="lab-a-app01", id="vm-300",
  marker=None)], "lab-a")` → `[('app01', 'create', 'does not exist')]`. No refusal.
- **Fix / cost:** one sentence on `Existing.name` — core compares it against the
  config's logical name, so a backend that transforms names must return the transformed
  form and accept that the clash check is then meaningless. A name-mapping method would
  be a speculative eighth abstract method for a backend that does not exist.

### F-SEAM-02 — two VMs carrying one marker collapse, and the report names the wrong one
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/backends/base.py:173-175`
- **What:** `by_logical` is a dict comprehension keyed on `marker.name`. Two VMs with the
  same marker payload — a clone — collapse to whichever came last in enumeration. The
  other is invisible: not decided on, and not warned about, since the loop at `:224` only
  warns for markers *not* in `wanted`.
- **Why it matters here:** on libvirt this needs `virt-clone`, which does copy
  `<metadata>`. On vSphere and Proxmox cloning is the normal provisioning idiom and the
  annotation/description travels with the clone by default. `cmd_destroy` does not use
  `decide()`, so destroy tears down both while preflight reported one: the operator is
  told about a VM they did not clone and not about the one they did.
- **Evidence:** two `Existing` records sharing `Marker.for_vm("app01","lab-a")`, named
  `app01` and `app01-clone`, give
  `[('app01','skip',"exists as 'app01-clone' (not compared)")]` and an empty problems
  list. The original `app01` appears nowhere in the output.
- **Fix / cost:** group rather than overwrite in `by_logical`, and emit an ERROR
  `Problem` when a logical name resolves to more than one VM. About eight lines inside
  `decide()`, no new record type. Justified because the current behaviour picks one
  arbitrarily and reports it as fact.

### F-SEAM-03 — core's `IMAGE_SCHEMA` is libvirt semantics, so backend two edits a core file
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/config.py:36-49`, `:78`; claims at `config.py:5-6`,
  `backends/base.py:3-4`, `docs/findings.md` §3 "Config composition"
- **What:** `image` is required at document level, requires `source_qcow2` and
  `base_volume_name`, and is `additionalProperties: false`. Every consumer of those
  fields is inside the libvirt backend (`preflight.py:261-262`, `render.py:71`,
  `schema.py:403`); core reads neither. A vSphere backend needs an OVA or a
  content-library name; Proxmox needs neither field. `config.py`'s own docstring says
  "Adding a backend adds a schema file inside its own package and touches nothing here",
  which is not true of this block. `vms` was deliberately left loose for exactly this
  reason (`config.py:8-13`); `image` was not.
- **Why it matters here:** the architecture's central claim, and the *first* thing a
  second backend hits — at config load, before any hypervisor code exists.
- **Evidence:** `validate()` with a `vsphere`-named fake backend and
  `image: {ova_source, content_library}` returns three errors, all from core:
  ```
  error [image]: Additional properties are not allowed ('content_library', 'ova_source' were unexpected)
  error [image]: 'source_qcow2' is a required property
  error [image]: 'base_volume_name' is a required property
  ```
  Both seam configs (`test_seam.py:34-36`, `test_cli.py:37-39`) carry the qcow2 block
  for a backend that by its own docstring has "no hypervisor semantics whatsoever" and
  never reads it. That is the tell.
- **Fix / cost:** documentation, not code — three sentences. Correct §3 and the two
  docstrings to say `image` is the one core block a second backend must open, and why
  it was left there: one backend, and a per-backend `image` would have been a second
  composition point for no benefit today. Moving it under `target.<backend>` is a
  schema break and a one-way door for hand-authored configs; it buys nothing until a
  second backend exists.

### F-SEAM-04 — `to_text_field` has a writer, no reader, and no caller
- **Severity:** S4 · **Confidence:** high
- **Location:** `orchestrator/marker.py:44-47`, `:131-134`
- **What:** `TEXT_FIELD_PREFIX` and `to_text_field()` exist for the vSphere/Proxmox
  free-text placement. There is no `from_text_field()`, while `marker.py:56` claims "one
  canonical payload, one serializer, one parser, every backend".
- **Why it matters here:** speculative surface by §3's own rule, and the *less* useful
  half — the hard part of the text form is the reader, finding the line among human-written
  text sharing the field. Backend two writes that itself, the duplication the "one parser"
  promise exists to prevent.
- **Evidence:** `grep -rn "TEXT_FIELD_PREFIX\|to_text_field" --include=*.py .` → three
  hits in `marker.py`, one in `tests/test_marker.py:104-108`, which parses by hand with
  `removeprefix`. No production caller.
- **Fix / cost:** pick one — delete both (four lines) and let backend two add the pair
  together, or amend `marker.py:56` (one line) to say the reader is still owed. Adding
  `from_text_field()` now would be more speculative surface, not less.

### F-SEAM-05 — the seam ends at the Python boundary; the image names libvirt three times
- **Severity:** S5 · **Confidence:** high
- **Location:** `Containerfile:101-102`, `:129-130`; `container/entrypoint.py:96`
- **What:** the image copies the committed lock into
  `orchestrator/backends/libvirt/tofu/`, warms the plugin cache from that same hardcoded
  path, and the entrypoint reads `cfg["target"]["libvirt"]` to write `~/.ssh/config`.
  Backend two edits all three. §3's "no edit to any core file" is scoped to
  `orchestrator/`, and nothing says so.
- **Why it matters here:** the deliverable is the image, not the package. A backend not
  wired into the Containerfile ships a container that cannot `init` its module offline —
  the air-gap failure, at a site, with no useful message. A Proxmox backend reaching the
  host over SSH gets no `~/.ssh/config` and reproduces the acceptance run's "Host key
  verification failed".
- **Evidence:** `grep -n "backends/libvirt" Containerfile` → 102, 129, 130;
  `entrypoint.py:96`: `target = (cfg.get("target") or {}).get("libvirt") or {}`.
- **Fix / cost:** a paragraph in §3, no code — the seam is a Python seam, and the image
  is a second layer with its own per-backend touch points. Do not generalise the
  Containerfile: §3 rules out `required_tools` driving it, and a build-time loop over
  the registry is that idea in a different hat.

## The touch-point count — 9 files, for a vSphere or Proxmox backend

| # | Layer | File | Why |
|---|---|---|---|
| 1 | core | `backends/__init__.py:19,21` | import + registry entry — the one the claim admits |
| 2 | core | `config.py:36-49` | `IMAGE_SCHEMA` (F-SEAM-03) — **unconditional** |
| 3 | core | `marker.py` | a `from_text_field` reader, for any annotation/description backend (F-SEAM-04) |
| 4 | core | `cli.py:244` | `_stage_module` copies top-level `*.tf` only; conditional on the module having `.tftpl` or submodules |
| 5 | image | `Containerfile:101-102` | per-backend lock COPY |
| 6 | image | `Containerfile:129-130` | plugin-cache warm, hardcoded module path |
| 7 | image | `Containerfile` RPM set | the backend's own tools (`open-vmdk`, a qcow2 decoder — `docs/future-backends.md`) |
| 8 | image | `container/entrypoint.py:96` | `target.libvirt` hardcoded |
| 9 | mirror | `.tools/tofu-mirror` + `docs/provider-<x>.lock.hcl` | a second provider must be mirrored and its lock committed |

**9 touch points across 4 layers** (core Python, image, provider mirror, and the test
configs that follow from #2), of which **2 are core-file edits the claim says will not
happen** — #2 always, #3 for the two backends §3 actually names. The predecessor
document said 1; `findings.md`'s errata said ~13 across 5. The built reality is 9 across
4, and the residual core coupling is one schema block and one missing parser: a real
improvement, just not the claim as written.

**S6, in one line:** `backends/base.py:240-241` says the module lives at `<pkg>/tofu/`,
but `cli.module_dir` resolves it beside the file that *defines* the class — agreeing only
while that file is `__init__.py` or sits directly inside the package. Deeper, backend two
gets `tofu init` in a directory with no `.tf` files. One docstring clause fixes it.

## Checked and sound

* **`Discovered.artifacts` is opaque to core on every path.** `grep -rn "\.artifacts"
  orchestrator/` returns four hits, all inside `backends/libvirt/`; `_look`,
  `cmd_deploy`, `cmd_destroy` and `_record` never touch it.
* **The ownership policy survives a non-UUID identity.** `Existing.id` is `str` and core
  reads it in one place, to interpolate into a refusal message (`base.py:214`);
  `lookupByUUIDString` is backend-internal (`destroy.py:243`). A Proxmox vmid or vSphere
  moid needs no change, and `Marker.id` is `uuid5` of the logical name — core-derived,
  never the hypervisor's. `decide()` is otherwise backend-neutral: it reads
  `marker.name`, `marker.deployment` and `name`, nothing else.
* **The seam test proves more than the ABC being implementable.** `test_seam.py` blocks
  `import libvirt` at `level == 0` only, correctly separating the binding from the
  same-named package, and guards the guard. `test_cli.py` runs the cycle through
  `main()` against a real `tofu`, exercising `module_dir`, `_stage_module`, the run
  directory and `parse_outputs` against a foreign backend, and
  `test_prepare_is_handed_data_not_a_connection` pins the signature. Two-backend
  composition is tested too, in `test_config.py:98-113` rather than `test_seam.py` —
  `fake_backend.py`'s docstring claim 3 is honest.
* **`tofu.py` is backend-agnostic** (no VM, pool or hypervisor concept anywhere),
  **`Inventory`/`Prepared`** carry nothing libvirt-shaped, and **`future-backends.md`**
  is honest about what it has not re-checked.

## Not checked

* The libvirt backend's connected half beyond the greps above (other agents own it);
  whether the pinned provider could serve a second backend's resources (a provider
  question, not a seam one); `orchestrator-architecture.md` §6.1/§6.3/§6.4/§7.

## Deserves its own agent

* **`cmd_destroy` calls the full `preflight`.** For libvirt that runs the pool walk, the
  orphan-volume refusal and the base-image size comparison at teardown, and prints
  deploy-oriented problems during a destroy. The severity asymmetry is documented as
  deliberate; whether a deploy-shaped `Discovered` is the right thing for destroy to
  build at all is not.
* **`_stage_module`'s copy set** — top-level `*.tf` plus one lock, silently. A module with
  `templates/` or a `.tftpl` loses files with no error until `tofu` fails.
