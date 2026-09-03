# vcows-deploy — v0.1 architecture and open work

## Context

`orchestrator-architecture.md` surveys a three-backend, four-stage, air-gapped VM deployment container. This document records what survives that review, what was cut, and the decisions settled since.

**A prior implementation was built and discarded because it became sprawling.** That governs everything here. The cuts in §5 are as much of the deliverable as the design is, and any addition has to earn its surface area.

### Settled

| | |
|---|---|
| Backend | **KVM/libvirt only.** Second backend undecided. Leave seams (§3), build nothing. |
| Provisioning | **`python3-libvirt` directly**, the same client destroy uses. |
| Destroy | **`python3-libvirt` directly**, via marker discovery. |
| Identity | **The marker, never the name.** Renaming a VM is a plausible accident; editing a marker is deliberate. |
| Convergence | **Never.** vcows creates and destroys. It does not modify. |
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
| **Create** | `python3-libvirt` | Upload, overlay, define. Stamps the marker at define time. |
| **Destroy** | `python3-libvirt` | Discover by marker, tear down directly. |

### Silent partial success, and the base volume that must not be deleted

Two defects any teardown here has to avoid, named once because the code cites this section for them.

**Silent partial success.** If 3 of 5 domains are torn down and two are not, an exit code alone reports nothing about the incomplete set. So `destroy` returns an `Outcome` naming every object it removed and every object it did not, and a consumer that does not read it reproduces the defect exactly.

**The shared base volume.** The golden image is created once per host and every deployment's overlays back onto it, so deleting it breaks VMs no run of this deployment created. Python cannot make that mistake structurally: volumes carry no marker, and destroy tears down only what marker discovery found. **Delete only a disk's own `source`, never a `<backingStore>` path.**

Also note: `undefine` has **no storage-deletion flag** — `virsh undefine --remove-all-storage` is client-side iteration, so disks must be deleted explicitly or every qcow2 orphans. The undefine itself passes `MANAGED_SAVE|SNAPSHOTS_METADATA|NVRAM` always and `CHECKPOINTS_METADATA|TPM` where the daemon is new enough, so a managed-saved or `pmsuspended` domain still undefines.

---

## 2. The ownership marker

The durable record of what was created, and the only one. vcows writes nothing a site could lose and still expect a teardown from.

### Payload

One canonical JSON object, so there is a single serializer and parser regardless of backend:

```json
{"v":"0.1.0.0","deployment":"lab-a","name":"app01","id":"3f2b8c1e-..."}
```

| Field | Purpose |
|---|---|
| `v` | vcows version that created it, four-digit semver. Provenance only — nothing branches on it. `MARKER_XMLNS` is the discriminator. |
| `deployment` | Which deployment stamped this VM. Empty when parsed from a marker written before the field existed, never `None`, so callers need no null check. Destroy is scoped by it. |
| `name` | Logical name from the config, not the hypervisor name. Survives a rename. |
| `id` | Stable machine identity. **Derive deterministically** — `uuid5(VCOWS_NS, "{deployment}/{name}")` — so it regenerates identically from the config alone. A random UUID would have to be recorded somewhere to be useful, which defeats the purpose. The deployment is in the input because this is also the seed ISO's `instance-id`; see *Accepted gaps*. |

`deployment` was written down as deliberately absent while destroy was host-wide. It is **present from 0.1.0.0** (D4): stamping it costs nothing, and adding it later would have meant a marker migration on every VM already deployed. Destroy is now scoped by it — see the rules below.

**Deliberately absent, and staying that way:** disk paths. They are read from the live domain XML (`devices/disk/source/@file`, plus `cdrom` for the seed ISO), which is correct even for a VM whose disks changed after creation. Preflight reads them to report; destroy re-reads the same domain immediately before undefining, so what is deleted is what the domain names *now* rather than what it named while the operator was still being asked. That window is unbounded — `cmd_destroy` waits on a human — and it is also where the marker is re-checked. A target whose domain has already gone leaves no document to re-read, so its recorded paths are instead checked against every path the host's other domains claim, and against the two names that VM is entitled to own.

**Write the parser to ignore unknown keys from day one**, or the extensibility is theoretical.

### Placement

**Text fields** (vSphere annotation, Proxmox description) — own line, prefixed so it is findable and removable without clobbering human-written text sharing the field:

```
vcows-managed: {"v":"0.1.0.0","deployment":"lab-a","name":"app01","id":"3f2b8c1e-..."}
```

**libvirt** — text content of a single namespaced element, satisfying libvirt's requirement that `<metadata>` have at least one *element* child, with a byte-identical payload:

```xml
<metadata>
  <vcows xmlns="urn:vcows:1">{"v":"0.1.0.0","deployment":"lab-a","name":"app01","id":"3f2b8c1e-..."}</vcows>
</metadata>
```

### It survives define — verified

`create.domain_xml` writes the `<vcows>` element inside `<metadata>` in the document handed to `DomainDefineXML`. libvirt deep-copies the subtree (`xmlCopyNode(node, 1)`) and persists it to `/etc/libvirt/qemu/<name>.xml`, surviving libvirtd restart and host reboot. Malformed marker XML fails loudly at define time, which is the right failure mode. **The restart half of that is now observed rather than reasoned** (2026-08-29): `systemctl restart virtqemud` on the rig, and `vcows-probe02`'s payload came back byte-identical, un-reindented, and still found by marker rather than by name. Host reboot rests on the same persistence and is still inferred.

Use `metadata`, not `description`/`title` — those are user-visible in virt-manager and Cockpit and third parties will edit them. Setting `description` additionally as a human-readable hint is fine.

Prior art: kcli uses namespaced libvirt domain metadata for exactly this.

### Rules

**Identity is the marker.** Discovery enumerates by marker and that set is authoritative. A renamed VM is still ours and still destroyable. A VM whose marker was edited is user error and out of scope.

**Destroy scope is this deployment's marked VMs, not every marked VM on the target.** An earlier revision of this section accepted host-wide scope for v0.1 on the assumption of one deployment per hypervisor, and named the change it was waiting for: a `deployment` field in the marker. D4 supplied that from 0.1.0.0, so by Stage 4 the scope was a filter on data already present rather than a format change. Host-wide would have made a second deployment sharing a hypervisor a data-loss event, and it is not symmetric with the create side, where `decide()` already refuses a VM belonging to another deployment.

Marked VMs from other deployments are **reported as found and skipped, with their deployment names**. That report is where the cost of scoping shows up: `deployment` defaults to the config's filename stem, so renaming a config orphans its VMs, and an operator seeing "belongs to deployment 'lab-a', not 'lab-b'" can tell what happened. A `--all` flag is deferred until something needs it — adding it later is backward compatible, removing it would not be.

**Name collisions are a create-time failure, not an ownership question.** libvirt rejects a duplicate name itself. Preflight should check names anyway so the operator gets a clear message instead of a raw libvirt error, but the check does not decide ownership.

**Marker present, config no longer matches:** report "exists (not compared)" and skip. Do not ship a half-comparator. libvirt rewrites domain XML on define — adding defaults, PCI addresses, device aliases — so a naive diff produces permanent false drift, and the natural fix is a normalization layer. That is precisely how the last version sprawled.

**Removing a VM from the config does not delete it.** Consistent with never-converge: preflight reports "marked VM `app03` exists but is not in this config" and leaves it alone. **The config is therefore not declarative in the way people expect, and that must be documented prominently** — the natural assumption is that deleting a line removes a VM. A `prune` subcommand comes later, keeping the destructive operation behind a separate deliberate command rather than making a truncated config a data-loss event.

**Teardown order is load-bearing.** `undefine` on a running domain leaves it running as a *transient* domain with its persistent config, and therefore its marker, deleted — orphaning a VM nobody owns. Destroy first, undefine second, with an explicit NVRAM flag or UEFI domains fail. Report and skip anything that will not resolve through a pool lookup, and **never add an `os.unlink` fallback**.

**That skip rule has a precondition, and without it the rule inverts.** `storageVolLookupByPath` and `listAllVolumes` read libvirt's *in-memory pool cache*, not the filesystem, so "will not resolve" means "libvirt has not looked" at least as often as it means "gone". Measured on the rig: three of four running domains' disks — real files inside an active pool's own target directory — return `VIR_ERR_NO_STORAGE_VOL` because they were written out of band and the pool has not been refreshed since. So **`pool.refresh(0)` before any volume is enumerated or resolved, in preflight and in destroy alike**. Without it, "report and skip" silently leaks every overlay, which is the opposite of what the rule is for; and preflight would report an out-of-band-seeded golden image as absent, set `create = true`, and `create` would die on "storage volume exists already" for a reason nobody could diagnose. A refresh is a directory rescan, not a configuration change, so it does not conflict with vcows never creating a pool.

**Delete only a disk's own `source`, never a `<backingStore>` path.** Per-VM disks are overlays on the shared golden image. Following the backing chain would destroy the base volume that every other deployment's overlays depend on.

### Accepted gaps

**Orphan volume on a mid-create crash.** Volumes cannot carry markers, so if a create dies between the volume and the domain that references it, the qcow2 has no marker and no owning domain. The next run tries to create it, hits a name collision, and fails. Do not build recovery machinery: name volumes deterministically from the logical name, and have preflight report "volume exists with no owning domain" and refuse, so the operator deletes one file rather than debugging a raw libvirt error.

**The orphan check is bounded to the config's VMs.** `orphan_volumes` iterates `cfg["vms"]` and asks about the two names each configured VM is entitled to, so an orphan left behind by a VM since removed from the config is invisible to it. That is the same config edit the README says does not delete the VM — the volume survives too, and nothing reports it. Widening the check to every volume in the pool was rejected for the reason the refusal message itself gives: on a shared pool vcows cannot attribute a volume it did not create, so it would be refusing deploys over somebody else's files. The bound is real and the fix is a `prune` verb that does not exist.

**`UNDEFINE_NVRAM` removes the varstore — observed, 2026-08-29.** Two EFI domains were deployed to the rig, one with libvirt autoselecting firmware and one with a pinned `loader` and `nvram_template`. Both wrote a varstore at define time — `app01_VARS.qcow2` and `app02_VARS.qcow2`, the pinned one from the qcow2 template it named — and a `destroy` two minutes later removed both, leaving `/var/lib/libvirt/qemu/nvram/` holding exactly the four varstores that predated the run. A root-side watcher sampled the directory every two seconds throughout, so the removal is observed rather than inferred from the exit code; `acceptance.md`'s claim, which had no such evidence behind it, is confirmed. **Scope it to what ran:** the qcow2 template path, on libvirt 12.0.0. Rocky 9 and Rocky 10 ship raw `.fd` OVMF templates this rig does not have, and what a flag-shed retry down to `FLOOR` leaves behind on 9.0/9.1 EUS is still unknown. Those two halves need an old-libvirt target and stay blocked. They matter because if a varstore ever does survive, a redeploy under the same name inherits the previous VM's UEFI variables, including its boot entries.

**A split-daemon host cannot present a live storage driver with a dead domain driver — measured, 2026-08-29.** The review asked whether it could, because if it could then a `lookupByUUIDString` failure mid-teardown would mean something worse than a race. It cannot, on this transport. With `virtqemud` and its sockets stopped and `virtstoraged` left running, `virtstoraged-sock` is still present and still listening, and nothing can reach it: every client enters through `virtqemud-sock`, so the connection never opens at all. `virsh` on the rig itself gets the same refusal vcows does — `virt-ssh-helper: cannot connect to '/var/run/libvirt/virtqemud-sock'`, `VIR_ERR_SYSTEM_ERROR` from `VIR_FROM_REMOTE`, before any driver call. So there is no state in which vcows holds a working connection whose domain driver is absent, and the stale-target window `_reverify` closes stays what it was: a race against another operator, not a driver asymmetry. `ERR_NO_DOMAIN` earns its keep on the race; it does not need to cover this.

**Preflight-then-create is TOCTOU.** Two operators running against the same hypervisor concurrently will race. libvirt's own name uniqueness catches it, so the loser gets a hard error mid-create rather than corruption — acceptable for v0.1, but named here so it is not a surprise.

**The base image is never cleaned up.** Destroy runs by marker and volumes are unmarked, so nothing removes it. This is intended — it is shared across deployments on that host and re-pushing multi-GB images over the SSH tunnel is the cost being avoided. Sweeping stale base images is a `prune` concern.

**Cross-deployment identity is in the derivations.** `derive_id` is `uuid5(VCOWS_NS, f"{deployment}/{name}")` and `derive_mac` is the same with `#nic{index}` appended, so two deployments each containing `app01` no longer derive one MAC and one cloud-init `instance-id`. Without it, on two hosts bridged to one L2 both guests boot, both apply their static address, and both report `cloud-init status: done` — `address_conflicts` only ever looks at one host, so nothing else catches it. Settled before first ship because `derive_mac`'s permanence is real: changing it renames the interface every running VM's guest configuration is keyed to.

**That narrows the collision rather than closing it.** Two hosts running the *same* deployment name still derive the same MACs, and nothing in vcows can see across hosts. The per-NIC `mac:` override is the only escape, and it is the reason the fold was acceptable at all: a site whose switch policy or DHCP reservations already own an address has somewhere to go. Do not read the fold as "MACs are unique now".

**Volume names stay undecorated, and the message carries the cost.** Prefixing the deployment onto `app01.qcow2` would make the collision above structurally impossible in a shared pool, and it was rejected: D16's predictable names are what an operator at a site has, holding the config and `virsh vol-list` and nothing else. The cost is that a volume in a shared pool cannot be attributed, so the orphan refusal says the volume *may* belong to another deployment rather than asserting an interrupted create — which is the truth vcows can actually establish. Volume names are not a one-way door either way: `destroy` reads disk paths out of the domain XML rather than re-deriving them.

**The size ceilings are typo-catchers, not policy.** `vcpus`, `memory_mib` and `disk_gb` carry a `maximum` — 512, 4 TiB and 64 TiB — so a fat-fingered zero is refused before the run creates volumes for a VM no host will start. They are not a claim about what is supported, and the hypervisor stays the authority on what it will actually serve. Each is read from `VCOWS_MAX_VCPUS` / `VCOWS_MAX_MEMORY_MIB` / `VCOWS_MAX_DISK_GB` at import, the same shape as `cli.MANIFEST`, so a site on hardware nobody here has seen raises the bound from the outside rather than editing a file inside the image. Raising one is always safe; lowering one can refuse a config that already deployed.

**`gateway` stays required on every NIC, and only the primary's becomes a route.** One default route per NIC leaves a multi-NIC guest choosing its egress by metric, which is the same failure shape as netplan's `default` keyword: it boots, it routes, and it routes somewhere nobody chose. The non-primary gateways are still parsed and still checked against their own subnets, so they are not unvalidated — they are simply not routes. Requiring a field that is no longer emitted looks like debt and is not: making a required field optional later is the backward-compatible direction and the reverse is not, so the requirement stays until there is a reason to drop it.

**A failed pool refresh refuses a deploy and only warns a destroy.** The refresh above is required for correctness, so a deploy that could not do one is about to decide "the golden image is absent" from a cache that was never read — it refuses. A teardown has no such decision to make: it resolves paths, and every path it could not resolve is already named in the outcome, so refusing would leak the volumes it was called to remove. The severities differ and the code does not branch on the verb: `cmd_deploy` treats `Discovered.problems` as fatal and `cmd_destroy` prints them as advisory, which is the same asymmetry the size-mismatch refusal already relies on.

**An object that cannot be read is reported and skipped, never silently dropped.** A domain, a volume or a DHCP lease list that will not answer is a WARNING naming it and what the skip cost — not an error, because one broken foreign object on a shared hypervisor is not this deployment's to fix and refusing every run on that host over it is worse. The reason it cannot be silent is that every check in `preflight` decides on what it found: an absent domain is no MAC collision and no name clash, an absent volume is no orphan, and an unreadable lease list is a free address. The two exceptions are both about *what may be deleted*: a pool that will not describe itself and a target whose live document disagrees with the snapshot are errors, because there the unknown is on the destructive side.

**Verify in the spike:** libvirt re-serializes metadata with pretty-printing. Confirm the JSON text content survives a define → dumpxml round trip without re-indentation. It should not matter, since nothing ever converges, but it is cheap to check now and annoying to discover later.

---

## 3. Expansion seams

Adding backend two should require no edit to any core file. Second backend is undecided, so these are sized to the union of what vSphere and Proxmox would need. Nothing speculative is implemented — every seam is a signature or a directory boundary.

**One block does not hold, and it is the same one in both places.** `config.IMAGE_SCHEMA` is written in qcow2 and libvirt terms — `source_qcow2` and `base_volume_name` are the field *names* — and it is wired into the core schema directly rather than composed from the registry the way `target` is. A backend wanting an OVA path or a template id edits `config.py`. The format reader behind it, `orchestrator/qcow2.py`, is a core module imported by exactly one backend. That is two core sites, not a layer, and neither is worth moving before there is a second backend to move it for. It is recorded rather than fixed so the seam claim above is read as "one known exception" rather than "verified complete".

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
      render.py          # pure: config -> the values create consumes
      create.py          # python3-libvirt upload, overlay, define
      destroy.py         # python3-libvirt teardown
  cloudinit.py           # seed ISO, MAC derivation, the NIC addressing checks
  config.py              # core schema; composes `target` from the registry
  imagecheck.py          # digest and capacity checks on the golden image
  limits.py              # the VM size ceilings and their environment overrides
  marker.py              # payload serialize/parse; shared by every backend
  problems.py            # Severity/Problem; core and every backend produce them
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
    def preflight(self, cfg, session) -> Discovered: ...
    @abstractmethod
    def prepare(self, cfg, workdir, discovered) -> dict: ...  # opaque to core
    @abstractmethod
    def create(self, cfg, session, prepared) -> dict: ...  # the inventory map
    @abstractmethod
    def destroy(self, cfg, session, targets) -> Outcome: ...
```

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

**`prepare` builds what `create` consumes, and reaches nothing.** Both shipped backends write the seed ISOs and return; `orchestrator/cloudinit.py` builds them, because nothing in that is hypervisor-specific. Proxmox's additionally carries `discovered.artifacts["image"]` through to `render`, because the pure half has no way to ask a storage what `import` content it already holds — `ProxmoxBackend.prepare` says so beside the line. The `workdir` parameter is sized for vSphere: a qcow2→VMDK→OVA conversion needs it plus a cache path, and a conversion product that must exist for the create has to live somewhere. That has no place in a four-stage pipeline as the original document describes it, and retrofitting it would mean restructuring, whereas it costs nothing today.

**The reason this section originally gave was wrong, and is corrected here by name.** It said Proxmox might need the orchestrator to serve the qcow2 over HTTP on the local network so PVE could pull it through the download-url API, holding a listening socket open. The backend that shipped in #149 does not do that and cannot want to: `create.upload` posts a local file to PVE's own upload endpoint for the `import` and `iso` content types, so vcows pushes and serves nothing. The decision survives; that justification does not.

**`prepare` takes what `preflight` found, and `preflight` is the only method that reads the target.** `create` only ever creates. The shared base image is created once per hypervisor and reused by every deployment's overlays, so from the second deploy onward it must not be created again, and the overlay needs its path on the host. That cannot be answered from the config, because the pool is someone else's and its target path is a property of the pool.

So a live connection has to ask — but `preflight` is **already** asking. The orphan-volume refusal below requires it to enumerate the pool and resolve every volume's path, so the base image's presence, path and size are a lookup on data it is holding. Handing the session to `prepare` would buy a second round trip, not a first, and would let `prepare` reach the hypervisor for anything else besides. Instead `preflight` returns everything one walk found:

```python
@dataclass(frozen=True)
class Discovered:
    vms: list[Existing]
    artifacts: dict[str, Any] = field(default_factory=dict)  # opaque to core
    problems: list[Problem] = field(default_factory=list)  # wrong with the target
```

`problems` carries what is wrong with the *target* rather than with the config: a missing or inactive pool, an orphaned volume, an address libvirt has already leased or reserved, a base image whose size disagrees with the local one. None of those are ownership questions, so `decide()` cannot reach them, and every one of them must stop a deploy — so there has to be a channel, and a list rather than an exception for the same reason `config.load` reports every fault at once. An operator at an air-gapped site should not round-trip once per problem.

Core reads `vms` and `problems`, applies §2's rules, and forwards the record to `prepare` without ever reading `artifacts` — so **core still never learns what a storage volume is**, and `prepare` cannot reach the target at all, which is a guarantee rather than a rule someone has to remember. It also makes "prepare runs after preflight" a type dependency instead of a convention. Do not replace this by caching the fact on the backend instance: `REGISTRY` holds a singleton, so that is process-global mutable state that silently reports "not present" if `prepare` ever runs without `preflight`.

**The session `preflight` used closes before anything is created.** `cli._look` opens one, runs `preflight` inside it and closes it before `decide` sees the result; `_deploy` then hands `prepare` that data rather than a connection, and opens a second session for `create`. Holding the first one open would mean threading it through the pure half of the pipeline to reach the one call that needs it. It also costs nothing to drop: libvirt-python registers no event loop here, so there is no keepalive and no RPC timeout, and a socket that hangs rather than resetting can block the closing RPC and wedge the CLI *after* every object has been made.

**The base image is verified, not merely found.** A partial upload leaves a file whose qcow2 header is intact, so `capacity` reports the full virtual size and the next run trusts it — every overlay then backs onto a truncated image and the VMs fail at random points in boot, on a host where the tool reported success. Compare the volume's `<physical>` against the local golden image's size; they match byte for byte, and `<physical>` arrives in the same `XMLDesc` the orphan-volume check already needs. A mismatch refuses, which covers a *different* image under that name as well as a truncated one. `<physical>` is optional in libvirt's schema and meaningless for non-file pools, so its absence warns rather than refuses. **Settled in the acceptance run:** the uploaded volume's `<physical>` equals the local file's `st_size`, so `create.content.url` streams the source bytes verbatim rather than re-encoding, and the check needs no sentinel-volume fallback.

**`render` stays pure** — config plus the record `prepare` produced, out to a dict. This was the original document's seam and it is right; it was wrong only as the *sole* seam, since it cannot express the I/O that `preflight` and `prepare` now own.

**`create`** takes what `render` produced and makes the objects it describes, returning the inventory map keyed by logical name. That map is the public contract, not whatever the backend's client hands back, so the per-VM record can be reshaped without breaking a consumer of `inventory.json`.

**`destroy`** takes the set `preflight` discovered and tears it down. libvirt does it directly through `python3-libvirt` (§1); another backend uses its own SDK. The seam exists precisely so that choice is per-backend.

### Config composition

Core carries `backend: <enum from the registry>` and `target: {<name>: <sub-schema from the backend>}`, assembled with a generated `if/then` per registered backend — roughly fifteen lines in `config.py`, written once. Adding a backend adds a schema file inside its own package and touches no core file.

Keep the `target.<backend>` nesting. An earlier note suggested flattening it to avoid conditional jsonschema; that held only while no second backend was coming.

### What the fake backend proves

`tests/fake_backend.py`, ~100 lines, shipped in nothing. It earns its place by proving three things interface design alone cannot:

1. Core runs the full pipeline — validate → preflight → prepare → create → destroy — **with no libvirt import anywhere in the call path.** That is the actual test of whether the seam is real.
2. The ownership policy is exercised against a backend with no libvirt semantics: absent → create, ours → skip, unmarked → refuse.
3. Two backends register simultaneously and the config schema composes.

### Three result carriers, and why they stay three

`Outcome`, `Discovered.problems` and `ConfigError.problems` are three
independently-invented ways of saying what went wrong. They are not unified, and
that is a decision rather than an oversight.

They answer different questions at different times: what is wrong with the config
(before anything connects), what is wrong with the target (while connected), and
what a teardown did to each of twenty objects. A single carrier would have to be
the union of three shapes, and every consumer would filter it back down to the
one it started with.

What was actually wrong is that they lost their contents at the consumer. The
rule instead is **each is printed where it arrives**: `load` returns its warnings
to every verb rather than only `validate` re-deriving them,
`Discovered.problems` reaches stderr on all three connected verbs, and `Outcome`
is returned and reported by `cmd_destroy`.

### The log is the only carrier of output

**Reversed, deliberately, one day after #136 shipped the opposite.** The earlier
version of this section is worth stating so the change reads as a decision rather
than a drift: #136 drew the line as *the printout carries the headline, the log
carries the detail*, and argued the log must not become another carrier beside
those above. That framing is gone. Every line vcows writes is now a log line —
timestamped, level-tagged, on stderr — and `print` is absent from `orchestrator/`
and `container/` entirely, gated by `test_logging.test_nothing_prints`.

**What is preserved is the distinction, not the mechanism.** The result carriers
above are still separate, and each is still reported once where it arrives;
`_problem` logs a `Problem` at the level its own `Severity` names and nothing
re-reports it. What changed is that the *level* now does the work the *stream*
used to: `INFO` is the report, `WARNING` is degraded-but-continuing, `ERROR` ends
the run, `DEBUG` is the recovery detail #136 added. A reader who wants the old
stdout/stderr split reads levels instead.

**The convention was weighed, not assumed.** The POSIX CLI convention — stdout is
the result, stderr is diagnostics — is what `git` and `kubectl` follow, and it is
what vcows had. The twelve-factor/container convention treats
all output as one event stream. The second was chosen on the maintainer's call,
with the trade understood: a single stream is materially simpler to maintain (one
logger, one handler, one format, no `propagate` or handler-clearing traps), and
it costs stdout as a separate result channel. Do not re-derive this; if vcows
ever grows a machine-readable stdout mode, that is the point to revisit it.

**Two consequences worth keeping.**

*The one exception is interactive.* `cli._confirm`'s prompt stays on stdout,
unprefixed, because `input()` writes it without a trailing newline so the cursor
stays where the operator types. Being the only unprefixed output there is, it is
trivially separable — which is the reason it is the exception.

*Logging is configured at package import*, in `orchestrator/__init__.py`, not in
`cli.main`. `backends.libvirt.schema` computes `MAX_VCPUS` and its siblings as
module-level constants consumed as literals inside `VM_SCHEMA`, and `_ceiling`
reports a malformed `VCOWS_MAX_*` while doing so — on the import chain, before
`main` is reached. A logger configured in `main` would miss it and the record
would fall through to `logging.lastResort`, which writes to stderr unprefixed and
ignores `VCOWS_LOG_LEVEL`. An import side effect is wrong for a library and right
for an application package. `test_logging` gates it.

*The handler resolves `sys.stderr` when it writes*, not when it is built —
`orchestrator._Stderr`, duplicated in `container/entrypoint.py`. A bound
`StreamHandler` configured at import holds the real stderr and writes past any
later replacement of the stream; measured at 39 failing tests, because that is
exactly what pytest's `capsys` does.

### Explicitly not built

No noop default implementations. No plugin discovery or entry points — an explicit dict in `backends/__init__.py`. No capability negotiation. No per-backend CLI subcommands. No backend-specific exception hierarchies. No `required_tools` declarations driving Containerfile generation.

---

## 4. Remaining findings

**F11 — Draft the `target.libvirt` schema block.** Now unblocked by the decisions above. Needs: `uri` (`qemu+ssh://` form), `ssh_keyfile`, `known_hosts`, `pool` name and whether the tool may create it, `bridge`, per-VM `mac`, per-VM `nics: [{bridge, ip_cidr, gateway, nameservers}]`, `disk_gb`, and firmware mode — Rocky 10 golden images are commonly UEFI, needing `os.firmware = "efi"`, a host-specific loader path, and an NVRAM template, none changeable after creation. This is the one-way door; other groups will author these by hand and keep them in their own version control.

Two rules to settle while drafting: `nics` is a list but the inventory carries one `ip`, so mark a primary (either first-wins or an explicit `primary: true`); and state whether a per-VM value **replaces or merges** with `defaults`. Write replace-not-merge down now — it is invisible until the first nested field, and by then configs exist.

**Inventory** — emit `inventory.json` with a minimal shape (name, addresses) and **document it as unstable**. Nothing consumes it at v0.1 since Ansible is cut; formalize the contract when something actually reads it, and add `inventory_version` at that point.

**Version bump rules** — deferred. The four-digit format is fixed; which digit a marker-format or schema-breaking change bumps can wait until there is a second release.

**F12 — Credentials in cleartext, deliberately temporary.** Consequence to keep visible: `config.yaml` becomes a secret artifact. It cannot be committed or shipped as an example, and its contents flow into the emitted tfvars and the state file. **State encryption is not enabled, and the earlier recommendation to enable it "regardless, it is one block and free" was wrong on both counts.** It is cheaper than that — `TF_ENCRYPTION` is an env var and needs no module change — and it protects the wrong artifact. The secret is `user_data`, and `user_data` lives in the seed ISO, which the run directory keeps deliberately so a VM that will not boot can be debugged from the media it was given. Encrypting the state beside it buys nothing, and a lost passphrase makes unreadable a file that is disposable by design (verified: an encrypted state without its passphrase cannot be read). What is done instead: the run directory is created `0700` and documented as carrying secrets, which is the same handling this paragraph already demands of `config.yaml`.

**R1 — `qemu+ssh://` prerequisites.** Most of the original rootless/SELinux problem evaporates with no local socket. What remains: an SSH private key inside the container at 0600 under the UID map, a `known_hosts` policy (not `no_verify=1`), an `ssh` binary in the image, and — a hypervisor-side prerequisite worth documenting — `nc` or `virt-ssh-helper` present on the *hypervisor*. Note that `vol-upload` over `qemu+ssh://` streams the whole image through the tunnel with no resume; base-plus-overlay means that cost is paid once per host rather than per VM.

**F2 — Seed ISO tooling: decide in the spike.** Compare `pycdlib` (pure Python, no subprocess, vendored as a wheel, removes `xorriso` from the image and the GPL BOM) against `xorrisofs` (already present, battle-tested output, reproducible by hand when a VM will not boot). `mkisofs` does not exist on Rocky 10 or 9 — Rocky ships `xorriso`, no `genisoimage` in BaseOS/AppStream/CRB, and EPEL 10's `genisoimage` does not provide the `mkisofs` binary. Do **not** use `libvirt_cloudinit_disk`: it stages the ISO in `os.TempDir()` and calls `RemoveResource()` when missing, so a container's empty `/tmp` makes the ISO, its volume, and the domain all show as needing recreation every run (issue #1368 open, PR #1369 unmerged).

**F5 — Base volume + per-VM overlay.** `vol-create-as <pool> x.qcow2 20G` followed by `vol-upload` writes source bytes from offset 0 including the golden image's qcow2 header and its `virtual size` field, silently discarding the declared capacity. Upload the golden image once per host; create each VM's disk as an overlay with explicit larger capacity. **Confirm `growpart`/`cloud-init` is in the golden images** — the design depends on it.

**R2 — Offline build gate. In scope.** `podman run --network=none` with `tofu init && tofu plan` succeeding, wired into the build. Set `CHECKPOINT_DISABLE=1`, `PIP_NO_INDEX=1`, `no_proxy=*` so residual egress fails fast rather than hanging.

**R6 — Air-gap packaging. The original §9 is wrong in four ways.** All verified; all fail silently on a connected build host and loudly at a disconnected site.

- **`/etc/tofurc` is not a path OpenTofu reads.** On Linux it reads `$HOME/.tofurc`, `$XDG_CONFIG_HOME/opentofu/tofurc`, `$HOME/.terraformrc`, or `TF_CLI_CONFIG_FILE`. As written the `filesystem_mirror` block is never loaded, OpenTofu falls back to `direct`, and `tofu init` reaches for `registry.opentofu.org` — a DNS hang at the site, not a clean error. Set `ENV TF_CLI_CONFIG_FILE=/opt/tofu/tofurc` explicitly. Worse under rootless with `--user`: a UID with no `/etc/passwd` entry gets `HOME=/`, so even a correctly placed `~/.tofurc` is missed.
- **`tofu providers mirror` mirrors what the *current configuration* requires**, so it runs per module directory, not once. It also resolves the newest version satisfying the constraint at build time while `.terraform.lock.hcl` pins exactly — the module constraint, the mirror contents, and the lock file are three independently drifting facts. Pin exact versions (`version = "= 0.9.8"`), mirror per module into one path, then `tofu providers lock -fs-mirror=/opt/tofu-mirror -platform=linux_amd64` so lock hashes come from the artifacts that actually ship. A lock produced against a registry records different hashes than one produced against a mirror, and the mismatch surfaces as a checksum error that reads like corruption.
- **Run `tofu init` at build time** so the runtime never performs provider installation. This also sidesteps that a runtime `init` needs a **writable** module directory (`.terraform/`, the lock file), which a read-only baked module tree will not provide — copy the pre-initialised tree into a writable work dir at start.

  **Built differently, for a reason found while building it.** The requirement is that the runtime never installs a provider *over a network*, and a filesystem mirror baked into the image meets that: `init` runs per deploy, offline, against `/opt/tofu-mirror`, and the CLI stages the module into a fresh run directory that is writable by construction (D40) — so the read-only-module problem never arises either. What a pre-initialised tree would have hidden is the real cost: **`init` does not symlink into the mirror, it unpacks a 26 MB copy**, and with a new run directory per deploy that recurs forever. `TF_PLUGIN_CACHE_DIR`, warmed at build time, makes `.terraform` symlinks instead — measured at 0 KiB per run against 26 MB. That is a larger recurring saving than the entire base-image question below, and it is the reason to keep `init` in the flow rather than design it out. One consequence for the gate: with the cache warm, `init` is satisfied before it consults the mirror, so the air-gap test disables the cache explicitly or it silently stops testing the mirror.
- **Pin OpenTofu 1.12.6.** The original §13 points at 1.11.x, whose support ended 2026-08-01.

**R7 — `python3-libvirt` comes from the RPM, not pip.** PyPI publishes sdist only — no wheels — so `pip install` needs gcc, `libvirt-devel`, and pkg-config, and default build isolation tries to fetch setuptools from the network, which fails air-gapped. Rocky ships `python3-libvirt` built against the platform Python. **If the application uses a virtualenv, the RPM binding is invisible unless the venv is created with `--system-site-packages`** — this is the obvious thing that bites, and it bites at import time with a confusing `ModuleNotFoundError` on a machine where `python3 -c 'import libvirt'` works fine outside the venv.

**R3 — Pin the provider and vendor its license. In scope.** `dmacvicar/libvirt` 0.9.x ships **no LICENSE file** — 404 at `main` and `v0.9.8`, GitHub API reports `license: null`, and `main` has *no common ancestor* with `v0.8.3`, so nothing was deleted; the Apache grant never carried into the rewrite by lineage. The grant does ship in the bundled README, and Apache-2.0 §4(a) puts the obligation to supply the license text on the redistributor regardless. Vendor the canonical text with a provenance note (upstream URL, tag commit, artifact SHA256, the verbatim README grant, the orphan-history explanation, a pointer to issue #1371). Pin exactly: the `metadata` pinning in `Read()`, the volume `key` passthrough (PR #1334), and the `isImport` branch are all recent, and only the volume one has acceptance-test coverage.

**R4 — Tests. In scope: golden-file, fake backend, `tofu validate` in CI, one manual E2E.** `tofu validate` against emitted tfvars with providers from the mirror is the highest value per line — it catches every missing, misnamed, or mistyped variable without contacting anything. Add `isoinfo` assertions on the seed ISO. §4.2's "trivially unit-testable pure functions" claim was circular: testable was *defined* as pure, and the failure modes that take a site down live in the impure half.

**R5 — Build manifest. In scope.** Tag **`vcows-deploy:<four-digit-semver>`** — an earlier revision said `orchestrator:`, which is the Python package rather than the product, and every other operator-facing name is `vcows`. (The OCI grammar allows `-` and `_`; what it rejects is uppercase. Podman stores an unqualified name as `localhost/vcows-deploy:<version>`.) Embed provider version and checksum, every wheel, and the git SHA; print from `--version` and copy into every run directory. There is still no air-gapped *update* path — §9 explains how to build the mirror and nothing about moving a site from N to N+1, or rolling back. Worth defining before the first external delivery.

**Base image — measured, and left as it is for now.** All three candidates were built and put through the same offline gate, and all three passed. Delivered size (`podman save | gzip`): `rockylinux:10` **152 MB**, `10-minimal` **134 MB**, a builder-stage `10-ubi-micro` **118 MB**. The payload dominates every option — the OpenTofu binary alone is 115 MB uncompressed and the provider another 26 MB — so the base image is worth 18 MB, not a redesign. What minimal costs is `vi`, `less`, `tar`, `ping` and `dnf` (it keeps `sh`, `rpm`, `python3` and `microdnf`) plus a font stack the base does not carry; micro additionally has no `rpm` at runtime, so the image cannot report its own contents at a site, and it needs a two-stage build. **Shipping the standard image for now**, revisit when the size matters more than the tooling. Switching is an `ARG` and a package-manager name, and both variants are known to pass.

**F16 — Fix the OCI labels. In scope.** The image inherits `org.opencontainers.image.licenses="BSD-3-Clause"`, `vendor="Rocky Enterprise Software Foundation"`, `name="rockylinux"`, and RESF authors from the base and overrides none of them — so it is mislabelled and self-identifies to third parties as an RESF product. Override `licenses`, `vendor`, `authors`, `name`, `summary`, `source`, `license`, and add `base.name` + `base.digest`. Six lines.

**F15 — Measure the GPL source sidecar.** The image ships `qemu-img` (GPL-2.0-**only**, so GPLv2 §3 applies with no upgrade path) and `xorriso`. Shipping source on the same media puts you under GPLv3 §6(a) / GPLv2 §3(a), which lets recipients lawfully re-copy to other sites — a written offer alone limits them to "occasionally and noncommercially," which does not describe a group deploying internally. Rocky publishes full source trees (`dl.rockylinux.org/pub/rocky/10/{BaseOS,AppStream}/source/tree/`, `vault/` for pinned point releases), so mirroring them in the same pass costs one more `reposync`. Estimated 1.5–3 GB, **unverified** — this is the only open item that can change media sizing.

**The BOM is no longer the blocker.** Sizing was deferred until the Containerfile froze; it has, and the build manifest now emits the deduplicated `SOURCERPM` list of everything shipped — **116 source packages** behind 160 binaries in the image as built. Mirroring is a `reposync` against a list that already exists rather than a research task. F15's own estimate is for the whole BaseOS + AppStream source trees and remains the wrong number for this obligation.

---

## 5. Cut from v0.1

| Cut | Why |
|---|---|
| §6.1, §6.4, §7, most of §2's table, §11 rows 2–3 | vSphere and Proxmox. ovftool, open-vmdk, `mkova.sh`, content libraries, govc, the `import` content type, the snippet/SFTP analysis. `image/convert.py` deletes outright. Move to `future-backends.md` so the research survives. |
| §4 Stage 3, `ansible/`, `exec/ansible.py` | Nobody has asked for post-config. Golden images already contain cloud-init and `user_data` is in the schema. Stage 3 has no stated purpose, no inputs, and no config surface. It drags in ansible-core, ansible-runner, a second air-gap vendoring pipeline, and the GPLv3 half of F15. **The seam is free: write `inventory.json` with no consumer.** |
| §4 Stages 0 and 4, `hooks/` | No downstream group has asked for a hook, and shipping one freezes an environment contract against zero requirements. An escape valve arriving before real extension points means downstream shells out instead of asking, inside sites you cannot debug. Adding later is backward-compatible; removing is not. Make CLI phases separably invocable instead. |
| §8, `telemetry.py` | Its purpose was correlating two child processes; there is one. The propagation gap it cites closed upstream (issue #3936, shipped in 1.13.0). Air-gap would mean a collector per site. **Reconsidered for #136 and still cut** — see §3's "The log is the only carrier of output", which corrects the #3936 ground and adds the RPM measurement. |
| The `:9` tag | Answered by the Haswell confirmation. Never the one-line diff §3 claims anyway — Rocky 9's platform Python is 3.9, `python3-libvirt` there is built against 3.9 only, and current ansible-core needs 3.12+. |
| S3/MinIO, `lifecycle: oneshot \| stateful` | State is always written. §10 conflated *shared* state with *a record of what you created*; "one-shot" should have meant unshared, not amnesiac. |

Extensibility is preserved through the seams in §3 and signalled through the version number. Breaking changes bump it.

---

## 6. Verification

**Spike first** — these settle open questions and are cheap:
1. `pycdlib` vs `xorrisofs` for the seed ISO (F2). Build one both ways, compare complexity and vendoring cost.
2. Marker round trip: define with the JSON payload, `dumpxml`, confirm it comes back intact and un-reindented. **Done** — through the OpenTofu create path on 2026-08-29, and across a `virtqemud` restart; see §2.
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

**Testbed gap worth closing — and it is narrower than this section originally claimed.** The rig is Fedora 44; the likely targets are RHEL 9 and RHEL 10, and Fedora being *newer* means it will not surface failures that run in that direction. But the specific claim that "RHEL 9 ships a much older libvirt than the Rocky 10 container's client library" is **no longer true**, verified directly against `dl.rockylinux.org` in Stage 3: Rocky 9 current (9.8) ships `libvirt-daemon-11.10.0-12.1.el9_8`, Rocky 10 current (10.2) ships `libvirt-daemon-11.10.0-12.1.el10_2`, and the container's client is the same 11.10.0. The rig is one minor ahead at 12.0.0, not a generation. Every `undefineFlags` bit vcows passes has existed since libvirt 8.9.0, so the version gate in `destroy.py` only earns its keep on 9.0/9.1 EUS (8.0.0 / 8.5.0), which reject `TPM`.

What remains genuinely untestable on this rig is narrower and worth naming precisely: every rig domain carries a `<tpm>`, so the TPM undefine bit is exercised there by accident and may be untested against a target guest that has none; and the rig writes `<name>_VARS.qcow2` from a qcow2 OVMF template while Rocky 9 and Rocky 10 `edk2-ovmf` ship only raw `.fd` templates. Get a RHEL 9 target in front of this before the first external delivery, but scope the expectation to those, not to a general version gap. **The full list of what such a host would settle is `docs/rhel9-target.md`**, written after the 2026-08-29 rig session narrowed it — including the point that "a RHEL 9 host" is three different machines and a current 9.8 box answers neither the flag shed nor the cloud-init question.

**Definition of done for v0.1:** one `config.yaml` produces N running, cloud-init'd, reachable VMs on libvirt from a golden qcow2 over `qemu+ssh://`; `destroy` removes exactly what it created, **with the state file deleted**; a renamed VM is still recognized; an unmarked name collision is refused; and the container builds and runs fully offline.

**The rig gate ran for the first time on 2026-08-29, and the suite has now run with nothing skipped.** `tests/test_libvirt_rig.py` was written in Stage 3 and never executed until then: 15 tests, read-only apart from `pool.refresh(0)`, all passing against `qemu+ssh://vcows@vcows/system`. With `VCOWS_GATES=all` and the rig and image gates both supplied, the full suite is **390 passed, 0 skipped** — the first run in which no gate reported itself unlooked-at. Two of those fifteen are worth naming because they assert facts about a live host rather than behaviour: the daemon version differing from the client's (12.0.0 against 11.10.0, which is what `undefine_mask` gates on), and a running domain's disks resolving only after the refresh, which is the live case for the precondition §2 calls the rule that inverts without it.

**On `<os firmware='efi'>` beside a pinned loader (review 2.15), the modern half is answered.** libvirt 12.0.0 honours the pin exactly: the deployed `app02` came back carrying its configured `/usr/share/edk2/ovmf/OVMF_CODE_4M.qcow2` and its named template, with `secure-boot` and `enrolled-keys` both `no`, while the autoselected `app01` got `OVMF_CODE_4M.secboot.qcow2` with both `yes`. The pin is not ignored and the two do not collide. That is *not* the finding 2.15 asks for — the question is whether **old** libvirt honours it, and this rig cannot be made old — but it removes the possibility that the construct is wrong in principle.

**Corrected 2026-08-31 (#107): the construct is wrong, and the rig could not see it.** The paragraph above is accurate and stays — libvirt 12.0.0 did honour that pin. It honoured it because *that rig's* firmware descriptors carry qcow2. Autoselection neither overrides nor defers: beside `firmware = "efi"`, libvirt validates the pin against the host's own descriptors and refuses a format they do not carry. Measured on an Ubuntu 24.04 runner whose four descriptors all declare `raw`, the same qcow2 pin is refused at `virsh define`. So a rig that agrees with the pin cannot distinguish "honoured" from "validated and happened to pass", and the deciding fact about a target is its descriptor set rather than its libvirt version. `main.tf` now emits `firmware = "efi"` only when no loader is set, which takes a pinned domain out of autoselection entirely and is what makes RHEL's raw `.fd` shape deployable.

**Met on 2026-08-29.** Items 7, 9, 10 and 11 above are closed; the full record, including the five defects the run found and what each cost, is in `acceptance.md`. Two of those are worth carrying here because they change what this document said:

- **`preflight` and the apply need different SSH transports.** libvirt does not recognise `sshcmd` at all; the provider's `ssh` dials a hardcoded monolithic `/var/run/libvirt/libvirt-sock` through a forward SELinux refuses, so it cannot reach a split-daemon host. One config, two schemes. The provider's `sshcmd` dialer already tries `virt-ssh-helper` first and falls back to `nc -U`, so the monolithic case needs nothing built.
- **`ssh_keyfile` and `known_hosts` cannot travel in the URI.** libvirt ignores `known_hosts` (libssh-only), the provider's `ssh` dialer spells it `knownhosts`, `sshcmd` fails on either, and `HOME` is not a lever because both resolve `~` from the passwd entry. Both run `ssh`, so the container's entrypoint writes `~/.ssh/config` instead. R-D still earns its keep: refusing an operator query string is what keeps `no_verify=1` off the connection, and refusing a password keeps a credential out of the rendered tfvars — `connection_uri` replaces the scheme and clears the query, but the netloc travels verbatim.

**D3 remains open.** The run used the stock `Rocky-9-GenericCloud-Base` stand-in, so the real golden artifact is still unverified.

---

## Appendix — errata in `orchestrator-architecture.md`

That document is archived as background. It is a good survey, but it was written before any code existed and several of its concrete claims are wrong. Do not copy commands or claims out of it without checking here first.

| Section | Claim | Reality |
|---|---|---|
| §11, §6.4 | `mkisofs -o seed.iso -V cidata ...` | `mkisofs` does not exist on Rocky 10 **or** 9. Rocky ships `xorriso`; no `genisoimage` in BaseOS/AppStream/CRB; EPEL 10's `genisoimage` does not provide the binary. The "xorriso provides mkisofs" behaviour is Debian/Arch. |
| §9 | Write mirror config to `/etc/tofurc` | Not a path OpenTofu reads. See R6. |
| §9 | One `tofu providers mirror` invocation | Mirrors only what the current config requires; must run per module. See R6. |
| §6.2 | `vol-create-as <pool> x.qcow2 20G` then `vol-upload` | Upload writes from offset 0 including the golden image's qcow2 header, silently discarding the declared capacity. See F5. |
| §2 | `dmacvicar/libvirt` — Apache-2.0, unqualified | True only for ≤ 0.8.3. 0.9.x ships no LICENSE file, and `main` has no common ancestor with `v0.8.3`. See R3. |
| §13 | OpenTofu 1.11.6 / 1.12.0 | 1.12.6 current; 1.11.x support ended 2026-08-01. |
| §8 | TRACEPARENT does not propagate; issue #3936 open | Closed 2026-04-15 and shipped in 1.13.0. §5.3's "human output or JSON, pick one" is also stale — `-json-into` shipped in 1.12.0 as a general CLI argument. |
| §5.3 | `TofuRunner`: `Popen` with `-json`, read stdout line by line | Measured against 1.12.6 and retired. `-json-into=<file>` writes the same stream to a file *while* human-readable output still goes to stdout, on `plan`, `apply`, `init` and `output` — so tofu inherits stdout and the file is parsed after exit. That removes the NDJSON reader, the two-pipe deadlock and any threading, and it keeps the operator's view of a multi-GB upload. |
| — | (not claimed anywhere, and easy to assume) | **`NO_COLOR` is not honoured by OpenTofu 1.12.6.** Colour is written even when stdout is a file — 11 escape sequences into a redirected plan. Only the `-no-color` flag suppresses it, so anything capturing or piping tofu's output must pass the flag rather than set the variable. |
| §2, §9, §12 | `community.vmware` needs pyVmomi; pin pyVmomi | 6.x replaced pyVmomi with `vcf-sdk`. Irrelevant to this MVP, but the mitigation is obsolete in three places. |
| §3, §12.1 | `:9` tag differs only in `FROM` and Python version | Rocky 9's platform Python is 3.9, `python3-libvirt` there is built against 3.9 only, and current ansible-core needs 3.12+. Moot — Haswell confirmed, Rocky 10 only. |
| §4.1 | Bind mounts need `:Z` | `:Z` applies a *private exclusive* label; on a shared `/srv/images` it relabels the directory out from under libvirtd. Use `:z` for shared read-only mounts. Largely moot under `qemu+ssh://`. |
| §4.2 | "A fourth backend is one new file plus one registry entry" | ~13 touch points across 5 layers. §3 of this document is the correction. |
| §10 | Default to one-shot, ephemeral state | Conflates *shared* state with *a record of what you created*. The marker (§2) is the actual answer. |
| §4.3 | The `backend: proxmox` sample config | Written before there was a schema to check it against, and nothing in it loads. Per `orchestrator/backends/proxmox/schema.py`: `target.proxmox` is `endpoint`, `node`, `datastore`, `import_datastore` and optional `insecure`, and the last two stores are usually different. There is no `api_token_env` — the variable name is fixed as `schema.TOKEN_ENV`, because `tofu._env()` passes `os.environ` through untouched and a configurable name would mean vcows moving the secret itself. `bridge` is per NIC, not per target. `lifecycle`, `state`, `defaults` and `image.distro` do not exist. The working shape is the Proxmox example under "The config" in `README.md`. |
| §6.3 | `download_file` and an orchestrator-side HTTP server are the primary image path; `proxmox_virtual_environment_file` "alternatively" pushes | Backwards. The shipped module uses `proxmox_virtual_environment_file` with `content_type = "import"` and a local `source_file.path`; the provider uploads through PVE's HTTP API and vcows serves nothing. See the `prepare` correction in §3. |
| §6.3 | PVE before 8.4 rejects `.qcow2` on download-url; rename to `.img` | True of the download-url endpoint only, which nothing here calls. The upload endpoint takes `.qcow2`, and `schema.IMPORT_SUFFIXES` warns when `base_volume_name` lacks a disk-image extension rather than renaming anything. |

Sound and worth keeping: the JSON-not-HCL decision, the §5.2 verdicts (no `python-terraform`, no CDKTF, no direct gRPC to providers), the x86-64-v3 reasoning, and the ovftool licensing conclusion.
