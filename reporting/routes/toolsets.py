import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.routes.query import _serialize_neo4j_value
from reporting.schema.mcp_config import (
    CallToolRequest,
    CallToolResponse,
    CreateToolRequest,
    CreateToolsetRequest,
    ToolIdResponse,
    ToolItem,
    ToolListResponse,
    ToolsetIdResponse,
    ToolsetListItem,
    ToolsetListResponse,
    ToolsetVersion,
    ToolsetVersionListResponse,
    ToolVersion,
    ToolVersionListResponse,
    UpdateToolRequest,
    UpdateToolsetRequest,
    validate_tool_arguments,
)
from reporting.services import report_store, reporting_neo4j
from reporting.services.mcp_builtins.synthetic import (
    builtin_tool,
    builtin_tools_for_group,
    builtin_toolset,
    builtin_toolsets,
    group_name_from_toolset_id,
    is_builtin_toolset_id,
)
from reporting.services.query_validator import validate_tool_cypher

logger = logging.getLogger(__name__)
router = APIRouter()


def _reject_builtin_mutation(toolset_id: str) -> None:
    """Raise an error if the value refers to or reserves a synthetic built-in.

    Accepts either a toolset ID (update/delete) or a proposed name (create).
    - Existing builtin ID  → 403 (read-only)
    - Reserved name prefix → 400 (name not allowed)
    """
    if is_builtin_toolset_id(toolset_id):
        raise HTTPException(status_code=403, detail="Built-in toolsets are read-only")
    if toolset_id.startswith("__builtin_"):
        raise HTTPException(
            status_code=400,
            detail="Toolset names starting with '__builtin_' are reserved",
        )


def _with_effective_tool_state(tool: ToolItem, toolset: ToolsetListItem) -> ToolItem:
    """Return a tool response with parent-disabled state folded in."""
    effective_enabled = tool.enabled and toolset.enabled
    disabled_reason = None
    if not toolset.enabled:
        disabled_reason = "toolset_disabled"
    elif not tool.enabled:
        disabled_reason = "tool_disabled"
    return tool.model_copy(
        update={
            "effective_enabled": effective_enabled,
            "disabled_reason": disabled_reason,
        }
    )


# ---------------------------------------------------------------------------
# Toolsets
# ---------------------------------------------------------------------------


@router.get("/api/v1/toolsets", response_model=ToolsetListResponse)
async def list_toolsets(
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_READ)),
) -> ToolsetListResponse:
    """List all toolsets (built-ins first, then user-defined)."""
    builtins = builtin_toolsets()
    user_toolsets = await report_store.list_toolsets()
    return ToolsetListResponse(toolsets=builtins + user_toolsets)


@router.post("/api/v1/toolsets", response_model=ToolsetListItem, status_code=201)
async def create_toolset(
    body: CreateToolsetRequest,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_WRITE)),
) -> ToolsetListItem:
    """Create a new toolset."""
    _reject_builtin_mutation(body.toolset_id)
    if await report_store.get_toolset(body.toolset_id):
        raise HTTPException(status_code=409, detail="Toolset already exists")
    return await report_store.create_toolset(
        toolset_id=body.toolset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        created_by=current.user.user_id,
    )


@router.get("/api/v1/toolsets/{toolset_id}", response_model=ToolsetListItem)
async def get_toolset(
    toolset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_READ)),
) -> ToolsetListItem:
    """Return a toolset by ID."""
    group = group_name_from_toolset_id(toolset_id)
    if group is not None:
        synthetic = builtin_toolset(group)
        if synthetic is None:
            raise HTTPException(status_code=404, detail="Toolset not found")
        return synthetic
    item = await report_store.get_toolset(toolset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return item


@router.put("/api/v1/toolsets/{toolset_id}", response_model=ToolsetListItem)
async def update_toolset(
    toolset_id: str,
    body: UpdateToolsetRequest,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_WRITE)),
) -> Any:
    """Update a toolset (creates a new version)."""
    _reject_builtin_mutation(toolset_id)
    item = await report_store.update_toolset(
        toolset_id=toolset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return item


@router.delete("/api/v1/toolsets/{toolset_id}", response_model=ToolsetIdResponse)
async def delete_toolset(
    toolset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_DELETE)),
) -> ToolsetIdResponse:
    """Delete a toolset and all its tools."""
    _reject_builtin_mutation(toolset_id)
    ok = await report_store.delete_toolset(toolset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return ToolsetIdResponse(toolset_id=toolset_id)


@router.get(
    "/api/v1/toolsets/{toolset_id}/versions",
    response_model=ToolsetVersionListResponse,
)
async def list_toolset_versions(
    toolset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_READ)),
) -> ToolsetVersionListResponse:
    """List all versions of a toolset."""
    if is_builtin_toolset_id(toolset_id):
        # Built-ins ship with the application — no stored version history.
        return ToolsetVersionListResponse(versions=[])
    item = await report_store.get_toolset(toolset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Toolset not found")
    versions = await report_store.list_toolset_versions(toolset_id)
    return ToolsetVersionListResponse(versions=versions)


@router.get(
    "/api/v1/toolsets/{toolset_id}/versions/{version}",
    response_model=ToolsetVersion,
)
async def get_toolset_version(
    toolset_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.TOOLSETS_READ)),
) -> ToolsetVersion:
    """Return a specific version of a toolset."""
    if is_builtin_toolset_id(toolset_id):
        raise HTTPException(status_code=404, detail="Toolset version not found")
    v = await report_store.get_toolset_version(toolset_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="Toolset version not found")
    return v


# ---------------------------------------------------------------------------
# Tools (nested under toolsets)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v1/toolsets/{toolset_id}/tools",
    response_model=ToolListResponse,
)
async def list_tools(
    toolset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_READ)),
) -> ToolListResponse:
    """List all tools in a toolset."""
    group = group_name_from_toolset_id(toolset_id)
    if group is not None:
        if builtin_toolset(group) is None:
            raise HTTPException(status_code=404, detail="Toolset not found")
        return ToolListResponse(tools=builtin_tools_for_group(group))
    ts = await report_store.get_toolset(toolset_id)
    if not ts:
        raise HTTPException(status_code=404, detail="Toolset not found")
    tools = await report_store.list_tools(toolset_id)
    return ToolListResponse(tools=[_with_effective_tool_state(tool, ts) for tool in tools])


@router.post(
    "/api/v1/toolsets/{toolset_id}/tools",
    response_model=ToolItem,
    status_code=201,
)
async def create_tool(
    toolset_id: str,
    body: CreateToolRequest,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_WRITE)),
) -> Any:
    """Create a new tool within a toolset."""
    _reject_builtin_mutation(toolset_id)
    if await report_store.get_tool(body.tool_id):
        raise HTTPException(status_code=409, detail="Tool already exists")
    validation = await validate_tool_cypher(body.cypher, body.parameters)
    if validation.has_errors:
        return JSONResponse(
            content={
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            status_code=400,
        )
    tool = await report_store.create_tool(
        toolset_id=toolset_id,
        tool_id=body.tool_id,
        name=body.name,
        description=body.description,
        cypher=body.cypher,
        parameters=[p.model_dump() for p in body.parameters],
        enabled=body.enabled,
        created_by=current.user.user_id,
    )
    if not tool:
        raise HTTPException(status_code=404, detail="Toolset not found")
    toolset = await report_store.get_toolset(toolset_id)
    if not toolset:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return _with_effective_tool_state(tool, toolset)


@router.get(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}",
    response_model=ToolItem,
)
async def get_tool(
    toolset_id: str,
    tool_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_READ)),
) -> ToolItem:
    """Return a tool by ID."""
    if is_builtin_toolset_id(toolset_id):
        synthetic = builtin_tool(tool_id)
        if synthetic is None or synthetic.toolset_id != toolset_id:
            raise HTTPException(status_code=404, detail="Tool not found")
        return synthetic
    tool = await report_store.get_tool(tool_id)
    if not tool or tool.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    toolset = await report_store.get_toolset(toolset_id)
    if not toolset:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return _with_effective_tool_state(tool, toolset)


@router.put(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}",
    response_model=ToolItem,
)
async def update_tool(
    toolset_id: str,
    tool_id: str,
    body: UpdateToolRequest,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_WRITE)),
) -> Any:
    """Update a tool (creates a new version)."""
    _reject_builtin_mutation(toolset_id)
    existing = await report_store.get_tool(tool_id)
    if not existing or existing.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    validation = await validate_tool_cypher(body.cypher, body.parameters)
    if validation.has_errors:
        return JSONResponse(
            content={
                "errors": validation.errors,
                "warnings": validation.warnings,
            },
            status_code=400,
        )
    tool = await report_store.update_tool(
        tool_id=tool_id,
        name=body.name,
        description=body.description,
        cypher=body.cypher,
        parameters=[p.model_dump() for p in body.parameters],
        enabled=body.enabled,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    toolset = await report_store.get_toolset(toolset_id)
    if not toolset:
        raise HTTPException(status_code=404, detail="Toolset not found")
    return _with_effective_tool_state(tool, toolset)


@router.delete(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}",
    response_model=ToolIdResponse,
)
async def delete_tool(
    toolset_id: str,
    tool_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_DELETE)),
) -> ToolIdResponse:
    """Delete a tool."""
    _reject_builtin_mutation(toolset_id)
    existing = await report_store.get_tool(tool_id)
    if not existing or existing.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    ok = await report_store.delete_tool(tool_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Tool not found")
    return ToolIdResponse(tool_id=tool_id)


@router.post(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}/call",
    response_model=CallToolResponse,
)
async def call_tool(
    toolset_id: str,
    tool_id: str,
    body: CallToolRequest,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_CALL)),
) -> Any:
    """Execute a tool's Cypher query with the provided arguments."""
    if is_builtin_toolset_id(toolset_id):
        # Built-in handlers are backed by Python, not Cypher — they're invoked
        # by the MCP server, not this REST route.  Surface a clear error so
        # clients know to use the MCP endpoint instead of executing an empty
        # placeholder Cypher string.
        raise HTTPException(
            status_code=400,
            detail="Built-in tools are invoked via the MCP server",
        )
    tool = await report_store.get_tool(tool_id)
    if not tool or tool.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    toolset = await report_store.get_toolset(toolset_id)
    if not toolset:
        raise HTTPException(status_code=404, detail="Toolset not found")
    if not toolset.enabled:
        raise HTTPException(status_code=400, detail="Toolset is disabled")
    if not tool.enabled:
        raise HTTPException(status_code=400, detail="Tool is disabled")
    errors = validate_tool_arguments(tool.parameters, body.arguments)
    if errors:
        return JSONResponse(content={"errors": errors}, status_code=400)
    try:
        results = await reporting_neo4j.run_query(tool.cypher, parameters=body.arguments)
        serialized = [{key: _serialize_neo4j_value(value) for key, value in record.items()} for record in results]
        return CallToolResponse(results=serialized)
    except Exception:
        logger.exception("Tool execution failed: %s", tool_id)
        raise HTTPException(status_code=500, detail="Tool execution failed")


@router.get(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}/versions",
    response_model=ToolVersionListResponse,
)
async def list_tool_versions(
    toolset_id: str,
    tool_id: str,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_READ)),
) -> ToolVersionListResponse:
    """List all versions of a tool."""
    if is_builtin_toolset_id(toolset_id):
        # Confirm the tool actually exists so we still 404 typos.
        synthetic = builtin_tool(tool_id)
        if synthetic is None or synthetic.toolset_id != toolset_id:
            raise HTTPException(status_code=404, detail="Tool not found")
        return ToolVersionListResponse(versions=[])
    tool = await report_store.get_tool(tool_id)
    if not tool or tool.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool not found")
    versions = await report_store.list_tool_versions(tool_id)
    return ToolVersionListResponse(versions=versions)


@router.get(
    "/api/v1/toolsets/{toolset_id}/tools/{tool_id}/versions/{version}",
    response_model=ToolVersion,
)
async def get_tool_version(
    toolset_id: str,
    tool_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.TOOLS_READ)),
) -> ToolVersion:
    """Return a specific version of a tool."""
    if is_builtin_toolset_id(toolset_id):
        raise HTTPException(status_code=404, detail="Tool version not found")
    v = await report_store.get_tool_version(tool_id, version)
    if not v or v.toolset_id != toolset_id:
        raise HTTPException(status_code=404, detail="Tool version not found")
    return v
