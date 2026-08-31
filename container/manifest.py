"""Write the build manifest. Runs once, inside the image, at build time.

R5 wants every release to be reproducible and archivable: which OpenTofu, which
provider, which RPMs, which git revision. Only a step inside the build can read
`rpm -qa`, and only the build knows the git SHA and the base digest -- so this
runs there and the result is baked in at `/opt/vcows/manifest.json`.

It also carries the **source**-RPM list. D22 settled that the GPL obligation
cannot be engineered away while the container is the deliverable, and D5 tied
sizing it to the BOM freezing. The BOM freezes here, so the list of source
packages behind the shipped binaries is emitted now and the sidecar becomes a
`reposync` against a list that already exists rather than a research task.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# `%{VENDOR}` because the source sidecar is assembled per vendor: the EPEL
# packages come from a different repository than the AppStream ones and are
# otherwise indistinguishable in `source_rpms`, which is the list D22's reposync
# runs against.
QUERY = "%{NAME}\t%{VERSION}-%{RELEASE}\t%{LICENSE}\t%{SOURCERPM}\t%{VENDOR}\n"

#: rpm renders a tag the package does not carry as the literal string
#: ``(none)``, which is truthy. The rows that hit this are the ``gpg-pubkey``
#: pseudo-packages: the ``Containerfile``'s ``dnf`` layer installs
#: ``epel-release``, which imports the EPEL key, and removes the package again
#: without removing the key from the rpmdb, so two of them are in every image.
#: Measured on rpm 4.19.1.1: the conditional query format
#: ``%|SOURCERPM?{%{SOURCERPM}}:{}|`` does **not** help -- the tag tests as
#: present and still renders ``(none)`` -- so the sentinel has to be dropped
#: here. ``%{VENDOR}`` renders it too, and is deliberately left alone: `tofu`
#: carries no vendor because it is a GitHub release RPM, and the manifest
#: recording what rpm actually said about each package is the point of
#: ``packages``. Only ``source_rpms`` below is a derived list that a reposync
#: consumes, so only it is filtered.
NO_TAG = "(none)"

#: A full commit, optionally marked dirty. `.containerignore` excludes `.git/`,
#: so the image cannot see its own tree state and this arrives as a build arg --
#: which means a stale or hand-typed value is always possible.
SHA_PATTERN = re.compile(r"[0-9a-f]{40}(-dirty)?\Z")

#: The two facts the lock records. `version = "0.9.8"` sits inside the provider
#: block, and the `h1:` hash is the only integrity anchor the registry offers.
LOCK_VERSION = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)
LOCK_HASH = re.compile(r'"(h1:[^"]+)"')


def packages() -> list[dict[str, str]]:
    # Fixed argv, no shell, and it runs inside our own image at build time:
    # the PATH these resolve against is the image's, not a user's.
    out = subprocess.run(  # noqa: S603
        ["rpm", "-qa", "--qf", QUERY],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found = []
    for line in out.splitlines():
        name, version, license_, source, vendor = line.split("\t")
        found.append(
            {
                "name": name,
                "version": version,
                "license": license_,
                "source_rpm": source,
                "vendor": vendor,
            }
        )
    return sorted(found, key=lambda p: p["name"])


def git_sha() -> str:
    """The commit, or ``unknown`` -- never a clean SHA the build cannot vouch for.

    R5's whole purpose is answering which build produced a given artifact, and
    the image built at `e5d5a2c` failed it: the manifest named a commit that did
    not contain `container/entrypoint.py`, which that image shipped. The build ran
    from a dirty tree and the arg said otherwise.

    `unknown` is not that failure. It says the build could not vouch for the
    value, which is true and readable; a clean SHA for a modified tree is a
    statement about source that cannot be checked out.
    """
    value = os.environ.get("GIT_SHA", "")
    return value if SHA_PATTERN.match(value) else "unknown"


def provider() -> dict:
    """Version and hash from the committed lock, not from build args.

    The ARGs and the lock are two records of one fact, and the *deploy* uses the
    lock -- it is copied into the module directory the CLI stages from. A manifest
    reading the ARGs can therefore name a provider the image does not install,
    which is the git-SHA untruth one layer down.
    """
    path = Path(os.environ["PROVIDER_LOCK"])
    text = path.read_text()
    version, lock_hash = LOCK_VERSION.search(text), LOCK_HASH.search(text)
    if version is None or lock_hash is None:
        raise SystemExit(
            f"{path}: no provider version or h1 hash in the lock, so the manifest "
            f"cannot say what provider this image installs"
        )
    return {
        "source": "registry.opentofu.org/dmacvicar/libvirt",
        "version": version.group(1),
        "artifact_sha256": os.environ.get("PROVIDER_SHA256", "unknown"),
        # The registry serves no signature for this provider, so the lock hash is
        # the only integrity anchor. See licenses/dmacvicar-libvirt/.
        "lock_hash": lock_hash.group(1),
    }


def tofu_version() -> dict:
    out = subprocess.run(
        ["tofu", "version", "-json"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def main() -> int:
    installed = packages()
    manifest = {
        "vcows": os.environ["VCOWS_VERSION"],
        "git_sha": git_sha(),
        "built": os.environ.get("BUILD_DATE", "unknown"),
        "base_image": {
            "name": os.environ.get("BASE_IMAGE", "unknown"),
            "digest": os.environ.get("BASE_DIGEST", "unknown"),
        },
        "tofu": tofu_version(),
        "provider": provider(),
        "packages": installed,
        # Deduplicated, because binaries outnumber their sources and it is the
        # sources that have to be mirrored -- roughly 160 down to roughly 116 as
        # built. Approximate on purpose: the exact pair moves with every base
        # image and every `dnf` change, and this file is the thing that reports
        # it, so a number written here is the one that goes stale.
        "source_rpms": sorted(
            {p["source_rpm"] for p in installed if p["source_rpm"] not in ("", NO_TAG)}
        ),
    }
    json.dump(manifest, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
