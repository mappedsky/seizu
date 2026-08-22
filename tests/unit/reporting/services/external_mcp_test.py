from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import ListToolsResult, TextContent, Tool, ToolAnnotations

from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.report_config import User
from reporting.services import external_mcp


def _user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="provider-subject",
            iss="https://issuer.example",
            email="user@example.com",
            display_name="Test User",
            preferred_username="tester",
            created_at="2024-01-01T00:00:00+00:00",
            last_login="2024-01-01T00:00:00+00:00",
        ),
        jwt_claims={"access_token": "user-token"},
        permissions=frozenset(),
    )


def _proxy(**changes) -> ExternalMCPProxy:
    values = {
        "name": "drive",
        "url": "https://proxy.example/sse",
        "auth_mode": "header_delegation",
        "header_mappings": {"user_id": "X-Forwarded-User"},
    }
    values.update(changes)
    return ExternalMCPProxy.model_validate(values)


def test_build_headers_are_fresh_and_user_scoped(monkeypatch) -> None:
    proxy = _proxy(
        auth_mode="m2m_jwt",
        token_env="MCP_SERVICE_TOKEN",
        header_mappings={"subject": "X-External-Subject"},
    )
    monkeypatch.setenv("MCP_SERVICE_TOKEN", "service-jwt")

    first = external_mcp.build_headers(proxy, _user())
    second = external_mcp.build_headers(proxy, _user())

    assert first == {
        "Authorization": "Bearer service-jwt",
        "X-External-Subject": "provider-subject",
        "X-Target-User-ID": "user-1",
    }
    assert first is not second


def test_build_headers_formats_a_delegated_access_token() -> None:
    proxy = _proxy(
        auth_mode="bearer",
        header_mappings={"access_token": "Authorization"},
    )
    assert external_mcp.build_headers(proxy, _user()) == {"Authorization": "Bearer user-token"}


def test_bearer_without_a_token_reaches_proxy_for_oauth_challenge(monkeypatch) -> None:
    proxy = _proxy(auth_mode="bearer", token_env="MCP_PROXY_TOKEN", header_mappings={})
    monkeypatch.delenv("MCP_PROXY_TOKEN", raising=False)

    assert external_mcp.build_headers(proxy, _user()) == {}


def test_oauth_challenge_parses_rfc9728_metadata_inside_exception_group() -> None:
    response = SimpleNamespace(
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer realm="mcp", resource_metadata="https://proxy.example/.well-known/mcp"'},
    )
    child = RuntimeError("unauthorized")
    child.response = response
    group = ExceptionGroup("transport", [RuntimeError("reader stopped"), child])

    challenge = external_mcp._oauth_challenge(group, "drive")

    assert challenge == external_mcp.OAuthChallenge(
        proxy_name="drive",
        resource_metadata="https://proxy.example/.well-known/mcp",
    )


async def test_list_proxy_tools_paginates_and_namespaces(mocker) -> None:
    session = mocker.AsyncMock()
    session.list_tools.side_effect = [
        ListToolsResult(
            tools=[
                Tool(
                    name="search",
                    description="Search files",
                    input_schema={"type": "object"},
                    annotations=ToolAnnotations(read_only_hint=True),
                )
            ],
            next_cursor="page-2",
        ),
        ListToolsResult(tools=[Tool(name="read", description="Read file", input_schema={"type": "object"})]),
    ]

    @asynccontextmanager
    async def fake_session(proxy, current_user):
        yield session

    mocker.patch.object(external_mcp, "_session", fake_session)

    tools = await external_mcp.list_proxy_tools(_proxy(), _user())

    assert [tool.name for tool in tools] == ["ext__drive__search", "ext__drive__read"]
    assert tools[0].annotations == ToolAnnotations(read_only_hint=True)
    assert session.list_tools.await_count == 2


def test_confirmation_policy_uses_hints_then_proxy_fallback(mocker) -> None:
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS", [])
    fallback_on = _proxy(require_confirmation=True)
    fallback_off = _proxy(require_confirmation=False)

    assert external_mcp.tool_requires_confirmation(fallback_on, "search", None) is True
    assert external_mcp.tool_requires_confirmation(fallback_off, "search", None) is False
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_on,
            "search",
            ToolAnnotations(read_only_hint=True),
        )
        is False
    )
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_on,
            "mkdir",
            ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )
        is False
    )
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_off,
            "write",
            ToolAnnotations(destructive_hint=True),
        )
        is True
    )
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_off,
            "send",
            ToolAnnotations(open_world_hint=True),
        )
        is True
    )
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_off,
            "edit",
            ToolAnnotations(idempotent_hint=False),
        )
        is True
    )
    # Partial reassuring hints do not establish a complete safe mutation
    # profile, so the configured fallback still decides.
    assert (
        external_mcp.tool_requires_confirmation(
            fallback_on,
            "mkdir",
            ToolAnnotations(destructive_hint=False, idempotent_hint=True),
        )
        is True
    )


def test_local_confirmation_override_wins_over_read_only_hint(mocker) -> None:
    mocker.patch.object(
        external_mcp.settings,
        "MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS",
        ["ext__drive__search"],
    )

    assert (
        external_mcp.tool_requires_confirmation(
            _proxy(require_confirmation=False),
            "search",
            ToolAnnotations(read_only_hint=True),
        )
        is True
    )


async def test_autonomous_listing_filters_external_tools_per_annotation(mocker) -> None:
    proxy = _proxy(require_confirmation=True)
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", [proxy])
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS", [])
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        return_value=[
            Tool(
                name="ext__drive__search",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(read_only_hint=True),
            ),
            Tool(
                name="ext__drive__delete",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(destructive_hint=True),
            ),
        ],
    )

    tools = await external_mcp.list_tools_for_user(_user(), exclude_confirmation_gated=True)

    assert [tool.name for tool in tools] == ["ext__drive__search"]


async def test_unauthorized_discovery_exposes_authentication_tool(mocker) -> None:
    proxy = _proxy()
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", [proxy])
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        side_effect=external_mcp.ExternalMCPAuthenticationRequired(
            external_mcp.OAuthChallenge("drive", "https://proxy.example/.well-known/mcp")
        ),
    )

    tools = await external_mcp.list_tools_for_user(_user())

    assert [tool.name for tool in tools] == ["ext__drive__seizu_authenticate"]
    assert "https://proxy.example/.well-known/mcp" in (tools[0].description or "")


async def test_web_catalog_converts_external_tools_to_read_only_items(mocker) -> None:
    proxy = _proxy()
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        return_value=[
            Tool(
                name="ext__drive__search_files",
                title="Search files",
                description="Search Drive files",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search text"},
                        "perPage": {"type": "number", "description": "Results per page"},
                    },
                    "required": ["query"],
                },
                annotations=ToolAnnotations(read_only_hint=True),
            )
        ],
    )

    items = await external_mcp.list_tool_items_for_proxy(proxy, _user())

    assert len(items) == 1
    assert items[0].toolset_id == "__external_drive__"
    assert items[0].tool_id == "__external_ext__drive__search_files__"
    assert items[0].name == "Search files"
    assert items[0].parameters[0].name == "query"
    assert items[0].parameters[1].name == "perPage"
    assert items[0].parameters[1].type == "float"


async def test_call_tool_bounds_text_output(mocker) -> None:
    session = mocker.AsyncMock()
    session.call_tool.return_value = SimpleNamespace(
        content=[TextContent(type="text", text="x" * 100)],
        structured_content=None,
        is_error=False,
    )

    @asynccontextmanager
    async def fake_session(proxy, current_user):
        yield session

    mocker.patch.object(external_mcp, "_session", fake_session)

    result = await external_mcp.call_tool(_proxy(), "search", {}, _user(), max_bytes=80)

    assert len(result.text.encode()) <= 80
    assert "truncated" in result.text


# The refusal GitHub's code-search endpoint actually returns, verbatim.
_RATE_LIMITED = (
    "failed to search code with query '\"from jwt\" repo:mappedsky/confidant': "
    "GitHub API rate limit exceeded. Retry after 44s."
)


def _rate_limited_then_ok(mocker, *, texts: list[tuple[str, bool]]):
    session = mocker.AsyncMock()
    session.call_tool.side_effect = [
        SimpleNamespace(content=[TextContent(type="text", text=text)], structured_content=None, is_error=err)
        for text, err in texts
    ]

    @asynccontextmanager
    async def fake_session(proxy, current_user):
        yield session

    mocker.patch.object(external_mcp, "_session", fake_session)
    return session


def test_a_rate_limit_names_its_own_delay():
    assert external_mcp._rate_limit_delay(_RATE_LIMITED) == 44.0
    assert external_mcp._rate_limit_delay("429 Too Many Requests") is not None
    # Not a rate limit: a 404 must not be retried into the same 404.
    assert external_mcp._rate_limit_delay("repository not found") is None


async def test_a_rate_limited_call_waits_the_stated_delay_and_retries(mocker) -> None:
    session = _rate_limited_then_ok(mocker, texts=[(_RATE_LIMITED, True), ("hit", False)])
    slept: list[float] = []
    mocker.patch.object(external_mcp.asyncio, "sleep", mocker.AsyncMock(side_effect=lambda d: slept.append(d)))

    result = await external_mcp.call_tool(_proxy(), "search_code", {}, _user())

    assert result.text == "hit" and result.is_error is False
    assert slept == [44.0]
    assert session.call_tool.await_count == 2


async def test_a_delay_longer_than_the_cap_is_handed_back_instead(mocker) -> None:
    session = _rate_limited_then_ok(mocker, texts=[("rate limit exceeded. Retry after 600s", True)])
    sleep = mocker.patch.object(external_mcp.asyncio, "sleep", mocker.AsyncMock())
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_RATE_LIMIT_MAX_WAIT_SECONDS", 60)

    result = await external_mcp.call_tool(_proxy(), "search_code", {}, _user())

    # One delegation must not spend its whole timeout asleep.
    assert result.is_error is True
    sleep.assert_not_awaited()
    assert session.call_tool.await_count == 1


async def test_a_non_rate_limit_error_is_not_retried(mocker) -> None:
    session = _rate_limited_then_ok(mocker, texts=[("repository not found", True)])
    sleep = mocker.patch.object(external_mcp.asyncio, "sleep", mocker.AsyncMock())

    result = await external_mcp.call_tool(_proxy(), "search_code", {}, _user())

    assert result.is_error is True
    sleep.assert_not_awaited()
    assert session.call_tool.await_count == 1


async def test_retries_are_bounded(mocker) -> None:
    session = _rate_limited_then_ok(mocker, texts=[(_RATE_LIMITED, True)] * 3)
    mocker.patch.object(external_mcp.asyncio, "sleep", mocker.AsyncMock())
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_RATE_LIMIT_RETRIES", 2)

    result = await external_mcp.call_tool(_proxy(), "search_code", {}, _user())

    assert result.is_error is True
    assert session.call_tool.await_count == 3  # the call plus two retries


def test_plugin_mcp_url_prefers_operator_config_over_advertised_metadata(mocker):
    configured = ExternalMCPProxy(
        name="configured",
        url="https://proxy.example/configured",
        upstream_urls=["https://upstream.example/mcp"],
    )
    advertised = ExternalMCPProxy(name="advertised", url="https://proxy.example/advertised")
    mocker.patch("reporting.services.external_mcp.settings.MCP_EXTERNAL_PROXIES", [configured, advertised])
    mocker.patch.dict(
        external_mcp._advertised_upstream_urls,
        {"advertised": frozenset({"https://upstream.example/mcp"})},
        clear=True,
    )
    assert external_mcp.proxy_for_upstream_url("https://upstream.example/mcp") == configured


def test_plugin_mcp_url_can_be_advertised_during_initialize(mocker):
    proxy = _proxy()
    mocker.patch("reporting.services.external_mcp.settings.MCP_EXTERNAL_PROXIES", [proxy])
    mocker.patch(
        "reporting.services.external_mcp.settings.MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE",
        external_mcp.settings.ExternalPluginURLMatchMode.LAX,
    )
    mocker.patch.dict(external_mcp._advertised_upstream_urls, {}, clear=True)
    result = SimpleNamespace(
        capabilities=SimpleNamespace(
            extensions={"com.mappedsky.seizu": {"upstreamUrls": ["https://upstream.example/mcp"]}}
        ),
        meta=None,
    )

    external_mcp._record_upstream_metadata(proxy, result)

    assert external_mcp.proxy_for_upstream_url("https://upstream.example/mcp") == proxy


def test_advertised_upstream_urls_are_ignored_when_urls_do_not_bind(mocker):
    """Under the default mode nothing an upstream claims can steer a binding."""
    proxy = _proxy()
    mocker.patch("reporting.services.external_mcp.settings.MCP_EXTERNAL_PROXIES", [proxy])
    mocker.patch(
        "reporting.services.external_mcp.settings.MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE",
        external_mcp.settings.ExternalPluginURLMatchMode.NONE,
    )
    mocker.patch.dict(external_mcp._advertised_upstream_urls, {}, clear=True)
    result = SimpleNamespace(
        capabilities=SimpleNamespace(
            extensions={"com.mappedsky.seizu": {"upstreamUrls": ["https://upstream.example/mcp"]}}
        ),
        meta=None,
    )

    external_mcp._record_upstream_metadata(proxy, result)

    assert external_mcp._advertised_upstream_urls == {}


def test_advertised_upstream_urls_are_bounded(mocker):
    proxy = _proxy()
    mocker.patch("reporting.services.external_mcp.settings.MCP_EXTERNAL_PROXIES", [proxy])
    mocker.patch(
        "reporting.services.external_mcp.settings.MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE",
        external_mcp.settings.ExternalPluginURLMatchMode.LAX,
    )
    mocker.patch.dict(external_mcp._advertised_upstream_urls, {}, clear=True)
    result = SimpleNamespace(
        capabilities=SimpleNamespace(
            extensions={
                "com.mappedsky.seizu": {"upstreamUrls": [f"https://upstream.example/{index}" for index in range(500)]}
            }
        ),
        meta=None,
    )

    external_mcp._record_upstream_metadata(proxy, result)

    assert len(external_mcp._advertised_upstream_urls[proxy.name]) == 32


def _cache_user(user_id: str) -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id=user_id,
            sub=user_id,
            iss="issuer",
            created_at="2026-01-01T00:00:00+00:00",
            last_login="2026-01-01T00:00:00+00:00",
        ),
        jwt_claims={},
        permissions=frozenset(),
    )


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    external_mcp.invalidate_discovery_cache()
    yield
    external_mcp.invalidate_discovery_cache()


async def test_a_scope_discovers_each_proxy_once(mocker):
    """AGT-038: a turn asks several times for one unchanging answer."""
    proxy = _proxy()
    discover = mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    user = _cache_user("u1")

    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, user)
        await external_mcp.discover_proxy_tools(proxy, user)
        await external_mcp.discover_proxy_tools(proxy, user)

    assert discover.await_count == 1


async def test_a_scope_never_shares_one_user_s_inventory_with_another(mocker):
    """A listing is what that identity is authorized to see."""
    proxy = _proxy()
    discover = mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))

    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, _cache_user("u1"))
        await external_mcp.discover_proxy_tools(proxy, _cache_user("u2"))

    assert discover.await_count == 2


async def test_without_a_scope_or_a_ttl_discovery_stays_live(mocker):
    proxy = _proxy()
    discover = mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 0)
    user = _cache_user("u1")

    await external_mcp.discover_proxy_tools(proxy, user)
    await external_mcp.discover_proxy_tools(proxy, user)

    assert discover.await_count == 2


async def test_the_ttl_cache_survives_between_scopes(mocker):
    proxy = _proxy()
    discover = mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 300)
    user = _cache_user("u1")

    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, user)
    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, user)

    assert discover.await_count == 1


async def test_an_expired_ttl_entry_is_rediscovered(mocker):
    proxy = _proxy()
    discover = mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 300)
    clock = mocker.patch.object(external_mcp.time, "monotonic", return_value=1000.0)
    user = _cache_user("u1")

    await external_mcp.discover_proxy_tools(proxy, user)
    clock.return_value = 1000.0 + 301
    await external_mcp.discover_proxy_tools(proxy, user)

    assert discover.await_count == 2


async def test_the_ttl_cache_is_bounded(mocker):
    proxy = _proxy()
    mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 300)

    for index in range(external_mcp._DISCOVERY_TTL_MAX_ENTRIES + 25):
        await external_mcp.discover_proxy_tools(proxy, _cache_user(f"u{index}"))

    assert len(external_mcp._discovery_ttl_cache) == external_mcp._DISCOVERY_TTL_MAX_ENTRIES


async def test_a_refused_identity_drops_that_user_s_cached_inventory(mocker):
    """A cached listing describes access the upstream just refused."""
    proxy = _proxy()
    mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 300)
    kept = _cache_user("keeper")
    refused = _cache_user("refused")

    await external_mcp.discover_proxy_tools(proxy, kept)
    await external_mcp.discover_proxy_tools(proxy, refused)
    external_mcp.invalidate_discovery_cache(refused)

    assert ("keeper", proxy.name) in external_mcp._discovery_ttl_cache
    assert ("refused", proxy.name) not in external_mcp._discovery_ttl_cache


async def test_discovery_traces_which_layer_answered(mocker):
    """A served call and a live one look identical to every caller above this."""
    proxy = _proxy()
    mocker.patch.object(external_mcp, "list_proxy_tools", new=AsyncMock(return_value=[]))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_DISCOVERY_TTL_SECONDS", 300)
    recorded: list[dict] = []
    mocker.patch.object(external_mcp.telemetry, "set_attributes", side_effect=lambda _span, **kw: recorded.append(kw))
    user = _cache_user("u1")

    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, user)  # live
        await external_mcp.discover_proxy_tools(proxy, user)  # scope
    with external_mcp.discovery_scope():
        await external_mcp.discover_proxy_tools(proxy, user)  # ttl, not scope

    # Only the attribution calls: `telemetry.span` sets attributes through the
    # same function, so this must not assume it owns every recorded call.
    sources = [item["discovery_source"] for item in recorded if "discovery_source" in item]
    assert sources == ["live", "scope", "ttl"]
