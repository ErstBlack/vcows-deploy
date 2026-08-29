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
import shutil
import stat
import textwrap

import pytest

from orchestrator import VERSION, cli
from orchestrator.backends.base import Existing
from orchestrator.marker import Marker
from tests.conftest import tofu_env
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


needs_tofu_binary = pytest.mark.skipif(
    shutil.which("tofu") is None, reason="needs `tofu` on PATH"
)


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
            vms=[],
            artifacts={"existing_names": []},
            problems=[Problem(Severity.ERROR, "storage pool 'images' does not exist")],
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


def test_a_target_problem_stops_the_deploy(backend, config, monkeypatch):
    """`Discovered.problems` is how a backend reports what is wrong with the
    *target* -- a missing pool, an orphaned volume. Deploy treats them as fatal."""
    from orchestrator.backends.base import Discovered, Problem, Severity

    monkeypatch.setattr(
        backend,
        "preflight",
        lambda cfg, session: Discovered(
            vms=[],
            artifacts={"existing_names": []},
            problems=[Problem(Severity.ERROR, "storage pool 'images' does not exist")],
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


def test_an_empty_run_dir_still_works(backend, config, tmp_path):
    """The bind-mounted mountpoint. `podman run -v ./runs/lab-a:/run-dir` presents
    an empty directory that already exists, and that has to keep working."""
    mount = tmp_path / "mount"
    mount.mkdir()
    backend.world = [ours("app01")]

    assert cli.main(["destroy", config, "--yes", "--run-dir", str(mount)]) == 0
    assert (mount / "run.json").is_file()


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
