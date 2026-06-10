import pytest

from reporting.authnz.headless import HeadlessIdentityError, resolve_stored_user
from reporting.authnz.permissions import ALL_PERMISSIONS, BUILTIN_ROLES
from reporting.schema.report_config import User

_NOW = "2024-01-01T00:00:00+00:00"


def _user(role: str | None = None, archived_at: str | None = None) -> User:
    return User(
        user_id="user-1",
        sub="sub",
        iss="iss",
        email="user@example.com",
        created_at=_NOW,
        last_login=_NOW,
        archived_at=archived_at,
        role=role,
    )


async def test_missing_user_raises(mocker):
    mocker.patch("reporting.services.report_store.get_user", mocker.AsyncMock(return_value=None))
    mocker.patch("reporting.authnz.headless.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    with pytest.raises(HeadlessIdentityError):
        await resolve_stored_user("user-1")


async def test_archived_user_raises(mocker):
    mocker.patch(
        "reporting.services.report_store.get_user",
        mocker.AsyncMock(return_value=_user(role="seizu-admin", archived_at=_NOW)),
    )
    mocker.patch("reporting.authnz.headless.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    with pytest.raises(HeadlessIdentityError):
        await resolve_stored_user("user-1")


async def test_stored_role_resolves_permissions(mocker):
    mocker.patch(
        "reporting.services.report_store.get_user",
        mocker.AsyncMock(return_value=_user(role="seizu-admin")),
    )
    mocker.patch("reporting.authnz.headless.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)

    current = await resolve_stored_user("user-1")

    assert current.user.user_id == "user-1"
    assert current.permissions == frozenset(p.value for p in BUILTIN_ROLES["seizu-admin"])


async def test_no_stored_role_falls_back_to_default_role(mocker):
    mocker.patch(
        "reporting.services.report_store.get_user",
        mocker.AsyncMock(return_value=_user(role=None)),
    )
    mocker.patch("reporting.authnz.headless.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    mocker.patch("reporting.settings.RBAC_DEFAULT_ROLE", "seizu-viewer")

    current = await resolve_stored_user("user-1")

    assert current.permissions == frozenset(p.value for p in BUILTIN_ROLES["seizu-viewer"])


async def test_dev_mode_grants_all_permissions(mocker):
    mocker.patch(
        "reporting.services.report_store.get_user",
        mocker.AsyncMock(return_value=_user(role=None)),
    )
    mocker.patch("reporting.authnz.headless.settings.DEVELOPMENT_ONLY_REQUIRE_AUTH", False)

    current = await resolve_stored_user("user-1")

    assert current.permissions == ALL_PERMISSIONS
