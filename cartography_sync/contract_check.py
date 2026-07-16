"""Contract check: every registry flag must exist in the installed cartography CLI.

``python -m cartography_sync.contract_check`` (run inside the pinned sync
image, e.g. ``make cartography_contract_test``). The module registry is
reviewed against a specific cartography release; this check catches a pin
bump (Dependabot or manual) that renames or removes a flag the registry still
emits, before a scheduled sync fails at runtime.
"""

import os
import subprocess
import sys

from cartography_sync.registry import MODULE_REGISTRY

# Flags the activity itself emits around the registry argv.
_BASE_FLAGS = ("--selected-modules", "--neo4j-uri", "--neo4j-user", "--neo4j-password-env-var")


def registry_flags() -> set[str]:
    flags = set(_BASE_FLAGS)
    for spec in MODULE_REGISTRY.values():
        for flag in spec.flags:
            flags.add(flag.flag)
        for token in spec.fixed_argv:
            flags.add(token.split("=", 1)[0])
    return flags


def main() -> int:
    binary = os.environ.get("CARTOGRAPHY_BIN", "cartography")
    # Cartography's rich-formatted help truncates long flag names to the
    # terminal width; a wide COLUMNS keeps every option name intact.
    env = {**os.environ, "COLUMNS": "500"}
    help_text = subprocess.run(  # noqa: S603 — fixed argv, no user input
        [binary, "--help"], capture_output=True, text=True, check=True, env=env
    ).stdout
    missing = sorted(flag for flag in registry_flags() if flag not in help_text)
    if missing:
        print(f"FAIL: flags in cartography_sync.registry but not in `{binary} --help`: {missing}")
        return 1
    print(f"OK: all {len(registry_flags())} registry flags exist in `{binary} --help`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
