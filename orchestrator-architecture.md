# Portable, Air-Gapped Orchestrator Container
## Golden VM Deployment to vSphere, KVM/libvirt, and Proxmox VE

**Revision 2** — adds Proxmox VE as a third backend, moves the base image to Rocky Linux 10, demotes `govc` from the critical path, formalises the four-stage architecture, and adds observability and Python-integration sections.

---

## 1. TL;DR

- Build a single Podman orchestrator container on **Rocky Linux 10**, bundling OpenTofu (MPL-2.0), ansible-core (GPL), mirrored providers and vendored collections. Ship the 1–3 Rocky golden qcow2 images as a **separate artifact**.
- The architecture is **four staged phases**: a **Python wrapper** that validates one YAML config and emits backend-specific variables → **OpenTofu** for provisioning and upload → **Ansible** for post-config → an optional **hooks directory** for pre/post extension.
- **Rocky 10 requires the x86-64-v3 microarchitecture** (Haswell / Excavator or newer). Container userspace does not inherit the host baseline, so a Rocky 10 orchestrator will fault on a pre-Haswell hypervisor even where that host's own RHEL 9 runs fine. Build the same Containerfile as `:10` and `:9` tags if any site has older hardware.
- **The biggest licensing trap is VMware's ovftool**, which is proprietary, internal-use-only, and non-redistributable. Replace it with `qemu-img` + `open-vmdk` for conversion and OpenTofu's vSphere provider (which embeds govmomi) for upload. `govc` is now **optional** — a debugging convenience, not a dependency.
- **Neither OpenTofu nor Ansible offers a real in-process Python API.** Both are driven by subprocess + structured JSON. Ansible ships `ansible-runner` to do that for you; for OpenTofu you write roughly 150 lines against documented, version-stable JSON formats. This is a smaller difference than it appears.
- **Lifecycle: default to one-shot.** Offer stateful OpenTofu as an opt-in, per-target, with state on a bind-mounted volume or self-hosted MinIO.
- **Both OpenTofu and Ansible support OpenTelemetry tracing today**, but trace context does not propagate between them. The Python wrapper must own the root span and inject `TRACEPARENT`.

---

## 2. Licensing verdicts

Everything below is free and redistributable unless flagged. This is the hard requirement, so it leads.

| Component | License | Free? | Notes |
|---|---|---|---|
| OpenTofu | MPL-2.0 (Linux Foundation, CNCF) | Yes | Use instead of Terraform. OTel tracing since 1.10. |
| HashiCorp Terraform | BUSL-1.1 | **Avoid** | Source-available, not open source. Relicensable at will. |
| `vmware/terraform-provider-vsphere` | MPL-2.0 | Yes | Maintained by VMware/Broadcom; works with OpenTofu. |
| `dmacvicar/terraform-provider-libvirt` | Apache-2.0 | Yes | Actively maintained; v0.9.x is a Plugin Framework rewrite. |
| `bpg/terraform-provider-proxmox` | MPL-2.0 | Yes | v0.110.0; supports PVE 9.x; requires OpenTofu 1.6+. |
| ansible-core | GPL-3.0-or-later | Yes | The free upstream engine. |
| Red Hat Ansible Automation Platform | Paid | **Avoid** | Not needed. EE base images are entitlement-gated. |
| `community.vmware` | GPL-3.0+ | Yes | Community-maintained; needs pyVmomi. See §9 caveats. |
| `vmware.vmware` / `vmware.vmware_rest` | GPL-3.0+ | Yes (upstream) | "Certified" but the code is GPL; only *support* needs AAP. |
| `community.libvirt` / `community.general` / `ansible.posix` | GPL-3.0+ | Yes | |
| `community.proxmox` | GPL-3.0+ | Yes | Split out of community.general; needs `proxmoxer>=2.0`. |
| `ansible-runner` | Apache-2.0 | Yes | The supported programmatic interface to Ansible. |
| pyVmomi / govmomi / open-vmdk / proxmoxer | Apache-2.0 / MIT | Yes | Freely redistributable. |
| **VMware ovftool** | **Proprietary** | **NO** | Internal use only, non-transferable, no redistribution rights. Cannot be baked into the image. |
| Rocky Linux 10 base image | BSD + various FOSS | Yes | No redistribution caveats (unlike UBI + non-UBI RPMs). |
| HashiCorp Packer / Vagrant | BUSL-1.1 | **Avoid** | Use osbuild / image-builder / bootc instead. |

**The ovftool finding in detail.** Broadcom's SDK License Agreement restricts ovftool to the licensee's internal operations and grants no distribution rights of any kind. Downloading it is free but requires an authenticated Broadcom account plus EULA acceptance, so it cannot be fetched non-interactively during an air-gapped build either. Community ovftool containers refuse to ship the binary for exactly this reason. It is out.

*If your legal posture requires certainty here, have counsel read the current Broadcom SDK EULA — the conclusion is well-supported but the verbatim terms sit behind Broadcom's legal pages.*

---

## 3. Base image: Rocky Linux 10

**Decision: Rocky 10 (10.2, May 2026), with a Rocky 9 fallback tag.**

Why Rocky over UBI9: UBI is freely redistributable, but adding any non-UBI RHEL RPM re-imposes the RHEL EULA and breaks redistribution for recipients without a subscription. Rocky sidesteps the question entirely. Avoid Alpine — musl breaks pyVmomi wheels and libvirt-python bindings.

**The constraint you must plan around:** Rocky 10 dropped x86-64-v2; x86-64-v3 (AVX, AVX2, BMI1/2, FMA — Intel Haswell+ or AMD Excavator+) is the baseline. Containers share the host kernel but ship their own userspace, so v3-compiled binaries will SIGILL on older CPUs regardless of what the host distro is. This collides directly with the "orchestrator may live on the hypervisor itself, on different systems" requirement.

Mitigation: one Containerfile, two tags. `orchestrator:10` for modern hosts, `orchestrator:9` for older ones. The only difference is the `FROM` line and the Python version.

Other Rocky 10 notes: Python 3.12; Podman 5 with `crun` as the default runtime and cgroups v2; `slirp4netns` networking deprecated.

---

## 4. Reference architecture

The proposed four-stage layout is sound. Each stage has a distinct failure mode and a distinct extension surface, which is exactly what makes it maintainable by hand and extensible by downstream groups. Below is the validated version with refinements.

```
┌─────────────────────────────────────────────────────────────┐
│ Stage 0 — hooks/pre.d/            (optional, drop-in)        │
├─────────────────────────────────────────────────────────────┤
│ Stage 1 — Python wrapper                                     │
│   • load + jsonschema-validate config.yaml                   │
│   • apply defaults, resolve golden image + verify checksum    │
│   • emit  <backend>.auto.tfvars.json                         │
│   • convert qcow2 → VMDK/OVA  (vSphere target only)          │
├─────────────────────────────────────────────────────────────┤
│ Stage 2 — OpenTofu                                           │
│   • static per-backend modules (tofu/vsphere|libvirt|proxmox)│
│   • upload image + create VMs                                │
│   • state: ephemeral (default) or /state mount (opt-in)      │
│   • handoff:  tofu output -json  →  inventory.json           │
├─────────────────────────────────────────────────────────────┤
│ Stage 3 — Ansible (via ansible-runner)                       │
│   • post-config against the freshly created VMs              │
│   • roles drop-in from a bind-mountable path                 │
├─────────────────────────────────────────────────────────────┤
│ Stage 4 — hooks/post.d/           (optional, drop-in)        │
└─────────────────────────────────────────────────────────────┘
```

### 4.1 Refinements worth adopting

**Generate JSON, not HCL.** OpenTofu natively reads `*.tf.json` and `*.auto.tfvars.json`. Keep your `.tf` modules static and hand-written; have Python emit only *variable values* as JSON via `json.dump()`. You get zero string templating, zero HCL quoting and escaping bugs, and the modules stay readable and reviewable. Templating HCL text is the single most common way these wrappers rot.

**Name the conversion stage explicitly.** qcow2 → streamOptimized VMDK → OVA is neither a Tofu nor an Ansible concern, and it is the slowest, most failure-prone step. Give it its own module, its own cache directory, and its own checksum verification. Skip it entirely for libvirt and Proxmox, which consume qcow2 natively.

**Define the Tofu → Ansible contract.** Use `tofu output -json` piped into a static inventory file rather than a dynamic inventory plugin. It is simpler, works air-gapped with no extra collection, and is trivially inspectable when something goes wrong. Declare a fixed output shape (`vm_name → {ip, hostname, backend}`) that every backend module must satisfy — this is what keeps the backends genuinely interchangeable.

**Define hook semantics before you ship them.** Lexical ordering (`10-foo.sh`, `20-bar.sh`), a documented environment contract, and an explicit answer to "does a non-zero exit abort the run?" Undefined hook failure semantics is how a drop-in directory becomes a support burden. Recommendation: pre-hooks abort, post-hooks warn.

**Guard the state mount.** State contains secrets in plaintext. If you mount it, use OpenTofu's native state encryption (free, with external key provider support). On Rocky with SELinux enforcing, bind mounts need `:Z`; under rootless Podman, watch UID mapping on the state directory.

### 4.2 Repo layout

```
orchestrator/
  Containerfile                     # Rocky 10 base (+ :9 variant)
  tofurc                            # filesystem_mirror, no direct{} block
  requirements.txt                  # pinned; vendored as wheels

  orchestrator/                     # the Python wrapper
    __init__.py
    config/
      schema.py                     # jsonschema definitions
      load.py                       # pure: bytes -> validated dict
      defaults.py                   # pure: dict -> dict
    render/
      vsphere.py                    # pure: config -> tfvars dict
      libvirt.py
      proxmox.py
    image/
      convert.py                    # qcow2 -> vmdk -> ova
      checksum.py
    exec/
      tofu.py                       # TofuRunner: subprocess + NDJSON
      ansible.py                    # ansible-runner wrapper
      hooks.py                      # pre.d / post.d discovery + exec
    telemetry.py                    # OTel root span, TRACEPARENT injection
    cli.py                          # argparse / prompts

  tests/                            # mirrors the package tree
    config/test_load.py
    render/test_vsphere.py
    exec/test_tofu.py
    ...

  tofu/
    vsphere/    main.tf variables.tf outputs.tf
    libvirt/    main.tf variables.tf outputs.tf
    proxmox/    main.tf variables.tf outputs.tf

  ansible/
    requirements.yml
    vendor/                         # downloaded collections + wheels
    roles/  playbooks/

  hooks/
    pre.d/   post.d/                # empty; bind-mountable

  images/                           # empty; golden qcow2s mounted at runtime
```

Every `render/*.py` module exposes the same single function — `render(config: Config) -> dict` — so a fourth backend is one new file plus one registry entry, with no change to any calling code. Functions stay pure: config in, dict out, no I/O. All side effects (subprocess, filesystem, network) are confined to `exec/` and `image/`, which keeps the interesting logic trivially unit-testable without a hypervisor.

### 4.3 Sample config

```yaml
schema_version: 1
backend: proxmox              # vsphere | libvirt | proxmox

target:
  proxmox:
    endpoint: https://pve.example.com:8006/
    node: pve01
    datastore: local
    bridge: vmbr0
    # API token only — seed-ISO cloud-init avoids the SSH/SFTP path (§6.4)
    api_token_env: PROXMOX_VE_API_TOKEN

lifecycle: oneshot            # oneshot | stateful
state:
  backend: local              # local | s3
  path: /state/deployment.tfstate
  encryption: true

image:
  distro: rocky9              # rocky8 | rocky9 | rocky10
  source_qcow2: /images/rocky9-golden-2026.08.qcow2
  sha256: "e3b0c442..."

defaults:
  vcpus: 2
  memory_mb: 4096
  disk_gb: 40

vms:
  - name: app01
    cloudinit:
      user_data: ./userdata/app01.yaml
  - name: app02
    vcpus: 4                  # overrides defaults
```

---

## 5. Does OpenTofu integrate with Python?

Short answer: **not the way Ansible appears to — but Ansible does not really work that way either.** The gap is smaller than it looks.

### 5.1 The honest comparison

Ansible's own documentation is explicit that the `ansible` Python package is not a public API: *"This API is intended for internal Ansible use. Ansible may make changes to this API at any time that could break backward compatibility with older versions of the API. Because of this, external use is not supported by Ansible."* The docs then redirect you to `ansible-runner`.

And `ansible-runner` is itself a subprocess wrapper. It is a genuinely good one — Apache-2.0, maintained by Red Hat, extracted from AWX, importable as a module, with structured per-host event callbacks and artifact collection — but it launches `ansible-playbook` as a child process and parses its event stream. It is not in-process execution.

There is also a licensing angle worth knowing: ansible-core is GPL, so importing it directly into your own code has copyleft implications. `ansible-runner` (Apache-2.0) driving a subprocess sidesteps that cleanly. This alone is a good reason to use it.

OpenTofu's equivalent is subprocess plus its documented machine-readable formats:

| Command | Output |
|---|---|
| `tofu plan -json` / `tofu apply -json` | NDJSON UI event stream, one message per line |
| `tofu show -json <planfile>` | Full structured plan, `format_version` 1.0 |
| `tofu output -json` | Output values — your Ansible handoff |
| `tofu providers schema -json` | Provider schemas, for validation tooling |
| `tofu version -json` | Version + resolved provider selections |

These formats carry explicit compatibility promises: minor version bumps are backward-compatible and unknown properties should be ignored; major bumps are breaking and should be rejected. That is a real contract, not text scraping. It is the same foundation Infracost, conftest, and Checkov build on.

So the comparison is: **both tools are subprocess + structured JSON.** Ansible hands you a library that manages the subprocess; OpenTofu does not, so you write it. That is perhaps 150 lines.

### 5.2 What to avoid

- **`python-terraform`** — inactive. Last release 0.10.1, no activity in years. Do not build on it.
- **CDKTF** — targets Terraform, requires a Node.js toolchain, and generates JSON that still gets applied by the CLI. All the dependency weight, none of the in-process benefit.
- **Talking gRPC directly to providers.** Providers are go-plugin gRPC servers speaking a published protobuf protocol (`tfplugin5`/`tfplugin6`), so a Python client is *technically* possible. Don't. You would be reimplementing OpenTofu's graph walker, dependency resolution, and state management — the entire reason you chose it.

### 5.3 Recommended shape

A `TofuRunner` in `exec/tofu.py`: `subprocess.Popen` with `-json`, read stdout line by line, `json.loads` each line into a small dataclass, dispatch on the `type` field (`planned_change`, `change_summary`, `diagnostic`, `apply_complete`), raise typed exceptions on `diagnostic` messages at error level. Streaming line-by-line rather than buffering gives you live progress on slow uploads, which matters when you're pushing a 4 GB OVA over a constrained link.

The public surface is three functions — `plan()`, `apply()`, `outputs()` — each returning a dataclass. Swapping OpenTofu for something else later means rewriting one file and nothing else.

Two ergonomic notes: `-json` replaces human-readable output rather than supplementing it, so decide up front whether operators see raw Tofu output or your wrapper's rendering (simultaneous output is an open upstream request, OpenTofu issue #3303). And `tofu show` supports `-json-into=FILE`, which does let you capture both at once for that command.

### 5.4 The alternative worth weighing

If "proper API calls" is a firm requirement, the honest option is not a better OpenTofu wrapper — it is **skipping OpenTofu** and calling `pyVmomi`, `libvirt-python`, and `proxmoxer` directly. All three are native Python libraries with real APIs.

What you would give up: idempotency, dependency ordering, drift detection, retry semantics, plan-before-apply, and the ability to destroy what you created. That is a lot to reimplement, and reimplementing it badly is worse than shelling out cleanly.

**Recommendation:** keep OpenTofu, wrap it properly. Reserve the direct-SDK path for narrow operations where OpenTofu genuinely has no coverage — the most likely candidate being a bare `import.vmdk`-style disk upload, which is the one govc capability neither the provider nor Ansible replaces.

---

## 6. Backend matrix

### 6.1 vSphere

**Provision:** OpenTofu vSphere provider. The `ovf_deploy` block on `vsphere_virtual_machine` takes `local_ovf_path` or `remote_ovf_url`, paired with the `vsphere_ovf_vm_template` data source, which submits the OVF and extracts its hardware settings as inputs. Content library upload also works from local files — the provider handles OVF descriptor and disk upload through upload sessions.

**Image path:** qcow2 → streamOptimized VMDK → OVA → provider upload.

```bash
qemu-img convert -p -f qcow2 -O vmdk \
  -o adapter_type=lsilogic,subformat=streamOptimized,compat6 \
  rocky9.qcow2 rocky9.vmdk

mkova.sh --num-cpus 4 --mem-size 4096 --firmware bios --hw 20 rocky9 rocky9.vmdk
```

If qemu emits a VMDK ESXi rejects (*"Unsupported or invalid disk type 7"*), fall back to open-vmdk's `vmdk-convert`, which is purpose-built for valid streamOptimized output.

**Caveats:** the `vsphere_content_library_item` docs state `file_url` must be reachable *from the vSphere environment* — test the local-path route against your vCenter version. Choose the disk controller deliberately (Paravirtual vs LSI Logic vs SATA). Ship open-vm-tools, not proprietary VMware Tools. Verify `.mf` checksums after slow-link transfers.

### 6.2 KVM / libvirt

**Provision:** `dmacvicar/libvirt` provider, or `virsh`/`virt-install` via Ansible.

**Image path:** native qcow2, no conversion.

```bash
virsh vol-create-as <pool> rocky9.qcow2 20G --format qcow2
virsh vol-upload --pool <pool> rocky9.qcow2 /images/rocky9.qcow2
# works over qemu+ssh://kvmhost/system
```

The simplest of the three. When the orchestrator runs on the hypervisor itself, this collapses to a local file copy.

### 6.3 Proxmox VE

**Provision:** `bpg/proxmox`. Attach with `import_from = "<datastore>:import/<file>"` for uncompressed images.

**Image path:** native qcow2 — but the *delivery* has three quirks worth designing around, and they are the reason Proxmox is the most awkward of the three despite being the simplest hypervisor:

1. **`proxmox_virtual_environment_download_file` uses PVE's download-url API — Proxmox pulls from a URL.** Air-gapped, that means the orchestrator must serve the qcow2 over HTTP on the local network. This is a mode neither other backend has. Alternatively `proxmox_virtual_environment_file` pushes directly.
2. **The `import` content type is not enabled by default** on PVE storages; it has to be added under Datacenter → Storage first. That is a manual per-site prerequisite — document it as a precondition your wrapper checks and fails fast on.
3. **Custom cloud-init user-data is a "snippet," and the API cannot upload snippets** — the `/nodes/{node}/storage/{storage}/upload` endpoint accepts only `iso`, `vztmpl`, and `import`. The provider therefore falls back to SSH/SFTP for snippets, which would mean Proxmox needs an SSH credential *alongside* its API token. **Avoid this entirely by shipping a NoCloud seed ISO instead — see §6.4.**

Minor trap: PVE before 8.4 rejects `.qcow2` and `.raw` extensions on download-url — rename to `.img`.

### 6.4 Avoiding the SSH credential: seed ISO instead of snippet

**Recommended.** ISOs *can* be uploaded through the plain API, so building a NoCloud seed ISO and attaching it as a CD-ROM removes the SFTP/PAM requirement completely. Proxmox itself works this way internally — when you set `cicustom`, it reads the snippet, packages it into an ISO, and attaches it as a CD-ROM at every VM start. You are simply doing that packaging step yourself, on the orchestrator, where you already do it for libvirt.

This is the better design for three reasons:

1. **One credential type.** Proxmox drops back to API-token-only, matching vSphere and libvirt. The config schema stays uniform.
2. **Shared code path.** libvirt and Proxmox both become "build cidata ISO → attach as CD-ROM." The `image/cloudinit.py` module is written once and serves two backends.
3. **No dependence on an unmerged upstream feature.** API snippet upload has been requested since Proxmox Bugzilla #2208 (2019) and was still an open feature request as of July 2026. Don't design around it landing.

**Three gotchas that will bite if you skip them:**

- **Do not attach Proxmox's native cloud-init drive at the same time.** Two `cidata` sources means cloud-init picks one non-deterministically. Use your ISO *or* the native drive, never both.
- **Do not name the file `vm-<vmid>-cloudinit.iso`.** Proxmox pattern-matches that name, assumes it owns the file, and tries to regenerate it on VM start — which fails with a `genisoimage ... exit code 141` task error. Any other filename is ignored and passed through untouched. A reported fix was simply renaming `vm-100-cloudinit.iso` to something outside the pattern.
- **Check boot order.** The seed ISO must be visible to the guest at first boot; verify the CD-ROM's position in the boot order.

**Trade-offs to accept:** ISO9660 overhead makes each seed roughly 370 KB versus about 1 KB for a snippet — irrelevant at any realistic VM count. Per-VM ISOs accumulate in ISO storage; stateful mode cleans them up on destroy, one-shot mode does not, so add a `prune` subcommand or a naming convention operators can sweep. And the attached ISO must remain present for as long as it is attached, the same coupling snippets have — detach it after first boot if that matters.

The native `initialization` block (user, SSH keys, IP config, DNS) still works over the plain API and needs none of this. Reserve the seed ISO for genuinely custom cloud-config; use the native block for the simple cases.

**Ansible alternative:** `community.proxmox`, split out of community.general (the old `community.general.proxmox` names are deprecated redirects slated for removal in community.general 15.0.0). Requires `proxmoxer>=2.0` and `requests`; tested against ansible-core 2.17–2.20.

Adding Proxmox is genuinely useful beyond its own merits: vSphere and libvirt are similar enough that two backends can hide a leaky abstraction. Proxmox's credential and delivery quirks stress-test the schema properly.

---

## 7. Dropping govc

**Upload and deploy: fully replaceable.** The old "Terraform can't import OVF" guidance is stale. `ovf_deploy` handles local and remote OVF/OVA, and since the provider embeds govmomi you are using the same library `govc` wraps — without a second binary. On the Ansible side, `community.vmware.vmware_deploy_ovf` (v6.2.1) deploys from an OVF or OVA on the filesystem or an HTTP server via pyVmomi.

**Conversion: not replaceable.** Neither tool turns qcow2 into a streamOptimized VMDK or writes an OVF descriptor. `qemu-img` and `open-vmdk` stay exactly as they were.

**What you give up:**
- `govc import.vmdk` — bare streamOptimized disk import with no OVF wrapper. Nothing replaces this. Irrelevant if you always build a full OVA.
- Interactive troubleshooting. `govc` is the fastest way to learn what vCenter actually thinks exists.
- Ansible content library coverage is shrinking: `vmware_content_deploy_ovf_template` and `vmware_content_library_manager` are both deprecated for removal in 7.0.0. The OpenTofu provider is the durable path for content library work.

**Verdict:** make `ovf_deploy` the load-bearing path and drop `govc` from the documented workflow — but keep the binary in the image as a debug tool. It's ~50 MB, Apache-2.0, statically linked, and it's the thing that tells you the truth when a lease hangs at 1%.

---

## 8. Observability

**OpenTofu** has had OpenTelemetry tracing since 1.10 (June 2025). Disabled by default, configured entirely through standard env vars:

```bash
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
export OTEL_EXPORTER_OTLP_INSECURE=true
```

The project is explicit that nothing phones home: traces go only where you point them.

**Ansible** has had an equivalent for longer — the `community.general.opentelemetry` callback plugin, which creates distributed traces for each Ansible task. Same env vars (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME`), enabled in `ansible.cfg`:

```ini
[defaults]
callbacks_enabled = community.general.opentelemetry

[callback_opentelemetry]
enable_from_environment = ANSIBLE_OPENTELEMETRY_ENABLED
```

It needs `opentelemetry-api`, `opentelemetry-sdk`, and `opentelemetry-exporter-otlp` on the controller — three more wheels for the vendored bundle. It also supports console output for a quick look without a collector.

**The catch, and it matters for this design:** trace context does not propagate between the two. OpenTofu does not currently inject `TRACEPARENT` into `local-exec` child processes (issue #3936, accepted for the 1.13.0 milestone). So a naive setup gives you two disconnected traces per deployment.

**Fix:** have the Python wrapper own the root span and export `TRACEPARENT` into both child environments. This is one more argument for the wrapper being a real program rather than a shell script — it is the only component that sees the whole run. `telemetry.py` in the layout above exists for exactly this.

**Air-gapped:** OTLP needs a collector endpoint. Run Jaeger or an OTel Collector locally at the site, or use console/file export and collect the artifacts. Since tracing is off unless explicitly enabled, it costs nothing at sites that don't want it.

---

## 9. Air-gap packaging

**OpenTofu providers.** On a connected build host:

```bash
tofu providers mirror -platform=linux_amd64 /opt/tofu-mirror
tofu providers lock -platform=linux_amd64      # pins checksums
```

Copy into the image and configure `/etc/tofurc`:

```hcl
provider_installation {
  filesystem_mirror {
    path    = "/opt/tofu-mirror"
    include = ["registry.opentofu.org/*/*"]
  }
  # no direct{} block — strict air-gap, no internet fallback
}
```

Commit `.terraform.lock.hcl`. To refresh, re-run `tofu init -upgrade` + `providers mirror` on the connected host and rebuild.

**Ansible collections.**

```bash
ansible-galaxy collection download -r requirements.yml -p ./vendor
ansible-galaxy collection install -r requirements.yml \
  -p /usr/share/ansible/collections --offline
```

Vendor Python deps with `pip download` / `pip install --no-index --find-links`. `ansible-builder` can automate this, but its default `ee-minimal` base is entitlement-gated — build your own EE base from Rocky to stay free.

**Pin aggressively.** pyVmomi 9.0.0.0 (June 2025) broke `community.vmware` — issue #2413 shows `Exception: No longer supported. Use pyVmomi.VmomiJSONEncoder instead`. Pin pyVmomi and every collection version, and record checksums.

**Runtime.** Golden images bind-mounted (`-v /srv/images:/images:ro`) and checksum-verified. Interactive mode `podman run -it`; config mode `podman run -v ./config.yaml:/config.yaml:ro`. Rootless works for remote targets; local libvirt operations on the same host may need the socket bind-mounted or elevated privileges.

---

## 10. Lifecycle and state

**Default to one-shot.** Air-gapped multi-operator sites are precisely where shared state breaks: two operators running from different machines against the same target produce drift or duplicates. Post-deploy changes go through Ansible against the live VM, or a redeploy.

**Opt-in stateful** where update/scale/destroy is genuinely needed. State goes either on a bind-mounted volume kept with the target's records, or in self-hosted MinIO via the S3 backend. OpenTofu 1.10+ supports `use_lockfile = true` for native S3 conditional-write locking, so **no DynamoDB is required** — which matters, because DynamoDB doesn't exist on-prem.

Make the state location an explicit per-target config field, and make the active mode obvious in the UX. Enable state encryption whenever state persists.

---

## 11. cloud-init across three hypervisors

Bake into the golden images so one image serves all three: `cloud-init`, `open-vm-tools`, `qemu-guest-agent`. Leave `datasource_list` unset or list both `NoCloud` and `VMware` so detection is automatic.

| Backend | Mechanism |
|---|---|
| libvirt | NoCloud seed ISO: `mkisofs -o seed.iso -V cidata -J -r user-data meta-data`, attached as CD-ROM. Both files required; label must be `cidata`. |
| vSphere | `guestinfo.metadata` / `guestinfo.userdata` via extraConfig, with `.encoding` = `base64` or `gzip+base64`. Requires open-vm-tools — it reads the host↔guest RPC channel, not the network. A NoCloud ISO also works. |
| Proxmox | Native `initialization` block for basics (user, SSH keys, IP) over the plain API. For custom cloud-config, **build the same NoCloud seed ISO as libvirt**, upload it via the API as `iso` content, and attach as CD-ROM — avoiding snippets and SSH entirely (see §6.4). Don't also attach the native cloud-init drive. |

---

## 12. Risks and open decisions

**Risks and mitigations**

| Risk | Mitigation |
|---|---|
| ovftool licensing | Solved: qemu-img + open-vmdk + provider upload |
| Rocky 10 x86-64-v3 baseline | Dual `:10` / `:9` tags |
| community.vmware drift + pyVmomi 9.x breakage | Pin versions; prefer OpenTofu for provisioning |
| qemu streamOptimized edge cases | Fall back to `vmdk-convert` |
| Shared-state corruption | One-shot default; explicit opt-in |
| Proxmox snippets need SSH/SFTP | Resolved: seed ISO over the API instead (§6.4) |
| Proxmox regenerating your seed ISO | Never name it `vm-<vmid>-cloudinit.iso`; omit the native cloud-init drive |
| Orphaned seed ISOs in one-shot mode | Naming convention + `prune` subcommand |
| Proxmox `import` type not enabled | Precondition check with a clear error |
| Disconnected traces | Wrapper owns root span, injects `TRACEPARENT` |
| Slow-link transfer corruption | Verify `.mf` / SHA checksums |

**Decisions still needed, prioritised**

1. **Rocky 10 only, or dual 10/9 tags?** Depends entirely on the oldest CPU in your target fleet. Check before committing — this is the one that's expensive to retrofit.
2. **Is `govc` in the image at all?** Recommendation: yes, as an optional debug tool, absent from the documented path.
3. **Lifecycle default per target** — one-shot everywhere, or stateful-with-MinIO for some sites.
4. **Which Ansible VMware collection** to standardise on (`community.vmware` today vs. migrating to `vmware.vmware`), and the pinned pyVmomi version.
5. **Golden image delivery** — bind mount vs. OCI artifact vs. named volume — and how images are versioned against the orchestrator.
6. **Is tracing on by default** in the container, or opt-in per run?

---

## 13. Things that are in flux — verify at adoption

- The `community.vmware` / `vmware.vmware` split and its CI health; pyVmomi 9.x compatibility.
- OpenTofu's cadence (1.11.6 stable April 2026; 1.12.0 May 2026). OTel `TRACEPARENT` propagation is milestoned for 1.13.0.
- The vSphere provider's move to the `vmware/` namespace.
- `bpg/proxmox` is pre-1.0 and explicitly does not guarantee backward compatibility across minor versions. Pin it and read release notes before bumping.
- Broadcom's ovftool EULA terms and the free-ESXi licensing situation (EOGA Feb 2024, a free edition reinstated with 8.0u3e in April 2025, VCF/VVF 9.x moved to subscription license files).

**Deliberately excluded as overkill:** CAPI/Cluster API (Kubernetes-centric), Foreman/Katello and MAAS (sprawling infrastructure, violates the hand-maintainable requirement), Terragrunt (unnecessary wrapper for a small module set).

---

## 14. Sources

**Licensing**
- OpenTofu: https://opentofu.org/
- Terraform BUSL: https://endoflife.date/terraform
- vSphere provider: https://github.com/vmware/terraform-provider-vsphere
- libvirt provider: https://github.com/dmacvicar/terraform-provider-libvirt
- Proxmox provider: https://github.com/bpg/terraform-provider-proxmox · https://library.tf/providers/bpg/proxmox/latest
- ovftool container refusing to redistribute: https://github.com/cseelye/ovftool
- UBI licensing: https://crunchtools.com/ubi-licensing/

**Rocky Linux 10**
- GA release notes: https://rockylinux.org/news/rocky-linux-10-0-ga-release
- Release notes / Podman 5: https://docs.rockylinux.org/10/releases/release_notes/10_0/
- Minimum hardware: https://docs.rockylinux.org/10/guides/minimum_hardware_requirements/
- CPU compatibility test: https://docs.rockylinux.org/10/gemstones/test_cpu_compat/

**vSphere**
- `ovf_deploy` / `vsphere_ovf_vm_template`: https://registry.terraform.io/providers/vmware/vsphere/latest/docs
- Content library upload internals: https://deepwiki.com/vmware/terraform-provider-vsphere/3.3-content-libraries
- `vmware_deploy_ovf`: https://docs.ansible.com/projects/ansible/latest/collections/community/vmware/vmware_deploy_ovf_module.html
- Deprecated content library modules: https://docs.ansible.com/projects/ansible/latest/collections/community/vmware/index.html
- open-vmdk: https://github.com/vmware/open-vmdk
- govmomi vmdk import: https://github.com/vmware/govmomi/blob/main/vmdk/import.go
- pyVmomi 9.x breakage: https://github.com/ansible-collections/community.vmware/issues/2413

**Proxmox**
- `download_file` resource: https://github.com/bpg/terraform-provider-proxmox/blob/main/docs/resources/virtual_environment_download_file.md
- `import_from` on VM resource: https://github.com/bpg/terraform-provider-proxmox/blob/main/docs/resources/virtual_environment_vm.md
- SFTP/PAM requirement for snippets: https://pkg.go.dev/github.com/bpg/terraform-provider-proxmox
- Dual upload paths (API vs SSH) explained: https://deepwiki.com/bpg/terraform-provider-proxmox/3.3.2-file-upload-mechanisms
- API upload rejects `snippets` content type: https://forum.proxmox.com/threads/creating-snippets-using-pve-api.54081/
- Still an open feature request (July 2026): https://forum.proxmox.com/threads/feature-request-expand-the-storage-upload-api-to-support-snippets.185204/
- Seed ISO workaround + the `vm-<vmid>-cloudinit.iso` naming trap: https://forum.proxmox.com/threads/configuring-cloud-init-via-api.162588/
- Proxmox packages snippets into an ISO internally: https://forum.proxmox.com/threads/understanding-cloud-init-provisioning.95796/
- Cloud-init images attached as ISO via virtual CD-ROM: https://pve.proxmox.com/wiki/Cloud-Init_FAQ
- API-only import discussion: https://github.com/bpg/terraform-provider-proxmox/issues/1913
- `community.proxmox`: https://github.com/ansible-collections/community.proxmox
- Deprecation of community.general.proxmox: https://docs.ansible.com/projects/ansible/latest/collections/community/general/proxmox_module.html

**Python integration**
- Ansible Python API is unsupported: https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_api.html
- ansible-runner: https://github.com/ansible/ansible-runner · https://ansible-runner.readthedocs.io/en/stable/python_interface/
- python-terraform inactive: https://security.snyk.io/package/pip/python-terraform
- OpenTofu machine-readable UI: https://opentofu.org/docs/internals/machine-readable-ui/
- OpenTofu JSON output format: https://opentofu.org/docs/internals/json-format/
- `tofu show` and `-json-into`: https://opentofu.org/docs/cli/commands/show/
- Simultaneous human + JSON output request: https://github.com/opentofu/opentofu/issues/3303

**Observability**
- OpenTofu 1.10 OTel: https://opentofu.org/docs/v1.10/intro/whats-new/ · https://opentofu.org/blog/opentofu-1-10-0/
- Ansible OTel callback: https://docs.ansible.com/projects/ansible/latest/collections/community/general/opentelemetry_callback.html
- TRACEPARENT propagation gap: https://github.com/opentofu/opentofu/issues/3936

**cloud-init**
- NoCloud datasource: https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html
- VMware datasource: https://blogs.vmware.com/cloud-foundation/2026/06/08/achieve-speed-scale-and-reliability-of-virtual-machine-deployments-with-cloud-init/
