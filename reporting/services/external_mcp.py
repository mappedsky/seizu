"""Per-user clients for MCP servers behind configurable identity proxies."""

import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx2
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams, TextContent, Tool, ToolAnnotations

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import (
    ExternalMCPAuthMode,
    ExternalMCPHeaderSource,
    ExternalMCPProxy,
    ExternalMCPTransport,
)
from reporting.schema.mcp_config import ToolItem, ToolsetListItem
from reporting.services import telemetry
from reporting.services.mcp_builtins.synthetic import params_from_input_schema

logger = logging.getLogger(__name__)

NAMESPACE_PREFIX = "ext"
AUTHENTICATE_TOOL_NAME = "seizu_authenticate"
EXTERNAL_TOOLSET_PREFIX = "__external_"
_SYNTHETIC_SUFFIX = "__"
_EPOCH = "1970-01-01T00:00:00+00:00"
_PLUGIN_EXTENSION_NAMESPACE = "com.mappedsky.seizu"
_advertised_upstream_urls: dict[str, frozenset[str]] = {}
_RESOURCE_METADATA_RE = re.compile(r"(?:^|[,\s])resource_metadata\s*=\s*(?:\"([^\"]+)\"|([^,\s]+))", re.I)


@dataclass(frozen=True)
class OAuthChallenge:
    proxy_name: str
    resource_metadata: str | None = None


class ExternalMCPError(RuntimeError):
    """An external proxy could not complete an MCP operation."""


class ExternalMCPAuthenticationRequired(ExternalMCPError):
    def __init__(self, challenge: OAuthChallenge) -> None:
        super().__init__(f"External MCP proxy '{challenge.proxy_name}' requires authentication")
        self.challenge = challenge


class ExternalMCPTokenExpired(ExternalMCPAuthenticationRequired):
    """A non-interactive proxy credential was rejected."""


@dataclass(frozen=True)
class ExternalToolResult:
    text: str
    is_error: bool = False


def namespaced_tool_name(proxy_name: str, remote_name: str) -> str:
    return f"{NAMESPACE_PREFIX}__{proxy_name}__{remote_name}"


def external_toolset_id(proxy_name: str) -> str:
    return f"{EXTERNAL_TOOLSET_PREFIX}{proxy_name}{_SYNTHETIC_SUFFIX}"


def external_tool_id(namespaced_name: str) -> str:
    return f"{EXTERNAL_TOOLSET_PREFIX}{namespaced_name}{_SYNTHETIC_SUFFIX}"


def parse_external_toolset_id(toolset_id: str) -> ExternalMCPProxy | None:
    if not toolset_id.startswith(EXTERNAL_TOOLSET_PREFIX) or not toolset_id.endswith(_SYNTHETIC_SUFFIX):
        return None
    proxy_name = toolset_id[len(EXTERNAL_TOOLSET_PREFIX) : -len(_SYNTHETIC_SUFFIX)]
    return next((proxy for proxy in settings.MCP_EXTERNAL_PROXIES if proxy.enabled and proxy.name == proxy_name), None)


def external_toolsets() -> list[ToolsetListItem]:
    """Configured proxies as read-only synthetic toolsets for the web catalog."""
    return [
        ToolsetListItem(
            toolset_id=external_toolset_id(proxy.name),
            name=proxy.name,
            description=f"Tools discovered dynamically from external MCP proxy {proxy.name}.",
            enabled=proxy.enabled,
            current_version=0,
            created_at=_EPOCH,
            updated_at=_EPOCH,
            created_by="",
            updated_by=None,
        )
        for proxy in settings.MCP_EXTERNAL_PROXIES
        if proxy.enabled
    ]


def external_tool_to_item(proxy: ExternalMCPProxy, tool: Tool) -> ToolItem:
    namespaced_name = tool.name
    remote_name = namespaced_name.removeprefix(f"{NAMESPACE_PREFIX}__{proxy.name}__")
    return ToolItem(
        tool_id=external_tool_id(namespaced_name),
        toolset_id=external_toolset_id(proxy.name),
        name=tool.title or remote_name,
        description=tool.description or "",
        cypher=f"-- External MCP handler: {namespaced_name}",
        parameters=params_from_input_schema(tool.input_schema),
        enabled=True,
        current_version=0,
        created_at=_EPOCH,
        updated_at=_EPOCH,
        created_by="",
        updated_by=None,
    )


def parse_namespaced_tool_name(name: str) -> tuple[ExternalMCPProxy, str] | None:
    prefix, separator, rest = name.partition("__")
    if prefix != NAMESPACE_PREFIX or not separator:
        return None
    proxy_name, separator, remote_name = rest.partition("__")
    if not separator or not proxy_name or not remote_name:
        return None
    proxy = next((item for item in settings.MCP_EXTERNAL_PROXIES if item.enabled and item.name == proxy_name), None)
    return (proxy, remote_name) if proxy is not None else None


def proxy_for_upstream_url(url: str) -> ExternalMCPProxy | None:
    """Return the configured identity proxy for a portable MCP endpoint."""
    configured = [proxy for proxy in settings.MCP_EXTERNAL_PROXIES if proxy.enabled and url in proxy.upstream_urls]
    if configured:
        return configured[0] if len(configured) == 1 else None
    advertised = [
        proxy
        for proxy in settings.MCP_EXTERNAL_PROXIES
        if proxy.enabled and url in _advertised_upstream_urls.get(proxy.name, frozenset())
    ]
    return advertised[0] if len(advertised) == 1 else None


# What a remote server may claim about itself. The aliases below come off the
# wire, so they are bounded and only consulted when a mode actually matches on
# URL -- under the default `none` the package URL is documentation and nothing
# an upstream says can influence which proxy a dependency binds to.
_MAX_ADVERTISED_UPSTREAM_URLS = 32


def _record_upstream_metadata(proxy: ExternalMCPProxy, initialize_result: Any) -> None:
    """Record portable endpoint aliases advertised during MCP initialize."""
    if settings.MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE == settings.ExternalPluginURLMatchMode.NONE:
        _advertised_upstream_urls.pop(proxy.name, None)
        return
    containers: list[Any] = []
    capabilities = getattr(initialize_result, "capabilities", None)
    extensions = getattr(capabilities, "extensions", None)
    if isinstance(extensions, dict):
        containers.append(extensions.get(_PLUGIN_EXTENSION_NAMESPACE))
    meta = getattr(initialize_result, "meta", None)
    if isinstance(meta, dict):
        containers.append(meta.get(_PLUGIN_EXTENSION_NAMESPACE))
    urls: set[str] = set()
    for value in containers:
        if not isinstance(value, dict):
            continue
        raw_urls = value.get("upstreamUrls")
        if isinstance(raw_urls, list):
            urls.update(url for url in raw_urls if isinstance(url, str) and url.startswith(("http://", "https://")))
    if urls:
        _advertised_upstream_urls[proxy.name] = frozenset(sorted(urls)[:_MAX_ADVERTISED_UPSTREAM_URLS])
    else:
        _advertised_upstream_urls.pop(proxy.name, None)


def tool_requires_confirmation(
    proxy: ExternalMCPProxy,
    remote_name: str,
    annotations: ToolAnnotations | None,
) -> bool:
    """Apply the local override, MCP hints, then the proxy fallback.

    An annotation-free or incomplete safe profile is deliberately ambiguous.
    Explicit risk signals fail closed; a mutating tool avoids confirmation only
    when it supplies the complete closed-world, non-destructive, idempotent
    profile. The synthetic authentication tool performs no remote action.
    """
    if remote_name == AUTHENTICATE_TOOL_NAME:
        return False

    name = namespaced_tool_name(proxy.name, remote_name)
    if name in settings.MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS:
        return True

    if annotations is not None:
        if annotations.read_only_hint is True:
            return False
        if (
            annotations.read_only_hint is False
            and annotations.destructive_hint is False
            and annotations.idempotent_hint is True
            and annotations.open_world_hint is False
        ):
            return False
        if (
            annotations.read_only_hint is False
            or annotations.destructive_hint is True
            or annotations.idempotent_hint is False
            or annotations.open_world_hint is True
        ):
            return True

    return proxy.require_confirmation


def _identity_value(source: ExternalMCPHeaderSource, current_user: CurrentUser) -> str | None:
    user = current_user.user
    values: dict[ExternalMCPHeaderSource, Any] = {
        ExternalMCPHeaderSource.USER_ID: user.user_id,
        ExternalMCPHeaderSource.SUBJECT: user.sub,
        ExternalMCPHeaderSource.ISSUER: user.iss,
        ExternalMCPHeaderSource.EMAIL: user.email,
        ExternalMCPHeaderSource.DISPLAY_NAME: user.display_name,
        ExternalMCPHeaderSource.PREFERRED_USERNAME: user.preferred_username,
        # Direct MCP/HTTP callers may provide this ephemeral value. Temporal
        # chat workers deliberately rebuild identity without a browser token;
        # those deployments use m2m_jwt plus a target-user header instead.
        ExternalMCPHeaderSource.ACCESS_TOKEN: current_user.jwt_claims.get("access_token"),
    }
    value = values[source]
    if value is None:
        return None
    rendered = str(value)
    if "\r" in rendered or "\n" in rendered:
        raise ExternalMCPError(f"Identity value for {source.value} is not safe for an HTTP header")
    return rendered


def build_headers(proxy: ExternalMCPProxy, current_user: CurrentUser) -> dict[str, str]:
    """Build a new header dictionary for exactly one user and operation."""
    headers: dict[str, str] = {}
    for source, header in proxy.header_mappings.items():
        value = _identity_value(source, current_user)
        if value is None:
            continue
        if source == ExternalMCPHeaderSource.ACCESS_TOKEN and header.casefold() == "authorization":
            value = f"Bearer {value}"
        headers[header] = value

    raw_service_token = os.environ.get(proxy.token_env, "") if proxy.token_env else ""
    if "\r" in raw_service_token or "\n" in raw_service_token:
        raise ExternalMCPError(f"Token environment variable for proxy '{proxy.name}' is not a valid bearer token")
    service_token = raw_service_token.strip()
    if service_token:
        headers["Authorization"] = f"Bearer {service_token}"
    if proxy.auth_mode == ExternalMCPAuthMode.M2M_JWT:
        if not service_token:
            raise ExternalMCPAuthenticationRequired(OAuthChallenge(proxy.name))
        if ExternalMCPHeaderSource.USER_ID not in proxy.header_mappings:
            headers["X-Target-User-ID"] = current_user.user.user_id
    return headers


@asynccontextmanager
async def _transport(
    proxy: ExternalMCPProxy,
    headers: dict[str, str],
    oauth_challenges: list[OAuthChallenge],
) -> AsyncIterator[tuple[Any, Any]]:
    if proxy.transport == ExternalMCPTransport.SSE:
        async with sse_client(
            proxy.url,
            headers=headers,
            timeout=proxy.connect_timeout_seconds,
            sse_read_timeout=proxy.read_timeout_seconds,
        ) as streams:
            yield streams
        return

    async def capture_oauth_challenge(response: httpx2.Response) -> None:
        challenge = _oauth_challenge_from_response(response, proxy.name)
        if challenge is not None:
            oauth_challenges.append(challenge)

    timeout = httpx2.Timeout(proxy.connect_timeout_seconds, read=proxy.read_timeout_seconds)
    async with httpx2.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        event_hooks={"response": [capture_oauth_challenge]},
    ) as http_client:
        async with streamable_http_client(proxy.url, http_client=http_client) as streams:
            yield streams


@asynccontextmanager
async def _session(proxy: ExternalMCPProxy, current_user: CurrentUser) -> AsyncIterator[ClientSession]:
    # Never cache this session or its transport: its headers carry one user's
    # delegated identity. A fresh context per operation is the confused-deputy
    # boundary when multiple chat turns share a worker process.
    headers = build_headers(proxy, current_user)
    oauth_challenges: list[OAuthChallenge] = []
    try:
        async with _transport(proxy, headers, oauth_challenges) as streams:
            async with ClientSession(*streams, read_timeout_seconds=proxy.read_timeout_seconds) as session:
                initialize_result = await session.initialize()
                _record_upstream_metadata(proxy, initialize_result)
                yield session
    except ExternalMCPAuthenticationRequired:
        raise
    except BaseException as exc:
        # StreamableHTTP turns non-2xx responses into an MCPError delivered on
        # its message stream. That exception has no response attached, so keep
        # the challenge captured by the underlying HTTP client's response hook.
        challenge = oauth_challenges[-1] if oauth_challenges else _oauth_challenge(exc, proxy.name)
        if challenge is not None:
            error_type = (
                ExternalMCPTokenExpired
                if proxy.auth_mode == ExternalMCPAuthMode.M2M_JWT
                else ExternalMCPAuthenticationRequired
            )
            raise error_type(challenge) from exc
        if isinstance(exc, Exception):
            raise ExternalMCPError(f"External MCP proxy '{proxy.name}' request failed") from exc
        raise


def _oauth_challenge_from_response(response: Any, proxy_name: str) -> OAuthChallenge | None:
    if response is not None and getattr(response, "status_code", None) == 401:
        authenticate = response.headers.get("WWW-Authenticate", "")
        match = _RESOURCE_METADATA_RE.search(authenticate)
        resource_metadata = (match.group(1) or match.group(2)) if match else None
        if resource_metadata:
            parsed = urlparse(resource_metadata)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                resource_metadata = None
        return OAuthChallenge(proxy_name=proxy_name, resource_metadata=resource_metadata)
    return None


def _oauth_challenge(exc: BaseException, proxy_name: str) -> OAuthChallenge | None:
    """Find a 401 response even when AnyIO wrapped it in an exception group."""
    challenge = _oauth_challenge_from_response(getattr(exc, "response", None), proxy_name)
    if challenge is not None:
        return challenge
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            challenge = _oauth_challenge(child, proxy_name)
            if challenge is not None:
                return challenge
    return None


# ---------------------------------------------------------------------------
# Discovery caching
# ---------------------------------------------------------------------------
#
# Discovering a proxy's tools costs a transport, an MCP initialize and a
# paginated tools/list. One chat turn asks for the same answer repeatedly --
# the system prompt's capability listing, the planner's, each render's
# dependency resolution -- so without this the same fan-out happens five to ten
# times per turn for one unchanging result.
#
# Two layers, because they carry different risk. The scope memo is valid by
# construction: one identity, one turn, seconds wide, and it only ever removes
# duplicate work. The TTL cache is what makes a *cold* turn cheap, and it can
# be stale, so it is opt-in and off by default.
#
# Both key on the user. A proxy's tool listing is what *that* identity is
# authorized to see -- the delegated headers say so -- and a cache keyed by
# proxy alone would hand one user another's view. This is the same rule that
# forbids pooling the transport itself, applied to what the transport returned.

_discovery_scope: ContextVar[dict[tuple[str, str], list[Tool]] | None] = ContextVar(
    "_external_mcp_discovery_scope", default=None
)

# Bounded so a long-lived worker serving many users cannot grow without limit.
_DISCOVERY_TTL_MAX_ENTRIES = 512
_discovery_ttl_cache: OrderedDict[tuple[str, str], tuple[float, list[Tool]]] = OrderedDict()


def begin_discovery_scope() -> None:
    """Start a memo scope for the rest of this task's context.

    The imperative form, for graph nodes that already set their ambient state
    this way and have no single block to wrap. The scope dies with the task, so
    a turn is its natural extent.
    """
    _discovery_scope.set({})


@contextmanager
def discovery_scope() -> Iterator[None]:
    """Memoize proxy discovery for the duration of one turn or request.

    Outside a scope nothing is memoized, so a caller that has not opted in --
    a background path, a test -- keeps today's live behaviour.
    """
    token = _discovery_scope.set({})
    try:
        yield
    finally:
        _discovery_scope.reset(token)


def _cache_key(proxy: ExternalMCPProxy, current_user: CurrentUser) -> tuple[str, str]:
    return (current_user.user.user_id, proxy.name)


def _cached_proxy_tools(key: tuple[str, str]) -> list[Tool] | None:
    scope = _discovery_scope.get()
    if scope is not None and key in scope:
        return scope[key]
    ttl = settings.MCP_EXTERNAL_DISCOVERY_TTL_SECONDS
    if ttl <= 0:
        return None
    entry = _discovery_ttl_cache.get(key)
    if entry is None:
        return None
    expires_at, tools = entry
    if expires_at <= time.monotonic():
        del _discovery_ttl_cache[key]
        return None
    _discovery_ttl_cache.move_to_end(key)
    if scope is not None:
        scope[key] = tools
    return tools


def _store_proxy_tools(key: tuple[str, str], tools: list[Tool]) -> None:
    scope = _discovery_scope.get()
    if scope is not None:
        scope[key] = tools
    ttl = settings.MCP_EXTERNAL_DISCOVERY_TTL_SECONDS
    if ttl <= 0:
        return
    _discovery_ttl_cache[key] = (time.monotonic() + ttl, tools)
    _discovery_ttl_cache.move_to_end(key)
    while len(_discovery_ttl_cache) > _DISCOVERY_TTL_MAX_ENTRIES:
        _discovery_ttl_cache.popitem(last=False)


def invalidate_discovery_cache(current_user: CurrentUser | None = None) -> None:
    """Drop cached discovery, for one user or entirely.

    Called when an upstream stops accepting the identity we discovered under:
    a cached listing is then describing access the user no longer has, and the
    next call would fail against a stale picture rather than re-authenticate.
    """
    scope = _discovery_scope.get()
    if current_user is None:
        _discovery_ttl_cache.clear()
        if scope is not None:
            scope.clear()
        return
    user_id = current_user.user.user_id
    for key in [key for key in _discovery_ttl_cache if key[0] == user_id]:
        del _discovery_ttl_cache[key]
    if scope is not None:
        for key in [key for key in scope if key[0] == user_id]:
            del scope[key]


async def discover_proxy_tools(proxy: ExternalMCPProxy, current_user: CurrentUser) -> list[Tool]:
    """``list_proxy_tools`` through the scope memo and the optional TTL cache.

    Traced with the layer that answered, because "how often does a turn discover"
    is not visible anywhere else: a served call and a live one look identical to
    every caller above this (AGT-038).
    """
    key = _cache_key(proxy, current_user)
    scope = _discovery_scope.get()
    # Read before the lookup: a TTL hit populates the scope on its way out, so
    # asking afterwards would report every TTL hit as a scope hit.
    from_scope = scope is not None and key in scope
    with telemetry.span("mcp discovery", proxy=proxy.name) as current:
        cached = _cached_proxy_tools(key)
        if cached is not None:
            telemetry.set_attributes(
                current,
                discovery_source="scope" if from_scope else "ttl",
                tools=len(cached),
            )
            return cached
        tools = await list_proxy_tools(proxy, current_user)
        _store_proxy_tools(key, tools)
        telemetry.set_attributes(current, discovery_source="live", tools=len(tools))
        return tools


async def list_proxy_tools(proxy: ExternalMCPProxy, current_user: CurrentUser) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    async with _session(proxy, current_user) as session:
        while True:
            result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor) if cursor else None)
            for tool in result.tools:
                if tool.name == AUTHENTICATE_TOOL_NAME:
                    logger.warning("External MCP proxy %s exposed reserved tool name %s", proxy.name, tool.name)
                    continue
                tools.append(
                    Tool(
                        name=namespaced_tool_name(proxy.name, tool.name),
                        title=tool.title,
                        description=tool.description or f"{tool.name} via external MCP proxy {proxy.name}",
                        input_schema=tool.input_schema,
                        annotations=tool.annotations,
                    )
                )
            cursor = result.next_cursor
            if not cursor:
                return tools


async def list_tools_for_user(
    current_user: CurrentUser | None, *, exclude_confirmation_gated: bool = False
) -> list[Tool]:
    if current_user is None:
        return []
    tools: list[Tool] = []
    for proxy in settings.MCP_EXTERNAL_PROXIES:
        if not proxy.enabled:
            continue
        try:
            proxy_tools = await discover_proxy_tools(proxy, current_user)
            if exclude_confirmation_gated:
                proxy_tools = [
                    tool
                    for tool in proxy_tools
                    if not tool_requires_confirmation(
                        proxy,
                        tool.name.removeprefix(f"{NAMESPACE_PREFIX}__{proxy.name}__"),
                        tool.annotations,
                    )
                ]
            tools.extend(proxy_tools)
        except ExternalMCPAuthenticationRequired as exc:
            # What we hold for this user describes access the upstream just
            # refused, so it must not answer the next call.
            invalidate_discovery_cache(current_user)
            metadata = exc.challenge.resource_metadata
            detail = f" Authentication metadata: {metadata}" if metadata else ""
            tools.append(
                Tool(
                    name=namespaced_tool_name(proxy.name, AUTHENTICATE_TOOL_NAME),
                    description=(
                        f"Authenticate the current user with external MCP proxy {proxy.name} before using its tools."
                        f"{detail}"
                    ),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                )
            )
        except ExternalMCPError:
            logger.exception("Failed to discover tools from external MCP proxy %s", proxy.name)
        except Exception:
            # One malformed or non-conformant proxy must not remove every other
            # configured server (or the Seizu-native tools) from a chat turn.
            logger.exception("Invalid tool listing from external MCP proxy %s", proxy.name)
    return tools


async def list_tool_items_for_proxy(proxy: ExternalMCPProxy, current_user: CurrentUser) -> list[ToolItem]:
    """Discover one proxy for the read-only web tool catalog."""
    try:
        tools = await list_proxy_tools(proxy, current_user)
    except ExternalMCPAuthenticationRequired as exc:
        metadata = exc.challenge.resource_metadata
        detail = f" Authentication metadata: {metadata}" if metadata else ""
        tools = [
            Tool(
                name=namespaced_tool_name(proxy.name, AUTHENTICATE_TOOL_NAME),
                description=(
                    f"Authenticate the current user with external MCP proxy {proxy.name} before using its tools."
                    f"{detail}"
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        ]
    return [external_tool_to_item(proxy, tool) for tool in tools]


def authentication_payload(exc: ExternalMCPAuthenticationRequired) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "authentication_required": True,
        "proxy": exc.challenge.proxy_name,
        "error": str(exc),
    }
    if exc.challenge.resource_metadata:
        payload["resource_metadata"] = exc.challenge.resource_metadata
        payload["error"] += f". Authentication metadata: {exc.challenge.resource_metadata}"
    if isinstance(exc, ExternalMCPTokenExpired):
        payload["token_expired"] = True
    return payload


#: An upstream refusing a call for rate reasons, in the words servers use.
_RATE_LIMITED_RE = re.compile(r"rate limit (?:exceeded|reached)|too many requests|\b429\b", re.I)
#: The delay such a refusal names, when it names one ("Retry after 44s").
_RETRY_AFTER_RE = re.compile(r"retry[- ]after[:\s]+(\d+)", re.I)


def _rate_limit_delay(text: str) -> float | None:
    """Seconds to wait before retrying, or ``None`` when this is not a rate limit.

    Read from the refusal rather than backed off blindly: the upstream knows
    when its window resets and says so, and guessing either wastes the wait or
    spends the next attempt on the same refusal. A rate limit that names no
    delay gets the configured default.
    """
    if not _RATE_LIMITED_RE.search(text):
        return None
    match = _RETRY_AFTER_RE.search(text)
    if match:
        return float(match.group(1))
    return float(max(0, settings.MCP_EXTERNAL_RATE_LIMIT_DEFAULT_WAIT_SECONDS))


async def call_tool(
    proxy: ExternalMCPProxy,
    remote_name: str,
    arguments: dict[str, Any],
    current_user: CurrentUser,
    *,
    max_bytes: int | None = None,
) -> ExternalToolResult:
    """Call one external tool, waiting out a rate limit the upstream names.

    A refusal is an ordinary result on the wire, so without this it reaches the
    agent as tool output and the agent does the only thing it can: call again,
    immediately, into the same closed window. Measured on one turn: 13 of 34
    code searches refused, and the sub-agent spent calls working around results
    it could have had by waiting (AGT-029).
    """
    attempts = max(0, settings.MCP_EXTERNAL_RATE_LIMIT_RETRIES) + 1
    cap = float(max(0, settings.MCP_EXTERNAL_RATE_LIMIT_MAX_WAIT_SECONDS))
    for attempt in range(attempts):
        result = await _call_tool_once(proxy, remote_name, arguments, current_user, max_bytes=max_bytes)
        if not result.is_error or attempt == attempts - 1:
            return result
        delay = _rate_limit_delay(result.text)
        if delay is None or delay > cap:
            # Not a rate limit, or a window longer than one call may wait out.
            # Either way the agent gets the refusal and decides for itself.
            return result
        logger.info(
            "external MCP %s/%s rate-limited; waiting %.0fs before retry %d of %d",
            proxy.name,
            remote_name,
            delay,
            attempt + 1,
            attempts - 1,
        )
        await asyncio.sleep(delay)
    return result


async def _call_tool_once(
    proxy: ExternalMCPProxy,
    remote_name: str,
    arguments: dict[str, Any],
    current_user: CurrentUser,
    *,
    max_bytes: int | None = None,
) -> ExternalToolResult:
    try:
        async with _session(proxy, current_user) as session:
            if remote_name == AUTHENTICATE_TOOL_NAME:
                # A successful re-list means authentication is now present; the
                # synthetic tool is stale and the user can retry discovery.
                await session.list_tools()
                return ExternalToolResult(json.dumps({"authenticated": True, "proxy": proxy.name}))
            result = await session.call_tool(remote_name, arguments)
    except ExternalMCPAuthenticationRequired:
        raise

    rendered: list[str] = []
    for item in getattr(result, "content", []) or []:
        if isinstance(item, TextContent):
            rendered.append(item.text)
        else:
            rendered.append(item.model_dump_json(by_alias=True, exclude_none=True))
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        rendered.append(json.dumps(structured, indent=2, default=str))
    text = "\n\n".join(rendered) or "(external tool returned no content)"
    if max_bytes is not None and max_bytes > 0 and len(text.encode("utf-8")) > max_bytes:
        marker = "\n\n[external MCP result truncated to the configured byte limit]"
        marker_bytes = marker.encode("utf-8")
        if max_bytes <= len(marker_bytes):
            text = marker_bytes[:max_bytes].decode("utf-8", errors="ignore")
        else:
            allowance = max_bytes - len(marker_bytes)
            text = text.encode("utf-8")[:allowance].decode("utf-8", errors="ignore") + marker
    return ExternalToolResult(text=text, is_error=bool(getattr(result, "is_error", False)))
