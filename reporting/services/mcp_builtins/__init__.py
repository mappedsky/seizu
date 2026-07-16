"""Registry of built-in MCP tools.

Each group module (e.g. ``reports``, ``toolsets``) exposes a
``GROUP_DEF: BuiltinGroup``.  The registry flattens them into a single
lookup table keyed by tool name (``<group>__<action>``) and exposes two
helpers used by ``reporting.services.mcp_server``:

* :func:`list_builtin_tools` — tools visible for tool listing, filtered by
  ``MCP_ENABLED_BUILTINS``.
* :func:`find_builtin` — resolve a tool by name, also filtered.
"""

from reporting.services.mcp_builtins import graph as _graph
from reporting.services.mcp_builtins import reports as _reports
from reporting.services.mcp_builtins import roles as _roles
from reporting.services.mcp_builtins import sandbox as _sandbox
from reporting.services.mcp_builtins import scheduled_queries as _scheduled_queries
from reporting.services.mcp_builtins import skillsets as _skillsets
from reporting.services.mcp_builtins import toolsets as _toolsets
from reporting.services.mcp_builtins import workflows as _workflows
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool

# Ordered so ``list_tools`` renders a consistent layout; ``graph`` first
# because it's the ad-hoc catch-all (schema + query), then everything
# alphabetical.
_GROUPS: list[BuiltinGroup] = [
    _graph.GROUP_DEF,
    _reports.GROUP_DEF,
    _roles.GROUP_DEF,
    _sandbox.GROUP_DEF,
    _scheduled_queries.GROUP_DEF,
    _workflows.GROUP_DEF,
    _skillsets.GROUP_DEF,
    _toolsets.GROUP_DEF,
]

_TOOLS_BY_NAME: dict[str, BuiltinTool] = {tool.name: tool for group in _GROUPS for tool in group.tools}

_ALL_GROUP_NAMES: list[str] = [g.name for g in _GROUPS]


def all_group_names() -> list[str]:
    """Return every registered group name (for default settings + admin UIs)."""
    return list(_ALL_GROUP_NAMES)


def _get_allowed() -> set | None:
    """Read MCP_ENABLED_BUILTINS from settings and return the allowed group set.

    Returns None when unset/empty (all groups enabled).
    Returns an empty set when set to the sentinel value "none" (all disabled).
    Returns a set of group names otherwise.
    """
    from reporting import settings

    enabled = settings.MCP_ENABLED_BUILTINS
    if not enabled:
        return None
    normalized = [v.strip().lower() for v in enabled]
    if normalized == ["none"]:
        return set()
    return set(normalized)


def list_builtin_groups() -> list[BuiltinGroup]:
    """Return every built-in group that is enabled per ``MCP_ENABLED_BUILTINS``."""
    allowed = _get_allowed()
    if allowed is None:
        return list(_GROUPS)
    return [g for g in _GROUPS if g.name in allowed]


def list_builtin_tools(*, include_chat_only: bool = False) -> list[BuiltinTool]:
    """Return every built-in tool whose group is enabled per ``MCP_ENABLED_BUILTINS``.

    ``include_chat_only`` controls whether tools marked ``chat_only=True`` are
    included.  The MCP server always omits them (default); the chat agent passes
    ``True`` so those tools appear in its tool list.
    """
    allowed = _get_allowed()
    tools: list[BuiltinTool] = []
    for group in _GROUPS:
        if allowed is not None and group.name not in allowed:
            continue
        for tool in group.tools:
            if tool.chat_only and not include_chat_only:
                continue
            if tool.enabled is not None and not tool.enabled():
                continue
            tools.append(tool)
    return tools


def always_disclosed_tool_names() -> frozenset[str]:
    """Return names of tools that are always callable under progressive disclosure.

    These tools appear in the chat agent's callable set regardless of whether a
    skill has been rendered to unlock them.  They are still subject to
    ``MCP_ENABLED_BUILTINS`` group filtering and permissions checks.
    """
    allowed = _get_allowed()
    names: set[str] = set()
    for group in _GROUPS:
        if allowed is not None and group.name not in allowed:
            continue
        for tool in group.tools:
            if tool.enabled is not None and not tool.enabled():
                continue
            if tool.always_disclosed:
                names.add(tool.name)
    return frozenset(names)


def find_builtin(name: str, *, include_chat_only: bool = False) -> BuiltinTool | None:
    """Look up a built-in tool by name; returns None if the group is disabled.

    ``include_chat_only`` mirrors the same parameter on :func:`list_builtin_tools`:
    the MCP server leaves it ``False``; the chat agent passes ``True``.
    """
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        return None
    allowed = _get_allowed()
    if allowed is not None and tool.group not in allowed:
        return None
    if tool.chat_only and not include_chat_only:
        return None
    if tool.enabled is not None and not tool.enabled():
        return None
    return tool


__all__ = [
    "BuiltinGroup",
    "BuiltinTool",
    "all_group_names",
    "always_disclosed_tool_names",
    "find_builtin",
    "list_builtin_groups",
    "list_builtin_tools",
]
