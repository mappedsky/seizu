"""Built-in ``toolsets__*`` and ``tools__*`` tools — manage user-defined toolsets."""

from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.mcp_config import CreateToolRequest, CreateToolsetRequest, UpdateToolRequest, UpdateToolsetRequest
from reporting.services import report_store
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema
from reporting.services.query_validator import validate_query

GROUP = "toolsets"


def _require_user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _toolset_id_prop() -> dict[str, Any]:
    return {"toolset_id": {"type": "string"}}


def _tool_id_prop() -> dict[str, Any]:
    return {"tool_id": {"type": "string"}}


def _target(action: str, resource_type: str, resource_id: Any) -> ActionConfirmationTarget:
    return ActionConfirmationTarget(action=action, resource_type=resource_type, resource_id=str(resource_id))


async def _confirm_toolset_create(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("create", "toolset", args.get("toolset_id", "new"))


async def _confirm_toolset_update(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("update", "toolset", args["toolset_id"])


async def _confirm_toolset_delete(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("delete", "toolset", args["toolset_id"])


async def _confirm_tool_create(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("create", "tool", args.get("tool_id", "new"))


async def _confirm_tool_update(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("update", "tool", args["tool_id"])


async def _confirm_tool_delete(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("delete", "tool", args["tool_id"])


# ---------------------------------------------------------------------------
# Toolset handlers
# ---------------------------------------------------------------------------


async def _list_toolsets(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    items = await report_store.list_toolsets()
    return {"toolsets": [i.model_dump() for i in items]}


async def _get_toolset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.get_toolset(args["toolset_id"])
    if not item:
        return {"error": "Toolset not found"}
    return item.model_dump()


async def _create_toolset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    body = CreateToolsetRequest.model_validate(args)
    if await report_store.get_toolset(body.toolset_id):
        return {"error": "Toolset already exists"}
    item = await report_store.create_toolset(
        toolset_id=body.toolset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        created_by=user.user.user_id,
    )
    return item.model_dump()


async def _update_toolset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    toolset_id = args["toolset_id"]
    body = UpdateToolsetRequest.model_validate({k: v for k, v in args.items() if k != "toolset_id"})
    item = await report_store.update_toolset(
        toolset_id=toolset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        updated_by=user.user.user_id,
        comment=body.comment,
    )
    if not item:
        return {"error": "Toolset not found"}
    return item.model_dump()


async def _delete_toolset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    ok = await report_store.delete_toolset(args["toolset_id"])
    if not ok:
        return {"error": "Toolset not found"}
    return {"toolset_id": args["toolset_id"]}


async def _list_toolset_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    toolset_id = args["toolset_id"]
    item = await report_store.get_toolset(toolset_id)
    if not item:
        return {"error": "Toolset not found"}
    versions = await report_store.list_toolset_versions(toolset_id)
    return {"versions": [v.model_dump() for v in versions]}


async def _get_toolset_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    v = await report_store.get_toolset_version(args["toolset_id"], int(args["version"]))
    if not v:
        return {"error": "Toolset version not found"}
    return v.model_dump()


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _list_tools(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    toolset_id = args["toolset_id"]
    ts = await report_store.get_toolset(toolset_id)
    if not ts:
        return {"error": "Toolset not found"}
    tools = await report_store.list_tools(toolset_id)
    return {"tools": [t.model_dump() for t in tools]}


async def _get_tool(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    tool = await report_store.get_tool(args["tool_id"])
    if not tool or tool.toolset_id != args["toolset_id"]:
        return {"error": "Tool not found"}
    return tool.model_dump()


async def _create_tool(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    toolset_id = args["toolset_id"]
    body = CreateToolRequest.model_validate({k: v for k, v in args.items() if k != "toolset_id"})
    if await report_store.get_tool(body.tool_id):
        return {"error": "Tool already exists"}
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    tool = await report_store.create_tool(
        toolset_id=toolset_id,
        tool_id=body.tool_id,
        name=body.name,
        description=body.description,
        cypher=body.cypher,
        parameters=[p.model_dump() for p in body.parameters],
        enabled=body.enabled,
        created_by=user.user.user_id,
    )
    if not tool:
        return {"error": "Toolset not found"}
    return tool.model_dump()


async def _update_tool(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    toolset_id = args["toolset_id"]
    tool_id = args["tool_id"]
    existing = await report_store.get_tool(tool_id)
    if not existing or existing.toolset_id != toolset_id:
        return {"error": "Tool not found"}
    body = UpdateToolRequest.model_validate({k: v for k, v in args.items() if k not in ("toolset_id", "tool_id")})
    validation = await validate_query(body.cypher)
    if validation.has_errors:
        return {"errors": validation.errors, "warnings": validation.warnings}
    tool = await report_store.update_tool(
        tool_id=tool_id,
        name=body.name,
        description=body.description,
        cypher=body.cypher,
        parameters=[p.model_dump() for p in body.parameters],
        enabled=body.enabled,
        updated_by=user.user.user_id,
        comment=body.comment,
    )
    if not tool:
        return {"error": "Tool not found"}
    return tool.model_dump()


async def _delete_tool(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    tool_id = args["tool_id"]
    existing = await report_store.get_tool(tool_id)
    if not existing or existing.toolset_id != args["toolset_id"]:
        return {"error": "Tool not found"}
    ok = await report_store.delete_tool(tool_id)
    if not ok:
        return {"error": "Tool not found"}
    return {"tool_id": tool_id}


async def _list_tool_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    tool_id = args["tool_id"]
    tool = await report_store.get_tool(tool_id)
    if not tool or tool.toolset_id != args["toolset_id"]:
        return {"error": "Tool not found"}
    versions = await report_store.list_tool_versions(tool_id)
    return {"versions": [v.model_dump() for v in versions]}


async def _get_tool_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    v = await report_store.get_tool_version(args["tool_id"], int(args["version"]))
    if not v or v.toolset_id != args["toolset_id"]:
        return {"error": "Tool version not found"}
    return v.model_dump()


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="toolsets__list",
            group=GROUP,
            description="List all toolsets.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.TOOLSETS_READ.value],
            handler=_list_toolsets,
        ),
        BuiltinTool(
            name="toolsets__get",
            group=GROUP,
            description="Return a toolset by ID.",
            input_schema={
                "type": "object",
                "properties": _toolset_id_prop(),
                "required": ["toolset_id"],
            },
            required_permissions=[Permission.TOOLSETS_READ.value],
            handler=_get_toolset,
        ),
        BuiltinTool(
            name="toolsets__create",
            group=GROUP,
            description="Create a new toolset.",
            input_schema=model_input_schema(CreateToolsetRequest),
            required_permissions=[Permission.TOOLSETS_WRITE.value],
            handler=_create_toolset,
            requires_user=True,
            confirmation=_confirm_toolset_create,
        ),
        BuiltinTool(
            name="toolsets__update",
            group=GROUP,
            description="Update a toolset (creates a new version).",
            input_schema=model_input_schema(
                UpdateToolsetRequest,
                extra_properties=_toolset_id_prop(),
                extra_required=["toolset_id"],
            ),
            required_permissions=[Permission.TOOLSETS_WRITE.value],
            handler=_update_toolset,
            requires_user=True,
            confirmation=_confirm_toolset_update,
        ),
        BuiltinTool(
            name="toolsets__delete",
            group=GROUP,
            description="Delete a toolset and all its tools.",
            input_schema={
                "type": "object",
                "properties": _toolset_id_prop(),
                "required": ["toolset_id"],
            },
            required_permissions=[Permission.TOOLSETS_DELETE.value],
            handler=_delete_toolset,
            confirmation=_confirm_toolset_delete,
        ),
        BuiltinTool(
            name="toolsets__list_versions",
            group=GROUP,
            description="List all versions of a toolset.",
            input_schema={
                "type": "object",
                "properties": _toolset_id_prop(),
                "required": ["toolset_id"],
            },
            required_permissions=[Permission.TOOLSETS_READ.value],
            handler=_list_toolset_versions,
        ),
        BuiltinTool(
            name="toolsets__get_version",
            group=GROUP,
            description="Return a specific version of a toolset.",
            input_schema={
                "type": "object",
                "properties": {
                    **_toolset_id_prop(),
                    "version": {"type": "integer"},
                },
                "required": ["toolset_id", "version"],
            },
            required_permissions=[Permission.TOOLSETS_READ.value],
            handler=_get_toolset_version,
        ),
        BuiltinTool(
            name="toolsets__list_tools",
            group=GROUP,
            description="List all tools in a toolset.",
            input_schema={
                "type": "object",
                "properties": _toolset_id_prop(),
                "required": ["toolset_id"],
            },
            required_permissions=[Permission.TOOLS_READ.value],
            handler=_list_tools,
        ),
        BuiltinTool(
            name="toolsets__get_tool",
            group=GROUP,
            description="Return a tool by ID.",
            input_schema={
                "type": "object",
                "properties": {**_toolset_id_prop(), **_tool_id_prop()},
                "required": ["toolset_id", "tool_id"],
            },
            required_permissions=[Permission.TOOLS_READ.value],
            handler=_get_tool,
        ),
        BuiltinTool(
            name="toolsets__create_tool",
            group=GROUP,
            description=(
                "Create a new tool within a toolset. The Cypher body is validated read-only at creation time."
            ),
            input_schema=model_input_schema(
                CreateToolRequest,
                extra_properties=_toolset_id_prop(),
                extra_required=["toolset_id"],
            ),
            required_permissions=[Permission.TOOLS_WRITE.value],
            handler=_create_tool,
            requires_user=True,
            confirmation=_confirm_tool_create,
        ),
        BuiltinTool(
            name="toolsets__update_tool",
            group=GROUP,
            description="Update a tool (creates a new version).",
            input_schema=model_input_schema(
                UpdateToolRequest,
                extra_properties={**_toolset_id_prop(), **_tool_id_prop()},
                extra_required=["toolset_id", "tool_id"],
            ),
            required_permissions=[Permission.TOOLS_WRITE.value],
            handler=_update_tool,
            requires_user=True,
            confirmation=_confirm_tool_update,
        ),
        BuiltinTool(
            name="toolsets__delete_tool",
            group=GROUP,
            description="Delete a tool.",
            input_schema={
                "type": "object",
                "properties": {**_toolset_id_prop(), **_tool_id_prop()},
                "required": ["toolset_id", "tool_id"],
            },
            required_permissions=[Permission.TOOLS_DELETE.value],
            handler=_delete_tool,
            confirmation=_confirm_tool_delete,
        ),
        BuiltinTool(
            name="toolsets__list_tool_versions",
            group=GROUP,
            description="List all versions of a tool.",
            input_schema={
                "type": "object",
                "properties": {**_toolset_id_prop(), **_tool_id_prop()},
                "required": ["toolset_id", "tool_id"],
            },
            required_permissions=[Permission.TOOLS_READ.value],
            handler=_list_tool_versions,
        ),
        BuiltinTool(
            name="toolsets__get_tool_version",
            group=GROUP,
            description="Return a specific version of a tool.",
            input_schema={
                "type": "object",
                "properties": {
                    **_toolset_id_prop(),
                    **_tool_id_prop(),
                    "version": {"type": "integer"},
                },
                "required": ["toolset_id", "tool_id", "version"],
            },
            required_permissions=[Permission.TOOLS_READ.value],
            handler=_get_tool_version,
        ),
    ],
)
