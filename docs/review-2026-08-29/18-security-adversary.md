# Hostile inputs and the credential path — review

Agent: 18-security-adversary · Date: 2026-08-29 · Scope: `container/entrypoint.py`,
`orchestrator/{config,tofu,cli,marker,qcow2}.py`, `backends/libvirt/*.py`, `tofu/*.tf`

## Summary

- `ssh_keyfile` and `known_hosts` are unpatterned strings interpolated line-wise into
  `~/.ssh/config`. A newline injects arbitrary OpenSSH directives: `ProxyCommand` is
  command execution in the container, and an injected `StrictHostKeyChecking no` **wins**
  over the tool's own `yes` because OpenSSH takes the first value. This reopens the exact
  hole `_check_target`'s query-string refusal (R-D) exists to close.
- `image.source_qcow2` is checked as a local *path* and consumed as a *URL*: a `file://`
  or `http://` string skips the `NotAQcow2` gate with a warning and reaches the provider's
  `create.content.url`.
- `destroy` deletes whatever `<source file>` paths the hypervisor's domain XML declares,
  filtered only on `device` type — the `<backingStore>` exclusion does not stop a plain
  `<disk>` entry naming the golden image.
- Otherwise clean: no credential in any argv or in the child environment, no `shell=True`,
  no `yaml.load`, no key material copied. `container/entrypoint.py` has **zero tests**.

## Findings

### F-SEC-01 — `ssh_keyfile` / `known_hosts` inject arbitrary directives into `~/.ssh/config`
- **Severity:** S1
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/schema.py:103-104`,
  `container/entrypoint.py:75,79,112`
- **What:** Both fields are `{"type": "string", "minLength": 1}` — no pattern — and
  `_check_target` does not look at either. `ssh_config()` builds
  `f"  IdentityFile {keyfile}"` and `f"  UserKnownHostsFile {known_hosts}"` and joins with
  `"\n"`, so a newline in either value emits new config lines. Reachable directives include
  `ProxyCommand` (run through `/bin/sh -c` on **every** ssh invocation, so it needs no
  `PermitLocalCommand`), `LocalCommand`, `Include`, `IdentityAgent`, and
  `StrictHostKeyChecking no` — and OpenSSH takes the *first* obtained value for a keyword,
  so an injected `no` beats the `yes` the code appends two lines later.
- **Why it matters here:** the config author is not the operator, and `_check_target`
  already calls "the operator must not be able to disable host key verification" the single
  most important check it makes (schema.py:222). The URI route is closed; this one is open
  and reaches further — `ProxyCommand` runs attacker-chosen commands as the container user,
  the user holding the mounted private key and the live SSH path to the hypervisor. Both
  variants look like success: the deploy completes normally. Two aggravators: `install()`
  runs on `validate` too, before any schema check, so `vcows validate` writes the poisoned
  file though `cmd_validate` says "nothing is written"; and `entrypoint.py:112` returns
  early if `~/.ssh/config` exists, so with a persistent `$HOME` one hostile config poisons
  every later run with a clean one.
- **Evidence:** `ssh_config()` called from the real module, with
  `ssh_keyfile="/k/id\n  ProxyCommand /bin/sh -c 'id>/tmp/pwned'"` and
  `known_hosts="/kh\n  StrictHostKeyChecking no"`:
  ```
  Host *
    BatchMode yes
    IdentityFile /k/id
    ProxyCommand /bin/sh -c 'id>/tmp/pwned'     <- injected, runs on every ssh
    IdentitiesOnly yes
    UserKnownHostsFile /kh
    StrictHostKeyChecking no                    <- injected, first value wins
    StrictHostKeyChecking yes
  ```
  A config carrying both loads clean: `orchestrator.config.load()` returns with only the
  unrelated `source_qcow2` warning, nothing against either field.
- **Fix:** give both fields a path pattern in `TARGET_SCHEMA` — `^/[^\s]*$` matches how
  they are actually used (absolute container paths to mounted files).
- **Cost of the fix:** two regex strings in an existing schema dict; no new module, no new
  concept. Justified by the argument already written for the query string.

### F-SEC-02 — `source_qcow2` is validated as a path and consumed as a URL
- **Severity:** S2
- **Confidence:** medium-high (offline half reproduced; provider fetch inferred from the
  pinned schema, not executed)
- **Location:** `orchestrator/config.py:42`, `orchestrator/backends/libvirt/schema.py:403`,
  `orchestrator/backends/libvirt/preflight.py` (`base_volume`, `os.stat(source)`),
  `orchestrator/backends/libvirt/tofu/main.tf:30`
- **What:** `source_qcow2` is `{"type": "string", "minLength": 1}`. `_check_disk_capacity`
  opens it as a filesystem path, so a real local non-qcow2 file raises `NotAQcow2` — a
  genuine hard ERROR. But a string the local layer cannot `open()` degrades to a WARNING,
  the deploy proceeds, and `render` copies it verbatim into `base_volume.source`, which
  `main.tf:30` hands to `create = { content = { url = ... } }` — described in
  `docs/provider-schema-0.9.8.json` as "URL to download content from". So
  `file:///run/secrets/id_ed25519` fails the local `open()` (no file is literally named
  `file:///...`), warns, then is resolved *as a URL* by the provider: the `NotAQcow2` gate
  is bypassed by spelling the same file differently. `http://…` is that pointed outward.
- **Why it matters here:** as exfiltration, a config author with no other access gets the
  container to read any file its UID can — including the mounted SSH private key — and
  upload it into the hypervisor's pool under `base_volume_name`, where they read it back.
  The apply then fails on the same declared-vs-detected format mismatch that bit the seed
  volume in the acceptance run, but the volume is already written. As an air-gap property,
  an `http(s)://` source is the one place a config value causes an outbound fetch to an
  arbitrary host; R7 and the removed `direct` block close the registry path, not this one.
- **Evidence:** `load()` on a config with `source_qcow2: file:///run/secrets/id_ed25519`
  returns successfully with one warning ("cannot read file:///run/secrets/id_ed25519 …"),
  and `render.render` emits
  `base_volume: {'name': 'golden.qcow2', 'create': True, 'path': '',
  'source': 'file:///run/secrets/id_ed25519'}`.
- **Fix:** `"pattern": "^/"` on `source_qcow2` in `IMAGE_SCHEMA`, making `file://` and
  `http://` structurally invalid and leaving the existing `NotAQcow2` ERROR as the only way
  in. The README's contract is already a bind-mounted image at `/images/golden.qcow2`.
- **Cost of the fix:** one regex. It forecloses a remote-source feature findings.md never
  contemplated and R7 rules out anyway.

### F-SEC-03 — destroy deletes disk paths the hypervisor chose, filtered only by device type
- **Severity:** S3
- **Confidence:** medium
- **Location:** `orchestrator/backends/libvirt/preflight.py:87-105` (`disks_of`),
  `orchestrator/cli.py:265`, `orchestrator/backends/libvirt/destroy.py:184`
- **What:** `disks_of` collects every `<devices/disk>` whose `device` is `disk` or `cdrom`
  and returns its `<source file=…>` verbatim; `_delete_volume` calls
  `conn.storageVolLookupByPath(path).delete(0)` on each. The only path filter is "not a
  `<backingStore>`". `destroy.py`'s docstring calls that exclusion "the only thing between
  this call and the shared golden image" — true, and that is the problem: it excludes an
  *element*, not a *path*, so a `<disk>` whose `<source file>` names the golden image is
  deleted. Separately, `cli.py:265` targets every marked domain whose `deployment` matches,
  not only names in the config, and the marker is a public deterministic value (`uuid5` of
  a documented namespace) anyone with define rights can forge.
- **Why it matters here:** the rig is a shared host with four VMs belonging to someone else.
  Anyone who can edit or define a domain there can make a routine `destroy` delete the
  golden image every other deployment's overlays back onto, or an unrelated pool volume,
  under the operator's credential. `vol.delete` offers no protection — the file itself notes
  `in_use` is never set here. `marker.py` calls a hand-edited marker "user error and out of
  scope": that covers corrupting *our* marker, not planting one to aim our teardown.
- **Evidence:** `disks_of` has no path predicate — `if path := source.get("file"):
  paths.append(path)` — and no caller adds one; `_delete_volume` receives `target.disks`
  unfiltered. `_domains()` builds `Existing.disks` straight from `dom.XMLDesc`.
- **Fix:** in `destroy`, delete only paths whose basename is in
  `{overlay_name(m.name), seed_name(m.name)}` for that target's marker, reporting anything
  else as skipped. Both helpers exist and `orphan_volumes` already relies on this naming.
- **Cost of the fix:** one set comprehension and a `Problem` in `destroy.py`. It adds one
  failure mode — a disk attached to a vcows VM out of band is skipped rather than deleted —
  which is the safe direction and already how an unresolvable path is treated.

### F-SEC-04 — URI userinfo is unchecked, so a password is written to disk and cannot work
- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/schema.py:205-241` (`_check_target`)
- **What:** `_check_target` inspects scheme, hostname, path, query and fragment, never the
  userinfo, and `connection_uri` preserves the whole netloc. So
  `qemu+ssh://user:hunter2@h/system` is accepted, becomes
  `qemu+sshcmd://user:hunter2@h/system` in `main.auto.tfvars.json` and `plan.bin`
  (13-run-dir-artifact), and appears in any diagnostic tofu prints — `tofu.py:_run`
  re-raises those into `TofuError`, which `cli.main` echoes to stderr, outside the 0700 run
  directory. It also cannot authenticate: neither client does password auth over these
  transports, so the whole userinfo goes to `ssh -l`.
- **Why it matters here:** the credential design is deliberate and complete — key path and
  known_hosts path, never material — and this is the one spelling that smuggles material in.
  A config author following habit rather than the README leaks a password to disk and to the
  terminal, for a connection that then fails naming a username nobody wrote.
- **Evidence:** `_check_target` reads only `parts.{scheme,hostname,path,query,fragment}`;
  `connection_uri` does `parts._replace(scheme=…, query="")`, leaving `netloc` untouched.
- **Fix:** reject `parts.password is not None`, next to the query-string refusal.
- **Cost of the fix:** three lines in the function that already owns URI hygiene.

## Checked and sound

- **No shell anywhere.** `subprocess.run` appears only in `tofu.py` (three sites) and
  `manifest.py`, all list argv, no `shell=True`; `entrypoint.py` uses `os.execv` with a
  list. No `os.system`, no `eval`. Both YAML loads are `yaml.safe_load`.
- **Process listing.** `tofu.py:_run` builds
  `[tofu, -chdir=…, <cmd>, -input=false, -no-color?, -json-into=…, …]`. No credential, URI,
  or config value reaches any argv; the provider child gets its config over tfplugin gRPC.
  `ps` on the host shows nothing sensitive.
- **Environment.** `tofu._env()` is `{**os.environ, CHECKPOINT_DISABLE, TF_IN_AUTOMATION}`;
  vcows never puts a credential in it, and the image's `ENV` block is paths and flags only,
  so `podman inspect` exposes mount paths and the config filename and nothing more.
- **`user_data` traced end to end.** One sink: `.encode()` into the seed ISO
  (`prepare.seed_files`). Not in the tfvars (`render._vm` carries only the `seed_iso` path),
  not in `run.json`, `inventory.json`, or the domain XML. No path join, no shell, no XML
  parse. The hypervisor-side copy is inherent to NoCloud; the local one is F12's 0700
  artifact.
- **Marker XML injection is closed.** `Marker.to_json` uses `json.dumps`, which does not
  escape `<`, `>` or `&`, and `to_xml` interpolates it raw into `marker_xml` → `<metadata>`.
  What prevents injection is that `name` and `deployment` are both constrained to
  `[A-Za-z0-9][A-Za-z0-9._-]{0,62}` and enforced before `render` runs — correct today, and
  the only thing holding it closed, which neither docstring mentions.
- **Path traversal via config-controlled names.** `deployment` (run directory), VM `name`
  (`<name>-seed.iso`) and the tofu paths are pattern-constrained or tool-generated;
  `deployment` defaults to `Path.stem`, which cannot hold a separator. `_run_dir` resolves
  and `chmod 0700`s the leaf rather than trusting umask.
- **D13's justification still matches the code.** All three `ET.fromstring` sites
  (`_domains`, `volume_facts`, `_network_claims`) parse libvirt's own `XMLDesc`
  re-serialisation, exactly as `preflight.py`'s docstring claims. No finding. `base_volume_name`
  is unpatterned but reaches only a libvirt volume name and dict keys, never a path join.

## Not checked

- No live-hypervisor or podman work, per the brief. F-SEC-02's provider-side fetch and the
  contents of `plan.json` / `apply.json` under a real diagnostic are reasoned from the
  pinned schema and the code, not observed.
- The seed ISO's own file mode (pycdlib writes at umask); the 0700 parent makes it moot,
  and 13-run-dir-artifact owns the run directory.
- OpenSSH's `StrictHostKeyChecking` precedence is taken from the documented "first value
  wins" rule, not run against `ssh -G` inside the image.

## Deserves its own agent

- **`container/entrypoint.py` has no tests at all** — no `test_entrypoint.py`, and
  `test_image.py` only runs the image with `--entrypoint` overridden to `python3` or `sh`,
  so `install()` is never exercised. It is the whole credential path, and the only file in
  the tree that writes into a home directory.
- The ordering itself: `entrypoint.install()` acts on an unvalidated config before `cli.py`
  runs, contradicting `cmd_validate`'s "nothing is written".
