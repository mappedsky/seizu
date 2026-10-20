#!/bin/sh
# Type-check from the project environment, so mypy resolves the libraries the
# app imports and `uv.lock` is the only place its version is declared.
#
# `--no-install-workspace` is required, not incidental: the wheel force-includes
# `build/` (the compiled frontend), so installing the workspace root fails
# wherever that has not been built -- a fresh clone or a CI runner. The sync
# flags match the Dockerfile's for that reason.
#
# The sync runs every invocation and is a no-op once the environment matches the
# lockfile, which keeps this working on a fresh clone and in a commit hook
# without a separate setup step.
set -e
uv sync --frozen --all-groups --all-packages --no-install-workspace --quiet
exec uv run --frozen --no-sync mypy "$@"
