# vcows-deploy -- the command menu.
#
# `just` with no arguments lists every recipe. Both CI pipelines call these and
# nothing else, so `.github/` and `.gitlab-ci.yml` hold no logic of their own and
# the GitLab migration is a deletion rather than a rewrite. scripts/lint.sh
# asserts that rather than trusting it.
#
# Recipes stay short on purpose. Anything with real logic -- a temp directory, a
# trap, a digest check -- lives in scripts/ where shellcheck reads it.

# List the recipes.
default:
    @just --list --unsorted

# --system-site-packages is not optional: the libvirt binding is an RPM, PyPI
# ships sdist only, and without the flag `import libvirt` fails everywhere.
# `--group dev` rather than `.[dev]`, which is not an extra this project has.

# Create .venv with the system libvirt binding visible, and install dev tools.
dev-env:
    uv venv --python /usr/bin/python3 --system-site-packages
    uv pip install -e . --group dev

# Install the OS packages the suite needs (python3-libvirt, xorriso).
os-deps:
    ./scripts/os-deps.sh

# Download the pinned tool binaries (uv, tofu, just, hadolint, trivy, syft).
tools:
    ./scripts/install-tools.sh

# Every static gate: ruff, ruff format, hadolint, tofu fmt, shellcheck.
lint:
    ./scripts/lint.sh

# The same gates, applying what ruff can fix first.
fix:
    ./scripts/lint.sh --fix

# Type-check with ty.
typecheck:
    .venv/bin/ty check

# The suite. `just test -k name` and friends pass through.
test *ARGS:
    .venv/bin/python -m pytest -q -rs {{ARGS}}

# What a developer runs before pushing, and what CI's `check` job runs.
check: lint typecheck test

# Build or refresh .tools/tofu-mirror from the registry.
mirror:
    ./scripts/mirror.sh

# Check an existing or cache-restored mirror without downloading.
verify-mirror:
    ./scripts/mirror.sh --verify-only

# Verify the mirror if it is there, build it if it is not. What CI calls.
ensure-mirror:
    ./scripts/mirror.sh --ensure

# Prove the provider version and hashes agree in all four places.
verify-provider:
    ./scripts/verify-provider.sh

# The OpenTofu module gates, demanded rather than skipped.
test-tofu:
    VCOWS_GATES=tofu .venv/bin/python -m pytest -q -rs

# Build the container image.
image:
    ./scripts/image-build.sh

# The offline container gate, demanded rather than skipped. Needs podman.
test-image:
    VCOWS_GATES=image .venv/bin/python -m pytest -q -rs

# Scan the built image: trivy against the CVE baseline, plus an SBOM.
scan:
    ./scripts/image-scan.sh

# Mutation testing. **Does not currently complete** -- mutmut's clean baseline
# fails with two copies of `orchestrator` loaded at once; see pyproject.toml. Left
# here because the config is right and the remaining problem is mutmut's sys.path,
# but no pipeline calls it.
mutants:
    VCOWS_GATES=tofu .venv/bin/mutmut run
