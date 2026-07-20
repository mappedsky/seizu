"""Canonical staged workflows, legacy projection, and persistence helpers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from reporting import scheduled_query_modules, settings
from reporting.schema.report_config import (
    ActionConfigFieldDef,
    CreateWorkflowRequest,
    ScheduledQueryItem,
    ScheduledQueryVersion,
    WorkflowItem,
    WorkflowVersion,
)
from reporting.schema.reporting_config import (
    WorkflowActivity,
    WorkflowStage,
)
from reporting.services import report_store, workflow_schedules
from reporting.services.activity_config import config_json_schema, validate_config
from reporting.services.query_validator import validate_query
from reporting.temporal_workflows import (
    WORKFLOW_REGISTRY,
    enabled_workflow_names,
    get_enabled_workflow_spec,
)

LEGACY_QUERY_OUTPUT = "query"


class WorkflowDefinitionError(ValueError):
    """A submitted workflow definition is invalid."""


class WorkflowNotFoundError(LookupError):
    """A workflow is missing or is not owned by the requesting user."""


def _legacy_stages(item: ScheduledQueryItem | ScheduledQueryVersion) -> list[WorkflowStage]:
    if item.inputs is not None or item.activities is not None:
        raise ValueError(
            "This workflow uses the superseded feature-branch inputs/activities shape; recreate or reseed it"
        )
    stages = [
        WorkflowStage(
            activities=[
                WorkflowActivity(
                    type="query",
                    output=LEGACY_QUERY_OUTPUT,
                    parameters={
                        "cypher": item.cypher,
                        "parameters": item.params,
                    },
                )
            ]
        )
    ]
    for position, action in enumerate(item.actions, start=1):
        action_type = str(action.get("action_type", ""))
        parameters = dict(action.get("action_config") or {})
        activity_type = "workflow" if action_type == "temporal" else action_type
        input_name: str | None = LEGACY_QUERY_OUTPUT
        if activity_type == "workflow":
            workflow_name = parameters.get("workflow")
            spec = get_enabled_workflow_spec(workflow_name) if isinstance(workflow_name, str) else None
            if spec is not None and not spec.requires_rows:
                input_name = None
        stages.append(
            WorkflowStage(
                activities=[
                    WorkflowActivity(
                        type=activity_type,
                        input=input_name,
                        output=f"action_{position}",
                        parameters=parameters,
                    )
                ]
            )
        )
    return stages


def _migrate_cartography_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Convert a superseded cartography config (modules/pipeline) to module_runs.

    Materializes the previously-implicit create-indexes (first) and analysis
    (last) runs so migrated schedules keep their behavior. A pipeline's
    within-stage parallelism is conservatively flattened to sequential order —
    parallelism now belongs to top-level workflow stages. Unparseable configs
    are returned untouched; validation and dispatch report them clearly.
    """
    if "module_runs" in parameters:
        return parameters
    modules = parameters.get("modules")
    pipeline = parameters.get("pipeline")
    runs: list[dict[str, Any]] | None = None
    if isinstance(modules, list) and modules and all(isinstance(module, str) for module in modules):
        runs = [{"module": module, "params": {}} for module in modules]
    elif isinstance(pipeline, str) and pipeline.strip():
        try:
            runs = [
                {"module": run["module"], "params": dict(run.get("params") or {})}
                for stage in json.loads(pipeline)["stages"]
                for run in stage["runs"]
            ]
        except (json.JSONDecodeError, TypeError, KeyError):
            runs = None
    if runs is None:
        return parameters
    migrated = {key: value for key, value in parameters.items() if key not in ("modules", "pipeline")}
    migrated["module_runs"] = [
        {"module": "create-indexes", "params": {}},
        *runs,
        {"module": "analysis", "params": {}},
    ]
    return migrated


def _migrate_activity(activity: WorkflowActivity) -> WorkflowActivity:
    """Read-time migration of the superseded ``workflow`` dispatcher shape.

    Stored activities of ``type: "workflow"`` selected a code-defined workflow
    via a ``workflow`` parameter; those workflows are now top-level activity
    types. Activities without a registered ``workflow`` parameter are returned
    untouched so validation and dispatch report them clearly (an unknown name
    must not become an unknown activity type).
    """
    if activity.type != "workflow":
        return activity
    parameters = dict(activity.parameters)
    workflow_name = parameters.pop("workflow", None)
    if not isinstance(workflow_name, str) or workflow_name not in WORKFLOW_REGISTRY:
        return activity
    spec = WORKFLOW_REGISTRY[workflow_name]
    if not spec.requires_rows:
        # The old dispatcher's base schema always exposed max_rows and
        # query_return_attribute, regardless of whether the selected workflow
        # consumed rows, and the frontend defaulted them in. The top-level
        # activity type for a rowless workflow (e.g. cartography_sync) has no
        # such fields, so a stray value here would be rejected as an extra
        # input by the strict activity config model.
        parameters.pop("max_rows", None)
        parameters.pop("query_return_attribute", None)
    if workflow_name == "cartography_sync":
        parameters = _migrate_cartography_parameters(parameters)
    return activity.model_copy(update={"type": workflow_name, "parameters": parameters})


def normalized_stages(item: ScheduledQueryItem | ScheduledQueryVersion) -> list[WorkflowStage]:
    if item.stages is not None:
        stages = [WorkflowStage.model_validate(stage) for stage in item.stages]
    else:
        stages = _legacy_stages(item)
    return [
        stage.model_copy(update={"activities": [_migrate_activity(activity) for activity in stage.activities]})
        for stage in stages
    ]


def item_to_workflow(item: ScheduledQueryItem) -> WorkflowItem:
    return WorkflowItem(
        workflow_id=item.scheduled_query_id,
        name=item.name,
        stages=normalized_stages(item),
        schedule=item.schedule,
        watch_scans=item.watch_scans,
        enabled=item.enabled,
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
        stages=normalized_stages(version),
        schedule=version.schedule,
        watch_scans=version.watch_scans,
        enabled=version.enabled,
        created_at=version.created_at,
        created_by=version.created_by,
        comment=version.comment,
    )


def _module_description(module: Any, fallback: str) -> str:
    value = getattr(module, "activity_description", None)
    return str(value()) if callable(value) else fallback


def _module_type_schema(module: Any, method: str, fallback: Any) -> dict[str, Any]:
    value = getattr(module, method, None)
    annotation = value() if callable(value) else fallback
    return TypeAdapter(annotation).json_schema()


def _workflow_config_fields(spec: Any) -> list[ActionConfigFieldDef]:
    """Config fields for a code-defined workflow's top-level activity type."""
    fields: list[ActionConfigFieldDef] = []
    if spec.requires_rows:
        fields.extend(
            [
                ActionConfigFieldDef(
                    name="max_rows",
                    label="Max result rows",
                    type="number",
                    required=False,
                    default=settings.TEMPORAL_WORKFLOW_MAX_RESULT_ROWS,
                    minimum=1,
                    maximum=settings.WORKFLOW_QUERY_MAX_ROWS,
                    description="Maximum referenced-output rows passed to the workflow.",
                ),
                ActionConfigFieldDef(
                    name="query_return_attribute",
                    label="Query return attribute",
                    type="string",
                    required=False,
                    default="details",
                    description="Result field containing the data passed to the workflow.",
                ),
            ]
        )
    if spec.config_fields is not None:
        known = {field.name for field in fields}
        fields.extend(field for field in spec.config_fields() if field.name not in known)
    return fields


def activity_definitions() -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {
        "query": {
            "description": "Runs a read-only Cypher query and outputs its rows.",
            "input_required": False,
            "input_schema": {},
            "output_schema": TypeAdapter(list[dict[str, Any]]).json_schema(),
            "config_fields": [
                ActionConfigFieldDef(
                    name="cypher",
                    label="Cypher",
                    type="text",
                    required=True,
                    description="Read-only Cypher to execute.",
                ).model_dump(),
                ActionConfigFieldDef(
                    name="parameters",
                    label="Query parameters",
                    type="parameters",
                    description="Static Cypher parameters; input is reserved for the referenced output.",
                ).model_dump(),
                ActionConfigFieldDef(
                    name="max_rows",
                    label="Max rows",
                    type="number",
                    default=settings.WORKFLOW_QUERY_MAX_ROWS,
                    minimum=1,
                    maximum=settings.WORKFLOW_QUERY_MAX_ROWS,
                    description="Maximum rows retained in the output.",
                ).model_dump(),
            ],
        }
    }
    for name in scheduled_query_modules.get_configured_action_names():
        module = scheduled_query_modules.get_module(name)
        config_fields = [field.model_dump() for field in module.action_config_schema()]
        if not any(field["name"] == "max_rows" for field in config_fields):
            config_fields.append(
                ActionConfigFieldDef(
                    name="max_rows",
                    label="Max input rows",
                    type="number",
                    required=False,
                    minimum=1,
                    maximum=settings.WORKFLOW_QUERY_MAX_ROWS,
                    description="Maximum referenced-output rows passed to this activity.",
                ).model_dump()
            )
        definitions[name] = {
            "description": _module_description(module, f"Runs the {name} activity."),
            "input_required": True,
            "input_schema": _module_type_schema(module, "activity_input_type", list[dict[str, Any]]),
            "output_schema": _module_type_schema(module, "activity_output_type", dict[str, Any]),
            "config_fields": config_fields,
        }
    # Reject collisions against every registered workflow, enabled or not: a
    # module named after a disabled workflow would look valid in the editor
    # but be classified as that child workflow at dispatch and fail at runtime.
    collisions = set(definitions) & set(WORKFLOW_REGISTRY)
    if collisions:
        raise ValueError(f"Activity modules collide with code-defined workflow names: {sorted(collisions)}")
    for name in enabled_workflow_names():
        spec = get_enabled_workflow_spec(name)
        if spec is None:
            continue
        definitions[name] = {
            "description": spec.description,
            "input_required": spec.requires_rows,
            "input_schema": (TypeAdapter(list[dict[str, Any]]).json_schema() if spec.requires_rows else {}),
            "output_schema": TypeAdapter(spec.output_type).json_schema(),
            "config_fields": [field.model_dump() for field in _workflow_config_fields(spec)],
        }
    for name, definition in definitions.items():
        fields = [ActionConfigFieldDef.model_validate(field) for field in definition["config_fields"]]
        definition["config_schema"] = config_json_schema(fields, name=f"{name.title()}ActivityConfig")
    return definitions


def activity_schemas() -> dict[str, list[ActionConfigFieldDef]]:
    return {
        name: [ActionConfigFieldDef.model_validate(field) for field in definition["config_fields"]]
        for name, definition in activity_definitions().items()
    }


def _activity_validators() -> dict[str, Any]:
    validators = scheduled_query_modules.get_action_validators()
    for name in enabled_workflow_names():
        spec = get_enabled_workflow_spec(name)
        if spec is not None and spec.config_validator is not None:
            validators[name] = spec.config_validator
    return validators


def _query_parameters(activity: WorkflowActivity) -> tuple[list[dict[str, Any]], str | None]:
    raw = activity.parameters.get("parameters", [])
    if not isinstance(raw, list):
        return [], "query parameters must be a list"
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for value in raw:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            return [], "each query parameter requires a name"
        name = value["name"]
        if not name:
            return [], "query parameter names must not be empty"
        if name == "input":
            return [], "query parameter 'input' is reserved for the referenced activity output"
        if name in seen:
            return [], f"duplicate query parameter '{name}'"
        seen.add(name)
        result.append(value)
    return result, None


async def validate_definition(body: CreateWorkflowRequest) -> str | None:
    definitions = activity_definitions()
    validators = _activity_validators()
    for stage_position, stage in enumerate(body.stages, start=1):
        for activity_position, activity in enumerate(stage.activities, start=1):
            label = f"Stage {stage_position} activity {activity_position} ('{activity.type}')"
            definition = definitions.get(activity.type)
            if definition is None:
                return f"{label} has an unknown type. Valid types: {sorted(definitions)}."
            if bool(definition["input_required"]) and activity.input is None:
                return f"{label} requires an input."
            config_fields = [ActionConfigFieldDef.model_validate(field) for field in definition["config_fields"]]
            if error := validate_config(
                activity.parameters,
                config_fields,
                name=f"{activity.type.title()}ActivityConfig",
            ):
                return f"{label}: {error}"
            if activity.type == "query":
                _, error = _query_parameters(activity)
                if error:
                    return f"{label}: {error}."
                cypher = activity.parameters.get("cypher")
                if not isinstance(cypher, str) or not cypher.strip():
                    return f"{label} requires Cypher."
                validation = await validate_query(cypher)
                if validation.has_errors:
                    return f"{label} is invalid: {'; '.join(str(error) for error in validation.errors)}"
            validator = validators.get(activity.type)
            if validator is not None and (error := validator(activity.parameters)):
                return f"{label}: {error}"
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
        stages=[stage.model_dump() for stage in body.stages],
    )
    return item_to_workflow(item)


async def update(workflow_id: str, body: CreateWorkflowRequest, updated_by: str) -> WorkflowItem | None:
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
        stages=[stage.model_dump() for stage in body.stages],
    )
    return item_to_workflow(item) if item is not None else None


async def require_owned_item(
    workflow_id: str,
    user_id: str,
    *,
    legacy_only: bool = False,
) -> ScheduledQueryItem:
    """Return an owner-mutable definition or use indistinguishable 404 semantics."""

    item = await report_store.get_scheduled_query(workflow_id)
    if item is None or item.created_by != user_id or (legacy_only and not legacy_representable(item)):
        # Deliberately use identical missing/not-owner semantics so callers
        # cannot use mutation endpoints to enumerate other users' workflows.
        raise WorkflowNotFoundError("Workflow not found")
    return item


async def create_managed(body: CreateWorkflowRequest, owner_user_id: str) -> WorkflowItem:
    if error := await validate_definition(body):
        raise WorkflowDefinitionError(error)
    created = await create(body, owner_user_id)
    await workflow_schedules.reconcile_by_id(created.workflow_id)
    refreshed = await report_store.get_scheduled_query(created.workflow_id)
    return item_to_workflow(refreshed) if refreshed is not None else created


async def update_managed(
    workflow_id: str,
    body: CreateWorkflowRequest,
    owner_user_id: str,
) -> WorkflowItem:
    await require_owned_item(workflow_id, owner_user_id)
    if error := await validate_definition(body):
        raise WorkflowDefinitionError(error)
    updated = await update(workflow_id, body, owner_user_id)
    if updated is None:
        raise WorkflowNotFoundError("Workflow not found")
    await workflow_schedules.reconcile_by_id(workflow_id)
    refreshed = await report_store.get_scheduled_query(workflow_id)
    return item_to_workflow(refreshed) if refreshed is not None else updated


async def delete_managed(workflow_id: str, owner_user_id: str) -> None:
    await require_owned_item(workflow_id, owner_user_id)
    # Delete the external trigger first. If Temporal is unavailable the stored
    # definition remains, making retry safe and preventing an orphan schedule.
    await workflow_schedules.delete_schedule(workflow_id)
    if not await report_store.delete_scheduled_query(workflow_id):
        raise WorkflowNotFoundError("Workflow not found")


async def run_managed(workflow_id: str, owner_user_id: str) -> tuple[str, str | None]:
    await require_owned_item(workflow_id, owner_user_id)
    return await workflow_schedules.run_now(workflow_id)


def legacy_representable(item: ScheduledQueryItem | ScheduledQueryVersion) -> bool:
    return item.stages is None and item.inputs is None and item.activities is None


def project_legacy_item(item: ScheduledQueryItem) -> ScheduledQueryItem:
    return item


def project_legacy_version(version: ScheduledQueryVersion) -> ScheduledQueryVersion:
    return version


def has_code_workflow(item: ScheduledQueryItem | ScheduledQueryVersion) -> bool:
    return any(activity.type in WORKFLOW_REGISTRY for stage in normalized_stages(item) for activity in stage.activities)
