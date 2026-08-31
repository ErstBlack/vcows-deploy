# Phase 0 — evidence baseline, 2026-08-31

Measured against `origin/master` `672a500` in a detached worktree, not the working tree:
issue `#21` is in flight there with uncommitted `main.tf` / `libvirt-module.tftest.hcl`
edits and one commit (`183b927`) ahead of master. The worktree carries its own `.venv`
built by `just dev-env`; `.tools/tofu-mirror` was copied in and `.tools/bin` symlinked, so
the pinned provider and tool binaries are the same bytes the main tree uses.

| Gate | Result | File |
|---|---|---|
| Suite, default gates | **411 passed, 25 skipped**, exit 0 | `00-pytest-default.txt` |
| `just lint` (ruff check, ruff format, hadolint, tofu fmt, shellcheck, workflows) | all gates pass, exit 0 | `01-lint.txt` |
| `just typecheck` (`ty`) | All checks passed, exit 0 | `02-typecheck.txt` |
| `just verify-provider` | all provider facts agree, exit 0 | `02-typecheck.txt` |
| `just image` | built `localhost/vcows-deploy:0.1.0.0` (`84dcf01a718d`), exit 0 | `03-image-build.txt` |
| Rig gate alone | **15 passed**, `qemu+ssh://vcows@vcows/system`, libvirt 12.0.0 | — |
| **Suite, `VCOWS_GATES=all` + rig + image** | **436 passed, 0 skipped**, exit 0 | `05-all-gates.txt` |
| `just scan` (live trivy vs `docs/cve-baseline.json`) | no findings outside the baseline, exit 0 | `06-scan.txt` |
| `just bundle` | 144M `.tar.gz` + SBOM + trivy report + `SHA256SUMS`, unsigned, exit 0 | `07-bundle.txt` |

## What is new in this baseline

**Nothing skipped, and nothing was skipped silently.** 436 passed / 0 skipped is the first
all-gates run inside a review. The comparable prior figures: 379/25 default and 389/15 with
the image gate at `6497f30` (2026-08-30 review), and 390/0 on 2026-08-29 outside any review
(`docs/findings.md:404`). The suite has grown by 46 tests since that 390.

**The live scan ran, and it moved one row.** `just scan` reports
`baseline entries no longer found (1 of 100; stale, or fixed by a pin bump): CVE-2026-58055`.
Nothing new appeared outside the baseline. This is dimension E's input and the first time
`docs/cve-baseline.json` has been compared against a real trivy run inside a review —
per `CLAUDE.md`, the remedy is a hand-edit, never `--write-baseline`.

**A first-attempt build failure, and it is an artefact of this rig, not a defect.**
`COPY .tools/tofu-mirror` fails when `.tools` is a symlink out of the build context
(`03-image-build.txt` first run, exit 125). Recorded because a reader will otherwise wonder;
the recorded build is the second, with the mirror copied in.

## Baseline verdict

Green. Every gate that has ever passed here passes, the two gates neither prior review
could run now run clean, and the one moving part is a stale baseline row. **The review
proceeds.**
