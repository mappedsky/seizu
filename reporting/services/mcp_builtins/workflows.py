"""Built-in ``workflows__*`` tools for configurable Temporal workflows."""

from collections.abc import Awaitable, Callable
from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.report_config import CreateWorkflowRequest
from reporting.services import report_store, workflows
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema

GROUP = "workflows"


def _user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _id_prop() -> dict[str, Any]:
    return {"workflow_id": {"type": "string"}}


def _confirmation(
    action: str,
) -> Callable[
    [dict[str, Any], CurrentUser | None],
    Awaitable[ActionConfirmationTarget],
]:
    async def resolve(
        args: dict[str, Any],
        current_user: CurrentUser | None,
    ) -> ActionConfirmationTarget:
        del current_user
        return ActionConfirmationTarget(
            action=action,
            resource_type="workflow",
            resource_id=str(args.get("workflow_id") or args.get("name") or "new"),
        )

    return resolve


_confirm_create = _confirmation("create")
_confirm_update = _confirmation("update")
_confirm_delete = _confirmation("delete")
_confirm_run = _confirmation("run")


async def _list(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    items = await report_store.list_scheduled_queries()
    return {"workflows": [workflows.item_to_workflow(item).model_dump() for item in items]}


async def _get(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.get_scheduled_query(args["workflow_id"])
    return workflows.item_to_workflow(item).model_dump() if item else {"error": "Workflow not found"}


async def _create(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    body = CreateWorkflowRequest.model_validate(args)
    try:
        item = await workflows.create_managed(body, _user(current_user).user.user_id)
    except workflows.WorkflowDefinitionError as exc:
        return {"error": str(exc)}
    return item.model_dump()


async def _update(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    workflow_id = args["workflow_id"]
    body = CreateWorkflowRequest.model_validate({key: value for key, value in args.items() if key != "workflow_id"})
    try:
        item = await workflows.update_managed(workflow_id, body, _user(current_user).user.user_id)
    except (workflows.WorkflowDefinitionError, workflows.WorkflowNotFoundError) as exc:
        return {"error": str(exc)}
    return item.model_dump()


async def _delete(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    workflow_id = args["workflow_id"]
    try:
        await workflows.delete_managed(workflow_id, _user(current_user).user.user_id)
    except workflows.WorkflowNotFoundError as exc:
        return {"error": str(exc)}
    return {"workflow_id": workflow_id}


async def _run(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    workflow_id = args["workflow_id"]
    try:
        temporal_workflow_id, run_id = await workflows.run_managed(
            workflow_id,
            _user(current_user).user.user_id,
        )
    except workflows.WorkflowNotFoundError as exc:
        return {"error": str(exc)}
    return {"workflow_id": workflow_id, "temporal_workflow_id": temporal_workflow_id, "run_id": run_id}


async def _versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    versions = await report_store.list_scheduled_query_versions(args["workflow_id"])
    return {"versions": [workflows.version_to_workflow(version).model_dump() for version in versions]}


async def _get_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    version = await report_store.get_scheduled_query_version(args["workflow_id"], int(args["version"]))
    return workflows.version_to_workflow(version).model_dump() if version else {"error": "Workflow version not found"}


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="workflows__list",
            group=GROUP,
            description="List configurable workflows.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.WORKFLOWS_READ.value],
            handler=_list,
        ),
        BuiltinTool(
            name="workflows__get",
            group=GROUP,
            description="Get a workflow.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["workflow_id"],
            },
            required_permissions=[Permission.WORKFLOWS_READ.value],
            handler=_get,
        ),
        BuiltinTool(
            name="workflows__create",
            group=GROUP,
            description="Create a Temporal-backed configurable workflow.",
            input_schema=model_input_schema(CreateWorkflowRequest),
            required_permissions=[Permission.WORKFLOWS_WRITE.value],
            handler=_create,
            requires_user=True,
            confirmation=_confirm_create,
        ),
        BuiltinTool(
            name="workflows__update",
            group=GROUP,
            description="Update a workflow and create a version.",
            input_schema=model_input_schema(
                CreateWorkflowRequest,
                extra_properties=_id_prop(),
                extra_required=["workflow_id"],
            ),
            required_permissions=[Permission.WORKFLOWS_WRITE.value],
            handler=_update,
            requires_user=True,
            confirmation=_confirm_update,
        ),
        BuiltinTool(
            name="workflows__delete",
            group=GROUP,
            description="Delete a workflow.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["workflow_id"],
            },
            required_permissions=[Permission.WORKFLOWS_DELETE.value],
            handler=_delete,
            requires_user=True,
            confirmation=_confirm_delete,
        ),
        BuiltinTool(
            name="workflows__run",
            group=GROUP,
            description="Start an immediate workflow run.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["workflow_id"],
            },
            required_permissions=[Permission.WORKFLOWS_WRITE.value],
            handler=_run,
            requires_user=True,
            confirmation=_confirm_run,
        ),
        BuiltinTool(
            name="workflows__list_versions",
            group=GROUP,
            description="List workflow versions.",
            input_schema={
                "type": "object",
                "properties": _id_prop(),
                "required": ["workflow_id"],
            },
            required_permissions=[Permission.WORKFLOWS_READ.value],
            handler=_versions,
        ),
        BuiltinTool(
            name="workflows__get_version",
            group=GROUP,
            description="Get a workflow version.",
            input_schema={
                "type": "object",
                "properties": {
                    **_id_prop(),
                    "version": {"type": "integer", "minimum": 1},
                },
                "required": ["workflow_id", "version"],
            },
            required_permissions=[Permission.WORKFLOWS_READ.value],
            handler=_get_version,
        ),
    ],
)
