# Lane review — `lane/readme-rootless`, issue #86

Input: `git diff origin/master...lane/readme-rootless` and nothing else. One file
changed, `README.md`, +23/−7. Reviewer default is REFUTED; everything below was
checked against the diff and against the two transcripts under `reverify/`.

Plan: `docs/plans/issue-86.md`. Transcripts:
`docs/review-readme-rootless/reverify/RX-G2.txt`, `RX-G3.txt`.

## Lens 1 — did it do what the plan said

The plan's §5 table lists seven changes. All seven are in the diff, and the diff
contains nothing else.

| §5 row | in the diff | verdict |
|---|---|---|
| "two things lined up" → "three things" | `:53` | done |
| `:U` sentence moved out of the key bullet | `:62-63` now ends at `Permission denied.` | done |
| new third bullet, quoting the `e555fe9` message, naming the verbs | `:64-69` | done |
| `:66` "With both, `preflight` and `deploy` run clean." deleted | gone; replaced by ":68-69 `validate` and `preflight` create no run directory and are unaffected, so a clean `preflight` says nothing about `deploy`." | done |
| `:67-68` the `0755` sentence deleted | gone | done |
| `--userns=keep-id:uid=4242,gid=0` named | `:74-78` | done |
| the `:U` price extended to the output side | `:79-84` | done |

The plan's constraints were kept:

* **No code.** `git diff --stat` is one file. `orchestrator/cli.py:167` and its
  guard, `tests/test_cli.py:216` and `:900` — the three anchors the issue said
  not to touch — are untouched. `just check` on the patched tree: six lint gates
  ok, `ty` clean, `430 passed, 25 skipped`, the branch baseline.
* **No rig contact.** Every run in both transcripts ends in `getaddrinfo` on
  `hypervisor.invalid` or earlier. `qemu+ssh://vcows@vcows/system` appears in
  neither. `just smoke-libvirt` was not run.
* **The rejected alternatives are recorded** (§5 O1–O6) rather than left as
  silence, including the one this lane took that the finder had explicitly
  parked (`keep-id`), with the measurement that unparked it.

One deviation from the plan as first written: §5's row for `:66` said the
replacement would read "a `preflight` that reaches the hypervisor does not mean
`deploy` will". It says "a clean `preflight` says nothing about `deploy`"
instead, because no run in either transcript reached a hypervisor — the fixture
stops at DNS. The plan was corrected to match the text, not the other way round.

## Lens 2 — is every claim in the new text one that was measured

Nineteen assertions in the new text. Eighteen have a transcript line; the
nineteenth is disclosed as carried over. Line numbers below are the new
`README.md`.

| # | claim | transcript line |
|---|---|---|
| 1 | `:53` three things, not two | RX-G2: three distinct failures across the four verbs |
| 2 | `:64` the run directory mount "is owned by that same UID and is `0755`" | RX-G2 §"The /runs mount as uid 4242 sees it": `drwxr-xr-x. 2 0 0 6 /runs`, and `id` → `uid=4242(4242) gid=0(root)` |
| 3 | `:64-65` uid 4242 cannot create `runs/<deployment>/<timestamp>` inside it | RX-G2 deploy: `vcows: cannot create the run directory /runs/lab-a/20260831T080720Z: Permission denied.` |
| 4 | `:65-67` `deploy` **and `destroy`** stop, with that message | RX-G2 destroy: `… /runs/lab-a/20260831T080721Z: Permission denied.` `exit=1` |
| 5 | `:66` "stop before connecting" | RX-G2: the `deploy` and `destroy` blocks carry that message and no `libvirtError`; `preflight` carries the `libvirtError` and nothing else |
| 6 | `:67` "and write nothing" | RX-G2 after both: `podman unshare find ./runs` → `drwxr-xr-x 0:0 ./runs`, one line |
| 7 | `:68` `validate` and `preflight` create no run directory | RX-G2 after each: same one-line `find` output; `validate` `exit=0`, `preflight` `exit=1` at the connect |
| 8 | `:74` `keep-id:uid=4242,gid=0` maps your own UID to 4242 | RX-G3 D: `id` inside → `uid=4242(4242) gid=0(root)` |
| 9 | `:74-76` "both mounts already have the owner they need" | RX-G3 C: no `:U` anywhere, `deploy` reaches the connect and writes its record |
| 10 | `:76` "Nothing on the host is chowned" | RX-G3 C: `secrets/id_ed25519.k` still `1000 1000`; `./runs/**` all `1000 1000` |
| 11 | `:76` "the run directory comes back owned by you" | RX-G3 C: `drwx------. 4 1000 1000 lab-a`, and `cat ./runs/lab-a/*/run.json` `exit=0` as uid 1000 |
| 12 | `:76-77` "It sets the container UID itself, so `--user` becomes redundant" | RX-G3 D: the flag alone, no `--user`, `id` → `uid=4242`, record written |
| 13 | `:77-78` "the writable home is still needed" | RX-G3 B: `keep-id` without `--passwd-entry` → `vcows: could not write /.ssh/config: [Errno 13] Permission denied: '/.ssh'` |
| 14 | `:78` "all four verbs behave" | RX-G3 C: `validate` `exit=0`; `preflight`/`deploy`/`destroy --yes` each reach the connect, `exit=1`, two records on disk |
| 15 | `:79-80` `:U` chowns the host paths "to the subuid backing 4242" | RX-G3: `drwxr-xr-x. 3 528529 1000 ./runs`; `podman unshare` shows the same objects as `4242:0` |
| 16 | `:80` "It also works" | RX-G3: all four verbs reach the connect |
| 17 | `:80-81` "Your key copy stops being yours" | RX-G3: `-rw-------. 1 528529 1000 secrets/id_ed25519.u`, `head` → `Permission denied` |
| 18 | `:81-82` `./runs/<deployment>` lands `drwx------` owned by a subuid | RX-G3: `drwx------. 3 528529 1000 30 lab-a` |
| 19 | `:82-84` `ls`, `cat` and `rm -rf` all answer `Permission denied`, and `podman unshare` reads it back | RX-G3: `exit=2`, `exit=1`, `exit=1` respectively; `podman unshare cat …` `exit=0` with the JSON |

**The one claim with no measurement behind it, and it is pre-existing.**
`:62-63`, "the mounted 0600 key is owned by the mapped host UID, so uid 4242
cannot read it: `Load key ...: Permission denied`". Carried over byte-identical
from `4eb378b`; the diff only removes the `:U` sentence that followed it. It
could not be re-measured here: `ssh` resolves the host before it reads the
identity file, so the `.invalid` fixture stops one step short —

```
$ ssh -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -i <mode-000 key> vcows@hypervisor.invalid true
ssh: Could not resolve hostname hypervisor.invalid: Name or service not known
exit=255
```

— and reaching it would need a resolvable host, which here means the rig. The
commit body and plan §3 C6 both say so. **Not fixed, not hidden.**

Two claims that are *quotations*, checked rather than measured: `:83-84`'s "the
`run.json` an air-gapped site ships home" restates
`orchestrator/cli.py:228`, re-read at HEAD; `:64`'s `runs/<deployment>/<timestamp>`
matches `orchestrator/cli.py:122`.

## Lens 3 — what moved

**Every `README.md` line at 69 or below is unchanged; every line at 69 or above
in the old file is now at +16.** Spot-checked in both directions: old `:107`
`## The config` → new `:123`; old `:226` → new `:242`; old `:247` → new `:263`;
old `:306` → new `:322`.

Sixty `README.md:<line>` citations across `docs/` point at a line ≥69 and are now
off by 16. None were edited, on the reasoning in plan §5 O6: the dated review
directories and the per-issue plans are records pinned to the commits they name,
and each states its commit in its own header. Rewriting them would make them
disagree with the trees they describe. The ones a reader is most likely to
follow, and their new targets:

| pointer | old | new |
|---|---|---|
| `docs/tooling-2026-08-29.md:78` | `README.md:226` | `:242` |
| `docs/tooling-2026-08-30.md:48` | `README.md:247` | `:263` |
| `docs/tooling-2026-08-30.md:105` | `README.md:259-271` | `:275-287` |
| `docs/tooling-2026-08-30.md:131` | `README.md:281` | `:297` |
| `docs/plans/issue-76.md:84` | `README.md:306` | `:322` |
| `docs/plans/issue-80.md:14` | `README.md:109-135` | `:125-151` |
| `docs/plans/issue-80.md:73`, `:93` | `README.md:91-94` | `:107-110` |
| `docs/review-2026-08-30/ledger/s1-s6.md:20` | `README.md:78`, `:91-94` | `:94`, `:107-110` |
| `docs/review-2026-08-31/verify/G-mediums.md:10` | `README.md:109-135` | `:125-151` |

**Text this change deleted, and who cites it.** `README.md:66` and `:66-68` no
longer exist as written. Five live pointers name them:
`docs/plans/issue-80.md:78` and `:292`, `docs/plans/issue-85.md:261`,
`docs/review-2026-08-31/REVIEW.md:140`, `:162`, and
`docs/review-2026-08-31/verify/G-mediums.md:74-115`. All five are records of why
this issue existed, and all five now point at the fix rather than the defect —
which is the correct end state for a finding, not drift to repair.

**Line numbers this change did *not* move.** `CLAUDE.md:28` (`README.md:7`),
`tests/test_cli.py:884` (`README:48-53`), and the `:49-52`, `:59-62`, `:60`,
`:62`, `:63`, `:66` citations in `docs/review-2026-08-29/` — the edit begins at
`:53` and the first content shift is at `:64`, so anything at or below `:63`
still resolves. `orchestrator/cli.py:156` and `Containerfile:220` reference the
README without a line number and are unaffected.

**Nothing outside `docs/` cites a `README.md` line number**, so no code, test or
script needs a follow-up.

## Ledger

**Raised**

| id | item | disposition |
|---|---|---|
| L1 | `:62-63`'s `Load key …: Permission denied` is carried over unmeasured | **accepted, disclosed.** Unreachable from any `.invalid` fixture; measuring it needs the rig, which this lane may not touch. Pre-existing text, not a new claim. |
| L2 | `--userns=keep-id` adds six lines to a project that treats surface as a defect | **accepted with reason** (plan §5 O2). The alternative is a README that names a trap and no way out of it; the flag is measured on all four verbs, which is the objection G raised against naming it. |
| L3 | `README.md:107-110` still says nothing about `--run-dir` under `--user`, which now warns twice and writes nothing | **out of scope, recorded.** #80's territory, closed, whose plan §9 deliberately left the README alone. Documenting the mode without the missing record would repeat the half-measurement that produced this issue. Worth a separate issue. |
| L4 | The section header says `--user` "needs three things" and the first remedy then makes `--user` redundant | **accepted.** Both are true and the text says which. Rewriting the header around `keep-id` would demote the flag the operator actually asked about. |
| L5 | `gid=0` in `keep-id:uid=4242,gid=0` was measured and never varied | **accepted.** The README states what was measured, in the form it was measured. No claim is made about other gids. |
| L6 | Sixty `docs/` citations are now off by 16 | **accepted, listed above** (plan §5 O6). |
| L7 | No test pins the recipe the README now recommends | **accepted, argued** (plan §7). A `tests/test_image.py` test is possible and worth having; it needs Python, a second `run()` helper, and a `/etc/subuid` dependency for the `image` gate. Not a side effect of a README fix. |

**Confirmed**

| id | item |
|---|---|
| C1 | RX-G2 reproduces at `e555fe9`, on **both** mutating verbs. The finder's original generalisation from `preflight` is the defect; the verifier's addition of `destroy --yes` holds. |
| C2 | RX-G3 reproduces at `e555fe9`, to the same figure: `drwx------ 528529:1000`, three commands denied, `podman unshare` the only way in. |
| C3 | The `0700` is deliberate and pinned. `orchestrator/cli.py:153-175`, `tests/test_cli.py:216-220` and `:900-912` all present, unchanged, untouched by this diff. |
| C4 | The security fact deleted along with `:67-68` is not lost: `README.md:227-230` still carries "It is created `0700` **and it carries secrets** … those ISOs contain `user_data` verbatim." |
| C5 | `4eb378b` introduced both generalisations, in the commit that created `README.md`. `git log -L66,68:README.md` returns that commit and no other. |

**Refuted**

| id | item |
|---|---|
| F1 | #86's quoted failure — `error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'` — no longer occurs. #85 replaced it with a `UsageError` naming the resolved path. The issue body is stale; the fix quotes the current message. |
| F2 | #86's pinning citations. `orchestrator/cli.py:150-153` is now `:153-175`; `tests/test_cli.py:191` is now `:216`; `:776` is now `:900`. All three moved in `e555fe9`. |
| F3 | `README.md:67-68` — a third false claim the issue does not name. Neither reading of "with neither" produces a `0755` run directory: under `--user` none is created at all, and without `--user` vcows creates it `0700`. The `0755` warning is a `--run-dir` behaviour, which is what `…remediation-checklist.md:269` said before `4eb378b` compressed it. |
| F4 | G's "`--userns=keep-id:uid=4242` … is untested here and should not be recommended unmeasured." The premise was correct; the state it described no longer holds. Measured on all four verbs, and it is the better of the two remedies. |

**Downgraded**

| id | item |
|---|---|
| D1 | RX-G2's severity framing, "the README's only `--user` guidance leaves the operator unable to run either mutating verb." Still true of the recipe, but the consequence is smaller than at `672a500`: the operator is now handed an absolute path and a sentence naming the mount rather than a relative path and an errno class. The defect is the guidance, not the diagnosis. |
| D2 | RX-G3's "undiscoverable from anything the project ships". True at `672a500`; false after this commit, which is the whole of the fix. |
