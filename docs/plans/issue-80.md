# Issue #80 — `--run-dir` under `--user` writes no `run.json`, and the suppression hides it

Lane L4. Reverified at `aed962d`. Raw output:
`docs/review-cli-records/reverify/RX-B7.txt`.

## 1. Reverification verdict

**REPRODUCED. Both arms, first attempt, no coaxing.** The issue is single-sourced
(`REVIEW.md:146-147` records that a verifier raised it while checking RX-G2 and
never re-ran it); it is now double-sourced.

Fixtures in a scratch `mktemp -d`: `qemu-img create -f qcow2 images/golden.qcow2
8G`, an `ssh-keygen -t ed25519` key at 0600, a one-line `known_hosts`, and
`README.md:109-135`'s config verbatim with the URI set to
`qemu+ssh://vcows@hypervisor.invalid/system`. `.invalid` cannot resolve, so every
run below dies in `getaddrinfo` and nothing reached
`qemu+ssh://vcows@vcows/system`. Image `localhost/vcows-deploy:0.1.0.0`
(`84dcf01a718d`), which `version` reports as revision `672a500`; `git diff
--name-only 672a500 aed962d -- orchestrator container licenses Containerfile
.containerignore` is empty, so it is a byte-valid substrate for `aed962d`. podman
5.8.2 rootless, host uid 1000, umask 0022, SELinux enforcing.

**Arm A — `--user 4242`, `--run-dir /runs`:**

```
vcows: cannot make /runs 0700; it stays 0755. This run's seed ISOs carry user_data
verbatim, and anyone who can read that directory can read them.
error: libvirtError: Cannot recv data: ssh: Could not resolve hostname hypervisor.invalid …
exit=1
$ podman unshare find runs -printf '%M %U:%G %p\n'
drwxr-xr-x 0:0 runs
```

Empty. No `run.json`, no `manifest.json`.

**Arm B — the identical failure with no `--user`:**

```
error: libvirtError: Cannot recv data: ssh: Could not resolve hostname hypervisor.invalid …
exit=1
$ podman unshare find runs -printf '%M %U:%G %p\n'
drwx------ 0:0 runs
-rw------- 0:0 runs/manifest.json
-rw------- 0:0 runs/run.json
```

The record exists, and its `error` field carries the same `libvirtError`.

**Mechanism, isolated in the same image:**

```
uid= 4242 mode= 0o755 owner_uid= 0
RAISED PermissionError [Errno 13] Permission denied: '/runs/run.json'
suppress block completed, no exception propagated
```

## 2. Anchor table

Every line re-read at `aed962d`. `orchestrator/` is byte-identical to `672a500`,
so the review's numbers are still exact.

| anchor | state |
|---|---|
| `orchestrator/cli.py:258` — `except BaseException as exc:` | ok |
| `orchestrator/cli.py:259-260` — "Suppressed, not handled" comment | ok |
| `orchestrator/cli.py:261-262` — `with contextlib.suppress(OSError): _record(...)` | ok |
| `orchestrator/cli.py:211` — `_record` | ok |
| `orchestrator/cli.py:220-227` — the **second** suppression, for the manifest copy | ok, and not named by the issue |
| `orchestrator/cli.py:228-245` — `_write_json(run.path / "run.json", …)` | ok |
| `orchestrator/cli.py:153-163` — the chmod guard that emits the `0755` line | ok |
| `orchestrator/cli.py:216-218` — "the run directory is what an air-gapped site ships back" | ok |
| `Containerfile:223` — `WORKDIR /` | ok |
| `README.md:91-94` — what `--run-dir` does | ok |

## 3. Corrections to the issue body

**C1 — the README citation is wrong.** The issue says "the exact case
`README.md:66-68` documents". `README.md:66-68` describes the **default** layout
on a foreign-UID mount ("a run directory on a foreign-UID mount also stays `0755`
and vcows tells you what that costs rather than failing"). Measured, that case
does not reach `_record` at all — it dies one step earlier, at `_run_dir`'s mkdir,
which is issue #85:

```
$ podman run --rm --user 4242 … -v ./runs:/runs:Z … deploy /config.yaml
error: PermissionError: [Errno 13] Permission denied: 'runs/lab-a'
exit=1
```

(`WORKDIR` is `/`, so the default `Path("runs")/deployment/timestamp` is
`/runs/lab-a/<ts>` and the mkdir lands inside the 0755 root-owned mount.) #80's
trigger is specifically `--run-dir` pointed at a directory vcows cannot write
into — the shape `README.md:91-94` describes. Replace the citation.

**C2 — `manifest.json` is missing too, and by a different suppression.** The
issue says "No `run.json` is produced". Arm A produced neither file. The manifest
copy is suppressed independently at `cli.py:220-227`, inside `_record`, with its
own recorded reason ("a failure copying provenance must not cost the record of
what happened"). A fix to `_guard` alone therefore reports the `run.json` loss
and stays silent about the manifest. That is the right split — see §5 O4 — but
the issue should not imply one suppression.

**C3 — the exception class.** The issue says `OSError`; the measured class is
`PermissionError`, which is a subclass, so `suppress(OSError)` catches it. The
claim is correct as written; recorded so the test asserts the class that actually
arrives.

**C4 — the silence is narrower than "the deployment record is never written".**
It is silent only when the body *also* failed. Every `_record` call reached from
the body — `"ok"` (`:430`), `"refused"` (`:359`), `"nothing-to-create"` (`:365`),
`"nothing-to-destroy"` (`:542`), `"cancelled"` (`:547`), the destroy record
(`:575`) — is outside the suppression, so an `OSError` there propagates into
`_guard`, fails the second write too, and re-raises to `main`'s catch-all.
Measured with a stubbed `_write_json`:

```
error: PermissionError: [Errno 13] Permission denied: '<run-dir>/run.json'
```

Loud, if unhelpfully worded. So the fix belongs in `_guard` and nowhere else.

## 4. The defect

`_run_dir` succeeds under `--run-dir /runs`: `/runs` already exists as a
directory, so `Path.mkdir(parents=True, exist_ok=True)` swallows the `EEXIST`
without ever attempting a write, and the `chmod` refusal at `:153-163` is caught
and downgraded to a warning **on purpose** — `cli.py:150-152` says a foreign-UID
bind mount "must not stop a run that is otherwise fine". The run therefore
proceeds with a run directory it cannot write into, and nothing has tested that
it can.

The connect then fails. `_guard` catches it at `:258` and calls `_record`, whose
`_write_json` raises `PermissionError`. `contextlib.suppress(OSError)` at `:261`
absorbs it and `raise` re-raises the original `libvirtError`. `main` prints the
`libvirtError` and exits 1 — a message about the hypervisor, on a run whose only
durable artifact does not exist.

The suppression is correct in intent and the issue is right that it must stay: a
full disk during the failure record must not replace the exception that says what
went wrong. What is missing is that suppressing is not the same as saying
nothing. The run directory is, by `cli.py:216-218`'s own words, "what an
air-gapped site ships back"; an absent one is indistinguishable from a run that
never started.

## 5. The fix

Keep the suppression's guarantee — never raise a second exception — and add one
line of stderr. `_guard:258-263` becomes:

```python
    except BaseException as exc:
        # Suppressed, not handled: a full disk here must not replace the
        # exception that says what actually went wrong. Reported, though: the
        # run directory is the whole account an air-gapped site ships back, and
        # its absence is otherwise indistinguishable from a run that never ran.
        try:
            _record(run, "failed", error=f"{type(exc).__name__}: {exc}")
        except OSError as unwritable:
            with contextlib.suppress(OSError):
                print(
                    f"vcows: this run left no record -- {run.path / 'run.json'} "
                    f"could not be written ({unwritable.strerror}). The failure "
                    f"below is reported on this stream only.",
                    file=sys.stderr,
                )
        raise
```

The nested `contextlib.suppress` is the original invariant restated: a closed
stderr must not become the exception the operator sees. Prototyped in a scratch
copy — `ruff check` and `ruff format --check` clean, `ty check` clean, suite
unchanged. Against the stubbed-`_write_json` harness:

```
vcows: this run left no record -- /tmp/…/handed-over/run.json could not be written
(Permission denied). The failure below is reported on this stream only.
error: RuntimeError: the connection dropped
rc = 1
```

The report precedes the failure, so the last line on the terminal is still the
reason the run failed.

**Rejected alternatives**

* **O1 — remove the suppression.** The issue forbids it and the comment at
  `:259-260` gives the reason: `_guard` must not raise a second exception while
  handling the first. A full disk would replace `libvirtError: … hypervisor` with
  `OSError: No space left on device`, which is the strictly worse message.
* **O2 — fall back to writing the record elsewhere** (`/tmp`, the cwd). New
  surface, and a second place a site has to be told to look. The run directory's
  path *is* the deliverable's identity; a record somewhere else is not the same
  artifact. Fails the brief's "unjustified surface area is a defect".
* **O3 — probe the run directory for writability in `_run_dir` and refuse.**
  This directly contradicts `cli.py:148-152`, which is a recorded decision that a
  foreign-UID mount must not stop a run, and it is pinned by
  `tests/test_cli.py:757-772`. It also does not fix the general case — a disk
  that fills mid-run passes any probe.
* **O4 — also report the suppressed manifest copy at `cli.py:220-227`.** Out of
  scope. The manifest is provenance; its absence is inferable from a record that
  exists, and the record's absence is the defect filed. Note it in the commit
  body, do not fix it here.
* **O5 — move the report into `_record`.** `_record` is also called from the
  body, where an `OSError` is already loud (§3 C4). Reporting there would double
  up on those paths and would sit in the wrong function: `_guard` owns the
  "record whatever it does" promise.

## 6. Surface cost

One function, `orchestrator/cli.py:258-263` → 14 lines. Net +11 in
`orchestrator/`, plus one test. No new function, no new module, no new key in
`run.json`, no new config field, no new dependency. `contextlib` and `sys` are
already imported.

## 7. The failing test

**File:** `tests/test_cli.py`, beside
`test_a_failed_apply_still_leaves_a_run_record` at `:250`, which is the test this
one is the missing half of.

```python
def test_a_run_record_that_could_not_be_written_says_so(
    backend, config, tmp_path, monkeypatch, capsys
):
    """The run directory is what an air-gapped site ships back. `_guard`
    suppresses a failure writing it so a full disk cannot replace the exception
    that says what went wrong -- but suppressing it is not the same as saying
    nothing, and an absent record is indistinguishable from a run that never
    started."""

    def dropped(*a, **k):
        raise RuntimeError("the connection dropped")

    def unwritable(path, payload):
        raise PermissionError(13, "Permission denied", str(path))

    given = tmp_path / "handed-over"
    given.mkdir()
    monkeypatch.setattr(backend, "preflight", dropped)
    monkeypatch.setattr(cli, "_write_json", unwritable)

    assert cli.main(["deploy", config, "--run-dir", str(given)]) == 1
    err = capsys.readouterr().err
    assert "left no record" in err and str(given / "run.json") in err
    assert "the connection dropped" in err, "the real failure is still the last word"
    assert not (given / "run.json").exists()
```

Assertion that fails today: `"left no record" in err`. Today's `err` contains
only `error: RuntimeError: the connection dropped`.

**No conditional skip.** Nothing here is gated — it is `monkeypatch` and the fake
backend, exactly like `:757`'s chmod test. `tests/test_gates.py` AST-walks the
suite and fails on a bare `pytest.skip`, `pytest.importorskip` or
`pytest.mark.skip`; this test introduces none. If a future variant does need a
gate it goes through `conftest.gate()` (`conftest.py:44`) or `conftest.require()`
(`:61`).

## 8. Verification

1. `just check` — six lint gates, `ty`, and **412 passed, 25 skipped** (baseline
   at `aed962d` is 411/25, measured in the worktree).
2. Teeth, **already measured on the prototype** — transcript
   `docs/review-cli-records/reverify/prototypes.txt`. In a scratch copy of
   `orchestrator/` + `tests/`, the §7 test as written fails on the unpatched tree:

   ```
   FAILED tests/test_proposed.py::test_a_run_record_that_could_not_be_written_says_so
     - AssertionError: assert ('left no record' in 'error: RuntimeError: the conne...
   ```

   and passes with the §5 patch applied, with `ruff check`, `ruff format --check`
   and `ty check` all clean and the scratch suite moving 401 → 404 for exactly the
   three new ids (this one plus #85's two). Nothing else moved.
3. **End to end, against a rebuilt image**, arm A from §1 repeated. Expected new
   first line:
   `vcows: this run left no record -- /runs/run.json could not be written (Permission denied). …`
   followed by the unchanged `error: libvirtError: … hypervisor.invalid`, exit 1,
   `podman unshare find runs` still empty. **This step needs `just image`**; the
   pinned `84dcf01a718d` carries `672a500` and cannot show the fix. Do not skip
   it — the whole finding is about behaviour that only appears under rootless
   podman with a foreign-UID mount.

   > **Not done, and the commit body says so.** The implementation lane was
   > instructed not to rebuild the image. Arm A's before-state above is that
   > image's; the after-state is the stubbed-`_write_json` harness and the §7
   > test, both run here. This step is still owed against the next image build.
4. Arm B repeated: unchanged, `run.json` still written, no new stderr line.

## 9. Non-goals

* **`README.md:66`.** RX-G2 is a separate finding with its own issue; nothing
  here edits the README.
* **#85's unguarded mkdir** (`cli.py:134`). Adjacent and separately filed. The
  two do not overlap: #85 changes `:134-135`, #80 changes `:258-263`. Fixing #85
  does not fix #80 — under `--run-dir /runs` the mkdir succeeds, so #85's new
  guard never fires. Either may land first.
* **The `:U` remedy's unreadable output** (RX-G3). Documentation, filed
  elsewhere.
* **The manifest-copy suppression** at `cli.py:220-227`. §5 O4.
* **`_record` raising from the body over a successful deploy.** Measured while
  reverifying (§3 C4) and real, but it is loud, it is a different function, and
  it is not what #80 filed. File separately if it is worth filing.
* **Making the run directory writable.** `cli.py:148-152` says it must not, and
  a test pins that.
