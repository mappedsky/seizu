"""Synthesize built-in groups as ``ToolsetListItem`` / ``ToolItem`` records.

Every registered :class:`BuiltinGroup` surfaces through the existing
``/api/v1/toolsets`` + ``/api/v1/toolsets/<id>/tools`` routes as a
synthetic, read-only row — no new endpoint needed.

ID shape (preserved for frontend backwards compat):

* toolset: ``__builtin_<group>__``              (e.g. ``__builtin_reports__``)
* tool:    ``__builtin_<full_tool_name>__``     where ``full_tool_name`` is
           already ``<group>__<action>`` — e.g. ``__builtin_reports__create__``
"""

from typing import Any

from reporting.schema.mcp_config import ToolItem, ToolParamDef, ToolsetListItem
from reporting.services.mcp_builtins import find_builtin, list_builtin_groups
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool

_BUILTIN_PREFIX = "__builtin_"
_EPOCH = "1970-01-01T00:00:00+00:00"

_GROUP_DESCRIPTIONS: dict[str, str] = {
    "graph": ("Built-in graph tools: Neo4j schema discovery and validated ad-hoc Cypher queries."),
    "reports": (
        "Built-in report management: list, get, create, delete, pin, and set "
        "the default dashboard; version history operations included."
    ),
    "scheduled_queries": ("Built-in scheduled query management: full CRUD plus version history."),
    "spaces": (
        "Built-in space management: CRUD for spaces and their sub-spaces, the "
        "space overview pointer, and filing reports into a space. Spaces carry "
        "no version history."
    ),
    "toolsets": (
        "Built-in toolset and tool management: full CRUD for toolsets and their nested tools, with version history."
    ),
    "roles": ("Built-in role management: list built-in roles, CRUD for user-defined roles, and version history."),
}


def is_builtin_toolset_id(toolset_id: str) -> bool:
    return toolset_id.startswith(_BUILTIN_PREFIX) and toolset_id.endswith("__")


def builtin_toolset_id(group: str) -> str:
    return f"{_BUILTIN_PREFIX}{group}__"


def builtin_tool_id(tool_name: str) -> str:
    return f"{_BUILTIN_PREFIX}{tool_name}__"


def group_name_from_toolset_id(toolset_id: str) -> str | None:
    if not is_builtin_toolset_id(toolset_id):
        return None
    return toolset_id[len(_BUILTIN_PREFIX) : -2]


def tool_name_from_tool_id(tool_id: str) -> str | None:
    if not tool_id.startswith(_BUILTIN_PREFIX) or not tool_id.endswith("__"):
        return None
    return tool_id[len(_BUILTIN_PREFIX) : -2]


_JSON_TYPE_MAP = {
    "string": "string",
    "integer": "integer",
    "number": "float",
    "boolean": "boolean",
}


def _params_from_input_schema(schema: dict[str, Any]) -> list[ToolParamDef]:
    """Best-effort mapping from a JSON Schema object to ``ToolParamDef``s.

    ``ToolParamDef`` only models scalar types, so nested objects/arrays are
    rendered as a string with an annotation in the description — enough for
    the UI to communicate "this tool exists, here are its fields".
    """
    properties = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    params: list[ToolParamDef] = []
    for name, prop in properties.items():
        json_type = prop.get("type")
        if isinstance(json_type, list):
            json_type = next((t for t in json_type if t != "null"), "string")
        param_type = _JSON_TYPE_MAP.get(json_type or "string", "string")
        description = (prop.get("description") or "").strip()
        if json_type in ("object", "array"):
            prefix = f"(JSON {json_type})"
            description = f"{prefix} {description}".strip()
        params.append(
            ToolParamDef(
                name=name,
                type=param_type,
                description=description,
                required=name in required,
                default=prop.get("default"),
            )
        )
    return params


def builtin_group_to_toolset(group: BuiltinGroup) -> ToolsetListItem:
    return ToolsetListItem(
        toolset_id=builtin_toolset_id(group.name),
        name=group.name,
        description=_GROUP_DESCRIPTIONS.get(group.name, ""),
        enabled=True,
        current_version=1,
        created_at=_EPOCH,
        updated_at=_EPOCH,
        created_by="",
        updated_by=None,
    )


def builtin_tool_to_tool_item(tool: BuiltinTool) -> ToolItem:
    return ToolItem(
        tool_id=builtin_tool_id(tool.name),
        toolset_id=builtin_toolset_id(tool.group),
        name=tool.name,
        description=tool.description,
        cypher=f"-- Built-in handler: {tool.name}",
        parameters=_params_from_input_schema(tool.input_schema),
        enabled=True,
        current_version=1,
        created_at=_EPOCH,
        updated_at=_EPOCH,
        created_by="",
        updated_by=None,
    )


def builtin_toolsets() -> list[ToolsetListItem]:
    return [builtin_group_to_toolset(g) for g in list_builtin_groups()]


def builtin_toolset(group_name: str) -> ToolsetListItem | None:
    for g in list_builtin_groups():
        if g.name == group_name:
            return builtin_group_to_toolset(g)
    return None


def builtin_tools_for_group(group_name: str) -> list[ToolItem]:
    for g in list_builtin_groups():
        if g.name == group_name:
            return [builtin_tool_to_tool_item(t) for t in g.tools]
    return []


def builtin_tool(tool_id: str) -> ToolItem | None:
    tool_name = tool_name_from_tool_id(tool_id)
    if tool_name is None:
        return None
    t = find_builtin(tool_name)
    if t is None:
        return None
    return builtin_tool_to_tool_item(t)
