from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS, VIEWER_PERMISSIONS
from reporting.schema.rbac import RoleItem, RoleVersion
from reporting.schema.report_config import User

_FAKE_USER = User(
    user_id="uid1",
    sub="sub123",
    iss="https://idp.example.com",
    email="alice@example.com",
    display_name="Alice Smith",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_ADMIN_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)
_VIEWER_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={},
    permissions=frozenset(p.value for p in VIEWER_PERMISSIONS),
)

_NOW = "2024-01-01T00:00:00+00:00"

_FAKE_ROLE = RoleItem(
    role_id="role1",
    name="Custom Role",
    description="A test role",
    permissions=["reports:read", "query:execute"],
    current_version=1,
    created_at=_NOW,
    updated_at=_NOW,
    created_by="uid1",
)

_FAKE_ROLE_VERSION = RoleVersion(
    role_id="role1",
    name="Custom Role",
    permissions=["reports:read"],
    version=1,
    created_at=_NOW,
    created_by="uid1",
)


def _make_app(current_user: CurrentUser = _ADMIN_USER) -> object:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


# ---------------------------------------------------------------------------
# GET /api/v1/roles/builtin
# ---------------------------------------------------------------------------


async def test_list_builtin_roles(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/builtin")
    assert ret.status_code == 200
    names = [r["name"] for r in ret.json()["roles"]]
    assert "seizu-viewer" in names
    assert "seizu-editor" in names
    assert "seizu-admin" in names


async def test_list_builtin_roles_includes_permissions(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/builtin")
    roles_by_name = {r["name"]: r for r in ret.json()["roles"]}
    assert "reports:read" in roles_by_name["seizu-viewer"]["permissions"]
    assert "reports:write" in roles_by_name["seizu-editor"]["permissions"]
    assert "toolsets:write" in roles_by_name["seizu-admin"]["permissions"]


async def test_list_builtin_roles_forbidden_without_permission(mocker):
    app = _make_app(current_user=CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=frozenset()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/builtin")
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/roles
# ---------------------------------------------------------------------------


async def test_list_roles(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.list_roles",
        new=AsyncMock(return_value=[_FAKE_ROLE]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles")
    assert ret.status_code == 200
    assert len(ret.json()["roles"]) == 1
    assert ret.json()["roles"][0]["role_id"] == "role1"


async def test_list_roles_empty(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.list_roles",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles")
    assert ret.status_code == 200
    assert ret.json()["roles"] == []


async def test_list_roles_forbidden_without_permission(mocker):
    app = _make_app(current_user=CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=frozenset()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles")
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/roles
# ---------------------------------------------------------------------------


async def test_create_role(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.create_role",
        new=AsyncMock(return_value=_FAKE_ROLE),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/roles",
            json={"name": "Custom Role", "permissions": ["reports:read"]},
        )
    assert ret.status_code == 201
    assert ret.json()["role_id"] == "role1"
    assert ret.json()["name"] == "Custom Role"


async def test_create_role_forbidden_for_viewer(mocker):
    app = _make_app(current_user=_VIEWER_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/roles",
            json={"name": "Custom Role", "permissions": ["reports:read"]},
        )
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/roles/{role_id}
# ---------------------------------------------------------------------------


async def test_get_role(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role",
        new=AsyncMock(return_value=_FAKE_ROLE),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/role1")
    assert ret.status_code == 200
    assert ret.json()["role_id"] == "role1"


async def test_get_role_not_found(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/nonexistent")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/v1/roles/{role_id}
# ---------------------------------------------------------------------------


async def test_update_role(mocker):
    updated = _FAKE_ROLE.model_copy(update={"name": "Updated Role"})
    mocker.patch(
        "reporting.routes.roles.report_store.update_role",
        new=AsyncMock(return_value=updated),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/roles/role1",
            json={"name": "Updated Role", "permissions": ["reports:read"]},
        )
    assert ret.status_code == 200
    assert ret.json()["name"] == "Updated Role"


async def test_update_role_not_found(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.update_role",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/roles/nonexistent",
            json={"name": "X", "permissions": []},
        )
    assert ret.status_code == 404


async def test_update_role_forbidden_for_viewer(mocker):
    app = _make_app(current_user=_VIEWER_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/roles/role1",
            json={"name": "X", "permissions": []},
        )
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/roles/{role_id}
# ---------------------------------------------------------------------------


async def test_delete_role(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.delete_role",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/roles/role1")
    assert ret.status_code == 200
    assert ret.json()["role_id"] == "role1"


async def test_delete_role_not_found(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.delete_role",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/roles/nonexistent")
    assert ret.status_code == 404


async def test_delete_role_forbidden_for_viewer(mocker):
    app = _make_app(current_user=_VIEWER_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/roles/role1")
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/roles/{role_id}/versions
# ---------------------------------------------------------------------------


async def test_list_role_versions(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role",
        new=AsyncMock(return_value=_FAKE_ROLE),
    )
    mocker.patch(
        "reporting.routes.roles.report_store.list_role_versions",
        new=AsyncMock(return_value=[_FAKE_ROLE_VERSION]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/role1/versions")
    assert ret.status_code == 200
    assert len(ret.json()["versions"]) == 1
    assert ret.json()["versions"][0]["version"] == 1


async def test_list_role_versions_role_not_found(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/nonexistent/versions")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/roles/{role_id}/versions/{version}
# ---------------------------------------------------------------------------


async def test_get_role_version(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role_version",
        new=AsyncMock(return_value=_FAKE_ROLE_VERSION),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/role1/versions/1")
    assert ret.status_code == 200
    assert ret.json()["version"] == 1
    assert ret.json()["role_id"] == "role1"


async def test_get_role_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.roles.report_store.get_role_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/roles/role1/versions/99")
    assert ret.status_code == 404


def test_role_version_coerces_none_version_to_zero():
    from reporting.schema.rbac import RoleVersion

    rv = RoleVersion(
        role_id="r1",
        name="Test",
        version=None,  # type: ignore[arg-type]
        created_at="2024-01-01T00:00:00+00:00",
        created_by="u1",
    )
    assert rv.version == 0
