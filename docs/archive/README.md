# Archive

A record of what was true at one date, never an instruction. **Every `just`
recipe named in here is gone** — `test-tofu`, `verify-provider`, `mirror`,
`sign`, `audit`, `cov`, `deliver`, `secrets`, `verify-signature` — and so is
`VCOWS_GATES=tofu`.

`review-tofu-module/` and `rhel9-target-tofu.md` are the OpenTofu module,
archived with it; `plans/` holds one plan per closed issue, several written
against `main.tf` and `variables.tf`. `acceptance.md` is the first acceptance run
against real hardware, defects and all, and the format a later one copies.
`orchestrator-architecture.md` is the survey this project was built from, and
`findings-ledger.md` the pre-implementation work ledger and definition of done.

It is kept because it is still cited: `docs/review/shell-errexit/REVIEW.md`
points here, `docs/research/tofu-eval-2026-09-02.md` records why the module went,
and the review lanes cite the ledger's labels.
