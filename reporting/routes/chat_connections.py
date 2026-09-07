"""Owner-scoped external gateway connection status and read-only checks."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from reporting import settings
from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.external_mcp import ExternalMCPConnection, ExternalMCPConnectionsResponse
from reporting.services import external_mcp, external_mcp_connections

router = APIRouter()


@router.get("/api/v1/chat/connections", response_model=ExternalMCPConnectionsResponse)
async def list_connections(
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ExternalMCPConnectionsResponse:
    return ExternalMCPConnectionsResponse(
        connections=await external_mcp_connections.list_connections(settings.MCP_EXTERNAL_PROXIES, current)
    )


@router.post("/api/v1/chat/connections/{proxy_name}/check", response_model=ExternalMCPConnection)
async def check_connection(
    proxy_name: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ExternalMCPConnection:
    proxy = next(
        (p for p in settings.MCP_EXTERNAL_PROXIES if p.name == proxy_name and p.enabled and p.user_authorization),
        None,
    )
    if proxy is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    started_at = datetime.now(UTC).isoformat()
    external_mcp.invalidate_discovery_cache(current)
    try:
        await external_mcp.list_proxy_tools(proxy, current)
    except external_mcp.ExternalMCPError:
        pass
    connection = (await external_mcp_connections.list_connections([proxy], current))[0]
    if connection.observed_at is None or connection.observed_at < started_at:
        raise HTTPException(status_code=503, detail="The connection check could not be saved. Try again.")
    return connection
