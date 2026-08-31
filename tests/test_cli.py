"""The five commands, end to end, with `import libvirt` broken.

This is `tests/test_seam.py`'s cycle run the way an operator runs it -- through
`main()`, against a real OpenTofu, writing a real run directory. The fake backend
has no hypervisor semantics, so anything that passes here passes because the
pipeline works and not because libvirt happened to be installed.

`deploy` really does call `tofu init`, `plan`, `apply` and `output`, against
`tests/tofu/`. That module uses the builtin `terraform_data`, so nothing is
installed and nothing is contacted; what it proves is the part that has never run
before this stage -- that render's output reaches a plan, that a plan reaches an
apply, and that the apply's outputs come back through `parse_outputs`.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import textwrap

import pytest

from orchestrator import VERSION, cli
from orchestrator.backends.base import Existing
from orchestrator.marker import Marker
from tests.conftest import needs_tofu_binary, tofu_env
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


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """Point OpenTofu at an empty filesystem mirror, so a stray provider
    reference fails immediately instead of reaching for the registry."""
    mirror = tmp_path / "empty-mirror"
    mirror.mkdir()
    env = tofu_env(tmp_path, mirror=mirror)
    for key in ("TF_CLI_CONFIG_FILE", "no_proxy"):
        monkeypatch.setenv(key, env[key])


def latest_run(tmp_path):
    (deployment,) = (tmp_path / "runs").iterdir()
    (run,) = deployment.iterdir()
    return run


def ours(name: str, deployment: str = "lab-a") -> Existing:
    marker = Marker.for_vm(name, deployment)
    return Existing(name=name, id=marker.id, marker=marker)


# -- version and validate ---------------------------------------------------


def test_version_prints_the_single_definition(capsys):
    assert cli.main(["version"]) == 0
    assert VERSION in capsys.readouterr().out


@pytest.mark.parametrize(
    "raised",
    [
        subprocess.TimeoutExpired(["tofu", "version"], 30),
        json.JSONDecodeError("Expecting value", "not json", 0),
    ],
)
def test_version_survives_every_way_tofu_version_can_fail(monkeypatch, capsys, raised):
    """`version` is the command you run *because* something is wrong with the
    build. `_tofu_version`'s tuple already names the four classes `_capture` can
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


def test_validate_is_offline(no_libvirt, backend, config, capsys):  # noqa: F811
    assert cli.main(["validate", config]) == 0
    assert "valid" in capsys.readouterr().out
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


# -- preflight --------------------------------------------------------------


def test_preflight_reports_and_opens_a_session(no_libvirt, backend, config, capsys):  # noqa: F811
    assert cli.main(["preflight", config]) == 0
    out = capsys.readouterr().out
    assert "app01" in out and "create" in out
    assert backend.sessions[0].closed, "the session must close on the way out"


def test_preflight_refuses_an_unmarked_collision(backend, config, capsys):
    backend.world = [Existing(name="app01", id="0", marker=None)]
    assert cli.main(["preflight", config]) == 1
    assert "will not adopt or overwrite" in capsys.readouterr().out


# -- deploy -----------------------------------------------------------------


@needs_tofu_binary
def test_deploy_runs_the_whole_pipeline(no_libvirt, backend, config, offline, tmp_path):  # noqa: F811
    assert cli.main(["deploy", config]) == 0
    run = latest_run(tmp_path)

    # What prepare built, kept so a VM that will not boot can be debugged from
    # the media it was actually given rather than from a rebuild.
    assert (run / "seed" / "fake-artifact").is_file()

    # What was applied, and the record of it.
    tfvars = json.loads((run / "tofu" / "main.auto.tfvars.json").read_text())
    assert set(tfvars["vms"]) == {"app01", "app02"}
    assert (run / "tofu" / "plan.bin").is_file()
    assert (run / "tofu" / "terraform.tfstate").is_file()
    for phase in ("init", "plan", "apply"):
        assert (run / "tofu" / f"{phase}.json").is_file()

    inventory = json.loads((run / "inventory.json").read_text())
    assert set(inventory["vms"]) == {"app01", "app02"}

    record = json.loads((run / "run.json").read_text())
    assert record["outcome"] == "ok"
    assert record["vcows"] == VERSION
    assert record["created"] == ["app01", "app02"]
    assert record["tofu"]["terraform_version"]


@needs_tofu_binary
def test_the_run_directory_is_not_world_readable(backend, config, offline, tmp_path):
    """It holds the seed ISOs, and those hold `user_data` verbatim (F12)."""
    assert cli.main(["deploy", config]) == 0
    mode = stat.S_IMODE(latest_run(tmp_path).stat().st_mode)
    assert mode == 0o700


def test_a_second_deploy_creates_nothing_and_launches_no_tofu(
    backend, config, tmp_path, monkeypatch, capsys
):
    """Every VM already exists and is ours. D23 drops them from the tfvars, which
    leaves nothing to apply -- so the apply must not happen at all."""
    backend.world = [ours("app01"), ours("app02")]
    monkeypatch.setattr(
        cli.tofu, "init", lambda *a, **k: pytest.fail("tofu must not run")
    )
    assert cli.main(["deploy", config]) == 0
    assert "nothing to create" in capsys.readouterr().out
    assert not (latest_run(tmp_path) / "tofu").exists()


def test_a_refusal_stops_the_deploy_before_anything_is_built(
    backend, config, tmp_path, monkeypatch, capsys
):
    backend.world = [Existing(name="app01", id="0", marker=None)]
    monkeypatch.setattr(
        cli.tofu, "init", lambda *a, **k: pytest.fail("tofu must not run")
    )
    assert cli.main(["deploy", config]) == 1

    run = latest_run(tmp_path)
    assert not (run / "seed").exists()
    assert not (run / "tofu").exists()
    assert json.loads((run / "run.json").read_text())["outcome"] == "refused"
    assert "nothing was changed" in capsys.readouterr().err


def test_a_refused_deploys_reason_reaches_the_run_record(
    backend, config, tmp_path, monkeypatch
):
    """`decisions` records what would have been done; without `problems` the
    *reason* it was not exists only on a terminal somebody has since closed."""
    from orchestrator.backends.base import Discovered, Problem, Severity

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


def test_a_failed_apply_still_leaves_a_run_record(
    backend, config, tmp_path, monkeypatch, capsys
):
    """The run directory is what a site ships back for support, and today it is
    present for every run where nothing happened and absent for every run where
    something did."""

    def boom(*a, **k):
        raise cli.tofu.TofuError("tofu init failed (exit 1): no provider mirror")

    monkeypatch.setattr(cli.tofu, "init", boom)
    assert cli.main(["deploy", config]) == 1

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "no provider mirror" in record["error"]
    assert record["decisions"], "what it was about to do survives the failure"
    assert "TofuError" in capsys.readouterr().err


def test_a_deploy_that_worked_is_not_failed_by_the_version_it_records(
    backend, config, tmp_path, monkeypatch, capsys
):
    """`tofu version` is provenance, asked for after the apply succeeded and
    `inventory.json` is already on disk. Letting it raise reached `_guard`, which
    wrote `outcome: failed` over a deploy that created every VM it was asked to --
    beside an inventory saying otherwise."""

    def boom(*a, **k):
        raise cli.tofu.TofuError("tofu version failed (exit 1)")

    monkeypatch.setattr(cli.tofu, "init", lambda w: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "plan", lambda w, o: cli.tofu.Result(0, changes={"add": 2})
    )
    monkeypatch.setattr(cli.tofu, "apply", lambda w, p: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu,
        "outputs",
        lambda w: {"vms": {"value": {"app01": {"name": "app01"}, "app02": {}}}},
    )
    monkeypatch.setattr(cli.tofu, "version", boom)

    assert cli.main(["deploy", config]) == 0
    run = latest_run(tmp_path)
    record = json.loads((run / "run.json").read_text())
    assert record["outcome"] == "ok"
    assert record["tofu"] is None
    assert record["created"] == ["app01", "app02"]
    assert (run / "inventory.json").is_file()
    assert "cannot record the tofu version" in capsys.readouterr().err


def test_a_failed_apply_records_the_warnings_that_came_before_it(
    backend, config, tmp_path, monkeypatch
):
    """`Result.warnings` exists so the run directory can keep them -- "the copy
    that outlives the terminal" -- and the failed run is where that copy matters
    most. Collecting them once after all three steps meant the runs that raised
    recorded none of them."""
    warned = cli.tofu.Result(
        0, diagnostics=(cli.tofu.Diagnostic("warning", "deprecated argument"),)
    )

    def boom(*a, **k):
        raise cli.tofu.TofuError("tofu apply failed (exit 1)")

    monkeypatch.setattr(cli.tofu, "init", lambda w: warned)
    monkeypatch.setattr(
        cli.tofu,
        "plan",
        lambda w, o: cli.tofu.Result(
            0,
            changes={"add": 2},
            diagnostics=(cli.tofu.Diagnostic("warning", "unused variable"),),
        ),
    )
    monkeypatch.setattr(cli.tofu, "apply", boom)

    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert record["tofu_warnings"] == [
        "warning: deprecated argument",
        "warning: unused variable",
    ]


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


def test_a_plan_that_creates_nothing_is_never_applied(
    backend, config, tmp_path, monkeypatch
):
    """The last check between a mis-rendered tfvars and an apply that reports
    success having done nothing.

    Distinct from `test_a_second_deploy_creates_nothing_and_launches_no_tofu`,
    which never reaches tofu at all: here two VMs are genuinely being created,
    the module is initialised and planned, and the *plan* is the thing that
    proposes nothing. Replacing the guard with `if False` passed the whole suite.
    """
    monkeypatch.setattr(cli.tofu, "init", lambda w: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "plan", lambda w, o: cli.tofu.Result(0, changes={"add": 0})
    )
    monkeypatch.setattr(
        cli.tofu, "apply", lambda *a, **k: pytest.fail("apply must not run")
    )
    assert cli.main(["deploy", config]) == 1

    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "no creates for 2 VM(s)" in record["error"]


def test_a_module_that_created_fewer_vms_than_asked_fails_the_deploy(
    backend, config, tmp_path, monkeypatch
):
    """A renamed or partial output yields `created 0 VM(s)` under `outcome: ok`:
    a run whose two artifacts contradict each other, with the record siding
    against the truth. This is the reporting shape acceptance defect 5 passed
    through."""
    monkeypatch.setattr(cli.tofu, "init", lambda w: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "plan", lambda w, o: cli.tofu.Result(0, changes={"add": 2})
    )
    monkeypatch.setattr(cli.tofu, "apply", lambda w, p: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "outputs", lambda w: {"vms": {"value": {"app01": {"name": "app01"}}}}
    )
    monkeypatch.setattr(cli.tofu, "version", lambda w=None: {})

    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    assert "1 VM(s) for the 2" in record["error"]
    assert not (latest_run(tmp_path) / "inventory.json").exists()


def test_staging_refuses_a_module_directory_it_cannot_copy_whole(tmp_path):
    """Staging copies `*.tf` and the lock, so anything else is left behind and the
    apply runs against a module missing a piece of itself -- diagnosed at a site,
    as OpenTofu's error for whatever the absent file defined."""
    source = tmp_path / "tofu"
    source.mkdir()
    (source / "main.tf").write_text("# module\n")
    (source / "cloud-init.tftpl").write_text("# a template nothing carries\n")
    with pytest.raises(RuntimeError, match=re.escape("cloud-init.tftpl")):
        cli._stage_module(source, tmp_path / "workdir")


def test_staging_ignores_what_a_local_tofu_init_left_behind(tmp_path):
    """`.terraform/` and its state file are byproducts, not module content -- the
    staged copy initialises itself. Refusing them would mean a developer who ran
    `tofu init` in the source tree could no longer deploy."""
    source = tmp_path / "tofu"
    (source / ".terraform" / "providers").mkdir(parents=True)
    (source / "main.tf").write_text("# module\n")
    (source / ".terraform.tfstate").write_text("{}\n")
    (source / ".terraform.lock.hcl").write_text("# lock\n")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    cli._stage_module(source, workdir)
    assert sorted(p.name for p in workdir.iterdir()) == [
        ".terraform.lock.hcl",
        "main.tf",
    ]


def test_staging_an_empty_module_directory_is_not_an_empty_module(tmp_path):
    source = tmp_path / "tofu"
    source.mkdir()
    with pytest.raises(RuntimeError, match="no module to stage"):
        cli._stage_module(source, tmp_path)


def test_a_target_problem_stops_the_deploy(backend, config, monkeypatch):
    """`Discovered.problems` is how a backend reports what is wrong with the
    *target* -- a missing pool, an orphaned volume. Deploy treats them as fatal."""
    from orchestrator.backends.base import Discovered, Problem, Severity

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
        cli.tofu, "init", lambda *a, **k: pytest.fail("tofu must not run")
    )
    assert cli.main(["deploy", config]) == 1


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


# -- destroy ----------------------------------------------------------------


def test_destroy_takes_only_this_deployment(backend, config, tmp_path, capsys):
    """D36. The marker has carried `deployment` since 0.1.0.0, so the scope is a
    filter on data that is already there -- and destroying somebody else's VMs
    because they share a hypervisor is the data-loss event findings.md §2 names.
    """
    backend.world = [ours("app01"), ours("elsewhere", "lab-b")]

    assert cli.main(["destroy", config, "--yes"]) == 0

    session = backend.sessions[-1]
    assert session.destroyed == ["app01"]
    out = capsys.readouterr().out
    assert "belongs to deployment 'lab-b'" in out
    assert json.loads((latest_run(tmp_path) / "run.json").read_text())["destroyed"] == [
        "app01"
    ]


def test_a_destroy_that_could_not_finish_says_what_it_left(
    backend, config, tmp_path, capsys
):
    """2.3, end to end. An inactive pool makes every disk in it resolve as
    "already gone": the domain is destroyed and undefined, its marker with it,
    both volumes stay on disk, and the operator was told it worked."""
    from orchestrator.backends.base import Outcome, Problem, Severity

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
    assert "/pool/app01.qcow2" in captured.out
    assert "/pool/app01-seed.iso" in captured.out
    assert "could not refresh pool" in captured.err

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
    findings.md §1 rejects `tofu destroy` for, arriving through the seam meant to
    prevent it. Unreachable through the libvirt backend, which raises."""
    from orchestrator.backends.base import Outcome, Problem, Severity

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


def test_a_module_that_created_the_right_count_under_wrong_names_fails(
    backend, config, tmp_path, monkeypatch
):
    """RW-B1. The reconciliation compared counts, so two VMs asked for and two
    reported passed even when one was a different VM -- `run.json` and
    `inventory.json` in the same directory disagreeing about what exists, with
    `outcome: ok` over both. The message already computed the set difference and
    carried an `or 'names differ'` fallback, so a set comparison was always the
    intent. Unreachable through the libvirt module, whose `vms` output is keyed
    off `for_each = var.vms`."""
    monkeypatch.setattr(cli.tofu, "init", lambda w: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "plan", lambda w, o: cli.tofu.Result(0, changes={"add": 2})
    )
    monkeypatch.setattr(cli.tofu, "apply", lambda w, p: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu,
        "outputs",
        lambda w: {
            "vms": {"value": {"app01": {"name": "app01"}, "ghost": {"name": "ghost"}}}
        },
    )
    monkeypatch.setattr(cli.tofu, "version", lambda w=None: {})

    assert cli.main(["deploy", config]) == 1
    record = json.loads((latest_run(tmp_path) / "run.json").read_text())
    assert record["outcome"] == "failed"
    # The count matched, so the fallback branch is the one that has to speak.
    assert "names differ" in record["error"] or "app02" in record["error"]
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
    from orchestrator.backends.base import Outcome, Problem, Severity

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


def test_destroy_with_nothing_of_ours_is_not_an_error(backend, config, capsys):
    backend.world = [Existing(name="somebody-elses", id="0", marker=None)]
    assert cli.main(["destroy", config, "--yes"]) == 0
    assert "no VMs marked for deployment 'lab-a'" in capsys.readouterr().out


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


@needs_tofu_binary
def test_nothing_in_the_run_directory_is_readable_by_anyone_else(
    backend, config, offline, tmp_path
):
    """The directory has been 0700 since Stage 4; its contents were not. The
    state, the saved plan and the JSON streams are written by tofu and the seed
    ISOs by pycdlib, so vcows opens none of them and no chmod can reach them."""
    assert cli.main(["deploy", config]) == 0
    loose = sorted(
        str(p.relative_to(latest_run(tmp_path)))
        for p in latest_run(tmp_path).rglob("*")
        if p.stat().st_mode & 0o077
    )
    assert loose == []


def test_a_plan_with_no_change_summary_is_not_a_plan_that_creates_nothing(
    backend, config, tmp_path, monkeypatch
):
    """`_read_stream` returns `{}` for a stream that is missing or will not parse,
    deliberately -- the exit code is the authority on success. Reported as "no
    creates" it sends whoever reads it to the module instead of to the file."""
    monkeypatch.setattr(cli.tofu, "init", lambda w: cli.tofu.Result(0))
    monkeypatch.setattr(cli.tofu, "plan", lambda w, o: cli.tofu.Result(0))
    monkeypatch.setattr(
        cli.tofu, "apply", lambda *a, **k: pytest.fail("apply must not run")
    )
    assert cli.main(["deploy", config]) == 1

    error = json.loads((latest_run(tmp_path) / "run.json").read_text())["error"]
    assert "no change summary" in error
    assert "plan.json" in error


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
