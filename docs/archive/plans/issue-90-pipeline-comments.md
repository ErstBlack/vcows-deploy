# Issue #90, the two items lane `workflow-gate` owns

**#90 lists eleven statements that are not true. This lane owns two of them** —
the ones in `.github/workflows/image.yml` and `.gitlab-ci.yml`. The other nine
live in `docs/cve-baseline.json`, `scripts/lib.sh`, `container/entrypoint.py`,
`orchestrator/backends/libvirt/{destroy.py,tofu/variables.tf}`,
`orchestrator/cli.py` (twice) and `docs/findings.md`, plus the stale baseline row.
None of those is touched here, and #90 cannot be closed by this branch.

Reverified at `aed962d`. #90's line numbers were verified at `672a500`; where they
have moved, this says so. Raw transcript:
`docs/review/workflow-gate/reverify/RX-E4.txt`.

---

## Item 1 — `.github/workflows/image.yml:60-64`, `fetch-depth: 0`

### The claim, verbatim at HEAD

```
60	      # Full history: image-build.sh computes the -dirty suffix from
61	      # `git status --porcelain` over the shipped paths.
62	      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7
63	        with:
64	          fetch-depth: 0
```

### Verdict: #90 is right, and its *reason* is right for a reason it does not give

#90 says two git calls exist in the build path, at `lib.sh:134` and `:135`, and
that a `--depth 1` clone gives the full 40-hex SHA and working porcelain. Both
re-measured.

**Correction to #90's citation.** `:134` still holds. The second call is at
**`:140`** at HEAD, not `:135` — `454ee7c` inserted the five-line SC2312 comment
at `:135-139`, which moved it:

```
$ grep -n 'git -C' scripts/lib.sh
134:    sha="$(git -C "$REPO" rev-parse HEAD)"
140:    dirt="$(git -C "$REPO" status --porcelain -- "${ship[@]}")"
```

A repo-wide sweep confirms there are only those two. The two other matches are
prose: `bundle.sh:15` and `lint.sh:174` are both comments.

**The `--depth 1` measurement, run rather than reasoned about.** A shallow clone
of `lane/workflow-gate`, then `source_revision` sourced and called inside it:

| scenario | shallow clone | full clone |
|---|---|---|
| `rev-parse HEAD` | `aed962d48ee85641ef9580515b698c50881a271d`, 40 hex | identical |
| clean tree | `aed962d…271d` | identical |
| `Containerfile` modified | `aed962d…271d-dirty`, with the warning | — |
| `docs/findings.md` modified | `aed962d…271d`, correctly no suffix | — |
| commits present | 1, `is-shallow-repository=true` | 27 |

Byte-identical output. So the comment's premise is false in a way #90 states but
does not spell out: **`git status --porcelain` reads the index and the worktree
and never reads history at all.** It is not that one commit happens to be enough;
history is not an input. The same is true of `rev-parse HEAD`, which resolves a
ref. `fetch-depth: 0` is doing nothing for either call.

### Should the setting change too? No, and #90 is right to say so

Measured, three runs each, local `file://` clone of the 8.7 MB object store:
`--depth 1` 283-314 ms, full 396-429 ms. About 100 ms on a job whose ceiling is
30 minutes and whose last 25 runs are 2m36s-2m54s. There is nothing to win.

The argument for touching the setting is that a justification nobody can state is
the thing that gets copied forward — `scheduled.yml:47-48` already carries the
same `fetch-depth: 0` with **no comment at all**, which is that copy having
already happened once. But removing it is a behaviour change to a green pipeline
in service of 100 ms, and it puts back the possibility that some later step wants
history. Correcting the comment costs nothing and removes the false claim, which
is the entire defect. Keep `fetch-depth: 0`.

### What the comment should say

Two things in the current text are wrong: `image-build.sh` does not compute the
suffix (`lib.sh:129` `source_revision` does; `image-build.sh:38` and
`bundle.sh:61` both call it), and `git status --porcelain` does not need history.

Proposed replacement for `:60-61`, same two lines:

```
      # Not needed by anything measured: source_revision (lib.sh:129) calls only
      # `rev-parse HEAD` and `status --porcelain`, and a --depth 1 clone gives
      # both. Kept because full history costs ~100ms on this repo and a shallow
      # default is the harder thing to notice going wrong.
```

That is four lines rather than two. If the shorter form is preferred, the
minimum honest version is one line: `# Full history. Nothing in the build path
needs it; see docs/archive/plans/issue-90-pipeline-comments.md.` — but this repo's
convention is to carry the reason in the file, not a pointer, so the four-line
form is the recommendation.

`scheduled.yml:47-48` should get the same treatment or a one-line pointer at
`image.yml`, so the two do not drift apart again. #90 does not mention
`scheduled.yml`; the review's RX-E11 does.

---

## Item 2 — `.gitlab-ci.yml:10-18`, the missing artifact-size assumption

### What `:10-18` actually is

Not "the artifact comment" — it is the **runner-assumptions block**:

```
10	# Runner assumptions, stated because getting them wrong is how a pipeline hangs
11	# pending rather than failing:
12	#   - `linux`  a Docker-executor runner that can reach the package mirrors and
...
17	# Every job is tagged. An untagged job on an instance with no matching runner
18	# hangs pending forever instead of failing, which is the worst of both.
```

#90's citation reads as if `:10-18` carried the defect. It does not — the block
is correct as far as it goes. The defect is an **omission**: an instance setting
that will fail these jobs is not listed beside the two that are. The finder is
unambiguous about this (`finders/E-build-pipeline.md` RX-E10: "name the setting in
the runner assumptions at `.gitlab-ci.yml:10-18`"), and `verify/E-lows.md` adds
"beside the tag assumptions that are already there for the same reason".

#90's other two citations, `:131-135` and `:161-165`, are the two `artifacts:`
blocks at `672a500`. **At HEAD they are `:165-169` and `:195-199`** — `a3068e3`'s
`smoke` job added 34 lines above them.

### Re-measured size

```
$ du -sh .cache/delivery ; du -sb .cache/delivery
153M	/home/ssullivan/vcows-deploy/.cache/delivery
160195828	/home/ssullivan/vcows-deploy/.cache/delivery

150804892  vcows-deploy-0.1.0.0-<sha>.tar.gz
  7024523  sbom.spdx.json
  2365961  trivy.json
      376  SHA256SUMS
       76  image.tar.sha256
```

160,195,828 bytes = 152.8 MiB, which is `du -sh`'s `153M`. #90's "153 MiB" and
"151 MB tarball alone" both check out (150,804,892 bytes = 150.8 MB decimal).
The finder's earlier "156 MB" was a different build.

### Correction to the cap's framing

#90 and RX-E10 both call the 100 MB default a **per-file** cap. It is not.
`docs.gitlab.com/administration/cicd/limits/`, fetched 2026-08-31:

> "Each artifact file in a job has a default maximum size of 100 MB."
> "…applies to the size of the final archive file, not individual files in a job."

So the comparison is 100 MB against the **whole 160 MB directory**, not against
the 151 MB tarball alone. The conclusion is unchanged and marginally stronger:
even splitting the tarball out would not bring the job under the cap.

This is documentation, not a measurement. `.gitlab-ci.yml` has never been
executed, the instance does not exist yet, and `grep -n 'GIT_DEPTH\|GIT_STRATEGY\|max_size'`
over the file returns nothing, so the file states no opinion either way.

### The GitLab/GitHub asymmetry is measured, not assumed

`.github/workflows/image.yml:90-94` uploads the identical `.cache/delivery/`
directory, and the last eight `image` workflow runs are eight successes
(`33359240426`, `33357163314`, `33357112690`, and five PR runs), 2m36s-2m54s each.
GitHub accepts the upload. The problem is specific to GitLab's instance setting.

### What the comment should say

Add a third assumption to the `:10-18` block, in the same shape as the two that
are there:

```
#   - artifacts  the instance's "Maximum artifacts size" must exceed ~160 MB. The
#                documented default is 100 MB, measured against the final archive
#                rather than per file, and .cache/delivery/ is 153 MiB (a 151 MB
#                tarball plus a 7 MB SBOM and a 2.3 MB report). Below that, the
#                `image` and `rebuild-scan` jobs do all their work and then lose
#                the artifact the comment at :162-164 calls "what makes the rest
#                of this job worth running". Never executed, so this is from
#                GitLab's documentation, not from a run.
```

Two things it must not become. **Do not add `artifacts:exclude` or split the
bundle** — the bundle is the deliverable and splitting it is the packaging bug
`bundle.sh:10-19` exists to have prevented (`verify/E-lows.md` says this in as
many words). **Do not set `artifacts:max_size` in the file**; it is not a job-level
key, and the cap is an instance setting the file cannot raise. The whole fix is a
comment.

---

## Verification for both items

Comments only. `just lint` must stay green — and this is not vacuous for item 2,
because `scripts/lint.sh`'s `workflows_carry_no_logic` parses `.gitlab-ci.yml`
with PyYAML, so a malformed comment block breaks the parse and fails the gate.
`just check` → six lint gates ok, `ty` clean, 411 passed / 25 skipped.

Nothing else is expected to move: `image.yml`'s and `scheduled.yml`'s command
counts (8 and 7) and `.gitlab-ci.yml`'s (17) are unchanged by a comment edit, and
re-running the M2 harness in the transcript is the cheap way to confirm it.

## Non-goals

* The nine other #90 items. Different files, different lanes.
* The `fetch-depth: 0` **setting**, in either workflow. §Item 1 records the
  measurement that says leave it.
* `artifacts:` keys, `expire_in`, `exclude`, or any split of the delivery bundle.
* Issue #82's gate defect, planned separately in `docs/archive/plans/issue-82.md`.
