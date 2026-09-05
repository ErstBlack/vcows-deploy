# libvirt backend, offline half — review

Agent: 03-libvirt-offline · Scope: `orchestrator/backends/libvirt/{schema,render,prepare}.py` · Date: 2026-08-29

## Summary

* The seed ISO is correct and I could not break it: Rock Ridge **and** Joliet
  names, `cidata` on the PVD *and* the Joliet SVD, `user_data` byte-identical
  through both, CRLF and trailing newlines included.
* The network-config survives cloud-init 24.4's own parser and its NetworkManager
  and netplan renderers. Two things in it are not honoured: `dhcp6: false`
  (F-LVOFF-07) and, on a multi-NIC VM, a single default route (F-LVOFF-03).
* `_check_target` guards the query string (R-D) but not the userinfo: a URI with
  `user:password@` validates, reaches neither client, and is copied into the run
  directory's tfvars and the OpenTofu state (F-LVOFF-01).
* `derive_mac` is deterministic and handles within-config collisions, but is
  keyed on the VM name alone: two deployments that each call a VM `app01` derive
  one MAC — permanent by docstring, unshipped in fact (F-LVOFF-02).
* Also past `validate`: a trailing newline in any name, `loader` without
  `loader_format`, absurd `vcpus`, an `ip_cidr` that is its own network address.

## Findings

### F-LVOFF-01 — a password in the URI validates, is ignored, and is written to disk
- **Severity:** S2 · **Confidence:** high
- **Location:** `schema.py:193` (`_check_target`), `schema.py:165` (`connection_uri`)
- **What:** `_check_target` inspects scheme, hostname, path, query and fragment,
  never `parts.password`; `connection_uri` copies the netloc through unchanged.
- **Why it matters here:** the operator who writes a password gets `Permission
  denied (publickey)` under `BatchMode yes`, with the password sitting in the
  config that "must" be right — and it lands in the run dir's tfvars and the
  state file, both kept deliberately, 0700 the only thing holding them.
- **Evidence:** `_check_target({"uri": "qemu+ssh://user:hunter2@vhost/system"})`
  returns no problems; `connection_uri(…, "sshcmd")` returns it verbatim.
- **Fix / cost:** one more clause rejecting a set `parts.password` — six lines
  beside four identical ones, no new concept. The username stays.

### F-LVOFF-02 — the derived MAC ignores `deployment`, so two deployments collide
- **Severity:** S2 · **Confidence:** medium
- **Location:** `schema.py:109` (`derive_mac`), `orchestrator/marker.py:139`
- **What:** `uuid5(VCOWS_NS, f"{name}#nic{index}")`. Deployments `lab-a` and
  `lab-b` that both contain `app01` derive the identical MAC and instance-id.
- **Why it matters here:** names are undecorated by D16, `validate` sees one
  config, and `decide()` refuses a cross-deployment clash only on the same host.
  Two hypervisors bridged to one L2 then carry duplicate MACs: both guests
  intermittently unreachable, both deploys reporting success, nothing pointing at
  the cause. The per-NIC `mac` override is the only escape and is undocumented.
- **Evidence:** `derive_mac(name, index)` and `Marker.for_vm` → `derive_id(name)`
  take no deployment; the pinned MAC depends on nothing else.
- **Fix:** before first ship, either fold `deployment` into the uuid5 input or
  document the name-uniqueness rule.
- **Cost of the fix:** an f-string and two re-pinned tests, or a paragraph.

### F-LVOFF-03 — every NIC carries a default route; `primary` does not choose one
- **Severity:** S3 · **Confidence:** high
- **Location:** `schema.py:52` (`gateway` required), `prepare.py:196`
- **What:** `gateway` is required per NIC and `_network_config` emits a
  `0.0.0.0/0` route for each, so a two-NIC VM always gets two default routes.
- **Why it matters here:** the NetworkManager renderer gives both profiles
  `route1=0.0.0.0/0,…` at equal priority, so NM's tie-break decides, not the
  config: vcows reports the primary address while egress may leave by the other.
- **Evidence:** a two-NIC VM whose *second* NIC is `primary: true` renders
  `nic0 → route1=0.0.0.0/0,192.168.1.1` and `nic1 → route1=0.0.0.0/0,10.10.0.1`.
- **Fix:** emit the default route only for `primary_index(vm)`. No schema change.
- **Cost of the fix:** one condition; making `gateway` optional is a relaxation
  and defers safely, two default routes do not.

### F-LVOFF-04 — `$` in the name and MAC patterns accepts a trailing newline
- **Severity:** S3 · **Confidence:** high
- **Location:** `schema.py:37,39` (and `orchestrator/config.py:33`, `sha256`)
- **What:** jsonschema's `pattern` is `re.search` and Python's `$` matches before
  a trailing newline, so `name: "app01\n"` and `mac: "52:54:00:aa:bb:cc\n"` pass.
- **Why it matters here:** the name becomes a libvirt domain name, two volume
  names, the marker payload, the cloud-init `local-hostname` and an interpolated
  NVRAM path in `main.tf`. Nothing downstream survives a newline in those.
- **Evidence:** `Draft202012Validator(VM_SCHEMA)` on the canonical VM with only
  that field changed reports no error for either value.
- **Fix / cost:** `\Z` instead of `$` in both patterns and the two core ones —
  two characters, affecting no config anyone meant to write.

### F-LVOFF-05 — `loader` without `loader_format` declares a qcow2 firmware as raw
- **Severity:** S3 · **Confidence:** medium
- **Location:** `schema.py:258` (`_check_firmware`), `tofu/main.tf:113`
- **What:** `loader` is accepted without `loader_format`, so the module emits null
  formats and the `.fd` suffix; libvirt defaults an undeclared format to `raw`.
- **Why it matters here:** on a host whose OVMF build is qcow2 the domain defines,
  starts and never boots, while `tofu apply` succeeds and vcows reports done.
  `loader` exists for hosts where autoselection is unavailable — where the
  operator is already guessing.
- **Evidence:** all four working domains on the rig declare it explicitly:
  `<loader … type='pflash' format='qcow2'>…OVMF_CODE_4M.secboot.qcow2</loader>`.
- **Fix:** require `loader_format` once `loader` is set, in the same function.
- **Cost of the fix:** four lines; RHEL 9's raw `.fd` still validates.

### F-LVOFF-06 — no upper bound on `vcpus`, `memory_mib` or `disk_gb`
- **Severity:** S3 · **Confidence:** high
- **Location:** `schema.py:73-75`
- **What:** `vcpus: 100000`, `memory_mib: 1099511627776` and `disk_gb: 1048576`
  all validate.
- **Why it matters here:** the failure this offline gate defers lands at
  domain-define time, after base volume, overlay and seed are written — the
  orphan-volume gap reached by a typo. And tightenings are not backward
  compatible: on a one-way door, a maximum omitted now can never be added.
- **Evidence:** all three values return no error from `VM_SCHEMA`.
- **Fix:** `"maximum"` on the three, at values no site approaches.
- **Cost of the fix:** three keys in a schema already carrying three `minimum`s.

### F-LVOFF-07 — `dhcp6: false` is inert on RHEL-family guests
- **Severity:** S3 · **Confidence:** high
- **Location:** `prepare.py:193`
- **What:** cloud-init's NetworkManager renderer — the RHEL 9.4+ / RHEL 10 path —
  emits no `[ipv6]` section, so NM's default `ipv6.method=auto` applies and the
  guest still does SLAAC and takes an RA default route.
- **Why it matters here:** the document asserts a v4-only statically addressed
  guest and produces one that may hold an unrequested v6 address and route.
  Third instance of the acceptance pattern: emitted, accepted, not honoured.
- **Evidence:** the keyfile cloud-init 24.4 renders from this exact output has
  `[ipv4] method=manual …` and no `[ipv6]`; `accept-ra: false` changes nothing.
- **Fix:** one docstring sentence saying the v6 half is not configured.
- **Cost of the fix:** a line; really disabling v6 needs a renderer-specific
  escape hatch that does not belong in v0.1.

### F-LVOFF-08 — an `ip_cidr` that is its own network or broadcast address validates
- **Severity:** S3 · **Confidence:** high
- **Location:** `schema.py:332-351`
- **What:** the gateway is checked for membership in the interface's network; the
  address is never checked for *being* that network's address or broadcast, and
  the gateway may equal the VM's own address.
- **Why it matters here:** an off-by-one in a hand-written subnet is the typo this
  check exists for, and the VM boots, reports `cloud-init status: done` and is
  unreachable while vcows prints the address as deployed.
- **Evidence:** `192.168.122.0/24` and `192.168.122.255/24` both pass validate.
- **Fix / cost:** two comparisons beside the gateway check, skipped for /31 and
  /32 — about eight lines in the function that already owns this reasoning.

### F-LVOFF-09 — `base_volume_name` is not checked against the derived volume names
- **Severity:** S3 · **Confidence:** medium
- **Location:** `render.py:42-47`, `schema.validate`
- **What:** overlays are `<vm>.qcow2` and seeds `<vm>-seed.iso`, in the same pool
  as `image.base_volume_name`; a collision is accepted and no test relates them.
- **Why it matters here:** the base volume is shared by every deployment on the
  host. The apply fails loudly, but only after writing the other volumes, and the
  first reading is "vcows tried to overwrite the golden image".
- **Fix:** one set-membership check against `{overlay_name(n), seed_name(n)}`.
- **Cost of the fix:** five lines, pulling `render`'s naming into `schema`.

### F-LVOFF-10 — nothing checks that `ssh_keyfile` and `known_hosts` exist
- **Severity:** S3 · **Confidence:** high
- **Location:** `schema.py:103-104`
- **What:** both are any non-empty string; only `container/entrypoint.py` reads
  them, from the raw YAML, writing the path into `~/.ssh/config` unchecked.
- **Why it matters here:** `BatchMode yes` turns a typo'd key path into `Permission
  denied (publickey)` and a typo'd `known_hosts` into `Host key verification
  failed`, the message the acceptance run spent a cycle on. Both are
  offline-checkable in the container, where the mounts already exist.
- **Evidence:** `grep -rn "ssh_keyfile\|known_hosts" --include=*.py` outside tests
  matches only the schema entry, docstrings and the entrypoint.
- **Fix:** a WARNING in `_check_target` — not an error; `validate` runs anywhere.
- **Cost of the fix:** eight lines and a second I/O touch beside `virtual_size`.

## Checked and sound

* **The seed ISO.** PVD `vol_ident` `cidata` *and* Joliet SVD label `cidata`;
  `blkid` reports `LABEL="cidata" TYPE="iso9660"`. All three files carry a
  conforming 9660 identifier, a Rock Ridge name and a Joliet name.
* **D27, byte-for-byte.** `user_data` with CRLF endings and three trailing
  newlines reads back identical through both the Rock Ridge and Joliet paths.
* **The network-config** through cloud-init 24.4's `parse_net_config_data`: one
  physical interface, static subnet, route normalised to `network 0.0.0.0 prefix
  0`, nameservers carried, clean under two renderers. `0.0.0.0/0` is right.
* **`derive_mac` is deterministic** (uuid5, QEMU OUI, three digest bytes), and a
  configured MAC colliding with another VM's *derived* MAC is caught: `_check_nics`
  dedupes on `mac_of(...)` lowercased. Duplicate IPs are caught the same way.
* **The NIC union** both-set and neither-set; multiple `primary: true`; the
  bios/UEFI cross-checks; `loader`↔`nvram_template` pairing; `disk_gb` under the
  image's virtual size (error), an unreadable image (warning); unicode and
  over-length names; a non-string or null `user_data`.
* **`connection_uri`** strips the query for both transports, rewriting only the
  scheme; a port survives, which `test_a_good_uri_passes` blesses deliberately.

## Not checked

* cloud-init 22.1 / 23.1 as shipped on RHEL 9.0–9.3 EUS — only 24.4 is on this
  host and no package index is reachable. The `sysconfig` renderer is where I
  would look next: it names the interface from the `ethernets` key, so `nic0`
  reaches `ifcfg-*` and `70-persistent-net.rules`.
* Anything needing an apply: F-LVOFF-05, -06 and -09 are reasoned from the
  read-only rig's existing domain XML, not from a failed create.

## Deserves its own agent

* **cloud-init version matrix.** The guest-side contract rests on one release
  having been observed. Boot this exact `network-config` on RHEL 9.2 and 9.6.
* **Interface renaming.** cloud-init's `apply_network_config_names` renames the
  guest's NICs to the `ethernets` keys, `nic0`/`nic1`. Nothing documents that
  vcows renames the operator's interfaces, and it does.
* **`orchestrator/config.py`'s patterns** carry the same `$` hole as F-LVOFF-04,
  `image.sha256` included, where a trailing newline in 64 hex is a plausible paste.
* **The run directory as a secret store.** F-LVOFF-01 shows the tfvars carry
  whatever userinfo the URI held; inventory what it holds after a real deploy.
