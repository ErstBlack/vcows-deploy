# CI

Written 2026-08-29. Two pipelines run the same recipes: `.github/workflows/` is
live, `.gitlab-ci.yml` is inert until the project moves to a self-hosted GitLab
instance.

## The rule

**Neither pipeline contains logic.** Every step is `just <recipe>`, or one of the
two bootstrap scripts that have to run before `just` exists on a fresh runner:
`scripts/os-deps.sh` brings `curl` and `unzip`, `scripts/install-tools.sh` brings
`just` itself.

`scripts/lint.sh` asserts this by parsing both files and rejecting any command
outside that allowlist. It is parsed rather than grepped because a GitLab job
puts its commands in a list *under* `script:`, so a line-oriented check would see
only the key and pass while the commands beneath it did anything at all.

The point is the migration: when the repository moves, `rm -rf .github/` loses
nothing, because nothing lives there.

## What runs

| Job | When | What |
|---|---|---|
| `check` | PRs/MRs and master pushes | `just check` — ruff, ruff format, hadolint, shellcheck, workflows, gitleaks, ty, pytest |
| `mutation` | PRs/MRs and master pushes | `just mutants` — differential against `docs/mutation-baseline.json` |
| `smoke` | PRs/MRs and master pushes | `just smoke-libvirt` — the create path run against a real libvirtd, then destroyed |
| `image` | master, tags, and PR/MRs touching the image's inputs | build, `just test-image`, `just scan` |
| `rebuild-scan` | monthly schedule | rebuild and scan; never blocks |

The `When` column describes GitHub, where `push` is scoped to `branches: [master]`
(`.github/workflows/ci.yml`'s `on:` block) -- so a feature branch with no PR open
runs no `check` at all, and `workflow_dispatch` is the only branch-side trigger.
It exists for exactly that. GitLab's `check` carries no `rules:`, so there it
also runs on every push.

The `mutation` job asks what the coverage floor cannot: the suite *runs* this
line, but would it notice the line being wrong? Measured at the current tree:
3850 mutants, 3176 killed, **674 survived** and none reached by no test at all —
an 82% mutation score against a 97.35% coverage figure. Most of that gap is code
the floor already calls covered.

It is a gate and not a report, which took work. **`mutmut run` exits 0 whatever
it finds** — measured, 964 survivors and exit 0 — so a job that merely called it
would have been green forever, the vacuous pass this repo names elsewhere.
`scripts/mutants.sh` therefore compares against `docs/mutation-baseline.json` and
fails only when `survived` or `no_tests` rises, the same differential shape
`scripts/image-scan.sh` uses against the CVE baseline. Red means the change under
review made the suite blinder. A drop is reported, not failed, with a note that
the baseline is now loose — a ceiling nobody tightens stops being a ceiling.

The mutation score itself is deliberately not gated: the denominator moves when
code is added or deleted, so deleting dead code would "improve" it without a test
being written.

It runs on pull requests rather than on a schedule because it is a question about
a change. Measured at 156s for the full 3835 on 16 cores; a hosted runner has 4,
and one cold run there measured 8m48s, against a 30-minute ceiling.

**`mutants/` is deliberately not cached**, and it was, until #157. The argument
for caching it was that a stale entry is safe: a verdict mutmut recomputes rather
than an artifact it trusts, because it hashes each function and resets whatever
moved. That holds for a change to
*source* and not to *tests*: mutmut hashes source functions, so a test-only
commit leaves every hash identical and inherits every verdict. Measured on the
branch that became #156, which added twenty-two tests and touched no source:
`0.00 mutations/second`, and the survivor count reported was master's.

That run failed, because the real number had gone down. The dangerous direction
is the quiet one — weaken a test, inherit the old verdicts, and the gate is
green, which is the vacuous pass the paragraph above is about arriving through
the cache. Keying on `tests/` would fix GitHub Actions and not GitLab, whose
`cache:key:files` takes at most two files and has both spent on
`pyproject.toml` and `uv.lock`. So both pipelines run cold.

The `gitleaks` gate is inside `just lint`, so it runs in `check` on every PR and
master push rather than as a job of its own.

**`gitleaks dir` does not honour `.gitignore`** — measured with a `ghp_`-shaped
canary planted under `mutants/`, `.venv/` and `.tools/`, all three reported. The
scanned-bytes figure gitleaks prints is misleading here: it counts decoded text,
so `.tools/` at 462 MB barely registers in it and looks skipped when it is not.
Left alone the gate would read ~570 MB of other people's code, and a credential-
shaped test fixture inside a dependency would turn `just lint` red for a secret
this repository neither contains nor can remove. `.gitleaks.toml` excludes those
paths, and `scripts/lint.sh` passes it with `-c` rather than relying on
gitleaks' fourth config source, which is discovered beside the target path and
would silently fall back to the default config if this file moved.

Measured after that: **1.0s over 5.98 MB**, down from 6.9s over 66 MB. The gate
is verified in both directions — a canary in `docs/` fails it, the same canary
in `.venv/` does not. Note that the obvious canary does *not* work: gitleaks
allowlists AWS's published example key, so a test using it passes everywhere and
proves nothing.

`gitleaks git`, which scans history, is deliberately not in the gate: that is a
one-time question, and a leak found there stays found after the file is removed,
which is an always-red gate rather than an actionable one.

`image` is not gated to post-merge because `tests/test_image.py` is the only
thing exercising the air-gap properties — every case run under `--network=none`,
and the RPM binding and every pip-installed dependency importable by the
interpreter that actually runs. Validating a
`Containerfile` change only after review has passed on it is the wrong order.

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
create and the teardown; `tests/test_libvirt_smoke.py` holds every assertion,
behind `VCOWS_GATES=smoke`, and the script invokes it twice — once with the
domain running and once after destroy. That split is `#122`, and it is why this
job runs `just dev-env` where it used to skip it.

The domain runs under TCG, so **no `/dev/kvm` is required** and the job behaves
the same on a GitHub-hosted runner and a GitLab.com SaaS one. Getting there needs
a two-attribute override — `<domain type='qemu'>` and no `<cpu>` element —
applied to a *copy* of `create.DOMAIN_XML` held by that one interpreter; the file
on disk is untouched, and each substitution is checked against the text it names
so an override that no longer matches `create.py` stops the gate rather than
silently doing nothing. `scripts/smoke-libvirt.sh` carries the reasoning.

**It boots no guest and observes no guest address.** The domain reaches firmware
and stops. `docs/archive/acceptance.md` defect 5 — guests healthy on the wrong addresses
— is outside what this gate can see.

It is not the rig gate and does not touch it: `tests/test_libvirt_rig.py` and
`VCOWS_RIG_URI` stay a named skip against real hardware, a real pool and a real
golden image. `smoke` creates a 64 MiB throwaway qcow2 of its own.

It is also not in `just check`. It installs packages, writes `/etc/libvirt` and
starts a system daemon, none of which belongs in a recipe a developer runs before
pushing or in the hook that runs on every agent turn.

Measured on `ubuntu-latest`: the domain defines and starts as `<domain
type='qemu'>` on a runner where nothing has touched `/dev/kvm`.

**35 assertions, in two pytest runs of 30 and 5** — measured green on run
33439584490, 1m11s for the job. The count this file carried
before `#122` was 28, which was already stale by two commits — `#75`'s repin and
`#111`'s varstore check both added to it. The shell made the same 35 assertions
out of 33 `check` lines, two of which looped.

## Which gates run, and which cannot

`VCOWS_GATES` turns a named gate's skip into a failure. It is comma-separated
with **no whitespace stripping** and is case-sensitive, so `rig,image` is
correct and `rig, image` silently demands only `rig`.

| Gate | In CI | Why |
|---|---|---|
| `image` | demanded, in the image job | needs a built image and podman |
| `pycdlib` | satisfied | a runtime dependency, installed by `just dev-env` |
| `smoke` | demanded, in the smoke job | exported by `scripts/smoke-libvirt.sh`, which builds the host it asserts about |
| `rig` | **never** | needs a reachable hypervisor |

`libvirt` is absent from that table on purpose: it is satisfied everywhere, since
`scripts/os-deps.sh` installs `python3-libvirt` in every job that builds a venv.
The closed set of names is `KNOWN` in `tests/test_gates.py`; this table is which
of them CI supplies.

`VCOWS_GATES=all` is never set. The rig gate needs hardware no hosted runner has;
demanding it would either fail every run or get "fixed" by re-adding a skip,
which is the vacuous-pass pattern the review already recorded once.

## Coverage is a CI gate

It did not used to be. `just test` now passes `--cov`, and `just test` is inside
`just check`, so `pyproject.toml`'s `fail_under = 90` blocks every developer run
and the `check` job with it.

The old argument against this was that the figure depends on which gates ran, so
one threshold across every shape would either sit low enough to be vacuous or
fail honest runs. That reasoning was sound and the measurement does not bear out
the premise. Measured on 2026-09-03 on the ungated suite, which is the `check`
job's shape: **98.33%**, 682 passed and 65 skipped, against a floor of 90.

Two things the floor deliberately does not cover. `container/manifest.py` is
omitted: it shells out to `rpm -qa` and only runs inside
the image, where `test_image.test_the_build_manifest_records_what_shipped`
asserts what it produced. And `VCOWS_GATES` is still never set in CI, so the floor
is a floor on the ungated suite — it does not turn a skip into coverage.

If a legitimate run ever fails this gate, the fix is a test, not a lower number.
A coverage gate that fails honest runs is a coverage gate somebody turns off, and
that argument has not stopped being true; it simply no longer describes this
repo's spread.

## Runner assumptions

GitHub: `ubuntu-latest`, which has podman.

GitLab, once it exists:

- `linux` — a Docker-executor runner that can reach the package mirrors.
  Build-time network only; nothing in CI resembles what the
  air-gapped site runs. The `smoke` job carries the same tag and one further
  requirement no tag expresses: it starts libvirtd and defines a domain, so the
  executor has to be privileged. `scripts/smoke-libvirt.sh` starts the daemons
  directly when `/run/systemd/system` is absent, which is the container case,
  but nothing can give an unprivileged container a tap device.
- `podman` — rootless podman, for the image job. **buildah is not a substitute.**
  It builds this Containerfile fine and even runs containers, but `buildah run`
  does not honour the image `ENTRYPOINT` and mutates the working container
  between calls — and proving `ENTRYPOINT`, `WORKDIR` and per-run isolation is
  precisely why `tests/test_image.py` exists.

Every GitLab job is tagged. An untagged job on an instance with no matching
runner hangs pending forever rather than failing, which is worse than either.

## Caching

The uv cache lives at `.cache/uv` rather than `~/.cache/uv`, because GitLab can
only cache paths inside `$CI_PROJECT_DIR`. `scripts/lib.sh` sets `UV_CACHE_DIR`
unconditionally so a developer box, GitHub and GitLab all use one path.

## Scanning, and what the baseline means

`just scan` fails only on findings absent from `docs/cve-baseline.json`. Red means
*new*. An absolute gate would be red from the first run and muted within a month,
and this repository already has a name for a check that is green by neglect.

The baseline carries a per-group rationale, not a bare list. Every accepted id is
in the Rocky base layer, and the reachability argument is the deciding one:
`CVE-2026-11979` is reached only by running `xmlcatalog --shell`, and nothing in
this tree invokes `xmlcatalog`.

**The re-check trigger is a new `BASE_DIGEST`**, and nothing automates it:
Dependabot watches `uv.lock` and the workflows, not a digest pinned in a
`Containerfile` ARG, and inventing a bespoke poller for one pin is worse than a
step in a runbook. So it is a step in a runbook, and the monthly rebuild-and-scan
is deliberately *not* it: that rebuilds from the same digest and cannot see a
base package change.

## The delivery bundle

`just bundle` assembles what actually ships, from what `just scan` already wrote.
It is the step that was missing: the README described a `podman save | gzip`
tarball that no script produced, while the only concrete artifact was the
uncompressed docker-archive `just scan` writes for trivy and syft to seek.

    just image        # build
    just scan         # trivy against the baseline, plus an SBOM
    just bundle       # -> .cache/delivery/

The bundle holds the compressed image, the SBOM and the trivy report that
describe *that* image, `vcows.sh` with the archive's stored tag substituted for
its `@IMAGE@` placeholder, and a `SHA256SUMS` over all four plus the digest of
the uncompressed archive inside the gzip — so a site can check before or after
decompressing. On receipt:

    ./vcows.sh install

which verifies `SHA256SUMS`, loads the one `vcows-deploy-*.tar.gz` beside it, and
checks the tag is there. The tag comes from `RepoTags[0]` inside `image.tar`
rather than from `image_tag`, so the wrapper names what `podman load` restores
even when the Containerfile has moved since the build.

Compression is `gzip -9 -n`. `-n` drops the stored filename and mtime, so the
same archive always compresses to the same bytes and the digest identifies the
content rather than the moment it was packed.

pigz was tried first and rejected on measurement, which is worth recording
because it is 15x faster and the reason not to use it is invisible until it
bites. Over 12 runs on one host with identical input, `pigz -9 -n` produced two
distinct outputs — 150516700 and 150516701 bytes, one byte apart, both
decompressing to identical content. It is a deflate block-framing difference
that depends on how the parallel block assembly interleaves, and it turned up
about once in a dozen runs: often enough to reach a real delivery, rare enough
that when it does it looks like corruption rather than a known property. An
artifact whose identity is its digest cannot be produced by something that
changes the digest without changing the content. gzip is single-threaded and
emits one deflate stream, so the seam does not exist. The cost is 82s against
5.5s on a 444 MB archive, once per delivery.

**Nothing here is signed.** `SHA256SUMS` catches corruption and a mismatched
pairing, not substitution — the bundle has integrity, not authenticity.

### Why signing was removed

There was a `just sign` built on cosign 3, and it worked: verified end to end
under `unshare -rn` against a local key, with no network. It was removed rather
than kept because it signed `.cache/scan/image.tar` while the README promised a
gzip tarball, so the signed bytes and the delivered bytes were two different
streams both called "the delivery tarball". A site handed both sees a signature
mismatch, which reads as tampering rather than as a packaging bug. No pipeline on
either platform ever called it, so it only ever ran on one developer's box.

It comes back once the artifact above is the thing being signed. That work does
not have to rediscover anything: `docs/research/tooling-2026-08-30.md` section 4.2 has the
cosign 3 API, and `docs/review/2026-08-30/finders/G-build-pipeline.md`'s
"`sign.sh --verify` works with no network" section has the verified air-gapped
reproduction. The short version, because it cost a session to find. `sign-blob` requires `--bundle`, since a bare detached signature
is no longer a complete artifact. `--tlog-upload=false` is refused against the
default signing config, so you need one from `cosign signing-config create` that
names no transparency log. Verification needs `--insecure-ignore-tlog`. Without
all three, signing reaches for the public Rekor log and verification reaches for
TUF metadata at `tuf-repo-cdn.sigstore.dev`, and an air-gapped site gets neither.
Do not add `--offline`; it was deprecated as a no-op in v3.0.3.

## Migrating to GitLab

1. Point the GitLab runners at the repository and give them the `linux` and
   `podman` tags.
2. Create one pipeline schedule: monthly with `REBUILD_SCAN=1`. That is the
   only schedule variable any job reads.
3. Replace Dependabot with self-hosted Renovate, or accept a manual
   `uv lock --upgrade` on a rhythm. Neither can recompute `BASE_DIGEST` or
   `PROXMOXER_SHA256`; both are re-pinned by hand.
4. `rm -rf .github/`.
5. Run `just lint` — the thinness check now covers only `.gitlab-ci.yml`, and
   should still pass.

**`.gitlab-ci.yml` has never been executed.** It is syntax-checked, and every
recipe it calls is exercised by the GitHub pipeline, which is not the same thing.
Expect to fix something on the first run.
