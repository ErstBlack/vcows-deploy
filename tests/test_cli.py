"""The five commands, end to end, with `import libvirt` broken.

This is `tests/test_seam.py`'s cycle run the way an operator runs it -- through
`main()`, writing a real run directory. The fake backend has no hypervisor
semantics, so anything that passes here passes because the pipeline works and not
because libvirt happened to be installed.

`deploy` really does call `prepare` and then `create` on the backend the registry
holds, against a session `connect` opened for it. What that proves is the part
core owns: that only the VMs being created reach either call, that what `create`
reports back is reconciled against what was asked for, and that the run directory
records both.
"""

from __future__ import annotations

import json
import re
import stat
import textwrap
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from orchestrator import VERSION, cli
from orchestrator.backends.base import Existing
from orchestrator.marker import Marker
from tests.fake_backend import FakeBackend
from tests.test_seam import no_libvirt  # noqa: F401 -- used as a fixture

CONFIG = """\
schema_version: 1
deployment: lab-a
backend: fake
target:
  fake:
    endpoint: good://example
image:
  source_qcow2: /images/golden.qcow2
  base_volume_name: golden.qcow2
vms:
  - name: app01
  - name: app02
"""


@pytest.fixture
def backend(monkeypatch):
    """A fake backend in the real registry, removed again afterwards."""
    fake = FakeBackend()
    monkeypatch.setitem(cli.REGISTRY, "fake", fake)
    return fake


@pytest.fixture
def config(tmp_path, monkeypatch):
    """The config, and a cwd for the default `runs/` directory to land in."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "lab-a.yaml"
    path.write_text(textwrap.dedent(CONFIG))
    return str(path)


def latest_run(tmp_path):
    (deployment,) = (tmp_path / "runs").iterdir()
    (run,) = deployment.iterdir()
    return run


def ours(name: str, deployment: str = "lab-a") -> Existing:
    marker = Marker.for_vm(name, deployment)
    return Existing(name=name, id=marker.id, marker=marker)


#: A complete build manifest, in the shape `_print_manifest` reads it. Every
#: field is looked up by name, and a name read wrong is a `KeyError` the
#: function reports as a manifest that will not parse.
BUILD: dict[str, Any] = {
    "git_sha": "da3f45c",
    "built": "2026-01-02T03:04:05Z",
    "base_image": {"name": "registry.example/base", "digest": "sha256:9f1cbeef"},
    "packages": ["one", "two", "three"],
    "source_rpms": ["one.src.rpm"],
}


# -- version and validate ---------------------------------------------------


def test_version_prints_the_single_definition(capsys):
    assert cli.main(["version"]) == 0
    assert VERSION in capsys.readouterr().err


def test_version_says_which_build_the_image_is(tmp_path, monkeypatch, capsys):
    """R5's whole purpose, and the reason `_print_manifest` runs before anything
    that can return early: "which build is this" answered from the image itself,
    for an air-gapped site that cannot rebuild it to find out."""
    baked = tmp_path / "manifest.json"
    baked.write_text(json.dumps(BUILD))
    monkeypatch.setattr(cli, "MANIFEST", baked)

    assert cli.main(["version"]) == 0
    err = capsys.readouterr().err
    # A field read by the wrong name lands in the same `except` as a broken file.
    assert "will not parse" not in err
    for value in (
        BUILD["git_sha"],
        BUILD["built"],
        BUILD["base_image"]["name"],
        BUILD["base_image"]["digest"],
    ):
        assert value in err, value


def test_validate_is_offline(no_libvirt, backend, config, capsys):  # noqa: F811
    assert cli.main(["validate", config]) == 0
    assert "valid" in capsys.readouterr().err
    assert backend.sessions == [], "validate must not open a connection"


def test_validate_reports_every_problem_at_once(tmp_path, backend, capsys):
    """`config.load` collects them all so an operator at a site does not
    round-trip once per typo. The CLI must not print only the first."""
    path = tmp_path / "broken.yaml"
    path.write_text(
        CONFIG.replace("schema_version: 1", "schema_version: 2").replace(
            "endpoint: good://example", "endpoint: 7"
        )
    )
    assert cli.main(["validate", str(path)]) == 1
    err = capsys.readouterr().err
    assert "schema_version" in err
    assert "endpoint" in err


def test_a_config_warning_reaches_more_than_validate(
    backend, tmp_path, monkeypatch, capsys
):
    """`load` computes them on the way into every verb. Dropping them everywhere
    but `validate` meant an operator only saw them if they asked twice."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "lab-a.yaml"
    path.write_text(CONFIG.replace("good://example", "odd://example"))

    assert cli.main(["validate", str(path)]) == 0
    assert "endpoint scheme is unusual" in capsys.readouterr().err
    assert cli.main(["preflight", str(path)]) == 0
    assert "endpoint scheme is unusual" in capsys.readouterr().err


def test_a_destroy_scopes_the_advisory_problems(backend, tmp_path, monkeypatch, capsys):
    """Preflight computes its refusals for a deploy and both verbs print them.
    D30's names a golden image shared across deployments; unlabelled, it reads
    as an instruction to an operator already in a destructive frame of mind."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "lab-a.yaml"
    path.write_text(CONFIG.replace("good://example", "odd://example"))

    assert cli.main(["destroy", str(path), "--yes"]) == 0
    err = capsys.readouterr().err
    assert "endpoint scheme is unusual" in err
    assert "none of them changes this teardown" in err
    # And into the record, which is the copy that outlives the terminal.
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert any("endpoint scheme is unusual" in p for p in record["problems"])


# -- preflight --------------------------------------------------------------


def test_preflight_reports_and_opens_a_session(no_libvirt, backend, config, capsys):  # noqa: F811
    assert cli.main(["preflight", config]) == 0
    out = capsys.readouterr().err
    assert "app01" in out and "create" in out
    assert backend.sessions[0].closed, "the session must close on the way out"


def test_preflight_refuses_an_unmarked_collision(backend, config, capsys):
    backend.world = [Existing(name="app01", id="0", marker=None)]
    assert cli.main(["preflight", config]) == 1
    assert "will not adopt or overwrite" in capsys.readouterr().err


def test_the_backend_is_handed_the_config_on_every_call(backend, config, monkeypatch):
    """The config is the first argument of `connect`, `preflight` and `destroy`
    -- it carries the endpoint, the credentials and the deployment name, and a
    backend that is handed nothing has nothing to connect to. The fake ignores
    it, so only a caller watching the seam can tell that it arrived."""
    seen: list[tuple[str, Any]] = []

    def watch(name):
        real = getattr(backend, name)

        def wrapper(cfg, *rest):
            seen.append((name, cfg))
            return real(cfg, *rest)

        monkeypatch.setattr(backend, name, wrapper)

    for name in ("connect", "preflight", "destroy"):
        watch(name)

    backend.world = [ours("app01")]
    assert cli.main(["preflight", config]) == 0
    assert cli.main(["destroy", config, "--yes"]) == 0

    assert [name for name, _ in seen] == [
        "connect",
        "preflight",
        "connect",
        "preflight",
        "destroy",
    ]
    assert all(cfg and cfg["deployment"] == "lab-a" for _, cfg in seen)


# -- deploy -----------------------------------------------------------------


def test_deploy_runs_the_whole_pipeline(no_libvirt, backend, config, tmp_path):  # noqa: F811
    assert cli.main(["deploy", config]) == 0
    run = latest_run(tmp_path)

    # What prepare built, kept so a VM that will not boot can be debugged from
    # the media it was actually given rather than from a rebuild.
    assert (run / "seed" / "fake-artifact").is_file()

    # `create` was handed a session of its own, after `prepare` ran without one.
    assert [s.closed for s in backend.sessions] == [True, True]
    # ...and the artifacts dict that `prepare` returned, not one core built. Passing
    # a fresh `{}` here loses the media the VMs are supposed to boot.
    assert backend.sessions[1].seed == str(run / "seed" / "fake-artifact")

    inventory = json.loads((run / "inventory.json").read_text())
    assert set(inventory["vms"]) == {"app01", "app02"}
    assert inventory["vms"]["app01"]["name"] == "app01"

    record = json.loads((run / "run.json").read_text())
    assert record["outcome"] == "ok"
    assert record["vcows"] == VERSION
    assert record["created"] == ["app01", "app02"]


def test_the_run_directory_is_not_world_readable(backend, config, tmp_path):
    """It holds the seed ISOs, and those hold `user_data` verbatim (F12)."""
    assert cli.main(["deploy", config]) == 0
    mode = stat.S_IMODE(latest_run(tmp_path).stat().st_mode)
    assert mode == 0o700


def test_a_second_deploy_creates_nothing_and_calls_no_backend(
    backend, config, tmp_path, monkeypatch, capsys
):
    """Every VM already exists and is ours. D23 drops them from the set being
    created, which leaves nothing to do -- so neither `prepare` nor `create`
    runs, and no seed ISO is written for a VM nobody asked for."""
    backend.world = [ours("app01"), ours("app02")]
    monkeypatch.setattr(
        backend, "create", lambda *a, **k: pytest.fail("create must not run")
    )
    assert cli.main(["deploy", config]) == 0
    assert "nothing to create" in capsys.readouterr().err
    assert not (latest_run(tmp_path) / "seed").exists()


def test_a_refusal_stops_the_deploy_before_anything_is_built(
    backend, config, tmp_path, monkeypatch, capsys
):
    backend.world = [Existing(name="app01", id="0", marker=None)]
    monkeypatch.setattr(
        backend, "create", lambda *a, **k: pytest.fail("create must not run")
    )
    assert cli.main(["deploy", config]) == 1

    run = latest_run(tmp_path)
    assert not (run / "seed").exists()
    assert json.loads((run / "run.json").read_text())["outcome"] == "refused"
    assert "nothing was changed" in capsys.readouterr().err


def test_a_refused_deploys_reason_reaches_the_run_record(
    backend, config, tmp_path, monkeypatch
):
    """`decisions` records what would have been done; without `problems` the
    *reason* it was not exists only on a terminal somebody has since closed."""
    from orchestrator.backends.base import Discovered
    from orchestrator.problems import Problem, Severity

    monkeypatch.setattr(
        backend,
        "preflight",
        lambda cfg, session: Discovered(
            vms=(),
            artifacts={"existing_names": []},
            problems=(Problem(Severity.ERROR, "storage pool 'images' does not exist"),),
        ),
    )
    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "refused"
    assert any("storage pool 'images'" in p for p in record["problems"])


def test_a_failed_create_still_leaves_a_run_record(
    backend, config, tmp_path, monkeypatch, capsys
):
    """The run directory is what a site ships back for support, and today it is
    present for every run where nothing happened and absent for every run where
    something did. `create` rolls nothing back, so the record of what it was
    part-way through is the only account of the leftovers."""

    class CreateError(Exception):
        """A backend's own error. findings.md §3 rules out a shared hierarchy,
        so core catches nothing narrower than `BaseException`."""

    def boom(*a, **k):
        raise CreateError("app02: could not define the domain")

    monkeypatch.setattr(backend, "create", boom)
    assert cli.main(["deploy", config]) == 1

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "could not define the domain" in record["error"]
    assert record["decisions"], "what it was about to do survives the failure"
    assert "CreateError" in capsys.readouterr().err


def test_a_run_record_that_could_not_be_written_says_so(
    backend, config, tmp_path, monkeypatch, capsys
):
    """The missing half of the test above. `_guard` suppresses a failure writing
    the record so a full disk cannot replace the exception that says what went
    wrong -- but suppressing it is not the same as saying nothing, and an absent
    run directory is indistinguishable from a run that never started."""

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


def test_an_interrupted_destroy_still_leaves_a_run_record(
    backend, config, tmp_path, monkeypatch
):
    """Ctrl-C mid-teardown is the case with the most to say and the least chance
    of being said. `except BaseException`, not `except Exception`."""

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    backend.world = [ours("app01")]
    monkeypatch.setattr(backend, "destroy", interrupt)
    assert cli.main(["destroy", config, "--yes"]) == 1

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "KeyboardInterrupt" in record["error"]


def test_a_backend_that_created_fewer_vms_than_asked_fails_the_deploy(
    backend, config, tmp_path, monkeypatch
):
    """A backend that reports a subset yields `created 0 VM(s)` under `outcome:
    ok`: a run whose two artifacts contradict each other, with the record siding
    against the truth. This is the reporting shape acceptance defect 5 passed
    through."""
    monkeypatch.setattr(backend, "create", lambda *a: {"app01": {"name": "app01"}})

    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "1 VM(s) for the 2" in record["error"]
    assert not (latest_run(tmp_path) / "inventory.json").exists()


def test_a_target_problem_stops_the_deploy(backend, config, monkeypatch):
    """`Discovered.problems` is how a backend reports what is wrong with the
    *target* -- a missing pool, an orphaned volume. Deploy treats them as fatal."""
    from orchestrator.backends.base import Discovered
    from orchestrator.problems import Problem, Severity

    monkeypatch.setattr(
        backend,
        "preflight",
        lambda cfg, session: Discovered(
            vms=(),
            artifacts={"existing_names": []},
            problems=(Problem(Severity.ERROR, "storage pool 'images' does not exist"),),
        ),
    )
    monkeypatch.setattr(
        backend, "create", lambda *a, **k: pytest.fail("create must not run")
    )
    assert cli.main(["deploy", config]) == 1


def test_only_the_vms_that_do_not_exist_yet_reach_the_backend(
    backend, config, tmp_path, monkeypatch
):
    """D23: `create` only ever creates, so a VM that already exists is dropped
    here rather than skipped further down. `prepare` is handed the same narrowed
    config -- a seed ISO for a VM that is not being created is `user_data`
    written for nothing."""
    backend.world = [ours("app01")]
    # Both halves of the apply, recording the config each was handed.
    narrowed: list[Any] = []
    for name in ("prepare", "create"):
        real = getattr(backend, name)
        monkeypatch.setattr(
            backend,
            name,
            lambda cfg, *rest, _f=real: narrowed.append(cfg) or _f(cfg, *rest),
        )

    assert cli.main(["deploy", config]) == 0
    assert [[vm["name"] for vm in cfg["vms"]] for cfg in narrowed] == [
        ["app02"],
        ["app02"],
    ]
    inventory = json.loads((latest_run(tmp_path) / "inventory.json").read_text())
    assert list(inventory["vms"]) == ["app02"]


# -- the run directory ------------------------------------------------------


@pytest.mark.parametrize("argv", [["deploy"], ["destroy", "--yes"]])
def test_a_non_empty_run_dir_is_refused_before_anything_connects(
    backend, config, tmp_path, capsys, argv
):
    """D40 gives every run a fresh state, and reusing a directory breaks it two
    different ways: deploy dies on `seed/` with a bare FileExistsError, destroy
    creates no subdirectories at all and silently overwrites the earlier run's
    `run.json` beside its still-current `inventory.json`. Refuse both, and refuse
    before the connected preflight has spent a session and printed clean.
    """
    used = tmp_path / "used"
    used.mkdir()
    (used / "run.json").write_text("{}\n")

    assert cli.main([argv[0], config, "--run-dir", str(used), *argv[1:]]) == 1
    assert backend.sessions == [], "the refusal must land before a connection"
    err = capsys.readouterr().err
    assert "--run-dir" in err and str(used) in err
    assert (used / "run.json").read_text() == "{}\n", "nothing was overwritten"


def test_a_run_dir_that_is_a_file_is_refused_in_a_sentence(
    backend, config, tmp_path, capsys
):
    """`exist_ok=True` suppresses `FileExistsError` for a directory and nothing
    else, so this used to reach `main`'s catch-all as `error: FileExistsError:
    [Errno 17] File exists` -- the output `UsageError` was added to replace."""
    notadir = tmp_path / "notadir"
    notadir.write_text("this is a file\n")

    assert cli.main(["destroy", config, "--yes", "--run-dir", str(notadir)]) == 1
    assert backend.sessions == [], "the refusal must land before a connection"
    err = capsys.readouterr().err
    assert "FileExistsError" not in err
    assert "is a file, not a directory" in err and str(notadir) in err
    assert notadir.read_text() == "this is a file\n"


def test_an_empty_run_dir_still_works(backend, config, tmp_path):
    """The bind-mounted mountpoint. `podman run -v ./runs/lab-a:/run-dir` presents
    an empty directory that already exists, and that has to keep working."""
    mount = tmp_path / "mount"
    mount.mkdir()
    backend.world = [ours("app01")]

    assert cli.main(["destroy", config, "--yes", "--run-dir", str(mount)]) == 0
    assert (mount / "run.json").is_file()


@pytest.mark.parametrize("argv", [["deploy"], ["destroy", "--yes"]])
def test_a_run_dir_that_cannot_be_created_is_refused_in_a_sentence(
    backend, config, tmp_path, capsys, argv
):
    """`UsageError` exists so a bad `--run-dir` is a sentence and not an
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


def test_a_run_dir_that_is_already_private_is_not_chmodded(
    backend, config, tmp_path, monkeypatch
):
    """The chmod is the half that can fail: a bind mount owned by another UID
    refuses it with EACCES, and a run that is otherwise fine must not stop for
    a mode it already has."""
    backend.world = [ours("app01"), ours("app02")]
    given = tmp_path / "already-private"
    given.mkdir(mode=0o700)
    monkeypatch.setattr(
        cli.os, "chmod", lambda *a, **k: pytest.fail("nothing needed tightening")
    )

    assert cli.main(["deploy", config, "--run-dir", str(given)]) == 0


def test_the_run_timestamp_is_utc(monkeypatch):
    """It names the run directory and both ends of `run.json`, and every log line
    beside it is UTC by `LOG_DATEFMT`. A site in another timezone would read a
    record whose stamps disagree with the directory holding it."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        before = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        stamped = cli._timestamp()
        after = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    finally:
        monkeypatch.undo()
        time.tzset()

    assert stamped in (before, after)


# -- destroy ----------------------------------------------------------------


def test_destroy_takes_only_this_deployment(backend, config, tmp_path, capsys):
    """D36. The marker has carried `deployment` since 0.1.0.0, so the scope is a
    filter on data that is already there -- and destroying somebody else's VMs
    because they share a hypervisor is the data-loss event findings.md §2 names.
    """
    backend.world = [ours("app01"), ours("elsewhere", "lab-b"), ours("stray", "")]

    assert cli.main(["destroy", config, "--yes"]) == 0

    session = backend.sessions[-1]
    assert session.destroyed == ["app01"]
    out = capsys.readouterr().err
    assert "belongs to deployment 'lab-b'" in out
    # The detail column is empty when the marker's name is the domain's name,
    # which is every ordinary case: repeating it read `app01  destroy  app01`.
    (row,) = [ln for ln in out.splitlines() if ln.endswith("destroy")]
    assert row.count("app01") == 1, row
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["destroyed"] == ["app01"]
    assert record["outcome"] == "ok"
    assert record["command"] == "destroy"
    assert record["started"] and record["started"] <= record["finished"]
    # The stdout row above outlives the terminal only if something records it.
    # The name alone would not: whose deployment it belongs to is the half of
    # the row that explains why this teardown left it alone. A marker carrying
    # no deployment at all is what `<unset>` is there to say.
    assert record["left_alone"] == {"elsewhere": "lab-b", "stray": "<unset>"}


def test_a_destroy_that_could_not_finish_says_what_it_left(
    backend, config, tmp_path, capsys
):
    """2.3, end to end. An inactive pool makes every disk in it resolve as
    "already gone": the domain is destroyed and undefined, its marker with it,
    both volumes stay on disk, and the operator was told it worked."""
    from orchestrator.backends.base import Outcome
    from orchestrator.problems import Problem, Severity

    backend.world = [ours("app01")]
    backend.outcome = Outcome(
        destroyed=["app01"],
        skipped=["/pool/app01.qcow2", "/pool/app01-seed.iso"],
        problems=[
            Problem(Severity.WARNING, "could not refresh pool 'images'", "storage")
        ],
    )

    assert cli.main(["destroy", config, "--yes"]) == 1
    captured = capsys.readouterr()
    # Rewritten rather than retargeted when every line became a log line: this
    # used to pin the stdout/stderr split, and the split is now the *level*.
    # Retargeting both halves to `.err` would have left it passing while
    # asserting nothing about which is which.
    assert captured.out == "", "stdout carries nothing but the interactive prompt"
    skipped = [ln for ln in captured.err.splitlines() if "/pool/app01" in ln]
    assert len(skipped) == 2, captured.err
    assert all(" INFO " in ln for ln in skipped), skipped
    (refresh,) = [
        ln for ln in captured.err.splitlines() if "could not refresh pool" in ln
    ]
    assert " WARNING " in refresh, refresh

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "partial"
    assert record["destroyed"] == ["app01"]
    assert record["skipped"] == ["/pool/app01-seed.iso", "/pool/app01.qcow2"]
    assert any("could not refresh pool" in p for p in record["problems"])


def test_a_returned_fatal_problem_is_not_recorded_as_ok(
    backend, config, tmp_path, capsys
):
    """RW-B2. `Backend.destroy`'s docstring says a backend is free to return
    rather than raise, and `Outcome`'s says a returned outcome "without its
    consumer reading it reproduces that defect exactly". Core read `skipped` and
    never `failed`, so a backend that returned a fatal problem with an empty
    `skipped` got `outcome: "ok"` and exit 0 -- the silent partial success
    findings.md §1 names, arriving through the seam meant to prevent it.
    Unreachable through the libvirt backend, which raises."""
    from orchestrator.backends.base import Outcome
    from orchestrator.problems import Problem, Severity

    backend.world = [ours("app01")]
    backend.outcome = Outcome(
        destroyed=["app01"],
        problems=[
            Problem(Severity.ERROR, "pool 'images' went away mid-run", "storage")
        ],
    )

    assert cli.main(["destroy", config, "--yes"]) == 1
    assert "pool 'images' went away" in capsys.readouterr().err

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert any("went away mid-run" in p for p in record["problems"])


def test_a_backend_that_created_the_right_count_under_wrong_names_fails(
    backend, config, tmp_path, monkeypatch
):
    """RW-B1. The reconciliation compared counts, so two VMs asked for and two
    reported passed even when one was a different VM -- `run.json` and
    `inventory.json` in the same directory disagreeing about what exists, with
    `outcome: ok` over both. The message already computed the set difference and
    carried an `or 'names differ'` fallback, so a set comparison was always the
    intent."""
    monkeypatch.setattr(
        backend,
        "create",
        lambda *a: {"app01": {"name": "app01"}, "ghost": {"name": "ghost"}},
    )

    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    # The name asked for and not reported back. `or 'names differ'` covers the
    # difference running the other way, and an assertion accepting either was
    # satisfied by that fallback rather than by the name.
    assert "app02" in record["error"]
    assert not (latest_run(tmp_path) / "inventory.json").exists()


def test_a_destroy_that_raises_still_records_what_it_removed(
    backend, config, tmp_path, monkeypatch
):
    """The teardown with a fatal problem is the one with the most to record and
    the one that reaches `_guard` as an exception rather than a return value, so
    the structured record below it never ran: `run.json` carried `outcome:
    failed` and a message, with no `destroyed` and no `skipped`. That is the run
    an air-gapped site ships back after a teardown it now has to finish by hand.
    """
    from orchestrator.backends.base import Outcome
    from orchestrator.problems import Problem, Severity

    class Failed(Exception):
        """A backend's own error carrying the whole outcome, the way the libvirt
        backend's `DestroyError` does. The attribute is all core reads."""

        def __init__(self, outcome: Outcome):
            self.outcome = outcome
            super().__init__("could not delete volume")

    def raises(*a, **k):
        raise Failed(
            Outcome(
                destroyed=["app01"],
                skipped=["/pool/app01-seed.iso"],
                problems=[
                    Problem(Severity.ERROR, "could not delete volume", "storage")
                ],
            )
        )

    backend.world = [ours("app01")]
    monkeypatch.setattr(backend, "destroy", raises)

    assert cli.main(["destroy", config, "--yes"]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "could not delete volume" in record["error"]
    assert record["destroyed"] == ["app01"]
    assert record["skipped"] == ["/pool/app01-seed.iso"]
    assert any("could not delete volume" in p for p in record["problems"])


def test_an_interrupted_destroy_records_what_it_removed(
    backend, config, tmp_path, monkeypatch
):
    """The same argument one test up, for the exception that is not an `Exception`.
    A Ctrl-C is the teardown least likely to have finished and the one whose record
    an operator most needs, and `except Exception` here dropped it on the floor."""
    from orchestrator.backends.base import Outcome

    def interrupt(*a, **k):
        # Attached the way `destroy` attaches it: onto an exception whose type
        # has no such attribute, which is why both sides go through `Any`.
        exc: Any = KeyboardInterrupt()
        exc.outcome = Outcome(destroyed=["app01"], skipped=["/pool/app02-seed.iso"])
        raise exc

    backend.world = [ours("app01")]
    monkeypatch.setattr(backend, "destroy", interrupt)

    assert cli.main(["destroy", config, "--yes"]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert record["destroyed"] == ["app01"]
    assert record["skipped"] == ["/pool/app02-seed.iso"]


def test_a_destroy_that_raises_without_an_outcome_still_records_the_failure(
    backend, config, tmp_path, monkeypatch
):
    """`getattr`, not the libvirt backend's own error type. A backend whose
    exception carries nothing records nothing extra rather than breaking."""

    def boom(*a, **k):
        raise RuntimeError("the connection dropped")

    backend.world = [ours("app01")]
    monkeypatch.setattr(backend, "destroy", boom)

    assert cli.main(["destroy", config, "--yes"]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "the connection dropped" in record["error"]
    assert "destroyed" not in record


def test_destroy_needs_an_answer_when_there_is_nobody_to_ask(
    backend, config, tmp_path, capsys
):
    """stdin is not a terminal under pytest, which is the same situation as a
    script. A destructive verb should have to be told, not assume."""
    backend.world = [ours("app01")]
    assert cli.main(["destroy", config]) == 1
    assert backend.sessions[-1].destroyed == []
    assert "pass --yes" in capsys.readouterr().err
    # The record is written either way: an empty run directory reads as a run
    # that crashed, and this one stopped because nobody answered.
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "cancelled"


def test_destroy_with_nothing_of_ours_is_not_an_error(
    backend, config, tmp_path, capsys
):
    backend.world = [Existing(name="somebody-elses", id="0", marker=None)]
    assert cli.main(["destroy", config, "--yes"]) == 0
    assert "no VMs marked for deployment 'lab-a'" in capsys.readouterr().err
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "nothing-to-destroy"


@pytest.mark.parametrize(
    "typed, code, destroyed",
    [(" yes\n", 0, ["app01"]), ("y", 1, []), ("YES", 1, [])],
)
def test_only_a_typed_yes_destroys(
    backend, config, monkeypatch, typed, code, destroyed
):
    """The answer is compared whole and after stripping: `y` is not consent, and
    an operator who typed it meant to be asked again rather than to lose a VM."""
    asked: list[str] = []
    backend.world = [ours("app01")]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: asked.append(prompt) or typed)

    assert cli.main(["destroy", config]) == code
    assert backend.sessions[-1].destroyed == destroyed
    # What the operator is answering: how many, and whose. A prompt missing
    # either is one they cannot check before typing yes.
    assert "1" in asked[0] and "lab-a" in asked[0], asked


def test_a_teardown_that_raises_keeps_the_problems_it_already_had(
    backend, tmp_path, monkeypatch
):
    """`run.extra["problems"]` already holds the advisory ones by the time the
    backend raises. Assigning over it rather than appending drops them, and the
    record then says the only thing wrong with the run was the failure."""
    from orchestrator.backends.base import Outcome
    from orchestrator.problems import Problem, Severity

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "lab-a.yaml"
    path.write_text(CONFIG.replace("good://example", "odd://example"))

    def raises(*a, **k):
        exc: Any = RuntimeError("could not delete volume")
        exc.outcome = Outcome(
            destroyed=["app01"],
            skipped=["/pool/app01.qcow2"],
            problems=[Problem(Severity.ERROR, "the pool went away", "storage")],
        )
        raise exc

    backend.world = [ours("app01")]
    monkeypatch.setattr(backend, "destroy", raises)

    assert cli.main(["destroy", str(path), "--yes"]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert any("endpoint scheme is unusual" in p for p in record["problems"])
    assert any("the pool went away" in p for p in record["problems"])


def test_a_teardown_that_returns_keeps_the_problems_it_already_had(
    backend, tmp_path, monkeypatch
):
    """The return path's twin of the test above. `_destroy` appends the
    outcome's problems to the advisory ones already in `run.extra`; an
    assignment there would drop the advisory half and pass every other
    assertion in this file, because none of them carries both at once."""
    from orchestrator.backends.base import Outcome
    from orchestrator.problems import Problem, Severity

    monkeypatch.chdir(tmp_path)
    path = tmp_path / "lab-a.yaml"
    path.write_text(CONFIG.replace("good://example", "odd://example"))

    backend.world = [ours("app01")]
    backend.outcome = Outcome(
        destroyed=["app01"],
        problems=[Problem(Severity.WARNING, "could not refresh pool", "storage")],
    )

    assert cli.main(["destroy", str(path), "--yes"]) == 0
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert any("endpoint scheme is unusual" in p for p in record["problems"])
    assert any("could not refresh pool" in p for p in record["problems"])


# -- the build manifest, and the modes ---------------------------------------


def test_the_build_manifest_travels_with_the_run(
    backend, config, tmp_path, monkeypatch
):
    """The run directory is what an air-gapped site ships back, and "which build
    did this" is unanswerable from it unless R5's record goes along."""
    backend.world = [ours("app01"), ours("app02")]
    baked = tmp_path / "baked-manifest.json"
    baked.write_text('{"git_sha": "da3f45c"}')
    monkeypatch.setattr(cli, "MANIFEST", baked)

    assert cli.main(["deploy", config]) == 0
    copied = json.loads((latest_run(tmp_path) / "manifest.json").read_text())
    assert copied["git_sha"] == "da3f45c"


def test_a_manifest_that_cannot_be_copied_does_not_cost_the_record(
    backend, config, tmp_path, monkeypatch
):
    """Provenance is the half that can be lost; what happened is the half that
    cannot. A read-only mount or a full disk under the copy must not take
    `run.json` with it."""
    backend.world = [ours("app01"), ours("app02")]
    baked = tmp_path / "baked-manifest.json"
    baked.write_text('{"git_sha": "da3f45c"}')
    monkeypatch.setattr(cli, "MANIFEST", baked)

    def denied(src, dst):
        raise PermissionError(13, "Permission denied", str(dst))

    monkeypatch.setattr(cli.shutil, "copyfile", denied)

    assert cli.main(["deploy", config]) == 0
    run = latest_run(tmp_path)
    assert not (run / "manifest.json").exists()
    assert json.loads((run / "run.json").read_text())["outcome"] == "nothing-to-create"


def test_a_checkout_has_no_manifest_and_that_is_not_an_error(
    backend, config, tmp_path, monkeypatch
):
    """A checkout is not a release. Inventing a manifest for one would make the
    two indistinguishable, which is the thing the manifest exists to settle."""
    backend.world = [ours("app01"), ours("app02")]
    monkeypatch.setattr(cli, "MANIFEST", tmp_path / "there-is-none.json")

    assert cli.main(["deploy", config]) == 0
    assert not (latest_run(tmp_path) / "manifest.json").exists()


def test_version_says_when_the_manifest_exists_and_will_not_parse(
    tmp_path, monkeypatch, capsys
):
    """Absent and unreadable used to be one return value, so an unreadable R5
    record on a delivered image looked exactly like a dev box."""
    bad = tmp_path / "manifest.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(cli, "MANIFEST", bad)

    assert cli.main(["version"]) == 0
    assert "will not parse" in capsys.readouterr().err


def test_a_run_dir_handed_to_us_is_made_private(backend, config, tmp_path):
    """The umask covers what vcows creates; this is the other case -- a directory
    that already existed, at whatever mode the operator left it."""
    backend.world = [ours("app01"), ours("app02")]
    given = tmp_path / "handed-over"
    given.mkdir(mode=0o755)

    assert cli.main(["deploy", config, "--run-dir", str(given)]) == 0
    assert stat.S_IMODE(given.stat().st_mode) == 0o700


def test_a_run_dir_that_cannot_be_made_private_says_which_mode_it_wanted(
    backend, config, tmp_path, monkeypatch, capsys
):
    """Refusing would break the foreign-UID bind mount README:48-53 documents.
    Saying nothing would leave `user_data` readable with nobody told."""
    backend.world = [ours("app01"), ours("app02")]
    given = tmp_path / "not-ours"
    given.mkdir(mode=0o755)

    def denied(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(cli.os, "chmod", denied)
    assert cli.main(["deploy", config, "--run-dir", str(given)]) == 0
    err = capsys.readouterr().err
    assert "0700" in err and "user_data" in err


def test_nothing_in_the_run_directory_is_readable_by_anyone_else(
    backend, config, tmp_path
):
    """The directory has been 0700 since Stage 4; its contents were not. The seed
    ISOs are written by pycdlib rather than by vcows, so no per-file chmod can
    reach them and the umask is the only lever."""
    assert cli.main(["deploy", config]) == 0
    loose = sorted(
        str(p.relative_to(latest_run(tmp_path)))
        for p in latest_run(tmp_path).rglob("*")
        if p.stat().st_mode & 0o077
    )
    assert loose == []


def test_a_backend_exception_becomes_a_message_and_an_exit_code(
    backend, config, monkeypatch, capsys
):
    """findings.md §3 rules out a shared exception hierarchy, so there is nothing
    narrower for core to catch -- and `str()` on the libvirt backend's DestroyError
    already carries every per-object failure."""

    class DestroyError(Exception):
        pass

    backend.world = [ours("app01")]
    monkeypatch.setattr(
        backend,
        "destroy",
        lambda *a: (_ for _ in ()).throw(DestroyError("app01: could not stop")),
    )
    assert cli.main(["destroy", config, "--yes"]) == 1
    assert "DestroyError: app01: could not stop" in capsys.readouterr().err


def test_the_traceback_is_there_when_it_is_asked_for(
    backend, config, monkeypatch, capsys
):
    """The message above is what an operator needs. A bug report needs the stack,
    and an air-gapped site cannot just re-run it under a debugger."""
    monkeypatch.setenv("VCOWS_TRACEBACK", "1")
    backend.world = [ours("app01")]
    monkeypatch.setattr(
        backend, "destroy", lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert cli.main(["destroy", config, "--yes"]) == 1

    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "RuntimeError: boom" in err


# -- what the record itself says ---------------------------------------------


def test_the_record_says_which_run_wrote_it(backend, config, tmp_path):
    """`run.json` reaches whoever reads it detached from the terminal that
    produced it, and often from the directory too: which verb ran, against which
    deployment and backend, and between which two instants."""
    backend.world = [ours("app01"), ours("app02")]

    assert cli.main(["deploy", config]) == 0
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["command"] == "deploy"
    assert record["deployment"] == "lab-a"
    assert record["backend"] == "fake"
    assert record["outcome"] == "nothing-to-create"
    assert record["started"] and record["started"] <= record["finished"]


def test_a_recorded_decision_names_the_domain_it_was_made_about(
    backend, config, tmp_path
):
    """`reason` is English. `existing` is the identity that sentence is about,
    and for a skip the id of the domain that was left alone appears nowhere else
    in the run directory at all."""
    backend.world = [ours("app01"), ours("app02")]

    assert cli.main(["deploy", config]) == 0
    decisions = json.loads((latest_run(tmp_path) / "run.json").read_text())["decisions"]
    assert [d["vm"] for d in decisions] == ["app01", "app02"]
    assert {d["action"] for d in decisions} == {"skip"}
    assert all(d["reason"] for d in decisions)
    assert decisions[0]["existing"] == {"name": "app01", "id": ours("app01").id}


def test_the_record_is_indented_and_key_sorted(backend, config, tmp_path):
    """It is read by hand at a site and diffed between two runs, and both want
    one field per line in an order that does not move."""
    backend.world = [ours("app01"), ours("app02")]

    assert cli.main(["deploy", config]) == 0
    text = (latest_run(tmp_path) / "run.json").read_text()
    keys = re.findall(r'^  "(\w+)"', text, re.MULTILINE)
    assert len(keys) > 1 and keys == sorted(keys), text


# -- the parser -------------------------------------------------------------


def test_the_parser_is_named_after_the_command_not_the_module():
    """The image's `/usr/local/bin/vcows` is `python3 -m orchestrator.cli`, so
    argparse's own default would put `__main__.py` in every usage line and every
    error message -- naming, to the operator, something they cannot type."""
    assert cli._parser().prog == "vcows"


def test_the_version_flag_answers_like_the_verb(capsys):
    """Two spellings, one answer. `--version` is the only top-level flag there
    is, and it is what somebody types at a container to ask what it is."""
    with pytest.raises(SystemExit) as exited:
        cli.main(["--version"])

    assert exited.value.code == 0
    assert VERSION in capsys.readouterr().out


def test_a_bare_vcows_is_a_usage_error_rather_than_a_traceback(capsys):
    """`required=True` is what makes argparse own the missing verb. Without it
    the namespace has no `func`, and `vcows` alone reaches `main`'s catch-all as
    an AttributeError and exits 1 -- the code a wrapper script reads as a failed
    deployment rather than as a mistyped command."""
    with pytest.raises(SystemExit) as exited:
        cli.main([])

    assert exited.value.code == 2
    assert "AttributeError" not in capsys.readouterr().err
