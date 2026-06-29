"""Tests for the MCP built-in registry (filtering + group resolution)."""

import json
from unittest.mock import patch

import mcp.types as mcp_types
import pytest

from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.services.mcp_builtins import all_group_names, find_builtin, list_builtin_tools
from reporting.services.mcp_server import _build_mcp_server, _mcp_permissions


def test_all_group_names_includes_known_groups():
    groups = all_group_names()
    assert "graph" in groups
    assert "reports" in groups
    assert "scheduled_queries" in groups
    assert "toolsets" in groups
    assert "roles" in groups


def test_list_builtin_tools_empty_filter_returns_all():
    # Empty MCP_ENABLED_BUILTINS means all groups are enabled.
    # chat_only tools are excluded by default (MCP server path), so groups
    # whose tools are all chat_only won't appear in the group set.
    with patch("reporting.settings.MCP_ENABLED_BUILTINS", []):
        all_tools = list_builtin_tools()
    assert "graph" in {t.group for t in all_tools}
    assert "reports" in {t.group for t in all_tools}
    # sandbox tools are chat_only — they must not appear in the MCP listing.
    assert "sandbox" not in {t.group for t in all_tools}


def test_list_builtin_tools_include_chat_only_returns_all_groups():
    # SANDBOX_ENABLED (default false) gates the sandbox group's only tool, so pin
    # it on to assert every group is represented regardless of the environment.
    with patch("reporting.settings.MCP_ENABLED_BUILTINS", []), patch("reporting.settings.SANDBOX_ENABLED", True):
        all_tools = list_builtin_tools(include_chat_only=True)
    assert {t.group for t in all_tools} == set(all_group_names())


def test_list_builtin_tools_filters_by_group():
    with patch("reporting.settings.MCP_ENABLED_BUILTINS", ["graph"]):
        tools = list_builtin_tools()
    assert {t.group for t in tools} == {"graph"}
    assert {t.name for t in tools} == {
        "graph__schema",
        "graph__query",
        "graph__validate_query",
        "graph__explain",
    }


def test_find_builtin_returns_tool():
    tool = find_builtin("reports__list")
    assert tool is not None
    assert tool.group == "reports"


def test_find_builtin_respects_group_filter():
    # Reports group disabled — lookup should miss even though the tool exists.
    with patch("reporting.settings.MCP_ENABLED_BUILTINS", ["graph"]):
        assert find_builtin("reports__list") is None


@pytest.mark.parametrize("sentinel", ["none", "NONE", "None", " none ", " NONE "])
def test_list_builtin_tools_none_sentinel_disables_all(sentinel):
    with patch("reporting.settings.MCP_ENABLED_BUILTINS", [sentinel]):
        assert list_builtin_tools() == []


def test_find_builtin_unknown_name_returns_none():
    assert find_builtin("nonexistent__tool") is None


def test_every_builtin_has_required_permissions():
    # A tool without any permission requirement would bypass RBAC entirely —
    # guard against accidentally adding one.  Check all tools including chat_only.
    for tool in list_builtin_tools(include_chat_only=True):
        assert tool.required_permissions, f"{tool.name} is missing required_permissions"


@pytest.mark.parametrize(
    "tool",
    [t for t in list_builtin_tools(include_chat_only=True) if not t.chat_only],
    ids=lambda t: t.name,
)
async def test_each_builtin_enforces_its_required_permission(tool):
    """Calling a tool without its required permission returns Permission denied."""
    insufficient = frozenset(ALL_PERMISSIONS) - frozenset(tool.required_permissions)
    server = _build_mcp_server()
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=tool.name, arguments={}),
    )
    tok = _mcp_permissions.set(insufficient)
    try:
        result = await handler(req)
    finally:
        _mcp_permissions.reset(tok)
    data = json.loads(result.root.content[0].text)
    assert "Permission denied" in data["error"], (
        f"{tool.name}: expected permission denial for {tool.required_permissions}, got: {data}"
    )
