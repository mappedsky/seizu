from contextlib import asynccontextmanager
from types import SimpleNamespace

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
