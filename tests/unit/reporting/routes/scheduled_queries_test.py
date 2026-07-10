from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ScheduledQueryItem, ScheduledQueryVersion, User
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

_SQ_ID = "sq-abc123"


def _make_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_CURRENT_USER
    return app


def _sq_item(sq_id=_SQ_ID, name="My Query", version=1):
    return ScheduledQueryItem(
        scheduled_query_id=sq_id,
        name=name,
        cypher="MATCH (n) RETURN n",
        frequency=60,
        enabled=True,
        actions=[{"action_type": "log", "action_config": {}}],
        current_version=version,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="user@example.com",
    )


def _sq_version(sq_id=_SQ_ID, version=1):
    return ScheduledQueryVersion(
        scheduled_query_id=sq_id,
        name="My Query",
        version=version,
        cypher="MATCH (n) RETURN n",
        frequency=60,
        enabled=True,
        actions=[{"action_type": "log", "action_config": {}}],
        created_at="2024-01-01T00:00:00+00:00",
        created_by="user@example.com",
        comment="Initial version",
    )


_VALID_SQ_BODY = {
    "name": "My Query",
    "cypher": "MATCH (n) RETURN n",
    "frequency": 60,
    "enabled": True,
    "actions": [{"action_type": "log", "action_config": {}}],
}


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries
# ---------------------------------------------------------------------------


async def test_list_scheduled_queries_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=[_sq_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/scheduled-queries")
    assert ret.status_code == 200
    items = ret.json()["scheduled_queries"]
    assert len(items) == 1
    assert items[0]["scheduled_query_id"] == _SQ_ID
    assert items[0]["name"] == "My Query"


async def test_list_scheduled_queries_empty(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=[]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/scheduled-queries")
    assert ret.status_code == 200
    assert ret.json()["scheduled_queries"] == []


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries/<sq_id>
# ---------------------------------------------------------------------------


async def test_get_scheduled_query_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}")
    assert ret.status_code == 200
    assert ret.json()["scheduled_query_id"] == _SQ_ID


async def test_get_scheduled_query_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}")
    assert ret.status_code == 404
    assert "error" in ret.json()


# ---------------------------------------------------------------------------
# POST /api/v1/scheduled-queries
# ---------------------------------------------------------------------------


async def test_create_scheduled_query_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.create_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            json=_VALID_SQ_BODY,
        )
    assert ret.status_code == 201
    assert ret.json()["scheduled_query_id"] == _SQ_ID


async def test_create_scheduled_query_cypher_validation_error(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=["Write queries are not allowed"])),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            json=_VALID_SQ_BODY,
        )
    assert ret.status_code == 400
    assert "errors" in ret.json()
    assert ret.json()["errors"] == ["Write queries are not allowed"]


async def test_create_scheduled_query_not_json(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
    assert ret.status_code == 422


async def test_create_scheduled_query_invalid_body(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            json={"invalid": "body"},
        )
    assert ret.status_code == 422


async def test_create_scheduled_query_unknown_action_type(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    body = dict(_VALID_SQ_BODY)
    body["actions"] = [{"action_type": "not_a_real_module", "action_config": {}}]
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            json=body,
        )
    assert ret.status_code == 400
    assert "not_a_real_module" in ret.json()["error"]


async def test_create_scheduled_query_action_config_error(mocker):
    from reporting.schema.report_config import ActionConfigFieldDef

    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": [ActionConfigFieldDef(name="target", label="Target", type="string", required=True)]},
    )
    body = dict(_VALID_SQ_BODY)
    body["actions"] = [{"action_type": "log", "action_config": {}}]
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/scheduled-queries",
            json=body,
        )
    assert ret.status_code == 400
    assert "error" in ret.json()


# ---------------------------------------------------------------------------
# PUT /api/v1/scheduled-queries/<sq_id>
# ---------------------------------------------------------------------------


async def test_update_scheduled_query_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.update_scheduled_query",
        new=AsyncMock(return_value=_sq_item(version=2)),
    )
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            f"/api/v1/scheduled-queries/{_SQ_ID}",
            json=_VALID_SQ_BODY,
        )
    assert ret.status_code == 200
    assert ret.json()["current_version"] == 2


async def test_update_scheduled_query_cypher_validation_error(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=["Write queries are not allowed"])),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            f"/api/v1/scheduled-queries/{_SQ_ID}",
            json=_VALID_SQ_BODY,
        )
    assert ret.status_code == 400
    assert "errors" in ret.json()
    assert ret.json()["errors"] == ["Write queries are not allowed"]


async def test_update_scheduled_query_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.update_scheduled_query",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            f"/api/v1/scheduled-queries/{_SQ_ID}",
            json=_VALID_SQ_BODY,
        )
    assert ret.status_code == 404


async def test_update_scheduled_query_not_json(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            f"/api/v1/scheduled-queries/{_SQ_ID}",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
    assert ret.status_code == 422


async def test_update_scheduled_query_invalid_body(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(
            f"/api/v1/scheduled-queries/{_SQ_ID}",
            json={"invalid": "body"},
        )
    assert ret.status_code == 422


async def test_update_scheduled_query_rejects_multiple_triggers(mocker):
    body = {
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "frequency": 60,
        "watch_scans": [{"grouptype": "CVE"}],
    }
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put(f"/api/v1/scheduled-queries/{_SQ_ID}", json=body)
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries/<sq_id>/versions
# ---------------------------------------------------------------------------


async def test_list_scheduled_query_versions_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.list_scheduled_query_versions",
        new=AsyncMock(return_value=[_sq_version(version=1), _sq_version(version=2)]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/versions")
    assert ret.status_code == 200
    versions = ret.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["version"] == 1


async def test_list_scheduled_query_versions_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/versions")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries/<sq_id>/versions/<n>
# ---------------------------------------------------------------------------


async def test_get_scheduled_query_version_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query_version",
        new=AsyncMock(return_value=_sq_version(version=1)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/versions/1")
    assert ret.status_code == 200
    assert ret.json()["version"] == 1
    assert ret.json()["scheduled_query_id"] == _SQ_ID


async def test_get_scheduled_query_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/versions/99")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/scheduled-queries/<sq_id>
# ---------------------------------------------------------------------------


async def test_delete_scheduled_query_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.delete_scheduled_query",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/scheduled-queries/{_SQ_ID}")
    assert ret.status_code == 200
    assert ret.json()["scheduled_query_id"] == _SQ_ID


async def test_delete_scheduled_query_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.delete_scheduled_query",
        new=AsyncMock(return_value=False),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete(f"/api/v1/scheduled-queries/{_SQ_ID}")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/scheduled-queries/<sq_id>/run
# ---------------------------------------------------------------------------


async def test_run_scheduled_query_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.request_scheduled_query_run",
        new=AsyncMock(return_value="2026-01-01T00:00:00+00:00"),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/scheduled-queries/{_SQ_ID}/run")
    assert ret.status_code == 202
    assert ret.json() == {
        "scheduled_query_id": _SQ_ID,
        "run_requested_at": "2026-01-01T00:00:00+00:00",
    }


async def test_run_scheduled_query_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.request_scheduled_query_run",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/scheduled-queries/{_SQ_ID}/run")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# schedule field
# ---------------------------------------------------------------------------


async def test_create_scheduled_query_with_schedule(mocker):
    mocker.patch(
        "reporting.services.scheduled_query_validation.scheduled_query_modules.get_action_schemas",
        return_value={"log": []},
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=[], warnings=[])),
    )
    create_mock = mocker.patch(
        "reporting.routes.scheduled_queries.report_store.create_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    body = {
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "schedule": {"type": "daily", "days_of_week": [0, 2], "hour": 9, "minute": 30},
        "enabled": True,
        "actions": [{"action_type": "log", "action_config": {}}],
    }
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/scheduled-queries", json=body)
    assert ret.status_code == 201
    assert create_mock.await_args.kwargs["schedule"] == {
        "type": "daily",
        "interval_minutes": None,
        "interval_hours": None,
        "days_of_week": [0, 2],
        "hour": 9,
        "minute": 30,
        "days_of_month": [],
    }
    assert create_mock.await_args.kwargs["frequency"] is None


async def test_create_scheduled_query_rejects_frequency_and_schedule(mocker):
    body = {
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "frequency": 60,
        "schedule": {"type": "interval", "interval_minutes": 5},
    }
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/scheduled-queries", json=body)
    assert ret.status_code == 422


async def test_create_scheduled_query_rejects_zero_frequency_and_schedule(mocker):
    body = {
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "frequency": 0,
        "schedule": {"type": "interval", "interval_minutes": 5},
    }
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/scheduled-queries", json=body)
    assert ret.status_code == 422


async def test_create_scheduled_query_rejects_schedule_and_watch_scans(mocker):
    body = {
        "name": "My Query",
        "cypher": "MATCH (n) RETURN n",
        "schedule": {"type": "interval", "interval_minutes": 5},
        "watch_scans": [{"grouptype": "CVE"}],
    }
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/scheduled-queries", json=body)
    assert ret.status_code == 422
