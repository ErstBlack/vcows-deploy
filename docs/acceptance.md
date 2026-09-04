# The acceptance run — 2026-08-29

The definition of done in `findings.md` §6, run once against a real hypervisor
through the container. Four of §6's verification items were open before this;
all four are closed below.

**What this proves:** the pipeline. **What it does not prove:** the real golden
artifact. D3 stays open — this ran against the stock `Rocky-9-GenericCloud-Base`
stand-in, so cloud-init and `growpart` are confirmed for *that* image only.

## Target

| | |
|---|---|
| Hypervisor | Fedora 44, libvirt 12.0.0, **split daemons** (`virtqemud`), **SELinux Enforcing** |
| Client | the shipped image, `localhost/vcows-deploy:0.1.0.0`, run rootless |
| Pool | `images`, `/var/lib/libvirt/images`, pre-existing and active |
| Image | `Rocky-9-GenericCloud-Base.latest.x86_64.qcow2`, 645988352 bytes |
| Config | two VMs, both EFI — `app01` with libvirt autoselecting firmware, `app02` with a pinned `loader`/`nvram_template` — static `.60`/`.61`, `disk_gb: 40` against a 10 GiB image |

## What it found

Five defects, none of which any earlier gate could have caught, because every one
of them lives past the point where a plan stops.

### 1. The provider and libvirt need different transports

`preflight` and the apply use different SSH clients, and no single URI works for
both:

| client | transport | result |
|---|---|---|
| libvirt (preflight) | `qemu+ssh` | **works** — reaches split daemons via `virt-ssh-helper` |
| libvirt | `qemu+sshcmd` | `remote_open: transport in URL not recognised` |
| libvirt | `qemu+libssh` | works for small replies, and honours both credential parameters. **Corrected 2026-09-04 (#247):** from the Python binding, replies of roughly 4-37 KB hang (`virNetLibsshSessionHasCachedData` sees only libvirt's own buffer), so it is not used. |
| provider | `qemu+ssh` | **fails** — dials a hardcoded `/var/run/libvirt/libvirt-sock` that a split-daemon host does not have; given `socket=` the forward is refused, because SELinux does not let `sshd` open a libvirt socket |
| provider | `qemu+sshcmd` | **works** — runs `ssh` and asks the remote end for `virt-ssh-helper`, falling back to `nc -U` |
| provider | `qemu+libssh` | `unsupported transport: libssh` |

So one config yields two schemes: `connection_uri(target)` for preflight,
`connection_uri(target, "sshcmd")` for the provider.

**On monolithic vs split daemons:** nothing needs building. The provider's
`sshcmd` dialer already runs

```sh
sh -c 'which virt-ssh-helper >/dev/null 2>&1; if test $? = 0; then virt-ssh-helper "%s"; else ... nc $ARG -U %s; fi'
```

— the modern path first, the monolithic socket as fallback. The `ssh` dialer is
the one with no fallback, and it is the one we no longer use.

### 2. The credential parameters do not work, in any spelling

`ssh_keyfile` and `known_hosts` were being passed as URI query parameters. That
never worked:

* libvirt's `qemu+ssh` honours `keyfile=` but **ignores `known_hosts=`** — it is a
  libssh/libssh2 parameter. The failure is `Host key verification failed`, with
  nothing pointing at the cause.
* The provider's `ssh` dialer spells it **`knownhosts`**, no underscore.
* `sshcmd` fails on either spelling.
* `HOME` is not a lever: OpenSSH and Go's `os/user` both resolve `~` from the
  passwd entry.

**Corrected 2026-09-04 (#247).** `keyfile=` does work on `qemu+ssh`, and
`command=` can name a wrapper that supplies `UserKnownHostsFile`, so
`preflight.connect` writes both credentials to a session temporary directory and
dials them that way. The container entrypoint that wrote `~/.ssh/config` is gone.

### 3. `libvirt_volume.seed` declared the wrong format

`target = { format = { type = "raw" } }` on an ISO. libvirt inspects the uploaded
content and reports what it detects, so the provider's post-apply read disagreed
with its own plan:

```
Provider produced inconsistent result after apply … .target.format.type:
was cty.StringVal("raw"), but now cty.StringVal("iso")
```

The apply failed *after* writing the volumes, leaving four orphans. Now `iso`.

### 4. The module never emitted ACPI, so no EFI domain could be defined

```
Failed to define domain in libvirt: unsupported configuration:
UEFI requires ACPI on this architecture
```

The provider writes exactly the XML it is handed and nothing supplies a default.
`features = { acpi = true, apic = {} }` now, matching what every domain libvirt
builds for itself carries — the rig's own guests are `<acpi/><apic/>`, recorded in
`tests/fixtures/libvirt/domain-unmarked-running.xml`.

### 5. `routes: [{to: default}]` is a netplan idiom cloud-init does not implement

The worst-shaped failure of the five. cloud-init 24.4 accepted the document, read
it, logged `Applying network configuration from ds`, and then threw out of its own
v2-to-v1 normaliser:

```
ValueError: Address default is not a valid ip address
  cloudinit/net/network_state.py:994 in _normalize_net_keys
```

It then applied nothing and fell back to DHCP. Both guests booted **healthy, on
the wrong addresses** — `.205` and `.253` instead of `.60` and `.61` — with
`cloud-init status: done`. Nothing short of checking the address would have
noticed. The default route is now `0.0.0.0/0`.

`prepare.py`'s docstring had flagged this exact line as unconfirmed and named the
acceptance run as what would confirm it. It disconfirmed it.

## The run

| | | |
|---|---|---|
| **A1** | `validate`, `preflight` | Valid; two CREATEs; `probe02` reported as existing and not in this config. |
| **A2** | `deploy`, base volume absent | 7 resources, **17 s including the 646 MB upload**. `create.content.url` accepts a local path — unverified since Stage 2. |
| **A3** | Both guests | Static address exactly as configured, `proto static`; hostname = logical name; configured resolver; injected key works (`user_data` verbatim, D27); **root filesystem 39 G from a 10 GiB image** — the overlay's capacity plus `growpart`, which is F5 and A4's entire premise. `cloud-init status: done`. |
| **A4** | `deploy` again | Both SKIP, `nothing to create`, **no OpenTofu process started and no `tofu/` directory written**. |
| **A5** | Refusals | `virsh domrename app01 app01-renamed` → preflight still reports `app01 skip exists as 'app01-renamed'` (§6 #10). An unmarked domain named `app03` → `refuse … vcows will not adopt or overwrite it`, exit 1, nothing built (§6 #11). |
| **A6** | `destroy`, every state file deleted | Both VMs undefined **including the renamed one**, all four overlays and seed ISOs deleted, **the shared base image untouched**, `vcows-probe02` skipped as `belongs to deployment 'spike'` (§6 #9, and D36). |
| **A7** | `deploy` with the base volume present | **6 resources, not 7** — the base was reused, which is what D23/D24 exist for, and D30's size check passed against it. |

## What the run settled

* **D30's open assumption.** The uploaded volume's `<physical>` equals the local
  file's `st_size`, so `create.content.url` streams the source bytes verbatim
  rather than re-encoding. The size check is sound and needs no sentinel volume.
* **The orphan-volume refusal, for real.** Defect 3's partial apply left four
  volumes. The next preflight named each one and its remedy, and exited 1 —
  findings.md §2's accepted gap, behaving as specified.
* **The NVRAM suffix.** Both domains wrote `<name>_VARS.qcow2`, matching the
  qcow2 template, and both files were removed by the `UNDEFINE_NVRAM` bit on
  teardown — the first NVRAM files this rig has ever had. **Independently
  confirmed on 2026-08-29** by watching `/var/lib/libvirt/qemu/nvram/` from a root
  shell at two-second resolution across a second deploy and destroy; this run
  could not read that directory itself, so until then the claim rested on
  inference. See `findings.md` §2.
* **Secure Boot, incidentally.** With no `loader` configured, libvirt selected
  `OVMF_CODE_4M.secboot.qcow2` with enrolled keys, and the guest booted. The
  pinned-loader VM got the non-secboot build. Both work.

## Still open

* **D3** — the real golden artifact is still unverified. This ran against the
  stand-in.
* **The serial console is not usable for debugging.** D26 put a pty console on
  every domain so a VM that fails cloud-init could be inspected. `virsh console`
  needs a controlling TTY, and a pty keeps no scrollback, so during this run it
  produced nothing on a guest that had already booted. What actually diagnosed
  defect 5 was SSH into the guest — which only worked because the guest was
  reachable at all. A `<log file=…/>` on the serial device would have given the
  boot transcript for free, and is worth considering.
* **A RHEL 9 or RHEL 10 target.** Everything here ran against Fedora 44.
