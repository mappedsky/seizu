"""Owner-scoped external gateway connections: status, checks, and user_oauth connect flow."""

import logging
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from reporting import settings
from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.external_mcp import (
    ExternalMCPAuthMode,
    ExternalMCPAuthorizationStart,
    ExternalMCPConnection,
    ExternalMCPConnectionsResponse,
    ExternalMCPProxy,
)
from reporting.services import external_mcp, external_mcp_connections, external_mcp_oauth

logger = logging.getLogger(__name__)

router = APIRouter()

_CONNECTIONS_PAGE = "/app/chat/connections"


def _per_user_proxy(proxy_name: str) -> ExternalMCPProxy:
    proxy = next(
        (p for p in settings.MCP_EXTERNAL_PROXIES if p.name == proxy_name and p.enabled and p.per_user),
        None,
    )
    if proxy is None:
        raise HTTPException(status_code=404, detail="Connection not found")
    return proxy


def _connectable_proxy(proxy_name: str) -> ExternalMCPProxy:
    proxy = _per_user_proxy(proxy_name)
    if proxy.auth_mode != ExternalMCPAuthMode.USER_OAUTH:
        raise HTTPException(status_code=404, detail="Connection not found")
    return proxy


@router.get("/api/v1/chat/connections", response_model=ExternalMCPConnectionsResponse)
async def list_connections(
    response: Response,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ExternalMCPConnectionsResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return ExternalMCPConnectionsResponse(
        connections=await external_mcp_connections.list_connections(settings.MCP_EXTERNAL_PROXIES, current)
    )


@router.post("/api/v1/chat/connections/{proxy_name}/check", response_model=ExternalMCPConnection)
async def check_connection(
    proxy_name: str,
    response: Response,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ExternalMCPConnection:
    response.headers["Cache-Control"] = "private, no-store"
    proxy = _per_user_proxy(proxy_name)
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


@router.post("/api/v1/chat/connections/{proxy_name}/connect", response_model=ExternalMCPAuthorizationStart)
async def connect(
    proxy_name: str,
    request: Request,
    response: Response,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ExternalMCPAuthorizationStart:
    """Start authorizing Seizu at a user_oauth gateway; the browser must follow the returned URL."""
    response.headers["Cache-Control"] = "private, no-store"
    proxy = _connectable_proxy(proxy_name)
    redirect_uri = settings.MCP_EXTERNAL_OAUTH_REDIRECT_URI or str(request.url_for("external_mcp_oauth_callback"))
    try:
        start = await external_mcp_oauth.begin_connect(proxy, current, redirect_uri)
    except external_mcp_oauth.OAuthConnectError as exc:
        logger.warning("Cannot start connecting proxy %s: %s", proxy.name, exc)
        raise HTTPException(status_code=400, detail="This gateway cannot be connected. Ask an administrator.") from None
    except external_mcp_oauth.GatewayOAuthUnavailable as exc:
        logger.warning("Gateway authorization for proxy %s is unavailable: %s", proxy.name, exc)
        raise HTTPException(status_code=502, detail="The gateway is unavailable. Try again later.") from None
    except RuntimeError as exc:
        logger.error("Cannot start connecting proxy %s: %s", proxy.name, exc)
        raise HTTPException(status_code=503, detail="Connections are not configured. Ask an administrator.") from None
    # Binds the callback to this browser: an authorization response arriving
    # in a browser that did not start the connection is refused (AGT-060).
    response.set_cookie(
        key=external_mcp_oauth.CONNECT_COOKIE_NAME,
        value=start.cookie,
        max_age=external_mcp_oauth.CONNECT_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        samesite="lax",
        path=external_mcp_oauth.CALLBACK_PATH,
    )
    return ExternalMCPAuthorizationStart(authorization_url=start.authorization_url)


@router.get(external_mcp_oauth.CALLBACK_PATH, name="external_mcp_oauth_callback", include_in_schema=False)
async def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    seizu_mcp_connect: str | None = Cookie(default=None),
) -> RedirectResponse:
    """Finish a connection in the browser that started it, then return to Chat Connections."""
    query: dict[str, str]
    if error or not code:
        query = {"connect_error": "denied" if error == "access_denied" else "failed"}
    else:
        try:
            result = await external_mcp_oauth.complete_connect(
                seizu_mcp_connect, state=state or "", code=code, proxies=settings.MCP_EXTERNAL_PROXIES
            )
        except external_mcp_oauth.OAuthConnectError as exc:
            logger.warning("Connecting a gateway failed: %s", exc)
            query = {"connect_error": exc.code}
        except external_mcp_oauth.GatewayOAuthUnavailable as exc:
            logger.warning("Connecting a gateway failed: %s", exc)
            query = {"connect_error": "unavailable"}
        else:
            external_mcp.invalidate_user_discovery_cache(result.user_id)
            query = {"connected": result.proxy_name}
    redirect = RedirectResponse(f"{_CONNECTIONS_PAGE}?{urlencode(query)}", status_code=303)
    redirect.headers["Cache-Control"] = "no-store"
    redirect.delete_cookie(
        key=external_mcp_oauth.CONNECT_COOKIE_NAME,
        path=external_mcp_oauth.CALLBACK_PATH,
        httponly=True,
        secure=settings.TALISMAN_FORCE_HTTPS,
        samesite="lax",
    )
    return redirect


@router.delete("/api/v1/chat/connections/{proxy_name}/grant", status_code=204)
async def disconnect(
    proxy_name: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Revoke and delete the user's grant for a user_oauth gateway."""
    proxy = _connectable_proxy(proxy_name)
    try:
        await external_mcp_oauth.disconnect(proxy, current)
    except RuntimeError as exc:
        logger.error("Cannot disconnect proxy %s: %s", proxy.name, exc)
        raise HTTPException(status_code=503, detail="Connections are not configured. Ask an administrator.") from None
    external_mcp.invalidate_discovery_cache(current)
    await external_mcp_connections.observe(proxy, current, "authorization_required")
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
