from unittest.mock import AsyncMock

from reporting.schema.report_config import (
    ActionConfigFieldDef,
    CreateWorkflowRequest,
    ScheduledQueryItem,
    ScheduledQueryVersion,
)
from reporting.services import workflows
from reporting.services.query_validator import ValidationResult


def _legacy_item(**updates):
    values = {
        "scheduled_query_id": "workflow-1",
        "name": "Legacy query",
        "cypher": "RETURN 1 AS details",
        "params": [{"name": "limit", "value": 10}],
        "frequency": 60,
        "actions": [{"action_type": "temporal", "action_config": {"workflow": "cartography_sync"}}],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "user-1",
    }
    values.update(updates)
    return ScheduledQueryItem.model_validate(values)


def test_legacy_item_normalizes_to_workflow():
    item = workflows.item_to_workflow(_legacy_item())

    assert list(item.inputs) == ["query"]
    assert item.inputs["query"].parameters[0].name == "limit"
    assert item.activities[0].type == "workflow"
    assert item.activities[0].input is None


def test_multi_input_workflow_is_hidden_from_legacy_api():
    item = _legacy_item(
        inputs={
            "first": {"type": "query", "cypher": "RETURN 1"},
            "second": {"type": "query", "cypher": "RETURN 2"},
        },
        activities=[],
    )

    assert not workflows.legacy_representable(item)


async def test_validate_definition_validates_every_input(mocker):
    validate = mocker.patch.object(
        workflows,
        "validate_query",
        new=AsyncMock(side_effect=[ValidationResult(), ValidationResult(errors=["write"])]),
    )
    body = CreateWorkflowRequest.model_validate(
        {
            "name": "Two queries",
            "inputs": {
                "first": {"type": "query", "cypher": "RETURN 1"},
                "second": {"type": "query", "cypher": "DELETE n"},
            },
            "activities": [],
        }
    )

    error = await workflows.validate_definition(body)

    assert validate.await_count == 2
    assert error == "Input 'second' is invalid: write"


def _body(**updates):
    values = {
        "name": "Pipeline",
        "inputs": {"query": {"type": "query", "cypher": "RETURN 1"}},
        "activities": [{"type": "log", "input": "query", "parameters": {}}],
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


def test_version_and_explicit_normalization():
    version = _version(
        inputs={"named": {"type": "query", "cypher": "RETURN 2"}},
        activities=[{"type": "log", "input": "named", "parameters": {}}],
    )
    result = workflows.version_to_workflow(version)
    assert result.workflow_id == "workflow-1"
    assert result.version == 2
    assert result.inputs["named"].cypher == "RETURN 2"
    assert result.activities[0].input == "named"


def test_activity_schema_and_validator_remap(mocker):
    field = ActionConfigFieldDef(name="workflow", label="Workflow", type="string")
    mocker.patch.object(workflows.scheduled_query_modules, "get_action_schemas", return_value={"temporal": [field]})

    def validator(value):
        return None

    mocker.patch.object(
        workflows.scheduled_query_modules,
        "get_action_validators",
        return_value={"temporal": validator},
    )
    assert workflows.activity_schemas() == {"workflow": [field]}
    assert workflows._activity_validators() == {"workflow": validator}


def test_activity_config_validation_branches(mocker):
    required = ActionConfigFieldDef(name="target", label="Target", type="string", required=True)
    accepted = ActionConfigFieldDef(name="accepted", label="Accepted", type="boolean", required=True)
    mocker.patch.object(
        workflows,
        "_activity_schemas",
        return_value={"log": [required, accepted], "workflow": []},
    )
    mocker.patch.object(workflows, "_activity_validators", return_value={"log": lambda value: value.get("error")})

    assert "Unknown activity type" in workflows.validate_activity_configs(
        _body(activities=[{"type": "missing", "input": "query", "parameters": {}}])
    )
    assert "requires an input" in workflows.validate_activity_configs(
        _body(activities=[{"type": "log", "parameters": {"target": "x", "accepted": True}}])
    )
    assert "missing required field 'target'" in workflows.validate_activity_configs(
        _body(activities=[{"type": "log", "input": "query", "parameters": {"accepted": True}}])
    )
    assert "requires 'accepted'" in workflows.validate_activity_configs(
        _body(activities=[{"type": "log", "input": "query", "parameters": {"target": "x", "accepted": False}}])
    )
    assert "bad" in workflows.validate_activity_configs(
        _body(
            activities=[
                {
                    "type": "log",
                    "input": "query",
                    "parameters": {"target": "x", "accepted": True, "error": "bad"},
                }
            ]
        )
    )


def test_row_workflow_requires_input(mocker):
    spec = mocker.Mock(requires_rows=True)
    mocker.patch.object(workflows, "_activity_schemas", return_value={"workflow": []})
    mocker.patch.object(workflows, "_activity_validators", return_value={})
    mocker.patch.object(workflows, "get_enabled_workflow_spec", return_value=spec)
    error = workflows.validate_activity_configs(
        _body(activities=[{"type": "workflow", "parameters": {"workflow": "code"}}])
    )
    assert "requires an input for 'code'" in error


async def test_validate_definition_stops_on_activity_error(mocker):
    mocker.patch.object(workflows, "validate_activity_configs", return_value="bad activity")
    validate = mocker.patch.object(workflows, "validate_query", new=AsyncMock())
    assert await workflows.validate_definition(_body()) == "bad activity"
    validate.assert_not_awaited()


async def test_create_and_update_persist_canonical_fields(mocker):
    item = _legacy_item(inputs={"query": {"type": "query", "cypher": "RETURN 1"}}, activities=[])
    create = mocker.patch.object(workflows.report_store, "create_scheduled_query", new=AsyncMock(return_value=item))
    update = mocker.patch.object(workflows.report_store, "update_scheduled_query", new=AsyncMock(return_value=item))
    body = _body(schedule={"type": "hourly", "interval_hours": 2}, comment="change")

    assert (await workflows.create(body, "creator")).workflow_id == "workflow-1"
    assert create.await_args.kwargs["inputs"]["query"]["cypher"] == "RETURN 1"
    assert create.await_args.kwargs["actions"] == []
    assert (await workflows.update("workflow-1", body, "editor")).workflow_id == "workflow-1"
    assert update.await_args.kwargs["comment"] == "change"
    update.return_value = None
    assert await workflows.update("missing", body, "editor") is None


def test_legacy_projection_round_trip():
    canonical = _legacy_item(
        cypher="",
        actions=[],
        inputs={"only": {"type": "query", "cypher": "RETURN 3", "parameters": [{"name": "x", "value": 1}]}},
        activities=[{"type": "workflow", "input": "only", "parameters": {"workflow": "cartography_sync"}}],
    )
    projected = workflows.project_legacy_item(canonical)
    assert projected.cypher == "RETURN 3"
    assert projected.actions[0]["action_type"] == "temporal"
    assert projected.inputs is None
    assert workflows.legacy_representable(canonical)
    assert workflows.project_legacy_item(_legacy_item()) == _legacy_item()

    version = _version(inputs=canonical.inputs, activities=canonical.activities)
    projected_version = workflows.project_legacy_version(version)
    assert projected_version.actions[0]["action_type"] == "temporal"
    assert workflows.project_legacy_version(_version()) == _version()
    assert workflows.activity_as_legacy_action(workflows.normalized_activities(canonical)[0]).action_type == "temporal"


def test_legacy_representable_rejects_foreign_activity_input():
    item = _legacy_item(
        inputs={"only": {"type": "query", "cypher": "RETURN 1"}},
        activities=[{"type": "log", "input": "other", "parameters": {}}],
    )
    assert not workflows.legacy_representable(item)
