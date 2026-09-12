"""The backend seam: an ABC, the records that cross it, and the ownership policy.

Adding a second backend should require no edit to any core file. Every method on
``Backend`` is a signature rather than an implementation, bar ``prepare``.

docs/findings.md §3 is where the argument lives: why an ABC rather than a
Protocol, why ``prepare`` is the one method allowed a default, and the one core
block that keeps the "no core edit" claim from being complete --
``config.IMAGE_SCHEMA``, and ``orchestrator/qcow2.py`` behind it.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import cloudinit
from ..marker import Marker
from ..problems import Problem


@dataclass(frozen=True)
class Existing:
    """One VM that already exists on the target, as every backend reports it.

    Mechanism is per-backend, policy is core: docs/findings.md §3.
    """

    name: str
    """Hypervisor name. Not identity -- a renamed VM is still ours.

    ``decide()`` compares this against the config's *logical* name, which works
    only because the libvirt backend names a domain after the logical name. A
    backend that prefixes or namespaces -- ``lab-a-app01``, a vSphere folder path
    -- must return the transformed form here, and must know that the name-clash
    refusal is then comparing two different things and will never fire. It is the
    one core safety check whose mechanism is not backend-neutral.
    """

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

    Core reads ``vms`` and ``problems`` and forwards the record without ever
    reading ``artifacts``. ``prepare`` receives this rather than a session, so a
    backend cannot reach the hypervisor from ``prepare`` at all. docs/findings.md
    §3 argues both.
    """

    vms: tuple[Existing, ...]
    """What ``decide()`` consumes."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    """Opaque to core. Whatever else the backend had to look at while connected."""

    problems: tuple[Problem, ...] = ()
    """What the backend found wrong with the *target*, as opposed to the config.

    A missing pool, an orphaned volume, a base image whose size disagrees with
    the local one. None of these is an ownership question, so ``decide()`` cannot
    reach them, and every one of them must stop a deploy. Reported all at once
    rather than raised one at a time, and a tuple rather than a list, for the
    reasons docs/findings.md §3 gives.
    """


@dataclass
class Outcome:
    """What a teardown actually did, per object. The point of the exercise.

    Five domains with three objects each is twenty things that can fail
    independently, so both lists hold *objects* -- domain names and volume paths
    together -- rather than VMs. Silent partial success is the specific defect
    findings.md §1 names, and a backend that returns this without its consumer
    reading it reproduces that defect exactly.

    The one mutable record here, and deliberately: it is accumulated across a
    teardown that is expected to fail in places. Its consumer treats it as
    finished.
    """

    destroyed: list[str] = field(default_factory=list)
    """Objects that are gone because this run removed them."""

    skipped: list[str] = field(default_factory=list)
    """Objects this run did not remove. Not an error, and not nothing either.

    A domain already gone is not a crash-window resume -- an undefined domain is
    in no ``listAllDomains``, so preflight yields no target for it at all. It is a
    domain that vanished between preflight and teardown, whose disks are still
    worth collecting (``libvirt/destroy.py``'s vanished branch). A volume that
    would not resolve is a leak. Neither carries a fatal ``Problem`` -- a skip
    never stops a teardown, so the rest of the targets are still attempted -- and
    both make the exit code non-zero, because something vcows was asked to remove
    is still there.
    """

    problems: list[Problem] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(p.fatal for p in self.problems)


@contextmanager
def carrying(**attrs: Any) -> Iterator[None]:
    """Attach ``attrs`` to whatever exception leaves this block, then re-raise.

    Both backends accumulate their result in a local -- ``create``'s inventory
    dict, ``destroy``'s ``Outcome`` -- which only the return carries out. Without
    this, a failure on the third VM loses every record of the two that are
    running, and an interrupt mid-teardown loses the account of what had already
    been removed, which an operator cannot re-derive.

    ``BaseException`` because a Ctrl-C is exactly the case this exists for, and
    an attribute on the exception rather than a backend exception type because
    ``cli._deploy`` and ``cli._destroy`` read it back with ``getattr``. Core
    never imports a backend class -- which is what findings.md section 3 buys by
    giving the backends no exception hierarchy, and what this helper has to keep:
    nothing here knows libvirt or proxmoxer, or either one's error type.
    """
    try:
        yield
    except BaseException as exc:
        for name, value in attrs.items():
            setattr(exc, name, value)
        raise


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
    """The VM this decision is *about*, when there is one. ``None`` for a create.

    The machine-readable half of ``reason``. Every branch that sets this also
    names the VM in prose, but only two of them name its id there, so for a SKIP
    and for the refusal of a name held by another deployment this field is the
    only place the hypervisor UUID appears. ``cli._record`` reads it into
    ``run.json``, which spares a consumer regexing it out of ``reason``.
    """


def decide(
    wanted: Sequence[str],
    existing: Sequence[Existing],
    deployment: str,
) -> tuple[list[Decision], list[Problem]]:
    """Apply the ownership rules. **This is the dangerous logic, written once.**

    Checked in order:

    * More than one marked VM carrying one logical name -> **ERROR**, and a
      **refusal** if we wanted that name. Ambiguous ownership must not be
      resolved by enumeration order, and a dict keyed on the marker resolves it
      exactly that way: the earlier holder disappears from every rule below and
      from the report. On libvirt this needs ``virt-clone``, which copies
      ``<metadata>``; on vSphere and Proxmox cloning is the normal idiom and the
      annotation travels by default.
    * A marked VM carrying a logical name we want, from this deployment ->
      **skip**, reported as "exists (not compared)". No half-comparator; §2 of
      findings.md says why not.
    * The same, but from a *different* deployment -> **refuse**. Someone else
      owns that name here.
    * Any VM whose hypervisor name we want -> **refuse**. The check does not
      decide ownership; it buys the clear message *before* the apply writes that
      VM's overlay and seed ISO, which is what checking here rather than letting
      the hypervisor reject the name adds.
    * Otherwise -> **create**.

    Marked VMs *not* in the config are reported and left alone. Consistent with
    never-converge: removing a VM from the config does not delete it.
    """
    holders: dict[str, list[Existing]] = {}
    for e in existing:
        if e.marker is not None:
            holders.setdefault(e.marker.name, []).append(e)

    # Every existing VM, not only the unmarked ones. A marked domain whose
    # *hypervisor* name is one we want, under some other logical name, is in
    # neither lookup otherwise and falls through to CREATE; the collision then
    # surfaces inside `create` at define time, after that VM's overlay volume
    # and seed ISO have been written -- findings.md §2's orphan-volume path. A
    # hypervisor that allows two VMs to share a name would collapse here, and
    # libvirt is not one.
    by_hv_name: dict[str, Existing] = {e.name: e for e in existing}

    decisions: list[Decision] = []
    problems: list[Problem] = []

    for logical, held in sorted(holders.items()):
        if len(held) > 1:
            problems.append(
                Problem.error(
                    f"{len(held)} VMs carry the marker for logical name "
                    f"{logical!r}: {_named(held)}. vcows cannot tell which one it "
                    f"owns, and will not decide it by enumeration order.",
                    where=logical,
                )
            )

    for name in wanted:
        held = holders.get(name, [])
        if len(held) > 1:
            decisions.append(
                Decision(
                    name,
                    Action.REFUSE,
                    f"{len(held)} VMs carry this marker: {_named(held)}",
                )
            )
            continue

        ours = held[0] if held else None
        if ours is not None:
            assert ours.marker is not None  # noqa: S101  by construction of `holders`
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

        clash = by_hv_name.get(name)
        if clash is not None:
            if clash.marker is None:
                reason = (
                    f"an unmarked VM named {name!r} already exists (id {clash.id}); "
                    f"vcows will not adopt or overwrite it"
                )
            else:
                # Ours, and not adoptable either: the marker says it is a
                # different logical VM, so this one still needs its own domain
                # and the hypervisor has no name left to give it.
                reason = (
                    f"a VM named {name!r} already exists (id {clash.id}); it is "
                    f"ours, but as logical name {clash.marker.name!r} in deployment "
                    f"{clash.marker.deployment or '<unset>'!r}, so creating this one "
                    f"would collide on the hypervisor name"
                )
            decisions.append(Decision(name, Action.REFUSE, reason, clash))
            continue

        decisions.append(Decision(name, Action.CREATE, "does not exist"))

    wanted_set = set(wanted)
    for e in existing:
        if e.marker is not None and e.marker.name not in wanted_set:
            problems.append(
                Problem.warning(
                    f"marked VM {e.marker.name!r} exists but is not in this config; "
                    f"leaving it alone. Removing a VM from the config does not "
                    f"delete it -- that needs a deliberate destroy.",
                    where=e.name,
                )
            )

    return decisions, problems


def _named(vms: list[Existing]) -> str:
    """Hypervisor name and id for each, so an operator can go and look."""
    return ", ".join(f"{e.name!r} (id {e.id})" for e in sorted(vms, key=lambda e: e.id))


class Backend(ABC):
    """One backend is one package, and the class the registry holds is its only
    entry point. Every method below is a signature core calls in a fixed order:
    ``validate`` offline, then ``connect`` around ``preflight``, then ``prepare``
    from what preflight found, then ``create`` against a session again."""

    @abstractmethod
    def config_schema(self) -> dict:
        """The ``target.<name>`` sub-schema, as jsonschema."""

    @abstractmethod
    def validate(self, cfg: dict, *, verify_digest: bool = True) -> list[Problem]:
        """Offline checks. No connection, no I/O against the target.

        ``verify_digest`` false means "skip the one check that reads the golden
        image": ``imagecheck.check_image_digest`` hashes the whole file, ~59 s
        for 10 GiB, and ``destroy`` never touches it. Every other verb leaves it
        true. A backend that reads the image in some other check owes it the same
        skip.
        """

    @abstractmethod
    def connect(self, cfg: dict) -> AbstractContextManager[Any]:
        """Open a session against the target, and close it on the way out.

        The backend owns it, and the session stays opaque to everything above.
        Not in findings.md §3's interface, which takes ``session`` as a parameter
        without saying who builds it.
        """

    @abstractmethod
    def preflight(self, cfg: dict, session: Any) -> Discovered:
        """What exists on the target. Mechanism is per-backend; policy is core.

        **The only place a backend reads the target during a deploy.** This is
        the one method holding a live session, so everything the pure half of
        the pipeline needs to know about the world has to be learned here and
        carried out in ``Discovered``.
        """

    def prepare(
        self, cfg: dict, workdir: Path, discovered: Discovered
    ) -> dict[str, Any]:
        """Build whatever ``create`` needs, under ``workdir``, and record it.

        The dict it returns is opaque to core, which carries it from this call to
        ``create`` and reads nothing in it. What both shipped backends need is
        the seed ISOs, written into the run directory and kept there -- nothing
        tears them down -- so a VM that will not boot can be debugged from the
        media it was actually given, plus whatever preflight had to look up while
        connected: libvirt's ``base_volume``, Proxmox's ``image``. The artifacts
        are forwarded whole rather than picked out by key, since picking would
        mean core naming ``base_volume``.

        **The one concrete method here**, and the only one allowed a default:
        docs/findings.md §3 sets the bar and says why nothing else clears it.
        Being written here rather than in each backend is what makes "reaches
        nothing" structural -- there is no session in this scope to reach with.
        """
        return {"seed_isos": cloudinit.build_all(cfg, workdir), **discovered.artifacts}

    @abstractmethod
    def create(self, cfg: dict, session: Any, prepared: dict[str, Any]) -> dict:
        """Create every VM in ``cfg`` and return the inventory map.

        ``session`` is what ``connect`` yielded. The result is keyed by logical VM
        name; the per-VM record is backend-specific but always carries ``name``
        and ``configured_address``. A failure raises with the resource named and
        rolls nothing back; ``preflight`` sees the leftovers on the next run.
        """

    @abstractmethod
    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
        """Tear down the set preflight discovered, and say what happened.

        Returning the record rather than ``None`` is what stops a partial teardown
        from reading as a success. A backend is free to raise as well -- and the
        libvirt one does, for anything fatal -- but everything it could not do
        must be in here whether it raises or not.
        """
