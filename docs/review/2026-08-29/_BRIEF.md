# Common brief — read this before reviewing anything

You are one of roughly eighteen agents reviewing `vcows-deploy` at
`/home/ssullivan/vcows-deploy`. Read `_ORIENTATION.md` next; it tells you what
the codebase is and where things are, so you do not spend your context
rediscovering it.

## The tool, in four sentences

`vcows-deploy` v0.1.0.0 deploys pre-built golden qcow2 images as VMs to
KVM/libvirt over `qemu+ssh://`, from an air-gapped Podman container. OpenTofu
creates; Python and the `libvirt` binding destroy, discovering what to destroy
from an ownership marker in each domain's `<metadata>`. It has never shipped and
has exactly one user. Review target: `45d5b92..HEAD`, eleven commits, about
2,800 lines of Python, 300 of HCL, 3,600 of tests.

## Rules

**This is a read-only review. Do not modify any tracked file.** You may run
tests, `git`, `podman`, and read-only shell commands. If you need to mutate code
to prove a point, copy the repo to a scratch directory first — use the
`scratchpad` path in your environment if you have one, otherwise `mktemp -d`.

**Do not fix anything.** Findings only. A patch is not a finding.

**The rig is live and is not yours.** `qemu+ssh://vcows@vcows/system` hosts four
working VMs belonging to someone else, plus two probe domains used as test
fixtures. Read-only libvirt calls are fine. **Do not define, start, stop,
undefine, or delete anything on it**, and do not create volumes.

## The documents, in order of authority

| | |
|---|---|
| `docs/findings.md` | **Authoritative.** Every settled decision, the cuts, the explicitly-not-built list, and an errata appendix for the architecture doc. |
| `docs/archive/acceptance.md` | The run that proved v0.1 on 2026-08-29, and the five defects it found. Recent and reliable. |
| `docs/spikes/README.md` | The four spikes (A1–A6) and what they measured. |
| `README.md` | The operator contract. |
| `docs/archive/orchestrator-architecture.md` | **Archived background, written before any code existed. Several of its concrete claims are wrong.** Its errata table lives inside `findings.md`. Never cite it against the code without checking there first. |

The decision numbers `D1`–`D52` referenced throughout the code live in
`/home/ssullivan/.claude/plans/this-repo-builds-vcows-deploy-fluttering-meerkat.md`.
Codes like `F11`, `R5`, `A4` refer to items inside `findings.md` and
`docs/spikes/README.md`.

## The governing constraint

**A previous implementation of this tool was built and discarded because it
became sprawling.** `findings.md` §5 ("Cut from v0.1") and §3's "Explicitly not
built" list bind as hard as the architecture does.

Two consequences for you:

* **Unjustified surface area is a defect here, not a matter of taste.** Dead
  code, speculative abstraction, a config field nothing reads, an option with no
  caller — those are findings (S4), not nits.
* **A fix that adds more surface than the defect warrants is itself a
  problem.** Every finding you write must say what its fix costs. "Add a
  normalisation layer" is precisely how the predecessor died.

## The test bed lies in one direction

Development and the acceptance run used a **Fedora 44** KVM host: libvirt
12.0.0, split daemons (`virtqemud`), SELinux enforcing, cloud-init 24.4 guests,
qcow2 OVMF firmware. The real targets are **RHEL 9 and RHEL 10**.

Fedora will pass things RHEL 9 fails. Flag anything depending on a newer
libvirt, a newer cloud-init, split daemons, SELinux specifics, or Fedora paths.
Rocky 9.8 and 10.2 both ship libvirt 11.10.0; Rocky 9.0/9.1 EUS ship 8.0.0 and
8.5.0. RHEL 9 ships raw `.fd` OVMF where Fedora ships qcow2.

## Settled — not findings

Do not re-litigate any of these. They have long arguments behind them in
`findings.md`:

* the Podman image as the deliverable, and its size
* four-digit `Major.Minor.Patch.Hotfix` versions
* destroy in Python rather than `tofu destroy` (§1 carries the full comparison)
* never converging — deploy creates, never modifies
* stdlib `ElementTree` over `defusedxml` (D13; reasoning in `preflight.py`'s
  module docstring)
* no pydantic at v0.1
* the OpenTofu state being disposable and never read back
* `subprocess.run` with `-json-into` rather than a `Popen` NDJSON reader (D21)
* the storage pool must pre-exist; vcows never creates one (D29)
* `inventory.json` having no consumer at v0.1

If you believe one of these is wrong, you may say so **once**, in a clearly
marked "Settled, but" note at the end of your file. Not as a finding.

## What is not a finding

Writing these down because they would otherwise be half the review:

* "Consider adding type hints / pydantic / a config class." The code is typed
  and checked with `ty`; the rest is settled.
* "This function is long." Length is not a defect. Say what is wrong with it.
* "Add more tests" without naming the behaviour that is untested and the failure
  it would catch.
* "Consider a plugin architecture / entry points / a registry." Explicitly cut.
* Style, naming, or formatting that `ruff` accepts.
* Anything justified by "best practice" without a failure mode in *this* tool.
* Restating a docstring's reasoning back as a finding.
* A finding whose evidence is "this could be a problem". Reproduce it or mark it
  low confidence and say what would settle it.

## Calibration — three real findings, already fixed

These came out of the acceptance run. They show what each severity means here.

**S1 — the cloud-init default route.** `prepare.py` emitted netplan's
`routes: [{to: default}]`. cloud-init 24.4 accepted the document, read it,
logged `Applying network configuration from ds`, then threw
`ValueError: Address default is not a valid ip address` out of its own v2-to-v1
normaliser, applied nothing, and fell back to DHCP. **Both guests booted
healthy, on the wrong addresses, reporting `cloud-init status: done`.** S1 is
not "it crashes" — S1 is "it looks like success and is not".

**S2 — `known_hosts` in the URI.** The config's `known_hosts` was passed as a
URI query parameter. libvirt's `qemu+ssh` ignores it (it is a libssh-only
parameter); the provider's dialer spells it `knownhosts`; its `sshcmd` transport
rejects both. The connection died with `Host key verification failed` and
nothing pointing at the cause. Loud, fatal at a site, undiagnosable from the
message.

**S6, not S5 — the NVRAM suffix.** `main.tf` hardcoded `_VARS.fd` while the
format came from `loader_format`, so a qcow2 varstore was written under a `.fd`
name. It booted correctly either way, because libvirt reads the declared format,
not the extension. Cosmetic, misleading to someone debugging on the host. It was
flagged three times before being fixed — which is the right treatment for an S6.

## Severity

| | |
|---|---|
| **S1** | Data loss, or silently wrong behaviour against a real target. It looks like success. |
| **S2** | Fails at a site, but loudly. Air-gap breakage, RHEL 9 incompatibility, a hard error the operator cannot self-diagnose. |
| **S3** | Correctness or robustness issue with a workaround, or a real edge case with no current trigger. |
| **S4** | Unjustified surface, dead code, speculative abstraction, or something crept past §5 / §3. First-class here. |
| **S5** | Documentation, docstring or comment that disagrees with the code — including reasoning that has quietly become false. |
| **S6** | Nit. Use sparingly. A file of S6s buries the S1. |

Rank most severe first. **Do not invent findings to fill a section.** An area
that is genuinely sound is a valuable result, and "Checked and sound" is where
you say so.

## Output contract

Write to `docs/agents-2026-08-29/<your-NN-slug>.md`. **Create the file even if
you find nothing. Maximum 200 lines** — the orchestrator reads every agent's
file in full and its context is finite.

````markdown
# <Area> — review

Agent: <NN-slug> · Scope: <paths> · Date: 2026-08-29

## Summary
<at most five bullets>

## Findings

### F-<SLUG>-01 — <one-line title>
- **Severity:** S1 | S2 | S3 | S4 | S5 | S6
- **Confidence:** high | medium | low
- **Location:** `path/to/file.py:123`
- **What:** what is wrong, concretely
- **Why it matters here:** tie it to this tool's failure modes, not to general
  principle — "at an air-gapped site this presents as X", "on RHEL 9 this is Y"
- **Evidence:** a command and its output, or a quoted line. Not an assertion.
- **Fix:** one paragraph. No rewritten files.
- **Cost of the fix:** what surface it adds, and what would justify it.

## Checked and sound
<bullets — what you examined and found correct. Not filler: the orchestrator
needs to know what was covered and cleared, and the review has a coverage
section that depends on it.>

## Not checked
<bullets — what you deliberately or unavoidably left, and why>

## Deserves its own agent
<optional — anything outside your scope that you saw and could not pursue. The
orchestrator commissions follow-up agents from these.>

## Settled, but
<optional, at most one short paragraph — see above>
````

## Your final message

**At most 30 lines.** The file path you wrote, counts per severity, your single
most important finding in one sentence, and anything belonging in "Deserves its
own agent". Nothing else — the orchestrator reads your file later, in full, and
a long return message wastes the budget that pays for that.

## Running things

```bash
.venv/bin/python -m pytest -q                    # 209 tests
VCOWS_RIG_URI=qemu+ssh://vcows@vcows/system \
VCOWS_IMAGE=localhost/vcows-deploy:0.1.0.0 \
  .venv/bin/python -m pytest -q                  # 235 with the opt-in gates
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/ty check
git log --oneline 45d5b92..HEAD
git diff 45d5b92..HEAD -- <path>
```

The container image `localhost/vcows-deploy:0.1.0.0` is built and current.
`.tools/tofu-mirror` holds the pinned provider. `tofu` 1.12.6 is on PATH.
