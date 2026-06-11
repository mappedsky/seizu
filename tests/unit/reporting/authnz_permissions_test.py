"""Tests for reporting.authnz.permissions."""

from unittest.mock import AsyncMock

from reporting.authnz import CurrentUser, sync_user_profile
from reporting.authnz.permissions import (
    ADMIN_PERMISSIONS,
    ALL_PERMISSIONS,
    BUILTIN_ROLES,
    EDITOR_PERMISSIONS,
    VIEWER_PERMISSIONS,
    Permission,
    resolve_permissions,
)
from reporting.schema.report_config import User

# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------


def test_viewer_is_subset_of_editor():
    assert VIEWER_PERMISSIONS.issubset(EDITOR_PERMISSIONS)


def test_editor_is_subset_of_admin():
    assert EDITOR_PERMISSIONS.issubset(ADMIN_PERMISSIONS)


def test_viewer_can_read_but_not_execute_adhoc():
    assert Permission.REPORTS_READ in VIEWER_PERMISSIONS
    assert Permission.TOOLS_CALL in VIEWER_PERMISSIONS
    assert Permission.CHAT_USE in VIEWER_PERMISSIONS
    assert Permission.CHAT_TOOLS_CALL not in VIEWER_PERMISSIONS
    assert Permission.CHAT_SKILLS_CALL not in VIEWER_PERMISSIONS
    assert Permission.QUERY_EXECUTE not in VIEWER_PERMISSIONS
    assert Permission.QUERY_VALIDATE not in VIEWER_PERMISSIONS
    assert Permission.QUERY_HISTORY_READ not in VIEWER_PERMISSIONS


def test_viewer_cannot_write():
    assert Permission.REPORTS_WRITE not in VIEWER_PERMISSIONS
    assert Permission.TOOLSETS_WRITE not in VIEWER_PERMISSIONS
    assert Permission.SCHEDULED_QUERIES_WRITE not in VIEWER_PERMISSIONS


def test_editor_can_write_reports():
    assert Permission.REPORTS_WRITE in EDITOR_PERMISSIONS
    assert Permission.REPORTS_DELETE in EDITOR_PERMISSIONS
    assert Permission.REPORTS_SET_DASHBOARD in EDITOR_PERMISSIONS
    assert Permission.QUERY_EXECUTE in EDITOR_PERMISSIONS
    assert Permission.QUERY_VALIDATE in EDITOR_PERMISSIONS
    assert Permission.QUERY_HISTORY_READ in EDITOR_PERMISSIONS


def test_editor_cannot_manage_toolsets_or_scheduled_queries():
    assert Permission.TOOLSETS_WRITE not in EDITOR_PERMISSIONS
    assert Permission.SCHEDULED_QUERIES_WRITE not in EDITOR_PERMISSIONS
    assert Permission.ROLES_WRITE not in EDITOR_PERMISSIONS


def test_admin_has_all_admin_permissions():
    assert Permission.TOOLSETS_WRITE in ADMIN_PERMISSIONS
    assert Permission.TOOLSETS_DELETE in ADMIN_PERMISSIONS
    assert Permission.CHAT_TOOLS_CALL in ADMIN_PERMISSIONS
    assert Permission.CHAT_SKILLS_CALL in ADMIN_PERMISSIONS
    assert Permission.SCHEDULED_QUERIES_WRITE in ADMIN_PERMISSIONS
    assert Permission.SCHEDULED_QUERIES_DELETE in ADMIN_PERMISSIONS
    assert Permission.ROLES_WRITE in ADMIN_PERMISSIONS
    assert Permission.ROLES_DELETE in ADMIN_PERMISSIONS


def test_all_permissions_is_union_of_all():
    assert ALL_PERMISSIONS == frozenset(p.value for p in Permission)


def test_builtin_roles_keys():
    assert set(BUILTIN_ROLES.keys()) == {"seizu-viewer", "seizu-editor", "seizu-admin"}


# ---------------------------------------------------------------------------
# resolve_permissions — built-in roles (no store I/O)
# ---------------------------------------------------------------------------


async def test_resolve_permissions_builtin_viewer(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    perms = await resolve_permissions({"seizu_role": "seizu-viewer"})
    assert "reports:read" in perms
    assert "tools:call" in perms
    assert "reports:write" not in perms


async def test_resolve_permissions_builtin_editor(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    perms = await resolve_permissions({"seizu_role": "seizu-editor"})
    assert "reports:write" in perms
    assert "reports:read" in perms
    assert "toolsets:write" not in perms


async def test_resolve_permissions_builtin_admin(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    perms = await resolve_permissions({"seizu_role": "seizu-admin"})
    assert "toolsets:write" in perms
    assert "roles:write" in perms
    assert "scheduled_queries:write" in perms
    # Admin is a superset — also has viewer permissions
    assert "reports:read" in perms
    assert "tools:call" in perms


async def test_resolve_permissions_builtin_no_store_call(mocker):
    """Built-in role resolution must not touch the store."""
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    mock_get_role = mocker.patch(
        "reporting.services.report_store.get_role_by_name",
        new=AsyncMock(),
    )
    await resolve_permissions({"seizu_role": "seizu-admin"})
    mock_get_role.assert_not_called()


# ---------------------------------------------------------------------------
# resolve_permissions — fallback to RBAC_DEFAULT_ROLE
# ---------------------------------------------------------------------------


async def test_resolve_permissions_no_claim_falls_back_to_default(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    perms = await resolve_permissions({})
    assert "reports:read" in perms
    assert "reports:write" not in perms


async def test_resolve_permissions_empty_default_returns_empty(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "")
    perms = await resolve_permissions({})
    assert perms == frozenset()


async def test_resolve_permissions_none_claim_value_falls_back(mocker):
    """A claim present but falsy (None/empty) falls back to RBAC_DEFAULT_ROLE."""
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-editor")
    perms = await resolve_permissions({"seizu_role": ""})
    assert "reports:write" in perms


# ---------------------------------------------------------------------------
# resolve_permissions — user-defined roles (single store lookup)
# ---------------------------------------------------------------------------


async def test_resolve_permissions_user_defined_role(mocker):
    from reporting.schema.rbac import RoleItem

    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    fake_role = RoleItem(
        role_id="r1",
        name="Custom Analyst",
        permissions=["reports:read", "query:execute"],
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="uid1",
    )
    mocker.patch(
        "reporting.services.report_store.get_role_by_name",
        new=AsyncMock(return_value=fake_role),
    )
    perms = await resolve_permissions({"seizu_role": "Custom Analyst"})
    assert perms == frozenset({"reports:read", "query:execute"})


async def test_resolve_permissions_unknown_role_returns_empty(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    mocker.patch(
        "reporting.services.report_store.get_role_by_name",
        new=AsyncMock(return_value=None),
    )
    perms = await resolve_permissions({"seizu_role": "no-such-role"})
    assert perms == frozenset()


# ---------------------------------------------------------------------------
# resolve_permissions — configurable claim name
# ---------------------------------------------------------------------------


async def test_resolve_permissions_respects_custom_claim_name(mocker):
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "custom_role_claim")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "")
    perms = await resolve_permissions({"custom_role_claim": "seizu-admin"})
    assert "toolsets:write" in perms


async def test_resolve_permissions_ignores_wrong_claim_name(mocker):
    """If RBAC_ROLE_CLAIM doesn't match, falls back to default."""
    mocker.patch("reporting.settings.RBAC_ROLE_CLAIM", "seizu_role")
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")
    # JWT has the role under a different key
    perms = await resolve_permissions({"role": "seizu-admin"})
    # Should get Viewer (default), not Admin
    assert "reports:read" in perms
    assert "toolsets:write" not in perms


# ---------------------------------------------------------------------------
# sync_user_profile
# ---------------------------------------------------------------------------

_FAKE_USER = User(
    user_id="user-1",
    sub="sub",
    iss="https://idp.example.com",
    email="u@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)


async def test_sync_user_profile_calls_update_and_returns_updated_current_user(mocker):
    updated_user = _FAKE_USER.model_copy(update={"email": "new@example.com"})
    mock_update = mocker.patch(
        "reporting.services.report_store.update_user_profile",
        new=AsyncMock(return_value=updated_user),
    )
    current = CurrentUser(
        user=_FAKE_USER,
        jwt_claims={"email": "new@example.com", "display_name": "New Name"},
        permissions=VIEWER_PERMISSIONS,
    )

    result = await sync_user_profile(current)

    mock_update.assert_awaited_once_with(
        user_id="user-1",
        email="new@example.com",
        display_name="New Name",
        preferred_username=None,
        token_iat=None,
    )
    assert result.user.email == "new@example.com"
    assert result.permissions == VIEWER_PERMISSIONS


def test_chat_bypass_permissions_role_membership():
    assert Permission.CHAT_BYPASS_PERMISSIONS in EDITOR_PERMISSIONS
    assert Permission.CHAT_BYPASS_PERMISSIONS in ADMIN_PERMISSIONS
    assert Permission.CHAT_BYPASS_PERMISSIONS not in VIEWER_PERMISSIONS
