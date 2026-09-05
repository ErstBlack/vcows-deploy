# G — the unread list · `REVIEW.md:480-517` · 2026-08-31
Worktree pinned at `origin/master` `672a500`; every `file:line` re-read at that commit.

## Summary

* The rootless-podman matrix ran for the first time and **falsifies `README.md:66`.** Following the
  README's own `--user` recipe verbatim, `deploy` dies at its first `mkdir` with `error:
  PermissionError: [Errno 13] Permission denied: 'runs/lab-a'` — a raw errno through `main`'s
  catch-all, naming a *relative* path. `preflight` runs clean; `deploy` does not. The README's
  remedy (`:U`) works and costs something it does not state: the run directory lands owned by a
  **subuid**, 0700, unreadable to the invoking operator — and that directory is what an air-gapped
  site ships back (`orchestrator/cli.py:214-218`).
* `tofu/variables.tf`, never read by any review, has one wrong sentence: `var.uri` is documented as
  the `qemu+ssh://` form and is always rendered `qemu+sshcmd://`. `preflight.py` outside the
  destructive halves is sound — three parse assumptions verified read-only against the live rig,
  all holding — and `docs/research/future-backends.md` and `docs/spikes/README.md` are clean.

### RX-G1 · **medium** · `orchestrator/cli.py:134` — a run directory that cannot be created reaches `main`'s catch-all
`_run_dir`'s `path.mkdir(parents=True, exist_ok=True)` is unguarded. `UsageError`
(`orchestrator/cli.py:63-70`) exists to stop exactly this — its docstring says a raw `OSError` here
"would print `error: FileExistsError: /runs/lab-a` and leave the operator to work out which of the
two paths they passed it means" — but the class covers only the is-a-file case at `:128`; `EACCES`
and `EROFS` are not. The message also prints the path *before* `path.resolve()` at `:135`, so the
default layout reports `runs/lab-a`, relative, with no `/` tying it to a mount. Reproduction: R8
and R12. These are the two ways an operator gets a bind mount wrong and both are reachable from the
README's own invocation block; `tests/test_cli.py:767` covers the `chmod` failure and nothing
covers the `mkdir` failure. Fix: one `except OSError` raising `UsageError` with the resolved path.

### RX-G2 · **medium** · `README.md:66`, against `orchestrator/cli.py:134` — the README says `deploy` runs clean under `--user`; it does not
"With both, `preflight` and `deploy` run clean." The two things `README.md:56-64` tells the
operator to line up are a writable home (`--passwd-entry`) and `:U` on the key mount. Neither
touches `/runs`, which `README.md:78` mounts `:Z` — owned by the mapped host UID, mode 0755. The
container UID cannot `mkdir` inside it, so the default layout `runs/<deployment>/<timestamp>`
(`README.md:91`) fails before anything connects; `preflight` is unaffected because it writes no run
directory. Reproduction: R13, the recipe verbatim — exit 1, no run directory. This is the only
`--user` guidance the project ships, it is presented as measured, and half of it is wrong for the
verb that matters. Fix: a sentence, plus `:U` on `/runs` in that bullet — which must also carry
RX-G3.

### RX-G3 · **medium** · `README.md:62-64` and `:91-94`, against `orchestrator/cli.py:214-218` — the `:U` remedy makes the run directory unreadable to the operator
and `README.md:91-94` `README.md:63` prices `:U` as "the cost of chowning your host copy" of the
key. Applied to `/runs` — which RX-G2 shows is required — the *output* is chowned too: the run
directory lands owned by the subuid backing container UID 4242, and `_run_dir` makes it 0700, so
uid 1000 gets `Permission denied` on its own `./runs`. `run.json`, `inventory.json` and the seed
ISOs are all inside it. `_record`'s docstring calls that directory "what an air-gapped site ships
back"; nothing says the operator will need `podman unshare` to open it. Reproduction: R10 — the run
reports what it did and the artifact it produced is inaccessible to the person who asked for it.
Fix: documentation. `--userns=keep-id:uid=4242` is worth naming as the alternative, but is untested
here and should not be recommended unmeasured.

### RX-G4 · **low** · `orchestrator/cli.py:156` — the `chmod` guard catches `PermissionError` only
`orchestrator/cli.py:150-152` says the `chmod` "must not stop a run that is otherwise fine" and
guards it with `except PermissionError`. `EROFS` is a plain `OSError`, so a `/runs` mounted `:ro`
kills the run *at the chmod* rather than at the first write (R12b: raised from `:155`, not from
`seed.mkdir()`). Low consequence — the run was doomed anyway — but the message points at the mode
rather than the mount. `tests/test_cli.py:767-769` monkeypatches `chmod` to raise `PermissionError`
specifically, which is why this was not caught. Fix: widen to `OSError`.

### RX-G5 · **low** · `orchestrator/backends/libvirt/tofu/variables.tf:10` — `variables.tf` documents `var.uri` as `qemu+ssh://`; it is always `qemu+sshcmd://`
"libvirt connection URI. qemu+ssh:// form, …". The only writer of that variable is
`orchestrator/backends/libvirt/render.py:61`, `connection_uri(target, "sshcmd")`, and
`tests/golden/libvirt.tfvars.json:9` pins the result as `"qemu+sshcmd://vcows@vcows/system"`. The
rest of the sentence — no query string, no password, credentials via `~/.ssh/config` — is correct.
The two-schemes-from-one-config split is the acceptance run's S2 defect and the most surprising
thing in this backend; this file is the module's only in-tree explanation of `var.uri` and it
states the wrong half. Fix: one word.

### RX-G6 · **low** · `orchestrator/cli.py:658` — `cmd_version` tolerates two of the four failures `_tofu_version` tolerates
`except (tofu.TofuError, OSError)`. `tofu._capture` (`orchestrator/tofu.py:246-264`) can also raise
`subprocess.TimeoutExpired` at `SHORT_TIMEOUT` (`tofu.py:44`, 120s) and `json.JSONDecodeError` on
unparseable stdout; `_tofu_version` (`orchestrator/cli.py:335`) catches all four for the same call,
deliberately. `_print_manifest`'s docstring (`orchestrator/cli.py:634-639`) records this regression
once already — "the one command that answers 'which build is this' answered nothing at all on
exactly the image somebody would be asking about". Two of the four broken-binary shapes still take
`vcows version` to `error: TimeoutExpired: …` and exit 1 rather than `tofu: unavailable (…)` and
exit 0. Fix: copy the tuple from `:335`.

### RX-G7 · **low** · `docs/archive/orchestrator-architecture.md:157-191`, against `docs/findings.md:417-438` — the errata covers the doc's commands, not its config sample
The errata table lists wrong *commands* and *versions*. The §4.3 sample config is wrong in a
different way and is not listed: `backend: proxmox` (`REGISTRY` holds `libvirt` only,
`orchestrator/backends/__init__.py:21`), top-level `lifecycle:`/`state:`/`defaults:` (the core
schema is `additionalProperties: false`, `orchestrator/config.py:79`),
`image.distro`/`image.sha256` with no `base_volume_name` (`orchestrator/config.py:43`), `memory_mb`
for `memory_mib`, and `vms[].cloudinit.user_data: ./file.yaml` where the code takes a top-level
`user_data` *string* used verbatim (`.../libvirt/prepare.py:50`). Not one line of it loads.
`…architecture.md:97`'s "fixed output shape (`vm_name → {ip, hostname, backend}`) that every
backend module must satisfy" is likewise contradicted by `tofu/outputs.tf:5-31` —
`name`/`uuid`/`configured_address`/`disks`, no IP ever observed — and is the only item here
somebody building a second backend could act on. Fix: two errata rows; the doc stays archived.

### RX-G8 · **nit** · `container/entrypoint.py:189` — a citation that moved
cites `orchestrator/cli.py:670` for the `os.umask(0o077)` it reasons about; that call is at
`orchestrator/cli.py:705`, and `:670` is the `ArgumentParser` constructor. The reasoning itself is
still correct.

### RX-G9 · **nit** · `orchestrator/backends/libvirt/preflight.py:186` — a shared MAC can be dropped from the collision index
`by_mac.setdefault(mac, name)` keeps only the first domain claiming a MAC. `preflight.py:551` then
drops every entry whose owner is one of ours, so if one of our domains is listed first, a *foreign*
domain holding the same MAC contributes nothing and `address_conflicts` calls it free. Requires two
domains already sharing a MAC — which libvirt permits and nothing here creates — but that index is
preflight's only MAC fact.

## The rootless-podman matrix

`podman 5.8.2`, rootless, SELinux `Enforcing`, host uid 1000, host umask 0022. Image
`localhost/vcows-deploy:0.1.0.0` (`84dcf01a718d`, the Phase 0 build). Fixtures in a scratch dir: a
real `qemu-img create -f qcow2 … 8G` golden image, an ed25519 key at 0600, a `known_hosts`, and the
README's config with the URI set to `qemu+ssh://vcows@hypervisor.invalid/system`. The `.invalid`
TLD cannot resolve, so every run stops at the connect and **nothing reached the rig.**

**R1 — the image's environment.** `podman image inspect` → `WorkingDir=/  User=<empty>
Entrypoint=[/usr/local/bin/vcows-entrypoint]`, so the default `runs/<deployment>/<ts>` resolves to
`/runs/…`, inside the README's bind mount. `--entrypoint sh IMG -c 'umask; id; getent passwd $(id
-u)'` → `0022`, `uid=0(root)`, `root:x:0:0:Super User:/root:/bin/bash`, confirming
`container/entrypoint.py:187`.

**R4b/R5 — baseline rootless, README mount block, no `--user`.** `validate` → `/config.yaml: valid
(1 VMs, deployment 'lab-a')`, exit 0. `deploy` → `error: libvirtError: … Could not resolve hostname
hypervisor.invalid …`, exit 1.
```
drwx------ ssullivan:ssullivan ./runs/lab-a/20260831T040426Z
-rw------- ssullivan:ssullivan ./runs/lab-a/20260831T040426Z/{run,manifest}.json
```
0700 directory, 0600 contents, owned by the invoking user — the `main` umask
(`orchestrator/cli.py:705`) does what it claims. **Sound.**

**R6/R7 — `--user 4242`, unfixed.** Both `README.md:56-64` claims reproduce exactly: podman
synthesises `4242:*:4242:0:container user:/:/bin/sh` with `HOME=/`; the key mounted from host uid
1000 shows as `-rw------- 0 0` → `cat: Permission denied`.

**R8 — `--user 4242`, default run dir.**
```
vcows: could not write /.ssh/config: [Errno 13] Permission denied: '/.ssh'. …
error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
exit=1
```
No run directory created. **RX-G1.**

**R9 — `--user 4242`, `--run-dir /runs`.** Reaches the connect, printing `vcows: cannot make /runs
0700; it stays 0755…`. This, and only this, is the case `README.md:66-68` describes.

**R13 — the README's `--user` recipe verbatim** (`--passwd-entry` writable home, `:U` on the key,
`/runs` `:Z` as `README.md:78` shows) → `error: PermissionError: [Errno 13] Permission denied:
'runs/lab-a'`, exit 1. `validate` under the same flags is clean, exit 0. **RX-G2.**

**R10 — `--user 4242` + `--passwd-entry` + `:U` on the key *and* `/runs`.** Runs to the connect, as
`README.md:66` promises. Host view afterwards:
```
drwxr-xr-x 528529:ssullivan ./runs
drwx------ 528529:ssullivan ./runs/lab-a          <- ls: Permission denied (exit 2)
-rw------- 528529:ssullivan ./runs/lab-a/<ts>/run.json
-rw-------      528529:1000 ./secrets/id_ed25519.u   <- head: Permission denied
```
`podman unshare find ./runs` shows `4242:root`. **RX-G3.**

**R11 — bind-mount source does not exist.** `-v ./no-such-runs:/runs:Z` → `Error: lstat
no-such-runs: no such file or directory`, exit **125**; absolute → `Error: statfs …`, exit 125.
Podman 5.8.2 refuses rather than silently creating an empty directory, for file and directory
mounts alike. **Sound — no finding.** **R12 / R12b — `/runs` read-only.** Default layout → `error:
OSError: [Errno 30] Read-only file system: 'runs/lab-a'` (RX-G1); `--run-dir /runs` → `… Read-only
file system: '/runs'`, raised from the `chmod` at `orchestrator/cli.py:155` (RX-G4). **SELinux,
recorded not filed:** `:Z` relabelled `./runs` to `container_file_t:s0:c66,c742`, and the invoking
`unconfined_t` user could still read and write it — so `README.md:82-86`'s "nothing else … can read
it afterwards" holds for a confined process, not an unconfined one. `:Z` vs `:z` is settled
(`docs/findings.md:434`).

## Checked and sound

* **`preflight.py` outside the destructive halves.** Three parse assumptions verified read-only
  against the rig, no state touched. `<physical>` is a top-level child of `<volume>`, so
  `preflight.py:205`'s `root.find("physical")` is right, and it is the file's `st_size`
  (`rocky9-box.qcow2`: `physical 2314272768` beside `allocation 2341974016`) — so `base_volume`'s
  comparison at `preflight.py:357-366` compares like with like. `<backingStore><path>` exists as
  `preflight.py:211` reads it. `dumpxml --inactive` on a running domain emits `device=` on every
  `<disk>` and omits `<source>` on an empty cdrom tray, which is what `disks_of` relies on; the
  rig's `_cloud-images` entry carries `<format type='dir'/>` and no `<physical>`, as `volume_facts`
  says. Also sound: `open_pool`'s narrow `ERR_NO_STORAGE_POOL` test, `walk`'s per-volume
  skip-and-report, `orphan_volumes`' whole-path comparison at `:417-419`.
* `preflight()` returns `artifacts` without `base_volume` when the pool is absent and `render`
  would `KeyError` on it — unreachable: both `open_pool` refusals are `Problem.error` and `_deploy`
  refuses on any fatal at `orchestrator/cli.py:356` before `prepare` runs. Likewise `cmd_validate`
  cannot report a fatal and still exit 0: `config.load` (`orchestrator/config.py:154`) raises
  first.
* `_stage_module`'s refuse-don't-skip loop (`orchestrator/cli.py:469-477`) and `module_dir`
  (`:101`). `outputs.tf` in full — correct, and `parse_outputs` is its only consumer. There is no
  `versions.tf`; the `terraform` block and the `= 0.9.8` pin live at `main.tf:1-14`.
* `docs/research/future-backends.md` and `docs/spikes/README.md` — every code-checkable claim holds: `prepare()` is
  a context manager, `qemu-img` is absent from the `Containerfile`, `backing_store` is used
  (`tofu/main.tf:49`), the qcow2 header read backs the `disk_gb` check, and the seed-ISO check is
  the three readers `docs/spikes/README.md:113` describes.

## Could not close from `REVIEW.md:480-517`

* The nineteen `docs/review/2026-08-29/` agent reports beyond `_BRIEF.md` and `_ORIENTATION.md` —
  out of budget; A–F cite them individually. `docs/research/tooling-2026-08-29.md` belongs with `#21`, which
  `_PLAN.md:39` defers.
* Provider internals, unchanged: the go-libvirt `sshcmd` dialer's argv construction is still not in
  tree, so a URI username beginning with `-o` is neither confirmed nor ruled out. And
  `--userns=keep-id`, the alternative to `:U` for RX-G3 — not measured, and naming it unmeasured
  would repeat the mistake RX-G2 records.
