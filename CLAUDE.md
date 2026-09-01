# vcows-deploy

Deploy pre-built golden qcow2 images as VMs to KVM/libvirt over `qemu+ssh://`,
or to Proxmox VE over its HTTPS API with a token, shipped as a container that
runs air-gapped apart from that one connection. OpenTofu creates; Python
destroys -- through the `libvirt` binding on one backend and `proxmoxer` on the
other.

Most of this project's operating rules are counterintuitive, and the obvious
helpful action breaks several of them. Each section below is one rule, an anchor,
and the reason the anchor is worth opening.

## The dev venv, and why `uv sync` is wrong

Create it with `just dev-env` and nothing else. `pyproject.toml` states the
failure mode: without `--system-site-packages` the `python3-libvirt` RPM binding
is invisible, and without an explicit `--python /usr/bin/python3` uv installs its
own managed CPython whose site-packages contains no RPMs at all, so the flag
appears to work while `import libvirt` still fails. `uv sync` can express
neither, which is why this project uses `uv venv` plus `uv pip install`. The
lockfile is honoured anyway: `just dev-env` exports `uv.lock` and installs the
export, so the `>=` bounds in `pyproject.toml` constrain `uv lock` and not the
venv.

This is not a rig-only concern. `tests/fake_libvirt.py` imports `libvirt` at
module scope, so a wrong venv breaks collection, not just the hardware tests.

## The config is not declarative

`README.md`'s opening section is headed "Read this first" for this reason:
**deleting a VM from `config.yaml` does not delete the VM.** vcows never
converges. `deploy` creates what is missing and reports what already exists; it
never modifies and never removes. Tearing something down is `vcows destroy`,
and nothing else.

Identity is the **marker**, a JSON payload in the domain's `<metadata>`, never
the name (`orchestrator/marker.py`). A renamed VM is still ours and still
destroyable, and a VM vcows did not create is never adopted or overwritten.

## CI calls `just` recipes and nothing else

`scripts/lint.sh`'s `workflows_carry_no_logic` parses both pipeline files
with PyYAML and rejects any command outside a small allowlist, so adding a `run:`
step that carries logic fails `just lint` rather than failing review. New logic
goes in `scripts/`, gets a recipe in the `justfile`, and the pipeline calls the
recipe.

## Gate discipline: a skip must be able to become a failure

`tests/conftest.py`: "A gate that quietly passes because it did not run is
worse than no gate." Every conditional skip goes through `conftest.gate()` or
`conftest.require()`, and `tests/test_gates.py` AST-walks the suite and fails
on any bare skip. **`BANNED` in that file is the list, not this sentence** -- it
has grown twice since this line was written, and it also catches
`unittest.SkipTest` and every aliased spelling, because `_is_banned` matches a
trailing attribute path rather than a literal. Introducing one is a test
failure, not a style note.

`VCOWS_GATES` (`tests/conftest.py`) turns a named gate's skip into a failure.
Seven names, a closed set: `tofu`, `image`, `rig`, `pycdlib`, `libvirt`, `smoke`,
`proxmox`, plus `all`. It is case-sensitive and does not strip whitespace, so
`VCOWS_GATES="tofu, image"` silently demands only `tofu`. The list is `KNOWN` in
`tests/test_gates.py`, not this sentence: a name absent from it is a test
failure. `smoke` was the sixth, added when `#122` moved
`scripts/smoke-libvirt.sh`'s assertions into `tests/test_libvirt_smoke.py`;
`proxmox` was the seventh, and it needs `VCOWS_PVE_ENDPOINT` **and**
`PROXMOX_VE_API_TOKEN`, because a gate that can name a cluster it cannot
authenticate to answers nothing.

## Do not cite line numbers

Anchor a reference to a name -- a symbol, a function, a heading, a variable --
never to `file:NN`. A line number drifts on every insertion above it, nothing
checks that it still points where it claims, and every correction so far has
been a renumbering rather than a removal of the fragility. A name survives the
edit that moves it.

## Commits, and how work ships

Imperative, sentence-length subject. The body says what was **measured**, and
corrects prior wrong claims by name. No `Co-Authored-By` trailer.

Branch off `master`, one commit per issue, `just check` before pushing, one PR,
squash-merge, and close by number from the commit body (`Closes #NN`). Related
issues that touch the same file land as one branch rather than piecemeal.

## `.claude/` holds two files with opposite tracking status

`.claude/settings.json` is committed: hooks and `permissions.deny`, reviewable
like every other gate here. `.claude/settings.local.json` is the machine-local
`allow` list and is **not** tracked -- it is ignored by `~/.config/git/ignore`,
not by anything in this repo, so `.gitignore` will not tell you and a clean
`git status` is not evidence that it is unmodified. `git check-ignore -v` is.

The consequence is that a change confined to the local file leaves no artifact
to review and cannot close an issue from a commit body.

## `docs/cve-baseline.json` is a differential gate, not a list to append to

Each group carries `why` and `recheck`, and every accept or reject turns on
**reachability**, not severity. The x/crypto acceptance rests on
`orchestrator/backends/libvirt/render.py` passing `sshcmd` to
`connection_uri`: that dialer execs OpenSSH and never enters `x/crypto/ssh`.

**`scripts/image-scan.sh`'s `--write-baseline` destroys that rationale.** It
regenerates a fresh object carrying only `image`, `generated`, `note` and
`accepted`, discarding every `why` and `recheck` in the file. Accepting a new
finding is a hand-edit. Do not reach for the flag to make a scan pass.

`.claude/settings.json` denies that command, but the matcher is a **prefix, not a
pattern** -- measured: a leading `*` matches nothing, and `X:*` breaks on an
argument boundary rather than a character one. So the rule covers the spellings
used from the repo root and nothing else. An absolute path, or a new `just`
recipe wrapping the flag, routes around it silently. It is a guardrail against
the obvious helpful action, not a boundary.

## Seven pins nothing can automate

* `BASE_DIGEST` — `Containerfile`
* `TOFU_RPM_SHA256` — `Containerfile`
* `PROVIDER_SHA256` — `Containerfile` (libvirt provider)
* `PVE_PROVIDER_SHA256` — `Containerfile` (Proxmox provider)
* `PROXMOXER_SHA256` — `Containerfile` (the one pip-installed dependency)
* the `h1:` hash — `docs/provider-0.9.8.lock.hcl` (libvirt)
* the `h1:` hash — `docs/provider-0.111.1.lock.hcl` (Proxmox)

No update bot can recompute any of them, which is why `dependabot.yml` is
deliberately not pointed at the `Containerfile`. `just verify-provider` is the
gate that catches a bump left half-finished.

**The lock filename carries the version and not the provider**, so two providers
that ever pin the same version string collide. 0.9.8 and 0.111.1 do not, and
renaming the scheme is filed rather than done. `verify-provider.sh` asserts every
backend module has a committed lock, so the collision would surface as a diff
rather than as a silently shared file.

**A mirror is built one provider at a time and merged, never mirrored into a
shared directory.** Measured against tofu 1.12.6: pointing `providers mirror` at
a directory that already holds another provider rewrites that provider's index
and drops its `zh:` hash, which is the only cross-check that the artifact in the
mirror is the one `PROVIDER_SHA256` asserts. `scripts/mirror.sh` says so where it
matters.

## Already evaluated and rejected

grype, tflint, checkov, terraform-docs, bandit, and making `VCOWS_GATES=all` the
default were each investigated and rejected with measurements, as were several
plausible-looking simplifications. Proposing one again is re-deriving settled
work. The reasoning lives in `docs/tooling-*.md`, `docs/review-*/` and
`docs/findings.md` — read the rejection before reopening it.

The governing constraint, from the review brief: a previous implementation of
this tool was built and discarded because it became sprawling. **Unjustified
surface area is a defect here, not a matter of taste.** A fix that adds more
surface than the defect warrants is itself a problem.

## Commands

| | |
|---|---|
| `just dev-env` | The only correct venv |
| `just lint` | Seven gates: ruff check, ruff format, hadolint, tofu fmt, shellcheck, workflows, gitleaks |
| `just typecheck` | `ty check` |
| `just check` | lint, typecheck, test |
| `just verify-provider` | Catches a half-finished provider bump |
| `just image`, `just scan`, `just bundle` | Build, scan against the baseline, assemble the delivery bundle |
