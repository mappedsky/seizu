#!/bin/sh
set -eu

uv run --frozen --no-sync mutmut run \
  "reporting.services.query_validator.x__scan_for_dangerous_constructs*" \
  "reporting.services.query_validator.x__summary_notifications*" \
  "reporting.services.query_validator.x_validate_tool_cypher*" \
  "reporting.services.report_store.sql.x_generate_report_id*" \
  --max-children "${MUTMUT_MAX_CHILDREN:-4}"
uv run --frozen --no-sync mutmut export-cicd-stats
uv run --frozen --no-sync python scripts/check_mutation_score.py mutants/mutmut-cicd-stats.json 70
