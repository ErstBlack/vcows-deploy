# vcows-deploy — v0.1 architecture and open work

## Context

`orchestrator-architecture.md` surveys a three-backend, four-stage, air-gapped VM deployment container. This document records what survives that review, what was cut, and the decisions settled since.

**A prior implementation was built and discarded because it became sprawling.** That governs everything here. The cuts in §5 are as much of the deliverable as the design is, and any addition has to earn its surface area.

### Settled

| | |
|---|---|
| Backend | **KVM/libvirt only.** Second backend undecided. Leave seams (§3), build nothing. |
| Provisioning | **OpenTofu for create.** Not reimplemented. |
| Destroy | **`python3-libvirt` directly**, via marker discovery. Works with or without state. |
| State file | Persisted best-effort to a per-deployment run dir. **Disposable, not authoritative.** Third parties will lose it. |
| Identity | **The marker, never the name.** Renaming a VM is a plausible accident; editing a marker is deliberate. |
| Convergence | **Never.** OpenTofu creates and destroys. It does not modify. |
| Version format | **Four-digit `Major.Minor.Patch.Hotfix`** — e.g. `0.1.0.0`. Non-negotiable; matches other delivered products. |
| Base image | Rocky 10 only. All hypervisors confirmed Haswell+, so no `:9` tag. |
| Connection | **Rootless, `qemu+ssh://` only.** Direct socket access is later work. |
| Network | **Bridged, static IPs from config.** The tool never asks libvirt for an address. |
| Disks | **Base volume + per-VM overlay** with explicit capacity. Needs `growpart` in the golden image. |
| Credentials | **Cleartext in the top-level config for now.** Explicitly temporary. |
| Air-gap | Strict, day 1. Golden qcow2 images already exist and are out of scope. |

---

## 1. Pipeline

| Stage | Tool | Role |
|---|---|---|
| **Preflight** | `python3-libvirt` | Enumerate by marker. What exists, what is ours, what conflicts. |
| **Create** | OpenTofu | `apply` against a static libvirt module. Stamps the marker at define time. |
| **State** | file | Written to the run directory. Treated as a convenience, not a source of truth. |
| **Destroy** | `python3-libvirt` | Discover by marker, tear down directly. |

### Why destroy is Python, not `tofu destroy`

The alternative — discover, `tofu import`, `tofu destroy` — **is technically valid**, and an earlier review that called it unworkable was evaluating import-then-*converge*, which is a different and genuinely broken thing. Verified against OpenTofu v1.12.6:

- `tofu destroy` does not plan against config. `walkPlanDestroy`'s `ConfigTransformer` excludes every non-ephemeral resource block, and `planDestroy` passes `Config: nullVal`. Resources reach the destroy graph only through `StateTransformer`. **A lossy import cannot affect destroy correctness.**
- `Delete()` reads exactly `uuid` (domain) and `key` (volume). Discovery produces both by construction.
- CLI `tofu import` is correct over `import {}` blocks — import blocks are realized by `apply`, which imports *and applies the diff*, and with `name`/`type` being `RequiresReplace` that can destroy and recreate a live VM.

It is not built because discovery is mandatory either way, and once it has run you already hold the UUID and disk paths. `dom.destroy()` / `undefineFlags()` / `vol.delete()` is roughly fifteen more lines. The import path replaces those with N+M subprocess invocations, `for_each` key coupling, and three problems Python does not have:

- **Non-idempotent retry** — re-running errors with "Resource already managed by OpenTofu."
- **Silent partial success.** If 3 of 5 domains import, `tofu destroy` destroys those three, leaves two, and **exits 0.** Nothing reports the set was incomplete.
- **Less capability than raw libvirt.** `domainUndefineFlagsForDelete` passes at most `NVRAM|TPM`, and `stopDomainIfRunning` returns early for any non-`RUNNING` state, so a managed-saved or `pmsuspended` domain fails to undefine. Python can pass `MANAGED_SAVE|SNAPSHOTS_METADATA|CHECKPOINTS_METADATA|NVRAM|TPM`.

Also note: `undefine` has **no storage-deletion flag** — `virsh undefine --remove-all-storage` is client-side iteration. Whichever path is used, disks must be deleted explicitly or every qcow2 orphans.

If a uniform `tofu destroy` across backends later becomes a hard requirement, it works with these non-negotiables: `tofu destroy -refresh=false -auto-approve`; no `data` blocks in the module; import both domain and volume; emit `for_each` keys from the discovered set; treat any non-zero import exit as fatal; reconcile discovered against destroyed afterward. `-refresh=false` is required, not optional — the default pre-destroy refresh runs a **full NormalMode plan** and aborts the entire destroy on any error. It is safe to skip because both `Delete()` implementations return cleanly when the object is already gone.

---

## 2. The ownership marker

The durable record of what was created. The state file is a convenience; this is the truth.

### Payload

One canonical JSON object, so there is a single serializer and parser regardless of backend:

```json
{"v":"0.1.0.0","name":"app01","id":"3f2b8c1e-..."}
```

| Field | Purpose |
|---|---|
| `v` | vcows version that created it, four-digit semver. Also the format discriminator. |
| `name` | Logical name from the config, not the hypervisor name. Survives a rename. |
| `id` | Stable machine identity. **Derive deterministically** — `uuid5(VCOWS_NS, logical_name)` — so it regenerates identically with no state file. A random UUID would only be useful when state exists, which defeats the purpose. |

**Deliberately absent for now**, both revisitable: a `deployment` field (destroy scope is currently every vcows VM on the host — see below), and disk paths (read `devices/disk/source/@file` from the live domain XML before undefining, and delete all attached disks).

**Write the parser to ignore unknown keys from day one**, or the extensibility is theoretical. This is the same forward-compatibility promise the original document praises OpenTofu's JSON formats for, applied to your own artifact.

### Placement

**Text fields** (vSphere annotation, Proxmox description) — own line, prefixed so it is findable and removable without clobbering human-written text sharing the field:

```
vcows-managed: {"v":"0.1.0.0","name":"app01","id":"3f2b8c1e-..."}
```

**libvirt** — text content of a single namespaced element, satisfying libvirt's requirement that `<metadata>` have at least one *element* child, with a byte-identical payload:

```xml
<metadata>
  <vcows xmlns="https://example.invalid/vcows">{"v":"0.1.0.0","name":"app01","id":"3f2b8c1e-..."}</vcows>
</metadata>
```

### It survives the OpenTofu create path — verified

`libvirt_domain` exposes `metadata = { xml = "..." }`, mapping to `libvirtxml.Domain.Metadata`, a `,innerxml` field that Go's `encoding/xml` writes verbatim rather than marshalling. It lands inside `<metadata>` in the XML handed to `DomainDefineXML`. libvirt deep-copies the subtree (`xmlCopyNode(node, 1)`) and persists it to `/etc/libvirt/qemu/<name>.xml`, surviving libvirtd restart and host reboot. No perpetual diff: v0.9.8's `Read()` pins the prior value rather than trusting readback (`domain_resource.go` L858, L929-931). Malformed marker XML fails loudly at define time, which is the right failure mode.

Use `metadata`, not `description`/`title` — those are user-visible in virt-manager and Cockpit and third parties will edit them. Setting `description` additionally as a human-readable hint is fine.

Prior art: kcli uses namespaced libvirt domain metadata for exactly this.

### Rules

**Identity is the marker.** Discovery enumerates by marker and that set is authoritative. A renamed VM is still ours and still destroyable. A VM whose marker was edited is user error and out of scope.

**Destroy scope is currently every vcows-marked VM on the target.** Accepted for v0.1 on the assumption of one deployment per hypervisor. **This is expected to change** — a second deployment sharing a hypervisor makes this a data-loss event — so the marker format must accommodate a `deployment` field, and adding one is a version bump.

**Name collisions are a create-time failure, not an ownership question.** libvirt rejects a duplicate name itself. Preflight should check names anyway so the operator gets a clear message instead of a raw libvirt error, but the check does not decide ownership.

**Marker present, config no longer matches:** report "exists (not compared)" and skip. Do not ship a half-comparator. libvirt rewrites domain XML on define — adding defaults, PCI addresses, device aliases — so a naive diff produces permanent false drift, and the natural fix is a normalization layer. That is precisely how the last version sprawled.

**Removing a VM from the config does not delete it.** Consistent with never-converge: preflight reports "marked VM `app03` exists but is not in this config" and leaves it alone. **The config is therefore not declarative in the way people expect, and that must be documented prominently** — the natural assumption is that deleting a line removes a VM. A `prune` subcommand comes later, keeping the destructive operation behind a separate deliberate command rather than making a truncated config a data-loss event.

**Teardown order is load-bearing.** `undefine` on a running domain leaves it running as a *transient* domain with its persistent config, and therefore its marker, deleted — orphaning a VM nobody owns. Destroy first, undefine second, with an explicit NVRAM flag or UEFI domains fail. Report and skip anything that will not resolve through a pool lookup, and **never add an `os.unlink` fallback**.

**Delete only a disk's own `source`, never a `<backingStore>` path.** Per-VM disks are overlays on the shared golden image. Following the backing chain would destroy the base volume that every other deployment's overlays depend on.

### Accepted gaps

**Orphan volume on a mid-create crash.** Volumes cannot carry markers, so if a create dies between the volume and the domain that references it, the qcow2 has no marker and no owning domain. The next run tries to create it, hits a name collision, and fails. Do not build recovery machinery: name volumes deterministically from the logical name, and have preflight report "volume exists with no owning domain" and refuse, so the operator deletes one file rather than debugging a raw libvirt error.

**Preflight-then-create is TOCTOU.** Two operators running against the same hypervisor concurrently will race. libvirt's own name uniqueness catches it, so the loser gets a hard error mid-apply rather than corruption — acceptable for v0.1, but named here so it is not a surprise.

**The base image is never cleaned up.** It is created as an OpenTofu resource and so lives in Tofu's state, but destroy runs through Python by marker and volumes are unmarked, so neither path removes it. This is intended — it is shared across deployments on that host and re-pushing multi-GB images over the SSH tunnel is the cost being avoided. Sweeping stale base images is a `prune` concern.

**Verify in the spike:** libvirt re-serializes metadata with pretty-printing. Confirm the JSON text content survives a define → dumpxml round trip without re-indentation. It should not matter, since the provider pins the prior value and you never converge, but it is cheap to check now and annoying to discover later.

---

## 3. Expansion seams

Adding backend two should require no edit to any core file. Second backend is undecided, so these are sized to the union of what vSphere and Proxmox would need. Nothing speculative is implemented — every seam is a signature or a directory boundary.

### A backend is a package

```
orchestrator/
  backends/
    __init__.py          # REGISTRY = {"libvirt": LibvirtBackend()}
    base.py              # ABC + shared records
    libvirt/
      __init__.py        # the Backend implementation
      schema.py          # the target.libvirt sub-schema
      preflight.py       # marker read + collision detection
      prepare.py         # seed ISO
      render.py          # pure: -> tfvars dict
      destroy.py         # python3-libvirt teardown
      tofu/              # main.tf variables.tf outputs.tf
  config.py              # core schema; composes `target` from the registry
  marker.py              # payload serialize/parse; shared by every backend
  tofu.py                # subprocess driver, backend-agnostic
  cli.py
tests/
  fake_backend.py        # in-memory, ~100 lines, test-only
```

### The interface

```python
class Backend(ABC):
    name: str

    @abstractmethod
    def config_schema(self) -> dict: ...  # target.<name> sub-schema
    @abstractmethod
    def validate(self, cfg) -> list[Problem]: ...  # offline, no connection
    @abstractmethod
    def connect(self, cfg) -> ContextManager[Any]: ...  # opens and closes the session
    @abstractmethod
    def preflight(self, cfg, session) -> list[Existing]: ...
    @abstractmethod
    def prepare(self, cfg, workdir, session) -> ContextManager[Prepared]: ...
    @abstractmethod
    def render(self, cfg, prepared) -> dict: ...  # pure -> tfvars
    @abstractmethod
    def parse_outputs(self, raw) -> Inventory: ...
    @abstractmethod
    def destroy(self, cfg, session, targets) -> None: ...
```

Convention, not a method: the module lives at `backends/<name>/tofu/`.

**ABC with `@abstractmethod`, and no default implementations.** The thing to avoid is *noop defaults*, not ABCs — a backend that forgets `destroy` and inherits a no-op deletes nothing and exits successfully; one that forgets `preflight` skips the safety check entirely. An ABC fails loudly at instantiation, which beats a `Protocol` that only complains if someone remembers to run mypy.

**`connect` — who builds the session.** The signatures above take `session` as a
parameter without saying where it comes from. Somebody must build it, and if that is
core then core imports libvirt and the seam is decorative — which is exactly what the
fake-backend test exists to catch. So the backend opens it and closes it on the way
out, and the session stays opaque to everything above.

**`preflight` — the "is it ours" seam.** Mechanism is per-backend, **policy is core**. libvirt reads domain `<metadata>`; vSphere would read an annotation; Proxmox a description. All three return the same record:

```python
@dataclass(frozen=True)
class Existing:
    name: str  # hypervisor name
    id: str  # UUID / moid / vmid
    marker: Marker | None  # parsed payload, or None if unmarked
    disks: tuple[str, ...] = ()  # source paths of attached media, for teardown
```

`disks` carries what §2 deliberately kept **out** of the marker: it is read from the
live domain XML at discovery time rather than stored, so it cannot go stale. Never a
`<backingStore>` path.

Core parses the marker, applies the rules from §2, and decides skip/create/refuse. **The dangerous logic is written once** — a backend author cannot implement the refusal incorrectly because they never implement it. Core also owns the marker's content and serialization (`marker.py`); the backend owns only where it is stored and how it is read back.

**`prepare` — a context manager, and this is the non-obvious choice.** For libvirt it yields immediately after building the seed ISO. It is a context manager because Proxmox may need the orchestrator to **serve the qcow2 over HTTP on the local network** so PVE can pull it via the download-url API — a listening socket held open for the duration of the apply and torn down after. That has no place in a four-stage pipeline as the original document describes it, and retrofitting it would mean restructuring. `with backend.prepare(...) as prepared:` costs nothing today and absorbs vSphere's qcow2→VMDK→OVA conversion too, which needs the `workdir` plus a cache path.

**`prepare` also takes the `session`, and the reason is forced rather than chosen.** Each apply runs against a fresh, empty state — state is disposable, so the module only ever creates. The shared base image is created once per hypervisor and reused by every deployment's overlays, so from the second deploy onward it must not be declared as a resource, and the overlay needs its path on the host. The pinned provider has **no pool and no volume data source** — `dmacvicar/libvirt` 0.9.8 exposes only `libvirt_domain_interface_addresses`, `libvirt_node_device_info`, `libvirt_node_devices` and `libvirt_node_info` — so "is it already here, and where" cannot be answered from HCL, and it cannot be answered from the config either. Something in the backend has to ask. `render` is pure and `preflight` returns VMs, so `prepare` is the only hook left, and it is already the impure one. The answer goes into `Prepared.artifacts`, so **core still never learns what a storage volume is.** Every alternative relocates that call rather than removing it; the ones that hide it — caching the fact on the backend instance — are worse, because `REGISTRY` holds a singleton and that is process-global mutable state that silently yields "not present" if `prepare` ever runs without `preflight`.

**The scope of that permission is deliberately narrow.** `prepare` may read the target *only* to answer "does this artifact already exist here, and where". Everything about VMs stays in `preflight`. Without that line the parameter becomes the cheapest place to put any future hypervisor read, and `preflight`'s monopoly on discovery erodes one convenient call at a time — which is the shape sprawl takes.

**The session is closed before the apply, not held across it.** The provider opens its own connection from `var.uri`, so holding ours adds a second idle SSH session that buys the transfer nothing. libvirt-python registers no event loop here, so there is no keepalive and no RPC timeout: a socket that hangs rather than resetting can block the closing RPC and leave the CLI wedged *after* a successful apply. `prepare` needs the session only before it yields, so an `ExitStack` closes the connection while `prepared` stays live for the apply's duration. A backend that genuinely needs something held open — Proxmox's HTTP server — holds a socket it opened itself, not the hypervisor session.

**`render` stays pure** — config plus the record `prepare` produced, out to a dict. This was the original document's seam and it is right; it was wrong only as the *sole* seam, since it cannot express the I/O that `preflight` and `prepare` now own.

**`parse_outputs`** converts raw `tofu output -json` into the inventory contract. It is per-backend because each backend's `.tf` module declares its own `output` blocks, so the raw shape differs. Without this step your module's output block *is* your public API — rename an output and every consumer of `inventory.json` breaks. With it, the module can be refactored freely. About ten lines for libvirt.

**`destroy`** takes the set `preflight` discovered and tears it down. libvirt does it directly through `python3-libvirt` (§1). Another backend could use `tofu import` + `tofu destroy -refresh=false`, or its own SDK. The seam exists precisely so that choice is per-backend.

### Config composition

Core carries `backend: <enum from the registry>` and `target: {<name>: <sub-schema from the backend>}`, assembled with a generated `if/then` per registered backend — roughly fifteen lines in `config.py`, written once. Adding a backend adds a schema file inside its own package and touches no core file.

Keep the `target.<backend>` nesting. An earlier note suggested flattening it to avoid conditional jsonschema; that held only while no second backend was coming.

### What the fake backend proves

`tests/fake_backend.py`, ~100 lines, shipped in nothing. It earns its place by proving three things interface design alone cannot:

1. Core runs the full pipeline — validate → preflight → prepare → render → apply → outputs → destroy — **with no libvirt import anywhere in the call path.** That is the actual test of whether the seam is real.
2. The ownership policy is exercised against a backend with no libvirt semantics: absent → create, ours → skip, unmarked → refuse.
3. Two backends register simultaneously and the config schema composes.

### Explicitly not built

No noop default implementations. No plugin discovery or entry points — an explicit dict in `backends/__init__.py`. No capability negotiation. No per-backend CLI subcommands. No backend-specific exception hierarchies. No `required_tools` declarations driving Containerfile generation.

---

## 4. Remaining findings

**F11 — Draft the `target.libvirt` schema block.** Now unblocked by the decisions above. Needs: `uri` (`qemu+ssh://` form), `ssh_keyfile`, `known_hosts`, `pool` name and whether the tool may create it, `bridge`, per-VM `mac`, per-VM `nics: [{bridge, ip_cidr, gateway, nameservers}]`, `disk_gb`, and firmware mode — Rocky 10 golden images are commonly UEFI, needing `os.firmware = "efi"`, a host-specific loader path, and an NVRAM template, none changeable after creation. This is the one-way door; other groups will author these by hand and keep them in their own version control.

Two rules to settle while drafting: `nics` is a list but the inventory carries one `ip`, so mark a primary (either first-wins or an explicit `primary: true`); and state whether a per-VM value **replaces or merges** with `defaults`. Write replace-not-merge down now — it is invisible until the first nested field, and by then configs exist.

**Inventory** — emit `inventory.json` with a minimal shape (name, addresses) and **document it as unstable**. Nothing consumes it at v0.1 since Ansible is cut; formalize the contract when something actually reads it, and add `inventory_version` at that point.

**Version bump rules** — deferred. The four-digit format is fixed; which digit a marker-format or schema-breaking change bumps can wait until there is a second release.

**F12 — Credentials in cleartext, deliberately temporary.** Consequence to keep visible: `config.yaml` becomes a secret artifact. It cannot be committed or shipped as an example, and its contents flow into the emitted tfvars and the state file. Enable OpenTofu's native state encryption regardless — it is one block and free.

**R1 — `qemu+ssh://` prerequisites.** Most of the original rootless/SELinux problem evaporates with no local socket. What remains: an SSH private key inside the container at 0600 under the UID map, a `known_hosts` policy (not `no_verify=1`), an `ssh` binary in the image, and — a hypervisor-side prerequisite worth documenting — `nc` or `virt-ssh-helper` present on the *hypervisor*. Note that `vol-upload` over `qemu+ssh://` streams the whole image through the tunnel with no resume; base-plus-overlay means that cost is paid once per host rather than per VM.

**F2 — Seed ISO tooling: decide in the spike.** Compare `pycdlib` (pure Python, no subprocess, vendored as a wheel, removes `xorriso` from the image and the GPL BOM) against `xorrisofs` (already present, battle-tested output, reproducible by hand when a VM will not boot). `mkisofs` does not exist on Rocky 10 or 9 — Rocky ships `xorriso`, no `genisoimage` in BaseOS/AppStream/CRB, and EPEL 10's `genisoimage` does not provide the `mkisofs` binary. Do **not** use `libvirt_cloudinit_disk`: it stages the ISO in `os.TempDir()` and calls `RemoveResource()` when missing, so a container's empty `/tmp` makes the ISO, its volume, and the domain all show as needing recreation every run (issue #1368 open, PR #1369 unmerged).

**F5 — Base volume + per-VM overlay.** `vol-create-as <pool> x.qcow2 20G` followed by `vol-upload` writes source bytes from offset 0 including the golden image's qcow2 header and its `virtual size` field, silently discarding the declared capacity. Upload the golden image once per host; create each VM's disk as an overlay with explicit larger capacity. **Confirm `growpart`/`cloud-init` is in the golden images** — the design depends on it.

**R2 — Offline build gate. In scope.** `podman run --network=none` with `tofu init && tofu plan` succeeding, wired into the build. Set `CHECKPOINT_DISABLE=1`, `PIP_NO_INDEX=1`, `no_proxy=*` so residual egress fails fast rather than hanging.

**R6 — Air-gap packaging. The original §9 is wrong in four ways.** All verified; all fail silently on a connected build host and loudly at a disconnected site.

- **`/etc/tofurc` is not a path OpenTofu reads.** On Linux it reads `$HOME/.tofurc`, `$XDG_CONFIG_HOME/opentofu/tofurc`, `$HOME/.terraformrc`, or `TF_CLI_CONFIG_FILE`. As written the `filesystem_mirror` block is never loaded, OpenTofu falls back to `direct`, and `tofu init` reaches for `registry.opentofu.org` — a DNS hang at the site, not a clean error. Set `ENV TF_CLI_CONFIG_FILE=/opt/tofu/tofurc` explicitly. Worse under rootless with `--user`: a UID with no `/etc/passwd` entry gets `HOME=/`, so even a correctly placed `~/.tofurc` is missed.
- **`tofu providers mirror` mirrors what the *current configuration* requires**, so it runs per module directory, not once. It also resolves the newest version satisfying the constraint at build time while `.terraform.lock.hcl` pins exactly — the module constraint, the mirror contents, and the lock file are three independently drifting facts. Pin exact versions (`version = "= 0.9.8"`), mirror per module into one path, then `tofu providers lock -fs-mirror=/opt/tofu-mirror -platform=linux_amd64` so lock hashes come from the artifacts that actually ship. A lock produced against a registry records different hashes than one produced against a mirror, and the mismatch surfaces as a checksum error that reads like corruption.
- **Run `tofu init` at build time** so the runtime never performs provider installation. This also sidesteps that a runtime `init` needs a **writable** module directory (`.terraform/`, the lock file), which a read-only baked module tree will not provide — copy the pre-initialised tree into a writable work dir at start.
- **Pin OpenTofu 1.12.6.** The original §13 points at 1.11.x, whose support ended 2026-08-01.

**R7 — `python3-libvirt` comes from the RPM, not pip.** PyPI publishes sdist only — no wheels — so `pip install` needs gcc, `libvirt-devel`, and pkg-config, and default build isolation tries to fetch setuptools from the network, which fails air-gapped. Rocky ships `python3-libvirt` built against the platform Python. **If the application uses a virtualenv, the RPM binding is invisible unless the venv is created with `--system-site-packages`** — this is the obvious thing that bites, and it bites at import time with a confusing `ModuleNotFoundError` on a machine where `python3 -c 'import libvirt'` works fine outside the venv.

**R3 — Pin the provider and vendor its license. In scope.** `dmacvicar/libvirt` 0.9.x ships **no LICENSE file** — 404 at `main` and `v0.9.8`, GitHub API reports `license: null`, and `main` has *no common ancestor* with `v0.8.3`, so nothing was deleted; the Apache grant never carried into the rewrite by lineage. The grant does ship in the bundled README, and Apache-2.0 §4(a) puts the obligation to supply the license text on the redistributor regardless. Vendor the canonical text with a provenance note (upstream URL, tag commit, artifact SHA256, the verbatim README grant, the orphan-history explanation, a pointer to issue #1371). Pin exactly: the `metadata` pinning in `Read()`, the volume `key` passthrough (PR #1334), and the `isImport` branch are all recent, and only the volume one has acceptance-test coverage.

**R4 — Tests. In scope: golden-file, fake backend, `tofu validate` in CI, one manual E2E.** `tofu validate` against emitted tfvars with providers from the mirror is the highest value per line — it catches every missing, misnamed, or mistyped variable without contacting anything. Add `isoinfo` assertions on the seed ISO. §4.2's "trivially unit-testable pure functions" claim was circular: testable was *defined* as pure, and the failure modes that take a site down live in the impure half.

**R5 — Build manifest. In scope.** Tag `orchestrator:<four-digit-semver>`. Embed provider version and checksum, every wheel, and the git SHA; print from `--version` and copy into every run directory. There is still no air-gapped *update* path — §9 explains how to build the mirror and nothing about moving a site from N to N+1, or rolling back. Worth defining before the first external delivery.

**F16 — Fix the OCI labels. In scope.** The image inherits `org.opencontainers.image.licenses="BSD-3-Clause"`, `vendor="Rocky Enterprise Software Foundation"`, `name="rockylinux"`, and RESF authors from the base and overrides none of them — so it is mislabelled and self-identifies to third parties as an RESF product. Override `licenses`, `vendor`, `authors`, `name`, `summary`, `source`, `license`, and add `base.name` + `base.digest`. Six lines.

**F15 — Measure the GPL source sidecar.** The image ships `qemu-img` (GPL-2.0-**only**, so GPLv2 §3 applies with no upgrade path) and `xorriso`. Shipping source on the same media puts you under GPLv3 §6(a) / GPLv2 §3(a), which lets recipients lawfully re-copy to other sites — a written offer alone limits them to "occasionally and noncommercially," which does not describe a group deploying internally. Rocky publishes full source trees (`dl.rockylinux.org/pub/rocky/10/{BaseOS,AppStream}/source/tree/`, `vault/` for pinned point releases), so mirroring them in the same pass costs one more `reposync`. Estimated 1.5–3 GB, **unverified** — this is the only open item that can change media sizing.

---

## 5. Cut from v0.1

| Cut | Why |
|---|---|
| §6.1, §6.4, §7, most of §2's table, §11 rows 2–3 | vSphere and Proxmox. ovftool, open-vmdk, `mkova.sh`, content libraries, govc, the `import` content type, the snippet/SFTP analysis. `image/convert.py` deletes outright. Move to `future-backends.md` so the research survives. |
| §4 Stage 3, `ansible/`, `exec/ansible.py` | Nobody has asked for post-config. Golden images already contain cloud-init and `user_data` is in the schema. Stage 3 has no stated purpose, no inputs, and no config surface. It drags in ansible-core, ansible-runner, a second air-gap vendoring pipeline, and the GPLv3 half of F15. **The seam is free: write `inventory.json` with no consumer.** |
| §4 Stages 0 and 4, `hooks/` | No downstream group has asked for a hook, and shipping one freezes an environment contract against zero requirements. An escape valve arriving before real extension points means downstream shells out instead of asking, inside sites you cannot debug. Adding later is backward-compatible; removing is not. Make CLI phases separably invocable instead. |
| §8, `telemetry.py` | Its purpose was correlating two child processes; there is one. The propagation gap it cites closed upstream (issue #3936, shipped in 1.13.0). Air-gap would mean a collector per site. |
| The `:9` tag | Answered by the Haswell confirmation. Never the one-line diff §3 claims anyway — Rocky 9's platform Python is 3.9, `python3-libvirt` there is built against 3.9 only, and current ansible-core needs 3.12+. |
| S3/MinIO, `lifecycle: oneshot \| stateful` | State is always written. §10 conflated *shared* state with *a record of what you created*; "one-shot" should have meant unshared, not amnesiac. |

Extensibility is preserved through the seams in §3 and signalled through the version number. Breaking changes bump it.

---

## 6. Verification

**Spike first** — these settle open questions and are cheap:
1. `pycdlib` vs `xorrisofs` for the seed ISO (F2). Build one both ways, compare complexity and vendoring cost.
2. Marker round trip: define with the JSON payload, `dumpxml`, confirm it comes back intact and un-reindented.
3. Measure the GPL source sidecar against the DVD budget (F15).
4. Confirm `growpart`/`cloud-init` is present in the golden images (F5).

**Before writing much code:**
5. Draft the full `target.libvirt` schema block (F11). One-way door.
6. Fix the OCI labels (F16) and vendor the provider license (R3).

**Before trusting the design:**
7. One acceptance run against a real hypervisor: pool → upload → overlay → cloud-init → domain → boot → reachable IP → destroy. Nothing in §6.2 or §11 of the original document is verified until this has run once.
8. `podman run --network=none` with `tofu init && tofu plan` succeeding, wired into the build (R2).
9. **Test destroy with the state file deleted.** The whole reason for the marker. First-class test, not an afterthought.
10. **Test the rename case:** create a VM, rename it out of band, confirm discovery still finds it by marker and destroys it.
11. **Test the refusal path:** hand-create an unmarked VM with a name the config wants, confirm a clear failure and non-zero exit with nothing else touched.
12. **Run the full pipeline against the fake backend with libvirt unimportable.** If core cannot complete a deploy/destroy cycle without it, the seam is not real regardless of the signatures.

**Testbed gap worth closing.** The rig is Fedora 44; the likely targets are RHEL 9 and RHEL 10. Fedora is *newer* than the target, so it will not surface the failures that matter — RHEL 9 ships a much older libvirt than the Rocky 10 container's client library, and the risks run in that direction: `undefineFlags` bits the older daemon does not implement, and API calls it does not have. Fedora will pass tests RHEL 9 fails. Get a RHEL 9 target in front of this before the first external delivery.

**Definition of done for v0.1:** one `config.yaml` produces N running, cloud-init'd, reachable VMs on libvirt from a golden qcow2 over `qemu+ssh://`; `destroy` removes exactly what it created, **with the state file deleted**; a renamed VM is still recognized; an unmarked name collision is refused; and the container builds and runs fully offline.

---

## Appendix — errata in `orchestrator-architecture.md`

That document is archived as background. It is a good survey, but it was written before any code existed and several of its concrete claims are wrong. Do not copy commands out of it without checking here first.

| Section | Claim | Reality |
|---|---|---|
| §11, §6.4 | `mkisofs -o seed.iso -V cidata ...` | `mkisofs` does not exist on Rocky 10 **or** 9. Rocky ships `xorriso`; no `genisoimage` in BaseOS/AppStream/CRB; EPEL 10's `genisoimage` does not provide the binary. The "xorriso provides mkisofs" behaviour is Debian/Arch. |
| §9 | Write mirror config to `/etc/tofurc` | Not a path OpenTofu reads. See R6. |
| §9 | One `tofu providers mirror` invocation | Mirrors only what the current config requires; must run per module. See R6. |
| §6.2 | `vol-create-as <pool> x.qcow2 20G` then `vol-upload` | Upload writes from offset 0 including the golden image's qcow2 header, silently discarding the declared capacity. See F5. |
| §2 | `dmacvicar/libvirt` — Apache-2.0, unqualified | True only for ≤ 0.8.3. 0.9.x ships no LICENSE file, and `main` has no common ancestor with `v0.8.3`. See R3. |
| §13 | OpenTofu 1.11.6 / 1.12.0 | 1.12.6 current; 1.11.x support ended 2026-08-01. |
| §8 | TRACEPARENT does not propagate; issue #3936 open | Closed 2026-04-15 and shipped in 1.13.0. §5.3's "human output or JSON, pick one" is also stale — `-json-into` shipped in 1.12.0 as a general CLI argument. |
| §2, §9, §12 | `community.vmware` needs pyVmomi; pin pyVmomi | 6.x replaced pyVmomi with `vcf-sdk`. Irrelevant to this MVP, but the mitigation is obsolete in three places. |
| §3, §12.1 | `:9` tag differs only in `FROM` and Python version | Rocky 9's platform Python is 3.9, `python3-libvirt` there is built against 3.9 only, and current ansible-core needs 3.12+. Moot — Haswell confirmed, Rocky 10 only. |
| §4.1 | Bind mounts need `:Z` | `:Z` applies a *private exclusive* label; on a shared `/srv/images` it relabels the directory out from under libvirtd. Use `:z` for shared read-only mounts. Largely moot under `qemu+ssh://`. |
| §4.2 | "A fourth backend is one new file plus one registry entry" | ~13 touch points across 5 layers. §3 of this document is the correction. |
| §10 | Default to one-shot, ephemeral state | Conflates *shared* state with *a record of what you created*. The marker (§2) is the actual answer. |

Sound and worth keeping: the JSON-not-HCL decision, the §5.2 verdicts (no `python-terraform`, no CDKTF, no direct gRPC to providers), the x86-64-v3 reasoning, and the ovftool licensing conclusion.
