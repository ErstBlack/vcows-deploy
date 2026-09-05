# Core seam — review

Agent: 01-core-seam · Scope: `orchestrator/__init__.py`, `marker.py`, `qcow2.py`,
`config.py`, `backends/__init__.py`, `backends/base.py` · Date: 2026-08-29

## Summary

- `decide()`'s rules are right for every world it can *see*. Two of its lookups are
  lossy before the rules run: `by_logical` collapses duplicate logical names, and the
  name-clash check indexes only unmarked VMs.
- No path returns SKIP for something not ours. It does return CREATE for a name that
  already exists, via a marked renamed domain (F-CORE-02).
- `VCOWS_NS` is genuinely `uuid5(NAMESPACE_DNS, "vcows-deploy")`; the pin is real,
  recomputed. `qcow2.virtual_size` survived every malformed input I constructed.
- `config.load` discards every WARNING, and only the `validate` verb recovers them.

## Findings

### F-CORE-01 — `decide()` silently drops all but one VM per logical name; which one survives depends on enumeration order

- **Severity:** S3 · **Confidence:** high · **Location:** `backends/base.py:173`
- **What:** `by_logical = {e.marker.name: e for e in existing if e.marker is not
  None}` is a dict comprehension over a list. Two marked domains carrying the same
  `marker.name` collapse to the last one; the earlier is invisible to every rule
  below. The warning loop at :224 does not catch it either — it skips anything whose
  `marker.name` is in `wanted_set`, which both duplicates are. One of the two
  appears in no decision and no problem.
- **Why it matters here:** a renamed domain is the supported case, so two deployments
  each with a logical `app01` — one renamed on the host to free the domain name —
  produces exactly this. The same VM set then decides differently depending on the
  order libvirt's `listAllDomains` returns, and REFUSE names a deployment the
  operator does not own while their own `app01` sits right there.
- **Evidence:** two `Existing` records (`app01`/lab-a, `app01-copy`/lab-b, both with
  `marker.name == 'app01'`) through `decide(['app01'], order, 'lab-a')`. Order
  `[ours, theirs]` → `refuse | ... belongs to deployment 'lab-b'`; order
  `[theirs, ours]` → `skip | exists as 'app01' (not compared)`. Zero warnings either
  way. `tests/test_policy.py` has no duplicate-logical-name case.
- **Fix / cost:** build `by_logical` with an explicit loop collecting a list per name
  and emit an ERROR `Problem` when any name has more than one holder — ambiguous
  ownership must not be resolved by luck. ~8 lines and one test; no new module or
  concept, the same shape as the refusals already there.

### F-CORE-02 — the name-clash refusal covers only unmarked VMs, so a marked renamed domain yields CREATE onto a name that exists

- **Severity:** S3 · **Confidence:** high · **Location:** `backends/base.py:176`, `:208`
- **What:** `unmarked_by_name` is built from `e.marker is None`. A marked domain
  whose *hypervisor* name equals a wanted name but whose *logical* name differs is in
  neither lookup, so `decide()` falls through to CREATE. The docstring says the check
  buys "a clear message instead of a raw libvirt error"; that holds only for unmarked
  domains.
- **Why it matters here:** `render.domain_name` returns the name undecorated (D16), so
  the domain name collides and the refusal happens inside `tofu apply` at define time
  — *after* that VM's overlay volume and seed ISO are written, landing the operator in
  findings.md §2's orphan-volume path. WARNINGs are not fatal in `cmd_deploy`
  (`cli.py:183`), so nothing stops the run.
- **Evidence:** `decide(['app01'], [Existing(name='app01', id='i1',
  marker=Marker.for_vm('web01','lab-b'))], 'lab-a')` returns `create | does not
  exist`, plus one warning naming `web01` that never says `app01` is taken.
- **Fix / cost:** build the clash lookup from *every* `Existing.name`, not only
  unmarked ones, and consult it after the `by_logical` miss. One comprehension loses
  its `if`, plus a message branch. It removes surface rather than adding it.

### F-CORE-03 — `load()` discards every WARNING, and only the `validate` verb recovers them

- **Severity:** S3 · **Confidence:** high · **Location:** `orchestrator/config.py:132`
- **What:** `load` calls `validate`, raises if anything is fatal, and drops the rest.
  `cmd_validate` re-runs `validate` to recover them (`cli.py:135`, with a comment
  acknowledging the discard). `cmd_preflight`, `cmd_deploy` and `cmd_destroy` do not.
- **Why it matters here:** the only config-level WARNING in the tree is
  `_check_disk_capacity`'s unreadable-image case (`libvirt/schema.py:409`), whose text
  predicts a later failure: "a VM whose disk_gb is below the image's virtual size will
  fail at create time". When the golden image is unreadable — a bind mount left off
  the `podman run`, a typo — the disk-capacity ERROR check is skipped entirely and
  `vcows deploy` says nothing at all.
- **Evidence:** with the stock `source_qcow2: /images/golden.qcow2` and no such file,
  `load('lab.yaml', REGISTRY)` returns silently while `validate(cfg, REGISTRY)` on the
  identical dict yields `warning [image.source_qcow2]: cannot read
  /images/golden.qcow2 to check disk_gb against it (No such file or directory)`.
- **Fix / cost:** have `load` return the non-fatal problems alongside the config, or
  print them itself; `cmd_validate`'s second `validate` call then goes away. A
  return-type change at four `cli.py` call sites, justified because the discarded
  warning is the pre-notice for a failure that otherwise appears mid-apply.

### F-CORE-04 — `Marker.from_json` coerces wrong-typed fields with `str()` instead of raising, defeating D12's safe direction

- **Severity:** S3 · **Confidence:** medium · **Location:** `orchestrator/marker.py:125`
- **What:** after the key check, every field goes through `str()`, so a JSON object
  with the right keys and wrong types produces a `Marker`, not a `MarkerError`.
  `"deployment": null` becomes the string `"None"`, not `""`, contradicting the
  comment two lines above ("Empty string, never None").
- **Why it matters here:** `preflight.marker_of` (`preflight.py:83`) catches
  `MarkerError` and returns `None` because "Unparseable is unmarked (D12) ... caught
  by the name-collision refusal instead, which is the safe direction." Coercion routes
  around that: a corrupted marker becomes a *valid* marker whose name matches nothing,
  so `decide()` returns CREATE and the run fails at define time — the unsafe direction
  D12 chose against, compounded by F-CORE-02 since the garbage name is not in
  `unmarked_by_name` either.
- **Evidence:** `Marker.from_json('{"v":1,"name":null,"id":[1,2],"deployment":{"a":1}}')`
  returns `Marker(name='None', deployment="{'a': 1}", id='[1, 2]', v='1')`, no
  exception. `str()` cannot fail, so nothing past the key check reaches `MarkerError`.
- **Fix / cost:** replace `str(data[k])` with an `isinstance(..., str)` test raising
  `MarkerError` naming the field and its type. Four lines and a helper; unknown-key
  tolerance is separate and stays. It narrows what is accepted.

### F-CORE-05 — `Marker.deployment`'s docstring says destroy is host-wide and nothing reads the field

- **Severity:** S5 · **Confidence:** high · **Location:** `orchestrator/marker.py:80-84`
- **What / why:** "v0.1 destroy scope stays host-wide, so nothing reads this for a
  destroy decision yet -- but a later release can filter on it". This is the docstring
  on the field that decides what gets deleted, and it states the opposite of what
  destroy does; findings.md:119 calls host-wide scope a data-loss event.
- **Evidence:** `cli.py:268` — `if e.marker is not None and e.marker.deployment == deployment`; findings.md:85 — "Destroy is now scoped by it".
- **Fix / cost:** rewrite that paragraph. Three lines of prose.

### F-CORE-06 — `v` is documented as the format discriminator; nothing reads it

- **Severity:** S5 · **Confidence:** high · **Location:** `orchestrator/marker.py:90`
- **What / why:** "vcows version that created it. Also the format discriminator." No
  read of `Marker.v` exists outside `to_json` and the tests, so a marker from a
  hypothetical 0.9.0.0 is trusted, skipped and destroyed identically to a 0.1 one. The
  real discriminator is `MARKER_XMLNS` (`marker.py:52` says so, and the rig fixture
  `vcows-spike-probe01` proves it). Two mechanisms documented, one exists.
- **Fix / cost:** drop the clause and point at `MARKER_XMLNS`. One clause. Do not add
  a version gate; the namespace already carries it.

### F-CORE-07 — the `deployment` default comes from the filename stem and is then pattern-checked, blaming a key the operator never wrote

- **Severity:** S6 · **Confidence:** high · **Location:** `orchestrator/config.py:130`
- **What / why:** `raw.setdefault("deployment", path.stem)` runs before `validate`, so
  a stem failing `DEPLOYMENT_PATTERN` errors at `deployment` — the operator sees an
  error about a key not in their file and cannot grep for it. Spaces and a leading dot
  both do it: `load('prod config.yaml', REGISTRY)` → `error [deployment]: 'prod
  config' does not match '^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$'`.
- **Fix / cost:** when the key was defaulted, raise with `where=str(path)` naming the
  filename as the source. A boolean and one branch.

### F-CORE-08 — `Discovered` and `Prepared` are `frozen=True` with mutable list and dict fields

- **Severity:** S6 · **Confidence:** high · **Location:** `backends/base.py:94`,
  `:97`, `:100`, `:118`
- **What / why:** `frozen=True` blocks attribute rebinding only — `d.vms.append(...)`,
  `d.problems.append(...)` and `d.artifacts['k']=...` all succeed, and
  `hash(Discovered(vms=[...]))` raises `TypeError: unhashable type: 'list'` because
  `frozen=True` generated `__hash__` over fields that are not. `Existing.disks` is
  correctly a `tuple`; the records crossing the seam are not. Nothing mutates them
  today, so this is latent — but `Discovered` is documented as "the only thing that
  crosses from the connected half of the pipeline into the pure half".
- **Fix / cost:** `tuple[Existing, ...]` and `tuple[Problem, ...]`, matching
  `Existing.disks`; `artifacts` is genuinely opaque, leave it. Two annotations;
  `preflight` builds both as local lists and would pass `tuple(...)`.

## Checked and sound

- **`VCOWS_NS` pin is real.** `uuid5(NAMESPACE_DNS, "vcows-deploy")` recomputes to
  `43a00ff6-89be-57a1-8596-246f665e9f4b`. `uuid5` is SHA-1 over namespace bytes plus
  `name.encode("utf-8")`, unchanged across CPython 3.x, so derivation is stable across
  the RHEL 9 / RHEL 10 / Rocky interpreter spread.
- **`qcow2.virtual_size`** on: 31-byte truncation, empty file, bad magic, qcow v1, v4,
  a v3 header with 104 bytes of extra header data, `size = 2**64-1`, a directory, a
  missing path. Each returns the right integer or raises `NotAQcow2`/`OSError`, both
  of which `_check_disk_capacity` catches and reports with `strerror`. The shared
  v2/v3 prefix assumption at offset 24 holds. The only misbehaving input is a FIFO,
  where `open()` blocks with no timeout — unreachable via a read-only bind mount.
- **Schema composition with two backends,** against a synthetic registry: correct
  pairing passes; `backend: vsphere` with `target: {libvirt: …}` fails the `if/then`;
  both targets fail `maxProperties`; empty target fails both `minProperties` and the
  `then`. The `required: ["backend"]` guard inside each `if` is what makes an absent
  `backend` fall to the top-level required check rather than silently passing.
- **`decide()` never returns SKIP for something not ours.** SKIP needs a parsed marker
  and exact `marker.deployment == deployment` equality. An absent `deployment` (`""`)
  refuses as `'<unset>'`, and `DEPLOYMENT_PATTERN` makes `""` unreachable as a config
  value, so a legacy marker cannot be adopted by accident.
- **Marker round trip.** Key-ordered compact JSON; unknown keys tolerated as
  documented; missing `v`/`name`/`id` lists *all* missing keys; a non-object payload
  raises with its type named.
- **`core_schema` with an empty registry** does not crash — `validate` returns problems
  and never reaches `registry[cfg["backend"]]`. The composed document is invalid
  against the 2020-12 metaschema (`"enum": []`, `"allOf": []`), but
  `Draft202012Validator` does not check its schema and the errors are sane.
- **`validate`'s early return** after schema errors, and the duplicate-VM-name check,
  behave as documented. VM `name` typing is enforced by the libvirt backend's own VM
  schema, so core's untyped `required: ["name"]` has no live hole.

## Not checked

- `tofu.py` and `cli.py` beyond the four call sites consuming `load`, `decide` and
  `Marker.deployment`; the libvirt backend's own checks, except where they consume
  `qcow2` or `Problem`. Whether `preflight` can emit two `Existing` records for one
  domain — F-CORE-01's trigger is two *domains*, which is what I tested.

## Deserves its own agent

- **Volume-name collisions between deployments.** `render.overlay_name` and
  `seed_name` are `f"{vm_name}.qcow2"` and `f"{vm_name}-seed.iso"`, undecorated like
  the domain name, so two deployments sharing a host and a logical VM name collide on
  both volumes in one flat pool. Whoever holds `preflight.py` should check whether §2's
  orphan-volume refusal fires before the apply writes.
- **WARNING severity across the verbs.** Deploy treats a target-side WARNING as
  non-fatal, destroy prints and proceeds. Every `Severity.WARNING` site deserves an
  audit against what each verb does with it.
