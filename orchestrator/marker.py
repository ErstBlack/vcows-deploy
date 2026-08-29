"""The ownership marker -- the durable record of what vcows created.

The state file is a convenience; this is the truth. Discovery enumerates by
marker and that set is authoritative, so a renamed VM is still ours and still
destroyable. A VM whose marker was hand-edited is user error and out of scope.

Core owns the marker's *content* and serialisation. A backend owns only where it
is stored and how it is read back. That split is why the dangerous ownership
logic is written exactly once.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from . import VERSION

#: Namespace for deterministic VM identities.
#:
#: **This value is permanent and must never change.** ``id = uuid5(VCOWS_NS,
#: f"{deployment}/{name}")`` exists so an id regenerates identically with no
#: state file -- a VM created by 0.1.0.0 and destroyed by 0.3.0.0 must derive the
#: same id from the same deployment and logical name. Deriving this from the
#: version, or regenerating it, would leave every release unable to identify its
#: predecessors' VMs.
#:
#: It is not arbitrary: it is ``uuid5(uuid.NAMESPACE_DNS, "vcows-deploy")``, so
#: anyone can re-derive it and confirm. ``tests/test_marker.py`` pins it.
VCOWS_NS = uuid.UUID("43a00ff6-89be-57a1-8596-246f665e9f4b")

#: XML namespace for the libvirt ``<metadata>`` child element.
#:
#: Stamped into every VM's persistent domain XML, so changing it makes markers
#: written by earlier versions unreadable. A URN rather than an http URL because
#: XML namespaces are identifiers, not addresses: there is no domain to own and
#: no question about whether it should resolve. The trailing digit leaves a clean
#: break if the marker format ever needs an incompatible one.
MARKER_XMLNS = "urn:vcows:1"

#: Element name inside ``<metadata>``.
MARKER_ELEMENT = "vcows"


class MarkerError(ValueError):
    """A marker was present but could not be understood."""


@dataclass(frozen=True)
class Marker:
    """One canonical payload, one serializer, one parser, every backend."""

    name: str
    """Logical name from the config, not the hypervisor name. Survives a rename."""

    deployment: str
    """Which deployment stamped this VM.

    Recorded from 0.1.0.0 so the data exists before any VM is marked. v0.1
    destroy scope stays host-wide, so nothing reads this for a destroy decision
    yet -- but a later release can filter on it with no marker migration and no
    "what does absent mean" ambiguity.
    """

    id: str
    """Stable machine identity, ``uuid5(VCOWS_NS, f"{deployment}/{name}")``."""

    v: str = VERSION
    """vcows version that created it. Also the format discriminator."""

    @classmethod
    def for_vm(cls, name: str, deployment: str) -> Marker:
        return cls(name=name, deployment=deployment, id=derive_id(name, deployment))

    def to_json(self) -> str:
        """Compact and key-ordered, so it is stable across runs and diffable."""
        return json.dumps(
            {
                "v": self.v,
                "deployment": self.deployment,
                "name": self.name,
                "id": self.id,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> Marker:
        """Parse a payload, **ignoring unknown keys**.

        Tolerating unknown keys from day one is what makes the format actually
        extensible rather than theoretically extensible: a later version adding a
        field must not make its VMs unreadable by this one.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MarkerError(f"marker is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise MarkerError(f"marker is {type(data).__name__}, expected object")
        missing = {"v", "name", "id"} - data.keys()
        if missing:
            raise MarkerError(f"marker missing required key(s): {sorted(missing)}")
        return cls(
            v=_text(data, "v"),
            name=_text(data, "name"),
            # Absent in markers written before `deployment` existed. Empty
            # string, never None, so callers need no null check.
            deployment=_text(data, "deployment", ""),
            id=_text(data, "id"),
        )

    def to_xml(self) -> str:
        """The libvirt form: one namespaced element whose text is the payload.

        libvirt requires ``<metadata>`` to have at least one *element* child, and
        deep-copies the subtree into the persistent domain XML. Verified to
        survive define -> dumpxml byte-identical and un-reindented; see
        docs/spikes.md A2.
        """
        return (
            f'<{MARKER_ELEMENT} xmlns="{MARKER_XMLNS}">'
            f"{self.to_json()}</{MARKER_ELEMENT}>"
        )


def _text(data: dict, key: str, default: str | None = None) -> str:
    """One string field, or a refusal. Never a coercion.

    ``str()`` would turn ``{"name": 123}`` into ``"123"`` and ``{"id": null}``
    into ``"None"``, so a marker that should be reported as unreadable instead
    names a VM -- and that name is what ``decide()`` compares against the config.
    A marker this malformed was not written by vcows.
    """
    if key not in data and default is not None:
        return default
    value = data[key]
    if not isinstance(value, str):
        raise MarkerError(
            f"marker key {key!r} is {type(value).__name__}, expected string"
        )
    return value


def derive_id(name: str, deployment: str) -> str:
    """Deterministic identity, so it regenerates identically with no state file.

    The deployment is in the input because this is also the seed ISO's
    ``instance-id``: without it, two deployments each containing ``app01``
    hand their guests the same identity from byte-identical media.
    """
    return str(uuid.uuid5(VCOWS_NS, f"{deployment}/{name}"))
