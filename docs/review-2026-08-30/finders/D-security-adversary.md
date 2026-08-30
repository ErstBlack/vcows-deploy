# Dimension D — Security adversary against the fixes

Scope: the S1 remediation (`df60f74`) and everything da3f45c..HEAD touching the
credential path. Target files: `container/entrypoint.py`,
`orchestrator/backends/libvirt/schema.py`, `orchestrator/config.py`. Verified
against OpenSSH_9.9p1 and libvirt 11.10.0 present on this host.

## Verdict

The critical injection (2026-08-29 F-SEC-01) is **closed and robust**. F-SEC-02
(`source_qcow2` path/URL confusion) and the URI-credential leak are closed for
every vector I could reach. One confirmed low-severity residue: the container
entrypoint writes `~/.ssh/config` on the `validate` verb, contradicting
`cmd_validate`'s "nothing is written" — but with the injection closed it writes
only a benign, pattern-validated file, so there is no security teeth left in it.

## Attack 1 — newline / directive-separator injection into `~/.ssh/config`

`SSH_PATH_PATTERN = r"^/[^\s]*\Z"` (schema.py:55) and its duplicate
`SSH_PATH = re.compile(r"^/[^\s]*\Z")` (entrypoint.py:45). Both guard: the schema
via `TARGET_SCHEMA` (schema.py:154-155), the entrypoint via `_path`
(entrypoint.py:74-91) which runs *before* `vcows` is a process.

I fired every line/token separator OpenSSH could honour through both
`entrypoint.ssh_config()` and the schema validator:

```
'/k\n  ProxyCommand id'    rejects  rejects   (LF)
'/k\r  ProxyCommand id'    rejects  rejects   (CR)
'/k\r\n ...'               rejects  rejects
'/k\x0b...' (VT)           rejects  rejects
'/k\x0c...' (FF)           rejects  rejects
'/k\x85...' (NEL)          rejects  rejects
'/k\xa0...' (NBSP)         rejects  rejects
'/k ...' (LS)         rejects  rejects
'/k ...' (PS)         rejects  rejects
'/k ProxyCommand id' (SP)  rejects  rejects
'/k\tProxyCommand id'      rejects  rejects
```

`[^\s]` under a `str` pattern is Unicode-aware, so it refuses NEL, NBSP, LS and
PS too — a wider net than OpenSSH's own `WHITESPACE " \t\r\n"` needs. `\Z`, not
`$`, closes the trailing-newline case Python's `$` would have admitted; the
commit message and comments call this out explicitly and it holds. The only
values the pattern *accepts* that carry metacharacters — `/k=b`, `/k#c`,
`/k%d/x`, `/k"q"`, `//host/k` — contain no whitespace, so on a single
`IdentityFile <value>` / `UserKnownHostsFile <value>` line they remain one token
and cannot open a second directive. No `ProxyCommand`, `Include`,
`StrictHostKeyChecking no`, `LocalCommand`, or `IdentityAgent` is reachable.
Injection is closed.

## Attack 2 — `install()` on the `validate` verb (CONFIRMED, low)

`main()` (entrypoint.py:198-203) calls `install(sys.argv[1:])` unconditionally
for every verb, then `os.execv`. `config_path` (entrypoint.py:61-71) finds the
config by existence, so `vcows validate <cfg>` triggers a write.

Reproduced: `install(["validate", cfg])` against a config whose `ssh_keyfile` /
`known_hosts` are valid paths writes `~/.ssh/config` — even when the config is
otherwise invalid (missing `vcpus`, which `vcows validate` would then report as
an error). `cmd_validate` (cli.py:245) documents "No connection is opened and
**nothing is written**." That sentence is false for the container invocation.

Security teeth: gone. A *poisoned* config no longer writes anything —
`ssh_config()` raises in `_path` before `home()` is consulted
(entrypoint.py:149-155), confirmed: a `\n ProxyCommand` value prints the refusal
and leaves no file. What remains is a benign file built from whitespace-free
absolute paths with `StrictHostKeyChecking yes`. The write also defers if
`~/.ssh/config` already exists. So this is a contract/quality defect, not an
exploit. Filed low as RW-D1 because the docstring at cli.py:245 states a
guarantee the code does not keep; the fix is either to scope `install()` off
`validate`/`version` or to correct the docstring.

## Attack 3 — schema vs. downstream: where the rejection lives

Both layers reject independently. The schema rejects at
`TARGET_SCHEMA`/`VM_SCHEMA` (config load path), and `entrypoint._path` rejects at
the container layer that runs before the schema is ever consulted. The container
rejection is upstream of any tfvars serialisation, and a poisoned value never
reaches `ssh_config()`'s interpolation. This is the correct placement — the
rejection is not merely downstream of render.

## Attack 4 — `source_qcow2` path vs. URL (`^/`, config.py:56)

`IMAGE_SCHEMA.source_qcow2` is now `{"minLength":1,"pattern":"^/"}`. It flows to
`create.content.url` in tofu (render.py:71 → main.tf:30).

- `file://…` and `http(s)://…` no longer match `^/`. The two spellings F-SEC-02
  used to skip the `NotAQcow2` ERROR are structurally invalid. Closed.
- A plain absolute path to a non-qcow2 file (e.g. `/run/secrets/id_ed25519`, the
  exfil target) matches `^/` but is opened by `_check_disk_capacity`
  (schema.py:569-583) and raises `NotAQcow2` — a hard ERROR, not the degraded
  WARNING the `file://` trick produced. The local-file exfil is closed.
- `//evil.example.com/payload.qcow2` matches `^/` (only the first char is
  anchored). As a filesystem path it collapses to `/evil.example.com/…` (local,
  absent → WARNING). Whether the pinned provider (dmacvicar/libvirt 0.9.8) treats
  an empty-scheme `//host/path` in `content.url` as a remote fetch I could not
  execute here; on the standard Go url semantics that field uses, an empty scheme
  reads the local `url.Path` and ignores the host, i.e. no outbound fetch. I did
  not confirm the provider end, so this is recorded as unverified, not a finding.

## Attack 5 — URI userinfo credentials reaching disk

`_check_target` now rejects `parts.password is not None` (schema.py:334-348).
`connection_uri` (schema.py:228-229) leaves the netloc verbatim, so a password
would otherwise have reached `main.auto.tfvars.json` via render.py:61 — that path
is now closed at validate. The username still renders into the URI (it is not a
secret and both `ssh -l` and libvirt consume it as an auth name). Closed for
credentials.

## Attack — URI hostname/username as an ssh option (new angle, closed by libvirt)

`_check_target` checks the scheme, that a hostname exists, the path, the query,
the password, and the fragment — but not the *characters* of hostname or
username. `connection_uri` passes the netloc through untouched to
`libvirt.open()` in preflight. I tried
`qemu+ssh://-oProxyCommand=<script>/system`: libvirt 11.10.0 rejects it with
`hostname contains invalid characters` and never spawns ssh — the ProxyCommand
did not fire. A leading-dash username is delivered through `ssh -l <name>`, whose
value is consumed regardless of a leading dash. libvirt's own hostname validation
closes this before the apply is reached. Not a finding.

## Attack 6 — malformed config: traceback vs. report

`install()` guards each level with `isinstance` (entrypoint.py:132-145) and wraps
the parse in `except (OSError, yaml.YAMLError)`. I fed it a scalar `libvirt:`, a
list, and `ssh_keyfile` as int / list / dict / bool: every case either returned
silently or printed the one-line refusal, none raised. No traceback escapes the
entrypoint into what should have been `validate`'s sentence. Closed.

## What I did not verify

- The dmacvicar/libvirt 0.9.8 provider's actual handling of `content.url` with an
  empty-scheme `//host/path` (no provider execution available offline).
- The go-libvirt `qemu+sshcmd` argv construction for a hostile netloc at apply
  time; preflight's libvirt-C-client hostname validation gates the deploy before
  apply, so this is defence-in-depth rather than the sole barrier.
