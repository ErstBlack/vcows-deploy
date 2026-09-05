# Phase 0 — evidence baseline, 2026-08-30

HEAD `6497f30`, tree clean. All figures reproduced on this machine.

| Gate | Result | File |
|---|---|---|
| Suite, default gates | 379 passed, 25 skipped, exit 0 | `00-pytest-default.txt` |
| `just lint` (ruff, ruff format, hadolint, tofu fmt, shellcheck, workflows-carry-no-logic) | all gates pass, exit 0 | `01-lint.txt` |
| `just typecheck` (ty) | all checks passed, exit 0 | `02-typecheck.txt` |
| `just image` | built `localhost/vcows-deploy:0.1.0.0`, exit 0 | `03-image-build.txt` |
| Suite + image gate | **389 passed, 15 skipped**, exit 0 | `05-image-gate.txt` |
| PR #1 | MERGEABLE; `check`, `tofu`, `image` all SUCCESS | `04-pr-status.txt` |

The 15 remaining skips are `tests/test_libvirt_rig.py` — they need `VCOWS_RIG_URI` and a
reachable hypervisor. That is the separate environment, deferred by decision on 2026-08-30.
No skip is silent: every one is gate-guarded and named in the summary line.

Baseline is green. The review proceeds.
