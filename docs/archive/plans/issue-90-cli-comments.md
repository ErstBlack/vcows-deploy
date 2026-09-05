# Issue #90 — the two items lane L4 owns

**#90 carries eleven items across seven dimensions. This lane owns two of them,
both in `orchestrator/cli.py`.** The other nine — `docs/cve-baseline.json:4`,
`scripts/lib.sh:88-93`, `.github/workflows/image.yml:60-64`, `.gitlab-ci.yml:10-18`,
`container/entrypoint.py:189`, `destroy.py:474`, `destroy.py:462-464`,
`tofu/variables.tf:10`, `docs/findings.md:419` — and the stale
`CVE-2026-58055` baseline row belong to other lanes and are not planned here.
Nothing in this document touches them.

Reverified at `aed962d`. Raw output:
`docs/review/cli-records/reverify/issue-90-cli-148.txt` and
`issue-90-cli-658.txt`.

## 1. Reverification verdict

### 1.1 `cli.py:148-152` — the comment above the chmod guard

**#90's claim holds in both halves.**

First half — `EROFS` does escape `:156`'s `except PermissionError`. Through the
real image, `--run-dir /runs` with `/runs` mounted `:ro`:

```
error: OSError: [Errno 30] Read-only file system: '/runs'
exit=1
```

Isolated in the same image, this also settles *which* call raises:

```
exists= True is_dir= True mode= 0o755
Path.mkdir(exist_ok=True): returned, no exception
bare os.mkdir RAISED FileExistsError 17 EEXIST [Errno 17] File exists: '/runs'
stat mode & 0o077 = 0o55                 -> the guard at :153 is entered
os.chmod RAISED OSError 30 EROFS [Errno 30] Read-only file system: '/runs'
isinstance(OSError(EROFS,...), PermissionError)? False
```

`Path.mkdir(parents=True, exist_ok=True)` swallows the `EEXIST`, so `:134` is not
the raiser. The `os.chmod` at `:155` is, and `OSError(EROFS)` is not a
`PermissionError`, so it goes straight to `main`'s catch-all.

Second half — **widening the catch is the wrong fix**, measured rather than
asserted. A 0555 run directory handed in with `--run-dir`, `cli.os.chmod` stubbed
to raise `OSError(EROFS)`:

```
-- as shipped (except PermissionError) --
error: OSError: [Errno 30] Read-only file system: '/tmp/…/runs-ro'
main() returned 1

-- widened (except OSError) --
vcows: cannot make /tmp/…/runs-ro 0700; it stays 0555. This run's seed ISOs carry
user_data verbatim, and anyone who can read that directory can read them.
  app01                skip    exists as 'app01' (not compared)
  app02                skip    exists as 'app02' (not compared)
error: PermissionError: [Errno 13] Permission denied: '/tmp/…/runs-ro/run.json'
main() returned 1
```

The uncaught `OSError` names the cause and the path at the moment it happened.
The widened guard names the *mode*, lets the run walk on through a preflight it
will not be able to record, and dies later at `run.json` with an errno that never
mentions the read-only mount. **Fix the comment, not the code.**

The `EACCES` arm, for contrast — this is the case the guard exists for and it
still behaves exactly as documented:

```
vcows: cannot make /runs 0700; it stays 0755. …
error: libvirtError: … Could not resolve hostname hypervisor.invalid …
exit=1
```

### 1.2 `cli.py:658` — `cmd_version` catches two of four

**#90's claim holds, including the ordering half.**

```
TimeoutExpired  is SubprocessError: True   is TofuError/OSError: False
JSONDecodeError is ValueError:      True   is TofuError/OSError: False

TofuError          -> main() returned 0     (tofu: unavailable (boom))
OSError            -> main() returned 0     (tofu: unavailable ([Errno 2] …))
TimeoutExpired     -> main() returned 1     (error: TimeoutExpired: …)
JSONDecodeError    -> main() returned 1     (error: JSONDecodeError: …)
```

All four are reachable. `_capture` (`tofu.py:238-264`) raises `TofuError` from
`_binary()` and from a non-zero exit, `subprocess.TimeoutExpired` from
`timeout=SHORT_TIMEOUT`, `OSError` from the exec, and `json.JSONDecodeError` from
`json.loads(completed.stdout or "{}")`.

Ordering: `vcows-deploy 0.1.0.0` precedes the error in all four arms, and inside
the image the manifest block prints in full before the tofu line:

```
$ podman run --rm localhost/vcows-deploy:0.1.0.0 version
vcows-deploy 0.1.0.0
image   672a500a5f3db394e91a3b91fb383517e504246d built 2026-08-31T03:52:16Z
base    quay.io/rockylinux/rockylinux:10@sha256:827d37bc…
provider registry.opentofu.org/dmacvicar/libvirt 0.9.8
packages 160 from 115 sources
tofu 1.12.6 on linux_amd64
```

So the regression `cli.py:636-638` records — "the one command that answers 'which
build is this' answered nothing at all on exactly the image somebody would be
asking about" — does not recur. **The whole consequence is the exit code.**

## 2. Anchor table

| anchor | state |
|---|---|
| `cli.py:148-152` — the four-line comment above the guard | ok |
| `cli.py:153` — `if path.stat().st_mode & 0o077:` | ok |
| `cli.py:154-155` — `try:` / `os.chmod(path, 0o700)` | ok |
| `cli.py:156` — `except PermissionError:` | ok |
| `cli.py:157-163` — the message, which names the mode | ok |
| `cli.py:134` — `path.mkdir(parents=True, exist_ok=True)` | ok, and **not** the raiser in the EROFS case |
| `cli.py:333-337` — `_tofu_version`'s four-exception tuple at `:335` | ok |
| `cli.py:653-662` — `cmd_version`; `:654` VERSION, `:655` `_print_manifest()`, `:656-660` the two-exception tuple at `:658` | ok |
| `cli.py:636-638` — the recorded regression this ordering exists for | ok |
| `tofu.py:238-264` — `_capture`, the source of all four exception classes | ok |
| `tests/test_cli.py:757-772` — the test that pins the `PermissionError` branch | ok |

## 3. Corrections to the issue body

**C1 — `cli.py:148-152` is not the whole comment `EROFS` falsifies.** The issue
cites `:148-152`, which is the block ending "a bind mount owned by another UID
(README's `--user`) refuses it, and that must not stop a run that is otherwise
fine." That sentence is *true* for `EACCES` and silent about `EROFS`. The
sentence that is actually wrong is narrower and is inside it: "the chmod is the
half that can fail" implies the guard covers the ways it can fail, and it covers
one of two. Correct that clause and add the `EROFS` case; do not rewrite the
paragraph, whose `--user` reasoning is right and is pinned by a test.

**C2 — "widening the catch is the wrong fix" needed the second half measuring,
and it survives it.** The issue asserts the widened guard "would name the mode
and defer the failure". Measured (§1.1): it does both, and the deferred failure
lands on a message strictly worse than the one it replaced.

**C3 — `cli.py:658` is described as "zero-cost".** It is two words in an
exception tuple, so the code change is. The *test* is not zero-cost: nothing in
`tests/test_cli.py` currently drives `cmd_version` past the happy path
(`test_version_prints_the_single_definition` at `:89` asserts only that `VERSION`
appears). §5.2 costs it honestly.

**C4 — one thing the issue does not say.** `_tofu_version:322-337` and
`cmd_version:653-662` are two callers of the same `tofu.version()` with
deliberately different policies: `_tofu_version` must not fail a good deploy, and
`cmd_version` may report `tofu: unavailable` and still exit 0. The divergence in
the tuples is not a policy difference — both intend "report and carry on" — so
aligning them is a correction, not a policy change. Say so, or the next reader
will assume the narrower tuple was chosen.

## 4. The defect

### 4.1 `cli.py:148-152` — a comment that describes one errno as if it were all of them

The code is right. `EROFS` reaching `main` produces
`OSError: [Errno 30] Read-only file system: '/runs'`, which names the mount, the
cause, and nothing else the operator has to infer. The guard exists for the
foreign-UID mount, where continuing is correct because the run *can* still be
written. On a read-only mount it cannot, and stopping is right.

What is wrong is that the comment does not say any of that. A reader who takes
"the chmod is the half that can fail" at face value, and then sees `EROFS` escape,
will conclude the guard is too narrow and widen it — which §1.1 measures as a
regression. The comment is the thing that makes the wrong fix look right.

### 4.2 `cli.py:658` — two of four

`_tofu_version:335` catches four classes for a call `cmd_version:657` makes
identically. `cmd_version:658` catches two. `subprocess.TimeoutExpired` and
`json.JSONDecodeError` therefore escape `cmd_version` and reach `main`'s
catch-all: `error: TimeoutExpired: …`, exit 1.

`version` is a diagnostic command. Its contract, stated by the very structure of
`:653-662`, is "print what this build is, then say what OpenTofu is here, and do
not fail because the second half could not be answered". A slow `tofu` and a
`tofu` that prints something unparseable are exactly the states the command is
run *to discover*, and they are the two it turns into a failure. Because
`_print_manifest()` runs first, the operator still sees the build; they just get
exit 1 and a traceback-shaped last line for it.

## 5. The fix

### 5.1 `cli.py:148-152` — comment only, no behaviour change

Amend the block so it names both errno families and says why only one of them is
survivable. Something of this shape, keeping the existing `--user` sentences
intact:

```python
    # `main` sets a 0o077 umask, so a directory vcows created is already private
    # and this has work to do only for one an operator handed us. Skipped when it
    # is already tight, because the chmod is the half that can fail: a bind mount
    # owned by another UID (README's `--user`) refuses it with EACCES, and that
    # must not stop a run that is otherwise fine.
    #
    # Only EACCES. EROFS -- `/runs` mounted `:ro` -- is deliberately *not* caught:
    # a run directory that cannot be chmod'ed because the filesystem is read-only
    # cannot be written to either, and the uncaught OSError names the mount
    # (`Read-only file system: '/runs'`). Widening this to `except OSError` was
    # measured: it reports the mode instead of the cause and defers the failure to
    # `run.json`, whose errno never mentions the mount.
```

**Rejected: `except OSError`.** §1.1 measures it. It replaces a message naming
the cause with one naming the mode, and moves the failure to a later, less
informative point.

**Rejected: `except (PermissionError, OSError) as exc:` with an errno test.** A
branch on `exc.errno == errno.EROFS` re-raising is the same behaviour as today
written in five more lines and one more import.

**Rejected: pre-testing writability at `:153`.** A probe answers a question the
chmod already answers, and adds a TOCTOU gap.

### 5.2 `cli.py:658` — align the tuple with `:335`

```python
    except (tofu.TofuError, subprocess.SubprocessError, ValueError, OSError) as exc:
```

Identical to `:335`, for the identical call. `subprocess` is already imported
(`cli.py:32`).

**Rejected: catching `Exception`.** It would also swallow a `KeyError` or an
`AttributeError` from a genuine bug in `_capture`, which should reach `main`.
The four-class tuple is the set `_capture` can actually raise, established by
reading it (§1.2), and it is already the set `:335` chose.

**Rejected: extracting a shared helper for the two call sites.** `_tofu_version`
takes a `workdir`, appends a `Problem` to the run (after issue #89's RX-B6) and
returns `dict | None`; `cmd_version` takes none, prints a formatted line and
returns an exit code. The only thing they share is the tuple. A helper here
would be surface for a four-name literal.

**Note on ordering with #89.** Issue #89's RX-B6 also edits `_tofu_version`, and
its fix changes the function's signature. The two do not collide — RX-B6 changes
`:333-337`'s body, this changes `:658` — but if #89 lands first, re-read `:335`
before copying the tuple, since RX-B6's patch keeps it and a future one might not.

### 5.3 Ordering against issue #85

**#85 lands first.** #85 edits `cli.py:121-123` and `:134-135`, both above
`:148-152`, so it shifts the line numbers this document's first item sits on.
This item is a comment with no behaviour, so rebasing it onto #85 costs nothing;
the reverse order would make #85's diff appear to touch a comment it does not
change, and would leave the amended comment's own line citations stale on
arrival. Sequence: **#85 → this → (#89 and #80 in either order)**.

## 6. Surface cost

| change | cost |
|---|---|
| `cli.py:148-152` | +8 comment lines, 0 lines of code, 0 tests |
| `cli.py:658` | 2 words in an existing tuple, +1 test with 2 parametrisations |

No new function, no new import, no new file.

## 7. The failing test

**5.1 has no test, deliberately.** It changes no behaviour, and a test asserting
the text of a comment is the wrong instrument. What holds it honest is the
existing `test_a_run_dir_that_cannot_be_made_private_says_which_mode_it_wanted`
(`tests/test_cli.py:757-772`), which pins the `EACCES` branch, plus the new test
below sitting one function away as the reader's next stop.

**5.2 gets one**, in `tests/test_cli.py` beside
`test_version_prints_the_single_definition` (`:89`):

```python
@pytest.mark.parametrize(
    "raised",
    [
        subprocess.TimeoutExpired(["tofu", "version"], 30),
        json.JSONDecodeError("Expecting value", "not json", 0),
    ],
)
def test_version_survives_every_way_tofu_version_can_fail(monkeypatch, capsys, raised):
    """`version` is the command you run *because* something is wrong with the
    build. `_tofu_version:335` already names the four classes `_capture` can
    raise for this same call; `cmd_version` named two, so a slow `tofu` and a
    `tofu` printing something unparseable -- the two states this command exists
    to discover -- exited 1."""

    def boom(*a, **k):
        raise raised

    monkeypatch.setattr(cli.tofu, "version", boom)
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert VERSION in out, "the build is still reported first"
    assert "tofu: unavailable" in out
```

Both parametrisations fail today: `cli.main(["version"])` returns 1, having
printed `error: TimeoutExpired: …` / `error: JSONDecodeError: …` to stderr.

`json` and `subprocess` need importing into `tests/test_cli.py`; `json` is
already there (`:17`), `subprocess` is not.

**No conditional skip.** This is `monkeypatch` and `capsys`, nothing gated.
`tests/test_gates.py` AST-walks the suite and fails on a bare `pytest.skip`,
`pytest.importorskip` or `pytest.mark.skip`, and this introduces none; a gate, if
one were ever needed, goes through `conftest.gate()` (`tests/conftest.py:44`) or
`conftest.require()` (`:61`).

## 8. Verification

1. `just check` — six lint gates, `ty` clean, **413 passed, 25 skipped**
   (baseline at `aed962d` is 411/25, measured in the worktree; +2 parametrisations).
2. Teeth: revert `:658` to `(tofu.TofuError, OSError)`, keep the test → both
   parametrisations fail on `cli.main(["version"]) == 0`. Restore → green.
3. `test_version_prints_the_single_definition` (`:89`) and
   `test_a_run_dir_that_cannot_be_made_private_says_which_mode_it_wanted`
   (`:757`) must stay green **unchanged**. If either moves, something other than
   a comment and a tuple changed.
4. The comment change is verified by re-running §1.1's two arms after the edit
   and confirming both messages are **byte-identical** to the ones recorded in
   `docs/review/cli-records/reverify/issue-90-cli-148.txt`. That needs a rebuilt
   image (`just image`) only because the transcript was taken through one; the
   local 0555 harness in the same transcript reproduces the same pair without a
   container and is sufficient.

## 9. Non-goals

* **The other nine #90 items and the stale baseline row.** Named at the top,
  owned elsewhere. In particular `docs/cve-baseline.json` must not be touched
  from this lane, and `scripts/image-scan.sh --write-baseline` must not be run.
* **Widening `except PermissionError`.** §5.1. The measurement is the reason.
* **Refactoring `cmd_version` and `_tofu_version` onto a shared helper.** §5.2.
* **The `0700` policy, the umask, or the `--user` guidance.** Untouched. RX-G2
  and RX-G3 are separate findings with their own issues.
* **`cmd_version`'s exit code on a `tofu` that is genuinely absent.** It is 0
  today and stays 0 — that is the documented behaviour of a diagnostic command,
  not a defect.
