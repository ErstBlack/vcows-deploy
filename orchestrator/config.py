"""Core config: load, compose the schema from the registry, validate.

Core carries ``backend`` (an enum built from the registry) and
``target: {<name>: <sub-schema from that backend>}``, wired together with a
generated ``if/then`` per registered backend. Adding a backend adds a schema file
inside its own package and touches nothing here -- **except ``IMAGE_SCHEMA``**,
which is not composed from the registry and whose field names (``source_qcow2``,
``base_volume_name``) are qcow2 and libvirt terms. It is the one block a second
backend has to open this file for. See ``backends/base.py``.

**``vms`` stays loose on purpose.** Core requires only what it needs to drive the
pipeline -- a name. The per-VM shape (vcpus, disks, and especially NICs, whose
valid forms are entirely backend-specific) is validated by the backend. That
keeps this file backend-agnostic, and it produces better errors: a jsonschema
``oneOf`` failure is close to unreadable, where a backend check can say "exactly
one of bridge/network, found both".

A top-level ``defaults`` block holds flat per-VM values -- scalars, strings,
booleans and lists -- folded into every VM that omits the key, and a per-VM value
**replaces** rather than merges (``backends/libvirt/schema.py``). Resolution is
``resolve`` here, so a backend is only ever handed a VM already carrying every
value it will be judged against, and no backend needs a merge rule of its own.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .problems import Problem, problems_from

SCHEMA_VERSION = 1

#: Deployment names end up in markers and in operator-facing messages. ``\Z``
#: rather than ``$``, because Python's ``$`` also matches before a trailing
#: newline and this value is stamped into every marker.
DEPLOYMENT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z"

IMAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_qcow2", "base_volume_name"],
    "properties": {
        # Path inside the container, bind-mounted read-only. Absolute, because a
        # relative path resolves against a working directory nothing here
        # controls, and because the backend hands this string to the provider's
        # `create.content.url`, which really does resolve a URL. Measured on the
        # rig: a bare path, `file://` and `http://` all create the volume, and
        # the HTTP fetch came from the *client* -- an http server bound to the
        # client's loopback, which the hypervisor cannot reach, served it. So
        # without this anchor a config could send the container to the network
        # for its own base image, at a site whose whole premise is that there
        # isn't one.
        "source_qcow2": {"type": "string", "minLength": 1, "pattern": r"^/"},
        "sha256": {"type": "string", "pattern": r"^[0-9a-fA-F]{64}\Z"},
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
            # Flat values only. A mapping would need a merge rule and a per-VM
            # value replaces, so there is nothing to merge. `name` is identity;
            # `nics` collides on `ip_cidr` for any second VM, and the useful
            # form is per-field. `{"not": {}}` rather than `False`: a `False`
            # sub-schema files its error against `defaults` with no property in
            # the path, so the message would not name the key the operator has
            # to delete.
            "defaults": {
                "type": "object",
                "additionalProperties": {"not": {"type": "object"}},
                "properties": {"name": {"not": {}}, "nics": {"not": {}}},
            },
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


def load(
    path: str | Path, registry: dict[str, Any], *, verify_digest: bool = True
) -> tuple[dict, list[Problem]]:
    """Load, validate, and return the config and everything non-fatal about it.

    Raises ``ConfigError`` carrying *every* problem rather than the first: an
    operator editing a config at a site should not have to round-trip once per
    typo.

    The warnings come back rather than being dropped because this is the only
    place they are computed. Every verb loads, so every verb has them without
    validating a second time.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ConfigError([Problem.error(str(exc), where=str(path))]) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(
            [Problem.error(f"invalid YAML: {exc}", where=str(path))]
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(
            [
                Problem.error(
                    f"config must be a mapping, got {type(raw).__name__}",
                    where=str(path),
                )
            ]
        )

    # `deployment` is document-level identity rather than a per-VM value, so it
    # is the one field with a default: the config's filename stem, so a config
    # that never mentions it still stamps something meaningful into every marker.
    defaulted = "deployment" not in raw
    raw.setdefault("deployment", path.stem)

    problems = validate(raw, registry, verify_digest=verify_digest)
    if defaulted:
        problems = [_blame_the_filename(p, path) for p in problems]
    if any(p.fatal for p in problems):
        raise ConfigError(problems)
    return resolve(raw), problems


def resolve(cfg: dict) -> dict:
    """Fold ``defaults`` into every VM. Pure, and idempotent.

    A VM's own value replaces the default rather than merging with it, which is
    what keeps the block flat: with no nested value there is no merge to get
    wrong. ``defaults`` stays on the result -- backends read ``vms`` and nothing
    else, so stripping it would be code with no reader.

    Correct only once the core schema has passed, since it assumes ``defaults``
    is a mapping and every VM is one. ``validate`` is what enforces that order.
    """
    defaults = cfg.get("defaults", {})
    return {**cfg, "vms": [{**defaults, **vm} for vm in cfg["vms"]]}


def _blame_the_filename(problem: Problem, path: Path) -> Problem:
    """Re-point a complaint about a defaulted ``deployment`` at the file.

    The stem became the deployment name, so a filename the pattern rejects is
    reported against a key the operator never wrote. Rewritten here rather than
    checked before ``validate`` so ``load`` still returns every problem rather
    than the first.
    """
    if problem.where != "deployment":
        return problem
    return Problem(
        problem.severity,
        f"the deployment name defaults to this config's filename, and "
        f"{path.stem!r} is not usable as one: {problem.message} Rename the file, "
        f"or set `deployment:` in it.",
        where=str(path),
    )


#: One VM's key, as ``problems_from`` renders it, and whatever the problem
#: reaches into below it: ``vms[0].nics[1].mac`` -> ``0``, ``nics``, ``[1].mac``.
_AT_A_VM_KEY = re.compile(r"vms\[(\d+)\]\.([^.\[]+)(.*)")

#: A problem filed anywhere inside one VM, including at the VM itself: an
#: unknown key renders as ``vms[2]`` with no key below it.
_AT_A_VM = re.compile(r"vms\[(\d+)\]")


def _blame_the_defaults(problems: list[Problem], cfg: dict) -> list[Problem]:
    """Re-point complaints about an inherited value at the key that supplied it.

    Same argument as ``_blame_the_filename``: the operator never wrote the key
    being blamed. It is also why exact duplicates go -- one bad default is one
    mistake, and every VM that inherited it produced the identical problem.

    ``cfg`` is the *unresolved* config, so "the VM did not set this" is still a
    question it can answer.
    """
    defaults = cfg.get("defaults")
    if not defaults:
        return problems
    out: list[Problem] = []
    for problem in problems:
        found = _AT_A_VM_KEY.fullmatch(problem.where)
        if found and found[2] in defaults and found[2] not in cfg["vms"][int(found[1])]:
            problem = Problem(
                problem.severity,
                problem.message,
                where=f"defaults.{found[2]}{found[3]}",
            )
        if problem not in out:
            out.append(problem)
    return out


def _name_the_vm(problems: list[Problem], cfg: dict) -> list[Problem]:
    """Put the VM's own name in the message of anything filed under ``vms[N]``.

    The index is what the document has and the name is what the operator has, so
    the report carries both -- but the name goes in the **message** and not in
    ``where``. ``where`` is an address the rest of the tree reads exactly:
    ``_blame_the_filename`` compares it, ``_blame_the_defaults`` regexes it,
    ``run.json`` records it, and the suite asserts it. The message is the half
    only the operator reads, so it is the half that can gain a name for free.

    Runs after ``_blame_the_defaults``, which is why a problem re-pointed at
    ``defaults.x`` comes out unnamed: it is about the default, not about any one
    VM that inherited it.
    """
    out: list[Problem] = []
    for problem in problems:
        found = _AT_A_VM.match(problem.where)
        vm = cfg["vms"][int(found[1])] if found else None
        if isinstance(vm, dict) and isinstance(vm.get("name"), str):
            problem = Problem(
                problem.severity,
                f"VM {vm['name']!r}: {problem.message}",
                where=problem.where,
            )
        out.append(problem)
    return out


def validate(
    cfg: dict, registry: dict[str, Any], *, verify_digest: bool = True
) -> list[Problem]:
    """Structural validation, then the selected backend's own checks.

    ``verify_digest`` is carried straight to the backend and read nowhere here;
    ``Backend.validate`` says what it costs and who turns it off.
    """
    validator = jsonschema.Draft202012Validator(core_schema(registry))
    problems = problems_from(validator.iter_errors(cfg), root="<root>")
    if problems:
        # Backend validators assume a structurally sound document. Running them
        # on a broken one piles noise on top of the errors that actually matter.
        return _name_the_vm(problems, cfg)

    # The backend sees a resolved VM and never the block itself; what it says
    # about a value no VM wrote is then filed against the key that did.
    problems += _blame_the_defaults(
        registry[cfg["backend"]].validate(resolve(cfg), verify_digest=verify_digest),
        cfg,
    )

    seen: set[str] = set()
    for vm in cfg["vms"]:
        name = vm["name"]
        if name in seen:
            problems.append(Problem.error(f"duplicate VM name {name!r}", where="vms"))
        seen.add(name)

    return _name_the_vm(problems, cfg)
