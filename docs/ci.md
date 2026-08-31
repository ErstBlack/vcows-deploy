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
| `check` | PRs/MRs and master pushes | `just check` — ruff, ruff format, hadolint, tofu fmt, shellcheck, ty, pytest |
| `tofu` | PRs/MRs and master pushes | mirror ensured and re-verified, `just verify-provider`, `just test-tofu` |
| `smoke` | PRs/MRs and master pushes | `just smoke-libvirt` — the module applied to a real libvirtd, then destroyed |
| `image` | master, tags, and PR/MRs touching the image's inputs | build, `just test-image`, `just scan` |
| `rebuild-scan` | monthly schedule | rebuild and scan; never blocks |

The `When` column describes GitHub, where `push` is scoped to `branches: [master]`
(`.github/workflows/ci.yml`'s `on:` block) -- so a feature branch with no PR open
runs neither `check` nor `tofu`, and `workflow_dispatch` is the only branch-side
trigger. It exists for exactly that. GitLab's `check` and `tofu` carry no
`rules:`, so there both also run on every push.

There is no mutation-testing job. `just mutants` exists and its configuration is
correct, but `mutmut run` does not complete here (see `pyproject.toml`), and a
scheduled job that always fails is no better than one that always passes.

`image` is not gated to post-merge because `tests/test_image.py` is the only
thing exercising the air-gap properties — the provider resolved from the baked
mirror with no `direct` block, the RPM binding visible to the interpreter that
actually runs, `init` not rewriting the committed lock. Validating a
`Containerfile` change only after review has passed on it is the wrong order.

## The smoke job, and what it is for

`tofu` reads the module through `mock_provider "libvirt" {}`, which satisfies the
pinned provider's schema with generated values. That reaches every expression in
the module and reaches no hypervisor, so three things it stands in for have never
run: `virStorageVolUpload`, libvirtd parsing the domain XML the module renders,
and define/start/undefine of that domain. `smoke` runs those against a libvirtd
installed on the runner and asserts against `virsh dumpxml`, `virsh vol-dumpxml`
and `qemu-img info` — what libvirtd created, not what tofu planned.

The domain runs under TCG, so **no `/dev/kvm` is required** and the job behaves
the same on a GitHub-hosted runner and a GitLab.com SaaS one. Getting there needs
a two-attribute override — `type = "qemu"`, `cpu = null` — written into a *copy*
of the module; the shipped tree is untouched. `scripts/smoke-libvirt.sh` carries
the reasoning, including the measurement that
`TF_PROVIDER_LIBVIRT_DOMAIN_TYPE`, which `docs/tooling-2026-08-30.md` §4.1
credits with the same swap, does not exist in the 0.9.8 provider binary and could
not win against a declared attribute if it did.

**It boots no guest and observes no guest address.** The domain reaches firmware
and stops. `docs/acceptance.md` defect 5 — guests healthy on the wrong addresses
— is outside what this gate can see.

It is not the rig gate and does not touch it: `tests/test_libvirt_rig.py` and
`VCOWS_RIG_URI` stay a named skip against real hardware, a real pool and a real
golden image. `smoke` creates a 64 MiB throwaway qcow2 of its own.

It is also not in `just check`. It installs packages, writes `/etc/libvirt` and
starts a system daemon, none of which belongs in a recipe a developer runs before
pushing or in the hook that runs on every agent turn.

Measured on `ubuntu-latest`: 1m05s for the job, 28 assertions, `Apply complete!
Resources: 4 added` and `Destroy complete! Resources: 4 destroyed`. The domain
defines and starts with `<domain type='qemu'>` on a runner where nothing has
touched `/dev/kvm`.

## Which gates run, and which cannot

`VCOWS_GATES` turns a named gate's skip into a failure. It is comma-separated
with **no whitespace stripping** and is case-sensitive, so `tofu,image` is
correct and `tofu, image` silently demands only `tofu`.

| Gate | In CI | Why |
|---|---|---|
| `tofu` | demanded | the mirror is rebuilt or restored first |
| `image` | demanded, in the image job | needs a built image and podman |
| `pycdlib` | satisfied | a runtime dependency, installed by `just dev-env` |
| `rig` | **never** | needs a reachable hypervisor |

`VCOWS_GATES=all` is never set. The rig gate needs hardware no hosted runner has;
demanding it would either fail every run or get "fixed" by re-adding a skip,
which is the vacuous-pass pattern the review already recorded once.

## Coverage is not a CI gate

`pytest --cov` works and `pyproject.toml` carries a `fail_under`, but neither
pipeline runs it, on purpose. The figure depends on which gates ran: the `check`
job has no provider mirror and so legitimately covers less of `tofu.py` than the
`tofu` job does. One threshold across both would either sit low enough to be
vacuous or fail honest runs, and a coverage gate that fails honest runs is a
coverage gate somebody turns off. Run it locally when the number is the question.

## Runner assumptions

GitHub: `ubuntu-latest`, which has podman.

GitLab, once it exists:

- `linux` — a Docker-executor runner that can reach the package mirrors and the
  OpenTofu registry. Build-time network only; nothing in CI resembles what the
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

The provider mirror is keyed on `orchestrator/backends/libvirt/tofu/main.tf`,
which declares the pin — **not** on `docs/provider-<v>.lock.hcl`, whose filename
carries the version. A bump renames that file, both platforms substitute a
placeholder for a file they cannot find, the key collapses to a constant, and the
result is a stale-mirror hit on exactly the run where the mirror changed. There
is no prefix fallback on the mirror for the same reason.

The uv cache lives at `.cache/uv` rather than `~/.cache/uv`, because GitLab can
only cache paths inside `$CI_PROJECT_DIR`. `scripts/lib.sh` sets `UV_CACHE_DIR`
unconditionally so a developer box, GitHub and GitLab all use one path.

A restored mirror is untrusted input — GitLab overwrites its cache key at the end
of every job — so `just ensure-mirror` re-checks the zip against the
`Containerfile`'s `PROVIDER_SHA256` on every run, not only when building. That
digest is the only out-of-band pin; checking against the mirror's own index would
be circular.

## Scanning, and what the baseline means

`just scan` fails only on findings absent from `docs/cve-baseline.json`. Red means
*new*. An absolute gate would be red from the first run and muted within a month,
and this repository already has a name for a check that is green by neglect.

The baseline carries a per-binary rationale, not a bare list. The load-bearing one:
terraform-provider-libvirt 0.9.8 embeds `golang.org/x/crypto` v0.46.0 and no
released version fixes it, so those nine HIGH findings are accepted on
reachability — the provider is configured with `qemu+sshcmd`, whose dialer execs
the OpenSSH binary and never enters `x/crypto/ssh`, and the CVEs are server-side
or verifier-side flaws that a client never reaches.

**The re-check trigger is a new provider release**, and nothing automates it:
Dependabot watches `uv.lock` and the workflows, not a Go module pinned in a
`Containerfile` ARG, and inventing a bespoke poller for one dependency is worse
than a step in a runbook. So it is a step in a runbook — when the monthly
rebuild-and-scan runs, check whether `dmacvicar/terraform-provider-libvirt` has
released past 0.9.8, and if it has, walk the provider bump through
`just verify-provider` and the schema diff.

## The delivery bundle

`just bundle` assembles what actually ships, from what `just scan` already wrote.
It is the step that was missing: the README described a `podman save | gzip`
tarball that no script produced, while the only concrete artifact was the
uncompressed docker-archive `just scan` writes for trivy and syft to seek.

    just image        # build
    just scan         # trivy against the baseline, plus an SBOM
    just bundle       # -> .cache/delivery/

The bundle holds the compressed image, the SBOM and the trivy report that
describe *that* image, and a `SHA256SUMS` over all three plus the digest of the
uncompressed archive inside the gzip — so a site can check before or after
decompressing. On receipt:

    sha256sum -c SHA256SUMS
    gunzip -c vcows-deploy-*.tar.gz | podman load

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
not have to rediscover anything: `docs/tooling-2026-08-30.md` section 4.2 has the
cosign 3 API, and `docs/review-2026-08-30/finders/G-build-pipeline.md`'s
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
   `uv lock --upgrade` on a rhythm. Neither can recompute the artifact digests in
   the `Containerfile`; those go through the schema-diff runbook by hand.
4. `rm -rf .github/`.
5. Run `just lint` — the thinness check now covers only `.gitlab-ci.yml`, and
   should still pass.

**`.gitlab-ci.yml` has never been executed.** It is syntax-checked, and every
recipe it calls is exercised by the GitHub pipeline, which is not the same thing.
Expect to fix something on the first run.
