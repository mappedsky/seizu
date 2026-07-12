from unittest.mock import AsyncMock
from urllib.parse import quote

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import (
    ScheduledQueryItem,
    ScheduledQueryVersion,
    User,
    WorkflowRunActivity,
    WorkflowRunDetail,
    WorkflowRunSummary,
)
from reporting.services import temporal_runs
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


_READONLY_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={},
    permissions=frozenset({"scheduled_queries:read"}),
)


def _make_app(current_user=_FAKE_CURRENT_USER):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
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


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries/<sq_id>/workflow-runs
# ---------------------------------------------------------------------------


def _temporal_sq_item():
    item = _sq_item()
    item.actions = [{"action_type": "temporal", "action_config": {"workflow": "cve_repo_report"}}]
    return item


_WORKFLOW_ID = f"seizu:cve_repo_report:{_SQ_ID}:2024-01-01T00:00:00+00:00"


async def test_list_workflow_runs_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_temporal_sq_item()),
    )
    runs_mock = mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.list_workflow_runs",
        new=AsyncMock(
            return_value=[
                WorkflowRunSummary(
                    workflow_id=_WORKFLOW_ID,
                    run_id="run-1",
                    workflow_name="cve_repo_report",
                    status="completed",
                    start_time="2024-01-01T00:00:00+00:00",
                    close_time="2024-01-01T01:00:00+00:00",
                    history_length=12,
                )
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs")
    assert ret.status_code == 200
    runs = ret.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["workflow_id"] == _WORKFLOW_ID
    assert runs[0]["status"] == "completed"
    runs_mock.assert_awaited_once_with(_SQ_ID, limit=20)


async def test_list_workflow_runs_no_temporal_action_skips_temporal(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    runs_mock = mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.list_workflow_runs",
        new=AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs")
    assert ret.status_code == 200
    assert ret.json()["runs"] == []
    runs_mock.assert_not_awaited()


async def test_list_workflow_runs_sq_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs")
    assert ret.status_code == 404


async def test_list_workflow_runs_temporal_unavailable(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_temporal_sq_item()),
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.list_workflow_runs",
        new=AsyncMock(side_effect=temporal_runs.TemporalUnavailableError("down")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs")
    assert ret.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/v1/scheduled-queries/<sq_id>/workflow-runs/<workflow_id>/<run_id>
# ---------------------------------------------------------------------------


async def test_get_workflow_run_success(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_temporal_sq_item()),
    )
    detail_mock = mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.get_workflow_run_detail",
        new=AsyncMock(
            return_value=WorkflowRunDetail(
                workflow_id=_WORKFLOW_ID,
                run_id="run-1",
                workflow_name="cve_repo_report",
                status="failed",
                failure="workflow failed",
                activities=[
                    WorkflowRunActivity(
                        activity_id="1",
                        activity_type="run_repo_report_chat",
                        status="failed",
                        attempts=3,
                        failure="boom",
                    )
                ],
            )
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs/{quote(_WORKFLOW_ID, safe='')}/run-1")
    assert ret.status_code == 200
    body = ret.json()
    assert body["status"] == "failed"
    assert body["activities"][0]["attempts"] == 3
    detail_mock.assert_awaited_once_with(_SQ_ID, _WORKFLOW_ID, "run-1", include_payload_previews=True)


async def test_get_workflow_run_readers_get_no_payload_previews(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_temporal_sq_item()),
    )
    detail_mock = mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.get_workflow_run_detail",
        new=AsyncMock(
            return_value=WorkflowRunDetail(
                workflow_id=_WORKFLOW_ID,
                run_id="run-1",
                workflow_name="cve_repo_report",
                status="completed",
            )
        ),
    )
    app = _make_app(_READONLY_CURRENT_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs/{quote(_WORKFLOW_ID, safe='')}/run-1")
    assert ret.status_code == 200
    detail_mock.assert_awaited_once_with(_SQ_ID, _WORKFLOW_ID, "run-1", include_payload_previews=False)


async def test_get_workflow_run_requires_temporal_action(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_sq_item()),
    )
    detail_mock = mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.get_workflow_run_detail",
        new=AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs/{quote(_WORKFLOW_ID, safe='')}/run-1")
    assert ret.status_code == 404
    detail_mock.assert_not_awaited()


async def test_get_workflow_run_not_found(mocker):
    mocker.patch(
        "reporting.routes.scheduled_queries.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_temporal_sq_item()),
    )
    mocker.patch(
        "reporting.routes.scheduled_queries.temporal_runs.get_workflow_run_detail",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/scheduled-queries/{_SQ_ID}/workflow-runs/{quote(_WORKFLOW_ID, safe='')}/run-1")
    assert ret.status_code == 404
