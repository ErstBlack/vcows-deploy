"""The build manifest's two claims that are not observations.

`packages()` and `tofu_version()` shell out and can only run inside the image,
which is where `tests/test_image.py` checks them. The two here are pure and are
the two that were wrong: the git SHA the image built at `e5d5a2c` recorded for a
tree it did not match, and the provider block that came from build args rather
than from the lock the deploy installs from.

Ungated and offline. `container/manifest.py` imports nothing but the standard
library, on purpose -- it runs before the application exists.
"""

from __future__ import annotations

import re

import pytest

from container import manifest
from tests.conftest import REPO

LOCK = REPO / "docs" / "provider-0.9.8.lock.hcl"

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
