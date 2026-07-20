"""Contract check the registry against the installed Cartography CLI.

``python -m cartography_sync.contract_check`` (run inside the pinned sync
image, e.g. ``make cartography_contract_test``). The module registry is
reviewed against a specific cartography release; this check catches a pin
bump (Dependabot or manual) that renames or removes a flag the registry still
emits, before a scheduled sync fails at runtime.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import cast

from cartography_sync.registry import MODULE_REGISTRY

# Flags the activity itself emits around the registry argv.
_BASE_FLAGS = ("--selected-modules", "--neo4j-uri", "--neo4j-user", "--neo4j-password-env-var")
_EXPECTED_VERSION = "0.139.0"

_DISCOVERY_SCRIPT = r"""
import importlib.metadata
import json

import typer

from cartography.cli import ALWAYS_SHOW_PANELS, CLI, MODULE_PANELS
from cartography.sync import TOP_LEVEL_MODULES

app = CLI()._build_app(set(MODULE_PANELS.values()) | ALWAYS_SHOW_PANELS)
command = typer.main.get_command(app)
result = {"version": importlib.metadata.version("cartography"), "modules": {}}
for module in TOP_LEVEL_MODULES:
    panel = MODULE_PANELS.get(module)
    flags = []
    if panel is not None:
        for param in command.params:
            if getattr(param, "rich_help_panel", None) != panel:
                continue
            # Deprecated aliases duplicate a canonical option and must not be
            # emitted alongside it (Microsoft/Entra and legacy report sources).
            if (param.help or "").startswith("DEPRECATED:"):
                continue
            flags.extend(option for option in param.opts if option.startswith("--"))
    result["modules"][module] = sorted(flags)
print(json.dumps(result))
"""


def registry_flags() -> set[str]:
    flags = set(_BASE_FLAGS)
    for spec in MODULE_REGISTRY.values():
        for flag in spec.flags:
            flags.add(flag.flag)
        for token in spec.fixed_argv:
            flags.add(token.split("=", 1)[0])
    return flags


def module_registry_flags() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for module, spec in MODULE_REGISTRY.items():
        flags = {flag.flag for flag in spec.flags}
        flags.update(token.split("=", 1)[0] for token in spec.fixed_argv)
        result[module] = flags
    return result


def _cartography_contract(binary: str) -> dict[str, object]:
    """Inspect the CLI through the interpreter from its installed entrypoint."""
    binary_path = shutil.which(binary) or binary
    first_line = pathlib.Path(binary_path).read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        raise RuntimeError(f"cannot find the Python interpreter for {binary_path}")
    interpreter = first_line[2:]
    completed = subprocess.run(  # noqa: S603 — pinned interpreter and constant script
        [interpreter, "-c", _DISCOVERY_SCRIPT],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    binary = os.environ.get("CARTOGRAPHY_BIN", "cartography")
    contract = _cartography_contract(binary)
    version = contract.get("version")
    if version != _EXPECTED_VERSION:
        print(f"FAIL: registry targets Cartography {_EXPECTED_VERSION}, installed version is {version}")
        return 1
    modules = cast(dict[str, list[str]], contract["modules"])
    upstream = {name: set(flags) for name, flags in modules.items()}
    registered = module_registry_flags()
    if set(upstream) != set(registered):
        print(
            "FAIL: module registry differs from the installed Cartography modules: "
            f"missing={sorted(set(upstream) - set(registered))}, "
            f"extra={sorted(set(registered) - set(upstream))}"
        )
        return 1
    mismatches = {
        module: {
            "missing": sorted(upstream[module] - registered[module]),
            "extra": sorted(registered[module] - upstream[module]),
        }
        for module in upstream
        if upstream[module] != registered[module]
    }
    if mismatches:
        print(f"FAIL: registry options differ from Cartography {_EXPECTED_VERSION}: {mismatches}")
        return 1
    print(
        f"OK: all {len(registered)} Cartography {version} modules and "
        f"{sum(len(flags) for flags in registered.values())} canonical module flags are registered"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
