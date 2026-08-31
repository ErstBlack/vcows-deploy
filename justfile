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
#
# The export is how uv.lock reaches an install. `uv pip install -e . --group dev`
# resolved against the `>=` bounds in pyproject.toml and read the lock never, so
# a ruff or pytest release could turn a green PR red for reasons unrelated to the
# PR. `uv sync` still cannot express the two flags above -- that argument is
# unchanged -- but exporting the lock into this venv needs neither of them.
#
# `--locked` rather than `--frozen`: it also asserts uv.lock still matches
# pyproject.toml, so editing a bound without re-locking stops here, with uv
# naming the fix, instead of installing a set the lock does not describe.
#
# **A file rather than a pipe, and that is the whole reason for the third line.**
# `uv export --locked ... | uv pip install -r -` takes the pipeline's exit status
# from the install, so a refused `--locked` made this recipe print a warning that
# no dependencies were found on stdin and **exit 0 having installed nothing** --
# measured. just runs each line in its own shell and stops on the first failure,
# so three lines check what one pipe did not. .venv/ is disposable and recreated
# by the first line, so the export needs no cleanup and no temp directory.
#
# The `dev` group is uv's default here, verified byte-identical with an explicit
# `--group dev`, so no group argument is carried.

# Create .venv with the system libvirt binding visible, and install from uv.lock.
dev-env:
    uv venv --python /usr/bin/python3 --system-site-packages
    uv export --locked --format requirements-txt -o .venv/requirements.txt
    uv pip install -r .venv/requirements.txt

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
test-image *ARGS:
    ./scripts/test-image.sh {{ARGS}}

# Scan the built image: trivy against the CVE baseline, plus an SBOM.
scan:
    ./scripts/image-scan.sh

# Assemble the delivery bundle from what `just scan` wrote: the compressed
# image, the SBOM and trivy report beside it, and SHA256SUMS over all of them.
# Integrity only -- nothing here is signed. See docs/ci.md.
bundle:
    ./scripts/bundle.sh

# Mutation testing. **Does not currently complete** -- mutmut's clean baseline
# fails with two copies of `orchestrator` loaded at once; see pyproject.toml. Left
# here because the config is right and the remaining problem is mutmut's sys.path,
# but no pipeline calls it.
mutants:
    VCOWS_GATES=tofu .venv/bin/mutmut run
