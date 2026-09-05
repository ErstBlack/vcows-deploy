# CI

Two pipelines run the same recipes: `.github/workflows/` is live, `.gitlab-ci.yml`
is inert until the project moves to a self-hosted GitLab instance.

## The rule

**Neither pipeline contains logic.** Every step is `just <recipe>`, or one of the
two bootstrap scripts that have to run before `just` exists on a fresh runner:
`scripts/os-deps.sh` brings the OS packages, `scripts/install-tools.sh` brings
`just` itself.

`scripts/lint.sh`'s `workflows_carry_no_logic` asserts this by parsing both files
and rejecting any command outside that allowlist. It is parsed rather than
grepped because a GitLab job puts its commands in a list *under* `script:`, so a
line-oriented check would see only the key and pass while the commands beneath it
did anything at all.

The point is the migration: when the repository moves, `rm -rf .github/` loses
nothing, because nothing lives there.

## What runs

| Job | Workflow | When | What |
|---|---|---|---|
| `check` | `ci.yml` | PRs/MRs and master pushes | `just check` — ruff, ruff format, hadolint, shellcheck, workflows, gitleaks, ty, pytest |
| `mutation` | `ci.yml` | PRs/MRs and master pushes | `just mutants`, five shards selected by `VCOWS_MUTANTS_SHARD=k/5` |
| `mutation-verdict` | `ci.yml` | after `mutation` | `just mutants-verdict` — the five shards summed, then the gate |
| `smoke` | `ci.yml` | PRs/MRs and master pushes | `just smoke-libvirt` — the create path against a real libvirtd, then destroyed |
| `image` | `image.yml` | master, tags, and PR/MRs touching the image's inputs | `just image`, `just test-image`, `just scan`, `just bundle` |
| `rebuild-scan` | `scheduled.yml` | monthly schedule | `just image`, `just scan`; never blocks |

The `When` column describes GitHub, where `push` is scoped to `branches: [master]`
— so a feature branch with no PR open runs no `check` at all, and
`workflow_dispatch` is the only branch-side trigger. It exists for exactly that.
GitLab's `check` carries no `rules:`, so there it also runs on every push.

`image` gets its own workflow because GitHub applies path filters per workflow.
It is not gated to post-merge: `tests/test_image.py` is the only thing exercising
the air-gap properties — every case run under `--network=none`, and the RPM
binding and every pip-installed dependency importable by the interpreter that
actually runs. Validating a `Containerfile` change only after review has passed on
it is the wrong order.

Neither `image` nor `rebuild-scan` uploads its bundle: the `upload-artifact`
steps carry `if: false`, because the account's artifact storage quota is shared
and a full delivery bundle fills it. Re-enabling `rebuild-scan`'s also needs
`just bundle` restored to that job, or the upload finds nothing and fails.

### The mutation job

It asks what the coverage floor cannot: the suite *runs* this line, but would it
notice the line being wrong? The mutant, killed and survivor numbers are in
`docs/mutation-baseline.json` and nowhere else, so there is one place to correct
when they move.

It is a gate and not a report, which took work. **`mutmut run` exits 0 whatever
it finds**, so a job that merely called it would have been green forever, the
vacuous pass this repo names elsewhere. `scripts/mutants.sh` therefore compares
against the baseline and fails only when `survived` or `no_tests` rises, the same
differential shape `scripts/image-scan.sh` uses against the CVE baseline. Red
means the change under review made the suite blinder. A drop is reported, not
failed, with a note that the baseline is now loose — a ceiling nobody tightens
stops being a ceiling.

The mutation score itself is deliberately not gated: the denominator moves when
code is added or deleted, so deleting dead code would "improve" it without a test
being written.

It runs on pull requests rather than on a schedule because it is a question about
a change. A shard judges nothing on its own; `mutation-verdict` sums the five and
asserts the sum accounts for every mutant mutmut generated, which is what catches
a shard that checked nothing and one whose numbers never arrived. The numbers
travel as job outputs rather than artifacts, for the quota reason above.

**`mutants/` is deliberately not cached.** The argument for caching it was that a
stale entry is safe: a verdict mutmut recomputes rather than an artifact it
trusts, because it hashes each function and resets whatever moved. That holds for
a change to *source* and not to *tests* — mutmut hashes source functions, so a
test-only commit leaves every hash identical and inherits every verdict, at
`0.00 mutations/second`. The dangerous direction is the quiet one: weaken a test,
inherit the old verdicts, and the gate is green. Keying on `tests/` would fix
GitHub Actions and not GitLab, whose `cache:key:files` takes at most two files and
has both spent on `pyproject.toml` and `uv.lock`. So both pipelines run cold.

### gitleaks

The `gitleaks` gate is inside `just lint`, so it runs in `check` on every PR and
master push rather than as a job of its own.

**`gitleaks dir` does not honour `.gitignore`** — measured with a `ghp_`-shaped
canary planted under `mutants/`, `.venv/` and `.tools/`, all three reported. The
scanned-bytes figure gitleaks prints is misleading here: it counts decoded text,
so `.tools/` at 462 MB barely registers in it and looks skipped when it is not.
Left alone the gate would read ~570 MB of other people's code, and a
credential-shaped test fixture inside a dependency would turn `just lint` red for
a secret this repository neither contains nor can remove. `.gitleaks.toml`
excludes those paths, and `scripts/lint.sh` passes it with `-c` rather than
relying on gitleaks' fourth config source, which is discovered beside the target
path and would silently fall back to the default config if this file moved.
Measured after that: **1.0s over 5.98 MB**, down from 6.9s over 66 MB.

The gate is verified in both directions — a canary in `docs/` fails it, the same
canary in `.venv/` does not. The obvious canary does *not* work: gitleaks
allowlists AWS's published example key, so a test using it passes everywhere and
proves nothing.

`gitleaks git`, which scans history, is deliberately not in the gate: that is a
one-time question, and a leak found there stays found after the file is removed,
which is an always-red gate rather than an actionable one.

## The smoke job, and what it is for

Everything else that reads `orchestrator/backends/libvirt/create.py` reads
`tests/fake_libvirt.py`, which records what it was handed and answers with
objects it invented rather than with anything a daemon parsed. So three things
that fake stands in for have never otherwise run: `virStorageVolUpload`,
libvirtd parsing the domain XML `create.domain_xml` renders, and
define/start/undefine of that domain. `smoke` runs those against a libvirtd
installed on the runner and asserts against what libvirtd created, not against
what was sent. The teardown then goes through the shipped `destroy.destroy`
rather than virsh, so the marker round trip is on the gate too.

The job is two pieces. `scripts/smoke-libvirt.sh` builds the host and drives the
create and the teardown; **every assertion is in `tests/test_libvirt_smoke.py`**,
behind `VCOWS_GATES=smoke`, and the script invokes it twice — once with the domain
running and once after destroy. That split is why this job runs `just dev-env`.

The domain runs under TCG, so **no `/dev/kvm` is required** and the job behaves
the same on a GitHub-hosted runner and a GitLab.com SaaS one. Getting there needs
a two-attribute override — `<domain type='qemu'>` and no `<cpu>` element —
applied to a *copy* of `create.DOMAIN_XML` held by that one interpreter; the file
on disk is untouched, and each substitution is checked against the text it names
so an override that no longer matches `create.py` stops the gate rather than
silently doing nothing. `scripts/smoke-libvirt.sh` carries the reasoning.

**It boots no guest and observes no guest address.** The domain reaches firmware
and stops. `docs/archive/acceptance.md` defect 5 — guests healthy on the wrong
addresses — is outside what this gate can see.

It is not the rig gate and does not touch it: `tests/test_libvirt_rig.py` and
`VCOWS_RIG_URI` stay a named skip against real hardware, a real pool and a real
golden image. `smoke` creates a 64 MiB throwaway qcow2 of its own.

It is also not in `just check`. It installs packages, writes `/etc/libvirt` and
starts a system daemon, none of which belongs in a recipe a developer runs before
pushing or in the hook that runs on every agent turn.

## Which gates run, and which cannot

`VCOWS_GATES` turns a named gate's skip into a failure. It is comma-separated
with **no whitespace stripping** and is case-sensitive, so `rig,image` is correct
and `rig, image` silently demands only `rig`. The closed set of names is `KNOWN`
in `tests/test_gates.py`, and README's "Test gates" says what each one needs.

CI supplies three of the six. `image` is demanded in the image job, which builds
the image and has podman; `smoke` is demanded by `scripts/smoke-libvirt.sh`,
which builds the host it asserts about; `libvirt` and `pycdlib` are satisfied
everywhere, because `scripts/os-deps.sh` installs `python3-libvirt` in every job
that builds a venv and `pycdlib` is a runtime dependency `just dev-env` brings.
`rig` and `proxmox` are never supplied: one needs a reachable hypervisor and the
other a reachable Proxmox cluster with a token, and no hosted runner has either.

`VCOWS_GATES=all` is therefore never set. Demanding it would either fail every
run or get "fixed" by re-adding a skip, which is the vacuous-pass pattern the
review already recorded once.

## Coverage is a CI gate

`just test` passes `--cov`, and `just test` is inside `just check`, so
`pyproject.toml`'s `fail_under = 90` blocks every developer run and the `check`
job with it.

**Nothing is omitted.** `container/manifest.py` was, because `packages()` shells
out to `rpm -qa` and `main()` is the assembly around it, and mutation testing
then found 127 mutants in the file that no test reached at all — more than the
rest of both packages put together. `tests/test_manifest.py` fakes
`subprocess.run` and drives all three;
`test_image.test_the_build_manifest_records_what_shipped` is still what asserts
the file real `rpm` produced, behind the image gate.

The floor sits below the measured figure on purpose, because the figure depends
on which gates ran: the rig, proxmox and image tests skip on the `check` job and
it legitimately covers less. A floor assuming the best case would fail honest
runs, which is how a coverage gate gets turned off. If a legitimate run ever
fails this gate, the fix is a test, not a lower number.

## Runner assumptions

GitHub: `ubuntu-latest`, which has podman.

GitLab, once it exists:

- `linux` — a Docker-executor runner that can reach the package mirrors.
  Build-time network only; nothing in CI resembles what the air-gapped site runs.
  The `smoke` job carries the same tag and one further requirement no tag
  expresses: it starts libvirtd and defines a domain, so the executor has to be
  privileged. `scripts/smoke-libvirt.sh` starts the daemons directly when
  `/run/systemd/system` is absent, which is the container case, but nothing can
  give an unprivileged container a tap device.
- `podman` — rootless podman, for the image and rebuild-scan jobs. **buildah is
  not a substitute.** It builds this Containerfile fine and even runs containers,
  but `buildah run` does not honour the image `ENTRYPOINT` and mutates the
  working container between calls — and proving `ENTRYPOINT`, `WORKDIR` and
  per-run isolation is precisely why `tests/test_image.py` exists.

Every GitLab job is tagged. An untagged job on an instance with no matching
runner hangs pending forever rather than failing, which is worse than either.

## Caching

The uv cache lives at `.cache/uv` rather than `~/.cache/uv`, because GitLab can
only cache paths inside `$CI_PROJECT_DIR`. `scripts/lib.sh` sets `UV_CACHE_DIR`
unconditionally so a developer box, GitHub and GitLab all use one path.

## Scanning, and what the baseline means

`just scan` is differential: it fails only on findings absent from
`docs/cve-baseline.json`. Red means *new*. An absolute gate would be red from the
first run and muted within a month, and this repository already has a name for a
check that is green by neglect.

The baseline is hand-edited. Each group carries its own `why` and `recheck`, and
`scripts/image-scan.sh`'s `--write-baseline` discards every one of them — so
accepting a finding is an edit, never that flag. The reachability argument the
acceptances turn on is in the baseline's `rationale`, and the procedure is in
`.claude/skills/cve-triage`.

**The re-check trigger is a new `BASE_DIGEST`**, and nothing automates it:
Dependabot watches `uv.lock` and the workflows, not a digest pinned in a
`Containerfile` ARG, and inventing a bespoke poller for one pin is worse than a
step in a runbook. So it is a step in a runbook, and the monthly rebuild-and-scan
is deliberately *not* it: that rebuilds from the same digest and cannot see a base
package change.

## The delivery bundle

`just bundle` assembles what ships, out of what `just scan` already wrote. README
"Delivering it" describes the artifact and what a site does with it; what is
CI-specific is that the `image` job runs `just image`, `just test-image`,
`just scan` and `just bundle` in that order, and that the upload of the result is
disabled.

**Nothing here is signed.** `SHA256SUMS` catches corruption and a mismatched
pairing, not substitution — the bundle has integrity, not authenticity.

### Why signing was removed

There was a `just sign` built on cosign 3, verified end to end under `unshare -rn`
against a local key with no network. It was removed rather than kept for two
reasons: it signed the uncompressed `.cache/scan/image.tar` while the delivery is
a gzip, so the signed bytes and the delivered bytes were two different streams
both called "the delivery tarball" and a site handed both reads the mismatch as
tampering; and no pipeline on either platform called it, so it only ever ran on
one developer's box.

It comes back once the artifact above is the thing being signed, and that work
does not have to rediscover the API — `docs/research/tooling-2026-08-30.md`
section 4.2 has it. The short version, because it cost a session to find:
`sign-blob` requires `--bundle`, since a bare detached signature is no longer a
complete artifact; `--tlog-upload=false` is refused against the default signing
config, so you need one from `cosign signing-config create` that names no
transparency log; and verification needs `--insecure-ignore-tlog`. Without all
three, signing reaches for the public Rekor log and verification for TUF metadata
at `tuf-repo-cdn.sigstore.dev`, and an air-gapped site gets neither. Do not add
`--offline`; it was deprecated as a no-op in v3.0.3.

## Migrating to GitLab

1. Point the GitLab runners at the repository and give them the `linux` and
   `podman` tags.
2. Create one pipeline schedule: monthly with `REBUILD_SCAN=1`. That is the only
   schedule variable any job reads.
3. Replace Dependabot with self-hosted Renovate, or accept a manual
   `uv lock --upgrade` on a rhythm. Neither can recompute `BASE_DIGEST` or
   `PROXMOXER_SHA256`; both are re-pinned by hand.
4. `rm -rf .github/`.
5. Run `just lint` — the thinness check then covers only `.gitlab-ci.yml`, and
   should still pass.

**`.gitlab-ci.yml` has never been executed.** It is syntax-checked, and every
recipe it calls is exercised by the GitHub pipeline, which is not the same thing.
Expect to fix something on the first run.
