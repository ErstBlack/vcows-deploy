# Dimension C — validation and schema

Agent: C-validation · Scope: `orchestrator/backends/libvirt/schema.py`,
`orchestrator/config.py`, `orchestrator/marker.py`, `orchestrator/backends/base.py`
(`problems_from`) and their tests · Range: `4eb378b..672a500` · Date: 2026-08-31

Measured in the worktree pinned at `672a500`, against `tests/conftest.CONFIG`. Extends
`docs/review-2026-08-30/finders/C-validation.md` rather than repeating it.

## Summary

* **#43 is correct, and provably so.** `removeprefix(".")` cannot strip a dot that belongs
  to a key, under either jsonschema in play. The production call site `_blame_the_filename`
  still fires; two tests fail if the `removeprefix` is dropped.
* **#27 is half a fix.** The identical defect survives one level up: any structural error
  on a VM — `vcpus: 0`, a typo'd key, anything at all — makes that VM skip `_check_nics`
  entirely, so its addresses and MACs never register and duplicates go unreported.
* **RW-C1's fix is real.** `image.sha256` is now computed and compared, both directions,
  case-insensitively, and only when declared. Its boundary is the local file.
* One malformed-URI class escapes `_check_target` as an uncaught `ValueError` and takes
  every other diagnostic in the config with it.
* Everything else on the surface rejects what it claims and nothing legitimate that I
  could construct.

---

## Findings

### RX-C1 — #27's defect survives at the VM level (medium)

- **Location:** `orchestrator/backends/libvirt/schema.py:243-249`
- **What:** `validate` runs `_check_vm_structure`, and on *any* structural problem does
  `continue`, skipping `_check_firmware` and `_check_nics` for that VM. `_check_nics` is
  the only thing that populates `seen_ips` and `seen_macs`, so a VM with an unrelated
  structural error silently forfeits its claim on every address and MAC it holds.
- **Why it matters:** this is the exact reasoning `schema.py:534-538` gives for the #27
  fix — "the next VM to reuse that address is not reported until the operator has fixed
  [it] and re-run — the round trip `validate` exists to avoid". The trigger fixed by #27
  (an unparseable gateway) is rarer than the ones still live here (a typo'd key, an
  out-of-range `vcpus`). The comment justifying the `continue` — "the checks below index
  into fields the schema just rejected" — is only true when the rejected field is inside
  `nics`; it is asserted for all of them.
- **Evidence:**

  ```
  # vms[0].vcpus = 0, vms[1] reuses vms[0]'s ip_cidr
  error [vms[0].vcpus]: 0 is less than the minimum of 1
  # ...and nothing else. No "already used by".

  # vms[0] carries a typo'd key `cpus`, vms[1] reuses vms[0]'s ip_cidr
  error [vms[0]]: Additional properties are not allowed ('cpus' was unexpected)
  # ...and nothing else.

  # for contrast, the #27 case, which works:
  error [vms[0].nics[0].gateway]: 'not-an-ip' does not appear to be an IPv4 or IPv6 address
  error [vms[1].nics[0].ip_cidr]: address 192.168.122.60 is already used by vms[0].nics[0]
  ```
- **Fix:** narrow the guard to structural problems whose `where` reaches into `nics` (or
  into the keys `_check_firmware` reads), rather than to any structural problem. One
  predicate over `structural`, no new function.
- **Cost of the fix:** a few lines inside `validate`, and one test per suppressed check.
  No new surface. Doing nothing is also defensible — the config is fatal either way — but
  then #27's own rationale does not survive contact with the commoner trigger, and the
  claims ledger should record it as PARTIAL rather than DONE.

### RX-C2 — a bracketed-host URI raises out of `_check_target` (medium)

- **Location:** `orchestrator/backends/libvirt/schema.py:350` (`parts = urlsplit(uri)`)
- **What:** `urlsplit` raises `ValueError` for three classes of netloc. None is caught.
  The exception unwinds through `schema.validate` → `config.validate` → `config.load` and
  is caught only by `cli.main`'s bare `except Exception` (`orchestrator/cli.py:719`).
- **Why it matters:** the operator gets `error: ValueError: Invalid IPv6 URL` — no field,
  no filename, no line — **and every other problem in the config is suppressed**, which is
  precisely what `config.load`'s docstring rules out: "Raises `ConfigError` carrying
  *every* problem rather than the first: an operator editing a config at a site should not
  have to round-trip once per typo." A missing `]` on an IPv6 hypervisor address is an
  ordinary typo, and at an air-gapped site the message points at nothing.
- **Evidence:**

  ```
  $ .venv/bin/python -m orchestrator.cli validate probes/cfg/base.yaml   # also has vcpus: 0
  error: ValueError: Invalid IPv6 URL
  exit=1

  # the three raising classes, Python 3.12.14:
  'qemu+ssh://[2001:db8::1/system'  RAISED Invalid IPv6 URL
  'qemu+ssh://2001:db8::1]/system'  RAISED Invalid IPv6 URL
  'qemu+ssh://h＃x/system'           RAISED netloc 'h＃x' contains invalid characters under NFKC normalization
  ```
- **Fix:** wrap the one `urlsplit` call in `try/except ValueError` and return a single
  `Problem.error` at `target.libvirt.uri` carrying `str(exc)` and the URI. The other two
  `urlsplit` sites (`connection_uri`, `render`) are both downstream of a passing
  `_check_target` and need nothing.
- **Cost of the fix:** four lines in a function that already builds a `problems` list. No
  new surface, no new concept.

### RX-C3 — `image.sha256` verifies the local file, not the deployed bytes (low)

- **Location:** `orchestrator/backends/libvirt/schema.py:286-314`;
  `orchestrator/backends/libvirt/preflight.py:366`
- **What:** `_check_image_digest` hashes `image.source_qcow2` on the machine running the
  deploy. When the pool already holds `image.base_volume_name`, `preflight.base_volume`
  reuses it after comparing **byte length** only — the digest is never carried to the host
  side. A host copy of the same length under the same name is accepted with `sha256` set.
- **Why it matters:** the base volume is shared across deployments and is the thing every
  overlay backs onto. An operator who sets `sha256` reasonably reads it as "the image these
  VMs boot is the one I pinned"; what it actually pins is the file in the bind mount.
  `preflight.py:326` states the size check "catches a *different* image under the same name
  as well", which holds only when the lengths differ.
- **Evidence:** `grep -n sha256 orchestrator/backends/libvirt/preflight.py` → no match.
  The only host-side comparison is `preflight.py:366` `if physical != local:`, against
  `os.stat(source).st_size`.
- **Fix:** none proposed. D30 settled size as the host-side check, and a host-side digest
  means reading a multi-GB volume over SSH inside the connected phase. Recorded so the
  record says what the RW-C1 fix does and does not cover.

### RX-C4 — the unverified-digest warning names the wrong field (nit)

- **Location:** `orchestrator/backends/libvirt/schema.py:296-303`
- **What:** when the image cannot be read, the warning is filed at `where="image.sha256"`.
  `_check_disk_capacity` warns about the same unreadable file at
  `where="image.source_qcow2"` (`schema.py:604-613`), which is the field the operator has
  to act on.
- **Evidence:** one missing image produces two warnings naming two different keys:
  `warning [image.sha256]: cannot read /images/golden.qcow2 to check its sha256 …` and
  `warning [image.source_qcow2]: cannot read /images/golden.qcow2 to check disk_gb …`.
- **Fix:** one string. Cost: nothing.

### RX-C5 — duplicate MAC is filed at the NIC, duplicate IP at the field (nit)

- **Location:** `orchestrator/backends/libvirt/schema.py:558-563`
- **What:** the address collision reports `where=f"{at}.ip_cidr"` (`schema.py:547-554`);
  the MAC collision three lines later reports `where=at`. For a derived MAC there is no
  `mac` key to point at, which is presumably the reason, but the message then reads
  `error [vms[1].nics[0]]` for a problem about one specific field.
- **Fix:** one string, or leave it. Cost: nothing.

---

## Checked and sound

* **`problems_from` (`base.py:70-89`) is correct for every shape reaching it.** Probed
  through `Draft202012Validator`: root error (`$` → `""` → `root`), top-level string key
  (`$.deployment` → `deployment`), top-level array index (`$[0]` → `[0]`), nested object
  (`$.a.b` → `a.b`), a key containing a dot, a key containing brackets, the empty-string
  key, a key *beginning* with a dot, an integer-like string key, and both `at`-prefixed
  forms (`$` → `vms[0]`, `$.nics[0].mac` → `vms[0].nics[0].mac`). The general argument:
  `json_path` emits its own `.` separator before a bare key, so the one dot `removeprefix`
  can remove is always that separator and never part of a key — a key `.hidden` renders
  `$..hidden` and survives as `.hidden`.
* **The two environments render `json_path` differently, and it does not matter.** The dev
  venv has jsonschema 4.26.0, which escapes non-identifier keys as `['a.b']`; the shipped
  image has **4.19.1**, which joins every key with a bare dot (`podman run --entrypoint
  /usr/bin/python3 localhost/vcows-deploy:0.1.0.0 …`). No user-controlled key reaches
  `json_path` in this schema — `additionalProperties: False` reports at the parent — so the
  divergence is inert, and `removeprefix` is right under both.
* **`_blame_the_filename` still fires** (`config.py:167`). End-to-end: a config named
  `9 bad name.yaml` with no `deployment:` key reports
  `error [<path>]: the deployment name defaults to this config's filename …`; a config
  named `good-name.yaml` validates; an explicit `deployment:` stays blamed on the key. It
  is pinned: deleting `.removeprefix(".")` in a `mktemp -d` copy failed
  `test_a_bad_filename_stem_blames_the_file_not_the_key` and
  `test_an_explicit_deployment_is_still_blamed_on_the_key` (409 passed, 2 failed).
  `Path(".yaml").stem` is `".yaml"`, which the pattern rejects and the filename message
  explains — the degenerate filename is handled.
* **RW-C1's fix, both directions:** matching digest clean; uppercase digest clean (the
  schema admits `[0-9a-fA-F]`, and the comparison lowercases); wrong digest an ERROR naming
  both digests; absent `sha256` reads nothing (pinned by a `monkeypatch` that raises);
  missing file and a directory both WARNING, non-fatal. Schema pattern rejects 63, 65,
  leading-space, trailing-newline and non-hex forms.
* **URI checks.** `qemu+ssh://[2001:db8::1]/system` clean and renders
  `qemu+sshcmd://[2001:db8::1]/system`; `QEMU+SSH://` accepted (urlsplit lowercases the
  scheme, and `connection_uri` rewrites it anyway); `/system/` and `/session` rejected;
  query, fragment and password rejected, including the empty password `u:@h`. A raw newline
  in the netloc is deleted by `urlsplit` before any check sees it (`user\npwn` → `userpwn`),
  and `%0A` survives as literal text into a JSON string — neither reaches a line-oriented
  file. The username is deliberately unconstrained (F-SEC-04); not reopened.
* **The port is not checked, and does not need to be.** `_check_target` never touches
  `parts.port`, so `qemu+ssh://host:notaport/system` validates clean. Measured against the
  rig: `libvirt.open` refuses it with `internal error: Unable to parse URI
  qemu+ssh://vcows@vcows:notaport/system` — loud, at preflight, and it names the URI.
  `:0` opens normally. Not filed.
* **`ssh_keyfile` / `known_hosts` (`SSH_PATH_PATTERN`, `schema.py:56`)** unchanged in range;
  the absent-file case is a WARNING at the right field, and the pattern's rejection of
  whitespace is the documented `~/.ssh/config` injection defence, mirrored at
  `container/entrypoint.py:45`.
* **`marker.py`** is untouched in `4eb378b..672a500` (`git diff --stat` lists only
  `base.py`, `schema.py`, `config.py` and `tests/test_libvirt_schema.py`). `derive_id` is
  at `orchestrator/marker.py:161`.
* `tests/test_libvirt_schema.py tests/test_config.py tests/test_marker.py` — 116 passed.

## Not checked

* Whether libvirt or QEMU refuses an operator-supplied `mac:` with the multicast bit set
  (`MAC_PATTERN` accepts `01:…` and `ff:ff:ff:ff:ff:ff`). Settling it needs a domain
  define, which this review may not do on the rig.
* `test_an_unreadable_image_warns_rather_than_failing_the_digest` depends on
  `/images/golden.qcow2` not existing on the host running the suite. It fails loudly rather
  than silently if that is false, so it is not the gate-that-cannot-fail class — dimension
  D's call, not filed here.
* Whether the provider's `create.content.url` handling changes behaviour for a
  `source_qcow2` containing a space or a `//` prefix. Last round left this open; it needs
  an apply, not a schema probe.
