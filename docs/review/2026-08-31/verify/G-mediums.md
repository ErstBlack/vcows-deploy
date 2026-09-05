# Phase 3 verify — dimension G, the three mediums · 2026-08-31

Verifier default is REFUTED. Everything below was re-run from scratch, not read off
G's transcript. Worktree `origin/master` `672a500`; image
`localhost/vcows-deploy:0.1.0.0` = `84dcf01a718d…` (same build G used). podman 5.8.2
rootless, host uid 1000, umask 0022, SELinux enforcing.

**Fixtures, built fresh in `mktemp -d`** (no tracked file touched, rig never contacted):
`qemu-img create -f qcow2 images/golden.qcow2 8G`; `ssh-keygen -t ed25519` at 0600;
a one-line `known_hosts`; and the README's own config (`README.md:109-135`) verbatim
with the URI set to `qemu+ssh://vcows@hypervisor.invalid/system`. `.invalid` cannot
resolve, so **every run stops at the libvirt connect and nothing reached the rig.**

`docs/findings.md:434` (`:Z` vs `:z`) read first and not re-litigated. The
2026-08-29 review's own record of this matrix —
`docs/review/2026-08-29/2026-08-29-remediation-checklist.md:269` — is quoted under
RX-G2, because it is the measurement `README.md:66` overstates.

---

## RX-G1 — `orchestrator/cli.py:134`, unguarded `mkdir` — **CONFIRMED, medium**

Re-verified citations at `672a500`: `UsageError` at `orchestrator/cli.py:63`, its
docstring's "raw ``OSError`` reaching ``main``'s catch-all" at `:66-68`; the
is-a-file classification at `:128`; `path.mkdir(parents=True, exist_ok=True)` at
`:134`; `path = path.resolve()` at `:135` — **after** the mkdir, so the message
carries the unresolved path.

Both errno families reach `main`'s catch-all. Three independent reproductions:

**EACCES, `--user` with no writable home** (README recipe half-applied):
```
$ podman run --rm --user 4242 -v ./lab-a.yaml:/config.yaml:ro,z \
    -v ./secrets/id_ed25519.u:/run/secrets/id_ed25519:ro,z,U \
    -v ./secrets/known_hosts:/run/secrets/known_hosts:ro,z \
    -v ./images:/images:ro,z -v ./runs:/runs:Z \
    localhost/vcows-deploy:0.1.0.0 deploy /config.yaml
vcows: could not write /.ssh/config: [Errno 13] Permission denied: '/.ssh'. …
error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
exit=1
```

**EROFS, `/runs` mounted `:ro`, no `--user` at all:**
```
$ podman run --rm … -v ./runs:/runs:ro,Z localhost/vcows-deploy:0.1.0.0 deploy /config.yaml
error: OSError: [Errno 30] Read-only file system: 'runs/lab-a'
exit=1
```

**EACCES, the README's `--user` recipe complete** — see RX-G2 below, same message.

The path in every one is `runs/lab-a`: **relative, no leading `/`**, so nothing in
the message ties it to the `/runs` bind mount the operator got wrong. The
is-a-file branch at `:128` produces a sentence; these produce an exception class
name. `UsageError`'s own docstring names this as the thing it exists to prevent.

**Test coverage claim re-checked and holds.** `tests/test_cli.py:757-769`
(`test_a_run_dir_that_cannot_be_made_private_says_which_mode_it_wanted`)
monkeypatches `cli.os.chmod` to raise `PermissionError` at `:767`. Grep of
`tests/test_cli.py` for `mkdir` returns only fixture setup — **no test exercises a
failing `_run_dir` mkdir.**

**Doc or code: code.** One `except OSError` around `:134` raising `UsageError` with
`path.resolve()` (or the absolute form of the intended path), naming the mount.
Surface cost is four lines and no new concept — `UsageError` and its message
convention already exist at `:63` and `:129-133`.

**Severity: medium, confirmed.** Reachable from the README's own invocation block
by two ordinary mistakes, loud, exit 1, no data at risk — but the operator is
handed an errno and a relative path and must infer the mount.

---

## RX-G2 — `README.md:66` — **CONFIRMED, medium**

`README.md:66`: "With both, `preflight` and `deploy` run clean."

The recipe assembled verbatim from `README.md:53-64` (writable home via
`--passwd-entry`, `:U` on the key) over the mount block at `README.md:72-79`
(`/runs` as `:Z`), against a **fresh empty `./runs`**:

```
$ podman run --rm --user 4242 --passwd-entry 'vcows:x:4242:0:vcows:/tmp:/bin/sh' \
    -v ./lab-a.yaml:/config.yaml:ro,z \
    -v ./secrets/id_ed25519.u:/run/secrets/id_ed25519:ro,z,U \
    -v ./secrets/known_hosts:/run/secrets/known_hosts:ro,z \
    -v ./images:/images:ro,z -v ./runs:/runs:Z \
    localhost/vcows-deploy:0.1.0.0 <verb> /config.yaml

validate  → /config.yaml: valid (1 VMs, deployment 'lab-a')                 exit=0
preflight → error: libvirtError: … Could not resolve hostname hypervisor.invalid  exit=1
deploy    → error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'    exit=1
destroy --yes → error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a' exit=1
```

`preflight` reaches the connect (the `.invalid` hostname is my fixture's stop, not
a defect) — so the two walls `README.md:56-64` names are genuinely down. `deploy`
never gets that far. Host view afterwards:
`drwxr-xr-x. 2 1000 1000 6 ./runs` — **no run directory was created.**

**G understated it by one verb.** `_run_dir` has exactly two callers,
`orchestrator/cli.py:314` (`deploy`) and `:491` (`destroy`); `preflight` and
`validate` never call it. So the README's only `--user` guidance leaves the
operator unable to run **either** mutating verb — including the teardown that
`README.md:23` says is the only way to remove a VM.

**Doc or code: doc, and provably so.** The measurement this sentence was written
from is on disk:
`docs/review/2026-08-29/2026-08-29-remediation-checklist.md:269` — "With
`--passwd-entry` giving a writable home and `:U` on the key mount, **`preflight`
runs clean**." `git log -L66,68:README.md` shows the sentence entered at `4eb378b`
already generalised to `preflight` and `deploy`. Nothing measured `deploy`. The
code is not failing a case it should handle: vcows cannot chown a mount it does not
own, so the lever is podman's, and `_run_dir`'s only code defect here is RX-G1's
message. Fix targets `README.md:66` — and whatever replacement it gets must carry
RX-G3, because the obvious remedy (`:U` on `/runs`) has a cost the README does not
state.

**Severity: medium, confirmed.** Not higher: it fails loudly at exit 1 and destroys
nothing. Not lower: it is the project's only `--user` guidance, is presented as
measured, and is wrong for both verbs that change anything.

---

## RX-G3 — `README.md:62-64` and `:91-94` — **CONFIRMED, medium**

Same recipe as RX-G2 plus `:U` on `/runs`, fresh empty `./runs`:

```
$ podman run --rm --user 4242 --passwd-entry 'vcows:x:4242:0:vcows:/tmp:/bin/sh' \
    … -v ./runs:/runs:Z,U localhost/vcows-deploy:0.1.0.0 deploy /config.yaml
error: libvirtError: … Could not resolve hostname hypervisor.invalid …
exit=1
```
Reaches the connect, as `README.md:66` promises. Host view, as uid 1000:
```
$ ls -lnd ./runs ; ls -ln ./runs ; ls -ln ./runs/lab-a
drwxr-xr-x. 3 528529 1000 19 ./runs
drwx------. 3 528529 1000 30 lab-a
ls: cannot open directory './runs/lab-a': Permission denied      (exit 2)

$ podman unshare find ./runs -printf '%M %U:%G %p\n'
drwxr-xr-x 4242:0 ./runs
drwx------ 4242:0 ./runs/lab-a
drwx------ 4242:0 ./runs/lab-a/20260831T043730Z
-rw------- 4242:0 ./runs/lab-a/20260831T043730Z/manifest.json
-rw------- 4242:0 ./runs/lab-a/20260831T043730Z/run.json
```
`run.json` and `manifest.json` exist and the invoking operator cannot open them.
The `:U` also chowned the key: `-rw------- 528529 1000 ./secrets/id_ed25519.u`,
`head` on it → exit 1, Permission denied — `README.md:63` prices that one ("the
cost of chowning your host copy") and prices nothing on the output side. The
operator cannot even clean up: `rm -rf runs` → `rm: cannot remove 'runs/lab-a':
Permission denied`, exit 1; it took `podman unshare rm -rf` to reset between my
own runs.

`orchestrator/cli.py:216` calls that directory "what an air-gapped site ships
back". `README.md:91-94` describes what lands in it and says nothing about who can
read it.

**One thing G did not measure, and it makes the doc gap worse.** The other way
through — `--run-dir /runs`, the case `README.md:66-68` actually describes — leaves
**no record at all**:
```
$ podman run --rm --user 4242 --passwd-entry … -v ./runs:/runs:Z \
    localhost/vcows-deploy:0.1.0.0 deploy --run-dir /runs /config.yaml
vcows: cannot make /runs 0700; it stays 0755. This run's seed ISOs carry user_data
verbatim, and anyone who can read that directory can read them.
error: libvirtError: … Could not resolve hostname hypervisor.invalid …
exit=1
$ podman unshare find ./runs -printf '%M %U:%G %p\n'
drwxr-xr-x 0:0 ./runs
```
Empty. The identical failure without `--user` (`./runs:/runs:Z`, default layout)
**does** write `run.json` and `manifest.json`. The difference is that `/runs` is
0755 root-owned in the container, uid 4242 cannot write into it, and `_guard`'s
`contextlib.suppress(OSError)` at `orchestrator/cli.py:261-262` swallows the
`_record` failure by design. So under `--user`, `:U` gives an unreadable record and
`--run-dir` gives a silent absent one. That belongs to dimension B's question
("does every failure path write a complete `run.json`"), not to G — flagging it
here rather than filing it.

**Doc or code: doc.** The chown is podman's semantics, and `_run_dir`'s 0700 is
deliberate and defended at `orchestrator/cli.py:150-153` and pinned by
`tests/test_cli.py:191` and `:776`. Loosening either to make the host read the
directory would be the wrong fix. What is missing is a sentence at `README.md:62-64`
or `:91-94` saying the output is chowned too and that reading it back needs
`podman unshare`. G's caution about `--userns=keep-id:uid=4242` is correct and I
did not measure it either — do not let it into the README unmeasured.

**Severity: medium, confirmed.** The run directory is the deliverable an air-gapped
site ships home, and following the documented remedy makes it unreadable to the
person who asked for it — recoverable with `podman unshare`, which is why this is
not higher, and undiscoverable from anything the project ships, which is why it is
not lower.

---

## Notes

* No tracked file was modified. Nothing was written to
  `qemu+ssh://vcows@vcows/system`; the fixture URI is `hypervisor.invalid` and
  every run died in `getaddrinfo`.
* `scripts/image-scan.sh --write-baseline` was not run.
* Every `file:line` above was re-read at `672a500` before being written.
