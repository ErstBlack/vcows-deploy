# Archived: the OpenTofu subsystem

Material whose whole subject is the `.tf` module deleted in #204 — the
`lane/tofu-module` review and the three plans written against `main.tf` and
`variables.tf`. Kept rather than deleted because the rejections and
measurements in it are still cited: `docs/review/shell-errexit/REVIEW.md`
points here, and `docs/research/tofu-eval-2026-09-02.md` records why the subsystem went.

Nothing here is an instruction. Every `just` recipe it names is gone —
`test-tofu`, `verify-provider`, `mirror`, `sign`, `audit`, `cov`, `deliver`,
`secrets`, `verify-signature` — and so is `VCOWS_GATES=tofu`.

The directory has since taken in the rest of the dated material: every plan
under `docs/archive/plans/`, `docs/archive/acceptance.md`, and
`docs/archive/orchestrator-architecture.md`, the survey this project was built
from. The same caveat covers all of it — a record of one date, not a current
instruction. Reviews live under `docs/review/` and evaluations of alternatives
under `docs/research/`.
