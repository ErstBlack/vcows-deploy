# Issue #85 — `_run_dir`'s mkdir is unguarded, so a bad `--run-dir` gives a raw errno

Lane L4. Reverified at `aed962d`. Raw output:
`docs/review/cli-records/reverify/RX-G1.txt`.

## 1. Reverification verdict

**REPRODUCED**, on both errno families and on both verbs that call `_run_dir`.
Third independent reproduction of RX-G1 (G, then G's verifier, then this).

Same fixtures and the same `hypervisor.invalid` URI as issue #80's plan; image
`localhost/vcows-deploy:0.1.0.0` (`84dcf01a718d`, revision `672a500`, and no
image-shipped path changed between `672a500` and `aed962d`).

**EACCES — `--user 4242` with no writable home:**

```
deploy        → error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'   exit=1
destroy --yes → error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'   exit=1
preflight     → error: libvirtError: … Could not resolve hostname hypervisor.invalid  exit=1
```

**EROFS — `/runs` mounted `:ro`, no `--user` at all:**

```
deploy        → error: OSError: [Errno 30] Read-only file system: 'runs/lab-a'   exit=1
destroy --yes → error: OSError: [Errno 30] Read-only file system: 'runs/lab-a'   exit=1
preflight     → error: libvirtError: … Could not resolve hostname hypervisor.invalid  exit=1
```

`preflight` reaching the connect in both is the positive control for the
two-caller claim: it does not call `_run_dir`, so it is untouched.

**Test coverage claim holds.** `grep -n mkdir tests/test_cli.py` returns nine
hits, all fixture setup. The two at `:751` and `:764` are `given.mkdir(mode=0o755)`
for the chmod tests, and `:769` monkeypatches `cli.os.chmod`, not the mkdir. No
test exercises a failing `_run_dir` mkdir.

## 2. Anchor table

| anchor | state |
|---|---|
| `orchestrator/cli.py:63` — `class UsageError(Exception)` | ok |
| `orchestrator/cli.py:66-69` — "deliberately not a raw ``OSError`` reaching ``main``'s catch-all" | ok, and it names this exact failure |
| `orchestrator/cli.py:121-123` — `path = (Path(override) if override else …)` | ok |
| `orchestrator/cli.py:128-133` — the is-a-file `UsageError` | ok |
| `orchestrator/cli.py:134` — `path.mkdir(parents=True, exist_ok=True)`, no `except` | ok |
| `orchestrator/cli.py:135` — `path = path.resolve()`, **after** the mkdir | ok |
| `orchestrator/cli.py:314` — `deploy`'s `_run_dir` call | ok |
| `orchestrator/cli.py:491` — `destroy`'s `_run_dir` call | ok |
| `tests/test_cli.py:466`, `:486`, `:503`, `:746`, `:757`, `:776` — the six `_run_dir` tests | ok, none covers a failing mkdir |
| `Containerfile:223` — `WORKDIR /` | ok |

## 3. Corrections to the issue body

**C1 — "Both carry a relative path" is true for the case measured and not in
general.** The path in the message is whatever the operator gave, unresolved. The
default layout is relative (`Path("runs")/…`), so it prints `runs/lab-a`. A
`--run-dir` passed as an absolute path already prints an absolute one — measured
while reverifying #90's `cli.py:148` item, where the same mount produces
`OSError: [Errno 30] Read-only file system: '/runs'`. The fix must therefore
*resolve* rather than merely re-word, and it should do so for every message in
the function, not just the new one.

**C2 — the two reproductions in the issue body are the same code path, not two.**
Both errno families reach the identical `mkdir` at `:134`, because `WORKDIR` is
`/` (`Containerfile:223`) and so the default `Path("runs")/deployment/timestamp`
resolves to `/runs/lab-a/<ts>` — inside the very mount the operator got wrong.
That is the reason the errno is the operator's whole diagnostic: the message
names a relative path that does not visibly correspond to any `-v` argument they
typed.

**C3 — the issue's fix sketch says "move or duplicate the `resolve()`".** Moving
it is the better half and the issue does not say where to. See §5: it moves to
`:121-123`, not to just before the mkdir, because two *other* messages in the
same function have the same relative-path problem.

**C4 — one thing the issue does not say.** `preflight` and `validate` are
unaffected, measured. Worth stating in the commit body because it bounds the
blast radius of the change to the two mutating verbs.

## 4. The defect

`_run_dir` is the function that exists to turn a bad `--run-dir` into a sentence.
`UsageError`'s docstring (`cli.py:66-69`) says so:

> deliberately not a raw ``OSError`` reaching ``main``'s catch-all, which would
> print ``error: FileExistsError: /runs/lab-a`` and leave the operator to work
> out which of the two paths they passed it means.

Two of the three failure modes get that treatment — the is-a-file branch at
`:128` and the not-empty branch at `:136`. The third, the mkdir itself at `:134`,
has no `except`, so `EACCES` and `EROFS` reach `main`'s catch-all as exactly the
shape the docstring rejects. And because `resolve()` is at `:135`, one line
*after* the failure, the message carries the unresolved path — for the default
layout, a bare `runs/lab-a` with no leading `/` and nothing tying it to the
`/runs` bind mount.

The operator's information is then: an exception class name, an errno, and a
relative path. Everything that would let them find the wrong `-v` argument is
absent.

## 5. The fix

Two edits in one function.

```python
    path = (
        Path(override) if override else Path("runs") / cfg["deployment"] / _timestamp()
    ).resolve()
    …
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UsageError(
            f"cannot create the run directory {path}: {exc.strerror}. Every run "
            f"writes its own directory; check the mount and the UID it is owned by."
        ) from exc
```

`resolve()` moves from `:135` up to the assignment at `:121-123`, and the mkdir
gets one `except OSError`. `Path.resolve()` is non-strict by default, so it works
on a path that does not exist yet; the existing `:135` call is then redundant and
is deleted rather than duplicated.

Moving `resolve()` to the top rather than to just above the mkdir is deliberate:
it fixes the same relative-path problem in the is-a-file message at `:130` and the
not-empty message at `:138`, which have it for exactly the same reason. It changes
no control flow — `Path.exists()` and `Path.is_dir()` already follow symlinks, so
resolving first does not change which branch is taken.

Prototyped in a scratch copy of `orchestrator/` + `tests/`:

```
before:  error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
after:   vcows: cannot create the run directory /tmp/…/runs/lab-a/20260831T061027Z:
         Permission denied. Every run writes its own directory; check the mount and
         the UID it is owned by.
```

`ruff check`, `ruff format --check` and `ty check` all clean on the prototype; the
suite is unchanged.

**Rejected alternatives**

* **O1 — catch only `PermissionError`.** It is the errno the issue leads with, and
  it would miss `EROFS`, which was reproduced just as easily and by a mistake at
  least as common (`:ro` on the wrong mount). `OSError` is the family the
  docstring at `:67` names.
* **O2 — leave `resolve()` at `:135` and build the absolute path separately for
  the message** (`Path.cwd() / path`). Works, but duplicates the normalisation
  and leaves `:130` and `:138` still printing relative paths. More lines for less
  effect.
* **O3 — `resolve(strict=True)` before the mkdir to pre-classify.** Raises
  `FileNotFoundError` for every legitimate first run. Wrong shape.
* **O4 — probe writability before the mkdir.** A second syscall to learn what the
  mkdir is about to tell us, and it introduces a TOCTOU gap. The mkdir is the
  authority.
* **O5 — a new exception type for "cannot create".** `UsageError` already means
  "the command cannot run as invoked, and the reason is a sentence" (`:64`),
  which is precisely this. A new type is surface for nothing.

## 6. Surface cost

One function in `orchestrator/cli.py`. Net +5 lines (`resolve()` moved onto an
existing line, one `try`, four for the `except`/`raise`). No new type, no new
import, no new call site, no behaviour change on any path that succeeds today.
One new test.

## 7. The failing test

**File:** `tests/test_cli.py`, beside the five other `_run_dir` tests at
`:466-512`.

```python
@pytest.mark.parametrize("argv", [["deploy"], ["destroy", "--yes"]])
def test_a_run_dir_that_cannot_be_created_is_refused_in_a_sentence(
    backend, config, tmp_path, capsys, argv
):
    """`UsageError:66-69` exists so a bad `--run-dir` is a sentence and not an
    errno. The is-a-file and not-empty branches got that; the mkdir did not, and
    its message named a relative path because `resolve()` ran after it."""
    parent = tmp_path / "unwritable"
    parent.mkdir(mode=0o555)
    wanted = parent / "run"

    assert cli.main([argv[0], config, "--run-dir", str(wanted), *argv[1:]]) == 1
    assert backend.sessions == [], "the refusal must land before a connection"
    err = capsys.readouterr().err
    assert "PermissionError" not in err
    assert "cannot create the run directory" in err
    assert str(wanted) in err, "the absolute path the operator can act on"
```

Assertions that fail today: `"PermissionError" not in err` and `"cannot create
the run directory" in err`. Today's `err` is
`error: PermissionError: [Errno 13] Permission denied: '<tmp>/unwritable/run'`.

The `parametrize` over `["deploy"]` / `["destroy", "--yes"]` is copied verbatim
from `tests/test_cli.py:465`, the only other place the suite covers both
`_run_dir` callers at once.

**No conditional skip.** Nothing here is gated; it is `tmp_path` and a mode bit.
`tests/test_gates.py` AST-walks the suite for bare `pytest.skip`,
`pytest.importorskip` and `pytest.mark.skip`, and this introduces none. A gate,
if one were ever needed, goes through `conftest.gate()` (`conftest.py:44`) or
`conftest.require()` (`:61`).

One caution for whoever writes it: the test must not run as root, where mode
`0555` does not deny the mkdir. The suite already assumes a non-root runner
(`tests/test_cli.py:776`'s mode assertions depend on it), so this needs no new
guard — but do not "fix" a root-CI failure by loosening the assertion.

## 8. Verification

1. `just check` — six lint gates, `ty`, and **413 passed, 25 skipped** (baseline
   at `aed962d` is 411/25, measured; the new test is parametrised over two verbs).
2. Teeth, **already measured on the prototype** — transcript
   `docs/review/cli-records/reverify/prototypes.txt`. In a scratch copy of
   `orchestrator/` + `tests/`, the §7 test as written fails on the unpatched tree
   for both parametrisations:

   ```
   FAILED …::test_a_run_dir_that_cannot_be_created_is_refused_in_a_sentence[argv0]
     - assert 'PermissionError' not in "error: Perm...table/run'\n"
   FAILED …::test_a_run_dir_that_cannot_be_created_is_refused_in_a_sentence[argv1]
   ```

   and passes with the §5 patch, `ruff check` / `ruff format --check` / `ty check`
   all clean, scratch suite 401 → 404 for exactly the three new ids (these two plus
   #80's one).
3. **The `resolve()` half needs its own check**, because the §7 test passes an
   absolute `--run-dir` and would still pass without it. From a cwd whose `runs/`
   is `0555`, the default layout must name the absolute path. Measured on the
   prototype:

   ```
   before: error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
   after:  vcows: cannot create the run directory
           /tmp/tmpzi1fayoc/runs/lab-a/20260831T061027Z: Permission denied. …
   ```

4. The three existing message tests must stay green unchanged —
   `test_a_non_empty_run_dir_is_refused_before_anything_connects` (`:466`),
   `test_a_run_dir_that_is_a_file_is_refused_in_a_sentence` (`:486`),
   `test_an_empty_run_dir_still_works` (`:503`). All three pass an absolute
   `tmp_path`, so `resolve()` is an identity for them; if any goes red, the
   symlink assumption in §5 is wrong and the fix needs O2 instead.
5. **End to end, against a rebuilt image** (`just image`), all four arms of §1
   repeated. Expected: the two `deploy`/`destroy` arms of each errno family now
   print `vcows: cannot create the run directory /runs/lab-a/<ts>: …`, and the
   two `preflight` arms are byte-identical to today.

   > **Not done.** The implementation lane was instructed not to rebuild the
   > image; the pinned `localhost/vcows-deploy:0.1.0.0` carries `672a500` and can
   > only show the before-state, which is §1's. Step 3's harness reproduces the
   > same pair without a container and is what the commit body claims.

## 9. Non-goals

* **`README.md:66`'s `--user` recipe** (RX-G2). This makes the failure legible;
  it does not make the recipe work, and vcows cannot chown a mount it does not
  own. Separate issue, documentation.
* **The `:U` remedy's unreadable output** (RX-G3). Separate.
* **#80's suppressed record.** Different line, different mechanism, and fixing
  this one does not fix it: under `--run-dir /runs` the mkdir succeeds.
* **The chmod guard at `:153-163`.** Its `except PermissionError` is narrower
  than it looks and #90 has an item on the comment above it — see
  `docs/archive/plans/issue-90-cli-comments.md`, and land #85 **first**: it edits
  `:121-123` and `:134-135`, which shift the line numbers `:148-152` sits on. The
  #90 item is a comment edit with no behaviour, so it costs nothing to rebase
  onto #85; the reverse ordering would make #85's diff read as touching a comment
  it does not change.
* **Any other `mkdir` in the codebase.** `seed.mkdir()` and `workdir.mkdir()` at
  `cli.py:376`/`:378` are inside a directory `_run_dir` has just proved writable.
