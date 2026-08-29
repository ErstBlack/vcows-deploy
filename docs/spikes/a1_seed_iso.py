#!/usr/bin/env python3
"""A1 -- seed ISO: pycdlib vs xorrisofs.

Build the same NoCloud cidata ISO both ways from identical inputs and compare.
"""
import hashlib
import io
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).parent
WORK = HERE / "a1work"

USER_DATA = b"""#cloud-config
hostname: probe01
users:
  - name: vcows
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI0000000000000000000000000000000000000 probe
"""

META_DATA = b"""instance-id: probe01
local-hostname: probe01
"""


def build_xorrisofs(out: pathlib.Path, src: pathlib.Path) -> None:
    subprocess.run(
        ["xorrisofs", "-quiet", "-o", str(out), "-V", "cidata", "-J", "-r",
         str(src / "user-data"), str(src / "meta-data")],
        check=True,
    )


def build_pycdlib(out: pathlib.Path) -> None:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="cidata")
    for name, blob in (("user-data", USER_DATA), ("meta-data", META_DATA)):
        iso.add_fp(
            io.BytesIO(blob),
            len(blob),
            f"/{name.upper().replace('-', '_')}.;1",
            rr_name=name,
            joliet_path=f"/{name}",
        )
    iso.write(str(out))
    iso.close()


def describe(iso: pathlib.Path) -> bool:
    """Inspect an ISO with the *other* toolchain than the one that built it."""
    import pycdlib

    print(f"\n--- {iso.name} ---")
    print(f"size: {iso.stat().st_size} bytes  sha256: "
          f"{hashlib.sha256(iso.read_bytes()).hexdigest()[:16]}")

    # volume id straight out of the primary volume descriptor (32KiB + 40)
    pvd = iso.read_bytes()[32768:32768 + 190]
    vol = pvd[40:72].decode("ascii", "replace").strip()
    print(f"volume id (PVD): {vol!r}  == 'cidata': {vol == 'cidata'}")

    # xorriso's view of the tree
    out = subprocess.run(["xorriso", "-indev", str(iso), "-find", "/"],
                         capture_output=True, text=True).stdout
    print(f"xorriso -find: {[ln for ln in out.splitlines() if ln.startswith(chr(39))]}")

    # pycdlib's view, reading content back through both Joliet and Rock Ridge
    ok = vol == "cidata"
    r = pycdlib.PyCdlib()
    r.open(str(iso))
    for facade, kw in (("joliet", "joliet_path"), ("rock ridge", "rr_path")):
        for name, expect in (("user-data", USER_DATA), ("meta-data", META_DATA)):
            buf = io.BytesIO()
            try:
                r.get_file_from_iso_fp(buf, **{kw: f"/{name}"})
                match = buf.getvalue() == expect
            except Exception as exc:                      # noqa: BLE001
                match = False
                print(f"  [{facade}] {name}: FAILED -- {exc}")
                continue
            print(f"  [{facade}] {name}: {len(buf.getvalue())} bytes, "
                  f"matches input: {match}")
            ok &= match
    r.close()
    return ok


def main() -> int:
    WORK.mkdir(exist_ok=True)
    src = WORK / "src"
    src.mkdir(exist_ok=True)
    (src / "user-data").write_bytes(USER_DATA)
    (src / "meta-data").write_bytes(META_DATA)

    a = WORK / "seed-xorrisofs.iso"
    b = WORK / "seed-pycdlib.iso"
    for p in (a, b):
        p.unlink(missing_ok=True)

    build_xorrisofs(a, src)
    build_pycdlib(b)

    ok = describe(a) & describe(b)
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
