"""Tests for the ``roles__*`` MCP built-in group."""

import json
from unittest.mock import AsyncMock, patch

from mcp import types as mcp_types

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.rbac import RoleItem, RoleVersion
from reporting.schema.report_config import User
from reporting.services.mcp_server import _build_mcp_server, _mcp_current_user, _mcp_permissions, _mcp_session_key

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


async def _call(server, name, arguments, permissions=None):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    perm_tok = _mcp_permissions.set(permissions if permissions is not None else ALL_PERMISSIONS)
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


def _role() -> RoleItem:
    return RoleItem(
        role_id="role1",
        name="custom",
        description="desc",
        permissions=["reports:read"],
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _role_version() -> RoleVersion:
    return RoleVersion(
        role_id="role1",
        name="custom",
        description="desc",
        permissions=["reports:read"],
        version=1,
        created_at=_NOW,
        created_by="u1",
    )


async def test_roles_list_builtin_returns_builtins():
    server = _build_mcp_server()
    result = await _call(server, "roles__list_builtin", {})
    data = json.loads(result[0].text)

    names = {r["name"] for r in data["roles"]}
    assert "seizu-admin" in names
    # Each entry carries a builtin: role_id
    assert all(r["role_id"].startswith("builtin:") for r in data["roles"])


async def test_roles_list_returns_user_roles():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.list_roles",
        new_callable=AsyncMock,
        return_value=[_role()],
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__list", {})
        data = json.loads(result[0].text)

    assert len(data["roles"]) == 1
    assert data["roles"][0]["role_id"] == "role1"


async def test_roles_get_returns_role():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.get_role",
        new_callable=AsyncMock,
        return_value=_role(),
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__get", {"role_id": "role1"})
        data = json.loads(result[0].text)

    assert data["role_id"] == "role1"


async def test_roles_get_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.get_role",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__get", {"role_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Role not found"}


async def test_roles_create_forwards_user_id():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.create_role",
        new_callable=AsyncMock,
        return_value=_role(),
    ) as mock_create:
        server = _build_mcp_server()
        result = await _call(
            server,
            "roles__create",
            {
                "name": "custom",
                "description": "desc",
                "permissions": ["reports:read"],
            },
        )
        data = json.loads(result[0].text)

    assert data["role_id"] == "role1"
    mock_create.assert_awaited_once_with(
        name="custom",
        description="desc",
        permissions=["reports:read"],
        created_by="u1",
    )


async def test_roles_update_returns_updated_role():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.update_role",
        new_callable=AsyncMock,
        return_value=_role(),
    ) as mock_update:
        server = _build_mcp_server()
        result = await _call(
            server,
            "roles__update",
            {
                "role_id": "role1",
                "name": "custom",
                "description": "desc",
                "permissions": ["reports:read"],
                "comment": "why",
            },
        )
        data = json.loads(result[0].text)

    assert data["role_id"] == "role1"
    mock_update.assert_awaited_once_with(
        role_id="role1",
        name="custom",
        description="desc",
        permissions=["reports:read"],
        updated_by="u1",
        comment="why",
    )


async def test_roles_update_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.update_role",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "roles__update",
            {
                "role_id": "nope",
                "name": "custom",
                "description": "desc",
                "permissions": ["reports:read"],
            },
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Role not found"}


async def test_roles_delete_returns_id():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.delete_role",
        new_callable=AsyncMock,
        return_value=True,
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__delete", {"role_id": "role1"})
        data = json.loads(result[0].text)

    assert data == {"role_id": "role1"}


async def test_roles_delete_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.delete_role",
        new_callable=AsyncMock,
        return_value=False,
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__delete", {"role_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Role not found"}


async def test_roles_list_versions_returns_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.roles.report_store.get_role",
            new_callable=AsyncMock,
            return_value=_role(),
        ),
        patch(
            "reporting.services.mcp_builtins.roles.report_store.list_role_versions",
            new_callable=AsyncMock,
            return_value=[_role_version()],
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__list_versions", {"role_id": "role1"})
        data = json.loads(result[0].text)

    assert len(data["versions"]) == 1
    assert data["versions"][0]["version"] == 1


async def test_roles_list_versions_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.get_role",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__list_versions", {"role_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Role not found"}


async def test_roles_get_version_returns_version():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.get_role_version",
        new_callable=AsyncMock,
        return_value=_role_version(),
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__get_version", {"role_id": "role1", "version": 1})
        data = json.loads(result[0].text)

    assert data["version"] == 1


async def test_roles_get_version_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.roles.report_store.get_role_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "roles__get_version", {"role_id": "role1", "version": 99})
        data = json.loads(result[0].text)

    assert data == {"error": "Role version not found"}


async def test_roles_create_requires_write_permission():
    readonly = frozenset({Permission.ROLES_READ.value})
    server = _build_mcp_server()
    result = await _call(
        server,
        "roles__create",
        {"name": "n", "description": "", "permissions": []},
        permissions=readonly,
    )
    data = json.loads(result[0].text)
    assert "Permission denied" in data["error"]
