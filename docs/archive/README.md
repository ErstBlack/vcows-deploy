# Archived: the OpenTofu subsystem

Material whose whole subject is the `.tf` module deleted in #204 — the
`lane/tofu-module` review and the three plans written against `main.tf` and
`variables.tf`. Kept rather than deleted because the rejections and
measurements in it are still cited: `docs/review-shell-errexit/REVIEW.md`
points here, and `docs/tofu-eval-2026-09-02.md` records why the subsystem went.

Nothing here is an instruction. Every `just` recipe it names is gone —
`test-tofu`, `verify-provider`, `mirror`, `sign`, `audit`, `cov`, `deliver`,
`secrets`, `verify-signature` — and so is `VCOWS_GATES=tofu`.

Tofu-era material whose subject still exists stayed in `docs/`, including the
firmware plan `docs/plans/issue-75.md` and every other `review-*` directory.
