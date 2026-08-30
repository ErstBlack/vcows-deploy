#!/usr/bin/env python3
"""A2 -- marker round trip.

Define a domain carrying the vcows marker, read it back three ways, and confirm
the JSON payload survives byte-identical and un-reindented.
"""
import json
import sys
import uuid
import xml.etree.ElementTree as ET

import libvirt

URI = ("qemu+ssh://vcows@vcows/system"
       "?keyfile=/home/ssullivan/.ssh/id_ed25519-vcows"
       "&known_hosts=/home/ssullivan/.ssh/known_hosts")
NS = "https://example.invalid/vcows"
VCOWS_NS = uuid.UUID("43a00ff6-89be-57a1-8596-246f665e9f4b")
DOM = "vcows-spike-probe01"

PAYLOAD = json.dumps(
    {"v": "0.1.0.0", "deployment": "spike", "name": "probe01",
     "id": str(uuid.uuid5(VCOWS_NS, "probe01"))},
    separators=(",", ":"),
)

DOMAIN_XML = f"""<domain type='kvm'>
  <name>{DOM}</name>
  <uuid>{uuid.uuid5(VCOWS_NS, 'probe01')}</uuid>
  <memory unit='MiB'>512</memory>
  <vcpu>1</vcpu>
  <os><type arch='x86_64' machine='q35'>hvm</type></os>
  <metadata>
    <vcows xmlns="{NS}">{PAYLOAD}</vcows>
  </metadata>
  <devices/>
</domain>
"""


def main():
    libvirt.registerErrorHandler(lambda ctx, err: None, None)
    conn = libvirt.open(URI)
    print(f"daemon libvirt: {conn.getLibVersion()}\n")
    print(f"payload sent ({len(PAYLOAD)} bytes):\n  {PAYLOAD}\n")

    # clean any leftover from a previous run
    try:
        conn.lookupByName(DOM).undefine()
    except libvirt.libvirtError:
        pass

    dom = conn.defineXML(DOMAIN_XML)
    ok = True

    # --- read 1: dom.metadata() -------------------------------------------
    got = dom.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT, NS, 0)
    # metadata() returns the serialized element; extract its text content
    text1 = ET.fromstring(got).text
    print(f"[1] dom.metadata() raw   : {got!r}")
    print(f"    text content         : {text1!r}")
    print(f"    byte-identical       : {text1 == PAYLOAD}")
    ok &= text1 == PAYLOAD

    # --- read 2: XMLDesc(INACTIVE) + ElementTree ---------------------------
    xml = dom.XMLDesc(libvirt.VIR_DOMAIN_XML_INACTIVE)
    node = ET.fromstring(xml).find(f"metadata/{{{NS}}}vcows")
    text2 = node.text if node is not None else None
    print(f"\n[2] XMLDesc parse        : {text2!r}")
    print(f"    byte-identical       : {text2 == PAYLOAD}")
    ok &= text2 == PAYLOAD

    # --- read 3: raw substring in the dumped XML ---------------------------
    print(f"\n[3] payload appears verbatim in XMLDesc: {PAYLOAD in xml}")
    ok &= PAYLOAD in xml
    for line in xml.splitlines():
        if "vcows" in line:
            print(f"    as emitted: {line!r}")

    # --- JSON still parses, unknown keys tolerated -------------------------
    parsed = json.loads(text2)
    print(f"\n[4] json.loads round trip: {parsed}")
    ok &= parsed["id"] == str(uuid.uuid5(VCOWS_NS, "probe01"))

    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    print(f"\nDomain '{DOM}' left defined for the restart check.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
