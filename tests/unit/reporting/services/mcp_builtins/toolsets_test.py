"""Tests for the ``toolsets__*`` MCP built-in group."""

import json
from unittest.mock import AsyncMock, patch

from mcp import types as mcp_types

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.mcp_config import ToolItem, ToolsetListItem, ToolsetVersion, ToolVersion
from reporting.schema.report_config import User
from reporting.services.mcp_server import _build_mcp_server, _mcp_current_user, _mcp_permissions, _mcp_session_key
from reporting.services.query_validator import ValidationResult

_NOW = "2024-01-01T00:00:00+00:00"


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="u1",
            sub="u1",
            iss="dev",
            email="u1@example.com",
            display_name="u1",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


async def _call(server, name, arguments):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    perm_tok = _mcp_permissions.set(ALL_PERMISSIONS)
    user_tok = _mcp_current_user.set(_current_user())
    session_tok = _mcp_session_key.set("test-session")
    try:
        with patch(
            "reporting.services.mcp_runtime.action_confirmations.ensure_confirmation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await handler(req)
    finally:
        _mcp_permissions.reset(perm_tok)
        _mcp_current_user.reset(user_tok)
        _mcp_session_key.reset(session_tok)
    return result.root.content


def _toolset() -> ToolsetListItem:
    return ToolsetListItem(
        toolset_id="ts1",
        name="my-toolset",
        description="desc",
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _toolset_version() -> ToolsetVersion:
    return ToolsetVersion(
        toolset_id="ts1",
        name="my-toolset",
        description="desc",
        enabled=True,
        version=1,
        created_at=_NOW,
        created_by="u1",
    )


def _tool(toolset_id: str = "ts1", tool_id: str = "t1") -> ToolItem:
    return ToolItem(
        tool_id=tool_id,
        toolset_id=toolset_id,
        name="my-tool",
        description="desc",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _tool_version(toolset_id: str = "ts1", tool_id: str = "t1") -> ToolVersion:
    return ToolVersion(
        tool_id=tool_id,
        toolset_id=toolset_id,
        name="my-tool",
        description="desc",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        version=1,
        created_at=_NOW,
        created_by="u1",
    )


# ---------------------------------------------------------------------------
# Toolset handlers
# ---------------------------------------------------------------------------


async def test_toolsets_list_returns_items():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.list_toolsets",
        new_callable=AsyncMock,
        return_value=[_toolset()],
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__list", {})
        data = json.loads(result[0].text)

    assert len(data["toolsets"]) == 1
    assert data["toolsets"][0]["toolset_id"] == "ts1"


async def test_toolsets_get_returns_item():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
        new_callable=AsyncMock,
        return_value=_toolset(),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__get", {"toolset_id": "ts1"})
        data = json.loads(result[0].text)

    assert data["toolset_id"] == "ts1"


async def test_toolsets_get_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__get", {"toolset_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_create_forwards_user_id():
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.create_toolset",
            new_callable=AsyncMock,
            return_value=_toolset(),
        ) as mock_create,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__create",
            {"toolset_id": "ts1", "name": "my-toolset", "description": "desc", "enabled": True},
        )
        data = json.loads(result[0].text)

    assert data["toolset_id"] == "ts1"
    mock_create.assert_awaited_once_with(
        toolset_id="ts1",
        name="my-toolset",
        description="desc",
        enabled=True,
        created_by="u1",
    )


async def test_toolsets_update_success():
    args = {
        "toolset_id": "ts1",
        "name": "renamed",
        "description": "new",
        "enabled": False,
        "comment": "why",
    }
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.update_toolset",
        new_callable=AsyncMock,
        return_value=_toolset(),
    ) as mock_update:
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update", args)
        data = json.loads(result[0].text)

    assert data["toolset_id"] == "ts1"
    mock_update.assert_awaited_once_with(
        toolset_id="ts1",
        name="renamed",
        description="new",
        enabled=False,
        updated_by="u1",
        comment="why",
    )


async def test_toolsets_update_returns_error_when_missing():
    args = {
        "toolset_id": "nope",
        "name": "renamed",
        "description": "",
        "enabled": True,
    }
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.update_toolset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_delete_success():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.delete_toolset",
        new_callable=AsyncMock,
        return_value=True,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__delete", {"toolset_id": "ts1"})
        data = json.loads(result[0].text)

    assert data == {"toolset_id": "ts1"}


async def test_toolsets_delete_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.delete_toolset",
        new_callable=AsyncMock,
        return_value=False,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__delete", {"toolset_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_list_versions_returns_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
            new_callable=AsyncMock,
            return_value=_toolset(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.list_toolset_versions",
            new_callable=AsyncMock,
            return_value=[_toolset_version()],
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__list_versions", {"toolset_id": "ts1"})
        data = json.loads(result[0].text)

    assert len(data["versions"]) == 1


async def test_toolsets_list_versions_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__list_versions", {"toolset_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_get_version_returns_version():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset_version",
        new_callable=AsyncMock,
        return_value=_toolset_version(),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__get_version", {"toolset_id": "ts1", "version": 1})
        data = json.loads(result[0].text)

    assert data["version"] == 1


async def test_toolsets_get_version_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_version",
            {"toolset_id": "ts1", "version": 99},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset version not found"}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def test_toolsets_list_tools_returns_items():
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
            new_callable=AsyncMock,
            return_value=_toolset(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.list_tools",
            new_callable=AsyncMock,
            return_value=[_tool()],
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__list_tools", {"toolset_id": "ts1"})
        data = json.loads(result[0].text)

    assert len(data["tools"]) == 1
    assert data["tools"][0]["tool_id"] == "t1"


async def test_toolsets_list_tools_returns_error_when_toolset_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_toolset",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__list_tools", {"toolset_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_get_tool_returns_tool():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=_tool(),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_tool",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data["tool_id"] == "t1"


async def test_toolsets_get_tool_rejects_mismatched_toolset():
    # Tool exists but belongs to a different toolset — treat as not found.
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=_tool(toolset_id="other"),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_tool",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_create_tool_success():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "my-tool",
        "description": "desc",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.create_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ) as mock_create,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__create_tool", args)
        data = json.loads(result[0].text)

    assert data["tool_id"] == "t1"
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["toolset_id"] == "ts1"
    assert mock_create.await_args.kwargs["created_by"] == "u1"


async def test_toolsets_create_tool_rejects_invalid_cypher():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "my-tool",
        "description": "desc",
        "cypher": "CREATE (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=["write not allowed"]),
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__create_tool", args)
        data = json.loads(result[0].text)

    assert data["errors"] == ["write not allowed"]


async def test_toolsets_create_tool_returns_error_when_toolset_missing():
    args = {
        "toolset_id": "nope",
        "tool_id": "t1",
        "name": "my-tool",
        "description": "desc",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.create_tool",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__create_tool", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Toolset not found"}


async def test_toolsets_update_tool_success():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "renamed",
        "description": "desc",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
        "comment": "why",
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.update_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ) as mock_update,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update_tool", args)
        data = json.loads(result[0].text)

    assert data["tool_id"] == "t1"
    mock_update.assert_awaited_once()
    assert mock_update.await_args.kwargs["tool_id"] == "t1"
    assert mock_update.await_args.kwargs["updated_by"] == "u1"
    assert mock_update.await_args.kwargs["comment"] == "why"


async def test_toolsets_update_tool_rejects_missing_tool():
    args = {
        "toolset_id": "ts1",
        "tool_id": "nope",
        "name": "renamed",
        "description": "",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update_tool", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_update_tool_rejects_mismatched_toolset():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "renamed",
        "description": "",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=_tool(toolset_id="other"),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update_tool", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_update_tool_rejects_invalid_cypher():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "renamed",
        "description": "",
        "cypher": "CREATE (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=["nope"]),
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update_tool", args)
        data = json.loads(result[0].text)

    assert data["errors"] == ["nope"]


async def test_toolsets_update_tool_returns_error_on_store_failure():
    args = {
        "toolset_id": "ts1",
        "tool_id": "t1",
        "name": "renamed",
        "description": "",
        "cypher": "MATCH (n) RETURN n",
        "parameters": [],
        "enabled": True,
    }
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.validate_tool_cypher",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.update_tool",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "toolsets__update_tool", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_delete_tool_success():
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.delete_tool",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__delete_tool",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data == {"tool_id": "t1"}


async def test_toolsets_delete_tool_rejects_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__delete_tool",
            {"toolset_id": "ts1", "tool_id": "nope"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_delete_tool_rejects_mismatched_toolset():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=_tool(toolset_id="other"),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__delete_tool",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_delete_tool_returns_error_on_store_failure():
    # get_tool returns a matching tool, but delete_tool still returns False
    # (e.g. race with another caller).
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.delete_tool",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__delete_tool",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_list_tool_versions_returns_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
            new_callable=AsyncMock,
            return_value=_tool(),
        ),
        patch(
            "reporting.services.mcp_builtins.toolsets.report_store.list_tool_versions",
            new_callable=AsyncMock,
            return_value=[_tool_version()],
        ),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__list_tool_versions",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert len(data["versions"]) == 1


async def test_toolsets_list_tool_versions_rejects_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__list_tool_versions",
            {"toolset_id": "ts1", "tool_id": "nope"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_list_tool_versions_rejects_mismatched_toolset():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool",
        new_callable=AsyncMock,
        return_value=_tool(toolset_id="other"),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__list_tool_versions",
            {"toolset_id": "ts1", "tool_id": "t1"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool not found"}


async def test_toolsets_get_tool_version_returns_version():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool_version",
        new_callable=AsyncMock,
        return_value=_tool_version(),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_tool_version",
            {"toolset_id": "ts1", "tool_id": "t1", "version": 1},
        )
        data = json.loads(result[0].text)

    assert data["version"] == 1


async def test_toolsets_get_tool_version_rejects_mismatched_toolset():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool_version",
        new_callable=AsyncMock,
        return_value=_tool_version(toolset_id="other"),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_tool_version",
            {"toolset_id": "ts1", "tool_id": "t1", "version": 1},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool version not found"}


async def test_toolsets_get_tool_version_rejects_missing():
    with patch(
        "reporting.services.mcp_builtins.toolsets.report_store.get_tool_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "toolsets__get_tool_version",
            {"toolset_id": "ts1", "tool_id": "t1", "version": 99},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Tool version not found"}
