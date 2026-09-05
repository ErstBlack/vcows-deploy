"""One VM deployed, booted, read over SSH, and destroyed again.

`tests/test_libvirt_smoke.py` says in its own docstring that **no guest is
booted and no guest address is observed** -- the domain reaches firmware and
stops there. That is the half this file supplies. It is the only test that sees
what cloud-init did inside a running guest, which is the only place three
recorded findings can surface at all: defect 5 in `docs/archive/acceptance.md` (the
document was accepted, the normaliser threw, the guest fell back to DHCP and
reported `cloud-init status: done` on an address nobody asked for), `#161` (the
device name the guest ends up with), and `#164`.

Under the `rig` gate alone, which already names the hypervisor. The deploy runs
in this process through `python3-libvirt`. No new gate name: `KNOWN` in
`tests/test_gates.py` stays a closed set of six.

Two facts about the rig it depends on, both of them checked by
`tests/test_libvirt_rig.py` in its own right:

* `Rocky-9-GenericCloud-Base.latest.x86_64.qcow2` is a volume in pool `images`,
  10 GiB virtual, and is attached here rather than uploaded -- the local
  `source_qcow2` is a header-only stand-in truncated to the rig copy's size, so
  `preflight.base_volume` reports `create: False`.
* `192.168.122.70` is free, and is nobody else's: the shared `CONFIG` uses
  `.60`/`.61`, the suite is shuffled, and this deployment name is its own, so
  the address, the MAC and the marker collide with no preflight test.
"""

from __future__ import annotations

import copy
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from orchestrator import cli
from orchestrator.backends.libvirt import preflight
from orchestrator.cloudinit import mac_of
from tests.conftest import CONFIG
from tests.test_libvirt_rig import BASE_ON_RIG, RIG, needs_rig
from tests.test_qcow2 import make_qcow2

pytestmark = needs_rig

#: Its own deployment name, so the derived MAC and the marker are its own too.
DEPLOYMENT = "boot"
ADDRESS = "192.168.122.70"
GATEWAY = "192.168.122.1"
#: The cloud-init default user of the rig's Rocky 9 image.
GUEST_USER = "rocky"

#: `disk_gb` clears the 10 GiB virtual size of the base image it overlays.
VM: dict = {
    "name": "probe",
    "vcpus": 2,
    "memory_mib": 2048,
    "disk_gb": 20,
    "nics": [
        {
            "network": "default",
            "ip_cidr": f"{ADDRESS}/24",
            "gateway": GATEWAY,
            "nameservers": [GATEWAY],
        }
    ],
}

#: `cloud-init status --wait` can exit non-zero on a recoverable error, and its
#: exit code is not what is being read here -- the guest answering SSH at all is
#: the readiness signal, and the two `ip` commands are what the tests assert on.
PROBE = "cloud-init status --wait >/dev/null 2>&1; ip -o link; echo ==; ip -o -4 addr"

BOOT_TIMEOUT = 180.0


def _ssh(key: Path) -> list[str]:
    """A one-shot login to a guest that has never been seen before.

    Host-key checking is off and the known-hosts file is `/dev/null` because the
    guest is created and destroyed inside this fixture: there is no prior key to
    trust and nothing to remember afterwards.
    """
    return [
        "ssh",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "LogLevel=ERROR",
        f"{GUEST_USER}@{ADDRESS}",
        PROBE,
    ]


def _parse(out: str) -> dict:
    """`ip -o link` and `ip -o -4 addr`, split on the `==` the probe prints.

    `ip -o` puts one interface per line, so both halves are field splits: the
    name is the second field with any `@parent` suffix and the trailing colon
    removed, the MAC follows `link/ether`, and the address follows `inet`.
    """
    links, _, addrs = out.partition("\n==\n")
    mac_by_name: dict[str, str] = {}
    for line in links.splitlines():
        fields = line.split()
        if len(fields) > 1:
            name = fields[1].rstrip(":").split("@")[0]
            if "link/ether" in fields:
                mac_by_name[name] = fields[fields.index("link/ether") + 1].lower()
    addr_by_name: dict[str, list[str]] = {}
    for line in addrs.splitlines():
        fields = line.split()
        if "inet" in fields and len(fields) > 1:
            name = fields[1].rstrip(":").split("@")[0]
            addr_by_name.setdefault(name, []).append(fields[fields.index("inet") + 1])
    return {"macs": mac_by_name, "addrs": addr_by_name, "raw": out}


@pytest.fixture(scope="module")
def guest(tmp_path_factory):
    """Deploy one VM, wait for it to answer, hand over what it reports.

    Destroy runs in `finally` so a guest that never answers is still torn down;
    it is asserted on so a destroy that fails is a failure and not a leftover
    the next run trips over.
    """
    assert RIG is not None  # every test here is behind needs_rig
    tmp = tmp_path_factory.mktemp("boot")

    key = tmp / "id"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    pubkey = (tmp / "id.pub").read_text().strip()

    cfg = copy.deepcopy(CONFIG)
    cfg["deployment"] = DEPLOYMENT
    cfg["target"]["libvirt"]["uri"] = RIG
    # The rig login comes from ~/.ssh/config, as in `tests/test_libvirt_rig.py`.
    cfg["target"]["libvirt"].pop("ssh_key", None)
    cfg["target"]["libvirt"].pop("known_hosts", None)
    vm = copy.deepcopy(VM)
    vm["user_data"] = f"#cloud-config\nssh_authorized_keys:\n  - {pubkey}\n"
    cfg["vms"] = [vm]
    cfg["image"]["base_volume_name"] = BASE_ON_RIG
    cfg["image"]["source_qcow2"] = str(_stand_in(cfg, tmp))

    path = tmp / f"{DEPLOYMENT}.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))

    assert cli.main(["deploy", str(path), "--run-dir", str(tmp / "deploy")]) == 0
    try:
        yield _parse(_wait_for(key))
    finally:
        assert (
            cli.main(["destroy", str(path), "--yes", "--run-dir", str(tmp / "destroy")])
            == 0
        )


def _stand_in(cfg: dict, tmp: Path) -> Path:
    """A local image the size of the rig's copy, so nothing is uploaded.

    A bare sparse file is refused before deploy gets anywhere: `validate` runs
    `imagecheck.check_disk_capacity`, which raises on the zero magic. A
    header-only qcow2 carrying the base image's virtual size, truncated to its
    physical size, passes both that and `preflight.base_volume`'s size
    comparison -- so `create` is False and the deploy attaches the volume that
    is already there.
    """
    with preflight.connect(cfg) as conn:
        pool, problems = preflight.open_pool(conn, cfg["target"]["libvirt"]["pool"])
        assert pool is not None, problems
        volumes, _ = preflight.walk(pool)
    golden = make_qcow2(tmp / "golden.qcow2", 10 * 2**30)
    with open(golden, "r+b") as handle:
        handle.truncate(volumes[BASE_ON_RIG]["physical"])
    return golden


def _wait_for(key: Path) -> str:
    """Poll until the guest answers, or say what the last attempt reported."""
    command = _ssh(key)
    deadline = time.monotonic() + BOOT_TIMEOUT
    while True:
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode == 0:
            # The run's own record of what the guest reported.
            print(done.stdout)
            return done.stdout
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{ADDRESS} did not answer within {BOOT_TIMEOUT:.0f}s: "
                f"ssh exited {done.returncode}, stderr {done.stderr.strip()!r}"
            )
        time.sleep(5)


def named(guest: dict) -> str:
    """The device carrying the MAC vcows derived for this VM's only NIC."""
    mac = mac_of(VM, 0, DEPLOYMENT).lower()
    matched = [name for name, seen in guest["macs"].items() if seen == mac]
    assert len(matched) == 1, f"{mac} is on {matched}, of {sorted(guest['macs'])}"
    return matched[0]


def test_the_mac_matched_device_holds_the_configured_address(guest):
    """Defect 5's shape: a guest that boots healthy on an address nobody asked
    for reports `cloud-init status: done` and is caught by nothing else."""
    device = named(guest)
    assert f"{ADDRESS}/24" in guest["addrs"].get(device, [])


def test_no_device_is_named_for_its_network_config_key(guest):
    """The README claimed cloud-init renames each interface to its key. It does
    not: a v2 ethernet is renamed only when it carries `set-name`, which vcows
    deliberately does not write. This fails if that ever changes."""
    assert "nic0" not in guest["macs"], guest["raw"]
