"""The backend seam: an ABC, the records that cross it, and the ownership policy.

Adding a second backend should require no edit to any core file. Every method on
``Backend`` is a signature, not an implementation.

**No default implementations, deliberately.** The thing to avoid is *noop
defaults*, not ABCs: a backend that forgets ``destroy`` and inherits a no-op
deletes nothing and exits successfully; one that forgets ``preflight`` skips the
safety check entirely. An ABC fails loudly at instantiation, which beats a
Protocol that only complains if someone remembers to run a type checker.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..marker import Marker


class Severity(enum.Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """Something wrong with a config, or with the world the config describes."""

    severity: Severity
    message: str
    where: str = ""

    @property
    def fatal(self) -> bool:
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity.value}{loc}: {self.message}"


@dataclass(frozen=True)
class Existing:
    """One VM that already exists on the target, as every backend reports it.

    libvirt reads domain ``<metadata>``; vSphere would read an annotation;
    Proxmox a description. All three produce this, and core decides what it
    means -- which is why a backend author cannot implement the refusal
    incorrectly. They never implement it.
    """

    name: str
    """Hypervisor name. Not identity -- a renamed VM is still ours."""

    id: str
    """UUID / moid / vmid."""

    marker: Marker | None
    """Parsed payload, or None if unmarked or unparseable."""

    disks: tuple[str, ...] = ()
    """Source paths of media attached to this VM, for teardown.

    Never includes a ``<backingStore>`` path: per-VM disks are overlays on the
    shared golden image, and following the backing chain would destroy the base
    volume every other deployment's overlays depend on.
    """


@dataclass(frozen=True)
class Discovered:
    """Everything one ``preflight`` walk found. The only thing that crosses from
    the connected half of the pipeline into the pure half.

    It exists because ``render`` is pure while each apply runs against a fresh,
    empty state -- so the module only ever creates, and something has to say
    which of the things it would create are already there. For libvirt that is
    the shared golden image, and preflight is *already* walking the pool to
    satisfy findings.md §2's orphan-volume refusal, so the answer is a lookup on
    data it is holding rather than a second round trip.

    Core reads ``vms`` and forwards the record without ever reading
    ``artifacts``, which is what keeps core from learning what a storage volume
    is. And because ``prepare`` takes *this* rather than a session, a backend
    cannot reach the hypervisor from ``prepare`` at all -- a guarantee rather
    than a rule someone has to remember.
    """

    vms: list[Existing]
    """What ``decide()`` consumes."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    """Opaque to core. Whatever else the backend had to look at while connected."""


@dataclass(frozen=True)
class Prepared:
    """Whatever ``prepare`` produced for ``render`` to consume."""

    workdir: Path
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Inventory:
    """The handoff contract. Minimal, and **documented as unstable**.

    Nothing consumes it at v0.1 -- the seam is free. Formalise the contract when
    something actually reads it, and add ``inventory_version`` at that point.
    """

    vms: dict[str, dict[str, Any]]


class Action(enum.Enum):
    CREATE = "create"
    SKIP = "skip"
    REFUSE = "refuse"


@dataclass(frozen=True)
class Decision:
    """What core decided to do about one configured VM, and why."""

    vm_name: str
    action: Action
    reason: str
    existing: Existing | None = None


def decide(
    wanted: list[str],
    existing: list[Existing],
    deployment: str,
) -> tuple[list[Decision], list[Problem]]:
    """Apply the ownership rules. **This is the dangerous logic, written once.**

    Checked in order:

    * A marked VM carrying a logical name we want, from this deployment ->
      **skip**, reported as "exists (not compared)". No half-comparator: libvirt
      rewrites domain XML on define -- adding defaults, PCI addresses, device
      aliases -- so a naive diff produces permanent false drift, and the natural
      fix is a normalisation layer. That is precisely how the last version
      sprawled.
    * The same, but from a *different* deployment -> **refuse**. Someone else
      owns that name here.
    * An unmarked VM whose hypervisor name we want -> **refuse**. libvirt would
      reject the duplicate itself; checking here buys a clear message instead of
      a raw libvirt error. The check does not decide ownership.
    * Otherwise -> **create**.

    Marked VMs *not* in the config are reported and left alone. Consistent with
    never-converge: removing a VM from the config does not delete it.
    """
    by_logical: dict[str, Existing] = {
        e.marker.name: e for e in existing if e.marker is not None
    }
    unmarked_by_name: dict[str, Existing] = {
        e.name: e for e in existing if e.marker is None
    }

    decisions: list[Decision] = []
    problems: list[Problem] = []

    for name in wanted:
        ours = by_logical.get(name)
        if ours is not None:
            assert ours.marker is not None  # by construction of by_logical
            if ours.marker.deployment == deployment:
                decisions.append(
                    Decision(
                        name,
                        Action.SKIP,
                        f"exists as {ours.name!r} (not compared)",
                        ours,
                    )
                )
            else:
                decisions.append(
                    Decision(
                        name,
                        Action.REFUSE,
                        f"exists as {ours.name!r} but belongs to deployment "
                        f"{ours.marker.deployment or '<unset>'!r}, not {deployment!r}",
                        ours,
                    )
                )
            continue

        clash = unmarked_by_name.get(name)
        if clash is not None:
            decisions.append(
                Decision(
                    name,
                    Action.REFUSE,
                    f"an unmarked VM named {name!r} already exists (id {clash.id}); "
                    f"vcows will not adopt or overwrite it",
                    clash,
                )
            )
            continue

        decisions.append(Decision(name, Action.CREATE, "does not exist"))

    wanted_set = set(wanted)
    for e in existing:
        if e.marker is not None and e.marker.name not in wanted_set:
            problems.append(
                Problem(
                    Severity.WARNING,
                    f"marked VM {e.marker.name!r} exists but is not in this config; "
                    f"leaving it alone. Removing a VM from the config does not "
                    f"delete it -- that needs a deliberate destroy.",
                    where=e.name,
                )
            )

    return decisions, problems


class Backend(ABC):
    """One backend is one package. Its tofu module lives at ``<pkg>/tofu/`` by
    convention, not by method."""

    name: str

    @abstractmethod
    def config_schema(self) -> dict:
        """The ``target.<name>`` sub-schema, as jsonschema."""

    @abstractmethod
    def validate(self, cfg: dict) -> list[Problem]:
        """Offline checks. No connection, no I/O against the target."""

    @abstractmethod
    def connect(self, cfg: dict) -> AbstractContextManager[Any]:
        """Open a session against the target, and close it on the way out.

        Not in findings.md §3's interface, which takes ``session`` as a parameter
        without saying who builds it. Somebody must, and if that is core then
        core imports libvirt and the seam is fake -- which is exactly what the
        fake-backend test exists to catch. So the backend owns it, and the
        session stays opaque to everything above.
        """

    @abstractmethod
    def preflight(self, cfg: dict, session: Any) -> Discovered:
        """What exists on the target. Mechanism is per-backend; policy is core.

        **The only place a backend reads the target during a deploy.** This is
        the one method holding a live session, so everything the pure half of
        the pipeline needs to know about the world has to be learned here and
        carried out in ``Discovered``.
        """

    @abstractmethod
    def prepare(
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> AbstractContextManager[Prepared]:
        """Build whatever the apply needs, and hold it open for the apply's life.

        A context manager because a future backend may need the orchestrator to
        serve the image over HTTP for the hypervisor to pull -- a listening
        socket held open for the duration and torn down after. For libvirt it
        yields immediately after building the seed ISOs. It costs nothing today;
        retrofitting it later would mean restructuring. Note that such a socket
        is one the backend opened itself, not the hypervisor session, which is
        closed by the time this runs.

        **Takes what ``preflight`` found, not a connection.** It needs the
        target's state -- which of the things the module would create already
        exist -- but not the ability to go and look, which ``preflight`` has
        already done. Passing data rather than a session also makes "prepare
        runs after preflight" a type dependency instead of a convention.
        """

    @abstractmethod
    def render(self, cfg: dict, prepared: Prepared) -> dict:
        """Pure: config plus prepare's record, out to a tfvars dict. No I/O."""

    @abstractmethod
    def parse_outputs(self, raw: dict) -> Inventory:
        """Raw ``tofu output -json`` to the inventory contract.

        Per-backend because each module declares its own outputs. Without this
        step the module's output block *is* the public API -- rename an output
        and every consumer of inventory.json breaks.
        """

    @abstractmethod
    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> None:
        """Tear down the set preflight discovered."""
