"""The driver against the real OpenTofu CLI, on a module with no providers.

Everything here is a property of OpenTofu's command line rather than of our code,
which is exactly why it needs pinning: `orchestrator/tofu.py` is built on three
behaviours that are documented but not obviously stable, and a version bump is the
thing that would break them silently.

* `-json-into` writes the machine stream to a file *while* the human-readable
  output still goes to stdout. If that ever stops being true, the operator loses
  their view of a multi-GB upload and nobody finds out from a unit test.
* A saved plan carries its variable values, so the plan file in a run directory is
  a self-contained record of what was applied.
* A saved plan is *refused* once the state has moved, rather than quietly
  re-planned. That is the only thing making "what was shown is what was done" true
  across the window between preflight and apply.

`tests/tofu/` uses the builtin `terraform_data`, so `init` installs nothing and
contacts nothing -- verified against 1.12.6 with an empty mirror. This gate
therefore needs `tofu` on PATH and nothing else: no provider mirror, no network
and no hypervisor.
"""

from __future__ import annotations

import json
import shutil

import pytest

from orchestrator import tofu
from tests.conftest import REPO, gate, tofu_env

pytestmark = gate("tofu", shutil.which("tofu") is not None, "needs `tofu` on PATH")

MODULE = REPO / "tests" / "tofu"

TFVARS = {
    "endpoint": "good://example",
    "seed": "/dev/null",
    "vms": {"app01": {"marker_xml": '<vcows xmlns="urn:vcows:1">{}</vcows>'}},
}


@pytest.fixture(scope="module")
def applied(tmp_path_factory):
    """One real init -> plan -> apply, shared by the assertions below."""
    mp = pytest.MonkeyPatch()
    workdir = tmp_path_factory.mktemp("run")
    for tf in MODULE.glob("*.tf"):
        shutil.copy(tf, workdir)
    (workdir / "main.auto.tfvars.json").write_text(json.dumps(TFVARS))

    # An empty mirror: nothing here has a provider to install, and this is what
    # proves it rather than assuming it.
    mirror = workdir / "empty-mirror"
    mirror.mkdir()
    env = tofu_env(workdir, mirror=mirror)
    for key in ("TF_CLI_CONFIG_FILE", "no_proxy"):
        mp.setenv(key, env[key])

    tofu.init(workdir)
    planned = tofu.plan(workdir, workdir / "plan.bin")
    yield workdir, planned
    mp.undo()


def test_the_machine_stream_and_the_human_output_coexist(applied):
    """V1. `-json` would have replaced the operator's view; `-json-into` does not."""
    workdir, planned = applied
    stream = (workdir / "plan.json").read_text().splitlines()
    assert [json.loads(line)["type"] for line in stream].count("planned_change") == 1
    assert planned.changes["add"] == 1


def test_a_saved_plan_applies_without_its_tfvars(applied):
    """V3. The plan file in the run directory is self-contained: the variables are
    frozen into it, so what gets applied cannot drift from what was shown."""
    workdir, _ = applied
    (workdir / "main.auto.tfvars.json").unlink()

    result = tofu.apply(workdir, workdir / "plan.bin")
    assert result.changes["add"] == 1
    assert tofu.outputs(workdir)["vms"]["value"]["app01"]["name"] == "app01"


def test_a_stale_plan_is_refused_rather_than_replanned(applied):
    """V4. Runs after the apply above, which is what makes the plan stale.

    This is the property that makes plan-then-apply worth an extra process launch:
    OpenTofu itself refuses to act on a plan whose world has moved.
    """
    workdir, _ = applied
    with pytest.raises(tofu.TofuError) as caught:
        tofu.apply(workdir, workdir / "plan.bin")
    assert "stale" in str(caught.value).lower()
