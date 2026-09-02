# vcows-deploy

Deploy pre-built golden qcow2 images as VMs to KVM/libvirt over `qemu+ssh://`,
or to Proxmox VE over its HTTPS API. `backend:` in the config picks one. Shipped
as a container image, built to run at a site with no network beyond that one
connection to the hypervisor.

## Read this first: the config is not declarative

**Deleting a VM from `config.yaml` does not delete the VM.**

The natural assumption is the opposite, and being wrong about it looks like data
loss. vcows never converges. `deploy` creates the VMs that do not exist yet and
reports the ones that do; it never modifies, and it never removes. Removing a VM
from the config makes `preflight` say

```
2026-08-31T22:44:12.881Z WARNING cli        [app03] marked VM 'app03' exists but is not
in this config; leaving it alone. Removing a VM from the config does not delete
it -- that needs a deliberate destroy.
```

Tearing something down is `vcows destroy`, and nothing else.

## What it does

| | |
|---|---|
| **preflight** | Enumerates the target by ownership marker. What exists, what is ours, what conflicts. |
| **deploy** | OpenTofu applies a static module: the golden image once per host, then a per-VM disk, a cloud-init seed ISO, and a VM carrying its marker. |
| **destroy** | Python and the hypervisor's own client (`libvirt`, `proxmoxer`) directly, by marker. Works with the state file deleted, and after a VM has been renamed. |

Identity is the **marker**, never the name — a JSON payload in the libvirt
domain's `<metadata>`, or in the Proxmox VM's `description`. A renamed VM is
still ours and still destroyable; a VM vcows did not create is never adopted or
overwritten.

## Requirements

**On a libvirt host**

* `qemu+ssh://` reachable for the configured user, and `nc` or `virt-ssh-helper`
  present — the SSH transport needs one of them on the *target*.
* A storage pool that already exists and is active. **vcows never creates a pool.**
  Creating one is a host-level change to somebody else's hypervisor.
* Golden images with `cloud-init` and `growpart`. Each VM's disk is an overlay
  with a larger capacity, and the guest grows into it on first boot.

**On a Proxmox node**

* An API token, exported as `PROXMOX_VE_API_TOKEN` where you run the container.
  `preflight` is what tries it against the cluster: it lists the node's VMs,
  reads both storages and their content types, and lists the `import` content
  of `import_datastore`. `validate` checks only the token's shape.
* `import_datastore` must allow the **Import** and **ISO image** content types,
  and `datastore` must allow **Disk image**. Import is off by default on a PVE
  storage and is enabled under Datacenter → Storage → Content; `preflight` names
  the storage and the missing type. **vcows never creates a storage** either.
* The bridge each NIC names must exist on the node.
* The same golden image requirement as above: `cloud-init` and `growpart`.

**Where you run the container**

Rootless podman. The image sets no `USER`: under rootless podman container root
*is* the invoking user, which is what makes a bind-mounted run directory and a
0600 SSH key work without a UID-mapping dance.

**`--user` works, and it needs three things lined up, not one.** Measured with
`--user 4242`:

* podman synthesises a passwd entry whose home is `/`, and `/` is not writable.
  The entrypoint resolves `~` from that entry — not from `HOME`, deliberately,
  because that is what `ssh` does — so it cannot write `~/.ssh/config`, says so,
  and the connection then fails with `Host key verification failed`. Setting
  `HOME` does not help. Give it a writable home (`--passwd-entry`) or mount your
  own config at the passwd home's `.ssh/config`.
* the mounted 0600 key is owned by the mapped host UID, so uid 4242 cannot read
  it: `Load key ...: Permission denied`.
* the run directory mount is owned by that same UID and is `0755`, so uid 4242
  cannot create `runs/<deployment>/<timestamp>` inside it. `deploy` and `destroy`
  stop before connecting, with `vcows: cannot create the run directory
  /runs/<deployment>/<timestamp>: Permission denied`, and write nothing.
  `validate` and `preflight` create no run directory and are unaffected, so a
  clean `preflight` says nothing about `deploy`.

The last two are one problem — a mount owned by the wrong UID — with two remedies
that are **not** equivalent:

* `--userns=keep-id:uid=4242,gid=0` maps your own UID to 4242 inside the
  container, so both mounts already have the owner they need. Nothing on the host
  is chowned, and the run directory comes back owned by you. It sets the
  container UID itself, so `--user` becomes redundant; the writable home is still
  needed. Measured with `--passwd-entry`: all four verbs behave.
* `:U` on the key mount and on the run directory mount chowns those *host* paths
  to the subuid backing 4242. It also works, and it charges both sides. Your key
  copy stops being yours, and so does the output: `./runs/<deployment>` lands
  `drwx------` owned by a subuid, so `ls`, `cat` and `rm -rf` all answer
  `Permission denied`, and reading back the `run.json` an air-gapped site ships
  home takes `podman unshare`.

**`--run-dir` on that same mount writes no record at all.** The third bullet
above is the default path, where vcows creates a subdirectory inside the mount
and cannot. `--run-dir /runs` names the mount itself, which already exists and is
empty, so nothing stops it — only the `0700` is refused, and that is deliberately
a warning. The run goes ahead and dies on the first thing it writes:

```
2026-08-31T22:44:12.104Z WARNING cli        cannot make /runs 0700; it stays 0755. This
run's seed ISOs carry user_data verbatim, and anyone who can read that directory
can read them.
2026-08-31T22:44:12.106Z ERROR   cli        this run left no record -- /runs/run.json
could not be written (Permission denied). The failure below is reported on this
stream only.
2026-08-31T22:44:12.107Z ERROR   cli        PermissionError: [Errno 13] Permission
denied: '/runs/run.json'
```

No `run.json` and no `manifest.json` are written. **`deploy` and `destroy` are
not symmetric here.** Everything `deploy` puts in the run directory — the seed
ISOs — it writes before it creates anything through the backend, so it fails
with nothing created on the hypervisor. `destroy` writes nothing until the
teardown is over, so the VMs are gone, no record says they were, and the exit
code is 1, which reads as a teardown that failed. An air-gapped site ships the
run directory home as its whole account of what happened; this one has nothing to
send.

`--userns=keep-id:uid=4242,gid=0` fixes it, exactly as above.

## Using it

```bash
podman run --rm \
  -v ./lab-a.yaml:/config.yaml:ro,z \
  -v ~/.ssh/id_ed25519:/run/secrets/id_ed25519:ro,z \
  -v ~/.ssh/known_hosts:/run/secrets/known_hosts:ro,z \
  -v /srv/images:/images:ro,z \
  -v ./runs:/runs:Z \
  vcows-deploy:0.1.0.0 preflight /config.yaml
```

On Proxmox there is no key and no `known_hosts` to mount. The token is an
environment variable, read by vcows and by the OpenTofu provider alike:

```bash
export PROXMOX_VE_API_TOKEN='vcows@pve!deploy=<secret>'   # user@realm!tokenid=secret
podman run --rm \
  -e PROXMOX_VE_API_TOKEN \
  -v ./lab-a.yaml:/config.yaml:ro,z \
  -v /srv/images:/images:ro,z \
  -v ./runs:/runs:Z \
  vcows-deploy:0.1.0.0 preflight /config.yaml
```

A PVE certificate from a private CA goes in the environment too: `SSL_CERT_FILE`
for the provider and `REQUESTS_CA_BUNDLE` for vcows, both naming a mounted
bundle. There is deliberately no `ca_file` in the config — bpg/proxmox 0.111.1
has no CA option, so a field only vcows honoured would pass `preflight` and fail
`deploy`. `insecure: true` under `target.proxmox` skips verification for a
self-signed certificate, and `validate` warns about it: the token is a bearer
credential and goes to whatever answers.

**The read-only mounts are `:z` and the run directory is `:Z`.** On an SELinux
host `:Z` relabels the *host* path with a category private to one container, so
nothing else — including your own `ssh` — can read it afterwards. That is right
for `./runs`, which belongs to that run alone, and wrong for a key, a config and
a golden-image directory the rest of the host shares.

Then `deploy /config.yaml`, and `destroy /config.yaml` when it is time. `validate`
needs none of the mounts but the config.

Each run writes `/runs/<deployment>/<timestamp>/` — its plan, its state, its seed
ISOs and a `run.json` saying what happened. `--run-dir` puts one run somewhere
else instead, and takes the timestamp with it: the directory must be empty, since
the state is thrown away between runs and two of them cannot share one.

| Command | |
|---|---|
| `validate <config>` | Offline. No connection is opened. |
| `preflight <config>` | Reports what exists and what would be done. Changes nothing. |
| `deploy <config>` | Creates what does not exist. Refuses, touching nothing, if anything conflicts. |
| `destroy <config>` | Tears down **this deployment's** marked VMs. Asks first unless `--yes`. |
| `version` | Version, OpenTofu version, and the build manifest. |

Exit codes are 0 and 1 — plus **2** from argparse, for an unknown verb or a
missing argument, which is refused before any command runs.

## The config

```yaml
schema_version: 1
deployment: lab-a              # defaults to the filename stem; goes in every marker
backend: libvirt
target:
  libvirt:
    uri: qemu+ssh://vcows@hypervisor.example/system   # no query string, no password
    pool: images                                       # must exist and be active
    ssh_keyfile: /run/secrets/id_ed25519      # mounted; see below
    known_hosts: /run/secrets/known_hosts     # mounted; see below
image:
  source_qcow2: /images/golden.qcow2
  base_volume_name: golden.qcow2   # shared per host, uploaded once
vms:
  - name: app01
    vcpus: 2
    memory_mib: 4096
    disk_gb: 40
    firmware: efi                  # efi | bios, default efi
    user_data: |                   # optional, passed to cloud-init verbatim
      #cloud-config
      packages: [tmux]
    nics:
      - network: default           # exactly one of network | bridge
        ip_cidr: 192.168.122.60/24
        gateway: 192.168.122.1
        nameservers: [192.168.122.1]
```

The same deployment against Proxmox:

```yaml
schema_version: 1
deployment: lab-a
backend: proxmox
target:
  proxmox:
    endpoint: https://pve.example.com:8006   # https, host[:port], no path, no credentials
    node: pve1
    datastore: local-lvm                     # VM disks; must allow "Disk image"
    import_datastore: local                  # golden image and seed ISOs; must allow "Import" and "ISO image"
    # insecure: true                         # self-signed certificate; validate warns
image:
  source_qcow2: /images/golden.qcow2
  base_volume_name: golden.qcow2   # uploaded once into import_datastore, reused
vms:
  - name: app01                    # a DNS name: no underscore
    vcpus: 2
    memory_mib: 4096
    disk_gb: 40
    firmware: efi                  # efi | bios; PVE owns OVMF and the EFI vars disk
    user_data: |
      #cloud-config
      packages: [tmux]
    nics:
      - bridge: vmbr0              # bridge only; there is no network:
        ip_cidr: 192.168.122.60/24
        gateway: 192.168.122.1
        nameservers: [192.168.122.1]
        # vlan_id: 42
```

**A Proxmox NIC attaches to a bridge, and only a bridge.** PVE has no equivalent
of a libvirt network, so `network:` is rejected and `bridge` is required;
`vlan_id` (1–4094) tags it. **Firmware is a choice, not a set of paths.** PVE
owns its OVMF and allocates the EFI vars disk itself, so `firmware: efi` is the
whole of it, and the libvirt keys `loader`, `loader_format` and `nvram_template`
are rejected. **A Proxmox VM name is a DNS name**, so the `_` a libvirt domain
name allows is refused. A libvirt config ported across hits all of these in
`validate`, offline.

vcows owns `meta-data` and `network-config`; `user_data` is yours and is passed
through with no interpretation.

**`deployment` names the blast radius.** It is stamped into every marker, and
`destroy` only tears down VMs whose marker matches. It defaults to the config's
filename stem — so renaming the file orphans that deployment's VMs. `destroy`
reports what it is skipping and why, which is where you would see it.

**MACs are derived, and the derivation includes `deployment`.** Each NIC gets
`uuid5` of the deployment, the VM name and the NIC index, because cloud-init
matches an interface by MAC and the address has to be known before the VM
exists. Set `mac:` on a NIC to override it — that is the only escape, and it is
what to use when a site's DHCP reservations or switch policy already own an
address. Two hosts running the *same* deployment name still derive the same
MACs: the derivation narrows that collision, it does not close it.

**The first NIC is primary** unless one carries `primary: true`. Its address is
what the inventory reports as `configured_address`, and its gateway is the only
one that becomes a default route — a second NIC keeps its address and its
gateway is checked against its own subnet, but does not add a second route for
the guest to choose between.

**`nic0`, `nic1`, … are identifiers, not device names.** vcows writes a
cloud-init `network-config` v2 document whose keys follow the order of `nics:`
in the config, and each entry is matched to an interface by MAC. The device
keeps whatever name the image gives it — `eth0`, `ens3`, `ens18`, whatever the
kernel and udev produce. cloud-init renames a matched interface only when the
entry carries `set-name`, which vcows deliberately does not write, so a golden
image keyed to its own kernel names — a firewall zone, an `ifcfg` file, a
monitoring check — keeps working.

**IPv4 only, in practice.** The schema accepts an IPv6 `ip_cidr` and validates it
correctly, but the generated `network-config` sets `dhcp6: false` and writes the
default route as `0.0.0.0/0`, so a v6 primary NIC gets its address and no route.
Give NICs v4 addresses at v0.1.

**`vcpus`, `memory_mib` and `disk_gb` have ceilings** — 512, 4 TiB and 64 TiB —
so a fat-fingered zero is refused before the run creates anything. They are a
typo check, not a statement about supported sizes. On a host bigger than one of
them, raise it: `VCOWS_MAX_VCPUS`, `VCOWS_MAX_MEMORY_MIB` and `VCOWS_MAX_DISK_GB`
are read from the container's environment.

**Replacing the golden image is not a deletion.** `base_volume_name` is shared
by every deployment on that host and every VM's disk is an overlay on it, so
removing it breaks running VMs. Point `base_volume_name` at a name the pool does
not hold and re-run: vcows uploads the new image alongside, and VMs created from
then on back onto it. Sweeping the old one is a host-level chore for when nothing
references it any more. On Proxmox the image is uploaded once into
`import_datastore` and stays there after `destroy`, so the next `deploy` skips
the upload; removing it is the same chore, done in the PVE UI.

**VMs vcows creates start with the host.** Every libvirt domain is defined with
autostart on and every Proxmox VM with *Start at boot*, so a hypervisor reboot
brings them back without vcows. The alternative is worse than it sounds: a re-run
after a reboot finds the VMs defined, reports them as ours, prints `nothing to
create` and exits 0, with every guest powered off. There is no `start` verb —
turn autostart off per domain with `virsh autostart --disable <name>`, or clear
*Start at boot* on the PVE VM, if a host must come up quiet.

> **The config is a secret artifact.** Credentials are cleartext at v0.1,
> deliberately and temporarily. Do not commit it, and do not ship it as an
> example. On Proxmox the token is not in the file — it is never written
> anywhere: not the config, the tfvars, `run.json` or the log — but `user_data`
> still is.

### How the SSH credentials actually reach libvirt

Neither client accepts them in the URI. libvirt's `qemu+ssh` ignores
`known_hosts` (it is a libssh parameter), the OpenTofu provider spells it
`knownhosts`, and the provider's `sshcmd` transport — the only one that reaches a
modern split-daemon host — rejects both. What they share is that both run `ssh`,
so the container's entrypoint writes `~/.ssh/config` from the two fields above
before handing over to `vcows`. The files stay where you mounted them, read-only;
nothing is copied.

Mount your own `~/.ssh/config` into the container and it is left alone.

## The run directory

Every `deploy` and `destroy` writes one, at `runs/<deployment>/<timestamp>Z/`
unless `--run-dir` says otherwise. It is created `0700` **and it carries
secrets** — the seed ISOs are kept so a VM that will not boot can be debugged
from the media it was actually given, and those ISOs contain `user_data`
verbatim.

```
seed/*.iso        what each VM was given
inventory.json    name -> configured_address, uuid, disks. Minimal, and unstable at v0.1
manifest.json     which build produced this run
run.json          what was asked, what was decided, what happened
```

**Nothing expires them.** Run directories accumulate, each with the seed ISOs of
its deploy, and deleting them is the operator's job — vcows never does, because a
run it removed is the one somebody needed. On a host that keeps them:

```bash
find runs/ -mindepth 2 -maxdepth 2 -type d -mtime +30 -exec rm -rf {} +
```

## The log

**Every line vcows writes is a log line** — timestamped, level-tagged, on
**stderr**. There is no second channel: `podman logs <id>` is the whole of it and
no mount is required.

```
2026-08-31T22:44:12.104Z INFO    preflight  connecting to qemu+ssh://vcows@hv1/system
2026-08-31T22:44:12.881Z INFO    cli        app-frontend-01      create  does not exist
2026-08-31T22:44:12.881Z INFO    cli        db01                 create  does not exist
2026-08-31T22:44:12.882Z WARNING cli        [image.source_qcow2] cannot read /images/golden.qcow2 to check disk_gb against it
2026-08-31T22:44:13.415Z ERROR   tofu       apply [libvirt_volume.overlay["app01"]]: Volume Creation Failed
    storage volume 'app01.qcow2' exists already
2026-08-31T22:44:13.502Z INFO    cli        created 2 VM(s); run directory runs/lab-a/20260831T224412Z
```

Four fixed-width columns: **when**, **how bad**, **which module**, and the
message. Timestamps are UTC with milliseconds, because a preflight puts several
lines in the same second. A message that runs to more than one line -- an
OpenTofu diagnostic's *why* -- is indented under its own line, so a continuation
never reads as a new record.

The level is what distinguishes the kinds of line, where the stream used to:

| Level | Carries |
|---|---|
| `DEBUG` | why something was not knowable: libvirt lookup misses, an unreadable pool, a config the entrypoint declined to read |
| `INFO` | the report — decision rows, counts, verdicts, the run directory, the `tofu` command line, OpenTofu's diagnostics |
| `WARNING` | degraded but continuing: a config `Problem`, an advisory, a run directory that could not be made `0700` |
| `ERROR` | refusals, fatal problems, and OpenTofu's own errors |

A `Problem` and an OpenTofu diagnostic are each logged at **their own** severity,
so a failed `apply` puts its errors at `ERROR` rather than burying them among the
report.

`VCOWS_LOG_LEVEL` sets the level and is read from the container's environment,
like `VCOWS_MAX_*` above. **The default is `INFO`**, so a delivered run records
its own account without anyone having opted in beforehand — which matters most
for `destroy`, since it cannot be re-run to reproduce what was not captured.
**`VCOWS_LOG_LEVEL=WARNING` is a quiet mode**: the report goes away and the
problems stay. A value that is not a level name is reported and ignored rather
than being fatal. `VCOWS_TRACEBACK=1` appends the Python traceback to the `ERROR`
line an unexpected exception produces: the message is what an operator needs,
the traceback is what a bug report needs, and an air-gapped site cannot re-run
to get it later.

Timestamps are UTC, matching the run directory's name. The first `run directory`
line is the join key that ties a `podman logs` dump to the `run.json` an
air-gapped site ships home.

**The one exception is the `destroy` confirmation prompt.** `input()` writes it
to stdout with no trailing newline so the cursor stays where you type, and it is
the only unprefixed output vcows produces — which makes it easy to tell apart
from the log, and is why it is the exception rather than an oversight. Nothing
else is written to stdout, so `2>/dev/null` silences vcows entirely.

**The log names paths, never contents.** No `user_data`, no seed ISO bytes, no
key material — `ssh_keyfile` and `known_hosts` appear as the paths they are.
Treat it as less protected than the run directory even so: that directory is
`0700`, and a container's logs are whatever the host's log driver does with them.

## Air gap

The image carries both OpenTofu providers in `/opt/tofu-mirror` and points at it
through `TF_CLI_CONFIG_FILE=/opt/tofu/tofurc`. There is no `direct` block in that
config: a provider missing from the mirror fails immediately instead of resolving
DNS and hanging. The gate for this is `tests/test_image.py`, which runs
`--network=none` and requires `tofu init` and `tofu validate` to succeed against
the real module with no network at all.

## The image

Built from `quay.io/rockylinux/rockylinux:10`, pinned by digest, 486 MB on disk
(`podman image ls`, with both providers). The delivery figure below is from the
one-provider build — 151 MB compressed, measured: 150,784,598 bytes — and has not
been re-measured, because `just scan`, which gates `just bundle`, is red on the
bpg/proxmox binary's Go stdlib findings. Most of the image is payload rather than
base: `du -sm` inside it puts the OpenTofu binary at 110 MiB and the two
providers at 53 MiB unpacked in the plugin cache plus 19 MiB of mirror zips.
Smaller bases were measured and both pass the same gate — `10-minimal`
delivers at 134 MB, losing `vi`, `less`, `tar`, `ping` and `dnf`; a
`10-ubi-micro` builder build delivers at 118 MB and additionally has no `rpm`, so
the image cannot report its own contents at a site. Switching later is one `ARG`
and a package-manager name.

The provider plugin cache is warmed at build time, so `tofu init` symlinks into
`/opt/tofu/plugin-cache` instead of unpacking a copy into every run directory.

## Delivering it

```bash
just image     # build
just scan      # trivy against docs/cve-baseline.json, plus an SBOM
just bundle    # assemble .cache/delivery/
```

`just bundle` is what produces the artifact that goes on the medium. It writes
the compressed image, the SBOM and trivy report describing *that* image, a
`SHA256SUMS` over all three, and `image.tar.sha256` — the digest of the
uncompressed archive inside the gzip, so a site can check before or after
decompressing. The file is named for the version and commit read out of the
image itself rather than out of the working tree, so a bundle cannot claim a
commit it does not contain.

At the site:

```bash
sha256sum -c SHA256SUMS
gunzip -c vcows-deploy-*.tar.gz | podman load
```

**The bundle is not signed.** `SHA256SUMS` catches corruption and a mismatched
pairing; it does not catch substitution. There was a cosign step and it was
removed rather than repaired, because it signed the uncompressed archive while
this section promised a compressed one — two byte streams both called "the
delivery tarball", which at a site reads as tampering rather than as a packaging
bug. `docs/ci.md` records why, and what reinstating it needs.

## Licensing

`/opt/vcows/manifest.json` lists every package in the image with its version,
licence and source RPM, plus the OpenTofu and provider versions and the git
revision that built it. The same file is copied into every run directory.

The image contains GPL-2.0-**only** components, and GPLv2 §3 offers no
network-server option for source — so **source ships as a separate medium
accompanying the delivery**, mirrored from the `source_rpms` list in that
manifest. Each non-RPM component has its licence and provenance vendored under
`/opt/vcows/licenses/`: `dmacvicar-libvirt/`, where upstream ships no `LICENSE`
file in 0.9.x and the note explains why that is a gap rather than a revocation;
`bpg-proxmox/`, MPL-2.0 and the only one of the three whose package the mirror
reports as signed; and `proxmoxer/`, MIT, the one pip-installed dependency.

## Development

```bash
./scripts/os-deps.sh        # python3-libvirt, xorriso, shellcheck, jq
./scripts/install-tools.sh  # pinned uv, tofu, just, hadolint, trivy, syft, gitleaks
just dev-env
just check
```

Optionally `.venv/bin/pre-commit install`, which runs the cheap half of
`just lint` before each commit.

`just` on its own lists every recipe. Both CI pipelines call the same ones and
nothing else, which is what `docs/ci.md` is about.

`--system-site-packages` is not optional: `python3-libvirt` comes from the RPM
(PyPI ships an sdist only), and without the flag the binding is invisible inside
the venv while `python3 -c 'import libvirt'` keeps working outside it. It is also
not a rig-only dependency — `tests/fake_libvirt.py` imports it at module scope to
build genuine `libvirt.libvirtError` instances, so most of the suite needs it.

`xorriso` is the other one people miss. `tests/test_seed_iso.py` shells out to it
to read back what `pycdlib` wrote, on the principle that a builder verified only
by itself is not verified. It is ungated, so without it that test fails rather
than skips.

### Test gates

Some tests need something this machine may not have. Each is skipped with an
explicit reason rather than quietly passing:

| | |
|---|---|
| `VCOWS_RIG_URI=qemu+ssh://…` | Runs preflight against a real libvirt hypervisor, and the boot gate: one VM deployed, booted, read over SSH and destroyed again. |
| `VCOWS_PVE_ENDPOINT=https://…` **and** `PROXMOX_VE_API_TOKEN` | Runs against a real Proxmox cluster. Both, or the gate answers nothing. |
| `VCOWS_IMAGE=localhost/vcows-deploy:0.1.0.0` | Runs the offline container gate. Needs podman; buildah cannot substitute. |
| `.tools/tofu-mirror` present | Runs the OpenTofu module gates. `just mirror` builds it. |
| `python3-libvirt` importable | Pins our literal flag and error constants against the real ABI. |
| `pycdlib` importable | Builds the seed ISO. |
| `scripts/smoke-libvirt.sh` running as root | Asserts what a real libvirtd made of the applied module. Not runnable by hand: `just smoke-libvirt` installs packages and starts a daemon. |

**A gate that passes because it did not run is worse than no gate.**
`VCOWS_GATES` turns a named gate's skip into a failure carrying its reason:

```bash
VCOWS_GATES=tofu just test        # or: rig, image, libvirt, pycdlib, smoke, proxmox, or all
```

It is comma-separated, case-sensitive, and does **not** strip whitespace —
`tofu,image` is right and `tofu, image` silently demands only `tofu`.
`tests/test_gates.py` asserts that every skip in the suite goes through this
mechanism, so a bare `pytest.skip` added later cannot hide from it.
