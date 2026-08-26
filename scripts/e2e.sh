#!/usr/bin/env bash
# The single definition of the e2e invocation: `mise run e2e` locally and the
# release.yml e2e job both run this file. Metered LLM spend; the programmer
# runs it, never Claude (.claude/rules/llm-spend.md).
#   -m e2e               the live-model suite
#   --no-cov             the fail_under=100 gate would fail on the e2e subset
#   -n auto --dist loadgroup   xdist, knowledge arc pinned to one worker
set -euo pipefail
exec uv run pytest -m e2e --no-cov -n auto --dist loadgroup --durations=20 "$@"
