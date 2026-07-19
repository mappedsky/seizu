"""Built-in ``scheduled_queries__*`` tools — CRUD for scheduled queries."""

from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.report_config import CreateScheduledQueryRequest
from reporting.services import report_store, workflow_schedules, workflows
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema
from reporting.services.query_validator import validate_query
from reporting.services.scheduled_query_validation import validate_action_configs

GROUP = "scheduled_queries"


def _require_user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _id_prop() -> dict[str, Any]:
    return {"scheduled_query_id": {"type": "string"}}


async def _confirm_create(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(
        action="create", resource_type="scheduled_query", resource_id=str(args.get("name", "new"))
    )


async def _confirm_update(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(
        action="update",
        resource_type="scheduled_query",
        resource_id=str(args["scheduled_query_id"]),
    )


async def _confirm_delete(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(
        action="delete",
        resource_type="scheduled_query",
        resource_id=str(args["scheduled_query_id"]),
    )


async def _confirm_run(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(
        action="run",
        resource_type="scheduled_query",
        resource_id=str(args["scheduled_query_id"]),
    )


async def _list(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    items = await report_store.list_scheduled_queries()
    return {"scheduled_queries": [i.model_dump() for i in items if workflows.legacy_representable(i)]}


async def _get(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.get_scheduled_query(args["scheduled_query_id"])
    if not item or not workflows.legacy_representable(item):
        return {"error": "Scheduled query not found"}
    return item.model_dump()


async def _create(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    body = CreateScheduledQueryRequest.model_validate(args)
    err = validate_action_configs(body.actions)
    if err:
        return {"error": err}
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    item = await report_store.create_scheduled_query(
        name=body.name,
        cypher=body.cypher,
        params=body.params,
        frequency=body.frequency,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        created_by=user.user.user_id,
    )
    return item.model_dump()


async def _update(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    sq_id = args["scheduled_query_id"]
    try:
        await workflows.require_owned_item(sq_id, user.user.user_id, legacy_only=True)
    except workflows.WorkflowNotFoundError:
        return {"error": "Scheduled query not found"}
    body = CreateScheduledQueryRequest.model_validate({k: v for k, v in args.items() if k != "scheduled_query_id"})
    err = validate_action_configs(body.actions)
    if err:
        return {"error": err}
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    item = await report_store.update_scheduled_query(
        sq_id=sq_id,
        name=body.name,
        cypher=body.cypher,
        params=body.params,
        frequency=body.frequency,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        actions=body.actions,
        updated_by=user.user.user_id,
        comment=body.comment,
    )
    if not item:
        return {"error": "Scheduled query not found"}
    return item.model_dump()


async def _delete(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    sq_id = args["scheduled_query_id"]
    try:
        await workflows.require_owned_item(sq_id, user.user.user_id, legacy_only=True)
    except workflows.WorkflowNotFoundError:
        return {"error": "Scheduled query not found"}
    await workflow_schedules.delete_schedule(sq_id)
    ok = await report_store.delete_scheduled_query(sq_id)
    if not ok:
        return {"error": "Scheduled query not found"}
    return {"scheduled_query_id": args["scheduled_query_id"]}


async def _run(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    sq_id = args["scheduled_query_id"]
    try:
        await workflows.require_owned_item(sq_id, user.user.user_id, legacy_only=True)
    except workflows.WorkflowNotFoundError:
        return {"error": "Scheduled query not found"}
    run_requested_at = await report_store.request_scheduled_query_run(sq_id)
    if run_requested_at is None:
        return {"error": "Scheduled query not found"}
    return {"scheduled_query_id": sq_id, "run_requested_at": run_requested_at}


async def _list_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    sq_id = args["scheduled_query_id"]
    item = await report_store.get_scheduled_query(sq_id)
    if not item or not workflows.legacy_representable(item):
        return {"error": "Scheduled query not found"}
    versions = await report_store.list_scheduled_query_versions(sq_id)
    return {"versions": [v.model_dump() for v in versions if workflows.legacy_representable(v)]}


async def _get_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    v = await report_store.get_scheduled_query_version(args["scheduled_query_id"], int(args["version"]))
    if not v or not workflows.legacy_representable(v):
        return {"error": "Scheduled query version not found"}
    return v.model_dump()


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="scheduled_queries__list",
            group=GROUP,
            description="List all scheduled queries.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.SCHEDULED_QUERIES_READ.value],
            handler=_list,
        ),
        BuiltinTool(
            name="scheduled_queries__get",
            group=GROUP,
            description="Return a scheduled query by ID.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["scheduled_query_id"],
            },
            required_permissions=[Permission.SCHEDULED_QUERIES_READ.value],
            handler=_get,
        ),
        BuiltinTool(
            name="scheduled_queries__create",
            group=GROUP,
            description=(
                "Create a new scheduled query. Cypher is validated (read-only) "
                "and each action's config is checked against its declared schema."
            ),
            input_schema=model_input_schema(CreateScheduledQueryRequest),
            required_permissions=[Permission.SCHEDULED_QUERIES_WRITE.value],
            handler=_create,
            requires_user=True,
            confirmation=_confirm_create,
        ),
        BuiltinTool(
            name="scheduled_queries__update",
            group=GROUP,
            description="Update an existing scheduled query (creates a new version).",
            input_schema=model_input_schema(
                CreateScheduledQueryRequest,
                extra_properties=_id_prop(),
                extra_required=["scheduled_query_id"],
            ),
            required_permissions=[Permission.SCHEDULED_QUERIES_WRITE.value],
            handler=_update,
            requires_user=True,
            confirmation=_confirm_update,
        ),
        BuiltinTool(
            name="scheduled_queries__delete",
            group=GROUP,
            description="Delete a scheduled query.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["scheduled_query_id"],
            },
            required_permissions=[Permission.SCHEDULED_QUERIES_DELETE.value],
            handler=_delete,
            requires_user=True,
            confirmation=_confirm_delete,
        ),
        BuiltinTool(
            name="scheduled_queries__run",
            group=GROUP,
            description=(
                "Request an immediate run of a scheduled query. The scheduled-"
                "query worker picks the request up on its next poll and runs "
                "the query (with its configured actions) even if it is disabled."
            ),
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["scheduled_query_id"],
            },
            required_permissions=[Permission.SCHEDULED_QUERIES_WRITE.value],
            handler=_run,
            requires_user=True,
            confirmation=_confirm_run,
        ),
        BuiltinTool(
            name="scheduled_queries__list_versions",
            group=GROUP,
            description="List all versions of a scheduled query.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["scheduled_query_id"],
            },
            required_permissions=[Permission.SCHEDULED_QUERIES_READ.value],
            handler=_list_versions,
        ),
        BuiltinTool(
            name="scheduled_queries__get_version",
            group=GROUP,
            description="Return a specific version of a scheduled query.",
            input_schema={
                "type": "object",
                "properties": {
                    **_id_prop(),
                    "version": {"type": "integer"},
                },
                "required": ["scheduled_query_id", "version"],
            },
            required_permissions=[Permission.SCHEDULED_QUERIES_READ.value],
            handler=_get_version,
        ),
    ],
)
