"""The connection API never accepts a caller-selected owner or executes tools."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from reporting.authnz import CurrentUser, get_current_user
from reporting.routes import chat_connections
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.report_config import User
from reporting.services import external_mcp, external_mcp_connections, report_store


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
    return app, config, current


async def test_list_uses_authenticated_owner_and_omits_private_configuration(setup):
    app, config, current = setup
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/connections?user_id=someone-else")
    assert response.status_code == 200
    assert response.json()["connections"] == [
        {
            "proxy_name": "gateway",
            "status": "unknown",
            "observed_at": None,
            "error_code": None,
            "reauthorize_url": "https://gateway.test/accounts",
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
