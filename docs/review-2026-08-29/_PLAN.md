# Overnight code review — orchestrator operating manual

**You are the orchestrator, starting cold. This file is your operating manual.**

Three companion files exist. **You read only this one and `_progress.md`.** The
other two are for the agents, and you must never load them into your own context:

| file | who reads it |
|---|---|
| `_PLAN.md` (this file) | you |
| `_progress.md` | you, every batch |
| `_BRIEF.md` | every agent, told to read it |
| `_ORIENTATION.md` | every agent, told to read it |

## What is being reviewed

`vcows-deploy` v0.1.0.0 at `/home/ssullivan/vcows-deploy` — an air-gapped
container that deploys pre-built golden qcow2 images as VMs to KVM/libvirt over
`qemu+ssh://`. OpenTofu creates; Python and `libvirt` destroy by an ownership
marker. It has never shipped. Everything from `45d5b92` (Initial Commit) to
`HEAD` is under review: one long PR across eleven commits. The v0.1 acceptance
run passed on 2026-08-29. This is the pass before anyone else depends on it.

## Deliverables

| | |
|---|---|
| Per-agent findings | `docs/agents-2026-08-29/NN-<slug>.md`, ≤200 lines each |
| Progress and budget ledger | `docs/agents-2026-08-29/_progress.md`, yours |
| **The review** | `docs/2026-08-29-review.md` — **you write this yourself**, in phase D |

## Your context budget — read this before anything else

You have roughly **250k tokens before coherence degrades**. The target is to
**finish having used 200k–250k**. Under-spending is a failure mode here: an
orchestrator that only ever sees 30-line summaries cannot arbitrate between two
agents who disagree, cannot tell an important finding from a loud one, and
cannot write a review worth reading. You are not a dispatcher. You are the
reviewer of record, and the agents are how you read 7,000 lines without
drowning.

Plan to spend roughly:

| phase | activity | budget |
|---|---|---|
| 0 | This file, `_progress.md`, orienting | ~12k |
| A | 8 broad agents; read their ≤30-line returns | ~12k |
| A′ | Read `docs/findings.md`, `docs/acceptance.md`, `README.md` yourself | ~14k |
| B | Derive and launch follow-ups; read returns | ~14k |
| C | Three adversarial agents; read returns | ~8k |
| D1 | **Read every agent file in full** | ~50k |
| D2 | **Verify the top findings yourself** against the source | ~30k |
| D3 | Draft and write `docs/2026-08-29-review.md` | ~20k |
| — | Tool overhead, thinking, progress updates | ~40k |
| | **total** | **~200k** |

Record your running estimate in `_progress.md` after each phase. If you reach
phase D with a lot of headroom, spend it on D2 — there is a list of what to
verify next at the end of this file. If you are running hot at the end of phase
C (over 140k), skip D2's optional items and protect D1 and D3.

**Never spend context on:** reading source files during phases A–C (that is what
the agents are for), re-reading a file you have already read, or pasting an
agent's file contents into a prompt.

## Rules

1. **Read-only review.** Neither you nor any agent modifies a tracked file. No
   fixes, no commits. Findings only.
2. **Do not read source during phases A–C.** In phase D2 you read source
   deliberately, to verify specific claims, and only those.
3. **Batch at most 4 agents concurrently.**
4. **Agent returns are capped at 30 lines.** That is enough to steer phase B.
   The detail lives in their files, which you read once, in phase D1.
5. **Do not paste agent output into another agent's prompt.** Point at the file
   path instead; agents can read.
6. If an agent fails or returns something unusable, **retry once** with the
   scope halved. If it fails again, mark it `failed` in `_progress.md` and carry
   the gap into the review's coverage section. Do not silently drop it.

## Resumption

This runs across usage-window resets. Treat interruption as normal.

* After each agent completes, append to `_progress.md`:
  `NN-<slug> | done | S1:n S2:n S3:n S4:n S5:n S6:n | <≤15-word headline>`
* After each phase, append `PHASE <x> COMPLETE | est. context used: <n>k`.
* Before launching any batch, read `_progress.md` and **skip anything already
  `done`**. That is the entire resume mechanism; keep it accurate.
* A resumed session that finds phases A–C complete goes straight to D1. Note
  that a resumed session starts its context budget at zero, which is fine —
  phase D alone fits comfortably.
* Priority if budget runs short: **A → B → D → C**. A synthesised review missing
  its adversarial pass beats fifteen unsynthesised agent files.

## How to launch an agent

Use the Agent tool. Every prompt follows this template exactly — substitute the
bracketed parts from the roster below. Do not inline `_BRIEF.md` or
`_ORIENTATION.md`; naming them is the point.

```
Before anything else, read these two files in full:
  docs/agents-2026-08-29/_BRIEF.md         (rules, output contract, severities)
  docs/agents-2026-08-29/_ORIENTATION.md   (what this codebase is and where things are)

You are agent <NN-slug>. Write your findings to
docs/agents-2026-08-29/<NN-slug>.md, following the output contract in _BRIEF.md
exactly. Maximum 200 lines.

## Your scope
<scope paths from the roster>

## Your lens
<the lens paragraph from the roster>

## Specific questions to answer
<the questions from the roster>

Your final message to me: at most 30 lines. Report the file you wrote, counts
per severity, your single most important finding in one sentence, and anything
you saw that falls outside your scope and deserves its own agent. Nothing else.
```

That last clause matters — it is how phase B gets its assignments.

---

## Phase A — broad coverage (8 agents, two batches of 4)

### `01-core-seam`
**Scope:** `orchestrator/__init__.py`, `marker.py`, `qcow2.py`, `config.py`,
`backends/__init__.py`, `backends/base.py` (~660 lines).
**Lens:** `decide()` is described in its own docstring as "the dangerous logic,
written once". Everything else in the tool trusts it. Attack it, and attack the
records it consumes.
**Questions:** Can `decide()` return CREATE for something that exists, or SKIP
for something that is not ours? What happens with two existing VMs claiming the
same logical name; a marker whose JSON parses but whose fields are the wrong
type; a marker from a future version; an empty `deployment` string; a VM marked
by a *different* tool that happens to use the same namespace? Is `uuid5`
derivation stable across Python versions, and is the `VCOWS_NS` pin real? Does
`qcow2.virtual_size` behave on a truncated file, wrong magic, a version-3 header
with extra fields, a directory, a named pipe? Does `config.load` really report
every problem at once? Does schema composition hold with zero backends and with
two? Are the frozen dataclasses in `base.py` actually immutable in the ways
callers assume (`tuple` vs `list` fields, mutable defaults)?

### `02-libvirt-connected`
**Scope:** `backends/libvirt/preflight.py`, `destroy.py` (~710 lines).
**Lens:** the only half that holds a live connection, and the only half that
destroys. Assume the hypervisor misbehaves.
**Questions:** What happens when a call raises mid-loop; a domain vanishes
between enumeration and use; a pool goes inactive after the refresh; XML is
missing an element the parser indexes into? Verify the `<backingStore>`
exclusion holds on **every** path — it is the only thing between destroy and the
shared golden image. Verify the undefine flag floor cannot shed `NVRAM`, that
`pool.refresh(0)` precedes every lookup (D35), and that a partial failure is
reported per object and exits non-zero. Is every `libvirtError` catch matching a
numeric code rather than a message? The preflight-then-create TOCTOU is a
documented accepted gap — check that its actual failure mode is what §2 claims
(a hard error, not corruption), including two operators racing and a VM created
between preflight and apply.

### `03-libvirt-offline`
**Scope:** `backends/libvirt/schema.py`, `render.py`, `prepare.py` (~680 lines).
**Lens:** `target.libvirt` is the one-way door (F11) — other groups author these
by hand and keep them in their own version control. Strict where it must be,
permissive where it should be.
**Questions:** Try to get a bad config past `validate`: a URI with a password or
a port, a NIC with neither `bridge` nor `network` or with both, a gateway
outside its subnet, duplicate MACs or IPs across VMs, `disk_gb` below the
image's virtual size, absurd `vcpus`/`memory_mib`, a `user_data` that is not a
string, unicode in a VM name, a name that is a valid libvirt name but not a
valid volume name. Is `derive_mac` deterministic, and what happens on a
collision between a derived MAC and a configured one? For the seed ISO: Rock
Ridge **and** Joliet names, the `cidata` label, and `user_data` passed through
byte-for-byte (D27) — including trailing newlines and CRLF. The network-config
default route was just changed from `default` to `0.0.0.0/0` after cloud-init
24.4 rejected the former; check the **rest** of that document against what
cloud-init 22.x through 24.x accept, since RHEL 9 ships older.

### `04-tofu-module`
**Scope:** `backends/libvirt/tofu/*.tf` (~300 lines), with
`docs/provider-schema-0.9.8.json` as ground truth for the provider.
**Lens:** the acceptance run found two defects here and both were the same
shape — *the module never emitted XML libvirt needs*. Find the rest of that
class.
**Questions:** What else does a domain need that nothing supplies — CPU model
and topology, machine type defaults, disk cache and IO mode, RNG, memballoon,
clock/timers, `on_reboot`/`on_crash`/`on_poweroff`, SMM for secure boot? Check
`for_each` keys against D16's naming rules, the `depends_on` that makes a
partial apply a no-op (D31), the `count` guard on the base volume, and whether
`outputs.tf` can produce a null or partially-known map. What happens on a
re-apply against a non-empty state, given that D23 says that never happens — is
that guaranteed by construction or by convention? Which of these behaviours
would differ on RHEL 9's older libvirt or an older QEMU machine type?

### `05-driver-cli`
**Scope:** `orchestrator/tofu.py`, `cli.py` (~650 lines).
**Lens:** the operator-facing surface, and the process handling underneath it.
**Questions:** Ctrl-C during a multi-GB upload — what is left on the hypervisor,
what is left in the run directory, and can a re-run recover or does it hit the
orphan-volume refusal? What does the absence of a timeout on `plan`/`apply`
(D42) cost when the SSH tunnel wedges rather than resets? How does the
`-json-into` parse behave on a truncated, absent, or enormous file? Is the run
directory's mode, contents and naming right, given it holds `user_data` inside
the seed ISOs? What does the top-level `except Exception` in `main()` swallow
that an operator needs to see? Check exit codes against the documented "0 and 1",
`--run-dir` pointing at an existing or unwritable path, two runs in the same
second, and whether any error message would leave an operator at an air-gapped
site with no next step.

### `06-container-supplychain`
**Scope:** `Containerfile`, `container/*`, `licenses/`, `.containerignore`,
`README.md` build and run sections (~600 lines).
**Lens:** air gap and supply chain — the properties that fail at a site rather
than on a desk.
**Questions:** Is every downloaded artifact checksum-verified **before** use, and
every version pinned exactly including the base image digest? Does the OCI label
set describe this image (F16), and is `org.opencontainers.image.licenses`
defensible against what `manifest.json` actually lists? Does the manifest capture
everything R5 asks for? Attack `container/entrypoint.py`: a config that is a
symlink or a FIFO, a UID with no passwd entry, an existing `~/.ssh/config`, YAML
with unexpected types, no config argument at all, an argument that looks like a
path but is not. Are the provider mirror and the warmed plugin cache consistent
with each other and with the committed lock? Can anything in the image reach the
network at runtime?

### `07-decision-compliance`
**Scope:** every decision, against the code.
**Lens:** a compliance audit, not a design critique. Do not argue with a
decision; report its status.
**Sources:** `docs/findings.md` in full, including the R- and F- items, and the
decision tables **D1–D52** in
`/home/ssullivan/.claude/plans/this-repo-builds-vcows-deploy-fluttering-meerkat.md`.
**Questions:** For each decision, one line: `HELD` / `VIOLATED` /
`SILENTLY-REVERSED` / `STALE` / `UNVERIFIABLE`, with a path citation. Produce
the complete table first — it is the deliverable — then findings **only** for
the non-compliant ones. Pay particular attention to decisions made before the
acceptance run whose premises the run changed.

### `08-silent-failure`
**Scope:** the whole tree. **Agent type:
`pr-review-toolkit:silent-failure-hunter`.**
**Lens:** the acceptance run's worst defect was silent — cloud-init rejected the
network config, applied nothing, fell back to DHCP, and both guests booted
healthy on the wrong addresses reporting `cloud-init status: done`. Find the
rest of that class.
**Questions:** Every `except` that continues, every `.get(…, default)` that
masks absence, every fallback, every failure downgraded to a warning, every
early `return` that leaves a caller believing work happened, every boolean that
defaults to the permissive value. For each: what would the operator see, and why
would they believe it worked?

**After phase A:** read `docs/findings.md`, `docs/acceptance.md` and `README.md`
yourself (phase A′, ~14k). You need them to judge drift claims in phase D
without taking an agent's word for it.

---

## Phase B — depth (4 fixed + up to 4 derived)

### `09-comment-accuracy`
**Scope:** the whole tree. **Agent type:
`pr-review-toolkit:comment-analyzer`.**
**Lens:** this codebase is unusually comment-dense, and the comments carry the
*reasoning* — why a flag is never shed, why a namespace is a URN, why a check
exists. A comment that has quietly become false is worse here than a missing
one, because the next maintainer will trust it.
**Questions:** Which docstrings and comments state something the code no longer
does? Which cite a measurement, a version, or an upstream behaviour that the
acceptance run contradicted? Which describe a decision by an obsolete name?
Which promise a guarantee the code does not enforce? Rank by how expensive
believing the comment would be.

### `10-seam-second-backend`
**Scope:** `backends/base.py`, `backends/__init__.py`, `config.py`,
`tests/fake_backend.py`, `tests/test_seam.py`, `docs/future-backends.md`.
**Lens:** the architecture's central claim is that adding a second backend
touches no core file. That claim has never been tested by anyone adding one.
**Questions:** Walk through adding a vSphere or Proxmox backend on paper. What
in core would actually have to change? Does anything in `config.py`, `cli.py` or
`tofu.py` assume libvirt semantics — naming, paths, the shape of `artifacts`,
the module directory convention, `Inventory`? Is `Discovered.artifacts` really
opaque to core on every path? Would the ownership policy survive a backend whose
identity is not a UUID? Is the seam test proving the claim, or proving something
weaker?

### `11-lifecycle-recovery`
**Scope:** `cli.py`, `destroy.py`, `preflight.py`, `docs/findings.md` §2 and R5.
**Lens:** everything after the happy path — crash, resume, upgrade, scale.
**Questions:** For each stage (preflight, prepare, init, plan, apply, outputs,
destroy), what does a crash leave behind and what does the next run do about it?
R5 records that there is **no air-gapped update path** — what actually breaks
moving a site from 0.1.0.0 to 0.2.0.0: marker format, `schema_version`, the
provider version, the base image name? Is the marker's `v` field ever read for
compatibility, or only written? What happens at twenty VMs rather than two —
run-directory growth, upload time, the `for_each` map, per-object destroy
accounting, the 26 MB plugin cache? What does the accumulated `runs/` directory
cost a site over a year?

### `12-test-teeth`
**Scope:** `tests/` (~3,600 lines) and whatever source it takes to break.
**Lens:** do the tests fail when the code is wrong?
**Questions:** Copy the repo to a scratch directory and **deliberately break
things**: invert a comparison in `decide()`, delete the `pool.refresh(0)` call,
shed the `NVRAM` bit, change the marker namespace, remove a `depends_on`, swap a
severity, return the raw URI from `render` again, put back `to: default`. Record
which mutation each test catches and which pass regardless. Can any of the three
opt-in gates (`VCOWS_RIG_URI`, `VCOWS_IMAGE`, the tofu mirror) pass by skipping?
Is the golden-file comparison load-bearing? Which behaviours that the acceptance
run had to discover are *still* untested?

### Derived agents (`13-…` onward, up to four)

**This is where your phase-A reading earns its keep.** Every phase-A agent was
asked to report anything outside its scope that deserves its own agent. Combine
those with your own reading of the returns and commission up to four follow-ups.

Good derived agents are narrow and evidence-driven: *"three agents independently
flagged the run directory as a secrets problem — trace every path that writes to
it and everything that ends up inside"*, or *"02 and 08 disagree about whether
the destroy loop can leave an overlay behind — settle it"*. Write these prompts
yourself, in the same template. Name them `13-<slug>` upward, and record what
prompted each one in `_progress.md` — the review's methodology section needs it.

If phase A produced no smoke worth chasing, say so in `_progress.md` and skip
this. Do not invent assignments to fill the slots.

---

## Phase C — adversarial (3 agents, one batch)

### `17-verify-severe`
**Lens:** try to refute. Read every `docs/agents-2026-08-29/*.md` produced so
far. Take every finding marked **S1 or S2** and attempt to disprove it:
reproduce the claim against the code, and check whether an existing test, guard,
or call-site invariant already prevents it. **Default to refuted when
uncertain.** Output per claim: `CONFIRMED` / `REFUTED` / `NEEDS-EVIDENCE`, the
reason, and the command run. This is the file you trust most in phase D.

### `18-security-adversary`
**Lens:** three hostile inputs, traced end to end — (a) a `config.yaml` authored
by another team, (b) domain XML returned by a hypervisor you do not control,
(c) `user_data` written by someone who is not the operator. Where does each
reach a shell, a path join, a file write, a subprocess argument, an XML parse?
Trace the credential path: what the entrypoint writes, what lands in the run
directory, what `podman inspect` or a process listing exposes, whether any
secret reaches the OpenTofu state or the JSON streams. D13 settled stdlib
`ElementTree` — do not re-argue it, but do check that the actual exposure still
matches the reasoning in `preflight.py`'s module docstring.

### `19-completeness-critic`
**Lens:** what did nobody look at? Read every agent file, including the "Not
checked" sections, and the repository tree. Which files has no agent read? Which
failure mode has no owner? Which of the eleven commits' changes were never
examined? Which claim in `findings.md` is verified by nothing — not a test, not
an agent, not the acceptance run? Output a gap list ordered by risk, and for
each gap say what would close it. **This section goes into the review verbatim
as its coverage honesty.**

---

## Phase D — you write the review

### D1 — read everything (~50k)
Read every `docs/agents-2026-08-29/NN-*.md` in full. Skip `_PLAN`, `_BRIEF`,
`_ORIENTATION`. Read `17-verify-severe.md` last and treat it as the arbiter of
what belongs in the top section.

### D2 — verify the top findings yourself (~30k)
For every S1 and S2 that survived phase C, **open the source and check it**.
This is the one place you read code, and it is what makes you the reviewer of
record rather than a stapler. Where two agents disagree, you settle it. Where a
finding's evidence is an assertion rather than a command, either verify it or
demote it and say why.

If you have headroom after that, verify in this order: the S3 findings that
touch destroy or the ownership policy; every drift claim in the S5 set against
the documents you read in phase A′; anything `19-completeness-critic` named as
unverified by anything.

### D3 — write `docs/2026-08-29-review.md` (~20k)

Under 500 lines. The agent files carry the detail; this is the document a
maintainer acts on.

1. **Verdict** — three sentences. Is this shippable to another team as v0.1, and
   what is the one thing that would stop you.
2. **Act before anyone else depends on this** — S1/S2 confirmed by phase C and
   verified by you in D2. Each: title, location, one sentence of consequence,
   the fix, and what the fix costs in surface. Ordered by severity, then by how
   cheap the fix is.
3. **Worth fixing** — S3 plus the S4 sprawl findings, grouped by area, one line
   each, pointing at the agent file.
4. **Documentation drift** — S5 as a table: claim, where stated, what the code
   does. You read the three authoritative documents in A′; use that.
5. **Disputed and refuted** — what phase C could not confirm, kept visible with
   the reason. A reader must see what was considered and dismissed.
6. **Coverage and gaps** — condensed from the agents' "Checked and sound" and
   "Not checked" sections plus `19-completeness-critic`. Be honest.
7. **Themes** — at most four patterns appearing across more than one agent's
   file. This is where the review earns its keep over a defect list.
8. **Methodology** — the agent roster, what each covered, which derived agents
   you commissioned and why, and your final context figure.

Rules: **deduplicate** — one defect found by three agents is one entry citing
three files. **Do not inflate** — if the codebase is largely sound, say so
plainly and keep it short. Preserve every S1 and S2 including refuted ones, in
section 5. No code rewrites.

---

## Closing out

1. `ls -la docs/agents-2026-08-29/ docs/2026-08-29-review.md`.
2. Append the final `_progress.md` line: completion, severity totals, and your
   estimated context used.
3. Report to the user in **at most 15 lines**: the verdict sentence, severity
   counts, the top three findings as one-liners, the two paths, and your context
   figure against the 250k budget. Do not paste the review.
4. **Do not commit anything.**
