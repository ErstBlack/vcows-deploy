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
    """One real init -> plan -> apply, shared by the assertions below.

    **The apply happens here, not in a test.** V3 and V4 describe one sequence --
    a plan is only "stale" because something already applied it -- and while that
    apply lived in `test_a_saved_plan_applies_without_its_tfvars`, V4 passed only
    when it happened to run afterwards. Real, undeclared, and invisible until the
    suite started shuffling: both tests fail together under about a third of the
    seeds. Doing the mutation once in the fixture leaves the three tests below
    reading the same finished state in any order, and V4's second apply is
    idempotent -- a stale plan stays stale however many times it is offered.
    """
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
    # Removed *before* the apply, because that is V3's whole claim: the saved plan
    # carries its own variable values and no longer needs the file they came from.
    (workdir / "main.auto.tfvars.json").unlink()
    result = tofu.apply(workdir, workdir / "plan.bin")
    yield workdir, planned, result, tofu.outputs(workdir)
    mp.undo()


def test_the_machine_stream_and_the_human_output_coexist(applied):
    """V1. `-json` would have replaced the operator's view; `-json-into` does not."""
    workdir, planned, _, _ = applied
    stream = (workdir / "plan.json").read_text().splitlines()
    assert [json.loads(line)["type"] for line in stream].count("planned_change") == 1
    assert planned.changes["add"] == 1


def test_a_saved_plan_applies_without_its_tfvars(applied):
    """V3. The plan file in the run directory is self-contained: the variables are
    frozen into it, so what gets applied cannot drift from what was shown.

    The fixture deletes the tfvars before applying; this reads what that produced.
    """
    workdir, _, result, outputs = applied
    assert not (workdir / "main.auto.tfvars.json").exists()
    assert result.changes["add"] == 1
    assert outputs["vms"]["value"]["app01"]["name"] == "app01"


def test_a_stale_plan_is_refused_rather_than_replanned(applied):
    """V4. The fixture already applied this plan, which is what makes it stale.

    This is the property that makes plan-then-apply worth an extra process launch:
    OpenTofu itself refuses to act on a plan whose world has moved.
    """
    workdir, _, _, _ = applied
    with pytest.raises(tofu.TofuError) as caught:
        tofu.apply(workdir, workdir / "plan.bin")
    assert "stale" in str(caught.value).lower()
