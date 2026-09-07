"""Gateway authority boundaries, transport failures, and service-token renewal."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from mcp.types import ListToolsResult, TextContent, Tool

from reporting.authnz import CurrentUser
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.report_config import User
from reporting.services import external_mcp, external_mcp_connections, external_mcp_tokens


def proxy(**updates):
    return ExternalMCPProxy.model_validate(
        {
            "name": "gateway",
            "url": "https://gateway.test/mcp",
            "transport": "streamable_http",
            "auth_mode": "header_delegation",
            "user_authorization": {"reauthorize_url": "https://gateway.test/accounts"},
            **updates,
        }
    )


def user(user_id="owner"):
    return CurrentUser(
        user=User(user_id=user_id, sub=user_id, iss="https://idp.test", created_at="now", last_login="now"),
        jwt_claims={},
        permissions=frozenset(),
    )


def credentials_proxy(**updates):
    return proxy(
        auth_mode="m2m_jwt",
        client_credentials={
            "token_url": "https://idp.test/token",
            "client_id": "seizu",
            "client_secret_env": "GATEWAY_SECRET",
            **updates,
        },
    )


@pytest.fixture(autouse=True)
def clean_state(mocker, monkeypatch):
    external_mcp_tokens._credentials.clear()
    external_mcp.invalidate_discovery_cache()
    monkeypatch.setenv("GATEWAY_SECRET", "private-secret")
    mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")
    yield
    external_mcp_tokens._credentials.clear()
    external_mcp.invalidate_discovery_cache()


def token_server(mocker, handler):
    mocker.patch.object(
        external_mcp_tokens,
        "AsyncOAuth2Client",
        side_effect=lambda **kwargs: AsyncOAuth2Client(transport=httpx.MockTransport(handler), **kwargs),
    )


@pytest.mark.parametrize("method", ["client_secret_basic", "client_secret_post"])
async def test_token_acquisition_coalesces_renews_and_keeps_user_headers_out(mocker, method):
    requests = []

    async def handle(request):
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(
            200, json={"access_token": f"token-{len(requests)}", "token_type": "Bearer", "expires_in": 3600}
        )

    token_server(mocker, handle)
    config = credentials_proxy(token_endpoint_auth_method=method, scope="mcp", audience="gateway")
    values = await asyncio.gather(*(external_mcp_tokens.service_token(config) for _ in range(8)))
    assert values == ["token-1"] * 8
    assert len(requests) == 1
    assert b"grant_type=client_credentials" in requests[0].content
    assert b"audience=gateway" in requests[0].content
    assert b"scope=mcp" in requests[0].content
    assert "x-target-user-id" not in requests[0].headers
    if method == "client_secret_basic":
        assert requests[0].headers["authorization"].startswith("Basic ")
    else:
        assert b"client_secret=private-secret" in requests[0].content
    entry = next(iter(external_mcp_tokens._credentials.values()))
    entry.renew_at = 0
    assert await external_mcp_tokens.service_token(config) == "token-2"
    external_mcp_tokens.invalidate_service_token(config, "token-1")
    assert await external_mcp_tokens.service_token(config) == "token-2"
    external_mcp_tokens.invalidate_service_token(config, "token-2")
    assert await external_mcp_tokens.service_token(config) == "token-3"


@pytest.mark.parametrize(
    "response",
    [
        {"access_token": "secret", "token_type": "Bearer"},
        {"access_token": "secret", "token_type": "Bearer", "expires_in": 0},
        {"access_token": "secret", "token_type": "Bearer", "expires_in": "NaN"},
        {"access_token": "secret", "token_type": "MAC", "expires_in": 60},
        {"access_token": "bad\nsecret", "token_type": "Bearer", "expires_in": 60},
        {"access_token": "", "token_type": "Bearer", "expires_in": 60},
    ],
)
async def test_invalid_token_responses_are_sanitized(mocker, response):
    token_server(mocker, lambda request: httpx.Response(200, json=response))
    with pytest.raises(external_mcp_tokens.ServiceCredentialError) as caught:
        await external_mcp_tokens.service_token(credentials_proxy())
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("failure", ["timeout", "redirect", "rejected"])
async def test_token_endpoint_failure_has_no_redirect_or_retry(mocker, failure):
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-secret")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.test/token"})
        return httpx.Response(401, json={"error": "invalid_client", "error_description": "private-secret"})

    token_server(mocker, handle)
    with pytest.raises(external_mcp_tokens.ServiceCredentialError, match="Service authentication"):
        await external_mcp_tokens.service_token(credentials_proxy())
    assert len(requests) == 1


@pytest.mark.parametrize("transport", ["sse", "streamable_http"])
@pytest.mark.parametrize(
    "code,marker,status",
    [
        (401, None, "service_authentication_failed"),
        (403, "user_authorization_required", "authorization_required"),
        (403, None, "permission_denied"),
    ],
)
async def test_actual_transport_auth_failures_are_distinct_and_persisted(mocker, transport, code, marker, status):
    real_client = httpx2.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx2.Response(code, headers={"X-Seizu-Auth-Error": marker} if marker else {})

    mocker.patch.object(
        external_mcp.httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: real_client(
            transport=httpx2.MockTransport(handle),
            **kwargs,
        ),
    )
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked) as caught:
        await external_mcp.list_proxy_tools(proxy(transport=transport), user())
    assert caught.value.status == status
    assert requests[0].headers["X-Target-User-ID"] == "owner"
    assert "authorization" not in requests[0].headers
    observation = external_mcp_connections.report_store.record_external_mcp_connection.call_args.args[0]
    assert observation["status"] == status
    assert observation["user_id"] == "owner"
    payload = external_mcp.authentication_payload(caught.value)
    assert payload["connections_url"] == "/app/chat/connections"
    assert ("reauthorize_url" in payload) == (status == "authorization_required")


async def test_detached_owners_get_fresh_transports_and_separate_authority(mocker):
    headers_seen = []
    sessions = []

    @asynccontextmanager
    async def transport(config, headers, challenges):
        headers_seen.append(headers)
        yield (None, None)

    def session_factory(*args, **kwargs):
        session = mocker.AsyncMock()
        session.__aenter__.return_value = session
        target = headers_seen[-1]["X-Target-User-ID"]
        session.list_tools.return_value = ListToolsResult(
            tools=[Tool(name=f"read_{target}", input_schema={"type": "object"})]
        )
        session.call_tool.return_value = SimpleNamespace(
            content=[TextContent(type="text", text=target)], structured_content=None, is_error=False
        )
        sessions.append(session)
        return session

    mocker.patch.object(external_mcp, "_transport", transport)
    mocker.patch.object(external_mcp, "ClientSession", side_effect=session_factory)
    for target in ["alice", "bob"]:
        tools = await external_mcp.discover_proxy_tools(proxy(), user(target))
        assert tools[0].name == f"ext__gateway__read_{target}"
        result = await external_mcp.call_tool(proxy(), f"read_{target}", {}, user(target))
        assert result.text == target
    assert len(sessions) == 4
    assert len({id(headers) for headers in headers_seen}) == 4
    assert all("Authorization" not in headers for headers in headers_seen)


async def test_storage_failure_never_replays_a_successful_tool(mocker):
    session = mocker.AsyncMock()
    session.__aenter__.return_value = session
    session.call_tool.return_value = SimpleNamespace(content=[], structured_content=None, is_error=False)
    mocker.patch.object(external_mcp, "ClientSession", return_value=session)

    @asynccontextmanager
    async def transport(*args):
        yield (None, None)

    mocker.patch.object(external_mcp, "_transport", transport)
    external_mcp_connections.report_store.record_external_mcp_connection.side_effect = RuntimeError(
        "database unavailable"
    )
    await external_mcp.call_tool(proxy(), "write", {}, user())
    session.call_tool.assert_awaited_once()


async def test_rejected_m2m_invalidates_token_without_replaying_tool(mocker):
    token_server(
        mocker,
        lambda request: httpx.Response(
            200, json={"access_token": "m2m-token", "token_type": "Bearer", "expires_in": 3600}
        ),
    )
    config = credentials_proxy()
    calls = 0

    @asynccontextmanager
    async def transport(config, headers, challenges):
        nonlocal calls
        calls += 1
        assert headers["Authorization"] == "Bearer m2m-token"
        challenges.append(external_mcp.OAuthChallenge(config.name, status="service_authentication_failed"))
        raise RuntimeError("refused")
        yield

    mocker.patch.object(external_mcp, "_transport", transport)
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked):
        await external_mcp.call_tool(config, "write", {}, user())
    assert calls == 1
    assert next(iter(external_mcp_tokens._credentials.values())).token == ""
