# Phase 3 verify — the six C/D mediums

Verifier: adversarial, default verdict REFUTED. Date: 2026-08-31.
Bench: detached worktree at `origin/master` (`672a500`), own `.venv`, Python 3.12.14,
tofu 1.12.6, provider mirror at `.tools/tofu-mirror`. Every mutation was applied in a
`cp -a` copy under `mktemp -d` and reverted; `git status --porcelain` in the worktree is
empty. The rig was not contacted and nothing was applied.

**Baseline, default gates, unmutated: `411 passed, 25 skipped`, exit 0.**
**Control, `VCOWS_GATES=all` with no rig/image env, unmutated: `411 passed, 25 errors`, exit 1.**

| | finder | verdict |
|---|---|---|
| RX-C1 | medium | reproduced · **low** |
| RX-C2 | medium | reproduced · **low** |
| RX-D1 | medium | reproduced · **low** |
| RX-D2 | medium | reproduced · **low** |
| RX-D3 | medium | reproduced · **medium** |
| RX-D4 | medium | reproduced · **low** |

Every `file:line` below was re-read at `672a500`. All six citations are exact.

---

## RX-C1 — `schema.py:243-249` · CONFIRMED, downgraded to low

### Citation

```
243:    for i, vm in enumerate(cfg["vms"]):
244:        where = f"vms[{i}]"
245:        structural = _check_vm_structure(vm, where)
246:        problems += structural
247:        if structural:
248:            # The checks below index into fields the schema just rejected.
249:            continue
250:        problems += _check_firmware(vm, where)
251:        problems += _check_nics(vm, where, seen_ips, seen_macs, cfg["deployment"])
```

Exact.

### Reproduction

Four configs built from `tests/conftest.CONFIG`, run through the real CLI verb
(`.venv/bin/python -m orchestrator.cli validate <cfg>`). In all four, `vms[1]` reuses
`vms[0]`'s `ip_cidr` (`192.168.122.60/24`). Two `ssh_keyfile` / `known_hosts` warnings and
one `image.source_qcow2` warning appear in every run and are elided here.

**`vms[0].vcpus = 0`** — the duplicate is not reported:

```
error [vms[0].vcpus]: 0 is less than the minimum of 1
exit=1
```

**`vms[0]` carries a typo'd key `cpus: 2`** — the duplicate is not reported:

```
error [vms[0]]: Additional properties are not allowed ('cpus' was unexpected)
exit=1
```

**Control, duplicate alone** — reported:

```
error [vms[1].nics[0].ip_cidr]: address 192.168.122.60 is already used by vms[0].nics[0]
```

**The `#27` case, `gateway: not-an-ip`** — both reported, i.e. the `#27` fix works and its
scope is nic-level only:

```
error [vms[0].nics[0].gateway]: 'not-an-ip' does not appear to be an IPv4 or IPv6 address
error [vms[1].nics[0].ip_cidr]: address 192.168.122.60 is already used by vms[0].nics[0]
```

The finding reproduces verbatim, including the finder's quoted output.

### Reachability and consequence

The question the lens asks is whether the skipped check is re-run later, or whether the
config is accepted with the conflict undetected. **It is neither: the config is never
accepted.**

`_check_vm_structure` (`schema.py:425-427`) is `problems_from(validator.iter_errors(vm))`,
and `problems_from` (`backends/base.py:70-89`) constructs `Problem.error` and nothing else.
`Problem.fatal` is `severity is Severity.ERROR` (`base.py:60-62`). So *every* structural
problem is fatal, `config.load` raises `ConfigError`, and the CLI exits 1 for every verb.
There is no path on which a VM skips `_check_nics` and then deploys: the operator must fix
the structural error and re-run, and the second run reports the duplicate (the control run
above is that second run).

The consequence is therefore exactly one extra edit round trip — no wrong deploy, no
address collision reaching libvirt, no state change. That round trip is a genuine cost, and
it is the one `config.load`'s own docstring rules out:

```
config.py:117-119
    Raises ``ConfigError`` carrying *every* problem rather than the first: an
    operator editing a config at a site should not have to round-trip once per
    typo.
```

and it is `#27`'s own stated rationale (`schema.py:534-538`). The finder is right that the
still-live triggers (`vcpus: 0`, a typo'd key) are commoner than the one `#27` fixed, and
right that the `continue`'s justifying comment is asserted for all structural problems while
being true only for those inside `nics`. But a diagnostic round trip on a config that is
fatal either way is not a medium on a scale where severity turns on reachability and
consequence. **Low.** The claims-ledger consequence the finder names — `#27` is `PARTIAL`,
not `DONE` — stands on its own and is the more useful half of this finding.

---

## RX-C2 — `schema.py:350` · CONFIRMED, downgraded to low

### Citation

```
346: def _check_target(target: dict) -> list[Problem]:
347:     """R-D: the URI is ours to assemble, not the operator's to decorate."""
348:     where = "target.libvirt.uri"
349:     uri = target["uri"]
350:     parts = urlsplit(uri)
```

Exact. `cli.py:719` is the bare `except Exception` (there is a second at `:552`, inside a
verb; `:719` is `main`'s).

### Reproduction

The three raising netloc classes, Python 3.12.14:

```
RAISE 'qemu+ssh://[2001:db8::1/system' -> Invalid IPv6 URL
RAISE 'qemu+ssh://2001:db8::1]/system' -> Invalid IPv6 URL
RAISE 'qemu+ssh://h＃x/system'          -> netloc 'h＃x' contains invalid characters under NFKC normalization
ok   'qemu+ssh://[2001:db8::1]/system'
```

End to end, on a config that also carries `vms[0].vcpus: 0` and two warning-producing
credential paths:

```
$ .venv/bin/python -m orchestrator.cli validate e-ipv6.yaml
error: ValueError: Invalid IPv6 URL
exit=1
```

Every other problem in the document is gone — the `vcpus` error and both warnings that the
same config reports when its URI is well-formed. Reproduced verbatim.

### Reachability and consequence

`config.load` runs for every verb, so `deploy` and `destroy` take the same exit. Measured:

```
$ .venv/bin/python -m orchestrator.cli deploy e-ipv6.yaml --run-dir $D
error: ValueError: Invalid IPv6 URL
exit=1
$ find $D          # empty
```

and the control, a well-formed URI with the same `vcpus: 0`, leaves the run directory empty
too. So the malformed-URI path is **not** a lost-record path: it fails at the same point an
ordinary `ConfigError` fails, before any run directory content, any connection, or any
apply. Nothing is created, nothing is half-done, and the exit code is the same 1.

The whole delta is message quality. Against a well-formed rejection the operator loses: the
`[target.libvirt.uri]` location, the `  ` indent that marks a Problem line, and every other
problem in the document. What they keep is an exit 1 and a string that does name the fault
class (`Invalid IPv6 URL`; the NFKC class even quotes the offending netloc). That is a
loud, non-destructive, moderately diagnosable failure. It violates `config.py:117-119` and
the fix is the four lines the finder describes, but on a scale where medium means a wrong
answer in an active path, this is **low**.

---

## RX-D1 — `tests/test_gates.py:195-198` · CONFIRMED, downgraded to low

### Citation

```
tests/conftest.py
61: def require(name: str, available: bool, reason: str) -> None:
62:     """`gate` for the places a mark cannot reach: a fixture body, a module import."""
63:     if available:
64:         return
65:     if demanded(name):
66:         pytest.fail(reason, pytrace=False)
67:     pytest.skip(reason)

tests/test_gates.py
195: def test_a_demanded_require_that_is_missing_fails(monkeypatch):
196:     monkeypatch.setattr("tests.conftest.GATES", {"tofu"})
197:     with pytest.raises(pytest.fail.Exception, match=REASON):
198:         require("tofu", False, REASON)
```

Both exact.

### Reproduction

Mutation: delete lines 65-66, so `require()` always skips when the dependency is absent.

```
$ .venv/bin/python -m pytest -q
410 passed, 26 skipped in 27.69s
exit=0

$ .venv/bin/python -m pytest -q tests/test_gates.py -rs
SKIPPED [1] tests/conftest.py:65: needs a thing this runner does not have
15 passed, 1 skipped in 0.65s
```

Against the 411/25 baseline the delta is exactly one test moving from passed to skipped —
its own. The `pytest.skip` raised inside `pytest.raises(pytest.fail.Exception)` propagates
and skips the test rather than failing it. Exit 0. **The test cannot fail.** Reproduced;
the finder's 435/1-vs-436/0 under all gates is the same delta measured with the rig and
image gates open.

### Reachability and consequence

The finder writes that `require()` "is the sole guard for the `libvirt` and `pycdlib`
gates". Checked each call site:

* **`tofu`** — `tests/test_tofu_module.py:54` and `:176`, both fixture bodies. Every test
  that consumes those fixtures also carries `@needs_tofu` (`:84`, `:91`, `:103`, `:115`,
  `:141`, `:190`, `:200`), which is `gate()`, and `gate()` is pinned in all three branches
  (`test_gates.py:169-190`, all confirmed by the finder's own mutations). Redundant.
* **`libvirt`** — `tests/test_libvirt_errors.py:20`, `tests/test_libvirt_destroy.py:36`.
  Covered independently: `tests/fake_libvirt.py:25` is a module-scope `import libvirt`, so
  a runner without the RPM gets a **collection error**, not a skip. The gate cannot go
  quiet even with `require()` gutted.
* **`pycdlib`** — `tests/test_seed_iso.py:26`. The genuine sole guard.
  `orchestrator/backends/libvirt/prepare.py:126` imports `pycdlib` inside the function, so
  its absence is silent everywhere else.

So the mutation's whole production reach is: on a runner missing `pycdlib`, with someone
demanding it, the seed-ISO tests skip green instead of failing. Nothing demands it —
`grep -rn VCOWS_GATES` over `justfile`, `scripts/` and both pipelines returns only
`VCOWS_GATES=tofu` (`justfile:90`) and `VCOWS_GATES=image` (`scripts/test-image.sh:16`).
Two further conditions must both hold before this costs anything.

Against `conftest.py:7` the finder is right and the irony is real: the file whose job is to
AST-walk the suite for skips that bypass the mechanism contains a test that turns itself
into a skip. That is worth fixing, in twelve test-only lines. It is not a medium: no
production surface, and the one gate it uniquely guards is demanded by nothing today.
**Low.**

---

## RX-D2 — `tests/conftest.py:37` · CONFIRMED, downgraded to low

### Citation

```
36: #: Gates the operator demanded. Comma-separated names, or `all`.
37: GATES = {g for g in os.environ.get("VCOWS_GATES", "").split(",") if g}
```

Exact. `tests/test_gates.py:130-140` is
`test_gates_is_parsed_without_whitespace_stripping`, which monkeypatches
`tests.conftest.GATES` and never performs the parse it is named after.

### Reproduction

Mutation A, `GATES: set = set()` — the environment variable read and discarded:

```
$ .venv/bin/python -m pytest -q                 411 passed, 25 skipped   exit=0
$ VCOWS_GATES=all .venv/bin/python -m pytest -q 411 passed, 25 skipped   exit=0
```

Control, unmutated, same command:

```
$ VCOWS_GATES=all .venv/bin/python -m pytest -q 411 passed, 25 errors    exit=1
```

Identical to the finder's numbers. `VCOWS_GATES=all` stops meaning anything and the suite
does not notice.

Mutation B, the obvious "helpful" fix — add `.strip()`:

```
$ .venv/bin/python -m pytest -q tests/test_gates.py            16 passed   exit=0
$ VCOWS_GATES="tofu, image" .venv/bin/python -m pytest -q      411 passed, 15 skipped, 10 errors   exit=1
```

The dedicated gate file is fully green while `image` is now demanded — i.e. the mutation
falsifies `CLAUDE.md:55-56` ("It is case-sensitive and does not strip whitespace, so
`VCOWS_GATES=\"tofu, image\"` silently demands only `tofu`") and nothing in the suite says
so. The documented trap can be removed or widened with `test_gates.py` green in both
directions.

### Reachability and consequence

No production surface: `GATES` is test infrastructure. The consequence of the line being
wrong is that `gate()` emits plain `pytest.mark.skip` instead of `gate_missing`, so
`just test-tofu` (`justfile:90`, `VCOWS_GATES=tofu`) would report green on a runner without
`tofu` or without the mirror, having exercised the module not at all. That is precisely
`RW-E1`'s original failure mode returning by a different door, and unlike RX-D1 it silences
all five gate names at once rather than one branch of one helper — this is the stronger of
the two.

It still needs a wrong edit to `conftest.py:37` before it costs anything, and the fix is two
test-only lines (`monkeypatch.setenv` plus a reload, or a `_parse(raw)` helper). Real hole
against `conftest.py:7`, no reachable consequence today. **Low** — and it should be filed
above RX-D1.

---

## RX-D3 — `main.tf:88` · CONFIRMED at medium

### Citation

```
84: resource "libvirt_domain" "vm" {
85:   for_each = var.vms
86:
87:   name        = each.value.domain_name
88:   type        = "kvm"
```

Exact.

### Reproduction

Mutation: `type = "kvm"` → `type = "qemu"`, in the copy's
`orchestrator/backends/libvirt/tofu/main.tf`, run through the repo's own harness
(`tests/test_tofu_module.py`, which copies the module, inits from the mirror against the
committed lock, and runs `tofu test` with `libvirt-module.tftest.hcl` and the golden
tfvars):

```
$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q tests/test_tofu_module.py
10 passed in 20.07s   exit=0

$ VCOWS_GATES=tofu .venv/bin/python -m pytest -q
411 passed, 25 skipped in 28.19s   exit=0
```

`tofu test` green, the module gate green, the whole suite green. Reproduced.

The nearest assertion is `libvirt-module.tftest.hcl:64`, `libvirt_domain.vm[k].os.type ==
"hvm"` — a different attribute, inside an `alltrue` that already reads `vcpu`, `memory`,
`memory_unit`, `os.type_machine` and `os.type_arch` from the same resource. The file
carries 43 `assert` blocks and none reads `libvirt_domain.vm[*].type`.

### Reachability and consequence

**Nothing else in the stack would catch it.** Checked every surface the lens names:

* **The schema.** `type` is not a config field. `orchestrator/config.py` and
  `backends/libvirt/schema.py` have no key for it; the operator cannot set it and the
  validator cannot see it.
* **`render.py`.** `grep -n type render.py` returns one comment line. The tfvars carry no
  domain type; the value is hardcoded in the module.
* **`outputs.tf`.** The output object is `name`, `uuid`, `configured_address`, `disks`.
  Type is not in the inventory contract, so nothing downstream of an apply can see it.
* **`preflight.py` / `destroy.py`.** Both parse `dom.XMLDesc(...)` (`preflight.py:167`,
  `:202`; `destroy.py:370`, `:400`) for the marker, the disks and the name. Neither reads
  `<domain @type>`. There is no capability, accelerator or KVM check anywhere in the
  orchestrator — `grep -in "kvm\|accel\|capabilit\|virtType"` over `orchestrator/` returns
  one docstring line in `orchestrator/__init__.py`.
* **The rig gate.** All fifteen tests in `tests/test_libvirt_rig.py` are preflight-shaped
  (marker discovery, pool open, volume walk, address and MAC conflicts). None defines or
  inspects a created domain.
* **Repo-wide.** `grep -rn kvm` over `orchestrator`, `tests`, `scripts`, `container`,
  `justfile` returns exactly two hits: `main.tf:88` itself, and a hardcoded
  `<domain type='kvm'>` string inside a destroy-test XML fixture
  (`tests/test_libvirt_destroy.py:94`) that is parsed for its marker.

So the finder's consequence claim holds in full. `<domain type='qemu'>` is TCG: every VM
defines, boots, runs cloud-init, and the deploy reports success — running unaccelerated,
with nothing anywhere reporting it. That is the S1 shape the brief calibrates on, and it is
the only one of these six findings whose consequence is silent-wrong rather than
loud-or-cosmetic.

One measurement narrows the trigger, and it is the reason this is medium and not high. The
provider schema makes the attribute **required**:

```
$ tofu providers schema -json | jq '...libvirt_domain.block.attributes.type'
{ "type": "string",
  "description": "Sets the hypervisor type used to run the domain (for example \"kvm\",
                  \"qemu\", or \"xen\"); this is required and must be a valid libvirt
                  domain driver name for the host.",
  "required": true }
```

(dmacvicar/libvirt 0.9.8, from the pinned mirror.) So the line cannot be silently dropped
by a refactor — deletion is a loud `tofu validate` failure. Reaching the defect needs a
deliberate edit of a one-line hardcoded constant that appears once in the repo, which is
unlikely. But the *hole* is 100% reproducible, the value is unasserted where 43 siblings
are asserted, the failure mode if it ever moves is undetectable at every later stage, and
the fix is one clause appended to the `alltrue` already at `.tftest.hcl:58-66`. Under
dimension D's stated bar — "mutate each new gate and observe it fail" — this is the one
survivor whose consequence justifies the assertion. **CONFIRMED, medium.**

---

## RX-D4 — `tests/test_marker.py:113-115` · CONFIRMED, downgraded to low

### Citation

```
tests/test_marker.py
113: def test_xml_payload_needs_no_escaping():
114:     """If the JSON ever needed XML-escaping, the byte-identical round trip that
115:     A2 verified would stop holding."""
116:     payload = Marker.for_vm("app01", "lab-a").to_json()
117:     assert not (set("<>&") & set(payload))

tests/test_properties.py
56: def test_marker_survives_a_json_round_trip(name, deployment):
57:     """The marker is the identity: a VM that cannot be read back is a VM that
58:     cannot be destroyed. This generalises the hand-written XML-escaping case."""
59:     marker = Marker.for_vm(name, deployment)
60:     assert Marker.from_json(marker.to_json()) == marker
```

Both exact. `NAME_PATTERN` is `orchestrator/backends/libvirt/schema.py:45`;
`DEPLOYMENT_PATTERN` is the identical string at `orchestrator/config.py:39`.

### Reproduction

Mutation: `NAME_PATTERN` widened to `r"^[A-Za-z0-9][A-Za-z0-9._<>&-]{0,62}\Z"`.

```
$ .venv/bin/python -m pytest -q
411 passed, 25 skipped in 27.12s   exit=0
```

while, in the same tree:

```
>>> Marker.for_vm('a<b&c','lab-a').to_xml()
<vcows xmlns="urn:vcows:1">{"v":"0.1.0.0","deployment":"lab-a","name":"a<b&c","id":"9aa37e60-..."}</vcows>
>>> ET.fromstring(...)
xml.etree.ElementTree.ParseError: not well-formed (invalid token): line 1, column 74
```

Reproduced exactly, including the finder's column number. `test_xml_payload_needs_no_escaping`
tests one hardcoded pair, `("app01","lab-a")`, whose JSON contains no special character
whatever `to_json` does — an input for which the assertion cannot fail. The property test
that claims in its docstring to generalise it never calls `to_xml`.

### Reachability and consequence

The path is real. `Marker.to_xml` (`marker.py:137-140`) is bare f-string concatenation with
no escaping; `render.py:85` puts its output in the tfvars as `marker_xml`; `main.tf:103` is
`metadata = { xml = each.value.marker_xml }`, unescaped. So a widened pattern does reach the
domain XML.

What it produces there is where the finder overstates. The finding offers two outcomes,
"a define libvirt refuses or a sibling element injected into `<metadata>`". The injection
half needs `/` — a self-closing or closing sibling tag cannot be written without it — and
`/` is pinned independently, by the guard `#16` added:

```
tests/test_properties.py:87-88
    assert re.match(NAME_PATTERN, "a/b") is None
    assert re.match(DEPLOYMENT_PATTERN, "a/b") is None
```

which the finder's own D-table confirms fails when either pattern is widened to admit `/`.
Without `/`, widening to `<` or `&` yields malformed XML that the provider or libvirt
rejects at define time — loud, at deploy, before any VM exists. Widening to `>` alone yields
well-formed XML that round-trips correctly and does nothing at all.

So: the gate hole is real and reproduces, and the two-character fix to the `#16` guard (or
the one-line `ET.fromstring(m.to_xml()).text == m.to_json()`) is worth taking. But the
worst reachable consequence of the hole being exercised is a loud failure, not the silent
undestroyable-VM shape that would carry a medium. **Low.**

---

## Method notes

* All six `file:line` citations were re-read at `672a500` and are exact. No drift.
* Mutations were applied only in `mktemp -d` copies (`cp -a`, own `.venv`, verified
  importing `orchestrator` from the copy and not from the worktree). The copies were
  deleted; `git status --porcelain` in the pinned worktree is empty.
* `qemu+ssh://vcows@vcows/system` was not contacted. No `tofu apply`. No
  `scripts/image-scan.sh --write-baseline`.
* Rig- and image-gated runs were not reproduced (the rig is out of scope for this
  verifier), so every suite count above is at default gates: baseline `411 passed,
  25 skipped`. The finder's all-gates counts (436/0 baseline) differ only by those 25.
