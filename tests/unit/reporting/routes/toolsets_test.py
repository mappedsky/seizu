from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.mcp_config import ToolItem, ToolsetListItem, ToolsetVersion, ToolVersion
from reporting.schema.report_config import User
from reporting.services.query_validator import ValidationResult

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="user@example.com",
    display_name="Test User",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)
_FAKE_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)
_UNPRIVILEGED_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=frozenset())

_TS_ID = "ts_abc123"
_TOOL_ID = "tool_xyz456"


def _make_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_CURRENT_USER
    return app


def _toolset_item(
    ts_id: str = _TS_ID,
    name: str = "My Toolset",
    version: int = 1,
    enabled: bool = True,
) -> ToolsetListItem:
    return ToolsetListItem(
        toolset_id=ts_id,
        name=name,
        description="A test toolset",
        enabled=enabled,
        current_version=version,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
    )


def _toolset_version(ts_id: str = _TS_ID, version: int = 1) -> ToolsetVersion:
    return ToolsetVersion(
        toolset_id=ts_id,
        name="My Toolset",
        description="A test toolset",
        enabled=True,
        version=version,
        created_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        comment="Initial version",
    )


def _tool_item(tool_id: str = _TOOL_ID, ts_id: str = _TS_ID, version: int = 1) -> ToolItem:
    return ToolItem(
        tool_id=tool_id,
        toolset_id=ts_id,
        name="My Tool",
        description="A test tool",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        current_version=version,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
    )


def _tool_version(tool_id: str = _TOOL_ID, ts_id: str = _TS_ID, version: int = 1) -> ToolVersion:
    return ToolVersion(
        tool_id=tool_id,
        toolset_id=ts_id,
        name="My Tool",
        description="A test tool",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        version=version,
        created_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        comment="Initial version",
    )


_VALID_TOOLSET_BODY = {
    "toolset_id": _TS_ID,
    "name": "My Toolset",
    "description": "A test toolset",
    "enabled": True,
}
_VALID_TOOL_BODY = {
    "tool_id": _TOOL_ID,
    "name": "My Tool",
    "description": "A test tool",
    "cypher": "MATCH (n) RETURN n",
    "parameters": [],
    "enabled": True,
}


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets
# ---------------------------------------------------------------------------


async def test_list_toolsets_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_toolsets",
        new=AsyncMock(return_value=[_toolset_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets")
    assert ret.status_code == 200
    items = ret.json()["toolsets"]
    # Built-in groups are prepended by the route — find the user toolset.
    user_items = [t for t in items if t["toolset_id"] == _TS_ID]
    assert len(user_items) == 1
    assert user_items[0]["name"] == "My Toolset"


async def test_list_toolsets_includes_builtins(mocker):
    """The MCP built-in registry is surfaced through the same endpoint."""
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_toolsets",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets")
    assert ret.status_code == 200
    items = ret.json()["toolsets"]
    ids = {t["toolset_id"] for t in items}
    # Every registered built-in group should be visible.
    assert "__builtin_graph__" in ids
    assert "__builtin_reports__" in ids
    assert "__builtin_toolsets__" in ids


async def test_list_toolsets_empty_user_still_returns_builtins(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_toolsets",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets")
    assert ret.status_code == 200
    items = ret.json()["toolsets"]
    # No user toolsets, but built-ins are always listed.
    assert all(t["toolset_id"].startswith("__builtin_") for t in items)
    assert len(items) >= 1


# ---------------------------------------------------------------------------
# POST /api/v1/toolsets
# ---------------------------------------------------------------------------


async def test_create_toolset_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.create_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/toolsets", json=_VALID_TOOLSET_BODY)
    assert ret.status_code == 201
    assert ret.json()["toolset_id"] == _TS_ID


async def test_create_toolset_invalid_body(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/toolsets", json={"invalid": "body"})
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}
# ---------------------------------------------------------------------------


async def test_get_toolset_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}")
    assert ret.status_code == 200
    assert ret.json()["toolset_id"] == _TS_ID


async def test_get_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}")
    assert ret.status_code == 404
    assert "error" in ret.json()


# ---------------------------------------------------------------------------
# PUT /api/v1/toolsets/{toolset_id}
# ---------------------------------------------------------------------------


async def test_update_toolset_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.update_toolset",
        new=AsyncMock(return_value=_toolset_item(version=2)),
    )
    app = _make_app()
    body = {**_VALID_TOOLSET_BODY, "comment": "Updated"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/toolsets/{_TS_ID}", json=body)
    assert ret.status_code == 200
    assert ret.json()["current_version"] == 2


async def test_update_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.update_toolset",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/toolsets/{_TS_ID}", json=_VALID_TOOLSET_BODY)
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/toolsets/{toolset_id}
# ---------------------------------------------------------------------------


async def test_delete_toolset_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.delete_toolset",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/toolsets/{_TS_ID}")
    assert ret.status_code == 200
    assert ret.json()["toolset_id"] == _TS_ID


async def test_delete_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.delete_toolset",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/toolsets/{_TS_ID}")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/versions
# ---------------------------------------------------------------------------


async def test_list_toolset_versions_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_toolset_versions",
        new=AsyncMock(return_value=[_toolset_version()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/versions")
    assert ret.status_code == 200
    assert len(ret.json()["versions"]) == 1
    assert ret.json()["versions"][0]["version"] == 1


async def test_list_toolset_versions_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/versions")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/versions/{version}
# ---------------------------------------------------------------------------


async def test_get_toolset_version_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset_version",
        new=AsyncMock(return_value=_toolset_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/versions/1")
    assert ret.status_code == 200
    assert ret.json()["version"] == 1


async def test_get_toolset_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/versions/99")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/tools
# ---------------------------------------------------------------------------


async def test_list_tools_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_tools",
        new=AsyncMock(return_value=[_tool_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools")
    assert ret.status_code == 200
    assert len(ret.json()["tools"]) == 1
    assert ret.json()["tools"][0]["tool_id"] == _TOOL_ID
    assert ret.json()["tools"][0]["effective_enabled"] is True
    assert ret.json()["tools"][0]["disabled_reason"] is None


async def test_list_tools_marks_parent_disabled_tools_effectively_disabled(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item(enabled=False)),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_tools",
        new=AsyncMock(return_value=[_tool_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools")
    assert ret.status_code == 200
    item = ret.json()["tools"][0]
    assert item["enabled"] is True
    assert item["effective_enabled"] is False
    assert item["disabled_reason"] == "toolset_disabled"


async def test_list_tools_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/toolsets/{toolset_id}/tools
# ---------------------------------------------------------------------------


async def test_create_tool_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.toolsets.validate_tool_cypher",
        new=AsyncMock(return_value=ValidationResult()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.create_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/toolsets/{_TS_ID}/tools", json=_VALID_TOOL_BODY)
    assert ret.status_code == 201
    assert ret.json()["tool_id"] == _TOOL_ID


async def test_create_tool_cypher_validation_error(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.toolsets.validate_tool_cypher",
        new=AsyncMock(return_value=ValidationResult(errors=["Write queries are not allowed"])),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/toolsets/{_TS_ID}/tools", json=_VALID_TOOL_BODY)
    assert ret.status_code == 400
    assert "errors" in ret.json()
    assert ret.json()["errors"] == ["Write queries are not allowed"]


async def test_create_tool_toolset_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.toolsets.validate_tool_cypher",
        new=AsyncMock(return_value=ValidationResult()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.create_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/toolsets/{_TS_ID}/tools", json=_VALID_TOOL_BODY)
    assert ret.status_code == 404


async def test_create_tool_invalid_body(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/toolsets/{_TS_ID}/tools", json={"invalid": "body"})
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/tools/{tool_id}
# ---------------------------------------------------------------------------


async def test_get_tool_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}")
    assert ret.status_code == 200
    assert ret.json()["tool_id"] == _TOOL_ID


async def test_get_tool_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}")
    assert ret.status_code == 404


async def test_get_tool_wrong_toolset(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item(ts_id="other-toolset")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/v1/toolsets/{toolset_id}/tools/{tool_id}
# ---------------------------------------------------------------------------


async def test_update_tool_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.validate_tool_cypher",
        new=AsyncMock(return_value=ValidationResult()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.update_tool",
        new=AsyncMock(return_value=_tool_item(version=2)),
    )
    app = _make_app()
    body = {**_VALID_TOOL_BODY, "comment": "Updated"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}", json=body)
    assert ret.status_code == 200
    assert ret.json()["current_version"] == 2


async def test_update_tool_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}", json=_VALID_TOOL_BODY)
    assert ret.status_code == 404


async def test_update_tool_cypher_validation_error(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.validate_tool_cypher",
        new=AsyncMock(return_value=ValidationResult(errors=["Write queries are not allowed"])),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}", json=_VALID_TOOL_BODY)
    assert ret.status_code == 400
    assert "errors" in ret.json()


# ---------------------------------------------------------------------------
# DELETE /api/v1/toolsets/{toolset_id}/tools/{tool_id}
# ---------------------------------------------------------------------------


async def test_delete_tool_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.delete_tool",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}")
    assert ret.status_code == 200
    assert ret.json()["tool_id"] == _TOOL_ID


async def test_delete_tool_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/tools/{tool_id}/versions
# ---------------------------------------------------------------------------


async def test_list_tool_versions_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.list_tool_versions",
        new=AsyncMock(return_value=[_tool_version()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/versions")
    assert ret.status_code == 200
    assert len(ret.json()["versions"]) == 1
    assert ret.json()["versions"][0]["version"] == 1


async def test_list_tool_versions_tool_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/versions")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/toolsets/{toolset_id}/tools/{tool_id}/versions/{version}
# ---------------------------------------------------------------------------


async def test_get_tool_version_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool_version",
        new=AsyncMock(return_value=_tool_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/versions/1")
    assert ret.status_code == 200
    assert ret.json()["version"] == 1


async def test_get_tool_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/versions/99")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/toolsets/{toolset_id}/tools/{tool_id}/call
# ---------------------------------------------------------------------------


async def test_call_tool_success(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[{"n": "value1"}, {"n": "value2"}]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 200
    assert ret.json()["results"] == [{"n": "value1"}, {"n": "value2"}]


async def test_call_tool_with_arguments(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    run_query_mock = AsyncMock(return_value=[])
    mocker.patch("reporting.routes.toolsets.reporting_neo4j.run_query", new=run_query_mock)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {"limit": 10}},
        )
    assert ret.status_code == 200
    run_query_mock.assert_awaited_once_with("MATCH (n) RETURN n", parameters={"limit": 10})


async def test_call_tool_not_found(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 404


async def test_call_tool_wrong_toolset(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item(ts_id="other-toolset")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 404


async def test_call_tool_disabled(mocker):
    disabled = _tool_item()
    disabled.enabled = False
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=disabled),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 400
    assert "disabled" in ret.json()["error"].lower()


async def test_call_tool_rejects_disabled_toolset(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item(enabled=False)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 400
    assert "toolset" in ret.json()["error"].lower()
    assert "disabled" in ret.json()["error"].lower()


async def test_call_tool_execution_error(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=_toolset_item()),
    )
    mocker.patch(
        "reporting.routes.toolsets.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=Exception("Neo4j unavailable")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 500


async def test_get_tool_version_wrong_toolset(mocker):
    mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool_version",
        new=AsyncMock(return_value=_tool_version(ts_id="other-toolset")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/versions/1")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# Built-in (synthetic) toolset surfacing
# ---------------------------------------------------------------------------
# The MCP built-in registry is rendered through the same /api/v1/toolsets
# routes so the existing frontend hooks keep working without a separate
# endpoint.  These tests lock in that contract.


async def test_get_builtin_toolset(mocker):
    """A synthetic id resolves without touching report_store."""
    get_toolset = mocker.patch(
        "reporting.routes.toolsets.report_store.get_toolset",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets/__builtin_graph__")
    assert ret.status_code == 200
    body = ret.json()
    assert body["toolset_id"] == "__builtin_graph__"
    assert body["name"] == "graph"
    get_toolset.assert_not_awaited()


async def test_get_builtin_toolset_unknown_group_returns_404():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets/__builtin_nonexistent__")
    assert ret.status_code == 404


async def test_list_builtin_tools_returns_registry_tools(mocker):
    """Tool listing for a built-in toolset pulls from the registry."""
    list_tools = mocker.patch(
        "reporting.routes.toolsets.report_store.list_tools",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets/__builtin_graph__/tools")
    assert ret.status_code == 200
    tools = ret.json()["tools"]
    names = {t["name"] for t in tools}
    # seizu group ships schema + query.
    assert {"graph__schema", "graph__query"} <= names
    list_tools.assert_not_awaited()


async def test_get_builtin_tool(mocker):
    get_tool = mocker.patch(
        "reporting.routes.toolsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets/__builtin_graph__/tools/__builtin_graph__query__")
    assert ret.status_code == 200
    body = ret.json()
    assert body["name"] == "graph__query"
    # Query tool takes a single "query" string parameter.
    assert any(p["name"] == "query" for p in body["parameters"])
    get_tool.assert_not_awaited()


async def test_builtin_toolset_mutation_rejected():
    """Built-ins are read-only — update/delete return 403."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upd = await client.put(
            "/api/v1/toolsets/__builtin_graph__",
            json={"name": "pwned", "description": "", "enabled": True},
        )
        dele = await client.delete("/api/v1/toolsets/__builtin_graph__")
    assert upd.status_code == 403
    assert dele.status_code == 403


async def test_create_toolset_reserved_name_rejected():
    """IDs must use the lower_snake_case user-defined ID format."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/toolsets",
            json={"toolset_id": "__builtin_custom", "name": "Builtin Custom", "description": "", "enabled": True},
        )
    assert ret.status_code == 422


async def test_builtin_tool_mutation_rejected():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/v1/toolsets/__builtin_graph__/tools",
            json={
                "tool_id": "x",
                "name": "x",
                "description": "",
                "cypher": "MATCH (n) RETURN n",
                "parameters": [],
                "enabled": True,
            },
        )
    assert create.status_code == 403


async def test_call_tool_requires_tools_call_permission():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _UNPRIVILEGED_CURRENT_USER
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/toolsets/{_TS_ID}/tools/{_TOOL_ID}/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 403


async def test_call_builtin_tool_returns_clear_error():
    """Built-in tools are handler-backed — invoking them via REST is an error."""
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/toolsets/__builtin_graph__/tools/__builtin_graph__schema__/call",
            json={"arguments": {}},
        )
    assert ret.status_code == 400
    body = ret.json()
    # FastAPI's default HTTPException shape is `{"detail": ...}`, but routes
    # already declared response_model=CallToolResponse; either way the error
    # message must tell clients to use the MCP endpoint.
    assert "MCP" in str(body)


async def test_builtin_toolset_versions_empty():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/toolsets/__builtin_graph__/versions")
    assert ret.status_code == 200
    assert ret.json() == {"versions": []}
