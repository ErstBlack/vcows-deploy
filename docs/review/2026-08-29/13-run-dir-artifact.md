# The run directory — review

Agent: 13-run-dir-artifact · Date: 2026-08-29 · Scope: `orchestrator/{cli,tofu}.py`,
`orchestrator/backends/libvirt/{prepare,render,schema}.py`, `container/entrypoint.py`,
`README.md` "The run directory", `docs/findings.md` F12

## Summary

* **As a record it is inverted.** The one outcome where VMs may exist half-created — a
  failed apply — writes no `run.json` and no `inventory.json`; every outcome where nothing
  happened writes one. `manifest.json` is written by no path at all.
* **As a secret store, 0700 is a directory bit and nothing else.** Every file under it is
  umask-governed — 0644 at umask 022, 0666 at umask 000 — the seed ISOs,
  `main.auto.tfvars.json`, `plan.bin` and `terraform.tfstate` alike. Only tofu's own
  `-json-into` streams are 0600, and that is tofu's doing.
* **F12's conclusion holds; one premise does not.** The secret is `user_data` in the seed
  ISO and state encryption would not protect it — correct. But F12 says the config's
  contents flow into the tfvars *and the state file*; the URI reaches the tfvars and
  `plan.bin`, not the state, both unencrypted 0644 files.
* **Reuse fails two ways:** `deploy` into an existing `--run-dir` dies with
  `FileExistsError`; `destroy` into it succeeds and overwrites the deploy's `run.json`.

## Findings

### F-RUNDIR-01 — the failed apply is the one outcome with no record
- **Severity:** S2 · **Confidence:** high
- **Location:** `orchestrator/cli.py:223` (`_record` sits after `tofu.outputs`), `:393-399`
- **What:** `_record` is reached on `refused`, `nothing-to-create`, `ok`,
  `nothing-to-destroy`, `cancelled`. A `TofuError` from `init`/`plan`/`apply`/`outputs`
  propagates past it to `main`'s `except Exception`, one printed line and rc 1. `outcome`
  has no `failed` value.
- **Why it matters here:** acceptance defect 3 is exactly this shape — an apply that
  failed *after* writing four volumes. The directory an air-gapped site ships back names
  no vcows version, no command, no start time and no outcome; the deployment name survives
  only inside `marker_xml` in the tfvars. The only machine-readable evidence of
  half-created VMs is `terraform.tfstate`, the artifact the tool calls disposable and
  never reads back.
- **Evidence:** `cli.main(["deploy", …])` with `tofu.apply` raising → `rc: 1`, contents
  `['seed', 'tofu']`, `run.json present: False`. Observed outcome map:

  | outcome | on disk |
  |---|---|
  | deploy ok | `seed/*.iso`, `tofu/` (module, `main.auto.tfvars.json`, `.terraform.lock.hcl`, `.terraform/` symlinks, `init.json`, `plan.json`, `apply.json`, `plan.bin`, `terraform.tfstate`), `inventory.json`, `run.json` |
  | refused at preflight | `run.json` alone (`outcome: refused`) |
  | **failed mid-apply** | `seed/`, `tofu/…` — **no `run.json`, no `inventory.json`** |
  | destroy, any result | `run.json` alone |
  | every outcome | **`manifest.json` never** |
- **Fix / cost:** wrap each verb's body from `_run_dir` onward in `try/except
  BaseException`, `_record(…, "failed", extra={"error": …})`, re-raise — one
  `try/except/raise` per verb and one new `outcome` string. The handler re-raises, so §3's
  ban on a shared exception hierarchy is untouched.

### F-RUNDIR-02 — 0700 protects the directory; every file inside is umask-governed
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:79` (`os.chmod`), `:84`, `:203`, `:205`,
  `backends/libvirt/prepare.py:98-112`
- **What:** only the leaf is chmodded. `seed/` and `tofu/` use bare `mkdir()`, and every
  file — ISOs via pycdlib's `open`, JSON via `write_text`, `plan.bin` and
  `terraform.tfstate` via tofu — takes `0666 & ~umask`.
- **Why it matters here:** cli.py's module docstring and README both present "created
  0700" as the handling F12 promised, and it does less than it reads. The protection
  belongs to the containing directory, so it evaporates the moment the contents move:
  `cp -r`, `scp -r`, `rsync` without `-p`, or uploading files to a support ticket all
  reproduce them at 0644 — the exact motion the directory exists to support. The
  container's umask is the caller's, so a permissive site writes `user_data` world-writable.
- **Evidence:** one deploy per umask, `find -printf '%M %P'`. At 022: `drwx------ .`,
  `drwxr-xr-x seed`, `-rw-r--r-- seed/…iso`, `-rw-r--r-- tofu/main.auto.tfvars.json`,
  `-rw-r--r-- tofu/plan.bin`, `-rw-r--r-- tofu/terraform.tfstate`, `-rw------- tofu/{init,
  plan,apply}.json`. At 002 the ISO is `-rw-rw-r--`; at 000 `-rw-rw-rw-`, `seed/`
  `drwxrwxrwx`. A real seed ISO from a config carrying `$6$…`: 0644, 69632 bytes, the hash
  once in cleartext.
- **Fix:** `os.umask(0o077)` once at the top of `main()`. It also covers what tofu writes,
  which per-file chmods cannot.
- **Cost of the fix:** one line, affecting nothing outside the process. Justified because
  file permissions are F12's entire mitigation.

### F-RUNDIR-03 — reusing a run directory: deploy fails, destroy silently rewrites
- **Severity:** S2 · **Confidence:** high
- **Location:** `orchestrator/cli.py:71-81`, `:203`, `:205`, `:296`; `README.md:66`
- **What:** `_run_dir` accepts an existing directory; `seed.mkdir()`/`workdir.mkdir()`
  then raise, after `_look()` has connected and printed a clean preflight. `destroy`
  creates no subdirectories, so it reuses happily: `_record` replaces `run.json` while the
  earlier deploy's `inventory.json`, `seed/` and `tofu/` stay put.
- **Why it matters here:** README:66 documents `deploy /config.yaml --run-dir /runs/lab-a`
  — a stable path. A second deploy with nothing to create exits 0 early, so the fault
  surfaces only on the run that matters: adding a VM and re-deploying. The destroy face is
  worse in kind: the resulting directory is internally coherent and wrong, reading as a
  destroy that produced an inventory of two live VMs and the media they were given.
- **Evidence:** `cli.main` twice against one `--run-dir`, a third VM added between →
  `error: FileExistsError: [Errno 17] File exists: '…/shared/seed'`, rc 1, the directory
  still holding the *first* deploy's `run.json`. Deploy then destroy into one `--run-dir`:
  `run.json` goes from `"command": "deploy" … "created": ["app01","app02"]` to
  `"command": "destroy" … "decisions": []`, `inventory.json` unchanged beside it. (The
  deploy half matches 05's F-DRV-01.)
- **Fix / cost:** refuse a non-empty `--run-dir` in `_run_dir`, before `_look()` spends a
  connection; both verbs inherit it. One conditional and one message; an empty directory
  still works.

### F-RUNDIR-04 — URI userinfo reaches the tfvars and the saved plan, not the state
- **Severity:** S3 · **Confidence:** high (medium on the state)
- **Location:** `backends/libvirt/schema.py:193-237`, `:165-166`, `tofu/variables.tf:8-11`
- **What:** verified independently of 03 — `_check_target` inspects scheme, hostname,
  path, query and fragment, never `parts.username`/`parts.password`; `connection_uri`
  clears only `query`, so the netloc reaches `render()`'s `uri` and
  `tofu/main.auto.tfvars.json` verbatim. `variable "uri"` is not marked `sensitive`.
- **Why it matters here:** the credential is written to a 0644 file kept forever by
  design, and again into `plan.bin`. It is inert at connect time (`BatchMode yes`,
  `IdentitiesOnly yes`), so the operator sees `Permission denied (publickey)` and no sign
  that what they typed is now on disk twice.
- **Evidence:** `_check_target({"uri": "qemu+ssh://vcows:sup3rs3cret@vcows/system"})` →
  `[]`; `connection_uri(t, "sshcmd")` → `qemu+sshcmd://vcows:sup3rs3cret@vcows/system`.
  Unzipping a real `plan.bin` (`tfplan`, `tfstate`, `tfstate-prev`, `tfconfig/…`,
  `.terraform.lock.hcl`): the variable value appears 3× in `tfplan`, 0× elsewhere.
  **Correction to 03 and to F12:** `var.uri` is consumed only by the `provider "libvirt"`
  block and OpenTofu does not persist provider configuration in state, so it does not land
  there. No libvirt state can be produced offline; a grep of the acceptance run's
  `terraform.tfstate` would close this.
- **Fix / cost:** one clause in `_check_target` rejecting a set `parts.password`, beside
  the four identical ones — six lines, no new concept. The username stays; it is required.

### F-RUNDIR-05 — `manifest.json` is promised in five places and written by none
- **Severity:** S5 · **Confidence:** high
- **Location:** `README.md:146`, `:181`, `docs/findings.md:308` (R5),
  `orchestrator/__init__.py:11`; `cmd_deploy`/`cmd_destroy` never copy it
- **What:** `cli.manifest()` reads `/opt/vcows/manifest.json` for `cmd_version` alone.
- **Why it matters here:** already reported by 05 and 06; recorded because this is the
  artifact it is missing from. Build identity is the one thing a returned run directory
  needs and the one thing it lacks. `orchestrator/__init__.py:11` also lists it among the
  five version consumers `tests/test_version.py` keeps from drifting.
- **Evidence:** `grep -rn 'manifest.json' --include=*.py .` → `cli.py:46,51`,
  `container/manifest.py`, `tests/test_image.py`. A successful deploy:
  `seed/ tofu/ inventory.json run.json`.
- **Fix / cost:** `shutil.copy(MANIFEST, run / "manifest.json")` in `_record`, guarded by
  `MANIFEST.is_file()` so a checkout writes nothing — two lines. Deleting the claims is the
  alternative, but R5 is a licensing obligation, so writing the file is cheaper.

### F-RUNDIR-06 — unconditional `chmod` fails on a bind mount the container UID does not own
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/cli.py:79`
- **What:** `os.chmod(path, 0o700)` runs every invocation, including when the directory
  already is 0700. `chmod(2)` requires ownership, so a `--run-dir` on a bind mount owned by
  another UID raises `PermissionError` before any work begins.
- **Why it matters here:** README:49-53 explicitly supports `--user`, under which a
  host-owned `./runs` maps to a foreign UID inside the container. The operator gets
  `error: PermissionError: [Errno 1] Operation not permitted: '/runs/lab-a'` with no
  mention of 0700 or why the tool wants it. Second face: `--run-dir /runs` (the mount
  root) succeeds and chmods the shared tree to 0700 on the host.
- **Evidence:** `cli._run_dir({'deployment':'lab-a'}, '/usr/share')` as uid 1000 →
  `PermissionError [Errno 1] Operation not permitted: '/usr/share'`; mode unchanged.
- **Fix:** skip the chmod when `stat().st_mode & 0o077 == 0`; on `PermissionError` say
  which mode was wanted and why.
- **Cost of the fix:** four lines in `_run_dir`, no new surface.

### F-RUNDIR-07 — nothing removes a run directory, and nothing tells the operator to
- **Severity:** S5 · **Confidence:** medium
- **Location:** `README.md:134-152`, `docs/findings.md` §2
- **What:** one directory per invocation under `runs/<deployment>/<timestamp>Z/`, never
  pruned. §2 records the base image as an accepted cleanup gap and not run directories. A
  failed connection leaves an empty one, since `_run_dir` precedes `_look()`.
- **Why it matters here:** what accumulates is not disk — roughly 70 KB of ISO per VM plus
  a few hundred KB of tofu artifacts, and `.terraform` is symlinks into the image's plugin
  cache so the 26 MB does not recur. It is cleartext `user_data`: a year of daily deploys
  leaves 365 copies of every guest's cloud-init and of the connection URI, none expiring,
  and the documentation never says when a run directory stops being needed.
- **Evidence:** `grep -rn 'rmtree\|unlink' orchestrator/` → `prepare.py:101` only, the ISO
  overwrite.
- **Fix / cost:** one sentence in README's run-directory section — these hold secrets
  indefinitely and are the operator's to delete. Not a `prune` verb; that is §5 territory.

## Checked and sound

* **`_run_dir`'s ordering.** `mkdir` → `resolve()` → `chmod`: the resolve happens after
  creation so a relative `--run-dir` cannot escape, and a real `chmod` rather than
  `mkdir`'s mode means umask cannot widen the leaf. The comment on cli.py:79 is accurate.
* **`tofu` path handling.** Every path handed to `-chdir`'d OpenTofu is `.resolve()`d
  first; `plan.bin`, the three `-json-into` streams and `terraform.tfstate` land under
  `tofu/` and nowhere else.
* **The saved plan is self-contained.** `plan.bin`'s `tfplan` member carries the variable
  values, so the plan really is a record of what was applied — `tofu.plan`'s docstring
  claim confirmed by unzipping it.
* **`refused` and `nothing-to-create` are honest.** Both write `run.json` and neither
  creates `seed/` or `tofu/`, so a refusal cannot be read as a partial deploy.
* **Seed ISO content.** `user_data` appears once in the image (Rock Ridge and Joliet share
  the extent), and the ISO is byte-identical for identical input.
* **The entrypoint touches no run directory.** It writes `~/.ssh/config` at 0600 under a
  0700 `~/.ssh`, refuses to overwrite a mounted one, and copies no key material.

## Not checked

* Whether a real libvirt `terraform.tfstate` carries the connection URI — the provider
  connects during `plan`, so no state can be produced offline and the rig is off limits.
* SELinux labelling under `:Z` — outside scope, and it changes none of the modes above.

## Deserves its own agent

* **The `--run-dir` / `--user` / bind-mount matrix.** F-RUNDIR-06 is one cell. README:49-53
  makes claims about rootless podman UID mapping that nobody this round tested against a
  real `podman run --user`, and every mount in the worked example depends on them.
* **`tests/test_version.py` and its five consumers.** F-RUNDIR-05 shows one of the five
  does not exist; whether that test checks all five or silently four is its own question.
