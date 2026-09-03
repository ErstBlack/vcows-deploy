"""The image, with the network switched off.

Gated on ``VCOWS_IMAGE``, skipped with an explicit reason -- never silently,
following ``needs_rig`` and ``needs_tofu``. A gate that quietly passes because it
did not run is worse than no gate, and this one covers the failures that only ever
appear at a site: a provider that is not really in the mirror, a CLI config the
container never reads, and an RPM binding invisible to the interpreter that
actually runs.

Everything here runs ``--network=none``. That is the point: the build host is
connected and the site is not, so any dependency the build left dangling has to
fail here rather than as a DNS timeout at a delivery.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap

import pytest

from orchestrator import VERSION
from tests.conftest import REPO, WORKTREE, gate

IMAGE = os.environ.get("VCOWS_IMAGE")

# podman is part of the predicate, not an assumption. buildah builds this
# Containerfile unchanged and can even run containers, so a buildah-only runner
# would set VCOWS_IMAGE, report the gate available, and then die with
# `FileNotFoundError: podman` inside every test -- which reads as a broken suite
# rather than a missing dependency. It is not a substitute either way: `buildah
# run` does not honour the image ENTRYPOINT and mutates the working container
# between calls, and ENTRYPOINT, WORKDIR and per-run isolation are three of the
# things this file exists to prove.
pytestmark = gate(
    "image",
    IMAGE is not None and shutil.which("podman") is not None,
    "set VCOWS_IMAGE to a built image (e.g. localhost/vcows-deploy:0.1.0.0) and "
    "install podman to run the container gate; build it with `just image`. "
    "buildah cannot substitute: this gate asserts ENTRYPOINT, WORKDIR and "
    "per-run isolation, and `buildah run` provides none of the three",
)

GOLDEN = REPO / "tests" / "golden" / "libvirt.tfvars.json"

CONFIG = """\
schema_version: 1
deployment: gate
backend: libvirt
target:
  libvirt:
    uri: qemu+ssh://vcows@vcows/system
    pool: images
image:
  source_qcow2: /images/golden.qcow2
  base_volume_name: golden.qcow2
vms:
  - name: app01
    vcpus: 2
    memory_mib: 4096
    disk_gb: 40
    nics:
      - network: default
        ip_cidr: 192.168.122.60/24
        gateway: 192.168.122.1
        nameservers: [192.168.122.1]
"""

# The R2 environment: residual egress should fail fast rather than hang at a site.
OFFLINE_ENV = ("-e", "PIP_NO_INDEX=1", "-e", "no_proxy=*")


def run(*args, entrypoint=None, mounts=(), env=None, check=False):
    argv = ["podman", "run", "--rm", "--network=none", *OFFLINE_ENV]
    for key, value in (env or {}).items():
        argv += ["-e", f"{key}={value}"]
    for host, dest in mounts:
        argv += ["-v", f"{host}:{dest}:ro,Z"]
    if entrypoint is not None:
        argv += ["--entrypoint", entrypoint]
    assert IMAGE is not None  # every caller is behind the module-level skip
    argv += [IMAGE, *args]
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=300, check=check
    )


# -- it runs at all ---------------------------------------------------------


def test_the_image_runs_with_no_network():
    result = run("version")
    assert result.returncode == 0, result.stderr
    # Every vcows line is a log line on stderr now; stdout carries only
    # `_confirm`'s interactive prompt, which no verb here reaches.
    assert VERSION in result.stderr
    assert "tofu 1.12.6" in result.stderr


def test_the_rpm_binding_is_visible_to_the_interpreter_that_runs():
    """R7's trap, and it cannot be checked from outside the image. PyPI ships
    libvirt-python as an sdist only, so the binding has to come from the RPM --
    and a venv without --system-site-packages would hide it while
    `python3 -c 'import libvirt'` kept working elsewhere on the same box."""
    result = run(
        "-c",
        "import libvirt, yaml, jsonschema, pycdlib; print(libvirt.getVersion())",
        entrypoint="python3",
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) >= 8009000, "every undefine flag needs 8.9.0+"


def test_validate_runs_offline(tmp_path):
    """The offline phase, end to end, in the shipped artifact."""
    config = tmp_path / "gate.yaml"
    config.write_text(CONFIG)
    result = run("validate", "/config.yaml", mounts=[(config, "/config.yaml")])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "valid" in result.stderr


# -- the R2 gate ------------------------------------------------------------

GATE = textwrap.dedent("""\
    set -e
    mod=/opt/vcows/orchestrator/backends/libvirt/tofu
    mkdir -p /tmp/mod && cd /tmp/mod
    cp $mod/*.tf $mod/.terraform.lock.hcl .
    cp /vars.json ./main.auto.tfvars.json
    tofu init -input=false -no-color
    tofu validate -no-color
    python3 -c "
import pathlib, sys
a = pathlib.Path('/tmp/mod/.terraform.lock.hcl').read_text()
b = pathlib.Path('$mod/.terraform.lock.hcl').read_text()
sys.exit(0 if a == b else 'init rewrote the committed lock')
"
    echo GATE-INIT-OK
    tofu plan -input=false -no-color
""")


@pytest.fixture(scope="module")
def gate():
    """One offline init -> validate -> plan against the real libvirt module.

    The plugin cache is disabled for this run *on purpose*. The image ships a warm
    one, and with it `init` is satisfied before it ever consults the mirror -- so
    leaving it on would quietly turn the air-gap assertion below into a test of the
    cache. Disabling it puts the mirror back on the only path.
    """
    return run(
        "-c",
        GATE,
        entrypoint="sh",
        mounts=[(GOLDEN, "/vars.json")],
        env={"TF_PLUGIN_CACHE_DIR": ""},
    )


def test_the_provider_installs_from_the_baked_mirror_offline(gate):
    """The whole air-gap story in one assertion. There is no `direct` block in
    the CLI config, so if the mirror were incomplete this could not fall back --
    which is the intent: fail here, loudly, rather than resolve DNS at a site."""
    output = gate.stdout + gate.stderr
    assert "GATE-INIT-OK" in output, output
    assert "Installed dmacvicar/libvirt v0.9.8" in output
    assert "Success! The configuration is valid." in output


def test_init_did_not_rewrite_the_committed_lock(gate):
    """A lock produced against a registry records different hashes than one
    produced against a mirror, and the mismatch reads like corruption (R6)."""
    assert "init rewrote the committed lock" not in gate.stdout + gate.stderr


CACHE_GATE = textwrap.dedent("""\
    set -e
    mod=/opt/vcows/orchestrator/backends/libvirt/tofu
    mkdir -p /tmp/run && cd /tmp/run
    cp $mod/*.tf $mod/.terraform.lock.hcl .
    tofu init -input=false -no-color > /dev/null
    du -sk .terraform | cut -f1
""")


def test_the_plugin_cache_keeps_the_provider_out_of_every_run_directory():
    """Without it, `init` copies a 26 MB provider into the working directory --
    and D40 makes that a brand new directory on every single deploy, so the cost
    recurs for the life of the deployment. With the cache warmed at build time,
    `.terraform` is symlinks and the run directory carries only its own artifacts.
    """
    result = run("-c", CACHE_GATE, entrypoint="sh")
    assert result.returncode == 0, result.stderr
    kilobytes = int(result.stdout.strip().splitlines()[-1])
    assert kilobytes < 1024, (
        f".terraform is {kilobytes} KiB; the cache is not being used"
    )


# -- what the image says about itself ---------------------------------------


def inspect(fmt: str) -> str:
    assert IMAGE is not None
    return subprocess.run(
        ["podman", "inspect", "--format", fmt, IMAGE],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_the_labels_are_ours_and_not_the_bases():
    """F16. The base labels licenses=BSD-3-Clause, name=rockylinux, vendor=RESF
    and RESF authors; an image overriding none of them self-identifies to a third
    party as an RESF product."""
    labels = json.loads(inspect("{{json .Labels}}"))
    assert labels["name"] == "vcows-deploy"
    assert labels["org.opencontainers.image.title"] == "vcows-deploy"
    assert labels["org.opencontainers.image.version"] == VERSION
    assert "rocky" not in json.dumps(labels).lower().replace(
        labels["org.opencontainers.image.base.name"], ""
    ), "only base.name may mention the base"
    assert labels["org.opencontainers.image.base.digest"].startswith("sha256:")


#: What the Containerfile's documented build command computes, and the only two
#: shapes `container/manifest.py` will record.
GIT_SHA = re.compile(r"[0-9a-f]{40}(-dirty)?\Z")


def built_revision() -> str:
    """What the build records in the manifest, asked of the build.

    Not restated here, and that is the whole point. This file used to carry its
    own four-path copy of the shipped set, and the copy went stale: it omitted
    the ``Containerfile`` and ``.containerignore``, so an image built before an
    edit to either still matched a clean ``HEAD`` and the gate passed on an
    image the Containerfile no longer described (#63).

    ``scripts/lib.sh`` is sourceable by design -- its own header says it holds
    no commands of its own -- so ``source_revision`` can be asked directly. It
    also interpolates the pinned provider version, which the copy hardcoded.

    Returns the ``-dirty`` form on a modified tree, which is exactly what the
    build writes, so there is no case where this cannot be compared.
    """
    return subprocess.run(
        ["bash", "-c", f"source {REPO}/scripts/lib.sh && source_revision"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_the_build_manifest_records_what_shipped():
    """R5, and D51: the source-RPM list is what turns the GPL sidecar from a
    research task into a reposync against a list that already exists.

    ``git_sha`` is asserted because it was wrong and nothing said so: the image
    built at `e5d5a2c` named a commit that did not contain the
    `container/entrypoint.py` it shipped.
    """
    result = run(
        "-c", "print(open('/opt/vcows/manifest.json').read())", entrypoint="python3"
    )
    manifest = json.loads(result.stdout)

    assert manifest["vcows"] == VERSION
    assert GIT_SHA.match(manifest["git_sha"]), (
        f"{manifest['git_sha']!r} is neither a commit nor a commit marked dirty"
    )
    revision = built_revision()
    assert manifest["git_sha"] == revision, (
        f"the image was built from {manifest['git_sha']}, this tree is {revision}; "
        f"rebuild before trusting the gate"
    )
    assert manifest["tofu"]["terraform_version"] == "1.12.6"
    assert manifest["provider"]["version"] == "0.9.8"
    assert manifest["provider"]["lock_hash"].startswith("h1:")
    assert manifest["base_image"]["digest"].startswith("sha256:")

    packages = {p["name"] for p in manifest["packages"]}
    assert {"python3-libvirt", "python3-pycdlib", "tofu", "openssh-clients"} <= packages
    # The vendor field exists to make the EPEL entries findable: the sidecar is a
    # reposync, and pycdlib comes from a different repository than everything
    # else in the closure. (`tofu` carries no vendor at all -- it is a GitHub
    # release RPM, not a distribution package -- so this names the two that do.)
    vendors = {p["name"]: p["vendor"] for p in manifest["packages"]}
    assert vendors["python3-pycdlib"] == "Fedora Project"
    assert vendors["python3-libvirt"] == "Rocky Enterprise Software Foundation"
    assert manifest["source_rpms"], "the sidecar list is the point of recording these"
    assert len(manifest["source_rpms"]) < len(manifest["packages"])
    # Both assertions above stayed true while the list's first entry was the
    # literal string `(none)`: `rpm -qa` returns two `gpg-pubkey` rows carrying
    # no `%{SOURCERPM}`, and rpm renders an absent tag as that truthy string.
    # D22's reposync runs against this list, so a name that is not an SRPM is
    # the research task the list exists to remove. Asserting the shape rather
    # than the one sentinel catches the next one too.
    assert all(s.endswith(".src.rpm") for s in manifest["source_rpms"]), (
        "not source RPM filenames: "
        f"{sorted(s for s in manifest['source_rpms'] if not s.endswith('.src.rpm'))}"
    )


def test_the_provider_licence_travels_with_the_provider():
    """R3: 0.9.x ships no LICENSE file, and Apache-2.0 §4(a) puts the obligation
    on the redistributor regardless."""
    result = run(
        "-c",
        "import pathlib;"
        "d = pathlib.Path('/opt/vcows/licenses/dmacvicar-libvirt');"
        "print((d / 'LICENSE').read_text()[:200]);"
        "print((d / 'PROVENANCE.md').read_text()[:400])",
        entrypoint="python3",
    )
    assert result.returncode == 0, result.stderr
    assert "Apache License" in result.stdout
    assert "no `LICENSE` file" in result.stdout


def test_containerignore_keeps_a_nested_worktree_out_of_the_build_context(tmp_path):
    """`.containerignore` excludes `.claude/worktrees/`, and podman agrees.

    Asserted against podman's own matcher rather than a re-implementation of it.
    A test that parsed `.containerignore` here would be checking this repo's model
    of `filepath.Match` against itself, and the defect this pins is precisely that
    the model was wrong: the other patterns are not recursive, so a nested `.venv`
    and `.tools` reach the builder while the top-level ones do not.

    `FROM scratch` plus `COPY . /ctx` is the whole image -- no base pull, no
    network. The context is a fixture, not the repo, so this costs no `just image`.
    """
    ctx = tmp_path / "ctx"
    for d in (".venv", ".claude/worktrees/wt/.venv", ".claude/worktrees/wt/.tools"):
        (ctx / d).mkdir(parents=True)
        (ctx / d / "marker").write_text("x")
    (ctx / "kept.txt").write_text("x")
    shutil.copy(REPO / ".containerignore", ctx / ".containerignore")
    (ctx / "Containerfile").write_text("FROM scratch\nCOPY . /ctx\n")

    # Named per worktree: the `rmi -f` below is unconditional and podman's image
    # store is per-machine, so two worktrees running `just test` at once would
    # delete each other's gate image mid-build.
    tag = f"localhost/vcows-containerignore-gate:t{'-' + WORKTREE if WORKTREE else ''}"
    try:
        subprocess.run(
            ["podman", "build", "-q", "-t", tag, str(ctx)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["podman", "save", tag, "-o", str(tmp_path / "i.tar")],
            check=True,
            capture_output=True,
            text=True,
        )
        names = subprocess.run(
            ["tar", "-tf", str(tmp_path / "i.tar")], capture_output=True, text=True
        )
        assert names.returncode == 0, names.stderr
        layers = [n for n in names.stdout.split() if n.endswith(".tar")]
        assert layers, "no layer in the saved archive"
        subprocess.run(
            ["tar", "-xf", str(tmp_path / "i.tar"), "-C", str(tmp_path), *layers],
            check=True,
            capture_output=True,
            text=True,
        )
        shipped = set()
        for layer in layers:
            listing = subprocess.run(
                ["tar", "-tf", str(tmp_path / layer)], capture_output=True, text=True
            )
            shipped.update(listing.stdout.split())
    finally:
        subprocess.run(["podman", "rmi", "-f", tag], capture_output=True)

    assert any(n.endswith("kept.txt") for n in shipped), (
        f"the fixture never reached the builder, so this proves nothing: {shipped}"
    )
    leaked = sorted(n for n in shipped if "worktrees" in n)
    assert not leaked, (
        "a worktree inside the tree reached the build context: "
        f"{leaked}. .containerignore needs `.claude/worktrees/`, and note its "
        "other patterns are not recursive, so the worktree's own .venv and "
        ".tools come with it."
    )
