from types import SimpleNamespace
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
    # The legacy temporal action migrates to its top-level activity type.
    assert item.stages[2].activities[0].type == "cartography_sync"
    assert "workflow" not in item.stages[2].activities[0].parameters
    assert item.stages[2].activities[0].input is None


def test_stored_workflow_activity_migrates_to_top_level_type():
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {"type": "query", "output": "rows", "parameters": {"cypher": "RETURN 1", "parameters": []}},
                ]
            },
            {
                "activities": [
                    {
                        "type": "workflow",
                        "input": "rows",
                        "output": "out",
                        "parameters": {"workflow": "cve_dependency_remediation", "max_rows": 100},
                    }
                ]
            },
        ],
    )
    stages = workflows.normalized_stages(item)
    migrated = stages[1].activities[0]
    assert migrated.type == "cve_dependency_remediation"
    assert migrated.parameters == {"max_rows": 100}
    assert migrated.input == "rows"
    assert workflows.has_code_workflow(item)


def test_stored_workflow_activity_with_unknown_name_stays_unmigrated():
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {"type": "workflow", "output": "out", "parameters": {"workflow": "not-registered"}},
                ]
            }
        ],
    )
    activity = workflows.normalized_stages(item)[0].activities[0]
    assert activity.type == "workflow"
    assert activity.parameters == {"workflow": "not-registered"}


def test_canonical_workflow_projects_post_completion_triggers():
    item = _legacy_item(
        cypher="",
        stages=_body().model_dump()["stages"],
        actions=[
            {
                "action_type": "trigger_workflow",
                "action_config": {"workflow_id": "workflow-2"},
            }
        ],
    )

    assert workflows.item_to_workflow(item).trigger_workflows == ["workflow-2"]


def test_stored_cartography_modules_migrate_to_module_runs():
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {
                        "type": "workflow",
                        "output": "sync",
                        "parameters": {
                            "workflow": "cartography_sync",
                            "modules": ["github", "cve"],
                            "stop_on_failure": True,
                        },
                    }
                ]
            }
        ],
    )
    activity = workflows.normalized_stages(item)[0].activities[0]
    assert activity.type == "cartography_sync"
    # The previously-implicit structural stages are materialized.
    assert [run["module"] for run in activity.parameters["module_runs"]] == [
        "create-indexes",
        "github",
        "cve",
        "analysis",
    ]
    assert activity.parameters["stop_on_failure"] is True
    assert "modules" not in activity.parameters
    assert "workflow" not in activity.parameters


def test_stored_cartography_drops_stray_max_rows_and_query_return_attribute():
    # The old dispatcher's base schema always populated max_rows and
    # query_return_attribute for every temporal action regardless of the
    # selected workflow's row requirement; cartography_sync's new top-level
    # schema has no such fields, so a leftover value must not survive
    # migration (it would fail save-time validation as an extra input).
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {
                        "type": "workflow",
                        "output": "sync",
                        "parameters": {
                            "workflow": "cartography_sync",
                            "modules": ["github"],
                            "max_rows": 200,
                            "query_return_attribute": "details",
                        },
                    }
                ]
            }
        ],
    )
    activity = workflows.normalized_stages(item)[0].activities[0]
    assert activity.type == "cartography_sync"
    assert "max_rows" not in activity.parameters
    assert "query_return_attribute" not in activity.parameters


def test_row_consuming_workflow_keeps_max_rows_and_query_return_attribute():
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {"activities": [{"type": "query", "output": "rows", "parameters": {"cypher": "RETURN 1"}}]},
            {
                "activities": [
                    {
                        "type": "workflow",
                        "input": "rows",
                        "output": "report",
                        "parameters": {
                            "workflow": "cve_repo_report",
                            "max_rows": 50,
                            "query_return_attribute": "details",
                        },
                    }
                ]
            },
        ],
    )
    activity = workflows.normalized_stages(item)[1].activities[0]
    assert activity.type == "cve_repo_report"
    assert activity.parameters == {"max_rows": 50, "query_return_attribute": "details"}


def test_stored_cartography_pipeline_migrates_flattened():
    pipeline = '{"stages": [{"runs": [{"module": "aws", "params": {"aws_sync_all_profiles": true}},'
    pipeline += ' {"module": "github"}]}, {"runs": [{"module": "cve"}]}]}'
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {
                        "type": "workflow",
                        "output": "sync",
                        "parameters": {"workflow": "cartography_sync", "pipeline": pipeline},
                    }
                ]
            }
        ],
    )
    activity = workflows.normalized_stages(item)[0].activities[0]
    runs = activity.parameters["module_runs"]
    assert [run["module"] for run in runs] == ["create-indexes", "aws", "github", "cve", "analysis"]
    assert runs[1]["params"] == {"aws_sync_all_profiles": True}
    assert "pipeline" not in activity.parameters


def test_stored_cartography_unparseable_pipeline_left_untouched():
    item = _legacy_item(
        cypher="",
        actions=[],
        stages=[
            {
                "activities": [
                    {
                        "type": "workflow",
                        "output": "sync",
                        "parameters": {"workflow": "cartography_sync", "pipeline": "{nope"},
                    }
                ]
            }
        ],
    )
    activity = workflows.normalized_stages(item)[0].activities[0]
    assert activity.type == "cartography_sync"
    assert activity.parameters["pipeline"] == "{nope"
    assert "module_runs" not in activity.parameters


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


def test_activity_definitions_reject_module_named_like_disabled_workflow(mocker):
    # A module named after ANY registered workflow — enabled or not — must be
    # rejected: it would look valid in the editor but be classified as that
    # child workflow at dispatch and fail at runtime.
    mocker.patch("reporting.settings.TEMPORAL_ENABLED_WORKFLOWS", ["cve_repo_report"])
    mocker.patch.object(
        workflows.scheduled_query_modules,
        "get_configured_action_names",
        return_value=["cartography_sync"],
    )
    mocker.patch.object(
        workflows.scheduled_query_modules,
        "get_module",
        return_value=SimpleNamespace(action_config_schema=lambda: []),
    )
    with pytest.raises(ValueError, match="collide"):
        workflows.activity_definitions()


async def test_validate_definition_rejects_workflow_sub_type(mocker):
    mocker.patch.object(workflows, "validate_query", new=AsyncMock(return_value=ValidationResult()))
    body = _body(
        stages=[
            {
                "activities": [
                    {"type": "workflow", "output": "out", "parameters": {"workflow": "cve_repo_report"}},
                ]
            }
        ]
    )
    error = await workflows.validate_definition(body)
    assert "unknown type" in (error or "")


async def test_validate_definition_accepts_top_level_workflow_types(mocker):
    mocker.patch.object(workflows, "validate_query", new=AsyncMock(return_value=ValidationResult()))
    body = _body(
        stages=[
            {"activities": [{"type": "query", "output": "rows", "parameters": {"cypher": "RETURN 1"}}]},
            {"activities": [{"type": "cve_repo_report", "input": "rows", "output": "report", "parameters": {}}]},
            {
                "activities": [
                    {
                        "type": "cartography_sync",
                        "output": "sync",
                        "parameters": {"module_runs": [{"module": "cve", "params": {}}]},
                    }
                ]
            },
        ]
    )
    assert await workflows.validate_definition(body) is None


async def test_validate_definition_requires_input_for_row_consuming_workflow(mocker):
    mocker.patch.object(workflows, "validate_query", new=AsyncMock(return_value=ValidationResult()))
    body = _body(
        stages=[
            {"activities": [{"type": "cve_repo_report", "output": "report", "parameters": {}}]},
        ]
    )
    error = await workflows.validate_definition(body)
    assert "requires an input" in (error or "")


async def test_validate_definition_runs_cartography_validator(mocker):
    mocker.patch.object(workflows, "validate_query", new=AsyncMock(return_value=ValidationResult()))
    body = _body(
        stages=[
            {
                "activities": [
                    {
                        "type": "cartography_sync",
                        "output": "sync",
                        "parameters": {"module_runs": [{"module": "not-a-module", "params": {}}]},
                    }
                ]
            }
        ]
    )
    error = await workflows.validate_definition(body)
    assert "not enabled" in (error or "")


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


async def test_create_and_update_drop_missing_and_self_trigger_targets(mocker):
    stored = _legacy_item(stages=_body().model_dump()["stages"], actions=[], cypher="")
    get = mocker.patch.object(
        workflows.report_store,
        "get_scheduled_query",
        new=AsyncMock(
            side_effect=lambda workflow_id: (
                stored
                if workflow_id == "workflow-2"
                else stored.model_copy(update={"created_by": "other-user"})
                if workflow_id == "foreign"
                else None
            )
        ),
    )
    create = mocker.patch.object(
        workflows.report_store,
        "create_scheduled_query",
        new=AsyncMock(return_value=stored),
    )
    update = mocker.patch.object(
        workflows.report_store,
        "update_scheduled_query",
        new=AsyncMock(return_value=stored),
    )
    body = _body(trigger_workflows=["workflow-2", "missing", "foreign"])

    await workflows.create(body, "user-1")
    assert create.await_args.kwargs["actions"] == [
        {
            "action_type": "trigger_workflow",
            "action_config": {"workflow_id": "workflow-2"},
        }
    ]

    body = _body(trigger_workflows=["workflow-1", "workflow-2", "missing", "foreign"])
    await workflows.update("workflow-1", body, "user-1")
    assert update.await_args.kwargs["actions"] == [
        {
            "action_type": "trigger_workflow",
            "action_config": {"workflow_id": "workflow-2"},
        }
    ]
    assert "workflow-1" not in [call.args[0] for call in get.await_args_list]


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
