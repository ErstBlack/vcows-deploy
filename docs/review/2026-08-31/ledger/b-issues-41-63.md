# Claims ledger — issues #41–#63

Agent: b · Range: the fifteen closed issues numbered 41–53, 57, 63 · Read at
`origin/master` = `672a500` in the detached worktree · Date: 2026-08-31

Suite at `672a500`, default gates, measured in the worktree:
**411 passed, 25 skipped** in 26.76 s. Every `file:line` below was re-read at
`672a500`; where a commit message cites a different number, the drift is named.

## Verdicts

| issue | verdict | evidence (file:line) | note |
|---|---|---|---|
| 41 | DONE | `orchestrator/backends/libvirt/preflight.py:167`, `:202`, `:477`; `orchestrator/backends/libvirt/destroy.py:234`, `:370`, `:400` | All six now read `# noqa: S314  libvirt's own XMLDesc output; D13, see preflight's module docstring`. Both clauses check out: the D13 reasoning is at `preflight.py:28-33`, and every site parses a real `XMLDesc` return (`:202` takes it as a parameter, and its only production caller is `preflight.py:296`). The S-ruleset census the same commit rewrote is also true — measured `orchestrator/` 6×S314 + 2×S603 + 2×S101, `container/` 1×S603 + 2×S607 + 1×S606, against `pyproject.toml:144-149`. |
| 42 | SUPERSEDED | `orchestrator/backends/libvirt/preflight.py:158-161`; `orchestrator/backends/libvirt/destroy.py:361-363` | Closed by rejection, which the issue body explicitly authorised (*"close this as wontfix — that is a legitimate outcome here"*). No shared iterator; what shipped is a cross-reference comment in each loop naming the twin and the reason. The commit's supporting count is true: `except (libvirt.libvirtError, ET.ParseError)` is a five-site pattern at `preflight.py:168`, `:298` and `destroy.py:235`, `:371`, `:401`, so merging two of five would leave three copies written out anyway. |
| 43 | DONE | `orchestrator/backends/base.py:70-89`; `orchestrator/config.py:181`; `orchestrator/backends/libvirt/schema.py:427` | The production call site the issue's body missed is handled. `problems_from` builds `(at + err.json_path[1:]).removeprefix(".") or root`, and `base.py:80-82` states why — `config._blame_the_filename` (`config.py:159`, dispatching at `:167` on `problem.where != "deployment"`) would silently stop firing without it. One conversion, one format, `json_path` instead of the reimplementation. |
| 44 | PARTIAL | landed: `Containerfile:171`; not landed: `Containerfile:163-165` | `COPY --chmod=0755 container/entrypoint.py` replaced the `COPY` + `RUN chmod` pair. The `printf`-generated `vcows` shim was deliberately left, on the shellcheck-coverage argument the issue itself raised (`scripts/lint.sh:159` runs `-s bash` over `scripts/*.sh` and `.claude/hooks/*.sh`, which an extensionless `#!/bin/sh` file would miss). The issue sanctioned that split. What is missing is the record: the reason lives only in `c124ffe`'s message, and nothing at `Containerfile:161-165` says the `printf` form was reconsidered and kept. |
| 45 | PARTIAL | `CLAUDE.md` exists, 130 lines, all eight facts present; three citations stale at `672a500` | `CLAUDE.md:99-101` cites `Containerfile:45`/`:62`/`:69` for `BASE_DIGEST`/`TOFU_RPM_SHA256`/`PROVIDER_SHA256`. Correct at `059c1ca`; at `672a500` those lines are prose inside the new base-pin comment block and the ARGs are at `:80`, `:97`, `:104` — moved 35 lines by `053869f`, a later commit from the same backlog, which did not update `CLAUDE.md`. `CLAUDE.md:39` cites `scripts/lint.sh:34-77` for `workflows_carry_no_logic`; the function is now `40-114`. `CLAUDE.md:85` cites `scripts/image-scan.sh:92`; the `--write-baseline` branch is at `:93`. Verified still accurate: `pyproject.toml:26-34`, `README.md:7`, `tests/fake_libvirt.py:25`, `tests/conftest.py:7`/`:37`/`:44`/`:61`, the five-name closed set (`tests/test_gates.py:27`), `render.py:61`, `docs/provider-0.9.8.lock.hcl:8`. |
| 46 | DONE | `.claude/settings.json:3-13`; `.claude/hooks/static-gate.sh` | The defect — no automatic static gate after an edit — is gone. The specific `PostToolUse` remedy landed at `26627ad` and was replaced at `053869f` by a `Stop` hook running `just lint` then `just typecheck`; `lint-after-edit.sh` no longer exists. See #57 for the residual gap. |
| 47 | NOT DONE | no artifact at `672a500` | Closed on a machine-local change only, as the issue predicted (*"LSP servers can only be declared by a plugin … this cannot be committed as project config"*). The change is real and I verified it outside the repo: `~/.claude/local-plugins/ty-lsp/.lsp.json` points at `/home/ssullivan/vcows-deploy/.venv/bin/ty server`, `~/.claude/settings.json:52` enables `ty-lsp@local`, and `pyright-lsp` is absent from `enabledPlugins`. None of that is reviewable from the repository, survives this machine, or can be re-verified by anyone else. Nothing in `CLAUDE.md`, `docs/findings.md` or `docs/research/tooling-*.md` records the decision, which departs from this project's own convention of writing rejections and adoptions down. |
| 48 | DONE | `.claude/skills/cve-triage/SKILL.md` (79 lines), `.claude/skills/delivery/SKILL.md` (90), `.claude/skills/provider-bump/SKILL.md` (78) | All three exist with `name` + `description` frontmatter. Spot-checked their load-bearing claims against code: `render.py:61` does pass `sshcmd` to `connection_uri`; `.github/workflows/ci.yml:77` does key the mirror cache on `main.tf` and carries no `restore-keys`. One drift: the delivery skill cites `scripts/bundle.sh:52` for the *"run 'just scan' first, which writes it"* die, which was right at `053869f` and is `:51` at `672a500`. |
| 49 | DONE | `.claude/settings.json:26-32` | Three deny entries, covering `./scripts/`, `scripts/` and `bash scripts/` spellings of `image-scan.sh --write-baseline`. Committed rather than machine-local, which is the stronger of the two options the issue left open. The limit is stated rather than implied, in `CLAUDE.md` and in `26627ad`'s body: the matcher is a prefix, a leading `*` matches nothing, so an absolute path or a `just` wrapper routes around it. Confirmed no recipe wraps it: `grep -n write-baseline justfile` is empty. |
| 50 | NOT DONE | no artifact at `672a500` | The change is real and machine-local. `.claude/settings.local.json` is untracked — `git check-ignore -v` names `~/.config/git/ignore:1`, not anything in this repo. Read directly: `Bash(just:*)` is gone and the nine named recipes are present (`lint`, `fix`, `typecheck`, `test`, `test-tofu`, `check`, `verify-mirror`, `verify-provider`, `--list`), with `image`, `scan`, `mirror`, `bundle`, `tools`, `os-deps`, `test-image`, `mutants`, `dev-env` off the list. `CLAUDE.md` records the tracking-status consequence, which is the only trace in the repo; the allowlist itself is not reviewable and closes no issue from a commit body. |
| 51 | DONE | `.claude/hooks/session-probe.sh:1-36`, wired at `.claude/settings.json:14-24` | Silent on the healthy path, exits 0 unconditionally, two checks and no second copy of `CLAUDE.md`. The issue's second check was wrong and was corrected rather than applied: it asked for `.tools/bin/tofu` missing, which would have printed on every start here; `:32` resolves `tofu` on the PATH `scripts/lib.sh` builds instead, and `:27-31` says why. |
| 52 | NOT DONE | no artifact at `672a500` | Machine-local only, and the issue said so up front (*"nothing here becomes a commit"*). Verified outside the repo: `~/.claude/settings.json` `enabledPlugins` has `context7@claude-plugins-official` and no GitHub MCP server, no `serena`, no `pyright-lsp`; `mcpServers` is empty. Note the resolution differs from what the issue proposed — the issue asked to *leave serena enabled and settle it at #30*; it was uninstalled instead. That reversal is argued in the closing comment, not in the repo, so it is not re-derivable here. |
| 53 | DONE | `Containerfile:53-79`; `.github/workflows/scheduled.yml:16-27` | The gap the issue named — a pin nothing rechecks and a monthly job that cannot notice — is recorded at the ARG with the mechanism (`:10` floats across minors: 10.0 2025-06-06, 10.1 2025-11-16, 10.2 2026-05-26), a **recheck by 2026-12**, and the one-line `skopeo inspect --no-tags` that answers it. The scheduled cron comment now says the base layer does not drift and that this job is not the control for it. Renovate rejected in writing at `:72-76`; the UBI question settled by measurement at `:44-51`. The digest itself was checked current, so no re-pin was needed. |
| 57 | PARTIAL | `.claude/hooks/static-gate.sh:52-59` | The Bash hole is closed by construction — `Stop`, no matcher, exit 2 to keep the turn going, and a content signature rather than the `git status --porcelain` guard the issue proposed (the commit is right that porcelain is blind to a second edit of an already-modified file). What is missing: the signature grep is `\.(py\|sh\|tf\|ya?ml)$\|(^\|/)Containerfile$`, and `.hcl` is not in it, so `tests/libvirt-module.tftest.hcl` is outside the 70-file set. `tofu fmt -check -recursive` does read that file — measured on a scratch copy, a misformatted `x.tftest.hcl` gives exit 3 — so an edit confined to it leaves the signature unchanged, the cached verdict is returned, and `just lint` never runs. That is `conftest.py:7`'s shape inside the gate written to close it. The header comment at `:47-49` claiming "Every file the six gates read" is wrong by the same one extension. |
| 63 | DONE | `tests/test_image.py:257-278`, `:298-302` | `SHIPPED` and `head_if_clean` are gone; `built_revision()` shells `source scripts/lib.sh && source_revision`, so the test can no longer hold a stale copy of the path set and the hardcoded `docs/provider-0.9.8.lock.hcl` is now interpolated from `provider_version()`. The `None` branch went with it, so the assertion always runs. |

## Overclaims

Nothing in this range claims a landed fix that is not there. Three smaller
accuracy problems, each named with its commit:

* **`053869f`** (#48, #53, #57) moved `Containerfile`'s three pinned ARGs down 35
  lines and did not update `CLAUDE.md:99-101`, which `059c1ca` had just written
  and which cites them by line. Two commits from the same backlog, and the file
  whose whole purpose is to be the pointer of record is now wrong in three
  places. Same commit's body cites `scripts/image-scan.sh:92-100` for the
  `--write-baseline` regeneration; it is `:93-101`.
* **`c124ffe`** (#32, #28, #44) cites `container/entrypoint.py:227` for the
  `os.execv` that proves the shim's `"$@"` forwarding is exercised. The claim is
  true — the exec is real and `tests/test_image.py:121-129` drives it — but it is
  at `:256`. `:227` is inside an unrelated `OSError` message.
* **`053869f`**'s delivery skill cites `scripts/bundle.sh:52`, correct when
  written and `:51` at `672a500` after `2b20608`.

## Fixes that followed a wrong remedy

**None in this range.** In each of the three documented traps the implementer
measured the remedy and declined it, in writing:

* **#63.** The issue asked that `scripts/lib.sh:124-125` — *"Both path filters
  already treat them as image inputs"* — be corrected. `3be2c28` read it as
  referring to the two **CI** path filters, not to `test_image.py`, found the
  sentence true, and left `lib.sh` untouched. Re-verified at `672a500`:
  `.github/workflows/image.yml:22-23` and `.gitlab-ci.yml:102-103` both list
  `Containerfile` and `.containerignore`. Making the "fix" would have replaced a
  true sentence with a false one.
* **#43.** The issue's body asserted that top-level keys are unaffected by the
  unification. `9f8c442` found they are — by a leading dot, not by brackets —
  and that `config._blame_the_filename` dispatches on the exact string
  `"deployment"`, so a naive port would have silently stopped the filename ever
  being blamed. The `removeprefix(".")` at `base.py:86` is there because of that,
  and `:80-82` says so.
* **#51.** The issue's second probe check (`.tools/bin/tofu` missing) would have
  printed on every session start on this machine. `26627ad` measured it and
  substituted a PATH resolution.

One related item that is *not* a wrong-remedy fix but is worth carrying: **#41's
replacement comments are themselves true.** I checked each of the six sites, the
D13 attribution, and the `preflight.py:28-33` docstring they point at. The false
packaging claim does still exist in the repository at
`docs/research/tooling-2026-08-29.md:289` and `:465`, left there on the arbitration that
dated records may go stale — but left *unannotated*, so a reader of that file
gets the false statement with nothing marking it as superseded.
