"""The backend seam: an ABC, the records that cross it, and the ownership policy.

Adding a second backend should require no edit to any core file. Every method on
``Backend`` is a signature, not an implementation.

**One core block is the exception, and it is known.** ``config.IMAGE_SCHEMA`` is
written in qcow2-and-libvirt terms -- ``source_qcow2`` and ``base_volume_name``
are the field *names*, not just their meanings -- and unlike ``target`` it is
wired into the core schema directly rather than composed from the registry. A
vSphere or Proxmox backend wanting an OVA or a template id opens ``config.py``.
The reader behind it, ``orchestrator/qcow2.py``, is core too and is imported by
exactly one backend. Neither is speculative to fix and both are cheap to move
when there is a second backend to move them for; they are named here so the "no
core edit" claim is not read as complete.

**No default implementations, deliberately.** The thing to avoid is *noop
defaults*, not ABCs: a backend that forgets ``destroy`` and inherits a no-op
deletes nothing and exits successfully; one that forgets ``preflight`` skips the
safety check entirely. An ABC fails loudly at instantiation, which beats a
Protocol that only complains if someone remembers to run a type checker.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..marker import Marker
from ..problems import Problem


@dataclass(frozen=True)
class Existing:
    """One VM that already exists on the target, as every backend reports it.

    libvirt reads domain ``<metadata>``; vSphere would read an annotation;
    Proxmox a description. All three produce this, and core decides what it
    means -- which is why a backend author cannot implement the refusal
    incorrectly. They never implement it.
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

    It exists because ``prepare`` cannot reach the target while ``create`` only
    ever creates, so something has to say which of the things ``create`` would
    make are already there. For libvirt that is the shared golden image, and
    preflight is *already* walking the pool to satisfy findings.md §2's
    orphan-volume refusal, so the answer is a lookup on data it is holding
    rather than a second round trip.

    Core reads ``vms`` and forwards the record without ever reading
    ``artifacts``, which is what keeps core from learning what a storage volume
    is. And because ``prepare`` takes *this* rather than a session, a backend
    cannot reach the hypervisor from ``prepare`` at all -- a guarantee rather
    than a rule someone has to remember.
    """

    vms: tuple[Existing, ...]
    """What ``decide()`` consumes."""

    artifacts: dict[str, Any] = field(default_factory=dict)
    """Opaque to core. Whatever else the backend had to look at while connected."""

    problems: tuple[Problem, ...] = ()
    """What the backend found wrong with the *target*, as opposed to the config.

    A missing pool, an orphaned volume, a base image whose size disagrees with the
    local one: none of these are ownership questions, so ``decide()`` cannot reach
    them, and all of them must stop a deploy. They are a list rather than an
    exception for the same reason ``config.load`` reports every problem at once --
    an operator at an air-gapped site should not round-trip once per fault.

    Core reads this and ``vms``, and still never reads ``artifacts``.

    A tuple, like ``Existing.disks``: ``frozen=True`` blocks rebinding only, so a
    list field leaves ``d.problems.append(...)`` working on the one record
    documented as crossing from the connected half of the pipeline into the pure
    half. ``artifacts`` stays a dict because it is genuinely opaque.
    """


@dataclass(frozen=True)
class Prepared:
    """Whatever ``prepare`` produced for ``create`` to consume.

    Opaque to core, which carries it from one call to the next and reads
    nothing in it. For both shipped backends it holds the seed ISOs and the one
    fact preflight had to look up while connected.
    """

    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class Outcome:
    """What a teardown actually did, per object. The point of the exercise.

    Five domains with three objects each is twenty things that can fail
    independently, so both lists hold *objects* -- domain names and volume paths
    together -- rather than VMs. Silent partial success is the specific defect
    findings.md §1 rejects ``tofu destroy`` for, and a backend that returns this
    without its consumer reading it reproduces that defect exactly.

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
    names the VM in prose, but only two of them name its id there -- so for a
    SKIP, and for the refusal of a name held by another deployment, the
    hypervisor UUID was in this field and in no artifact the site ships back.
    ``cli._record`` reads it into ``run.json``; a consumer that had to regex it
    out of ``reason`` was the alternative.
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
      **skip**, reported as "exists (not compared)". No half-comparator: libvirt
      rewrites domain XML on define -- adding defaults, PCI addresses, device
      aliases -- so a naive diff produces permanent false drift, and the natural
      fix is a normalisation layer. That is precisely how the last version
      sprawled.
    * The same, but from a *different* deployment -> **refuse**. Someone else
      owns that name here.
    * Any VM whose hypervisor name we want -> **refuse**. libvirt would reject
      the duplicate itself; checking here buys a clear message instead of a raw
      libvirt error, and buys it *before* the apply writes that VM's overlay and
      seed ISO. The check does not decide ownership.
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
    # surfaces inside `tofu apply` at define time, after that VM's overlay volume
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
    def prepare(self, cfg: dict, workdir: Path, discovered: Discovered) -> Prepared:
        """Build whatever ``create`` needs, under ``workdir``, and record it.

        For both shipped backends that is the seed ISOs, written into the run
        directory and kept there so a VM that will not boot can be debugged from
        the media it was actually given.

        **Takes what ``preflight`` found, not a connection.** It needs the
        target's state -- which of the things ``create`` would make already
        exist -- but not the ability to go and look, which ``preflight`` has
        already done. Passing data rather than a session also makes "prepare
        runs after preflight" a type dependency instead of a convention.
        """

    @abstractmethod
    def create(self, cfg: dict, session: Any, prepared: Prepared) -> dict:
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
