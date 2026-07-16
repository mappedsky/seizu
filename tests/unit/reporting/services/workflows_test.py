from unittest.mock import AsyncMock

from reporting.schema.report_config import CreateWorkflowRequest, ScheduledQueryItem
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
