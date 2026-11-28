#!/usr/bin/env python3
"""Configure the local .env for the external MCP gateway (``make external_mcp_enable``).

Every dev MCP server sits behind the local Pomerium gateway, and Seizu reaches
each as a ``user_oauth`` proxy: a user connects once from Chat Connections and
calls then carry that user's own token. This writes the settings that path
needs and leaves everything else in .env alone:

- ``MCP_EXTERNAL_ENABLED=true`` and the dev ``MCP_EXTERNAL_PROXIES`` (replaced
  if different; the previous file is kept as .env.bak);
- ``MCP_EXTERNAL_CA_FILE``, the CA the gateway generates on first start;
- ``DELEGATED_TOKEN_ENCRYPTION_KEYS`` and ``SESSION_TOKEN_ENCRYPTION_KEY``,
  generated only when absent, since replacing them would discard stored grants
  or sessions.

``disable`` sets ``MCP_EXTERNAL_ENABLED=false`` and keeps the rest. Standard
library only; values are never printed.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"

_GATEWAY = "https://{host}.localhost.pomerium.io:8443"
DEV_PROXIES = [
    {
        "name": "github",
        "url": _GATEWAY.format(host="github") + "/mcp",
        "upstream_urls": ["https://mcp.github.test/mcp"],
        "transport": "streamable_http",
        "auth_mode": "user_oauth",
    },
    {
        "name": "deps",
        "url": _GATEWAY.format(host="deps") + "/mcp",
        "upstream_urls": ["https://mcp.deps.test/mcp"],
        "transport": "streamable_http",
        "auth_mode": "user_oauth",
        "require_confirmation": False,
    },
    {
        "name": "whoami",
        "url": _GATEWAY.format(host="whoami") + "/",
        "transport": "streamable_http",
        "auth_mode": "user_oauth",
        "require_confirmation": False,
    },
]
# Inside the seizu containers the repository is mounted at /home/seizu/seizu.
DEV_CA_FILE = "/home/seizu/seizu/.compose/external-mcp-gateway/ca.pem"


def _new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def _read() -> list[str]:
    if not ENV_FILE.exists():
        shutil.copyfile(EXAMPLE_FILE, ENV_FILE)
    return ENV_FILE.read_text().splitlines()


def _values(lines: list[str]) -> dict[str, str]:
    values = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            values[key.strip()] = value
    return values


def _set(lines: list[str], key: str, value: str) -> list[str]:
    """Replace the first uncommented ``key=`` line, or append one."""
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("#") and line.partition("=")[0].strip() == key:
            lines[index] = f"{key}={value}"
            return lines
    return [*lines, f"{key}={value}"]


def enable() -> list[str]:
    lines = _read()
    current = _values(lines)
    wanted = {
        "MCP_EXTERNAL_ENABLED": "true",
        "MCP_EXTERNAL_PROXIES": json.dumps(DEV_PROXIES, separators=(",", ":")),
        "MCP_EXTERNAL_CA_FILE": DEV_CA_FILE,
    }
    for key in ("DELEGATED_TOKEN_ENCRYPTION_KEYS", "SESSION_TOKEN_ENCRYPTION_KEY"):
        if not current.get(key, "").strip():
            wanted[key] = f"dev:{_new_key()}" if key == "DELEGATED_TOKEN_ENCRYPTION_KEYS" else _new_key()
    changed = [key for key, value in wanted.items() if current.get(key) != value]
    if not changed:
        return []
    for key in changed:
        lines = _set(lines, key, wanted[key])
    return _write(lines, changed)


def disable() -> list[str]:
    lines = _read()
    if _values(lines).get("MCP_EXTERNAL_ENABLED") == "false":
        return []
    return _write(_set(lines, "MCP_EXTERNAL_ENABLED", "false"), ["MCP_EXTERNAL_ENABLED"])


def _write(lines: list[str], changed: list[str]) -> list[str]:
    shutil.copyfile(ENV_FILE, ENV_FILE.with_suffix(".bak"))
    ENV_FILE.write_text("\n".join(lines) + "\n")
    return changed


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action not in {"enable", "disable"}:
        sys.exit("usage: dev_external_mcp.py enable|disable")
    changed = enable() if action == "enable" else disable()
    if changed:
        print(f"Updated {', '.join(changed)} in .env (previous copy: .env.bak).")
    else:
        print(".env already has these settings.")


if __name__ == "__main__":
    main()
