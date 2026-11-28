"""The connection API never accepts a caller-selected owner or executes tools."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from reporting.authnz import CurrentUser, get_current_user
from reporting.routes import chat_connections
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.report_config import User
from reporting.services import external_mcp, external_mcp_connections, external_mcp_oauth, report_store


@pytest.fixture
def setup(mocker):
    config = ExternalMCPProxy(
        name="gateway",
        url="https://gateway.test/mcp",
        user_authorization={"reauthorize_url": "https://gateway.test/accounts"},
    )
    current = CurrentUser(
        user=User(user_id="owner", sub="subject", iss="https://idp.test", created_at="now", last_login="now"),
        jwt_claims={},
        permissions=frozenset({"chat:use"}),
    )
    app = FastAPI()
    app.include_router(chat_connections.router)
    app.dependency_overrides[get_current_user] = lambda: current
    mocker.patch.object(chat_connections.settings, "MCP_EXTERNAL_PROXIES", [config])
    mocker.patch.object(report_store, "list_external_mcp_connections", return_value=[])
    mocker.patch.object(report_store, "list_delegated_tokens", return_value=[])
    return app, config, current


async def test_list_uses_authenticated_owner_and_omits_private_configuration(setup):
    app, config, current = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/connections?user_id=someone-else")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["connections"] == [
        {
            "proxy_name": "gateway",
            "status": "unknown",
            "observed_at": None,
            "error_code": None,
            "reauthorize_url": "https://gateway.test/accounts",
            "elicitations": [],
            "connectable": False,
            "has_grant": False,
        }
    ]
    report_store.list_external_mcp_connections.assert_awaited_once_with("owner")


async def test_check_bypasses_cache_and_returns_recovered_status(setup, mocker):
    app, config, current = setup
    discovery = mocker.patch.object(external_mcp, "list_proxy_tools", return_value=[])
    call = mocker.patch.object(external_mcp, "call_tool")
    invalidate = mocker.patch.object(external_mcp, "invalidate_discovery_cache")
    report_store.list_external_mcp_connections.side_effect = lambda user_id: [
        {
            "proxy_name": "gateway",
            "fingerprint": external_mcp_connections.fingerprint(config),
            "status": "connected",
            "observed_at": datetime.now(UTC).isoformat(),
            "error_code": None,
        }
    ]
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/connections/gateway/check")
    assert response.json()["status"] == "connected"
    discovery.assert_awaited_once_with(config, current)
    invalidate.assert_called_once_with(current)
    call.assert_not_called()


async def test_failed_status_write_does_not_report_old_success_as_a_new_check(setup, mocker):
    app, config, current = setup
    discovery = mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        side_effect=external_mcp.ExternalMCPGatewayBlocked(config, "authorization_required"),
    )
    report_store.list_external_mcp_connections.return_value = [
        {
            "proxy_name": "gateway",
            "fingerprint": external_mcp_connections.fingerprint(config),
            "status": "connected",
            "observed_at": "2026-01-01T00:00:00+00:00",
            "error_code": None,
        }
    ]
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/connections/gateway/check")
    assert response.status_code == 503
    discovery.assert_awaited_once()


async def test_changed_config_hides_stale_status(setup):
    app, config, current = setup
    report_store.list_external_mcp_connections.return_value = [
        {"proxy_name": "gateway", "fingerprint": "old", "status": "connected"}
    ]
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/connections")
    assert response.json()["connections"][0]["status"] == "unknown"


@pytest.mark.parametrize("kind", ["disabled", "shared", "missing"])
async def test_checks_reject_non_per_user_proxies(setup, mocker, kind):
    app, config, current = setup
    if kind == "disabled":
        config.enabled = False
    elif kind == "shared":
        config.user_authorization = None
    discovery = mocker.patch.object(external_mcp, "list_proxy_tools")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/chat/connections/{'missing' if kind == 'missing' else 'gateway'}/check")
    assert response.status_code == 404
    discovery.assert_not_called()


async def test_connections_require_chat_permission(setup, mocker):
    app, config, current = setup
    mocker.patch.object(chat_connections.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user=current.user, jwt_claims={}, permissions=frozenset()
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.get("/api/v1/chat/connections")).status_code == 403
        assert (await client.post("/api/v1/chat/connections/gateway/check")).status_code == 403


# ── user_oauth connect flow ────────────────────────────────────────────────


@pytest.fixture
def oauth_setup(setup, mocker):
    app, _, current = setup
    oauth_proxy = ExternalMCPProxy(
        name="deps", url="https://deps.gateway.test/mcp", transport="streamable_http", auth_mode="user_oauth"
    )
    header_proxy = ExternalMCPProxy(
        name="gateway",
        url="https://gateway.test/mcp",
        user_authorization={"reauthorize_url": "https://gateway.test/accounts"},
    )
    mocker.patch.object(chat_connections.settings, "MCP_EXTERNAL_PROXIES", [oauth_proxy, header_proxy])
    mocker.patch.object(chat_connections.settings, "MCP_EXTERNAL_OAUTH_REDIRECT_URI", "")
    return app, oauth_proxy, current


async def test_connect_returns_the_authorization_url_and_binds_the_browser(oauth_setup, mocker):
    app, proxy, current = oauth_setup
    begin = mocker.patch.object(
        chat_connections.external_mcp_oauth,
        "begin_connect",
        return_value=external_mcp_oauth.ConnectStart(
            authorization_url="https://deps.gateway.test/authorize?x", cookie="sealed"
        ),
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/connections/deps/connect")
    assert response.status_code == 200
    assert response.json() == {"authorization_url": "https://deps.gateway.test/authorize?x"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("seizu_mcp_connect=sealed")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Path=/api/v1/chat/connections/oauth/callback" in cookie
    begin.assert_awaited_once_with(proxy, current, "http://test/api/v1/chat/connections/oauth/callback")


async def test_connect_is_only_for_user_oauth_gateways(oauth_setup):
    app, _, _ = oauth_setup
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.post("/api/v1/chat/connections/gateway/connect")).status_code == 404
        assert (await client.post("/api/v1/chat/connections/missing/connect")).status_code == 404
        assert (await client.delete("/api/v1/chat/connections/gateway/grant")).status_code == 404


async def test_connect_reports_an_unreachable_gateway_without_details(oauth_setup, mocker):
    app, _, _ = oauth_setup
    mocker.patch.object(
        chat_connections.external_mcp_oauth,
        "begin_connect",
        side_effect=external_mcp_oauth.GatewayOAuthUnavailable("internal detail"),
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/connections/deps/connect")
    assert response.status_code == 502
    assert "internal detail" not in response.text


async def test_callback_success_returns_to_connections_and_clears_the_cookie(oauth_setup, mocker):
    app, proxy, _ = oauth_setup
    complete = mocker.patch.object(
        chat_connections.external_mcp_oauth,
        "complete_connect",
        return_value=external_mcp_oauth.ConnectResult(proxy_name="deps", user_id="owner"),
    )
    invalidate = mocker.patch.object(external_mcp, "invalidate_user_discovery_cache")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        client.cookies.set("seizu_mcp_connect", "sealed")
        response = await client.get("/api/v1/chat/connections/oauth/callback?code=c&state=s")
    assert response.status_code == 303
    assert response.headers["location"] == "/app/chat/connections?connected=deps"
    assert 'seizu_mcp_connect=""' in response.headers["set-cookie"]
    assert complete.await_args.args[0] == "sealed"
    assert complete.await_args.kwargs["state"] == "s"
    invalidate.assert_called_once_with("owner")


@pytest.mark.parametrize(
    ("query", "raised", "expected"),
    [
        ("error=access_denied", None, "connect_error=denied"),
        ("state=s", None, "connect_error=failed"),
        ("code=c&state=s", external_mcp_oauth.OAuthConnectError("state_mismatch", "x"), "connect_error=state_mismatch"),
        ("code=c&state=s", external_mcp_oauth.GatewayOAuthUnavailable("x"), "connect_error=unavailable"),
    ],
)
async def test_callback_failures_return_a_display_safe_reason(oauth_setup, mocker, query, raised, expected):
    app, _, _ = oauth_setup
    mocker.patch.object(chat_connections.external_mcp_oauth, "complete_connect", side_effect=raised)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/chat/connections/oauth/callback?{query}")
    assert response.status_code == 303
    assert response.headers["location"] == f"/app/chat/connections?{expected}"


async def test_disconnect_deletes_the_grant_and_records_the_status(oauth_setup, mocker):
    app, proxy, current = oauth_setup
    disconnect = mocker.patch.object(chat_connections.external_mcp_oauth, "disconnect", return_value=True)
    observe = mocker.patch.object(external_mcp_connections, "observe")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/connections/deps/grant")
    assert response.status_code == 204
    disconnect.assert_awaited_once_with(proxy, current)
    observe.assert_awaited_once_with(proxy, current, "authorization_required")


async def test_list_marks_user_oauth_gateways_connectable_with_their_grant(oauth_setup, mocker):
    app, proxy, _ = oauth_setup
    report_store.list_delegated_tokens.return_value = [
        {
            "proxy_name": "deps",
            "resource": proxy.url,
            "key_id": "k1",
            "version": 1,
            "created_at": "now",
            "updated_at": "now",
        }
    ]
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        connections = (await client.get("/api/v1/chat/connections")).json()["connections"]
    assert [(c["proxy_name"], c["connectable"], c["has_grant"]) for c in connections] == [
        ("deps", True, True),
        ("gateway", False, False),
    ]
