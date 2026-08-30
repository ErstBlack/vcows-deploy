# Dimension C — validation and schema tightening

Scope: `orchestrator/backends/libvirt/schema.py`, `orchestrator/config.py`,
`orchestrator/marker.py` and their tests, over `da3f45c..HEAD`. Every new or changed
pattern, bound and semantic check was exercised in both directions against the
canonical config in `tests/conftest.py`.

Headline: **the S5/S6 tightening is sound.** Every new rule I could construct an input
for fires exactly where it claims to and nowhere else. The `$` → `\Z` sweep is complete.
The three findings below are one inert schema field and two property tests that do not
constrain what their docstrings say they do.

---

## 1. What I traced, and what it does

### 1.1 The `$` → `\Z` sweep is complete

Every `pattern` in the tree, and the one regex outside it:

| Pattern | File:line | End anchor |
| --- | --- | --- |
| `NAME_PATTERN` | `schema.py:44` | `\Z` |
| `MAC_PATTERN` | `schema.py:46` | `\Z` |
| `SSH_PATH_PATTERN` | `schema.py:55` | `\Z` |
| `DEPLOYMENT_PATTERN` | `config.py:39` | `\Z` |
| `image.sha256` | `config.py:57` | `\Z` |
| `image.source_qcow2` | `config.py:56` | `^/` only — intentional, a path has no fixed tail |
| `container/entrypoint.py:45` `SSH_PATH` | duplicate of `SSH_PATH_PATTERN` | `\Z` |
| `container/manifest.py:33` `SHA_PATTERN` | git describe output | `\Z` |

Checked through `jsonschema.Draft202012Validator` (which uses `re.search`, so the
leading `^` matters and is present everywhere):

```
NAME   trailing-newline: False | plain: True
MAC    trailing-newline: False
SSH    trailing-newline: False
DEPLOY trailing-newline: False | plain: True
sha256 trailing-newline: False
```

`image.source_qcow2` has no end anchor by design, so `"/img.qcow2\nX"` is accepted. That
string's only sinks are a JSON-escaped tfvars value and `qcow2.virtual_size(source)` —
no line-oriented file, no argv — so the newline is inert there. Not a finding.

`^/` does reject the three cases the docstring names (`images/x.qcow2`,
`http://host/x.qcow2`, `./x.qcow2`) — confirmed, and `tests/test_config.py` parametrises
exactly those. It accepts `//evil/img.qcow2`, which a URL parser would read as
protocol-relative; the provider's `create.content.url` handling would fall through to the
local-file branch on an empty scheme, so I could not turn this into a network fetch and
am not filing it.

### 1.2 `SSH_PATH_PATTERN` — direction 2

`^/[^\s]*\Z` forbids paths containing spaces or tabs. **This is deliberate and
documented** (`schema.py:48-54`), it is mirrored in `container/entrypoint.py:45` with a
comment explaining the duplication, and `tests/test_libvirt_schema.py` parametrises both
the rejections and an accepted ordinary path. An operator whose key really does live
under a path with a space is broken by this, but the alternative is `ProxyCommand`
injection into `~/.ssh/config`, and the error message names the constraint. Correct
trade, correctly documented.

One residue: `[^\s]` does not exclude `\x00`, so `/run/k\x00evil` validates. `Path(...).
is_file()` swallows the `ValueError` and warns; the byte then reaches `IdentityFile` in
`~/.ssh/config`. It cannot start a new directive, so it degrades to a key path that will
not open. Below the bar.

Nothing pins the two copies of the pattern together — no test asserts
`entrypoint.SSH_PATH.pattern == schema.SSH_PATH_PATTERN`. Drift is loud in both
directions (either validate refuses and the entrypoint would have accepted, or the
entrypoint refuses before `vcows` is a process), so I am not filing it.

### 1.3 `ip_cidr` network / broadcast rejection and the /31, /32 exemption

`schema.py:482-498`. Measured:

| `ip_cidr` | result |
| --- | --- |
| `192.168.122.0/24` | ERROR "is the network address" |
| `192.168.122.255/24` | ERROR "is the broadcast address" |
| `10.0.0.0/30` | ERROR "is the network address" |
| `10.0.0.0/31` | clean |
| `10.0.0.5/32` | clean |
| `2001:db8::/64` | ERROR "is the network address" |
| `2001:db8::ffff:ffff:ffff:ffff/64` | ERROR "is the broadcast address" |

`num_addresses > 2` is the right single condition for both families — it exempts /31 and
/32 and, for free, /127 and /128. The IPv6 all-ones case is reported as "the broadcast
address", which IPv6 does not have; RFC 2526 reserves the top interface identifiers as
anycast, so refusing it is defensible and only the wording is loose. Cosmetic, not filed.

### 1.4 Size ceilings

`MAX_VCPUS=512`, `MAX_MEMORY_MIB=4194304` (4 TiB), `MAX_DISK_GB=65536` (64 TB),
`schema.py:95-97`. All three reject `max+1` with the jsonschema message naming the field,
and `_ceiling` (`schema.py:66-88`) reports and ignores a non-integer or non-positive
override rather than taking it. `int()` accepts `" 512 "` and `"1_000"`; neither is a
problem. The constants are read at import, and `test_the_ceilings_are_raisable_from_the_
environment` correctly reloads the module to test the override.

None of the three bounds can plausibly reject a real host: the smallest, 512 vCPUs, is
above the largest x86 socket count, and each is raisable from the environment.

### 1.5 `loader` requires `loader_format`

`schema.py:430-443`. Both directions now hold, and they are separate checks:

* `loader` without `loader_format` → ERROR at `vms[1].loader` (new).
* `loader_format` without `loader` → ERROR at `vms[1].loader_format` (pre-existing).
* `loader` + `nvram_template` + `loader_format` → clean.
* `firmware: bios` with any of the three → ERROR per key, and the function returns
  before the UEFI checks, so there is no double report.

The one gap is that `bios` returns early at `schema.py:407`, so a `bios` VM never reaches
the `loader`/`loader_format` pairing check — which is correct, since all three keys are
already refused outright for `bios`.

### 1.6 `base_volume_name` vs derived volume names

`_check_volume_names`, `schema.py:257-284`. `base_volume_name: "app01.qcow2"` and
`"app02-seed.iso"` both produce an ERROR at `image.base_volume_name` naming the VM and
the kind. This runs outside the per-VM loop, so it still fires when a VM failed structural
validation; `vm["name"]` is guaranteed present by the core schema and is only
f-string-interpolated, so a non-string name cannot crash it.

`overlay_name` is `f"{n}.qcow2"` and `seed_name` is `f"{n}-seed.iso"`, so overlay and seed
names cannot collide with each other across VMs given distinct VM names, and
`config.validate:200-207` already refuses duplicate VM names. The check is complete for
one deployment in one pool. Collisions between *two deployments* sharing a pool are the
subject of the previous review's dimension 15 and are out of scope here.

### 1.7 `Marker.from_json` — `isinstance` instead of `str()`

`marker.py:143-158`. `{"name": 123}` and `{"id": null}` now raise `MarkerError` where they
used to yield `"123"` and `"None"`. The direction-2 risk with a tightening like this is an
uncaught exception on a path that previously degraded — it is handled:
`preflight.marker_of` (`preflight.py:95-96`) catches `MarkerError` and returns `None`, and
D12's "unparseable is unmarked" then routes the domain to the name-collision refusal.
Safe direction, and the required-key check at `marker.py:117-119` runs before `_text`, so
the `default is None` branch can never hit a `KeyError`.

### 1.8 URI password refusal

`schema.py:334-348`. `qemu+ssh://u:pw@h/system` and `qemu+ssh://u:@h/system` both ERROR
(the second because `urlsplit` gives `password == ""`, not `None`). The netloc still
travels verbatim, and the *username* is still unchecked — the previous review considered
this at 18-security-adversary F-SEC-04 and 03-libvirt-offline F-LVOFF, and concluded "the
username stays; it is required". I looked for a way to turn a username into an `ssh`
option (a leading `-o…`), but the go-libvirt `sshcmd` dialer's argv construction is not in
this tree and I could not confirm it, so I am not filing against a decision the previous
review made deliberately. Flagged in coverage.

### 1.9 `config.load` returning warnings, and `_blame_the_filename`

`config.py:114-176`. The rewrite is correctly scoped: it fires only when `deployment` was
defaulted from the filename stem, matches on `problem.where == "deployment"` (which is
exactly what `config.validate:187` produces for that key), and leaves an operator-written
`deployment:` blamed on the key. Both directions are tested.

---

## 2. Findings

### RW-C1 — `image.sha256` is validated and then never used (medium)

`orchestrator/config.py:57` declares:

```python
"sha256": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}\Z"},
```

`IMAGE_SCHEMA` has `additionalProperties: False`, so the key has to be declared to be
accepted — it is a deliberate part of the config contract. `docs/orchestrator-architecture.md:179`
shows it in the canonical config block. S5 tightened its anchor to `\Z` and
`tests/test_config.py` added two tests for that anchor, both of which reinforce the
impression that the value is enforced.

Nothing reads it. The whole non-doc, non-test tree contains exactly one occurrence:

```
$ grep -rn sha256 --include=*.py --include=*.tf --include=*.sh --include=*.yml .
container/manifest.py:100:  "artifact_sha256": os.environ.get("PROVIDER_SHA256", "unknown")   # provider zip, unrelated
orchestrator/config.py:57:  "sha256": {...}
scripts/*.sh                                                                     # tool downloads, unrelated
```

`_check_disk_capacity` opens `image.source_qcow2` and reads its qcow2 header
(`schema.py:569-583`) — the one place a digest check would naturally live — and does not
compute or compare a digest. There is no warning saying the value is inert.

Consequence: an operator at an air-gapped site who pins the golden image with `sha256:`
gets a syntax check on their hex string and no integrity check on the image. A corrupted
or substituted `/images/golden.qcow2` deploys with no signal. This is the one field in the
config whose whole purpose is to catch that.

Severity medium rather than high: no vcows computation produces a wrong answer, and
nothing else in the pipeline depends on the value. The harm is false assurance from a
documented, schema-enforced, recently-tightened field that does nothing.

Two acceptable fixes, both small: verify the digest in `_check_disk_capacity` (it already
has the file open and already degrades to a WARNING when the image is unreadable), or
remove the field and let `additionalProperties: False` say so.

### RW-C2 — `test_derived_ids_separate_deployments` asserts a property that is false (low)

`tests/test_properties.py:66-71`:

```python
first, second = Marker.for_vm(*a), Marker.for_vm(*b)
assert (first.id == second.id) == (a == b)
```

`derive_id` is `uuid5(VCOWS_NS, f"{deployment}/{name}")` (`marker.py:168`) with an
unescaped `/` delimiter, and both strategies are `st.text`, which generates `/`. So the
asserted biconditional is false:

```
>>> a = Marker.for_vm("b/c", "a")      # deployment "a",   name "b/c" -> "a/b/c"
>>> b = Marker.for_vm("c",   "a/b")    # deployment "a/b", name "c"   -> "a/b/c"
>>> a.id == b.id, a == b
(True, False)
```

Hypothesis draws `a` and `b` independently, so it will never construct a matching pair;
the test passes on every run. It reads as a proof that the S3 deployment/name separation
is injective, and it is not one. The collision is unreachable through a config —
`NAME_PATTERN` and `DEPLOYMENT_PATTERN` both forbid `/` — but the constraint holding it
closed is those patterns, not the derivation, and this test is what claims otherwise.

Fix: either restrict the strategies to the character class the patterns allow (which makes
the assertion true and the test honest about what it covers), or keep `st.text` and assert
the weaker true property, `id == id ⟹ f"{d}/{n}"` equal.

### RW-C3 — the CIDR property strategy generates two prefix lengths (low)

`tests/test_properties.py:74-87`. The docstring is "Anything the stdlib will render, the
parser will read back identically." The strategy:

```python
st.ip_addresses(v=4).map(lambda a: ipaddress.IPv4Network(a).supernet(24))
st.ip_addresses(v=6).map(lambda a: ipaddress.IPv6Network(a).supernet(8))
```

`IPv4Network(addr)` is a /32 and `.supernet(24)` is therefore always a /8; `IPv6Network
(addr)` is a /128 and `.supernet(8)` is always a /120. Measured over 300 draws:

```
prefix lengths generated: [8, 120]
```

Two of 33 + 129 possible prefix lengths. The test then feeds
`f"{network.network_address}/{network.prefixlen}"` and asserts
`parsed.network.prefixlen == network.prefixlen`, which for a network address is trivially
the input. So the one function in this module that "does real CIDR arithmetic across two
address families" is quantified over two prefix lengths and zero host addresses — and
every input it generates is now an address the new `_check_nics` rule refuses.

Fix: draw the prefix length (`st.integers(0, 32)` / `st.integers(0, 128)`) and draw a host
address inside the block, then assert the round trip. That is the shape that would exercise
the `num_addresses > 2` boundary the S5 work added.

---

## 3. Considered and not filed

* **`to_xml` does not XML-escape its payload.** `Marker(name="a<b&c").to_xml()` is not
  well-formed XML; only `NAME_PATTERN`/`DEPLOYMENT_PATTERN` hold it closed. The previous
  review already recorded this (18-security-adversary, "Marker XML injection is closed…
  the only thing holding it closed"). Worth noting that `test_properties.py:11-14` claims
  the JSON round-trip property "generalises the hand-written XML-escaping case" while never
  calling `to_xml` — but that is a docstring overclaim on an already-filed observation.
* **`seen_ips` registration is nested under `gateway is not None`** (`schema.py:500-517`),
  so a NIC with an unparseable gateway does not register its address for the duplicate
  check. Pre-existing, and the gateway error is fatal anyway, so nothing is silently
  accepted.
* **`base_volume_name` and `pool` are only `minLength: 1`** while VM names, MACs and
  credential paths are now pattern-constrained. Both reach libvirt as object names. Not
  changed by this diff and libvirt rejects the interesting cases itself.
* **`_ceiling` prints its refusal at import time**, before argparse, so a bad
  `VCOWS_MAX_*` produces a line ahead of `--help`. Cosmetic.
* **URI username is unchecked.** See §1.8 — a decision the previous review made explicitly.
