"""The static OpenTofu module, checked offline against the pinned provider.

**Three commands, because no two of them are enough** -- and finding that out is
worth recording, since findings.md R4 credits `tofu validate` with catching "every
missing, misnamed or mistyped variable":

* `tofu validate` checks the *module*: resource attribute names, expression types,
  provider schema conformance. It does **not** read tfvars at all. A tfvars
  document missing a required object attribute passes it.
* `tofu console` evaluates the variables, so it is what actually type-checks the
  emitted tfvars against the `variable` blocks. Its exit code is 0 regardless, so
  the gate is its diagnostics rather than its status.
* Neither of those reads an attribute *value*. Both accept a seed volume declared
  `raw`, a domain with no `features` block, and a domain with no marker at all --
  twelve mutations of `main.tf` passed the entire suite green, including three
  that re-introduce acceptance defects. `tofu test` against a mocked provider is
  what closes that, and it lives in `libvirt-module.tftest.hcl` beside this file.

`tofu plan` would catch all three, but it configures the real provider and so
needs the hypervisor. These run with no network at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import MIRROR, NEEDS_TOFU, REPO, TOFU, needs_tofu, require, tofu_env

MODULE = REPO / "orchestrator" / "backends" / "libvirt" / "tofu"
LOCK = REPO / "docs" / "provider-0.9.8.lock.hcl"
GOLDEN = REPO / "tests" / "golden" / "libvirt.tfvars.json"


def run(args, workdir, env, timeout=180):
    assert TOFU is not None  # every caller is behind `needs_tofu`
    return subprocess.run(
        [TOFU, f"-chdir={workdir}", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        input="null\n",
    )


@pytest.fixture(scope="module")
def initialised(tmp_path_factory):
    """One initialised copy of the module; tests swap tfvars in and out of it."""
    require("tofu", TOFU is not None and MIRROR.is_dir(), NEEDS_TOFU)
    workdir = tmp_path_factory.mktemp("tofu")
    for tf in MODULE.glob("*.tf"):
        shutil.copy(tf, workdir)
    # The committed lock, so init cannot quietly select a different build.
    shutil.copy(LOCK, workdir / ".terraform.lock.hcl")
    env = tofu_env(workdir)
    r = run(["init", "-input=false"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    return workdir, env


def write_vars(workdir: Path, tfvars: dict) -> None:
    (workdir / "x.auto.tfvars.json").write_text(json.dumps(tfvars, indent=2))


def diagnostics(result) -> str:
    """Console reports problems and still exits 0, so read what it said."""
    out = result.stdout + result.stderr
    return out if ("Error:" in out or "undeclared variable" in out) else ""


def golden_tfvars() -> dict:
    """The golden document, plus the one variable `render` stopped emitting.

    The module still declares `uri` and is deleted whole rather than trimmed, so
    supplying it here keeps this gate checking the other twenty-odd variables
    instead of failing on the one that is on its way out.
    """
    return {"uri": "qemu+sshcmd://vcows@vcows/system", **json.loads(GOLDEN.read_text())}


@pytest.fixture
def tfvars() -> dict:
    return golden_tfvars()


# -- the module -------------------------------------------------------------


@needs_tofu
def test_module_validates(initialised):
    workdir, env = initialised
    r = run(["validate"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_tofu
def test_init_used_the_mirror_and_the_committed_lock(initialised):
    """A lock produced against a registry records different hashes than one
    produced against a mirror, and the mismatch reads like corruption."""
    workdir, _ = initialised
    assert LOCK.read_text() == (workdir / ".terraform.lock.hcl").read_text()
    assert (workdir / ".terraform" / "providers").is_dir()


# -- the emitted tfvars -----------------------------------------------------


@needs_tofu
def test_golden_tfvars_satisfy_the_variable_types(initialised, tfvars):
    workdir, env = initialised
    write_vars(workdir, tfvars)
    r = run(["console"], workdir, env)
    # Console reports problems and still exits 0, so `diagnostics` is the gate --
    # but a zero exit is not nothing either, and reading only the text would miss
    # a console that died before it evaluated anything.
    assert r.returncode == 0, r.stdout + r.stderr
    assert diagnostics(r) == ""


@needs_tofu
def test_the_uefi_and_bridge_branches_also_typecheck(initialised, tfvars):
    """Bridged networking is unexercised at v0.1 but the branch still has to
    render and type-check, or the schema is carrying a field that cannot work."""
    vm = tfvars["vms"]["app01"]
    vm["nics"] = [
        {
            "mac": "52:54:00:11:22:33",
            "model": "virtio",
            "network": None,
            "bridge": "br0",
        }
    ]
    tfvars["base_volume"] = {
        "name": "golden.qcow2",
        "create": False,
        "path": "/var/lib/libvirt/images/golden.qcow2",
        "source": "",
    }
    workdir, env = initialised
    write_vars(workdir, tfvars)
    r = run(["console"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert diagnostics(r) == ""


@needs_tofu
@pytest.mark.parametrize(
    "mutate, expect",
    [
        (lambda v: v["vms"]["app01"].pop("machine"), "machine"),
        (lambda v: v.__setitem__("pooll", v.pop("pool")), "undeclared variable"),
        (
            lambda v: v["vms"]["app01"].__setitem__("vcpus", "two"),
            "a number is required",
        ),
        (lambda v: v["base_volume"].pop("create"), "create"),
    ],
)
def test_the_gate_has_teeth(initialised, tfvars, mutate, expect):
    """A gate that cannot fail is not a gate. Each mutation is a mistake render.py
    could plausibly make."""
    mutate(tfvars)
    workdir, env = initialised
    write_vars(workdir, tfvars)
    assert expect in diagnostics(run(["console"], workdir, env))


# -- the rendered module ----------------------------------------------------

TFTEST = REPO / "tests" / "libvirt-module.tftest.hcl"


@pytest.fixture(scope="module")
def mocked(tmp_path_factory):
    """A second initialised copy, for `tofu test` against a mocked provider.

    Its own directory rather than `initialised`'s: the console tests swap a
    deliberately broken ``x.auto.tfvars.json`` in and out of theirs, and a run
    landing between two of them would be checking a mutation.
    """
    require("tofu", TOFU is not None and MIRROR.is_dir(), NEEDS_TOFU)
    workdir = tmp_path_factory.mktemp("tofu-test")
    for tf in MODULE.glob("*.tf"):
        shutil.copy(tf, workdir)
    shutil.copy(LOCK, workdir / ".terraform.lock.hcl")
    shutil.copy(TFTEST, workdir)
    # The name `_deploy` writes, so the module is fed exactly what a run feeds it.
    document = json.dumps(golden_tfvars(), indent=2)
    (workdir / "main.auto.tfvars.json").write_text(document)
    env = tofu_env(workdir)
    r = run(["init", "-input=false"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    return workdir, env


@needs_tofu
def test_the_module_renders_what_the_acceptance_run_settled(mocked):
    """The assertions are in the .tftest.hcl, each with its own message. This is
    the harness: it is the exit code and the output that reach a reader here."""
    workdir, env = mocked
    r = run(["test", "-no-color"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failed" in r.stdout, r.stdout + r.stderr


@needs_tofu
def test_the_module_gate_has_teeth(mocked, tmp_path):
    """A gate that cannot fail is not a gate -- and this one is the only thing
    reading the module's values, so it gets its own proof. Deleting the marker is
    the mutation with the worst production shape: the VMs boot, the run reports
    success, and `vcows destroy` can never find them again."""
    workdir, env = mocked
    main = workdir / "main.tf"
    intact = main.read_text()
    try:
        main.write_text(
            intact.replace("metadata = { xml = each.value.marker_xml }", "")
        )
        r = run(["test", "-no-color"], workdir, env)
        assert r.returncode != 0, r.stdout + r.stderr
    finally:
        main.write_text(intact)
