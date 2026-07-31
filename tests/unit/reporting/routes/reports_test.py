from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting import settings
from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion, User
from reporting.schema.space_config import SpaceListItem, SubspaceItem
from reporting.services.spaces import SPACE_MEMBER_ACCESS

settings.REPORT_QUERY_SIGNING_SECRET = "test-secret"

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="user@example.com",
    display_name="Test User",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_FAKE_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
    permissions=ALL_PERMISSIONS,
)


_UNPRIVILEGED_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
    permissions=frozenset(),
)


def _make_current_user(user_id: str = "test-user-id", email: str | None = None) -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id=user_id,
            sub="sub123",
            iss="https://idp.example.com",
            email=email or f"{user_id}@example.com",
            display_name="Test User" if user_id == "test-user-id" else "Other User",
            created_at="2024-01-01T00:00:00+00:00",
            last_login="2024-01-01T00:00:00+00:00",
        ),
        jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
        permissions=ALL_PERMISSIONS,
    )


def _make_app(current_user: CurrentUser = _FAKE_CURRENT_USER):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def _report_list_item(
    report_id="rid1",
    name="My Report",
    current_version=1,
    space_id=None,
    subspace_id=None,
    access=None,
):
    return ReportListItem(
        report_id=report_id,
        name=name,
        current_version=current_version,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
        access=access or {"scope": "public"},
        space_id=space_id,
        subspace_id=subspace_id,
    )


def _report_version(report_id="rid1", version=1):
    return ReportVersion(
        report_id=report_id,
        name="My Report",
        version=version,
        config={"rows": []},
        created_at="2024-01-01T00:00:00+00:00",
        created_by="user@example.com",
        comment=None,
        report_created_by="test-user-id",
        report_updated_by="test-user-id",
        access={"scope": "public"},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/reports
# ---------------------------------------------------------------------------


async def test_list_reports_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.list_reports",
        new=AsyncMock(return_value=[_report_list_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports")
    assert ret.status_code == 200
    reports = ret.json()["reports"]
    assert len(reports) == 1
    assert reports[0]["report_id"] == "rid1"
    assert reports[0]["name"] == "My Report"
    assert reports[0]["current_version"] == 1
    assert ret.json()["total"] == 1
    assert ret.json()["page"] == 1
    assert ret.json()["per_page"] == 100


async def test_list_reports_paginates(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.list_reports",
        new=AsyncMock(
            return_value=[
                _report_list_item(report_id="rid1", name="Report 1"),
                _report_list_item(report_id="rid2", name="Report 2"),
                _report_list_item(report_id="rid3", name="Report 3"),
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports?page=2&per_page=1")
    assert ret.status_code == 200
    body = ret.json()
    assert body["total"] == 3
    assert body["page"] == 2
    assert body["per_page"] == 1
    assert [report["report_id"] for report in body["reports"]] == ["rid2"]


async def test_list_reports_empty(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.list_reports",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports")
    assert ret.status_code == 200
    assert ret.json()["reports"] == []
    assert ret.json()["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/v1/reports/dashboard
# ---------------------------------------------------------------------------


async def test_get_dashboard_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report",
        new=AsyncMock(return_value=_report_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/dashboard")
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"
    assert ret.json()["version"] == 1
    assert "query_capabilities" not in ret.json()


async def test_get_dashboard_report_with_query_capabilities(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report",
        new=AsyncMock(return_value=_report_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/dashboard?include_query_capabilities=true")
    assert ret.status_code == 200
    assert ret.json()["query_capabilities"] == {}


async def test_get_dashboard_report_not_configured(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/dashboard")
    assert ret.status_code == 404
    assert "dashboard" in ret.json()["error"].lower()


# ---------------------------------------------------------------------------
# PUT /api/v1/reports/<report_id>/dashboard
# ---------------------------------------------------------------------------


async def test_set_dashboard_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.set_dashboard_report",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/dashboard")
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"


async def test_set_dashboard_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.set_dashboard_report",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/missing/dashboard")
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_set_dashboard_report_rejects_private(mocker):
    private = _report_list_item()
    private.access.scope = "private"
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=private),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/dashboard")
    assert ret.status_code == 400
    assert "public" in ret.json()["error"].lower()


# ---------------------------------------------------------------------------
# PUT /api/v1/reports/<report_id>/visibility
# ---------------------------------------------------------------------------


async def test_update_report_visibility_passes_access_to_service(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_visibility",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/visibility", json={"access": {"scope": "public"}})

    assert ret.status_code == 200
    mock_update.assert_called_once_with(
        report_id="rid1",
        updated_by="test-user-id",
        access=ReportAccess(scope="public"),
    )


async def test_update_report_visibility_rejects_unpublish_when_pinned(mocker):
    report = _report_list_item()
    report.pinned = True
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=report),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report_id",
        new=AsyncMock(return_value=None),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_visibility",
        new=AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/visibility", json={"access": {"scope": "private"}})

    assert ret.status_code == 400
    assert "private" in ret.json()["error"].lower()
    mock_update.assert_not_called()


async def test_update_report_visibility_rejects_unpublish_when_dashboard(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report_id",
        new=AsyncMock(return_value="rid1"),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_visibility",
        new=AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/visibility", json={"access": {"scope": "private"}})

    assert ret.status_code == 400
    assert "private" in ret.json()["error"].lower()
    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/v1/reports/<report_id>
# ---------------------------------------------------------------------------


async def test_get_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=_report_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1")
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"
    assert ret.json()["version"] == 1
    assert "query_capabilities" not in ret.json()
    assert ret.json()["config"] == {"rows": []}


async def test_get_report_with_query_capabilities(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=_report_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1?include_query_capabilities=true")
    assert ret.status_code == 200
    assert ret.json()["query_capabilities"] == {}


async def test_get_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/missing")
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_get_private_report_returns_404_for_non_owner(mocker):
    async def _get_report_latest(report_id: str, user_id: str | None = None):
        if user_id != "test-user-id":
            return None
        return _report_version()

    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(side_effect=_get_report_latest),
    )
    app = _make_app(_make_current_user(user_id="other-user"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/reports/<report_id>/versions
# ---------------------------------------------------------------------------


async def test_list_versions_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.list_report_versions",
        new=AsyncMock(return_value=[_report_version(version=2), _report_version(version=1)]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions")
    assert ret.status_code == 200
    versions = ret.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1


async def test_list_versions_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.list_report_versions",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/missing/versions")
    assert ret.status_code == 404


async def test_list_versions_private_report_returns_404_for_non_owner(mocker):
    async def _list_report_versions(report_id: str, user_id: str | None = None):
        if user_id != "test-user-id":
            return []
        return [_report_version(version=2), _report_version(version=1)]

    mocker.patch(
        "reporting.routes.reports.report_store.list_report_versions",
        new=AsyncMock(side_effect=_list_report_versions),
    )
    app = _make_app(_make_current_user(user_id="other-user"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/reports/<report_id>/versions/<version_num>
# ---------------------------------------------------------------------------


async def test_get_version_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_version",
        new=AsyncMock(return_value=_report_version(version=3)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions/3")
    assert ret.status_code == 200
    assert ret.json()["version"] == 3
    assert "query_capabilities" not in ret.json()


async def test_get_version_with_query_capabilities(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_version",
        new=AsyncMock(return_value=_report_version(version=3)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions/3?include_query_capabilities=true")
    assert ret.status_code == 200
    assert ret.json()["query_capabilities"] == {}


async def test_get_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions/99")
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_get_version_private_report_returns_404_for_non_owner(mocker):
    async def _get_report_version(report_id: str, version_num: int, user_id: str | None = None):
        if user_id != "test-user-id":
            return None
        return _report_version(version=version_num)

    mocker.patch(
        "reporting.routes.reports.report_store.get_report_version",
        new=AsyncMock(side_effect=_get_report_version),
    )
    app = _make_app(_make_current_user(user_id="other-user"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/reports/rid1/versions/3")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/reports
# ---------------------------------------------------------------------------


async def test_create_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports", json={"name": "My Report"})
    assert ret.status_code == 201
    assert ret.json()["report_id"] == "rid1"
    assert ret.json()["name"] == "My Report"
    assert ret.json()["current_version"] == 1


async def test_create_version_with_query_capabilities(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(version=2)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/rid1/versions?include_query_capabilities=true",
            json={"config": {"name": "Test Report", "rows": []}},
        )
    assert ret.status_code == 201
    assert ret.json()["query_capabilities"] == {}


async def test_create_report_passes_fields_to_service(mocker):
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/reports", json={"name": "My Report"})
    mock_create.assert_called_once_with(
        name="My Report",
        created_by="test-user-id",
        # No space, so no forced visibility: the store's private default stands.
        access=None,
        space_id=None,
        subspace_id=None,
    )


async def test_create_report_missing_required_fields(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports", json={})
    assert ret.status_code == 422


async def test_create_report_non_json_body(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/reports/<report_id>/versions
# ---------------------------------------------------------------------------


async def test_create_version_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(version=2)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/rid1/versions",
            json={"config": {"name": "Test Report", "rows": []}, "comment": "v2"},
        )
    assert ret.status_code == 201
    assert ret.json()["version"] == 2


async def test_create_version_passes_fields_to_service(mocker):
    mock_save = mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(version=2)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/reports/rid1/versions",
            json={
                "config": {"name": "Test Report", "rows": [{"name": "r", "panels": []}]},
                "comment": "update",
            },
        )
    mock_save.assert_called_once_with(
        report_id="rid1",
        config={"name": "Test Report", "rows": [{"name": "r", "panels": []}]},
        created_by="test-user-id",
        comment="update",
        user_id="test-user-id",
    )


async def test_create_version_passes_config_name_to_service(mocker):
    mock_save = mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(version=2)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/reports/rid1/versions",
            json={"config": {"name": "Renamed Report", "rows": []}, "comment": "rename"},
        )
    mock_save.assert_called_once_with(
        report_id="rid1",
        config={"name": "Renamed Report", "rows": []},
        created_by="test-user-id",
        comment="rename",
        user_id="test-user-id",
    )


async def test_create_version_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/missing/versions",
            json={"config": {"name": "Test Report", "queries": {}, "rows": []}},
        )
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_create_version_private_report_returns_404_for_non_owner(mocker):
    async def _save_report_version(
        report_id: str,
        config: dict[str, object],
        created_by: str,
        comment: str | None = None,
        user_id: str | None = None,
    ):
        if user_id != "test-user-id":
            return None
        return _report_version(version=2)

    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(side_effect=_save_report_version),
    )
    app = _make_app(_make_current_user(user_id="other-user"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/rid1/versions", json={"config": {"name": "Test Report", "rows": []}})
    assert ret.status_code == 404


async def test_create_version_missing_config_field(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/rid1/versions",
            json={"comment": "no config"},
        )
    assert ret.status_code == 422


async def test_create_version_rejects_top_level_panels(mocker):
    mock_save = mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/rid1/versions",
            json={
                "config": {
                    "name": "Invalid Report",
                    "panels": [{"type": "markdown", "content": "Not nested under a row"}],
                }
            },
        )

    assert ret.status_code == 422
    assert "panels must be nested under 'rows[].panels'" in str(ret.json())
    mock_save.assert_not_awaited()


async def test_create_version_non_json_body(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports/rid1/versions",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/reports/<report_id>
# ---------------------------------------------------------------------------


async def test_delete_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.delete_report",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/reports/rid1")
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"


async def test_delete_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.delete_report",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/reports/missing")
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


# ---------------------------------------------------------------------------
# PUT /api/v1/reports/<report_id>/pin
# ---------------------------------------------------------------------------


async def test_pin_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.pin_report",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/pin", json={"pinned": True})
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"


async def test_unpin_report_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.pin_report",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/pin", json={"pinned": False})
    assert ret.status_code == 200
    assert ret.json()["report_id"] == "rid1"


async def test_pin_report_passes_fields_to_service(mocker):
    mock_pin = mocker.patch(
        "reporting.routes.reports.report_store.pin_report",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.put("/api/v1/reports/rid1/pin", json={"pinned": True})
    mock_pin.assert_called_once_with("rid1", True, updated_by="test-user-id", user_id="test-user-id")


async def test_pin_report_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.pin_report",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/missing/pin", json={"pinned": True})
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_pin_report_missing_body(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/pin")
    assert ret.status_code == 422


async def test_pin_report_wrong_type(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/pin", json={})
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/reports/<report_id>/clone
# ---------------------------------------------------------------------------


async def test_clone_report_success(mocker):
    source = _report_version(report_id="src1")
    new_item = _report_list_item(report_id="new1", name="Copy of My Report")
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=source),
    )
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=new_item),
    )
    mock_save = mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(report_id="new1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/src1/clone", json={"name": "Copy of My Report"})
    assert ret.status_code == 201
    assert ret.json()["report_id"] == "new1"
    assert ret.json()["name"] == "Copy of My Report"
    mock_create.assert_called_once_with(
        name="Copy of My Report",
        created_by="test-user-id",
        access=None,
        space_id=None,
        subspace_id=None,
    )
    mock_save.assert_called_once_with(
        report_id="new1",
        config={**source.config, "name": "Copy of My Report"},
        created_by="test-user-id",
        comment="Cloned from My Report",
        user_id="test-user-id",
    )


async def test_clone_report_source_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/missing/clone", json={"name": "Clone"})
    assert ret.status_code == 404
    assert "not found" in ret.json()["error"].lower()


async def test_clone_report_missing_name(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/src1/clone", json={})
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# PUT /api/v1/reports/<report_id>/space
# ---------------------------------------------------------------------------


def _space(space_id="sp1"):
    return SpaceListItem(
        space_id=space_id,
        name="Cloud",
        description="",
        overview_report_id="ovr1",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
    )


def _subspace(subspace_id="ss1", space_id="sp1"):
    return SubspaceItem(
        subspace_id=subspace_id,
        space_id=space_id,
        name="Network",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
    )


async def test_update_report_space_success(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch("reporting.services.report_store.get_subspace", new=AsyncMock(return_value=_subspace()))
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1", subspace_id="ss1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/reports/rid1/space",
            json={"space_id": "sp1", "subspace_id": "ss1"},
        )
    assert ret.status_code == 200
    assert ret.json()["space_id"] == "sp1"
    assert ret.json()["subspace_id"] == "ss1"
    mock_update.assert_called_once_with(
        report_id="rid1",
        space_id="sp1",
        subspace_id="ss1",
        updated_by="test-user-id",
        user_id="test-user-id",
    )


async def test_update_report_space_omitted_subspace_clears_it(mocker):
    """Replace semantics: omitting subspace_id on a move clears it.

    This is the rule that makes "moving to another space drops the sub-space"
    work without special-casing, so it is asserted on the omitted-key form
    rather than an explicit null.
    """
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space("spB")))
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=_report_list_item(space_id="spB")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={"space_id": "spB"})
    assert ret.status_code == 200
    assert mock_update.call_args.kwargs["subspace_id"] is None


async def test_update_report_space_clears_membership(mocker):
    """Unfiling needs no access check, so it must not read the report first."""
    mock_meta = mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=None),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={})
    assert ret.status_code == 200
    assert mock_update.call_args.kwargs["space_id"] is None
    assert mock_update.call_args.kwargs["subspace_id"] is None
    mock_meta.assert_not_called()


async def test_update_report_space_rejects_subspace_without_space(mocker):
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={"subspace_id": "ss1"})
    # 400, not 422: the body is well-formed, the pairing is not valid.
    assert ret.status_code == 400
    assert "without a space" in ret.json()["error"]
    mock_update.assert_not_called()


async def test_update_report_space_rejects_unknown_space(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={"space_id": "missing"})
    assert ret.status_code == 400
    assert "Space not found" in ret.json()["error"]


async def test_update_report_space_rejects_subspace_from_another_space(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item()),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space("sp1")))
    mocker.patch(
        "reporting.services.report_store.get_subspace",
        new=AsyncMock(return_value=_subspace("ss1", space_id="sp2")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/reports/rid1/space",
            json={"space_id": "sp1", "subspace_id": "ss1"},
        )
    assert ret.status_code == 400
    assert "does not belong" in ret.json()["error"]


async def test_update_report_space_not_found(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/missing/space", json={})
    assert ret.status_code == 404


async def test_update_report_space_requires_reports_write():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _UNPRIVILEGED_CURRENT_USER
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={})
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# Space membership on create / clone / visibility
# ---------------------------------------------------------------------------


async def test_create_report_with_space_membership(mocker):
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch("reporting.services.report_store.get_subspace", new=AsyncMock(return_value=_subspace()))
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1", subspace_id="ss1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/reports",
            json={"name": "My Report", "space_id": "sp1", "subspace_id": "ss1"},
        )
    assert ret.status_code == 201
    mock_create.assert_called_once_with(
        name="My Report",
        created_by="test-user-id",
        # Landing in a space publishes: space members are public.
        access=SPACE_MEMBER_ACCESS,
        space_id="sp1",
        subspace_id="ss1",
    )


async def test_create_report_rejects_invalid_space(mocker):
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=None))
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports", json={"name": "My Report", "space_id": "missing"})
    assert ret.status_code == 400
    mock_create.assert_not_called()


async def test_clone_report_inherits_source_space(mocker):
    source = _report_version(report_id="src1")
    source = source.model_copy(update={"space_id": "sp1", "subspace_id": "ss1"})
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=source),
    )
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item(report_id="new1")),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(report_id="new1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/src1/clone", json={"name": "Copy"})
    assert ret.status_code == 201
    mock_create.assert_called_once_with(
        name="Copy",
        created_by="test-user-id",
        access=SPACE_MEMBER_ACCESS,
        space_id="sp1",
        subspace_id="ss1",
    )


async def test_clone_report_honours_explicit_space(mocker):
    source = _report_version(report_id="src1").model_copy(update={"space_id": "spA", "subspace_id": "ssA"})
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_latest",
        new=AsyncMock(return_value=source),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space("spB")))
    mock_create = mocker.patch(
        "reporting.routes.reports.report_store.create_report",
        new=AsyncMock(return_value=_report_list_item(report_id="new1")),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.save_report_version",
        new=AsyncMock(return_value=_report_version(report_id="new1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/reports/src1/clone", json={"name": "Copy", "space_id": "spB"})
    assert ret.status_code == 201
    assert mock_create.call_args.kwargs["space_id"] == "spB"
    assert mock_create.call_args.kwargs["subspace_id"] is None


async def test_update_report_space_rejects_filing_a_draft(mocker):
    """A private report cannot be filed into a space (409, not 400).

    The body is well formed and names a real space; it is the report's current
    visibility that conflicts.
    """
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item(access={"scope": "private"})),
    )
    mocker.patch("reporting.services.report_store.get_space", new=AsyncMock(return_value=_space()))
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={"space_id": "sp1"})
    assert ret.status_code == 409
    assert "Publish the report" in ret.json()["error"]
    mock_update.assert_not_called()


async def test_update_report_space_unfiles_a_draft(mocker):
    """Removing a report from a space is allowed whatever its visibility.

    Only filing is gated, so a draft that predates the rule can still be moved
    out rather than being stuck in a space nobody can empty.
    """
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_space",
        new=AsyncMock(return_value=_report_list_item(access={"scope": "private"})),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/reports/rid1/space", json={"space_id": None})
    assert ret.status_code == 200
    mock_update.assert_called_once()


async def test_update_visibility_rejects_privatising_a_space_member(mocker):
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1")),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_visibility",
        new=AsyncMock(return_value=_report_list_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/reports/rid1/visibility",
            json={"access": {"scope": "private"}},
        )
    assert ret.status_code == 409
    assert "Remove the report from its space" in ret.json()["error"]
    mock_update.assert_not_called()


async def test_update_visibility_allows_publishing_a_space_member(mocker):
    """The guard is about privatising, not about touching a member at all."""
    mocker.patch(
        "reporting.routes.reports.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1")),
    )
    mocker.patch(
        "reporting.routes.reports.report_store.get_dashboard_report_id",
        new=AsyncMock(return_value=None),
    )
    mock_update = mocker.patch(
        "reporting.routes.reports.report_store.update_report_visibility",
        new=AsyncMock(return_value=_report_list_item(space_id="sp1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            "/api/v1/reports/rid1/visibility",
            json={"access": {"scope": "public"}},
        )
    assert ret.status_code == 200
    mock_update.assert_called_once()
