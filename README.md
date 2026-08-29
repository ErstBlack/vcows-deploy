# vcows-deploy

Deploy pre-built golden qcow2 images as VMs to KVM/libvirt over `qemu+ssh://`.
Shipped as a container image, built to run at a site with no network beyond the
SSH tunnel to the hypervisor.

## Read this first: the config is not declarative

**Deleting a VM from `config.yaml` does not delete the VM.**

The natural assumption is the opposite, and being wrong about it looks like data
loss. vcows never converges. `deploy` creates the VMs that do not exist yet and
reports the ones that do; it never modifies, and it never removes. Removing a VM
from the config makes `preflight` say

```
warning [app03]: marked VM 'app03' exists but is not in this config; leaving it
alone. Removing a VM from the config does not delete it -- that needs a
deliberate destroy.
```

Tearing something down is `vcows destroy`, and nothing else.

## What it does

| | |
|---|---|
| **preflight** | Enumerates the target by ownership marker. What exists, what is ours, what conflicts. |
| **deploy** | OpenTofu applies a static module: the golden image once per host, then a per-VM overlay, a cloud-init seed ISO, and a domain carrying its marker. |
| **destroy** | Python and `libvirt` directly, by marker. Works with the state file deleted, and after a VM has been renamed. |

Identity is the **marker**, never the name — a JSON payload in the domain's
`<metadata>`. A renamed VM is still ours and still destroyable; a VM vcows did not
create is never adopted or overwritten.

## Requirements

**On the hypervisor**

* `qemu+ssh://` reachable for the configured user, and `nc` or `virt-ssh-helper`
  present — the SSH transport needs one of them on the *target*.
* A storage pool that already exists and is active. **vcows never creates a pool.**
  Creating one is a host-level change to somebody else's hypervisor.
* Golden images with `cloud-init` and `growpart`. Each VM's disk is an overlay
  with a larger capacity, and the guest grows into it on first boot.

**Where you run the container**

Rootless podman. The image sets no `USER`: under rootless podman container root
*is* the invoking user, which is what makes a bind-mounted run directory and a
0600 SSH key work without a UID-mapping dance.

**`--user` works, and it needs two things lined up, not one.** Measured with
`--user 4242`:

* podman synthesises a passwd entry whose home is `/`, and `/` is not writable.
  The entrypoint resolves `~` from that entry — not from `HOME`, deliberately,
  because that is what `ssh` does — so it cannot write `~/.ssh/config`, says so,
  and the connection then fails with `Host key verification failed`. Setting
  `HOME` does not help. Give it a writable home (`--passwd-entry`) or mount your
  own config at the passwd home's `.ssh/config`.
* the mounted 0600 key is owned by the mapped host UID, so uid 4242 cannot read
  it: `Load key ...: Permission denied`. `:U` on that mount fixes it, at the
  cost of chowning your host copy.

With both, `preflight` and `deploy` run clean. With neither, a run directory on
a foreign-UID mount also stays `0755` and vcows tells you what that costs rather
than failing — the seed ISOs in it carry `user_data` verbatim.

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

**Inside the guest the interfaces are called `nic0`, `nic1`, …** — not `eth0` or
`ens3`. vcows writes a cloud-init `network-config` v2 document keyed on those
names and matched by MAC, and cloud-init renames each interface to the key it
matched. It follows the order of `nics:` in the config. Anything in the golden
image keyed to a predictable kernel name — a firewall zone, an `ifcfg` file, a
monitoring check — sees the renamed device.

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
references it any more.

**VMs vcows creates start with the host.** Every domain is defined with
autostart on, so a hypervisor reboot brings them back without vcows. The
alternative is worse than it sounds: a re-run after a reboot finds the domains
defined, reports them as ours, prints `nothing to create` and exits 0, with every
guest powered off. There is no `start` verb — turn autostart off per domain
with `virsh autostart --disable <name>` if a host must come up quiet.

> **The config is a secret artifact.** Credentials are cleartext at v0.1,
> deliberately and temporarily. Do not commit it, and do not ship it as an
> example.

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
tofu/             the module, the tfvars, the saved plan, the JSON streams, the state
inventory.json    name -> configured_address, uuid, disks. Minimal, and unstable at v0.1
manifest.json     which build produced this run
run.json          what was asked, what was decided, what happened
```

The OpenTofu state is written here and **never read back**. Destroy works from the
marker, so losing the state costs nothing.

**Nothing expires them.** Run directories accumulate, each with the seed ISOs of
its deploy, and deleting them is the operator's job — vcows never does, because a
run it removed is the one somebody needed. On a host that keeps them:

```bash
find runs/ -mindepth 2 -maxdepth 2 -type d -mtime +30 -exec rm -rf {} +
```

## Air gap

The image carries the OpenTofu provider in `/opt/tofu-mirror` and points at it
through `TF_CLI_CONFIG_FILE=/opt/tofu/tofurc`. There is no `direct` block in that
config: a provider missing from the mirror fails immediately instead of resolving
DNS and hanging. The gate for this is `tests/test_image.py`, which runs
`--network=none` and requires `tofu init` and `tofu validate` to succeed against
the real module with no network at all.

## The image

Built from `quay.io/rockylinux/rockylinux:10`, pinned by digest, ~444 MB on disk
and ~152 MB as a delivered `podman save | gzip` tarball. Most of that is payload
rather than base: the OpenTofu binary is 115 MB and the provider another 26 MB.
Smaller bases were measured and both pass the same gate — `10-minimal` delivers
at 134 MB, losing `vi`, `less`, `tar`, `ping` and `dnf`; a `10-ubi-micro` builder
build delivers at 118 MB and additionally has no `rpm`, so the image cannot report
its own contents at a site. Switching later is one `ARG` and a package-manager
name.

The provider plugin cache is warmed at build time, so `tofu init` symlinks into
`/opt/tofu/plugin-cache` instead of unpacking a 26 MB copy into every run
directory.

## Licensing

`/opt/vcows/manifest.json` lists every package in the image with its version,
licence and source RPM, plus the OpenTofu and provider versions and the git
revision that built it. The same file is copied into every run directory.

The image contains GPL-2.0-**only** components, and GPLv2 §3 offers no
network-server option for source — so **source ships as a separate medium
accompanying the delivery**, mirrored from the `source_rpms` list in that
manifest. The OpenTofu provider's licence and its provenance are vendored at
`/opt/vcows/licenses/dmacvicar-libvirt/`; upstream ships no `LICENSE` file in
0.9.x, and the note there explains why that is a gap rather than a revocation.

## Development

```bash
uv venv --python /usr/bin/python3 --system-site-packages
uv pip install -e . --group dev
pytest
```

`--system-site-packages` is not optional: `python3-libvirt` comes from the RPM
(PyPI ships an sdist only), and without the flag the binding is invisible inside
the venv while `python3 -c 'import libvirt'` keeps working outside it.

Three test gates are skipped unless you opt in, each with an explicit reason:

| | |
|---|---|
| `VCOWS_RIG_URI=qemu+ssh://…` | Runs preflight against a real hypervisor. |
| `VCOWS_IMAGE=localhost/vcows-deploy:0.1.0.0` | Runs the offline container gate. |
| `.tools/tofu-mirror` present | Runs the OpenTofu module gates. |
