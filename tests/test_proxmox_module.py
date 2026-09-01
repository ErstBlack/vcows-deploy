"""The Proxmox OpenTofu module, checked offline against the pinned provider.

Same three commands and the same reasoning as `tests/test_tofu_module.py`, which
carries the long form. What is different is that this module has never been
applied against a real cluster, so these gates are the only thing standing
between a typo and a failure discovered at a site.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.conftest import MIRROR, NEEDS_TOFU, REPO, TOFU, needs_tofu, require, tofu_env

MODULE = REPO / "orchestrator" / "backends" / "proxmox" / "tofu"
LOCK = REPO / "docs" / "provider-0.111.1.lock.hcl"
GOLDEN = REPO / "tests" / "golden" / "proxmox.tfvars.json"
TFTEST = REPO / "tests" / "proxmox-module.tftest.hcl"


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
def mocked(tmp_path_factory):
    require("tofu", TOFU is not None and MIRROR.is_dir(), NEEDS_TOFU)
    workdir = tmp_path_factory.mktemp("pve-tofu")
    for tf in MODULE.glob("*.tf"):
        shutil.copy(tf, workdir)
    # The committed lock, so init cannot quietly select a different build.
    shutil.copy(LOCK, workdir / ".terraform.lock.hcl")
    shutil.copy(TFTEST, workdir)
    # The name `_deploy` writes, so the module is fed exactly what a run feeds it.
    shutil.copy(GOLDEN, workdir / "main.auto.tfvars.json")
    env = tofu_env(workdir)
    r = run(["init", "-input=false"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    return workdir, env


@needs_tofu
def test_the_module_is_valid_against_the_pinned_provider(mocked):
    """Resource attribute names, expression types, provider schema conformance.
    Offline, and it contacts nothing."""
    workdir, env = mocked
    r = run(["validate", "-no-color"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr


@needs_tofu
def test_the_rendered_tfvars_type_check_against_the_variables(mocked):
    """`validate` does not read tfvars at all -- a document missing a required
    object attribute passes it. `console` evaluates the variable blocks, so it is
    what catches a render that stopped matching the module."""
    workdir, env = mocked
    r = run(["console", "-no-color"], workdir, env)
    assert "Error:" not in (r.stdout + r.stderr), r.stdout + r.stderr


@needs_tofu
def test_the_module_renders_what_the_config_asked_for(mocked):
    """The assertions are in the .tftest.hcl, each with its own message. This is
    the harness: it is the exit code and the output that reach a reader here."""
    workdir, env = mocked
    r = run(["test", "-no-color"], workdir, env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 failed" in r.stdout, r.stdout + r.stderr


@needs_tofu
def test_the_module_gate_has_teeth(mocked):
    """A gate that cannot fail is not a gate -- and this one is the only thing
    reading the module's values. Deleting the marker is the mutation with the
    worst production shape: the VMs boot, the run reports success, and
    `vcows destroy` can never find them again."""
    workdir, env = mocked
    main = workdir / "main.tf"
    intact = main.read_text()
    try:
        main.write_text(
            intact.replace("description = each.value.description", 'description = ""')
        )
        r = run(["test", "-no-color"], workdir, env)
        assert r.returncode != 0, r.stdout + r.stderr
    finally:
        main.write_text(intact)
