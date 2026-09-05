# Container and supply chain — review

Agent: 06-container-supplychain · Scope: `Containerfile`, `container/*`, `licenses/`,
`.containerignore`, `README.md` build/run sections · Date: 2026-08-29

## Summary

* Pinning is good but not uniform: base by digest, tofu RPM sha256-verified before `rpm -i`, provider by exact
  version and `h1:` lock — but the five distro RPMs and `epel-release` are unconstrained, so the image is
  archivable, not reproducible. Mirror, lock, Containerfile ARGs and `PROVENANCE.md` all agree today (verified
  byte for byte); nothing enforces it.
* `manifest.json` is never copied into the run directory, though README says so twice, `__init__.py` says so,
  `cli.py` says Stage 5 did it, and R5 asks for it.
* One runtime network path escapes the air-gap design: `image.source_qcow2` is an unvalidated string reaching
  the provider's HTTP-capable `create.content.url`.
* `container/entrypoint.py` tracebacks on three classes of malformed config, reproduced below, in a file whose
  docstring says that is `validate`'s job.

## Findings

### F-SUPPLY-01 — the build manifest is never copied into the run directory
- **Severity:** S3 · **Confidence:** high
- **Location:** `README.md:139`, `README.md:178`, `orchestrator/__init__.py:11`, `cli.py:105`,
  `cli.py:222-231`
- **What:** Four places assert the R5 manifest lands in every run directory. `cmd_deploy` writes
  `inventory.json` and `run.json`, `cmd_destroy` writes `run.json`, and nothing reads `MANIFEST` except
  `cmd_version`.
- **Why it matters here:** The run directory is what an operator carries back from a site for support.
  `run.json` has the vcows and tofu versions but not the git SHA, base digest, provider checksum or RPM set —
  what identifies *which build* failed is what is absent, while the README sends them there for it.
- **Evidence:** `grep -rn "MANIFEST\|manifest" orchestrator/` → five hits, none a write into `run`. README:
  "The same file is copied into every run directory."
- **Fix:** `shutil.copy(MANIFEST, run)` in or beside `_record`, guarded by `MANIFEST.is_file()`.
- **Cost of the fix:** Two lines. The docs and R5 already promise it.

### F-SUPPLY-02 — `source_qcow2` accepts an HTTP URL the provider will fetch
- **Severity:** S3 · **Confidence:** high
- **Location:** `orchestrator/config.py:42`, `backends/libvirt/render.py:71`,
  `backends/libvirt/tofu/main.tf:29`
- **What:** `source_qcow2` is `{"type": "string", "minLength": 1}` with no scheme constraint, passed verbatim
  to `create.content.url`. Both local checks degrade to WARNING when it is unreadable (`schema.py:404-416`,
  `preflight.py:279-288`), and on a first deploy (`create: True`) preflight reports nothing about it at all.
- **Why it matters here:** The air-gap guarantee rests on there being no `direct` block in `container/tofurc`,
  and that guard does not cover the golden image. A config with an `https://` source passes `validate` with
  one warning and `preflight` cleanly, then reaches out from inside the container mid-`apply`.
- **Evidence:** provider README inside the mirrored 0.9.8 zip, §"Volume Source URLs": `url =
  "https://cloud-images.ubuntu.com/…"`, "capacity is automatically detected from Content-Length"; the binary
  carries `failed to fetch URL: %w`.
- **Fix:** In `schema.py`'s semantic checks, ERROR on a `source_qcow2` parsing with any scheme other than
  `file`.
- **Cost of the fix:** One check. Same reasoning that removed the `direct` block.

### F-SUPPLY-03 — the entrypoint tracebacks on malformed config before vcows runs
- **Severity:** S3 · **Confidence:** high
- **Location:** `container/entrypoint.py:88-97`
- **What:** `install()` catches only `OSError` and `yaml.YAMLError`. Three reachable cases escape: `target:` a
  scalar, `target.libvirt` a scalar (both `AttributeError` from `.get` on `str`), and a non-UTF-8 file
  (`UnicodeDecodeError`). The exception leaves `main()`, so `os.execv` never runs and `vcows` never starts.
- **Why it matters here:** Line 90 says "Not our error to report. `vcows validate` says it properly." For
  these inputs validate never runs; the operator gets a traceback naming `/usr/local/bin/vcows-entrypoint` —
  glue they did not know existed — instead of `config.load`'s report. A list-valued `ssh_keyfile` is
  separately written as a Python repr.
- **Evidence:** run against a copy of the file — `target: nope` and `target.libvirt: "x"` each RAISED
  `AttributeError 'str' object has no attribute 'get'`; non-UTF-8 bytes RAISED `UnicodeDecodeError`.
- **Fix:** `isinstance` checks on `target`, `target["libvirt"]` and the two string fields, or one wider
  `except Exception: return` around parse and extraction.
- **Cost of the fix:** Two checks; restores what the docstring already claims.

### F-SUPPLY-04 — the application ships with no licence of its own
- **Severity:** S3 · **Confidence:** high
- **Location:** repo root (no `LICENSE`), `pyproject.toml` (no `license`), `Containerfile:63`,
  `Containerfile:151`
- **What:** `COPY orchestrator /opt/vcows/orchestrator` puts ~2,800 lines into a redistributable image.
  `org.opencontainers.image.licenses` names eight licences, all from dependencies; `manifest.json` lists only
  RPMs. Nothing states the terms for the vcows code, and `image.source` points a recipient at a repo with none
  either.
- **Why it matters here:** F16's failure mode is the image misidentifying itself to a third party. The labels
  fixed the inherited half; the tool's own half is unstated, so the first external delivery hands someone an
  image whose largest component has no grant.
- **Evidence:** no `LICENSE` in the repo root; `grep -i license pyproject.toml` → no match; `licenses/` holds
  only `dmacvicar-libvirt/`.
- **Fix:** A root `LICENSE`, `project.license` in `pyproject.toml`, that identifier added to `IMAGE_LICENSES`.
- **Cost of the fix:** One file, two lines, no code surface.

### F-SUPPLY-05 — the manifest records source RPMs but not where they came from
- **Severity:** S3 · **Confidence:** medium
- **Location:** `container/manifest.py:22`, `Containerfile:68-78`
- **What:** `QUERY` captures `NAME`, `VERSION-RELEASE`, `LICENSE`, `SOURCERPM` — not `VENDOR`. The build
  enables EPEL, installs from it, then removes `epel-release` and its repo files, so the image cannot say
  which packages are EPEL's and which Rocky's.
- **Why it matters here:** D22's argument is that the GPL sidecar becomes "a `reposync` against a list that
  already exists". A list of SRPM filenames with no repo of origin is not that: the EPEL entries
  (`python3-pycdlib`, and `python3-jsonschema` if Rocky 10 lacks it) will not resolve against Rocky's source
  repos, and the sidecar's builder finds that out by hand at delivery.
- **Evidence:** `Containerfile:68` installs `epel-release` before the five packages and `:76-78` removes it;
  `manifest.py:22` `QUERY = "%{NAME}\t…\t%{SOURCERPM}\n"`.
- **Fix:** Append `%{VENDOR}` and carry it into each record. Unverified against a built image — `podman run
  --rm IMAGE rpm -qa --qf '%{NAME} %{VENDOR}\n'` settles it.
- **Cost of the fix:** One format directive, one dict key.

### F-SUPPLY-06 — the provider facts live in five places and no two are checked
- **Severity:** S4 · **Confidence:** high
- **Location:** `Containerfile:52-54`
- **What:** `PROVIDER_VERSION`, `PROVIDER_SHA256` and `PROVIDER_LOCK_HASH` are ARGs whose values also live in
  `docs/provider-0.9.8.lock.hcl`, the mirror's `0.9.8.json`, `main.tf`'s `version = "= 0.9.8"` and
  `PROVENANCE.md`. The build compares none of them, unlike `Containerfile:80-84` for the tofu RPM.
- **Why it matters here:** `manifest.json` is the archival record of what shipped, and its provider block is
  copied from ARGs rather than measured. A stale ARG after a version bump, or a `--build-arg` override, yields
  a manifest stating a checksum the image does not contain, with no build failure. The `h1:` hash is checked
  only indirectly, by the warm `tofu init` against the committed lock, never against the ARG.
- **Evidence:** all four agree today — `sha256sum` of the mirrored zip is `061e5187…26ee1`, matching
  `PROVIDER_SHA256`, the mirror's `zh:` entry and `PROVENANCE.md`; the lock's single hash matches
  `PROVIDER_LOCK_HASH`.
- **Fix:** One `sha256sum -c` on the mirrored zip after the COPY, and have `manifest.py` read version and lock
  hash from the committed lock, not the env.
- **Cost of the fix:** One shell line, two ARGs deleted — it removes surface.

### F-SUPPLY-07 — README's `,Z` mount relabels the golden-image directory
- **Severity:** S3 · **Confidence:** medium
- **Location:** `README.md:60`
- **What:** The copy-paste run command uses `-v /srv/images:/images:ro,Z`. `Z` applies a private SELinux MCS
  label recursively, even for `:ro`. If that directory is or feeds a libvirt pool on the same host — the
  acceptance rig's own layout — the qcow2 files move off `virt_image_t` and `qemu` can no longer open them.
- **Why it matters here:** SELinux is enforcing on the Fedora test bed and both RHEL targets. The operator's
  first `podman run` breaks unrelated running VMs on that box, and the `Permission denied` in the qemu log
  points at libvirt, not at vcows.
- **Evidence:** `README.md:60`. Not reproduced — I did not run podman; `ls -Z /srv/images` before and after
  one run settles it.
- **Fix:** Use `z` (shared), or drop the relabel and say the directory must already carry a label the
  container can read.
- **Cost of the fix:** One character, plus a sentence.

### F-SUPPLY-08 — two docstrings describe the pre-initialised tree Stage 5 rejected
- **Severity:** S5 · **Confidence:** high
- **Location:** `orchestrator/cli.py:239-243`, `orchestrator/tofu.py:176-181`
- **What:** `_stage_module` says "Stage 5 replaces this copy with a pre-initialised tree anyway (R6)"; `init`
  says "Stage 5 decides whether the run directory is seeded from a pre-initialised tree instead". Stage 5
  decided the opposite — `findings.md` R6 records the tree was not built because it would have hidden the 26
  MB-per-run copy, and `TF_PLUGIN_CACHE_DIR` was warmed instead. `_stage_module` also says the module ships no
  lock: true of the checkout, false of the image (`Containerfile:101`).
- **Why it matters here:** Both are what a maintainer reads before touching run-directory staging, and both
  point at a rejected design.
- **Evidence:** `Containerfile:124-132`; `findings.md` R6, "Built differently, for a reason found while
  building it."
- **Fix / cost:** Rewrite both paragraphs. Prose only.

## Checked and sound

* **Every downloaded artefact is checksum-verified before use.** The tofu RPM is `sha256sum -c`'d before `rpm
  -i`; the base is pinned by digest at `FROM` with the ARGs correctly redeclared after it; distro RPMs are
  GPG-verified by dnf.
* **Mirror, lock, warm cache and module constraint are mutually consistent.** The mirror's `0.9.8.json` lists
  exactly the `h1:` in `docs/provider-0.9.8.lock.hcl` and a `zh:` equal to the zip's real sha256; `main.tf`
  pins `= 0.9.8`; the warm init ran against that lock and mirror with `TF_CLI_CONFIG_FILE` exported.
  `tests/test_image.py` asserts init does not rewrite the lock and disables `TF_PLUGIN_CACHE_DIR` for the
  mirror gate — without that the air-gap assertion becomes a cache test.
* **Nothing in the tool's runtime path opens a socket but the SSH tunnel.** No `direct` block;
  `CHECKPOINT_DISABLE=1` in `ENV` and re-set by `tofu._env()`, which inherits rather than replaces the
  environment so `TF_CLI_CONFIG_FILE` and `TF_PLUGIN_CACHE_DIR` survive; no remote module `source`.
  F-SUPPLY-02 is the one exception.
* **The OCI label set covers F16's list and more** — the eight it names plus `title`, `description`,
  `version`, `revision`, `created`. `test_the_labels_are_ours_and_not_the_bases` asserts no label but
  `base.name` contains "rocky", which also catches an inherited `url`.
* **`PROVENANCE.md` is defensible** — its SHA256 and lock hash match the mirror, and the orphan-history
  argument is the correct reading of Apache-2.0 §4(a).
* **`entrypoint.py` handles four attacks correctly.** No config argument → returns silently. UID with no
  passwd entry → `KeyError` caught, message, no write. Existing `~/.ssh/config` → left alone, documented.
  Read-only `~/.ssh` → `OSError` reported. A symlinked config resolves through `is_file()`. A FIFO is skipped,
  giving a bare `Host key verification failed`, but nobody passes a FIFO.
* **`.containerignore` is correct.** `.tools/*` then `!.tools/tofu-mirror` re-admits the mirror and its
  subtree, because `*` does not cross `/`. Only that, `container/`, `licenses/`, `orchestrator/` and one lock
  file are `COPY`ed.

## Not checked

* Anything needing a build or `podman run`: F-SUPPLY-05's vendor question, F-SUPPLY-07's relabel, label values
  on the built image, and whether `--nodocs` preserved `%license` files (it should, but unconfirmed here).
* Whether podman's default `--passwd` writes an `/etc/passwd` entry for a `--user` UID. If it does,
  `entrypoint.home()`'s `None` branch is unreachable; if not, `ssh` aborts with "no user exists for uid" and
  README:48's "supported" is wrong. `podman run --user 4242 … id` settles it.
* `IMAGE_LICENSES` against a real `rpm -qa --qf %{LICENSE}`. The Containerfile calls it deliberately
  non-exhaustive and I have not re-litigated that.

## Deserves its own agent

* **`config.py`'s permissiveness on path-shaped strings.** F-SUPPLY-02 is one instance: `source_qcow2`,
  `ssh_keyfile` and `known_hosts` are all bare `{"type": "string"}`, and the last two are written verbatim
  into `~/.ssh/config`, where a newline injects ssh options.
* **The run directory's secret handling end to end.** Mode 0700 is the only thing protecting seed ISOs
  containing `user_data`, and F-SUPPLY-01 adds a fourth file there. Nobody in my scope owns `--run-dir`
  pointing at a directory with a laxer mode.
