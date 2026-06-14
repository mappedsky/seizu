from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting import settings
from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion, User

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


def _report_list_item(report_id="rid1", name="My Report", current_version=1):
    return ReportListItem(
        report_id=report_id,
        name=name,
        current_version=current_version,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
        access={"scope": "public"},
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
    mock_create.assert_called_once_with(name="Copy of My Report", created_by="test-user-id")
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
