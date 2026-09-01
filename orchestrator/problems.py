"""``Problem``, and the jsonschema adapter that produces them.

**Core, and not under ``backends/``, for an import reason rather than a taste
one.** ``orchestrator/backends/__init__.py`` builds ``REGISTRY`` eagerly, so
importing anything under ``backends`` runs that file, which imports every backend
package. A core module that wanted ``Problem`` from ``backends.base`` therefore
pulled the whole registry in behind it -- and once a backend imported that core
module back, the cycle closed and the failure was a partially-initialised module
several hops from either edit.

``Problem`` was never a backend type. ``config.py``, ``cloudinit.py``,
``imagecheck.py`` and ``cli.py`` all produce or consume one, and only the
dataclasses in ``backends/base.py`` made it look otherwise.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class Severity(enum.Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """Something wrong with a config, or with the world the config describes."""

    severity: Severity
    message: str
    where: str = ""

    # ``Severity.ERROR`` alone on the first line is what wrapped nearly every
    # construction to three. The explicit three-argument form stays valid and
    # stays in use: ``config._blame_the_filename`` propagates an existing
    # severity rather than choosing one, so it cannot go through either of these.
    @classmethod
    def error(cls, message: str, where: str = "") -> Problem:
        return cls(Severity.ERROR, message, where)

    @classmethod
    def warning(cls, message: str, where: str = "") -> Problem:
        return cls(Severity.WARNING, message, where)

    @property
    def fatal(self) -> bool:
        return self.severity is Severity.ERROR

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.severity.value}{loc}: {self.message}"


def problems_from(errors: Iterable[Any], at: str = "", root: str = "") -> list[Problem]:
    """Every ``jsonschema`` validation error as a fatal Problem, ordered by path.

    ``err.json_path`` is the library's own rendering -- ``$.vms[0].nics[0].mac``
    -- so the ``$`` comes off and what is left is the location. ``at`` prefixes
    it, because the backend validates one VM at a time and is the only half that
    knows it is looking at ``vms[3]``; core validates the whole document and
    passes nothing. ``root`` is what an error against the document itself reads
    as, where ``json_path`` gives no path at all.

    The ``removeprefix`` is not cosmetic. Without it a top-level key renders as
    ``.deployment``, and ``config._blame_the_filename`` dispatches on ``where ==
    "deployment"`` exactly, so the filename would silently stop being blamed.
    """
    return [
        Problem.error(
            err.message, where=(at + err.json_path[1:]).removeprefix(".") or root
        )
        for err in sorted(errors, key=lambda e: list(map(str, e.absolute_path)))
    ]
