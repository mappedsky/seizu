from unittest.mock import AsyncMock

import pytest

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ScheduledQueryItem, ScheduledQueryVersion, User
from reporting.services.mcp_builtins import workflows as builtin
from reporting.services.workflows import item_to_workflow

_NOW = "2026-01-01T00:00:00+00:00"


def _item():
    return ScheduledQueryItem.model_validate(
        {
            "scheduled_query_id": "workflow-1",
            "name": "Pipeline",
            "cypher": "",
            "stages": [
                {
                    "activities": [
                        {
                            "type": "query",
                            "output": "query",
                            "parameters": {"cypher": "RETURN 1"},
                        }
                    ]
                }
            ],
            "created_at": _NOW,
            "updated_at": _NOW,
            "created_by": "user-1",
        }
    )


def _version():
    values = _item().model_dump()
    values.pop("current_version")
    values.pop("updated_at")
    values["version"] = 1
    return ScheduledQueryVersion.model_validate(values)


def _user():
    return CurrentUser(
        user=User(user_id="user-1", sub="sub", iss="issuer", created_at=_NOW, last_login=_NOW),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


def _args():
    return {
        "name": "Pipeline",
        "stages": [
            {
                "activities": [
                    {
                        "type": "query",
                        "output": "query",
                        "parameters": {"cypher": "RETURN 1"},
                    }
                ]
            }
        ],
    }


async def test_user_and_confirmation_helpers():
    with pytest.raises(RuntimeError):
        builtin._user(None)
    target = await builtin._confirm_create({"name": "Pipeline"}, None)
    assert target.resource_id == "Pipeline"
    assert (await builtin._confirm_update({"workflow_id": "workflow-1"}, None)).action == "update"
    assert (await builtin._confirm_delete({}, None)).resource_id == "new"


async def test_list_and_get(mocker):
    mocker.patch.object(builtin.report_store, "list_scheduled_queries", new=AsyncMock(return_value=[_item()]))
    get = mocker.patch.object(builtin.report_store, "get_scheduled_query", new=AsyncMock(return_value=_item()))
    assert len((await builtin._list({}, None))["workflows"]) == 1
    assert (await builtin._get({"workflow_id": "workflow-1"}, None))["workflow_id"] == "workflow-1"
    get.return_value = None
    assert "error" in await builtin._get({"workflow_id": "missing"}, None)


async def test_create_validation_and_refresh(mocker):
    validate = mocker.patch.object(builtin.workflows, "validate_definition", new=AsyncMock(return_value="bad"))
    assert await builtin._create(_args(), _user()) == {"error": "bad"}
    validate.return_value = None
    created = item_to_workflow(_item())
    mocker.patch.object(builtin.workflows, "create", new=AsyncMock(return_value=created))
    reconcile = mocker.patch.object(builtin.workflow_schedules, "reconcile_by_id", new=AsyncMock())
    get = mocker.patch.object(builtin.report_store, "get_scheduled_query", new=AsyncMock(return_value=_item()))
    assert (await builtin._create(_args(), _user()))["workflow_id"] == "workflow-1"
    reconcile.assert_awaited_once()
    get.return_value = None
    assert (await builtin._create(_args(), _user()))["workflow_id"] == "workflow-1"


async def test_update_branches(mocker):
    args = {"workflow_id": "workflow-1", **_args()}
    validate = mocker.patch.object(builtin.workflows, "validate_definition", new=AsyncMock(return_value="bad"))
    assert await builtin._update(args, _user()) == {"error": "bad"}
    validate.return_value = None
    update = mocker.patch.object(builtin.workflows, "update", new=AsyncMock(return_value=None))
    assert "error" in await builtin._update(args, _user())
    update.return_value = item_to_workflow(_item())
    mocker.patch.object(builtin.workflow_schedules, "reconcile_by_id", new=AsyncMock())
    get = mocker.patch.object(builtin.report_store, "get_scheduled_query", new=AsyncMock(return_value=_item()))
    assert (await builtin._update(args, _user()))["workflow_id"] == "workflow-1"
    get.return_value = None
    assert (await builtin._update(args, _user()))["workflow_id"] == "workflow-1"


async def test_delete_and_run_branches(mocker):
    delete = mocker.patch.object(builtin.report_store, "delete_scheduled_query", new=AsyncMock(return_value=False))
    assert "error" in await builtin._delete({"workflow_id": "missing"}, None)
    delete.return_value = True
    remove = mocker.patch.object(builtin.workflow_schedules, "delete_schedule", new=AsyncMock())
    assert await builtin._delete({"workflow_id": "workflow-1"}, None) == {"workflow_id": "workflow-1"}
    remove.assert_awaited_once()

    get = mocker.patch.object(builtin.report_store, "get_scheduled_query", new=AsyncMock(return_value=None))
    assert "error" in await builtin._run({"workflow_id": "missing"}, None)
    get.return_value = _item()
    mocker.patch.object(builtin.workflow_schedules, "run_now", new=AsyncMock(return_value=("temporal", "run")))
    result = await builtin._run({"workflow_id": "workflow-1"}, None)
    assert result["run_id"] == "run"


async def test_versions(mocker):
    mocker.patch.object(
        builtin.report_store,
        "list_scheduled_query_versions",
        new=AsyncMock(return_value=[_version()]),
    )
    get = mocker.patch.object(
        builtin.report_store,
        "get_scheduled_query_version",
        new=AsyncMock(return_value=_version()),
    )
    assert len((await builtin._versions({"workflow_id": "workflow-1"}, None))["versions"]) == 1
    assert (await builtin._get_version({"workflow_id": "workflow-1", "version": 1}, None))["version"] == 1
    get.return_value = None
    assert "error" in await builtin._get_version({"workflow_id": "workflow-1", "version": 2}, None)


def test_group_exposes_expected_tools():
    assert {tool.name for tool in builtin.GROUP_DEF.tools} == {
        "workflows__list",
        "workflows__get",
        "workflows__create",
        "workflows__update",
        "workflows__delete",
        "workflows__run",
        "workflows__list_versions",
        "workflows__get_version",
    }
