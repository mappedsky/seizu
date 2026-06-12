from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import User

_NOW = "2024-01-01T00:00:00+00:00"

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub",
    iss="iss",
    email="user@example.com",
    created_at=_NOW,
    last_login=_NOW,
)


def _current_user(permissions: frozenset[str] = ALL_PERMISSIONS) -> CurrentUser:
    return CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=permissions)


def _make_app(current: CurrentUser | None = None):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current or _current_user()
    return app


def _record(grouptypes, syncedtypes, groupids):
    return {
        "grouptypes": grouptypes,
        "syncedtypes": syncedtypes,
        "groupids": groupids,
    }


async def test_returns_sorted_distinct_values(mocker):
    mocker.patch(
        "reporting.routes.sync_metadata.reporting_neo4j.run_query",
        mocker.AsyncMock(
            return_value=[_record(["GitHub", "CVE", None, "CVE"], ["recent", "year"], ["CVE_METADATA", ""])]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sync-metadata/values")

    assert response.status_code == 200
    data = response.json()
    assert data["grouptypes"] == ["CVE", "GitHub"]
    assert data["syncedtypes"] == ["recent", "year"]
    assert data["groupids"] == ["CVE_METADATA"]


async def test_empty_graph_returns_empty_lists(mocker):
    mocker.patch(
        "reporting.routes.sync_metadata.reporting_neo4j.run_query",
        mocker.AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sync-metadata/values")

    assert response.status_code == 200
    assert response.json() == {"grouptypes": [], "syncedtypes": [], "groupids": []}


async def test_allows_scheduled_queries_read(mocker):
    mocker.patch(
        "reporting.routes.sync_metadata.reporting_neo4j.run_query",
        mocker.AsyncMock(return_value=[]),
    )
    app = _make_app(_current_user(frozenset({"scheduled_queries:read"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sync-metadata/values")
    assert response.status_code == 200


async def test_allows_chat_schedule(mocker):
    mocker.patch(
        "reporting.routes.sync_metadata.reporting_neo4j.run_query",
        mocker.AsyncMock(return_value=[]),
    )
    app = _make_app(_current_user(frozenset({"chat:schedule"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sync-metadata/values")
    assert response.status_code == 200


async def test_rejects_without_either_permission(mocker):
    query_mock = mocker.patch("reporting.routes.sync_metadata.reporting_neo4j.run_query")
    app = _make_app(_current_user(frozenset({"chat:use"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/sync-metadata/values")
    assert response.status_code == 403
    query_mock.assert_not_called()
