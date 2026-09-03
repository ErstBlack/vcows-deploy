"""An in-memory backend with no hypervisor semantics whatsoever. Shipped in nothing.

It earns its place by proving three things interface design alone cannot:

1. Core runs the whole pipeline -- validate, preflight, prepare, create,
   destroy -- **with no libvirt import anywhere in the call path.** That is the
   actual test of whether the seam is real.
2. The ownership policy holds against a backend with no libvirt semantics:
   absent -> create, ours -> skip, unmarked -> refuse.
3. Two backends register at once and the config schema composes.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orchestrator.backends.base import (
    Backend,
    Discovered,
    Existing,
    Outcome,
    Prepared,
)
from orchestrator.marker import Marker
from orchestrator.problems import Problem, Severity


class FakeSession:
    """Stands in for a hypervisor connection. Records that it was closed."""

    def __init__(self, world: list[Existing]):
        self.world = world
        self.closed = False
        self.destroyed: list[str] = []
        #: What `create` read out of the `Prepared` it was handed. A caller that
        #: builds its own instead of passing `prepare`'s is visible here.
        self.seed: str | None = None


class FakeBackend(Backend):
    def __init__(self, name: str = "fake", world: list[Existing] | None = None):
        #: The registry key, and the `target.<name>` block `validate` reads.
        self.name = name
        #: What "already exists on the target". Tests set this directly.
        self.world: list[Existing] = list(world or [])
        self.sessions: list[FakeSession] = []
        #: What `destroy` reports back. None means "everything that was asked".
        self.outcome: Outcome | None = None

    # -- offline ---------------------------------------------------------

    def config_schema(self) -> dict:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["endpoint"],
            "properties": {"endpoint": {"type": "string", "minLength": 1}},
        }

    def validate(self, cfg: dict) -> list[Problem]:
        target = cfg["target"][self.name]
        where = f"target.{self.name}.endpoint"
        if target["endpoint"].startswith("bad://"):
            return [Problem(Severity.ERROR, "endpoint scheme is not supported", where)]
        # A backend check that refuses nothing. Every verb computes these on the
        # way in and only `validate` used to get them back.
        if target["endpoint"].startswith("odd://"):
            return [
                Problem(Severity.WARNING, "endpoint scheme is unusual", where),
            ]
        return []

    # -- session ---------------------------------------------------------

    @contextmanager
    def connect(self, cfg: dict):
        session = FakeSession(self.world)
        self.sessions.append(session)
        try:
            yield session
        finally:
            session.closed = True

    def preflight(self, cfg: dict, session: Any) -> Discovered:
        return Discovered(
            vms=tuple(session.world),
            # Stands in for whatever else a backend has to look at while it is
            # connected -- for libvirt, whether the golden image is already in
            # the pool. Core never reads this.
            artifacts={"existing_names": sorted(e.name for e in session.world)},
        )

    # -- apply -----------------------------------------------------------

    def prepare(self, cfg: dict, workdir: Path, discovered: Discovered) -> Prepared:
        (workdir / "fake-artifact").write_text("seed\n")
        return Prepared(
            artifacts={
                "seed": str(workdir / "fake-artifact"),
                # Carried through from preflight, not looked up again.
                "existing_names": discovered.artifacts["existing_names"],
            },
        )

    def create(self, cfg: dict, session: Any, prepared: Prepared) -> dict:
        """Put every configured VM into the world, and report what went in.

        The real backends define a domain or clone a template here. This one
        appends to the list `preflight` reads, which is what makes a second
        deploy against the same fake see them and skip.
        """
        # A `Prepared` that is not `prepare`'s is a KeyError here rather than a
        # deploy that quietly creates VMs from media nothing built.
        session.seed = prepared.artifacts["seed"]
        for vm in cfg["vms"]:
            marker = Marker.for_vm(vm["name"], cfg["deployment"])
            session.world.append(Existing(name=vm["name"], id=marker.id, marker=marker))
        return {
            vm["name"]: {
                "name": vm["name"],
                # The two keys `Backend.create` promises in every record. The
                # fake has no networking, so this is what the config asked for
                # and empty when it asked for nothing.
                "configured_address": (vm.get("nics") or [{}])[0]
                .get("ip_cidr", "")
                .split("/")[0],
            }
            for vm in cfg["vms"]
        }

    def destroy(self, cfg: dict, session: Any, targets: list[Existing]) -> Outcome:
        for t in targets:
            session.destroyed.append(t.name)
            session.world = [e for e in session.world if e.name != t.name]
        # A backend with no hypervisor semantics tears everything down. Tests that
        # need a partial teardown -- the case the CLI has to report -- set
        # `self.outcome` and get it back verbatim.
        if self.outcome is not None:
            return self.outcome
        return Outcome(destroyed=[t.name for t in targets])
