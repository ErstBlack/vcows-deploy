"""An in-memory backend with no hypervisor semantics whatsoever. Shipped in nothing.

It earns its place by proving three things interface design alone cannot:

1. Core runs the whole pipeline -- validate, preflight, prepare, render, apply,
   outputs, destroy -- **with no libvirt import anywhere in the call path.** That
   is the actual test of whether the seam is real.
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
    Inventory,
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


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, name: str = "fake", world: list[Existing] | None = None):
        self.name = name
        #: What "already exists on the target". Tests set this directly.
        self.world: list[Existing] = list(world or [])
        self.sessions: list[FakeSession] = []
        self.prepared_dirs: list[Path] = []
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

    @contextmanager
    def prepare(self, cfg: dict, workdir: Path, discovered: Discovered):
        self.prepared_dirs.append(workdir)
        (workdir / "fake-artifact").write_text("seed\n")
        yield Prepared(
            workdir=workdir,
            artifacts={
                "seed": str(workdir / "fake-artifact"),
                # Carried through from preflight, not looked up again.
                "existing_names": discovered.artifacts["existing_names"],
            },
        )

    def render(self, cfg: dict, prepared: Prepared) -> dict:
        return {
            "endpoint": cfg["target"][self.name]["endpoint"],
            "seed": prepared.artifacts["seed"],
            "vms": {
                vm["name"]: {
                    "marker_xml": Marker.for_vm(vm["name"], cfg["deployment"]).to_xml(),
                }
                for vm in cfg["vms"]
            },
        }

    def parse_outputs(self, raw: dict) -> Inventory:
        if "vms" not in raw:
            raise ValueError("the tofu module declared no `vms` output")
        return Inventory(vms=raw["vms"].get("value", {}))

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
