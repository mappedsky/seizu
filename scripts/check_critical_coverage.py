"""Enforce branch-aware coverage floors for security and persistence code."""

import json
import sys
from pathlib import Path

THRESHOLDS = {
    "reporting/authnz/": 85.0,
    "reporting/services/query_validator.py": 95.0,
    "reporting/services/report_store/": 75.0,
}


def _matches(filename: str, target: str) -> bool:
    return filename == target or filename.startswith(target)


def main() -> int:
    report = json.loads(Path(sys.argv[1]).read_text())
    failed = False
    for target, threshold in THRESHOLDS.items():
        summaries = [entry["summary"] for filename, entry in report["files"].items() if _matches(filename, target)]
        covered = sum(item["covered_lines"] + item["covered_branches"] for item in summaries)
        total = sum(item["num_statements"] + item["num_branches"] for item in summaries)
        percent = 100.0 if total == 0 else covered * 100.0 / total
        print(f"{target}: {percent:.2f}% branch-aware coverage (required {threshold:.2f}%)")
        failed |= percent < threshold
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
