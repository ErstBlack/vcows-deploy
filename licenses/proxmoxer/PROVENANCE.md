# `proxmoxer` — licence provenance

The one runtime dependency in this image that is **not** an RPM, so it is the one
whose licence `rpm -qa` cannot report and this file has to.

## The artifact

| | |
|---|---|
| Package | `proxmoxer` |
| Version | `2.3.0` (released 2026-03-04) |
| Artifact | `proxmoxer-2.3.0-py3-none-any.whl` |
| SHA256 | `1c03445e95cf9c53b6e50614dbaf561e0e1eb3ec878cf45ddde4bc4421c56743` |
| Licence | MIT, `LICENSE` beside this file |
| Upstream | https://github.com/proxmoxer/proxmoxer |

## Why it is a wheel and not an RPM

Measured 2026-09-01: `dnf repoquery python3-proxmoxer` returns nothing across
Rocky 10 `baseos`, `appstream`, `crb`, `extras` or EPEL 10 — while
`python3-pycdlib` returns a hit from the same query, so the absence is real and
not a broken lookup. Repology shows no Fedora or RHEL-derived packaging anywhere.

It is `py3-none-any` with `requires_dist: null`, so nothing is resolved and
nothing is compiled: the `pydeps` stage in the `Containerfile` verifies one
sha256-pinned wheel and unpacks it, and the delivered image carries no pip. Its
only real dependency, `requests`, **is** an RPM (`python3-requests`) and is
installed as one.

It lands in `/opt/vcows/vendor` rather than in site-packages, so `rpm -qa` stays
the whole truth about what dnf installed. `container/manifest.py` reports it
separately under `pip_packages`, because a manifest that stayed silent would
disagree with the SBOM — syft finds the `.dist-info` and names it.
