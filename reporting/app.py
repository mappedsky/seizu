import asyncio
import base64
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlparse

import secure
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp as StarletteASGIApp
from starlette.types import Receive, Scope, Send

from reporting import settings
from reporting.routes import auth as auth_routes
from reporting.routes import chat as chat_routes
from reporting.routes import config as config_routes
from reporting.routes import confirmations as confirmations_routes
from reporting.routes import graph as graph_routes
from reporting.routes import me as me_routes
from reporting.routes import query as query_routes
from reporting.routes import query_history as query_history_routes
from reporting.routes import reports as reports_routes
from reporting.routes import roles as roles_routes
from reporting.routes import scheduled_queries as sq_routes
from reporting.routes import skillsets as skillsets_routes
from reporting.routes import static as static_routes
from reporting.routes import toolsets as toolsets_routes
from reporting.routes import users as users_routes
from reporting.routes import validate as validate_routes
from reporting.services import report_store
from reporting.services.chat_graph import (
    close_chat_checkpoints,
    initialize_chat_checkpoints,
    validate_chat_llm_config,
)

_CSP_NONCE_PLACEHOLDER = "{{ csp_nonce() }}"


def _generate_csp_nonce() -> str:
    return base64.b64encode(secrets.token_bytes(16)).decode("ascii")


def _build_csp_policy(nonce: str | None = None) -> str:
    connect_src = ["'self'"]
    if settings.OIDC_AUTHORITY:
        parsed = urlparse(settings.OIDC_AUTHORITY)
        oidc_origin = f"{parsed.scheme}://{parsed.netloc}"  # noqa: E231
        if oidc_origin not in connect_src:
            connect_src.append(oidc_origin)
    style_src = ["'self'"]
    if nonce is not None:
        style_src.append(f"'nonce-{nonce}'")
    directives = [
        "default-src 'self'",
        f"connect-src {' '.join(connect_src)}",
        f"style-src {' '.join(style_src)}",
        # style-src governs <style> blocks (nonce-only above). style-src-attr
        # governs `style="..."` attributes — nonces don't apply to attributes,
        # so without 'unsafe-inline' here, MUI/emotion and dynamic positioning
        # libraries (react-grid-layout, @xyflow/react, popper) would have
        # their inline styles blocked. CSS-based exfil via this loophole
        # (e.g. `background:url(...)`) is still blocked by img-src below.
        "style-src-attr 'unsafe-inline'",
        # img-src allows same-origin images plus inline `data:` URIs. MUI and
        # react-grid-layout/react-resizable embed small SVG assets (resize
        # handles, etc.) as base64 `data:image/svg+xml;...` URIs. Browsers
        # disable scripting in SVGs loaded as images, and data URIs can't
        # make outbound requests, so this is safe. External http(s) images
        # are intentionally not allowed — keeping them out also closes the
        # CSS-exfil-via-background-image vector for the inline-style
        # attribute loophole above.
        "img-src 'self' data:",
        "script-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    if settings.OIDC_AUTHORITY:
        parsed = urlparse(settings.OIDC_AUTHORITY)
        oidc_origin = f"{parsed.scheme}://{parsed.netloc}"  # noqa: E231
        directives.append(f"frame-src {oidc_origin}")
    return "; ".join(directives)


def _inject_csp_nonce(content: str, nonce: str) -> str:
    return content.replace(_CSP_NONCE_PLACEHOLDER, nonce)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply security headers to every response."""

    def __init__(self, app: Any, secure_headers: secure.Secure) -> None:
        super().__init__(app)
        self._secure_headers = secure_headers

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request.state.csp_nonce = _generate_csp_nonce()
        response = await call_next(request)
        self._secure_headers.set_headers(response)
        response.headers["Content-Security-Policy"] = _build_csp_policy(request.state.csp_nonce)
        return response


_TIMEOUT_RESPONSE_BODY = b'{"error":"Request timed out"}'


class _TimeoutMiddleware:
    """Abort HTTP requests that exceed API_REQUEST_TIMEOUT seconds with a 504."""

    def __init__(self, app: StarletteASGIApp, timeout: float, exempt_paths: frozenset[str] | None = None) -> None:
        self._app = app
        self._timeout = timeout
        self._exempt_paths = exempt_paths or frozenset({"/api/v1/chat/stream"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope.get("path", "") in self._exempt_paths:
            await self._app(scope, receive, send)
            return

        response_started = False

        async def _tracked_send(message: Any) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await asyncio.wait_for(
                self._app(scope, receive, _tracked_send),
                timeout=self._timeout,
            )
        except TimeoutError:
            if not response_started:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 504,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(_TIMEOUT_RESPONSE_BODY)).encode()),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": _TIMEOUT_RESPONSE_BODY,
                        "more_body": False,
                    }
                )
            else:
                await send({"type": "http.response.body", "body": b"", "more_body": False})


_CSRF_HEADER_NAME = "x-seizu-csrf"
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_CSRF_FAILURE_BODY = b'{"error":"Missing or invalid CSRF header"}'


class _CSRFMiddleware:
    """Require ``X-Seizu-Csrf`` on mutating requests authenticated by cookie.

    CSRF is only possible when the browser attaches credentials automatically
    (cookies), not when the client sets ``Authorization: Bearer`` itself.  So
    this middleware checks for the session cookie on mutating requests; if
    the cookie is present, it requires the custom header (defended by the
    browser's preflight rules — an attacker page cannot add a non-CORS-safe
    header on a cross-origin request without preflight, and our CORS posture
    never authorizes the header for foreign origins).

    Bearer-only requests (CLI, MCP, programmatic) skip the check entirely.
    """

    def __init__(self, app: StarletteASGIApp, *, cookie_name: str) -> None:
        self._app = app
        self._cookie_name = cookie_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method in _CSRF_SAFE_METHODS:
            await self._app(scope, receive, send)
            return

        raw_headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
        cookie_value = b""
        csrf_header_present = False
        for name, value in raw_headers:
            if name == b"cookie":
                cookie_value = value
            elif name == _CSRF_HEADER_NAME.encode("ascii"):
                if value.strip():
                    csrf_header_present = True

        if not cookie_value:
            await self._app(scope, receive, send)
            return

        cookies: SimpleCookie = SimpleCookie()
        try:
            cookies.load(cookie_value.decode("latin-1"))
        except Exception:  # noqa: BLE001 — malformed cookies = no session cookie present
            await self._app(scope, receive, send)
            return
        if self._cookie_name not in cookies:
            await self._app(scope, receive, send)
            return

        if csrf_header_present:
            await self._app(scope, receive, send)
            return

        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(_CSRF_FAILURE_BODY)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _CSRF_FAILURE_BODY, "more_body": False})


class _MCPMiddleware:
    """Pure ASGI middleware that intercepts /api/v1/mcp* before FastAPI routing.

    In Starlette 1.0.0, Mount("/api/v1/mcp") only matches paths with a
    trailing slash (regex ^/api/v1/mcp/.*).  The GET /{full_path:path} SPA
    catch-all returns a PARTIAL match for POST /api/v1/mcp, which causes
    FastAPI to return 405 before Starlette can issue its automatic redirect.
    This middleware runs ahead of the router and bypasses that issue entirely.
    """

    def __init__(self, app: StarletteASGIApp, mcp_app: StarletteASGIApp) -> None:
        self._app = app
        self._mcp_app = mcp_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == "/api/v1/mcp" or path.startswith("/api/v1/mcp/") or path.startswith("/.well-known/oauth-"):
                await self._mcp_app(scope, receive, send)
                return
        await self._app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    should_init = settings.DYNAMODB_CREATE_TABLE or (settings.REPORT_STORE_BACKEND == "sqlmodel")
    if should_init:
        await report_store.initialize()
    if settings.CHAT_ENABLED:
        validate_chat_llm_config()
        await initialize_chat_checkpoints()
    try:
        mcp_session_manager = getattr(app.state, "mcp_session_manager", None)
        if mcp_session_manager is not None:
            async with mcp_session_manager.run():
                yield
        else:
            yield
    finally:
        if settings.CHAT_ENABLED:
            await close_chat_checkpoints()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Seizu",
        version="1.0.0",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # Security headers middleware
    hsts = None
    if settings.TALISMAN_FORCE_HTTPS:
        hsts = secure.StrictTransportSecurity().max_age(31536000).include_subdomains()
    secure_headers = secure.Secure(
        server=secure.Server().set(""),
        hsts=hsts,
        referrer=secure.ReferrerPolicy().strict_origin_when_cross_origin(),
        xcto=secure.XContentTypeOptions().nosniff(),
    )
    app.add_middleware(_SecurityHeadersMiddleware, secure_headers=secure_headers)
    app.add_middleware(_TimeoutMiddleware, timeout=settings.API_REQUEST_TIMEOUT)
    app.add_middleware(_CSRFMiddleware, cookie_name=settings.SESSION_COOKIE_NAME)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": str(exc.detail) if exc.detail else str(exc.status_code),
            },
        )

    # API routers
    for router_module in [
        auth_routes,
        config_routes,
        confirmations_routes,
        graph_routes,
        me_routes,
        query_routes,
        query_history_routes,
        reports_routes,
        roles_routes,
        sq_routes,
        skillsets_routes,
        toolsets_routes,
        users_routes,
        validate_routes,
        static_routes,
    ]:
        app.include_router(router_module.router)

    if settings.CHAT_ENABLED:
        app.include_router(chat_routes.router)

    # MCP server — wired in as a pure ASGI middleware so it intercepts
    # /api/v1/mcp* before FastAPI's router.  This avoids a Starlette 1.0.0
    # issue where Mount("/api/v1/mcp") requires a trailing slash, causing the
    # GET /{full_path:path} SPA catch-all to return 405 for POST /api/v1/mcp.
    if settings.MCP_ENABLED:
        from reporting.services.mcp_server import get_mcp_app

        mcp_session_manager, mcp_asgi = get_mcp_app()
        app.state.mcp_session_manager = mcp_session_manager
        app.add_middleware(_MCPMiddleware, mcp_app=mcp_asgi)

    # Static files from the React build
    static_folder = settings.STATIC_FOLDER
    static_dir = os.path.join(static_folder, "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Root-level static files (manifest.json, favicon, etc.) and SPA catch-all
    _register_static_routes(app, static_folder)

    return app


_ROOT_STATIC_FILES = {
    "manifest.json",
    "asset-manifest.json",
    "favicon.png",
    "favicon.svg",
}


def _register_static_routes(app: FastAPI, static_folder: str) -> None:
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str, request: Request) -> Response:
        # Strip leading slashes from path
        path = full_path.lstrip("/")

        # Serve known root-level static files directly
        if path in _ROOT_STATIC_FILES:
            # Use basename to prevent path traversal even though path is already
            # validated against the fixed set of known filenames.
            file_path = os.path.join(static_folder, os.path.basename(path))
            if os.path.isfile(file_path):
                return FileResponse(file_path)

        # Fall back to serving index.html for all other paths (SPA)
        index_path = os.path.join(static_folder, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, encoding="utf-8") as f:
                content = _inject_csp_nonce(f.read(), request.state.csp_nonce)
            return HTMLResponse(
                content=content,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                },
            )
        # If no index.html exists (dev mode without a frontend build), return 404
        return JSONResponse(content={"error": "Not found"}, status_code=404)
