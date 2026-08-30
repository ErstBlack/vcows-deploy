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
| `check` | every push and PR/MR | `just check` — ruff, ruff format, hadolint, tofu fmt, shellcheck, ty, pytest |
| `tofu` | every push and PR/MR | mirror ensured and re-verified, `just verify-provider`, `just test-tofu` |
| `image` | master, tags, and PR/MRs touching the image's inputs | build, `just test-image`, `just scan` |
| `rebuild-scan` | monthly schedule | rebuild and scan; never blocks |

There is no mutation-testing job. `just mutants` exists and its configuration is
correct, but `mutmut run` does not complete here (see `pyproject.toml`), and a
scheduled job that always fails is no better than one that always passes.

`image` is not gated to post-merge because `tests/test_image.py` is the only
thing exercising the air-gap properties — the provider resolved from the baked
mirror with no `direct` block, the RPM binding visible to the interpreter that
actually runs, `init` not rewriting the committed lock. Validating a
`Containerfile` change only after review has passed on it is the wrong order.

## Which gates run, and which cannot

`VCOWS_GATES` turns a named gate's skip into a failure. It is comma-separated
with **no whitespace stripping** and is case-sensitive, so `tofu,image` is
correct and `tofu, image` silently demands only `tofu`.

| Gate | In CI | Why |
|---|---|---|
| `tofu` | demanded | the mirror is rebuilt or restored first |
| `image` | demanded, in the image job | needs a built image and podman |
| `pycdlib` | satisfied | it is in the dev group |
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
  air-gapped site runs.
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

## Migrating to GitLab

1. Point the GitLab runners at the repository and give them the `linux` and
   `podman` tags.
2. Create two pipeline schedules: monthly with `REBUILD_SCAN=1`, weekly with
   `MUTANTS=1`.
3. Replace Dependabot with self-hosted Renovate, or accept a manual
   `uv lock --upgrade` on a rhythm. Neither can recompute the artifact digests in
   the `Containerfile`; those go through the schema-diff runbook by hand.
4. `rm -rf .github/`.
5. Run `just lint` — the thinness check now covers only `.gitlab-ci.yml`, and
   should still pass.

**`.gitlab-ci.yml` has never been executed.** It is syntax-checked, and every
recipe it calls is exercised by the GitHub pipeline, which is not the same thing.
Expect to fix something on the first run.
