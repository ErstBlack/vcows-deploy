# Comment and docstring accuracy — review

Agent: 09-comment-accuracy · Scope: `orchestrator/`, `container/`, `tests/`, `backends/libvirt/tofu/*.tf`, `Containerfile` · Date: 2026-08-29

## Summary

- Unusually accurate for the density: everything the acceptance run *edited* was
  updated with it (`prepare.py`, `main.tf`, `connection_uri`, `entrypoint.py`).
- What went stale is one layer out, in modules the fixes did not touch: `marker.py`
  carries the premise `findings.md` §3 retracted, three comments still describe SSH
  credentials travelling in the URI, two await a Stage 5 built differently.
- Two promise a guarantee nothing enforces: version coherence "asserted" by a test
  that does not assert it, and a serial console the acceptance run found useless for
  the case it is justified with.
- Counts: **S5 × 6, S6 × 7.** Nothing S1–S4 in this lens.

## Findings

### F-CMT-01 — `marker.py` states destroy is host-wide; it has been deployment-scoped since Stage 4
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/marker.py:63-68` (`Marker.deployment` docstring)
- **What:** "v0.1 destroy scope stays host-wide, so nothing reads this for a destroy
  decision yet -- but a later release can filter on it." Both halves are false:
  `decide()` compares `marker.deployment` on every create decision (`base.py:170`)
  and `cmd_destroy` filters targets on it (`cli.py:279-283`).
- **Why it matters here:** the highest-cost false comment in the tree. It sits in
  the module that defines ownership and tells a maintainer `destroy` tears down every
  vcows VM on the host. "Fixing" the filter as an unintended restriction makes a
  second deployment sharing a hypervisor a data-loss event.
- **Evidence:** `findings.md:119` — "An earlier revision of this section accepted
  host-wide scope for v0.1 ... Destroy is now scoped by it." `git log -- marker.py`
  → `55cbfee` only; `git log -S'deployment == deployment' -- cli.py` → `c989a89`.
- **Fix:** say what is true — recorded from 0.1.0.0 (D4), read by `decide()` on create
  and by `cmd_destroy` to scope teardown (D36). **Cost:** three lines of prose.

### F-CMT-02 — three comments still describe SSH credentials travelling in the URI
- **Severity:** S5 · **Confidence:** high
- **Location:** `tofu/variables.tf:8`; `schema.py:214-224` (comment **and** message)
- **What:** `var.uri` is documented as "qemu+ssh:// form, with the SSH options vcows
  assembled from ssh_keyfile and known_hosts". The provider gets `qemu+sshcmd://`
  (`render.py:56`) and nothing is assembled into it — `connection_uri` sets
  `query=""` unconditionally. In `schema.py` the comment says a query string "would
  silently override what vcows appends from ssh_keyfile/known_hosts" and the emitted
  message says "vcows builds it from ssh_keyfile and known_hosts". vcows appends
  nothing, and `connection_uri`'s docstring 30 lines above says so.
- **Why it matters here:** `variables.tf` is read while debugging a failed apply and
  names the exact scheme acceptance defect 1 proved fatal against a split-daemon
  host. The `schema.py` one is worse because it is *emitted*: an operator with
  `?keyfile=…` is told vcows will build the URI from the two config fields, deletes
  the query string, and assumes the key is now carried. It never will be, and the
  real mechanism (`~/.ssh/config`, container only) is never named.
- **Evidence:** `render.py:56` `"uri": connection_uri(target, "sshcmd")`;
  `schema.py:180` `urlunsplit(parts._replace(scheme=f"qemu+{transport}", query=""))`.
- **Fix:** `variables.tf` → "qemu+sshcmd://, no query string; credentials reach `ssh`
  via `~/.ssh/config`". Keep `schema.py`'s refusal (R-D's `no_verify=1` argument holds)
  and change its message to name `~/.ssh/config`.
- **Cost of the fix:** two strings, one comment.

### F-CMT-03 — `orchestrator/__init__.py` claims a version-drift test that does not exist
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/__init__.py:4-12`, `tests/test_version.py:9`
- **What:** "Five things consume it, and `tests/test_version.py` asserts they agree
  so they cannot drift" — `--version`, the marker's `v`, the image tag, the OCI
  label, the build manifest. `test_version.py` asserts three things, one of which is
  on that list: the format regex, the marker, and `pyproject.toml` (omitted from the
  five). Label and manifest are asserted in `test_image.py`, behind the opt-in
  `VCOWS_IMAGE` gate. **The image tag is asserted nowhere** — it is the operator's
  `-t` argument, and `Containerfile` carries its own `ARG VCOWS_VERSION=0.1.0.0`
  literal. `test_version.py` compounds it: "The consumers that do not exist yet ...
  are picked up as their stages land." Stage 5A and 5B landed; nothing was added.
- **Why it matters here:** the version is the marker's format discriminator. A hotfix
  that edits `__init__.py` and forgets the `Containerfile` `ARG`, or tags an image
  `0.1.0.1` against `VERSION` `0.1.0.0`, passes the default 209-test run silently —
  the tests that would catch it are gated off, and the docstring stops anyone
  checking.
- **Evidence:** `test_version.py` has three test functions; the tag is in no test.
- **Fix:** state where each consumer is asserted and behind which gate, and drop
  "cannot drift" — or add the missing assertion (Containerfile's `ARG VCOWS_VERSION`
  matches `VERSION`, parseable offline with no image).
- **Cost:** prose, or prose plus one ungated four-line test.

### F-CMT-04 — `main.tf` justifies the serial console with a capability the acceptance run disproved
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:175-180`
- **What:** "Without these, a VM that fails cloud-init at an air-gapped site is
  unreachable and un-inspectable." `acceptance.md` ("Still open") records the pty
  console produced nothing during the run — `virsh console` needs a controlling TTY
  and a pty keeps no scrollback. What diagnosed defect 5 was SSH into the guest,
  available only because the guest was reachable, which is the case the comment says
  the console covers.
- **Why it matters here:** a reasoning comment defending two devices on every domain,
  whose reasoning was tested and failed. The next person with an unbootable VM at a
  site follows it and spends the outage on a console showing nothing.
- **Evidence:** `acceptance.md`, "Still open" §2 — "during this run it produced
  nothing on a guest that had already booted."
- **Fix:** record what the run found: the pty is the interactive path, gives no boot
  transcript, and a serial log file is the open question. Comment only. **Cost:** four
  lines; adding `<log file=…/>` is a separate decision with real surface.

### F-CMT-05 — two docstrings promise Stage 5 will replace module staging with a pre-initialised tree; it deliberately did not
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/cli.py:240-242`, `orchestrator/tofu.py:179-182`
- **What:** `_stage_module` — "Stage 5 replaces this copy with a pre-initialised tree
  anyway (R6)"; `tofu.init` — "Stage 5 decides whether the run directory is seeded
  from a pre-initialised tree instead." `findings.md:299` records it was built
  differently on purpose: `init` stays in the flow, offline against the baked mirror,
  and a build-time-warmed `TF_PLUGIN_CACHE_DIR` removes the 26 MB per-run cost.
- **Why it matters here:** `_stage_module` reads as scaffolding awaiting deletion
  when it is the shipped path, and the plugin cache — what actually solved the
  problem — is invisible from either file. Someone trimming "superseded" code breaks
  every deploy.
- **Evidence:** `Containerfile:122-133`; `findings.md:299` — "it is the reason to
  keep `init` in the flow rather than design it out."
- **Fix:** replace both forward references with the settled outcome and point at the
  plugin cache. Also correct `_stage_module`'s "Today the libvirt module does not
  [ship a lock]": `Containerfile:100-102` copies `docs/provider-0.9.8.lock.hcl` in, so
  `lock.is_file()` is the live path inside the image.
- **Cost of the fix:** two docstrings.

### F-CMT-06 — `conftest.tofu_env` is documented as "filesystem mirror only" while writing a `direct` block
- **Severity:** S5 · **Confidence:** high
- **Location:** `tests/conftest.py:41-59`
- **What:** the docstring says "A CLI config pointing at a filesystem mirror only."
  The config it writes contains `direct { exclude = [...] }`, so any provider other
  than `dmacvicar/libvirt` may install from the network. `container/tofurc:14-18` makes
  the *absence* of a `direct` block the shipped config's central property.
- **Why it matters here:** tests and deliverable differ on the one setting the
  air-gap story rests on, and the docstring conceals it. A future gate adding a
  second or builtin provider passes on a connected dev box and fails at a site.
- **Evidence:** `tests/conftest.py:52-54` vs `container/tofurc`.
- **Fix:** say what the block is for — the exclusion pins libvirt to the mirror while
  leaving OpenTofu's normal behaviour intact, which is not what ships. **Cost:** one
  sentence.

### F-CMT-07 — smaller inaccuracies, grouped
- **Severity:** S6 · **Confidence:** high
- **Locations and what:**
  - `qcow2.py:8-10` rejects `qemu-img` partly for being "GPL-2.0-only -- the most
    constrained licence that would be in the bundle". `Containerfile:52-58` records
    GPL-2.0-only as *already* in the bundle (glibc, util-linux-core, libzstd,
    python3-pycdlib) and `IMAGE_LICENSES` carries GPL-3.0-or-later. Written at
    Stage 1, before the image existed; the size argument survives, the licence one
    does not, so D18 rests partly on a premise the build disproved.
  - `container/manifest.py:44` "300-odd binaries come from far fewer sources" — the
    shipped manifest has **160** packages and **116** source RPMs; neither the number
    nor the ratio is close. `Containerfile:57` "across 161 packages" — 160.
  - `destroy.py:44-45` "All three predate libvirt 1.2.9" — `UNDEFINE_NVRAM` was
    *introduced* in 1.2.9, per the line above. The conclusion still holds.
  - `preflight.py:88` `disks_of` "Every source path this domain owns" collects only
    `source/@file`; a `<source dev=…>` block disk yields nothing, silently. No trigger
    — vcows creates only file volumes — but it overclaims for a function whose output
    drives deletion.
  - `Containerfile:18` and `tests/conftest.py:32` cite "the Stage 2 prerequisites",
    which live in the plan file outside the repo.
  - `base.py:4` "no edit to any core file" to add a backend vs `backends/__init__.py:4`
    "adds one line here" — `__init__.py` is core. `config.py` has the accurate claim.
- **Evidence:** `/opt/vcows/manifest.json` in the built image → `160 116`.
- **Fix:** correct the counts (write "roughly" — they drift on any package change),
  soften `disks_of` to "every file-backed source path", reduce `qcow2.py`'s licence
  clause to "one more GPL binary for no capability we need", qualify the Stage-2 refs.
- **Cost of the fix:** none beyond the edits.

## Checked and sound

- `prepare.py::_network_config` — names defect 5's failure, version, exception and
  source file; the code emits `0.0.0.0/0`. The best comment in the tree.
- `main.tf` — `features`, the `iso` seed format, the NVRAM suffix ternary and the
  `depends_on` edge match the code and the acceptance record.
- `schema.connection_uri` — the two-transport docstring is correct and complete,
  including which client rejects which spelling. Its neighbour (F-CMT-02) is not.
- `container/entrypoint.py` — libssh-only `known_hosts`, the provider's `knownhosts`,
  `HOME` not a lever, the passwd lookup: all match `acceptance.md` §2.
- `destroy.py` — ordering rule, undroppable `NVRAM` floor, single-retry argument, no
  `os.unlink` fallback; `_undefine` retries once, to `FLOOR`, on `INVALID_ARG` only.
- `preflight.py` — `pool.refresh(0)` (D35), `<backingStore>`, unparseable-is-unmarked
  (D12), one `XMLDesc` per domain. `base.py::decide` — four docstring rules, four
  branches, in order; `Discovered`'s "core never reads `artifacts`" holds.
- `tofu.py` `SHORT_TIMEOUT` and the `-chdir` note; `cli.py`'s run-directory 0700, D23
  drop-before-render and top-level `except Exception` justifications; and the
  `tests/` module docstrings (`fake_backend`, `fake_libvirt`, `test_seam`,
  `test_image`): all accurate.

## Not checked

- Upstream claims I could not exercise: OpenTofu 1.12.6 ignoring `NO_COLOR`,
  `-json-into` landing in 1.12.0, the `qemu-img` 14.2 MB figure. Each is stated as
  measured and cited consistently twice.
- Comment accuracy against RHEL 9/10 behaviour — every claim in the tree is a Fedora
  44 measurement and I had no RHEL target. `docs/*.md` prose is out of scope; I read
  it only as the authority to check comments against.

## Deserves its own agent

- **The serial console decision (D26).** F-CMT-04 is the comment half only. Whether
  `<log file=…/>` should be added, and who owns the host path it writes to, is a design
  call `acceptance.md` leaves open and no scope covers.
- **Version coherence as a mechanism.** `Containerfile`'s `ARG VCOWS_VERSION` and the
  image tag are unguarded by any ungated test (F-CMT-03).
- **`tests/conftest.py`'s `direct` block.** Whether the test config *should* diverge
  from the shipped `tofurc` on air-gap behaviour is a gates question.
