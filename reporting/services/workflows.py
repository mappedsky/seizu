"""Canonical workflow model, legacy conversion, and persistence helpers.

The report store keeps the existing scheduled-query key/table namespace for a
compatibility release. New records use the explicit ``inputs`` and
``activities`` fields; old records are normalized at this boundary.
"""

from __future__ import annotations

from typing import Any

from reporting import scheduled_query_modules
from reporting.schema.report_config import (
    CreateWorkflowRequest,
    ScheduledQueryItem,
    ScheduledQueryVersion,
    WorkflowItem,
    WorkflowVersion,
)
from reporting.schema.reporting_config import (
    ScheduledQueryAction,
    WorkflowActivity,
    WorkflowQueryInput,
)
from reporting.services import report_store
from reporting.services.query_validator import validate_query
from reporting.temporal_workflows import get_enabled_workflow_spec

LEGACY_INPUT_ID = "query"


def _legacy_input(cypher: str, params: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "query",
        "cypher": cypher,
        "parameters": params,
    }


def normalized_inputs(item: ScheduledQueryItem | ScheduledQueryVersion) -> dict[str, WorkflowQueryInput]:
    raw = item.inputs
    if raw is None:
        raw = {LEGACY_INPUT_ID: _legacy_input(item.cypher, item.params)}
    return {name: WorkflowQueryInput.model_validate(value) for name, value in raw.items()}


def _legacy_activity(action: dict[str, Any]) -> WorkflowActivity:
    action_type = str(action.get("action_type", ""))
    parameters = dict(action.get("action_config") or {})
    activity_type = "workflow" if action_type == "temporal" else action_type
    input_id: str | None = LEGACY_INPUT_ID
    if activity_type == "workflow":
        workflow_name = parameters.get("workflow")
        spec = get_enabled_workflow_spec(workflow_name) if isinstance(workflow_name, str) else None
        if spec is not None and not spec.requires_rows:
            input_id = None
    return WorkflowActivity(type=activity_type, input=input_id, parameters=parameters)


def normalized_activities(item: ScheduledQueryItem | ScheduledQueryVersion) -> list[WorkflowActivity]:
    if item.activities is not None:
        return [WorkflowActivity.model_validate(value) for value in item.activities]
    return [_legacy_activity(action) for action in item.actions]


def item_to_workflow(item: ScheduledQueryItem) -> WorkflowItem:
    return WorkflowItem(
        workflow_id=item.scheduled_query_id,
        name=item.name,
        inputs=normalized_inputs(item),
        schedule=item.schedule,
        watch_scans=item.watch_scans,
        enabled=item.enabled,
        activities=normalized_activities(item),
        current_version=item.current_version,
        created_at=item.created_at,
        updated_at=item.updated_at,
        created_by=item.created_by,
        updated_by=item.updated_by,
        last_run_status=item.last_run_status,
        last_run_at=item.last_run_at,
        last_errors=item.last_errors,
        schedule_sync_status=item.schedule_sync_status,
        schedule_sync_error=item.schedule_sync_error,
        schedule_synced_at=item.schedule_synced_at,
    )


def version_to_workflow(version: ScheduledQueryVersion) -> WorkflowVersion:
    return WorkflowVersion(
        workflow_id=version.scheduled_query_id,
        name=version.name,
        version=version.version,
        inputs=normalized_inputs(version),
        schedule=version.schedule,
        watch_scans=version.watch_scans,
        enabled=version.enabled,
        activities=normalized_activities(version),
        created_at=version.created_at,
        created_by=version.created_by,
        comment=version.comment,
    )


def _activity_schemas() -> dict[str, list[Any]]:
    schemas = scheduled_query_modules.get_action_schemas()
    if "temporal" in schemas:
        temporal_schema = schemas.pop("temporal")
        schemas.setdefault("workflow", temporal_schema)
    return schemas


def _activity_validators() -> dict[str, Any]:
    validators = scheduled_query_modules.get_action_validators()
    if "temporal" in validators:
        temporal_validator = validators.pop("temporal")
        validators.setdefault("workflow", temporal_validator)
    return validators


def activity_schemas() -> dict[str, list[Any]]:
    """Return configured activity schemas, using canonical activity names."""
    return _activity_schemas()


def validate_activity_configs(body: CreateWorkflowRequest) -> str | None:
    schemas = _activity_schemas()
    validators = _activity_validators()
    for position, activity in enumerate(body.activities, start=1):
        if activity.type not in schemas:
            return f"Unknown activity type '{activity.type}'. Valid types: {sorted(schemas)}."
        if activity.type != "workflow" and activity.input is None:
            return f"Activity {position} ('{activity.type}') requires an input."
        if activity.type == "workflow":
            workflow_name = activity.parameters.get("workflow")
            spec = get_enabled_workflow_spec(workflow_name) if isinstance(workflow_name, str) else None
            if spec is not None and spec.requires_rows and activity.input is None:
                return f"Activity {position} ('workflow') requires an input for '{workflow_name}'."
        for field in schemas[activity.type]:
            if not field.required:
                continue
            value = activity.parameters.get(field.name)
            if value is None or value == "" or value == []:
                return f"Activity {position} ('{activity.type}') is missing required field '{field.name}'."
            if field.type == "boolean" and value is not True:
                return f"Activity {position} ('{activity.type}') requires '{field.name}' to be accepted."
        validator = validators.get(activity.type)
        if validator is not None:
            error = validator(activity.parameters)
            if error:
                return f"Activity {position} ('{activity.type}'): {error}"
    return None


async def validate_definition(body: CreateWorkflowRequest) -> str | None:
    error = validate_activity_configs(body)
    if error:
        return error
    for input_id, query_input in body.inputs.items():
        result = await validate_query(query_input.cypher)
        if result.has_errors:
            messages = "; ".join(str(error) for error in result.errors)
            return f"Input '{input_id}' is invalid: {messages}"
    return None


async def create(body: CreateWorkflowRequest, created_by: str) -> WorkflowItem:
    item = await report_store.create_scheduled_query(
        name=body.name,
        cypher="",
        params=[],
        frequency=None,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=[value.model_dump() for value in body.watch_scans],
        enabled=body.enabled,
        actions=[],
        created_by=created_by,
        inputs={name: value.model_dump() for name, value in body.inputs.items()},
        activities=[value.model_dump() for value in body.activities],
    )
    return item_to_workflow(item)


async def update(
    workflow_id: str,
    body: CreateWorkflowRequest,
    updated_by: str,
) -> WorkflowItem | None:
    item = await report_store.update_scheduled_query(
        sq_id=workflow_id,
        name=body.name,
        cypher="",
        params=[],
        frequency=None,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=[value.model_dump() for value in body.watch_scans],
        enabled=body.enabled,
        actions=[],
        updated_by=updated_by,
        comment=body.comment,
        inputs={name: value.model_dump() for name, value in body.inputs.items()},
        activities=[value.model_dump() for value in body.activities],
    )
    return item_to_workflow(item) if item is not None else None


def legacy_representable(item: ScheduledQueryItem | ScheduledQueryVersion) -> bool:
    """Whether a canonical record can be projected without losing meaning."""
    if item.inputs is None and item.activities is None:
        return True
    inputs = normalized_inputs(item)
    if len(inputs) != 1:
        return False
    only_input = next(iter(inputs))
    return all(activity.input in (only_input, None) for activity in normalized_activities(item))


def project_legacy_item(item: ScheduledQueryItem) -> ScheduledQueryItem:
    if item.inputs is None and item.activities is None:
        return item
    inputs = normalized_inputs(item)
    input_id, query_input = next(iter(inputs.items()))
    actions = []
    for activity in normalized_activities(item):
        actions.append(
            {
                "action_type": "temporal" if activity.type == "workflow" else activity.type,
                "action_config": activity.parameters,
            }
        )
    return item.model_copy(
        update={
            "cypher": query_input.cypher,
            "params": [value.model_dump() for value in query_input.parameters],
            "actions": actions,
            "inputs": None,
            "activities": None,
        }
    )


def project_legacy_version(version: ScheduledQueryVersion) -> ScheduledQueryVersion:
    if version.inputs is None and version.activities is None:
        return version
    inputs = normalized_inputs(version)
    _, query_input = next(iter(inputs.items()))
    actions = [
        {
            "action_type": "temporal" if activity.type == "workflow" else activity.type,
            "action_config": activity.parameters,
        }
        for activity in normalized_activities(version)
    ]
    return version.model_copy(
        update={
            "cypher": query_input.cypher,
            "params": [value.model_dump() for value in query_input.parameters],
            "actions": actions,
            "inputs": None,
            "activities": None,
        }
    )


def activity_as_legacy_action(activity: WorkflowActivity) -> ScheduledQueryAction:
    return ScheduledQueryAction(
        action_type="temporal" if activity.type == "workflow" else activity.type,
        action_config=activity.parameters,
    )
