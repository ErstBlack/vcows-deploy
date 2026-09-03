#!/usr/bin/env bash
# Check the state that is cheap to detect and expensive to misdiagnose.
#
# pyproject.toml describes the venv trap: without --system-site-packages
# the python3-libvirt RPM is invisible, and without an explicit
# --python /usr/bin/python3 uv installs its own managed CPython whose
# site-packages holds no RPMs at all -- **so the flag appears to work while
# `import libvirt` still fails.** A written rule asks an agent to remember that.
# This checks it, in 0.15s, once per session.
#
# SessionStart stdout is injected into context, so **silence is the healthy
# path**: a probe that prints on every start is a per-session token tax. It also
# always exits 0. SessionStart cannot block, and a probe that could fail is a way
# to disrupt every session in the project.
#
# Deliberately one check. This is not a second copy of CLAUDE.md.

set -uo pipefail
IFS=$'\n\t'

REPO="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

if ! "$REPO/.venv/bin/python" -c "import libvirt" >/dev/null 2>&1; then
    printf '%s\n' "vcows: the venv cannot import libvirt. Run 'just dev-env', never 'uv sync' -- see CLAUDE.md. tests/fake_libvirt.py imports libvirt at module scope, so collection fails, not just the hardware tests."
fi

exit 0
