# Decision compliance (D1–D52) — review

Agent: 07-decision-compliance · Scope: every recorded decision vs. the code · Date: 2026-08-29

## Summary

- 44 of 52 decisions HELD; no decision is violated by the code itself. The real
  gap is one missing artifact: **`manifest.json` is documented as a run-directory
  artifact in two places and never written** (R5).
- Four docstrings still state premises a later decision or the acceptance run
  reversed: destroy scope (D4→D36), credentials in the URI (acceptance #2), the
  pre-initialised tofu tree (R6→D48), the serial console's usefulness (D26).
- Every pre-acceptance decision the run could have invalidated was checked
  individually. Only D26's stated benefit is now false; D30 was confirmed and D3
  is correctly still open.

## The table

| D | Status | Where |
|---|---|---|
| D1 | HELD (mechanism moved to Python per D11) | `backends/libvirt/schema.py:319` |
| D2 | HELD | `tests/conftest.py:98,120`, `README.md:104` |
| D3 | HELD — still open, recorded as open | `docs/archive/acceptance.md:8,146` |
| D4 | STALE — its "destroy stays host-wide" clause reversed by D36 | `orchestrator/marker.py:61` |
| D5 | STALE — superseded by D51 once the BOM froze | `container/manifest.py:8` |
| D6 | HELD | `Containerfile:68,76` |
| D7 | HELD | `Containerfile:80-84` |
| D8 | UNVERIFIABLE — sidecar not built; deferred by D51 | — |
| D9 | HELD | `backends/base.py:254` |
| D10 | HELD | `orchestrator/qcow2.py:1` |
| D11 | HELD | `orchestrator/config.py:79-83` |
| D12 | HELD | `backends/libvirt/preflight.py:81-84` |
| D13 | HELD | `backends/libvirt/preflight.py:21,34` |
| D14 | HELD | `orchestrator/marker.py:39` |
| D15 | HELD | `backends/libvirt/tofu/main.tf:158` |
| D16 | HELD | `backends/libvirt/render.py:38-47` |
| D17 | HELD | `backends/libvirt/preflight.py:98` |
| D18 | HELD — verified `qemu-img` absent from the built image | `Containerfile:68-78` |
| D19 | HELD (delivered as the EPEL RPM, not a wheel, per D47) | `Containerfile:74` |
| D20 | HELD | `orchestrator/marker.py:30`, `tests/test_marker.py` |
| D21 | HELD | `orchestrator/tofu.py:154` |
| D22 | UNVERIFIABLE — analysis; conclusion reflected in the licence label | `Containerfile:63` |
| D23 | HELD | `orchestrator/cli.py:196-200` |
| D24 | HELD | `backends/base.py:275-277`, `backends/libvirt/__init__.py:58` |
| D25 | HELD | `backends/libvirt/schema.py:109-127` |
| D26 | HELD — but its stated benefit was disconfirmed by the run | `main.tf:181-187` |
| D27 | HELD | `backends/libvirt/prepare.py:50-52` |
| D28 | HELD | `backends/libvirt/__init__.py:13-15` |
| D29 | HELD | `backends/libvirt/preflight.py:190-208` |
| D30 | HELD — assumption settled by the run | `backends/libvirt/preflight.py:291-311` |
| D31 | HELD | `main.tf:81` |
| D32 | HELD | `backends/libvirt/preflight.py:346-423` |
| D33 | HELD | `backends/libvirt/preflight.py:389-391` |
| D34 | HELD | `tests/test_libvirt_rig.py:37-38` |
| D35 | HELD — both halves | `preflight.py:210`, `destroy.py:203-231` |
| D36 | HELD | `orchestrator/cli.py:264-281` |
| D37 | HELD | `orchestrator/cli.py:72-80`, `README.md:136` |
| D38 | HELD | `orchestrator/cli.py:213-218` |
| D39 | HELD | `orchestrator/tofu.py:152-153` |
| D40 | HELD | `orchestrator/cli.py:204`, `tofu.py:149` |
| D41 | HELD — deploy fatal, destroy advisory | `cli.py:183`, `cli.py:274` |
| D42 | HELD | `orchestrator/tofu.py:162,212,241` |
| D43 | HELD | `orchestrator/cli.py:56-65` |
| D44 | HELD | `orchestrator/cli.py:393-399` |
| D45 | HELD — two commits, `f3f12f7` then `a74537f` | `git log` |
| D46 | HELD — delta recorded with what is lost | `Containerfile:22-30` |
| D47 | HELD | `Containerfile:106-108,117` |
| D48 | HELD — mirror only, cache warmed, gate disables the cache | `Containerfile:124-132`, `tests/test_image.py:155` |
| D49 | HELD | `Containerfile:42`, `README.md:63` |
| D50 | HELD | `README.md:49-52`, no `USER` in `Containerfile` |
| D51 | HELD | `container/manifest.py:72` |
| D52 | HELD | `tests/test_image.py:38` |

Also checked: F11 HELD except its URI-credential clause (F-DEC-03); F12, F16, R3,
R7 HELD; R6 HELD as re-decided by D48; **R5 VIOLATED** (F-DEC-01); §4.1's
`:Z`/`:z` erratum VIOLATED (F-DEC-06).

## Findings

### F-DEC-01 — `manifest.json` is documented in the run directory and never written
- **Severity:** S3
- **Confidence:** high
- **Location:** `orchestrator/cli.py:94-124`, `README.md:146`, `orchestrator/__init__.py:11`
- **What:** R5 requires the build manifest be printed from `--version` *and*
  copied into every run directory. `cli.py` reads `/opt/vcows/manifest.json` only
  to print it in `cmd_version`; `_record` writes `run.json` alone. Nothing copies
  the manifest, and two documents say it is there.
- **Why it matters here:** the run directory is what comes back from an air-gapped
  site when a deploy goes wrong. `run.json` carries the vcows and tofu versions
  but not the provider, the base digest or the git SHA, so the one question an
  archived run cannot answer is which build produced it — and the README says the
  answer is in the directory.
- **Evidence:** `grep -rn manifest orchestrator/` returns only the `MANIFEST`
  constant, `manifest()`, and its use in `cmd_version`. `README.md:146`:
  `manifest.json     which build produced this run`.
- **Fix:** one guarded `shutil.copy(MANIFEST, run)` in `_record` — `manifest()`
  already establishes that a checkout has none and writes nothing.
- **Cost of the fix:** two lines, no new surface. R5 is in scope and two documents
  already promise it.

### F-DEC-02 — `Marker.deployment`'s docstring still says destroy is host-wide
- **Severity:** S5
- **Confidence:** high
- **Location:** `orchestrator/marker.py:61-68`
- **What:** it reads "v0.1 destroy scope stays host-wide, so nothing reads this
  for a destroy decision yet" — D4's text, reversed by D36.
- **Why it matters here:** it is the docstring on the field that decides what a
  destroy touches. A reader concludes the field is inert, that a second deployment
  sharing a host is at risk, or that changing it is free.
- **Evidence:** `cli.py:268` — `e.marker.deployment == deployment`.
- **Fix:** replace it with D36's rule and the default-stem consequence. No cost.

### F-DEC-03 — Two places still say vcows builds the URI from the credentials
- **Severity:** S5
- **Confidence:** high
- **Location:** `backends/libvirt/schema.py:228-230`, `backends/libvirt/tofu/variables.tf:8`
- **What:** the query-string refusal tells the operator "vcows builds it from
  ssh_keyfile and known_hosts"; `variables.tf` describes `uri` as "qemu+ssh://
  form, with the SSH options vcows assembled from ssh_keyfile and known_hosts".
  `connection_uri` now strips the query, credentials go through `~/.ssh/config`,
  and the provider is handed `qemu+sshcmd`.
- **Why it matters here:** this is the area that produced acceptance defect 2,
  whose symptom was `Host key verification failed` with nothing pointing at the
  cause. Someone debugging that reads the error message first, and it sends them
  hunting for query parameters that no longer exist.
- **Evidence:** `schema.py:166` strips the query; `render.py:61` passes `sshcmd`.
- **Fix:** say the URI carries no query string and that credentials travel through
  `~/.ssh/config`; in `variables.tf`, name the `qemu+sshcmd` scheme. Text only.

### F-DEC-04 — Two docstrings still promise a pre-initialised tofu tree
- **Severity:** S5
- **Confidence:** high
- **Location:** `orchestrator/cli.py:239-242`, `orchestrator/tofu.py:180-184`
- **What:** `_stage_module` says "Stage 5 replaces this copy with a
  pre-initialised tree anyway (R6)"; `tofu.init` says Stage 5 "decides whether"
  it does. D48 decided against, and Stage 5 has shipped.
- **Why it matters here:** the next person to touch staging reads it as unfinished
  work and completes it, undoing the one change that keeps 26 MB of provider out
  of every run directory.
- **Evidence:** `Containerfile:124-132` warms the cache; `tests/test_image.py:200`
  asserts the provider stays out of every run directory.
- **Fix:** say D48 settled it and point at the plugin cache. Text only.

### F-DEC-05 — `main.tf` still claims the serial console makes a failed VM inspectable
- **Severity:** S5
- **Confidence:** high
- **Location:** `orchestrator/backends/libvirt/tofu/main.tf:181-185`
- **What:** the comment justifying D26 says that without the console "a VM that
  fails cloud-init at an air-gapped site is unreachable and un-inspectable". The
  run disconfirmed that: `virsh console` needs a controlling TTY and a pty keeps
  no scrollback, so it produced nothing. SSH diagnosed defect 5.
- **Why it matters here:** eight lines of HCL are kept for a benefit they do not
  deliver, and this comment is what would stop someone adding the `<log file=…/>`
  `docs/archive/acceptance.md` says would have given the boot transcript for free.
- **Evidence:** `docs/archive/acceptance.md:139-145`.
- **Fix:** record what the run found. Text only — adding the log file is a
  separate decision and must not ride along with a comment correction.

### F-DEC-06 — README's `podman run` uses `:Z` where the errata prescribes `:z`
- **Severity:** S3
- **Confidence:** medium
- **Location:** `README.md:59-62`
- **What:** the documented command mounts `~/.ssh/id_ed25519`,
  `~/.ssh/known_hosts` and `/srv/images` with `:Z`, relabelling those host paths
  in place with a private exclusive MCS category.
- **Why it matters here:** on an SELinux-enforcing workstation this relabels the
  operator's own key and known_hosts out from under their own `ssh`, and a shared
  `/srv/images` out from under anything else serving it — after the container
  exits, on unrelated commands, looking nothing like a vcows failure.
- **Evidence:** `findings.md` errata §4.1: "`:Z` applies a *private exclusive*
  label; on a shared `/srv/images` it relabels the directory out from under
  libvirtd. Use `:z` for shared read-only mounts."
- **Fix:** `:z` on the three read-only mounts; `./runs` is the container's own and
  keeps `:Z`. Not reproduced here — confirm with `ls -Z` on an enforcing host.
- **Cost of the fix:** one character, three places.

## Checked and sound

- All 52 decisions traced to code, tests or the Containerfile; 44 HELD.
- Decisions the run could have invalidated: D26 (benefit false, code correct),
  D30 (confirmed), D3 (correctly open), D23/D31 (A4, A7), D36 (A6).
- D20 and D25, the permanence decisions, are pinned by tests, not just docstrings.
- D35 is present on both sides, and destroy refreshes *every* active pool —
  stricter than D35 asked for, and right.
- D18 verified empirically (`rpm -q qemu-img` says "not installed"); D41's
  asymmetry runs the documented direction, `cli.py:183` vs `cli.py:274`.

## Not checked

- Whether any decision is *right*. Out of lens by instruction.
- The tests' own correctness; read only as evidence a decision is pinned.
- D8/D22's licensing conclusions — no artifact to audit. The rig was not touched;
  D34's fixtures confirmed from test source only.

## Deserves its own agent

- **F-DEC-01 is one instance of a class.** The README's run-directory listing is a
  contract nothing tests. An agent comparing every artifact the README promises
  against what `cli.py` writes would settle whether `inventory.json`, `plan.bin`
  and the JSON streams all appear under the documented names.
