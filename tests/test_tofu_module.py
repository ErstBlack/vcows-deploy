"""The static OpenTofu module, checked offline against the pinned provider.

**Two commands, because neither alone is enough** -- and finding that out is worth
recording, since findings.md R4 credits `tofu validate` with catching "every
missing, misnamed or mistyped variable":

* `tofu validate` checks the *module*: resource attribute names, expression types,
  provider schema conformance. It does **not** read tfvars at all. A tfvars
  document missing a required object attribute passes it.
* `tofu console` evaluates the variables, so it is what actually type-checks the
  emitted tfvars against the `variable` blocks. Its exit code is 0 regardless, so
  the gate is its diagnostics rather than its status.

`tofu plan` would catch both, but it configures the provider and so needs the
hypervisor. These two run with no network at all.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = REPO / "orchestrator" / "backends" / "libvirt" / "tofu"
MIRROR = REPO / ".tools" / "tofu-mirror"
LOCK = REPO / "docs" / "provider-0.9.8.lock.hcl"
GOLDEN = REPO / "tests" / "golden" / "libvirt.tfvars.json"

TOFU = shutil.which("tofu")

needs_tofu = pytest.mark.skipif(
    TOFU is None or not MIRROR.is_dir(),
    reason=(
        "needs `tofu` on PATH and a provider mirror at .tools/tofu-mirror; "
        "see the Stage 2 prerequisites in the plan"
    ),
)


def tofu_env(workdir: Path) -> dict:
    """A CLI config pointing at the mirror only.

    `/etc/tofurc` is not a path OpenTofu reads, and under a rootless container a
    UID absent from /etc/passwd gets HOME=/, so even a correct ~/.tofurc is
    missed. TF_CLI_CONFIG_FILE is the only reliable lever (findings.md R6).
    """
    rc = workdir / "tofurc"
    rc.write_text(
        f"provider_installation {{\n"
        f"  filesystem_mirror {{\n"
        f'    path    = "{MIRROR}"\n'
        f'    include = ["registry.opentofu.org/dmacvicar/libvirt"]\n'
        f"  }}\n"
        f"  direct {{\n"
        f'    exclude = ["registry.opentofu.org/dmacvicar/libvirt"]\n'
        f"  }}\n"
        f"}}\n"
    )
    return {
        **os.environ,
        "TF_CLI_CONFIG_FILE": str(rc),
        "CHECKPOINT_DISABLE": "1",
        # Residual egress should fail fast rather than hang at a site.
        "no_proxy": "*",
        # Diagnostics are matched as text, so keep the ANSI out of them.
        "NO_COLOR": "1",
    }


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
    if TOFU is None or not MIRROR.is_dir():
        pytest.skip("tofu or mirror unavailable")
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


@pytest.fixture
def tfvars() -> dict:
    return json.loads(GOLDEN.read_text())


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
    assert diagnostics(run(["console"], workdir, env)) == ""


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
    assert diagnostics(run(["console"], workdir, env)) == ""


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
