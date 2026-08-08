"""Dispatch helpers for tests that drive the MCP server's registered handlers.

MCP 2.0 replaced the lowlevel ``Server``'s decorator registration (and its
public ``request_handlers`` dict, keyed by request model) with constructor
``on_*`` arguments reachable through ``get_request_handler(method)``, and
handlers now take ``(context, params)`` and return the full result model rather
than a bare list. Every test that pokes a handler went through those details,
so they live here instead of in eight test files.
"""

from typing import Any
from unittest.mock import MagicMock

from mcp import types as mcp_types
from mcp.server.lowlevel import Server


def _request_context() -> Any:
    """A stand-in for the ``ServerRequestContext`` the SDK would build.

    Seizu's handlers read the caller's identity and permissions from the
    ContextVars that ``_MCPAuthMiddleware`` sets and never touch the context
    argument, so tests need no live ``ServerSession`` to reach them.
    """
    return MagicMock()


async def dispatch(server: Server[Any], method: str, params: Any) -> Any:
    """Invoke the handler ``server`` registered for JSON-RPC ``method``."""
    entry = server.get_request_handler(method)
    assert entry is not None, f"no handler registered for {method}"
    return await entry.handler(_request_context(), params)


async def list_tools(server: Server[Any]) -> mcp_types.ListToolsResult:
    return await dispatch(server, "tools/list", None)


async def call_tool(
    server: Server[Any], name: str, arguments: dict[str, Any] | None = None
) -> mcp_types.CallToolResult:
    params = mcp_types.CallToolRequestParams(name=name, arguments=arguments or {})
    return await dispatch(server, "tools/call", params)


async def list_prompts(server: Server[Any]) -> mcp_types.ListPromptsResult:
    return await dispatch(server, "prompts/list", None)


async def get_prompt(
    server: Server[Any], name: str, arguments: dict[str, str] | None = None
) -> mcp_types.GetPromptResult:
    params = mcp_types.GetPromptRequestParams(name=name, arguments=arguments or {})
    return await dispatch(server, "prompts/get", params)
