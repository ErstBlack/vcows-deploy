# vcows-deploy -- the command menu.
#
# `just` with no arguments lists every recipe. Both CI pipelines call these and
# nothing else, so `.github/` and `.gitlab-ci.yml` hold no logic of their own and
# the GitLab migration is a deletion rather than a rewrite. scripts/lint.sh
# asserts that rather than trusting it.
#
# Recipes stay short on purpose. Anything with real logic -- a temp directory, a
# trap, a digest check -- lives in scripts/ where shellcheck reads it.

# `dev-env` calls a bare `uv`, and the pinned uv is in .tools/bin -- which no
# recipe sees, because `just` does not source scripts/lib.sh.
export PATH := justfile_directory() + "/.tools/bin:" + env("PATH")

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

# Seven static gates: ruff, ruff format, hadolint, tofu fmt, shellcheck, workflows,
# gitleaks.

# Every static gate, in one pass.
lint:
    ./scripts/lint.sh

# The same gates, applying what ruff can fix first.
fix:
    ./scripts/lint.sh --fix

# The secret scan on its own, for when that is the question. `just lint` runs the
# same command as its seventh gate, so this adds no coverage -- it is the short
# way to re-check after touching something that looks like a credential, without
# waiting on ruff, hadolint, shellcheck and the workflow parser.
#
# `git` rather than `dir` scans history instead of the tree, and is the one-time
# question this deliberately does not ask on every run:
#   .tools/bin/gitleaks git . --no-banner --redact

# Scan the working tree for secrets (the seventh `just lint` gate, on its own).
secrets:
    .tools/bin/gitleaks dir . -c .gitleaks.toml --no-banner --redact

# Type-check with ty.
typecheck:
    .venv/bin/ty check

# The suite. `just test -k name` and friends pass through.
test *ARGS:
    .venv/bin/python -m pytest --cov -q -rs {{ARGS}}

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

# Apply the module against a real libvirtd under TCG, assert against what
# libvirtd created, and destroy it. Deliberately not in `check`: it installs
# packages, writes /etc/libvirt and starts a system daemon. Needs the provider
# mirror, so `just ensure-mirror` comes first.
smoke-libvirt:
    ./scripts/smoke-libvirt.sh

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

# Mutation testing, against docs/mutation-baseline.json. Fails only when the tree
# got worse -- `mutmut run` itself exits 0 whatever it finds (measured: 964
# survivors, exit 0), so a recipe that only called it would be green forever.
# See pyproject.toml for what used to stop it completing and what fixed it.
#
# Measured on 16 cores: 3835 mutants in 156s. A hosted runner has 4, so budget
# roughly four times that on a cold cache; mutmut re-tests only the mutants whose
# function changed, so a warm one is far less.
#
# No `VCOWS_GATES` here, deliberately. It used to demand `tofu`, which cannot hold
# inside mutmut's copied tree: the gate wants `.tools/tofu-mirror`, mutmut copies
# only `source_paths`, `tests/` and `also_copy`, and a 441 MB mirror is not
# something to copy per run. The tests it would turn on are the HCL module tests,
# which drive `tofu` as a subprocess and kill no Python mutant anyway.
# `tests/test_tofu_driver.py`, which is what actually covers `orchestrator/tofu.py`,
# is ungated and runs regardless.

# Mutation testing, failing only on a regression against the baseline.
mutants:
    ./scripts/mutants.sh

# A deliberate act with a reason in the commit body -- never the way to make a red
# `just mutants` go green.

# Record the current mutation numbers in docs/mutation-baseline.json.
mutants-baseline:
    ./scripts/mutants.sh --write-baseline
