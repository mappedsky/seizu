from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ScheduledQueryItem, User
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
            "inputs": {"critical": {"type": "query", "cypher": "RETURN 1 AS details"}},
            "activities": [{"type": "log", "input": "critical", "parameters": {}}],
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
    assert list(workflow["inputs"]) == ["critical"]
    assert workflow["activities"][0]["type"] == "log"


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
        "inputs": {"critical": {"type": "query", "cypher": "RETURN 1 AS details"}},
        "activities": [{"type": "log", "input": "critical", "parameters": {}}],
    }
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post("/api/v1/workflows", json=body)

    assert response.status_code == 201
    reconcile.assert_awaited_once_with("workflow-1")
