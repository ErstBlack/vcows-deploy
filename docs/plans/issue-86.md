# Issue #86 — both rootless recipes in the README fail as written

Branch `lane/readme-rootless`, forked at `e555fe9` (= `origin/master`). The issue
was filed against `672a500`; **two merges landed in between that change exactly
the behaviour it documents**, so nothing in the issue body was taken on trust.

* **#85** (`8322c17`) guarded `_run_dir`'s `mkdir` and moved `path.resolve()`
  *before* it.
* **#80** (`18a43cb`) made a run that cannot write its own record say so.

Everything below was measured on `localhost/vcows-deploy-l7:0.1.0.0`, built from
this branch — `/opt/vcows/manifest.json` records
`git_sha e555fe9f1c1044e90d476de5edadd8d71f24a7ab`, no `-dirty`. podman 5.8.2
rootless, host uid 1000, umask 0022, SELinux enforcing. Fixtures in a `mktemp -d`:
`qemu-img create -f qcow2 images/golden.qcow2 8G`, `ssh-keygen -t ed25519` at
0600, a one-line `known_hosts`, and the README's own config verbatim with the URI
set to `qemu+ssh://vcows@hypervisor.invalid/system`. `.invalid` cannot resolve, so
every run stops at the libvirt connect and **nothing reached the rig**.

Raw transcripts: `docs/review-readme-rootless/reverify/RX-G2.txt`, `RX-G3.txt`.

## 1. Reverification verdict

**Both reproduce. Neither #85 nor #80 fixed either one.** What #85 changed is the
message, and it is the message the fix now quotes.

**RX-G2 — reproduced, and worse than the issue states.** All four verbs, the
README recipe verbatim (`--user 4242 --passwd-entry`, `:U` on the key, `/runs:Z`,
fresh empty `./runs`):

```
validate      → /config.yaml: valid (1 VMs, deployment 'lab-a')            exit=0
preflight     → error: libvirtError: … Could not resolve hostname hypervisor.invalid  exit=1
deploy        → vcows: cannot create the run directory /runs/lab-a/20260831T080720Z:
                Permission denied. …                                       exit=1
destroy --yes → vcows: cannot create the run directory /runs/lab-a/20260831T080721Z:
                Permission denied. …                                       exit=1
```

`podman unshare find ./runs` after each: `drwxr-xr-x 0:0 ./runs` and nothing
else. No run directory, both times.

**RX-G3 — reproduced exactly as recorded.** Same recipe plus `:U` on `/runs`: all
four verbs reach the connect, and

```
$ ls -lnd ./runs ; ls -lnA ./runs
drwxr-xr-x. 3 528529 1000 19 ./runs
drwx------. 3 528529 1000 30 lab-a
$ ls -ln ./runs/lab-a
ls: cannot open directory './runs/lab-a': Permission denied      exit=2
$ cat ./runs/lab-a/*/run.json
cat: './runs/lab-a/*/run.json': Permission denied                exit=1
$ rm -rf ./runs
rm: cannot remove './runs/lab-a': Permission denied              exit=1
$ podman unshare cat ./runs/lab-a/*/run.json
{ … "outcome": "failed" … }                                      exit=0
```

`drwx------ 528529:1000`, unreadable and unremovable by uid 1000, recoverable
only through `podman unshare`. Identical to the figure recorded at `672a500`.

**A third false claim in the same paragraph, which the issue does not name.** See
§3, C3.

## 2. Anchor table

Every anchor the issue cites, re-read at `e555fe9` before the edit.

| anchor as cited (at `672a500`) | state at `e555fe9` |
|---|---|
| `README.md:66` "With both, `preflight` and `deploy` run clean." | present, unmoved, **false** |
| `README.md:62-64` — the `:U` bullet and "the cost of chowning your host copy" | present, unmoved, true but half-priced |
| `README.md:91-94` — what `--run-dir` does | present, unmoved |
| `orchestrator/cli.py:150-153` — the deliberate `0700` | **moved to `:153-175`**; comment `:153-164`, guard `:165-175`, `os.chmod(path, 0o700)` at `:167` |
| `tests/test_cli.py:191` `test_the_run_directory_is_not_world_readable` | **moved to `:216-220`**, unchanged, still asserts `mode == 0o700` |
| `tests/test_cli.py:776` `test_nothing_in_the_run_directory_is_readable_by_anyone_else` | **moved to `:900-912`**, unchanged, still asserts `loose == []` |
| `docs/review-2026-08-29/2026-08-29-remediation-checklist.md:269` — the source measurement | present, unmoved |
| `4eb378b` — the commit that generalised it | confirmed: `git log -L66,68:README.md` shows the three lines entering there, already generalised, in the commit that created `README.md` |

Two anchors #86 does not cite that the reverification needed:

| anchor | state |
|---|---|
| `orchestrator/cli.py:121-123` — `path = (…).resolve()` **before** the mkdir | #85's move; this is why the message now names `/runs/lab-a/<ts>` and not `runs/lab-a` |
| `orchestrator/cli.py:136-140` — `except OSError` → `UsageError` | #85's guard; `main:771-773` prints it as `vcows: <sentence>`, exit 1 |

## 3. Corrections to the issue body

**C1 — the failure message the issue quotes no longer exists.** #86 inherits
RX-G2's transcript, which records `error: PermissionError: [Errno 13] Permission
denied: 'runs/lab-a'` — a raw errno on a *relative* path through `main`'s
catch-all. At `e555fe9` the same two verbs produce

```
vcows: cannot create the run directory /runs/lab-a/20260831T080720Z: Permission
denied. Every run writes its own directory; check the mount and the UID it is
owned by.
```

Absolute, and it names the mount. #85 fixed the legibility and left the recipe
broken, which is what its own §9 said it would do. The README fix quotes the new
message, not the old one.

**C2 — the pinning citations have all moved.** `orchestrator/cli.py:150-153` →
`:153-175`; `tests/test_cli.py:191` → `:216`; `:776` → `:900`. Both tests exist,
are unchanged, and still pin `0700`. Nothing here touches them.

**C3 — a third false claim, in the sentence pair being replaced.**
`README.md:67-68`: "With neither, a run directory on a foreign-UID mount also
stays `0755` and vcows tells you what that costs rather than failing." Measured
both readings of "with neither":

```
--user 4242, no --passwd-entry, no :U, default layout, ./runs:/runs:Z
  vcows: could not write /.ssh/config: …
  vcows: cannot create the run directory /runs/lab-a/20260831T081113Z: Permission denied. …
  exit=1
  podman unshare find ./runs  →  drwxr-xr-x 0:0 ./runs        (nothing was created)

no --user at all, same mounts
  error: libvirtError: … Could not resolve hostname hypervisor.invalid …   exit=1
  ./runs/lab-a  drwx------ 1000 1000                          (0700, not 0755)
```

Neither case produces a `0755` run directory. The `0755` warning fires only for
`--run-dir` pointed at the mount itself, which is what the source measurement
actually said — `…remediation-checklist.md:269`: "A `--run-dir` on a foreign-UID
mount stays `0755`". `4eb378b` dropped the `--run-dir` from that sentence in the
same commit and by the same compression that produced RX-G2.

**C4 — that `--run-dir` case is no longer silent, and the sentence overstates
what it now says.** Post-#80:

```
$ podman run … --user 4242 --passwd-entry … -v ./runs:/runs:Z … deploy --run-dir /runs /config.yaml
vcows: cannot make /runs 0700; it stays 0755. This run's seed ISOs carry user_data
verbatim, and anyone who can read that directory can read them.
vcows: this run left no record -- /runs/run.json could not be written (Permission
denied). The failure below is reported on this stream only.
error: libvirtError: … Could not resolve hostname hypervisor.invalid …
exit=1
$ podman unshare find ./runs -printf '%M %U:%G %p\n'
drwxr-xr-x 0:0 ./runs
```

"vcows tells you what that costs rather than failing" describes one of the two
messages. The run also leaves no record at all. That belongs to #80, which is
closed; see §9.

**C5 — the `:U` remedy is not the only one, and it is the worse one.** G's
finder named `--userns=keep-id:uid=4242` as the alternative and correctly refused
to recommend it unmeasured. Measured here, all four verbs, no `:U` anywhere:

```
$ podman run --rm --userns=keep-id:uid=4242,gid=0 --passwd-entry 'vcows:x:4242:0:vcows:/tmp:/bin/sh' … deploy /config.yaml
error: libvirtError: … Could not resolve hostname hypervisor.invalid …   exit=1
$ ls -lnR ./runs
drwx------. 1000 1000 lab-a/20260831T080927Z/{manifest.json,run.json}
$ cat ./runs/lab-a/*/run.json   → the record, as uid 1000, no podman unshare
$ rm -rf ./runs                 → rc=0
```

The key copy also stays `1000 1000` and readable. `--user` becomes redundant:
with the flag alone and no `--user`, `id` inside the container is
`uid=4242(4242) gid=0(root)` and the run behaves identically. `--passwd-entry` is
still required — without it the entrypoint still cannot write `/.ssh/config`,
which is bullet 1 and unrelated to the UID mapping.

**C6 — one claim in the bullet being edited could not be re-measured, and is
carried over unchanged.** `README.md:62-63`'s `Load key ...: Permission denied`.
`ssh` resolves the host before it reads the identity file, so a `.invalid`
fixture cannot reach it:

```
$ ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -i <mode-000 key> vcows@hypervisor.invalid true
ssh: Could not resolve hostname hypervisor.invalid: Name or service not known
exit=255
```

Reaching that error needs a resolvable host, and the only one available is the
real rig. The clause is left byte-identical and is not restated as newly
measured.

## 4. The defect

Not a code defect. `_run_dir` is behaving correctly: `/runs` is a bind mount
owned by the mapped host UID at `0755`, container uid 4242 cannot create a
subdirectory in it, and **vcows cannot chown a mount it does not own**. The lever
is podman's, on the invocation, and the README is where the invocation lives.

The failure is a documentation one with a traceable cause. The measurement exists
and is correct — `…remediation-checklist.md:269`, "With `--passwd-entry` giving a
writable home and `:U` on the key mount, **`preflight` runs clean**". `4eb378b`
wrote `README.md:66` as "`preflight` **and `deploy`** run clean" and
`README.md:67-68` as a `--run-dir` fact with the `--run-dir` removed. Two
generalisations of one measurement, in one commit, in the same three lines.

The consequence is bounded but real: this is the project's only `--user`
guidance, it is presented as measured, and it is wrong for **both** verbs that
change anything — including the teardown `README.md:22` says is the only way to
remove a VM. Following it to the letter gets `preflight` working and then stops.
Following the remedy it offers (`:U`) gets a run whose `run.json` — the artifact
`orchestrator/cli.py:228` calls "what an air-gapped site ships back" — the
operator cannot open, cannot delete, and has nothing in the repo telling them
`podman unshare` is the way in.

## 5. The fix

`README.md:53-68` replaced by `:53-84`. Nothing else in the file, nothing outside
it.

**What changes, claim by claim:**

| old | new | measured in |
|---|---|---|
| "two things lined up, not one" | "three things" | RX-G2, all four verbs |
| `:U` sentence inside the key bullet | moved out, into the remedies list | — |
| — | new third bullet: the run-directory mount, quoting the `e555fe9` message and naming which verbs are affected | RX-G2 |
| ":66 With both, `preflight` and `deploy` run clean." | deleted; replaced by "a clean `preflight` says nothing about `deploy`" | RX-G2 |
| ":67-68 With neither, a run directory … stays `0755` …" | deleted (C3) | RX-G2 §"README.md:67-68 checked directly" |
| — | `--userns=keep-id:uid=4242,gid=0`, named as the remedy that costs nothing | RX-G3 measurements C and D |
| ":63-64 at the cost of chowning your host copy" | kept, and extended to the output side: `drwx------`, the three commands that fail, `podman unshare` | RX-G3 |

**Rejected alternatives:**

* **O1 — say `deploy` needs `:U` on `/runs` and stop there.** This is what the
  finder's one-line fix proposed, and it is what RX-G3 exists to say is not
  enough. Documenting the remedy without its price is how `README.md:63` already
  reads for the key mount, and the price on the output side is larger: the key
  has a spare copy, the run record does not.
* **O2 — leave `--userns=keep-id` out, on surface grounds.** Six lines is real
  cost. Rejected because the alternative is a README that names a trap and no way
  around it. The operator's remaining move is to stop using `--user`, which the
  section exists to support. G's objection to `keep-id` was that it was
  unmeasured; §3 C5 measures it on all four verbs.
* **O3 — recommend `keep-id` and drop `:U` entirely.** Rejected: `:U` works, it
  is already in the README, and silently deleting a documented flag leaves anyone
  running it today with no explanation of the `drwx------` they are looking at.
  Both are listed, with the difference stated.
* **O4 — loosen the `0700`, or make `_run_dir` chmod the parent.** Ruled out by
  the issue and by the code. `orchestrator/cli.py:153-164` records why the chmod
  is skipped rather than enforced, and `tests/test_cli.py:216` and `:900` pin
  both halves. Neither was touched.
* **O5 — add the `--run-dir`-under-`--user` failure to `README.md:107-110`.**
  Rejected as scope. That is #80, closed, whose plan §9 deliberately left the
  README alone; documenting half of it (the mode, not the missing record) would
  be worse than the current silence. §9.
* **O6 — correct the `README.md:*` citations in `docs/plans/` and
  `docs/review-2026-08-*/`.** Rejected: those are records pinned to the commits
  they name, and each states its commit in its own header. The shift is recorded
  in `docs/review-readme-rootless/REVIEW.md` instead.

## 6. Surface cost

One file, `README.md`, +23/−7, net +16 lines. No new section, no new heading, no
code, no test, no gate. The `--user` paragraph goes from 16 lines to 32; it is
the only `--user` guidance the project ships and it now carries three failures
and two remedies instead of two failures and one.

Every line ≥69 in `README.md` shifts by +16. Nothing in `orchestrator/`,
`container/`, `tests/` or `scripts/` cites a `README.md` line number — the only
two references are `orchestrator/cli.py:156` ("README's `--user`", no number) and
`tests/test_cli.py:884` ("README:48-53"), and `:48-53` still spans the same
content because the edit begins at `:53`.

## 7. The failing test

**None was written, and one is possible — but not under this issue's constraints,
and not without new surface.** Stating both halves rather than the convenient one.

*What could be pinned.* `tests/test_image.py` already runs the shipped image
under podman behind `gate("image", …)`, and the RX-G2 failure happens in
`_run_dir`, **before anything connects** — so `--network=none`, which
`test_image.py:84` hardcodes, does not get in the way. Two assertions are
reachable:

1. the recipe the README used to recommend still fails, exit 1, with `cannot
   create the run directory` on stderr and an empty `./runs`;
2. the recipe it now recommends works: `--userns=keep-id:uid=4242,gid=0
   --passwd-entry …` leaves `run.json` on the host owned by `os.getuid()` and
   `stat().st_mode & 0o077 == 0` — the second half being the same property
   `tests/test_cli.py:900` asserts, checked through the UID mapping instead of
   in-process.

(2) is the one worth having. It is an assertion about the README's own
recommendation, it fails today for a real reason, and it would catch a podman or
entrypoint change that quietly breaks the recipe again.

*What it would cost, and why it is not here.* `test_image.py:83-95`'s `run()`
helper hardcodes `--network=none`, `:ro,Z` on every mount and no user flags. Both
tests need a writable mount and a `--userns`/`--passwd-entry` pair, so the helper
grows a signature or the file grows a second one. That is Python, and this lane
is documentation-only by the issue's own instruction — "**Both fixes target the
documentation.**" It is also a genuinely new dependency for the `image` gate:
`--userns=keep-id` needs `/etc/subuid` configured for the running user, which the
GitHub runner has and a `buildah`-only runner does not, and `gate()` demands a
skip reason that can be turned into a failure. That is a gate-design decision,
not a side effect of a README fix.

*What cannot be pinned here at all.* `tests/test_scripts.py` runs `scripts/*.sh`
in a tree it owns; the README is not a script and nothing in it is executed.
`tests/test_entrypoint.py:191` (`test_an_unwritable_home_says_what_it_costs`)
pins bullet 1's *mechanism* by monkeypatching an unwritable home, which is why
bullet 1 is the one claim in this section that has never drifted — but it proves
nothing about a podman invocation. And `README.md:62-63`'s `Load key …:
Permission denied` cannot be reached by any offline fixture at all (C6). A test
that extracted commands from the README and ran them would be new machinery of
the kind `docs/tooling-*.md` has rejected repeatedly; it is not proposed.

**The evidence this fix rests on is the transcript, not a test.** Both
transcripts are committed under `docs/review-readme-rootless/reverify/` with the
image's `git_sha`, the host's podman version and UID, and the exit status of
every command.

## 8. Verification

* Image built from this branch: `VCOWS_IMAGE_TAG=localhost/vcows-deploy-l7:0.1.0.0
  just image`. `/opt/vcows/manifest.json` → `git_sha
  e555fe9f1c1044e90d476de5edadd8d71f24a7ab`, no `-dirty`, so the container under
  test is this tree.
* Four verbs × two recipes, plus four adjacent measurements, all against
  `hypervisor.invalid`. Every run in both transcripts ends in `getaddrinfo` or
  earlier. `qemu+ssh://vcows@vcows/system` appears nowhere;
  `just smoke-libvirt` was not run.
* Each of the seven claims in the new text is traceable to a quoted line in one
  of the two transcripts — the table in §5 gives the mapping, and
  `docs/review-readme-rootless/REVIEW.md` lens 2 quotes them.
* `just check` on the patched tree: six lint gates ok, `ty` clean,
  **`430 passed, 25 skipped`** — the branch baseline, unchanged. Expected: no
  Python moved.
* Fixtures and the `:U`-chowned output removed with `podman unshare rm -rf`; the
  scratch image removed with `podman rmi`.

## 9. Non-goals

* **`--run-dir` under `--user`.** That is #80, closed. Its plan §9 says "nothing
  here edits the README", so the README still says nothing about it. The false
  half of `README.md:67-68` is deleted here because the sentence being replaced
  contained it; the true half is not re-documented anywhere, because saying "the
  mode stays 0755" without "and the record is not written" would be the same
  half-measurement that produced this issue. Worth a separate issue; not this
  one.
* **The `0700`, and the two tests that pin it.** `orchestrator/cli.py:167`,
  `tests/test_cli.py:216`, `tests/test_cli.py:900`. Untouched by instruction and
  by agreement — loosening any of them to make the host read the directory would
  trade a documentation gap for a secrets one.
* **#85's message.** Already landed, already correct, quoted here rather than
  changed.
* **`README.md:107-110`** (the `--run-dir` paragraph), which RX-G3 named as an
  alternative site for the ownership sentence. The sentence went to the bullet
  where the operator chooses `:U`, not to the paragraph that describes what the
  directory contains.
* **The `README.md:*` citations in `docs/`.** §5 O6.
* **`--userns=keep-id` on anything but this recipe.** Measured for
  `uid=4242,gid=0` with the README's own mounts and nothing else. It is named in
  the README as what was measured, not as general advice.
