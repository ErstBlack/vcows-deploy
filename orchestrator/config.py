"""Core config: load, compose the schema from the registry, validate.

Core carries ``backend`` (an enum built from the registry) and
``target: {<name>: <sub-schema from that backend>}``, wired together with a
generated ``if/then`` per registered backend. Adding a backend adds a schema file
inside its own package and touches nothing here.

**``vms`` stays loose on purpose.** Core requires only what it needs to drive the
pipeline -- a name. The per-VM shape (vcpus, disks, and especially NICs, whose
valid forms are entirely backend-specific) is validated by the backend. That
keeps this file backend-agnostic, and it produces better errors: a jsonschema
``oneOf`` failure is close to unreadable, where a backend check can say "exactly
one of bridge/network, found both".

There is no ``defaults`` block at v0.1 and so no resolution step. Every VM spells
out every field. Adding defaults later is backward-compatible -- configs that
spell everything out keep validating, and making a required field defaultable is
a relaxation -- so nothing is pre-built for it here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .backends.base import Problem, Severity

SCHEMA_VERSION = 1

#: Deployment names end up in markers and in operator-facing messages.
DEPLOYMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"

IMAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_qcow2", "base_volume_name"],
    "properties": {
        # Path inside the container, bind-mounted read-only.
        "source_qcow2": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}$"},
        # Deterministic and shared per host. Named after the image rather than
        # the deployment because the base volume is shared across deployments --
        # that sharing is the whole point of not re-pushing multi-GB images.
        "base_volume_name": {"type": "string", "minLength": 1},
    },
}


class ConfigError(Exception):
    """The config could not be loaded, or did not validate."""

    def __init__(self, problems: list[Problem]):
        self.problems = problems
        super().__init__("\n".join(str(p) for p in problems))


def core_schema(registry: dict[str, Any]) -> dict:
    """Compose the whole-document schema from the registered backends."""
    names = sorted(registry)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "backend", "target", "image", "vms"],
        "properties": {
            "schema_version": {"const": SCHEMA_VERSION},
            "deployment": {"type": "string", "pattern": DEPLOYMENT_PATTERN},
            "backend": {"enum": names},
            "target": {
                "type": "object",
                "additionalProperties": False,
                "minProperties": 1,
                "maxProperties": 1,
                "properties": {n: registry[n].config_schema() for n in names},
            },
            "image": IMAGE_SCHEMA,
            "vms": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "object", "required": ["name"]},
            },
        },
        # The whole of the composition: the selected backend must be the one
        # configured under `target`.
        "allOf": [
            {
                "if": {
                    "properties": {"backend": {"const": n}},
                    "required": ["backend"],
                },
                "then": {"properties": {"target": {"required": [n]}}},
            }
            for n in names
        ],
    }


def load(path: str | Path, registry: dict[str, Any]) -> dict:
    """Load, validate, and return the config.

    Raises ``ConfigError`` carrying *every* problem rather than the first: an
    operator editing a config at a site should not have to round-trip once per
    typo.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError([Problem(Severity.ERROR, str(exc), where=str(path))]) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(
            [Problem(Severity.ERROR, f"invalid YAML: {exc}", where=str(path))]
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            [
                Problem(
                    Severity.ERROR,
                    f"config must be a mapping, got {type(raw).__name__}",
                    where=str(path),
                )
            ]
        )

    # `deployment` is document-level identity rather than a per-VM value, so it
    # is the one field with a default: the config's filename stem, so a config
    # that never mentions it still stamps something meaningful into every marker.
    raw.setdefault("deployment", path.stem)

    problems = validate(raw, registry)
    if any(p.fatal for p in problems):
        raise ConfigError(problems)
    return raw


def validate(cfg: dict, registry: dict[str, Any]) -> list[Problem]:
    """Structural validation, then the selected backend's own checks."""
    validator = jsonschema.Draft202012Validator(core_schema(registry))

    problems = [
        Problem(
            Severity.ERROR,
            err.message,
            where=".".join(str(p) for p in err.absolute_path) or "<root>",
        )
        for err in sorted(
            validator.iter_errors(cfg), key=lambda e: list(map(str, e.absolute_path))
        )
    ]
    if problems:
        # Backend validators assume a structurally sound document. Running them
        # on a broken one piles noise on top of the errors that actually matter.
        return problems

    problems += registry[cfg["backend"]].validate(cfg)

    seen: set[str] = set()
    for vm in cfg["vms"]:
        name = vm["name"]
        if name in seen:
            problems.append(
                Problem(Severity.ERROR, f"duplicate VM name {name!r}", where="vms")
            )
        seen.add(name)

    return problems


def vm_names(cfg: dict) -> list[str]:
    return [vm["name"] for vm in cfg["vms"]]
