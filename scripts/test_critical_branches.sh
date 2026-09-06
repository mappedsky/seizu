#!/bin/sh
set -eu

uv run --frozen --no-sync pytest \
  -q \
  tests/unit/reporting/authnz_headless_test.py \
  tests/unit/reporting/authnz_permissions_test.py \
  tests/unit/reporting/authnz_test.py \
  tests/unit/reporting/services/query_validator_test.py \
  tests/unit/reporting/services/report_store \
  --cov=reporting.authnz \
  --cov=reporting.services.query_validator \
  --cov=reporting.services.report_store \
  --cov-branch \
  --cov-report=json:coverage/critical-branches.json \
  --cov-fail-under=0

uv run --frozen --no-sync python scripts/check_critical_coverage.py coverage/critical-branches.json
