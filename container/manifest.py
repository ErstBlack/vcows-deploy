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
import subprocess
import sys

QUERY = "%{NAME}\t%{VERSION}-%{RELEASE}\t%{LICENSE}\t%{SOURCERPM}\n"


def packages() -> list[dict[str, str]]:
    out = subprocess.run(
        ["rpm", "-qa", "--qf", QUERY], capture_output=True, text=True, check=True
    ).stdout
    found = []
    for line in out.splitlines():
        name, version, license_, source = line.split("\t")
        found.append(
            {
                "name": name,
                "version": version,
                "license": license_,
                "source_rpm": source,
            }
        )
    return sorted(found, key=lambda p: p["name"])


def tofu_version() -> dict:
    out = subprocess.run(
        ["tofu", "version", "-json"], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def main() -> int:
    installed = packages()
    manifest = {
        "vcows": os.environ["VCOWS_VERSION"],
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "built": os.environ.get("BUILD_DATE", "unknown"),
        "base_image": {
            "name": os.environ.get("BASE_IMAGE", "unknown"),
            "digest": os.environ.get("BASE_DIGEST", "unknown"),
        },
        "tofu": tofu_version(),
        "provider": {
            "source": "registry.opentofu.org/dmacvicar/libvirt",
            "version": os.environ.get("PROVIDER_VERSION", "unknown"),
            "artifact_sha256": os.environ.get("PROVIDER_SHA256", "unknown"),
            # The registry serves no signature for this provider, so the lock
            # hash is the only integrity anchor. See licenses/dmacvicar-libvirt/.
            "lock_hash": os.environ.get("PROVIDER_LOCK_HASH", "unknown"),
        },
        "packages": installed,
        # Deduplicated, because 300-odd binaries come from far fewer sources and
        # it is the sources that have to be mirrored.
        "source_rpms": sorted({p["source_rpm"] for p in installed if p["source_rpm"]}),
    }
    json.dump(manifest, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
