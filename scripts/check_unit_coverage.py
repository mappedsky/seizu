#!/usr/bin/env python3
"""Enforce coverage ratchets for the unit-test coverage report."""

from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("coverage/cov.json")
RATCHETS = {
    "backend and shared schema": (("reporting/", "seizu_schema/"), 89.0),
    "all measured Python packages": (
        ("reporting/", "seizu_schema/", "seizu_cli/", "cartography_sync/"),
        85.0,
    ),
}


def main() -> int:
    coverage = json.loads(REPORT.read_text())
    failed = False

    for label, (prefixes, minimum) in RATCHETS.items():
        covered = 0
        statements = 0
        for filename, details in coverage["files"].items():
            if filename.startswith(prefixes):
                summary = details["summary"]
                covered += summary["covered_lines"]
                statements += summary["num_statements"]

        percent = 100.0 * covered / statements if statements else 0.0
        print(f"{label}: {percent:.2f}% (minimum {minimum:.2f}%)")
        failed |= percent < minimum

    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
