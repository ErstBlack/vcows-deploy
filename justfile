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

# Six static gates: ruff, ruff format, hadolint, shellcheck, workflows,
# gitleaks.

# `just lint --fix` applies what ruff can fix first; arguments pass through.

# Every static gate, in one pass.
lint *ARGS:
    ./scripts/lint.sh {{ARGS}}

# Type-check with ty.
typecheck:
    .venv/bin/ty check

# The suite. `just test -k name` and friends pass through.
test *ARGS:
    .venv/bin/python -m pytest --cov -q -rs {{ARGS}}

# What a developer runs before pushing, and what CI's `check` job runs.
check: lint typecheck test

# Deploy against a real libvirtd under TCG, assert against what libvirtd created,
# and destroy it. Deliberately not in `check`: it installs packages, writes
# /etc/libvirt and starts a system daemon.
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
# Measured on 16 cores: 5008 mutants in 2m07s. A hosted runner has fewer and took
# 8m31s for the same work, of which the mutant loop was 7m53s. mutmut re-tests
# only the mutants whose function changed, so a warm tree is far less.
#
# CI does not run this recipe whole any more: five parallel jobs each set
# VCOWS_MUTANTS_SHARD to their k/5 and `just mutants-verdict` sums what they
# leave behind. A local run sets nothing and stays one full run. The sharding
# note in scripts/mutants.sh has the reasoning.

# `just mutants --write-baseline` records the current numbers in
# docs/mutation-baseline.json: a deliberate act with a reason in the commit
# body, never the way to make a red `just mutants` go green.

# Mutation testing, failing only on a regression against the baseline.
mutants *ARGS:
    ./scripts/mutants.sh {{ARGS}}

# Needs jq and the files the shard jobs left in .cache/mutation-stats/, and
# neither a venv nor a mutants/ tree.
# The verdict over a sharded CI run: the shard stats summed, then the baseline gate.
mutants-verdict:
    ./scripts/mutants.sh --verdict .cache/mutation-stats

# Machine-local recipes, if this box has any. `import?` is silent when the file
# is absent, which is every CI runner; what a box keeps there is its own
# business and never lands in this repository.
import? '/srv/vcows-holding/local.just'
