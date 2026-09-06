"""Fail when the checked mutation score falls below its ratchet."""

import json
import sys
from pathlib import Path


def main() -> int:
    stats = json.loads(Path(sys.argv[1]).read_text())
    minimum = float(sys.argv[2])
    killed = int(stats["killed"])
    survived = int(stats["survived"])
    checked = killed + survived
    if checked == 0:
        print("No mutants were checked")
        return 1
    score = killed * 100.0 / checked
    print(f"Mutation score: {score:.2f}% ({killed}/{checked} killed; required {minimum:.2f}%)")
    return int(score < minimum)


if __name__ == "__main__":
    raise SystemExit(main())
