"""Every claim the build manifest makes, and the two that were once wrong.

`packages()` and `tofu_version()` shell out to `rpm` and `tofu`, so only the
image can run them for real -- `test_image.test_the_build_manifest_records_what_shipped`
is what checks the shipped file, behind the image gate. What is checked here is
the shape either one is asked for and what `main()` does with the answer, against
a faked `subprocess.run`: the `(none)` sentinel filter and the `source_rpms`
deduplication are both measured behaviours that nothing asserted until now.

`git_sha` and `provider` need no faking and are the two that were wrong: the git
SHA the image built at `e5d5a2c` recorded for a tree it did not match, and the
provider block that came from build args rather than from the lock the deploy
installs from.

Ungated and offline. `container/manifest.py` imports nothing but the standard
library, on purpose -- it runs before the application exists.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from container import manifest
from tests.conftest import REPO

LOCK = REPO / "docs" / "provider-0.9.8.lock.hcl"
PVE_LOCK = REPO / "docs" / "provider-0.111.1.lock.hcl"

CLEAN = "15e8dcfe0139e134093cb35f4e5c66760bb0d086"


# -- the git SHA -------------------------------------------------------------


@pytest.mark.parametrize("value", [CLEAN, f"{CLEAN}-dirty"])
def test_a_commit_is_recorded_as_given(monkeypatch, value):
    monkeypatch.setenv("GIT_SHA", value)
    assert manifest.git_sha() == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "unknown",
        "15e8dcf",  # the short form an operator would type
        CLEAN.upper(),
        CLEAN[:-1],
        f"{CLEAN}-DIRTY",
        f"{CLEAN} -dirty",
        f"{CLEAN}\n{CLEAN}",
    ],
)
def test_anything_else_becomes_unknown(monkeypatch, value):
    """`unknown` is not the failure being closed here. Recording a *clean* commit
    for a tree that had uncommitted changes is, and that is what shipped: the
    image built at `e5d5a2c` named a commit without the `container/entrypoint.py`
    it contained."""
    monkeypatch.setenv("GIT_SHA", value)
    assert manifest.git_sha() == "unknown"


def test_an_unset_git_sha_is_unknown_rather_than_a_crash(monkeypatch):
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert manifest.git_sha() == "unknown"


# -- the provider ------------------------------------------------------------


def test_the_provider_is_read_from_the_lock_the_deploy_installs_from(monkeypatch):
    """Not from the ARGs. They are two records of one fact, and only one of them
    reaches `tofu init` -- so a manifest reading the other can name a provider the
    image does not contain."""
    monkeypatch.setenv("PROVIDER_LOCK", str(LOCK))
    monkeypatch.setenv("PROVIDER_SHA256", "0" * 64)

    found = manifest.provider()
    assert found["version"] == "0.9.8"
    assert found["lock_hash"] == "h1:yqZeKoJ+EZc3687/+ZBqBmtwzvBPLNwaEHW74+bSc6Y="
    assert found["artifact_sha256"] == "0" * 64


def test_the_remaining_arg_still_agrees_with_the_lock():
    """`PROVIDER_VERSION` survives only because the build's `sha256sum -c` names
    the mirrored zip by version. It is the last place the two can drift."""
    declared = re.search(
        r"^ARG PROVIDER_VERSION=(\S+)$", (REPO / "Containerfile").read_text(), re.M
    )
    assert declared is not None
    assert declared.group(1) == "0.9.8"


def test_the_containerfile_states_the_provider_version_once():
    """What makes the docstring above true. The lock `COPY` used to spell the
    version out, and `just verify-provider` reads neither that line nor the
    header comment -- so a bump that satisfied all four of its checks could still
    bake the previous lock, which `container/manifest.py` would then report as
    the provider (#118). Interpolating the `COPY` source removed the record; this
    stops it coming back."""
    text = (REPO / "Containerfile").read_text()
    declared = re.search(r"^ARG PROVIDER_VERSION=(\S+)$", text, re.M)
    assert declared is not None
    version = declared.group(1)

    stated = [line for line in text.splitlines() if version in line]
    assert stated == [f"ARG PROVIDER_VERSION={version}"], (
        f"the Containerfile states {version} in more than one place: {stated}"
    )
    assert re.findall(r"provider-\d+\.\d+\.\d+\.lock\.hcl", text) == [], (
        "the lock filename carries a literal version again; interpolate "
        "${PROVIDER_VERSION} instead"
    )


@pytest.mark.parametrize(
    "text",
    [
        'provider "x" {\n  version = "0.9.8"\n}\n',  # no hash
        'provider "x" {\n  hashes = ["h1:abc="]\n}\n',  # no version
        "",
    ],
)
def test_a_lock_that_does_not_say_fails_the_build(tmp_path, monkeypatch, text):
    """A build that cannot describe its provider must not produce an image that
    describes it wrongly. `SystemExit` out of a `RUN` step is a failed build."""
    lock = tmp_path / ".terraform.lock.hcl"
    lock.write_text(text)
    monkeypatch.setenv("PROVIDER_LOCK", str(lock))

    with pytest.raises(SystemExit):
        manifest.provider()


# -- the two shellouts -------------------------------------------------------


class _Run:
    """A stand-in for `subprocess.run`, dispatching on the command it was given.

    It records the keyword arguments as well as the argv, because how these two
    are invoked is part of what they promise: `check=True` is what turns a failed
    `rpm -qa` into a failed build rather than an empty package list.
    """

    def __init__(self, stdout_by_command: dict[str, str]):
        self.stdout_by_command = stdout_by_command
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(stdout=self.stdout_by_command[argv[0]])


def row(name, source, version="1.0-1.el10", license_="MIT", vendor="Fedora Project"):
    return "\t".join([name, version, license_, source, vendor])


@pytest.fixture
def run(monkeypatch):
    def _install(rpm_rows=(), tofu='{"terraform_version": "1.10.6"}'):
        fake = _Run({"rpm": "".join(f"{r}\n" for r in rpm_rows), "tofu": tofu})
        monkeypatch.setattr(manifest.subprocess, "run", fake)
        return fake

    return _install


def test_every_field_the_query_asks_for_is_split_back_out(run):
    fake = run([row("zlib", "zlib-1.3.1-2.el10.src.rpm", "1.3.1-2", "Zlib", "Fedora")])

    assert manifest.packages() == [
        {
            "name": "zlib",
            "version": "1.3.1-2",
            "license": "Zlib",
            "source_rpm": "zlib-1.3.1-2.el10.src.rpm",
            "vendor": "Fedora",
        }
    ]
    argv, kwargs = fake.calls[0]
    assert argv == ["rpm", "-qa", "--qf", manifest.QUERY]
    assert kwargs == {"capture_output": True, "text": True, "check": True}


def test_the_package_list_is_sorted_by_name(run):
    run([row(n, f"{n}.src.rpm") for n in ("zlib", "bash", "python3")])

    assert [p["name"] for p in manifest.packages()] == ["bash", "python3", "zlib"]


def test_a_package_rpm_gives_no_source_for_is_still_recorded(run):
    """The asymmetry `NO_TAG` documents. `packages` is what rpm said, verbatim --
    only the derived `source_rpms` list below is filtered, because only it is what
    D22's reposync runs against."""
    run([row("gpg-pubkey", manifest.NO_TAG)])

    assert manifest.packages()[0]["source_rpm"] == manifest.NO_TAG


def test_the_tofu_version_is_the_json_tofu_printed(run):
    fake = run(tofu='{"terraform_version": "1.10.6", "platform": "linux_amd64"}')

    assert manifest.tofu_version() == {
        "terraform_version": "1.10.6",
        "platform": "linux_amd64",
    }
    argv, kwargs = fake.calls[0]
    assert argv == ["tofu", "version", "-json"]
    assert kwargs == {"capture_output": True, "text": True, "check": True}


# -- the assembled manifest --------------------------------------------------


BUILT = {
    "VCOWS_VERSION": "0.1.0.0",
    "GIT_SHA": CLEAN,
    "BUILD_DATE": "2026-09-01T00:00:00Z",
    "BASE_IMAGE": "quay.io/centos/centos:stream10",
    "BASE_DIGEST": "sha256:" + "0" * 64,
    "PROVIDER_SHA256": "1" * 64,
    "PROVIDER_LOCK": str(LOCK),
}


@pytest.fixture
def built(monkeypatch):
    for key, value in BUILT.items():
        monkeypatch.setenv(key, value)


def emitted(capsys) -> tuple[dict, str]:
    out = capsys.readouterr().out
    return json.loads(out), out


def test_the_manifest_records_the_build_it_was_run_by(built, run, capsys):
    run([row("zlib", "zlib-1.3.1-2.el10.src.rpm")])

    assert manifest.main() == 0

    found, _ = emitted(capsys)
    assert found["vcows"] == "0.1.0.0"
    assert found["git_sha"] == CLEAN
    assert found["built"] == "2026-09-01T00:00:00Z"
    assert found["base_image"] == {
        "name": "quay.io/centos/centos:stream10",
        "digest": "sha256:" + "0" * 64,
    }
    assert found["provider"]["artifact_sha256"] == "1" * 64
    assert found["tofu"] == {"terraform_version": "1.10.6"}
    assert [p["name"] for p in found["packages"]] == ["zlib"]


@pytest.mark.parametrize(
    "unset, key",
    [
        ("BUILD_DATE", "built"),
        ("BASE_IMAGE", "base_image.name"),
        ("BASE_DIGEST", "base_image.digest"),
        ("PROVIDER_SHA256", "provider.artifact_sha256"),
    ],
)
def test_a_build_arg_the_build_did_not_pass_reads_unknown(
    built, run, capsys, monkeypatch, unset, key
):
    """`unknown` rather than a crash, and rather than a plausible-looking value:
    the same reason `git_sha` refuses to record a clean SHA it cannot vouch for."""
    monkeypatch.delenv(unset)
    run([row("zlib", "zlib.src.rpm")])

    manifest.main()

    found, _ = emitted(capsys)
    for part in key.split("."):
        found = found[part]
    assert found == "unknown"


def test_a_build_that_does_not_know_its_own_version_fails(built, run, monkeypatch):
    """The one build arg with no fallback. A manifest that cannot say which vcows
    it describes is worse than no image."""
    monkeypatch.delenv("VCOWS_VERSION")
    run([row("zlib", "zlib.src.rpm")])

    with pytest.raises(KeyError):
        manifest.main()


def test_the_source_list_drops_the_sentinel_and_deduplicates(built, run, capsys):
    """Both measured behaviours, in one manifest. `(none)` is what rpm renders for
    the `gpg-pubkey` pseudo-packages the EPEL key leaves in the rpmdb -- it is
    truthy, so it would otherwise become a source RPM D22's reposync went looking
    for. And binaries outnumber their sources, which is the whole point of the
    list: roughly 160 packages down to roughly 116 sources as built."""
    run(
        [
            row("python3-libs", "python3.13-3.13.5-1.el10.src.rpm"),
            row("python3", "python3.13-3.13.5-1.el10.src.rpm"),
            row("gpg-pubkey", manifest.NO_TAG),
            row("hand-built", ""),
            row("zlib", "zlib-1.3.1-2.el10.src.rpm"),
        ]
    )

    manifest.main()

    found, _ = emitted(capsys)
    assert found["source_rpms"] == [
        "python3.13-3.13.5-1.el10.src.rpm",
        "zlib-1.3.1-2.el10.src.rpm",
    ]
    assert len(found["packages"]) == 5


def test_the_manifest_is_written_to_be_diffed(built, run, capsys):
    """Indented, key-sorted and newline-terminated. R5 wants releases comparable,
    and two manifests that differ only in key order are not."""
    run([row("zlib", "zlib.src.rpm"), row("bash", "bash.src.rpm")])

    manifest.main()

    found, raw = emitted(capsys)
    assert raw == json.dumps(found, indent=2, sort_keys=True) + "\n"


# -- every provider, and the one package rpm cannot see ----------------------


def test_providers_names_both_backends_from_their_own_locks(monkeypatch):
    """`provider()` can only ever describe one, and the image now installs two.
    Both are read from the lock the deploy installs from rather than from build
    args, for the reason the single-provider test above records."""
    monkeypatch.setenv("PROVIDER_LOCK", str(LOCK))
    monkeypatch.setenv("PROVIDER_SHA256", "0" * 64)
    monkeypatch.setenv("PVE_PROVIDER_LOCK", str(PVE_LOCK))
    monkeypatch.setenv("PVE_PROVIDER_SHA256", "1" * 64)

    found = manifest.providers()
    assert [p["source"] for p in found] == [
        "registry.opentofu.org/dmacvicar/libvirt",
        "registry.opentofu.org/bpg/proxmox",
    ]
    assert [p["version"] for p in found] == ["0.9.8", "0.111.1"]
    assert found[1]["lock_hash"].startswith("h1:")
    assert found[1]["artifact_sha256"] == "1" * 64


def test_a_build_without_the_proxmox_lock_reports_only_libvirt(monkeypatch):
    """The list is what the *image* carries. A build that shipped one provider
    must not claim two -- that is the same untruth as the git SHA."""
    monkeypatch.setenv("PROVIDER_LOCK", str(LOCK))
    monkeypatch.delenv("PVE_PROVIDER_LOCK", raising=False)
    assert len(manifest.providers()) == 1


def test_the_vendored_wheel_is_reported_because_rpm_cannot_see_it(monkeypatch):
    """`packages()` reads the RPM database, which is silent about
    /opt/vcows/vendor. syft finds the .dist-info and reports proxmoxer, so a
    manifest that stayed quiet would disagree with the SBOM shipped beside it."""
    monkeypatch.setenv("PROXMOXER_VERSION", "2.3.0")
    monkeypatch.setenv("PROXMOXER_SHA256", "a" * 64)

    found = manifest.pip_packages()
    assert len(found) == 1
    assert found[0]["name"] == "proxmoxer"
    assert found[0]["version"] == "2.3.0"
    assert found[0]["license"] == "MIT"
    assert found[0]["artifact_sha256"] == "a" * 64


def test_a_build_that_vendored_nothing_reports_nothing(monkeypatch):
    """Empty, not a placeholder entry: the field says what is there."""
    monkeypatch.delenv("PROXMOXER_VERSION", raising=False)
    assert manifest.pip_packages() == []


def test_the_containerfile_and_the_provenance_note_agree_on_the_wheel():
    """Two records of one fact, which is how one of them goes stale.
    `just verify-provider` checks the same pair; this is the half that runs in
    the default suite, with no mirror and no image."""
    text = (REPO / "Containerfile").read_text()
    version = re.search(r"^ARG PROXMOXER_VERSION=(\S+)$", text, re.M)
    digest = re.search(r"^ARG PROXMOXER_SHA256=(\S+)$", text, re.M)
    assert version is not None and digest is not None

    prov = (REPO / "licenses" / "proxmoxer" / "PROVENANCE.md").read_text()
    assert f"`{version.group(1)}`" in prov
    assert digest.group(1) in prov
    # The URL must name the version the ARG pins, or the build downloads one
    # wheel while every record describes another.
    assert f"proxmoxer-{version.group(1)}-py3-none-any.whl" in text


def test_the_containerfile_states_the_proxmox_provider_version_once():
    """Same rule as the libvirt provider's: a literal survives a bump that
    updated every other place, and the manifest would then truthfully report a
    provider nobody meant to ship."""
    text = (REPO / "Containerfile").read_text()
    declared = re.search(r"^ARG PVE_PROVIDER_VERSION=(\S+)$", text, re.M)
    assert declared is not None
    version = declared.group(1)
    stated = [ln for ln in text.splitlines() if version in ln and "ARG " in ln]
    assert stated == [f"ARG PVE_PROVIDER_VERSION={version}"], (
        f"the Containerfile spells {version} somewhere beyond its ARG: {stated}. "
        f"Use ${{PVE_PROVIDER_VERSION}} instead"
    )
