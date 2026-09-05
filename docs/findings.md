# vcows-deploy — v0.1 architecture and open work

## Context

`docs/archive/orchestrator-architecture.md` surveys a three-backend, four-stage, air-gapped VM deployment container. This document is the design reference for the marker, the pipeline, the backend interface and the result carriers.

**A prior implementation was built and discarded because it became sprawling.** That governs everything here. The cuts in §5 are as much of the deliverable as the design is, and any addition has to earn its surface area.

### Settled

| | |
|---|---|
| Backend | **Two ship, `libvirt` and `proxmox`** — `REGISTRY` in `orchestrator/backends/__init__.py`. The seams are §3. |
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

`create.domain_xml` writes the `<vcows>` element inside `<metadata>` in the document handed to `DomainDefineXML`. libvirt deep-copies the subtree (`xmlCopyNode(node, 1)`) and persists it to `/etc/libvirt/qemu/<name>.xml`, surviving libvirtd restart and host reboot. Malformed marker XML fails loudly at define time, which is the right failure mode. Measured on the rig: across a `systemctl restart virtqemud`, `vcows-probe02`'s payload came back byte-identical, un-reindented, and still found by marker rather than by name. Host reboot rests on the same persistence and is inferred.

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

**`UNDEFINE_NVRAM` removes the varstore — observed.** Two EFI domains on the rig, one with libvirt autoselecting firmware and one with a pinned `loader` and `nvram_template`, both wrote a varstore at define time — `app01_VARS.qcow2` and `app02_VARS.qcow2`, the pinned one from the qcow2 template it named. A `destroy` removed both, leaving `/var/lib/libvirt/qemu/nvram/` holding exactly the four varstores that predated the run, with a root-side watcher sampling the directory every two seconds throughout. **Scope it to what ran:** the qcow2 template path, on libvirt 12.0.0. Rocky 9 and Rocky 10 ship raw `.fd` OVMF templates this rig does not have, and what a flag-shed retry down to `FLOOR` leaves behind on 9.0/9.1 EUS is unknown; both halves need an old-libvirt target. They matter because a surviving varstore means a redeploy under the same name inherits the previous VM's UEFI variables, including its boot entries.

**A split-daemon host cannot present a live storage driver with a dead domain driver — measured.** With `virtqemud` and its sockets stopped and `virtstoraged` left running, `virtstoraged-sock` is still listening and nothing can reach it: every client enters through `virtqemud-sock`, so the connection never opens. `virsh` on the rig gets the same refusal vcows does — `virt-ssh-helper: cannot connect to '/var/run/libvirt/virtqemud-sock'`, `VIR_ERR_SYSTEM_ERROR` from `VIR_FROM_REMOTE`, before any driver call. So vcows never holds a working connection whose domain driver is absent, and the stale-target window `_reverify` closes is a race against another operator rather than a driver asymmetry. `ERR_NO_DOMAIN` covers the race and needs to cover nothing else.

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

Adding a backend should require no edit to any core file. Nothing speculative is implemented — every seam is a signature or a directory boundary.

**One block does not hold, and it is the same one in both places.** `config.IMAGE_SCHEMA` is written in qcow2 and libvirt terms — `source_qcow2` and `base_volume_name` are the field *names* — and it is wired into the core schema directly rather than composed from the registry the way `target` is. A backend wanting an OVA path or a template id edits `config.py`. The format reader behind it, `orchestrator/qcow2.py`, is a core module imported by exactly one backend. Proxmox reuses both field names as they stand, so the cost has not been paid: that is two core sites, not a layer, and a backend wanting different names is what would make moving them worth it. Recorded rather than fixed, so the seam claim above reads as "one known exception" rather than "verified complete".

### A backend is a package

```
orchestrator/
  backends/
    __init__.py          # REGISTRY = {"libvirt": LibvirtBackend(), "proxmox": ProxmoxBackend()}
    base.py              # ABC, shared records, and `decide()` -- the ownership policy
    libvirt/
      __init__.py        # the Backend implementation
      schema.py          # the target.libvirt sub-schema
      preflight.py       # marker read + collision detection
      render.py          # pure: config -> the values create consumes
      create.py          # python3-libvirt upload, overlay, define
      destroy.py         # python3-libvirt teardown
      errors.py          # the libvirt error constants, pinned against the real ABI
    proxmox/
      __init__.py        # the Backend implementation
      schema.py          # the target.proxmox sub-schema
      api.py             # the proxmoxer session
      preflight.py       # marker read + collision detection
      render.py          # pure: config -> the values create consumes
      create.py          # proxmoxer upload, import, define
      destroy.py         # proxmoxer teardown
  cloudinit.py           # seed ISO, MAC derivation, the NIC addressing checks
  config.py              # core schema; composes `target` from the registry
  imagecheck.py          # digest and capacity checks on the golden image
  limits.py              # the VM size ceilings and their environment overrides
  marker.py              # payload serialize/parse; shared by every backend
  problems.py            # Severity/Problem; core and every backend produce them
  qcow2.py               # the qcow2 header reader
  cli.py
tests/
  fake_backend.py        # in-memory, test-only
```

### The interface

```python
class Backend(ABC):
    @abstractmethod
    def config_schema(self) -> dict: ...  # target.<name> sub-schema
    @abstractmethod
    def validate(self, cfg: dict, *, verify_digest: bool = True) -> list[Problem]: ...
    @abstractmethod
    def connect(self, cfg: dict) -> AbstractContextManager[Any]: ...
    @abstractmethod
    def preflight(self, cfg: dict, session: Any) -> Discovered: ...
    def prepare(  # concrete; the dict is opaque to core
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> dict[str, Any]: ...
    @abstractmethod
    def create(self, cfg: dict, session: Any, prepared: dict[str, Any]) -> dict: ...
    @abstractmethod
    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome: ...
```

`validate`'s `verify_digest` is false only for `destroy`, which never reads the
golden image and should not pay `imagecheck.check_image_digest`'s whole-file
hash. It is a cost seam, not a safety one: a backend that ignores the flag is
correct and slow.

**ABC with `@abstractmethod`, and one default implementation.** The thing to avoid is *noop defaults*, not ABCs — a backend that forgets `destroy` and inherits a no-op deletes nothing and exits successfully; one that forgets `preflight` skips the safety check entirely. An ABC fails loudly at instantiation, which beats a `Protocol` that only complains if someone remembers to run mypy. `prepare` is concrete on `Backend`: the two shipped overrides were the same body bar the artifact key. It is the only method that passes the test this paragraph sets — forgetting to override it builds the seed ISOs and forwards every artifact, and a backend needing more fails in `create` on the key it did not build.

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

**`prepare` builds what `create` consumes, and reaches nothing.** It writes the seed ISOs and returns; `orchestrator/cloudinit.py` builds them, because nothing in that is hypervisor-specific. It also forwards whatever `preflight` found — libvirt's `base_volume`, Proxmox's `image` — because the pure half has no way to ask a pool for its target directory or a storage for the `import` content it already holds. Neither backend writes any of that itself: `Backend.prepare` does it for both, and being written in core is what makes "reaches nothing" structural rather than a rule per backend. The `workdir` parameter is sized for vSphere: a qcow2→VMDK→OVA conversion needs it plus a cache path, and a conversion product that must exist for the create has to live somewhere. That has no place in a four-stage pipeline as the original document describes it, and retrofitting it would mean restructuring, whereas it costs nothing today.

**vcows serves nothing.** `create.upload` posts a local file to PVE's own upload endpoint for the `import` and `iso` content types, so the Proxmox backend holds no listening socket and reaches for no download-url API. The `workdir` seam stands on the vSphere conversion above, which is the case that needs it.

**`prepare` takes what `preflight` found, and `preflight` is the only method that reads the target.** `create` only ever creates. The shared base image is created once per hypervisor and reused by every deployment's overlays, so from the second deploy onward it must not be created again, and the overlay needs its path on the host. That cannot be answered from the config, because the pool is someone else's and its target path is a property of the pool.

So a live connection has to ask — but `preflight` is **already** asking. The orphan-volume refusal below requires it to enumerate the pool and resolve every volume's path, so the base image's presence, path and size are a lookup on data it is holding. Handing the session to `prepare` would buy a second round trip, not a first, and would let `prepare` reach the hypervisor for anything else besides. Instead `preflight` returns everything one walk found:

```python
@dataclass(frozen=True)
class Discovered:
    vms: tuple[Existing, ...]
    artifacts: dict[str, Any] = field(default_factory=dict)  # opaque to core
    problems: tuple[Problem, ...] = ()  # wrong with the target
```

`vms` and `problems` are tuples because `frozen=True` blocks rebinding only, and
a list field would leave `d.problems.append(...)` working on the one record that
crosses from the connected half of the pipeline into the pure half. `artifacts`
stays a dict because it is genuinely opaque.

`problems` carries what is wrong with the *target* rather than with the config: a missing or inactive pool, an orphaned volume, an address libvirt has already leased or reserved, a base image whose size disagrees with the local one. None of those are ownership questions, so `decide()` cannot reach them, and every one of them must stop a deploy — so there has to be a channel, and a list rather than an exception for the same reason `config.load` reports every fault at once. An operator at an air-gapped site should not round-trip once per problem.

Core reads `vms` and `problems`, applies §2's rules, and forwards the record to `prepare` without ever reading `artifacts` — so **core still never learns what a storage volume is**, and `prepare` cannot reach the target at all, which is a guarantee rather than a rule someone has to remember. It also makes "prepare runs after preflight" a type dependency instead of a convention. Do not replace this by caching the fact on the backend instance: `REGISTRY` holds a singleton, so that is process-global mutable state that silently reports "not present" if `prepare` ever runs without `preflight`.

**The session `preflight` used closes before anything is created.** `cli._look` opens one, runs `preflight` inside it and closes it before `decide` sees the result; `_deploy` then hands `prepare` that data rather than a connection, and opens a second session for `create`. Holding the first one open would mean threading it through the pure half of the pipeline to reach the one call that needs it. It also costs nothing to drop: libvirt-python registers no event loop here, so there is no keepalive and no RPC timeout, and a socket that hangs rather than resetting can block the closing RPC and wedge the CLI *after* every object has been made.

**The base image is verified, not merely found.** A partial upload leaves a file whose qcow2 header is intact, so `capacity` reports the full virtual size and the next run trusts it — every overlay then backs onto a truncated image and the VMs fail at random points in boot, on a host where the tool reported success. Compare the volume's `<physical>` against the local golden image's size; they match byte for byte, and `<physical>` arrives in the same `XMLDesc` the orphan-volume check already needs. A mismatch refuses, which covers a *different* image under that name as well as a truncated one. `<physical>` is optional in libvirt's schema and meaningless for non-file pools, so its absence warns rather than refuses. **Settled in the acceptance run:** the uploaded volume's `<physical>` equals the local file's `st_size`, so `create.content.url` streams the source bytes verbatim rather than re-encoding, and the check needs no sentinel-volume fallback.

**`render` stays pure** — config plus the record `prepare` produced, out to a dict. This was the original document's seam and it is right; it was wrong only as the *sole* seam, since it cannot express the I/O that `preflight` and `prepare` now own.

**`create`** takes what `render` produced and makes the objects it describes, returning the inventory map keyed by logical name. That map is the public contract, not whatever the backend's client hands back, so the per-VM record can be reshaped without breaking a consumer of `inventory.json`.

**`destroy`** takes the set `preflight` discovered and tears it down. libvirt does it directly through `python3-libvirt` (§1); another backend uses its own SDK. The seam exists precisely so that choice is per-backend.

### Config composition

Core carries `backend: <enum from the registry>` and `target: {<name>: <sub-schema from the backend>}`, assembled with a generated `if/then` per registered backend — one comprehension in `config.py`, written once. Adding a backend adds a schema file inside its own package and touches no core file.

Keep the `target.<backend>` nesting. An earlier note suggested flattening it to avoid conditional jsonschema; that held only while no second backend was coming.

### What the fake backend proves

`tests/fake_backend.py`, in-memory and shipped in nothing. It earns its place by proving three things interface design alone cannot:

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

Every line vcows writes is a log line — timestamped, level-tagged, on stderr —
and `print` is absent from `orchestrator/` and `container/` entirely, gated by
`test_logging.test_nothing_prints`.

**The level carries what a stream would.** `INFO` is the report, `WARNING` is
degraded-but-continuing, `ERROR` ends the run, `DEBUG` is recovery detail, and a
reader wanting a stdout/stderr split reads levels. The result carriers above stay
separate under it: `_problem` logs a `Problem` at the level its own `Severity`
names, once, where it arrives.

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
`cli.main`. `limits` computes `MAX_VCPUS` and its siblings as module-level
constants, consumed as literals inside `backends.libvirt.schema`'s `VM_SCHEMA`,
and `_ceiling` reports a malformed `VCOWS_MAX_*` while doing so — on the import
chain, before `main` is reached. A logger configured in `main` would miss it and the record
would fall through to `logging.lastResort`, which writes to stderr unprefixed and
ignores `VCOWS_LOG_LEVEL`. An import side effect is wrong for a library and right
for an application package. `test_logging` gates it.

*The handler resolves `sys.stderr` when it writes*, not when it is built —
`orchestrator._Stderr`. A bound `StreamHandler` configured at import holds the
real stderr and writes past any later replacement of the stream; measured at 39
failing tests, because that is exactly what pytest's `capsys` does.

### Explicitly not built

No noop default implementations. No plugin discovery or entry points — an explicit dict in `backends/__init__.py`. No capability negotiation. No per-backend CLI subcommands. No backend-specific exception hierarchies. No `required_tools` declarations driving Containerfile generation.

---

## 5. Cut from v0.1

| Cut | Why |
|---|---|
| §6.1, §6.4, §7, most of §2's table, §11 rows 2–3 | vSphere and Proxmox. ovftool, open-vmdk, `mkova.sh`, content libraries, govc, the `import` content type, the snippet/SFTP analysis. `image/convert.py` deletes outright. Move to `docs/research/future-backends.md` so the research survives. |
| §4 Stage 3, `ansible/`, `exec/ansible.py` | Nobody has asked for post-config. Golden images already contain cloud-init and `user_data` is in the schema. Stage 3 has no stated purpose, no inputs, and no config surface. It drags in ansible-core, ansible-runner, a second air-gap vendoring pipeline, and a GPLv3 half of the source-medium obligation. **The seam is free: write `inventory.json` with no consumer.** |
| §4 Stages 0 and 4, `hooks/` | No downstream group has asked for a hook, and shipping one freezes an environment contract against zero requirements. An escape valve arriving before real extension points means downstream shells out instead of asking, inside sites you cannot debug. Adding later is backward-compatible; removing is not. Make CLI phases separably invocable instead. |
| §8, `telemetry.py` | Its purpose was correlating two child processes; there is one. The propagation gap it cites closed upstream (issue #3936, shipped in 1.13.0). Air-gap would mean a collector per site. Cut; see §3's "The log is the only carrier of output". |
| The `:9` tag | Answered by the Haswell confirmation. Never the one-line diff §3 claims anyway — Rocky 9's platform Python is 3.9, `python3-libvirt` there is built against 3.9 only, and current ansible-core needs 3.12+. |
| S3/MinIO, `lifecycle: oneshot \| stateful` | State is always written. §10 conflated *shared* state with *a record of what you created*; "one-shot" should have meant unshared, not amnesiac. |

Extensibility is preserved through the seams in §3 and signalled through the version number. Breaking changes bump it.

