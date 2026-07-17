from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ScheduledQueryItem, User
from reporting.services import temporal_runs
from reporting.services.workflows import item_to_workflow

_USER = User(
    user_id="user-1",
    sub="sub-1",
    iss="https://issuer.example",
    created_at="2026-01-01T00:00:00+00:00",
    last_login="2026-01-01T00:00:00+00:00",
)
_CURRENT = CurrentUser(user=_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)


def _app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _CURRENT
    return app


def _stored_item():
    return ScheduledQueryItem.model_validate(
        {
            "scheduled_query_id": "workflow-1",
            "name": "Notify",
            "cypher": "",
            "stages": [
                {
                    "activities": [
                        {
                            "type": "query",
                            "output": "critical",
                            "parameters": {"cypher": "RETURN 1 AS details"},
                        }
                    ]
                }
            ],
            "current_version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "created_by": "user-1",
        }
    )


async def test_list_workflows_returns_canonical_shape(mocker):
    mocker.patch(
        "reporting.routes.workflows.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=[_stored_item()]),
    )
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/v1/workflows")

    assert response.status_code == 200
    workflow = response.json()["workflows"][0]
    assert workflow["workflow_id"] == "workflow-1"
    assert workflow["stages"][0]["activities"][0]["output"] == "critical"


async def test_create_workflow_reconciles_schedule(mocker):
    item = _stored_item()
    mocker.patch(
        "reporting.routes.workflows.workflows.validate_definition",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.workflows.workflows.create",
        new=AsyncMock(return_value=item_to_workflow(item)),
    )
    reconcile = mocker.patch(
        "reporting.routes.workflows.workflow_schedules.reconcile_by_id",
        new=AsyncMock(),
    )
    mocker.patch(
        "reporting.routes.workflows.report_store.get_scheduled_query",
        new=AsyncMock(return_value=item),
    )
    body = {
        "name": "Notify",
        "stages": [
            {
                "activities": [
                    {
                        "type": "query",
                        "output": "critical",
                        "parameters": {"cypher": "RETURN 1 AS details"},
                    }
                ]
            }
        ],
    }
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post("/api/v1/workflows", json=body)

    assert response.status_code == 201
    reconcile.assert_awaited_once_with("workflow-1")


async def _request(method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def test_get_update_and_delete_workflow(mocker):
    item = _stored_item()
    get = mocker.patch(
        "reporting.routes.workflows.report_store.get_scheduled_query",
        new=AsyncMock(return_value=item),
    )
    response = await _request("GET", "/api/v1/workflows/workflow-1")
    assert response.status_code == 200
    get.return_value = None
    assert (await _request("GET", "/api/v1/workflows/missing")).status_code == 404

    mocker.patch("reporting.routes.workflows.workflows.validate_definition", new=AsyncMock(return_value=None))
    update = mocker.patch(
        "reporting.routes.workflows.workflows.update",
        new=AsyncMock(return_value=item_to_workflow(item)),
    )
    get.return_value = item
    reconcile = mocker.patch("reporting.routes.workflows.workflow_schedules.reconcile_by_id", new=AsyncMock())
    body = {
        "name": "Updated",
        "stages": [{"activities": [{"type": "query", "output": "critical", "parameters": {"cypher": "RETURN 1"}}]}],
    }
    assert (await _request("PUT", "/api/v1/workflows/workflow-1", json=body)).status_code == 200
    reconcile.assert_awaited_once_with("workflow-1")
    update.return_value = None
    assert (await _request("PUT", "/api/v1/workflows/missing", json=body)).status_code == 404

    delete = mocker.patch(
        "reporting.routes.workflows.report_store.delete_scheduled_query",
        new=AsyncMock(return_value=True),
    )
    delete_schedule = mocker.patch("reporting.routes.workflows.workflow_schedules.delete_schedule", new=AsyncMock())
    assert (await _request("DELETE", "/api/v1/workflows/workflow-1")).status_code == 200
    delete_schedule.assert_awaited_once_with("workflow-1")
    delete.return_value = False
    assert (await _request("DELETE", "/api/v1/workflows/missing")).status_code == 404


async def test_create_validation_error(mocker):
    mocker.patch("reporting.routes.workflows.workflows.validate_definition", new=AsyncMock(return_value="bad"))
    response = await _request(
        "POST",
        "/api/v1/workflows",
        json={
            "name": "Bad",
            "stages": [{"activities": [{"type": "query", "output": "query", "parameters": {"cypher": "RETURN 1"}}]}],
        },
    )
    assert response.status_code == 400


async def test_run_workflow_success_missing_and_unavailable(mocker):
    get = mocker.patch(
        "reporting.routes.workflows.report_store.get_scheduled_query",
        new=AsyncMock(return_value=_stored_item()),
    )
    run = mocker.patch(
        "reporting.routes.workflows.workflow_schedules.run_now",
        new=AsyncMock(return_value=("temporal-id", "run-id")),
    )
    response = await _request("POST", "/api/v1/workflows/workflow-1/run")
    assert response.status_code == 202
    assert response.json()["run_id"] == "run-id"
    get.return_value = None
    assert (await _request("POST", "/api/v1/workflows/missing/run")).status_code == 404
    get.return_value = _stored_item()
    run.side_effect = RuntimeError("down")
    assert (await _request("POST", "/api/v1/workflows/workflow-1/run")).status_code == 503


async def test_versions_and_runs_endpoints(mocker):
    item = _stored_item()
    version = item.model_dump()
    version.pop("current_version")
    version.pop("updated_at")
    version["version"] = 1
    from reporting.schema.report_config import ScheduledQueryVersion, WorkflowRunDetail, WorkflowRunSummary

    stored_version = ScheduledQueryVersion.model_validate(version)
    get = mocker.patch(
        "reporting.routes.workflows.report_store.get_scheduled_query",
        new=AsyncMock(return_value=item),
    )
    mocker.patch(
        "reporting.routes.workflows.report_store.list_scheduled_query_versions",
        new=AsyncMock(return_value=[stored_version]),
    )
    get_version = mocker.patch(
        "reporting.routes.workflows.report_store.get_scheduled_query_version",
        new=AsyncMock(return_value=stored_version),
    )
    assert len((await _request("GET", "/api/v1/workflows/workflow-1/versions")).json()["versions"]) == 1
    assert (await _request("GET", "/api/v1/workflows/workflow-1/versions/1")).status_code == 200
    get_version.return_value = None
    assert (await _request("GET", "/api/v1/workflows/workflow-1/versions/2")).status_code == 404

    summary = WorkflowRunSummary(
        workflow_id="temporal-id", run_id="run-id", workflow_name="configured", status="completed"
    )
    list_runs = mocker.patch(
        "reporting.routes.workflows.temporal_runs.list_workflow_runs",
        new=AsyncMock(return_value=[summary]),
    )
    assert len((await _request("GET", "/api/v1/workflows/workflow-1/runs")).json()["runs"]) == 1
    list_runs.side_effect = temporal_runs.TemporalUnavailableError()
    assert (await _request("GET", "/api/v1/workflows/workflow-1/runs")).status_code == 503
    get.return_value = None
    list_runs.side_effect = None
    assert (await _request("GET", "/api/v1/workflows/missing/runs")).status_code == 404

    detail = WorkflowRunDetail(
        workflow_id="temporal-id", run_id="run-id", workflow_name="configured", status="completed"
    )
    get_run = mocker.patch(
        "reporting.routes.workflows.temporal_runs.get_workflow_run_detail",
        new=AsyncMock(return_value=detail),
    )
    path = "/api/v1/workflows/workflow-1/runs/temporal-id/run-id"
    assert (await _request("GET", path)).status_code == 200
    get_run.return_value = None
    assert (await _request("GET", path)).status_code == 404
    get_run.side_effect = temporal_runs.TemporalUnavailableError()
    assert (await _request("GET", path)).status_code == 503
