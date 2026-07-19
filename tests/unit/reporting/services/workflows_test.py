from unittest.mock import AsyncMock

import pytest

from reporting.schema.report_config import CreateWorkflowRequest, ScheduledQueryItem, ScheduledQueryVersion
from reporting.services import workflows
from reporting.services.query_validator import ValidationResult


def _legacy_item(**updates):
    values = {
        "scheduled_query_id": "workflow-1",
        "name": "Legacy query",
        "cypher": "RETURN $limit AS details",
        "params": [{"name": "limit", "value": 10}],
        "frequency": 60,
        "actions": [
            {"action_type": "log", "action_config": {"log_attrs": ["limit"]}},
            {"action_type": "temporal", "action_config": {"workflow": "cartography_sync"}},
        ],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "user-1",
    }
    values.update(updates)
    return ScheduledQueryItem.model_validate(values)


def _body(**updates):
    values = {
        "name": "Pipeline",
        "stages": [
            {
                "activities": [
                    {
                        "type": "query",
                        "output": "query",
                        "parameters": {"cypher": "RETURN 1", "parameters": []},
                    }
                ]
            }
        ],
    }
    values.update(updates)
    return CreateWorkflowRequest.model_validate(values)


def _version(**updates):
    values = _legacy_item().model_dump()
    values.pop("current_version")
    values.pop("updated_at")
    values["version"] = 2
    values.update(updates)
    return ScheduledQueryVersion.model_validate(values)


def test_legacy_item_projects_to_query_then_sequential_actions():
    item = workflows.item_to_workflow(_legacy_item())

    assert [len(stage.activities) for stage in item.stages] == [1, 1, 1]
    query = item.stages[0].activities[0]
    assert query.type == "query"
    assert query.output == "query"
    assert query.parameters["parameters"][0]["name"] == "limit"
    assert item.stages[1].activities[0].input == "query"
    assert item.stages[2].activities[0].type == "workflow"
    assert item.stages[2].activities[0].input is None


def test_canonical_stages_are_not_exposed_by_legacy_api():
    item = _legacy_item(stages=_body().model_dump()["stages"], actions=[], cypher="")
    assert not workflows.legacy_representable(item)
    assert workflows.legacy_representable(_legacy_item())


def test_branch_only_shape_is_not_supported():
    item = _legacy_item(inputs={"query": {"type": "query", "cypher": "RETURN 1"}})
    with pytest.raises(ValueError, match="superseded"):
        workflows.normalized_stages(item)


async def test_validate_definition_checks_query_and_reserved_input(mocker):
    validate = mocker.patch.object(
        workflows,
        "validate_query",
        new=AsyncMock(return_value=ValidationResult(errors=["write"])),
    )
    error = await workflows.validate_definition(_body())
    assert "write" in (error or "")
    validate.assert_awaited_once()

    validate.return_value = ValidationResult()
    body = _body(
        stages=[
            {
                "activities": [
                    {
                        "type": "query",
                        "output": "query",
                        "parameters": {
                            "cypher": "RETURN $input",
                            "parameters": [{"name": "input", "value": 1}],
                        },
                    }
                ]
            }
        ]
    )
    assert "reserved" in (await workflows.validate_definition(body) or "")


def test_schema_rejects_duplicate_and_non_earlier_outputs():
    with pytest.raises(ValueError, match="earlier stage"):
        _body(
            stages=[
                {
                    "activities": [
                        {"type": "query", "input": "later", "output": "first", "parameters": {"cypher": "RETURN 1"}},
                        {"type": "query", "output": "later", "parameters": {"cypher": "RETURN 2"}},
                    ]
                }
            ]
        )
    with pytest.raises(ValueError, match="duplicate"):
        _body(
            stages=[
                {"activities": [{"type": "query", "output": "same", "parameters": {"cypher": "RETURN 1"}}]},
                {"activities": [{"type": "query", "output": "same", "parameters": {"cypher": "RETURN 2"}}]},
            ]
        )


async def test_create_and_update_persist_stages(mocker):
    stored = _legacy_item(stages=_body().model_dump()["stages"], actions=[], cypher="")
    create = mocker.patch.object(workflows.report_store, "create_scheduled_query", new=AsyncMock(return_value=stored))
    update = mocker.patch.object(workflows.report_store, "update_scheduled_query", new=AsyncMock(return_value=stored))
    body = _body(schedule={"type": "hourly", "interval_hours": 2}, comment="change")

    assert (await workflows.create(body, "creator")).workflow_id == "workflow-1"
    assert create.await_args.kwargs["stages"][0]["activities"][0]["output"] == "query"
    assert create.await_args.kwargs["actions"] == []
    assert (await workflows.update("workflow-1", body, "editor")).workflow_id == "workflow-1"
    assert update.await_args.kwargs["comment"] == "change"
    update.return_value = None
    assert await workflows.update("missing", body, "editor") is None


async def test_managed_mutations_are_owner_scoped(mocker):
    foreign = _legacy_item(
        stages=_body().model_dump()["stages"],
        actions=[],
        cypher="",
        created_by="other-user",
    )
    get = mocker.patch.object(
        workflows.report_store,
        "get_scheduled_query",
        new=AsyncMock(return_value=foreign),
    )
    update = mocker.patch.object(workflows, "update", new=AsyncMock())
    delete = mocker.patch.object(workflows.report_store, "delete_scheduled_query", new=AsyncMock())
    run = mocker.patch.object(workflows.workflow_schedules, "run_now", new=AsyncMock())

    with pytest.raises(workflows.WorkflowNotFoundError):
        await workflows.update_managed("workflow-1", _body(), "user-1")
    with pytest.raises(workflows.WorkflowNotFoundError):
        await workflows.delete_managed("workflow-1", "user-1")
    with pytest.raises(workflows.WorkflowNotFoundError):
        await workflows.run_managed("workflow-1", "user-1")

    get.assert_awaited()
    update.assert_not_awaited()
    delete.assert_not_awaited()
    run.assert_not_awaited()


async def test_delete_managed_removes_schedule_before_record(mocker):
    owned = _legacy_item(
        stages=_body().model_dump()["stages"],
        actions=[],
        cypher="",
    )
    mocker.patch.object(
        workflows.report_store,
        "get_scheduled_query",
        new=AsyncMock(return_value=owned),
    )
    remove_schedule = mocker.patch.object(
        workflows.workflow_schedules,
        "delete_schedule",
        new=AsyncMock(side_effect=RuntimeError("Temporal offline")),
    )
    delete_record = mocker.patch.object(
        workflows.report_store,
        "delete_scheduled_query",
        new=AsyncMock(return_value=True),
    )

    with pytest.raises(RuntimeError, match="Temporal offline"):
        await workflows.delete_managed("workflow-1", "user-1")

    remove_schedule.assert_awaited_once_with("workflow-1")
    delete_record.assert_not_awaited()


def test_version_uses_staged_shape():
    version = _version(stages=_body().model_dump()["stages"], actions=[], cypher="")
    result = workflows.version_to_workflow(version)
    assert result.workflow_id == "workflow-1"
    assert result.version == 2
    assert result.stages[0].activities[0].output == "query"
