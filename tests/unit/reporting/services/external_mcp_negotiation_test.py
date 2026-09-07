"""Exercise negotiation through the actual SDK and Streamable HTTP transport."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest

from reporting.services import external_mcp, external_mcp_connections
from tests.unit.reporting.services.external_mcp_gateway_test import proxy, user


@pytest.fixture
def wire(mocker):
    real_client = httpx2.AsyncClient
    requests = []
    behavior = {"probe": "modern", "interaction": False}
    mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")

    async def handle(request):
        if not request.content:
            return httpx2.Response(405)
        body = json.loads(request.content)
        requests.append((body, dict(request.headers)))
        method = body.get("method")
        if method == "server/discover":
            mode = behavior["probe"]
            if isinstance(mode, int):
                return httpx2.Response(mode)
            if mode == "timeout":
                raise httpx2.ReadTimeout("sensitive timeout details")
            if mode == "slow":
                await asyncio.Event().wait()
            if mode in {"legacy", "disjoint", "legacy_versions", "retry", "server_error"}:
                error = {"code": -32601, "message": "Method not found"}
                if mode not in {"legacy", "server_error"}:
                    supported = {"legacy_versions": "2025-11-25", "retry": "2026-07-28"}.get(mode, "2099-01-01")
                    error = {
                        "code": -32022,
                        "message": "Unsupported version",
                        "data": {
                            "requested": "2026-07-28",
                            "supported": [supported],
                        },
                    }
                return httpx2.Response(
                    500 if mode == "server_error" else 200,
                    json={"jsonrpc": "2.0", "id": body["id"], "error": error},
                )
            result = {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "_meta": {"com.mappedsky.seizu": {"upstreamUrls": ["https://upstream.test/mcp"]}},
            }
            if mode == "legacy_result":
                result["supportedVersions"] = ["2025-11-25"]
            if mode == "disjoint_result":
                result["supportedVersions"] = ["2099-01-01"]
        elif method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "legacy", "version": "1"},
            }
        elif method == "notifications/initialized":
            return httpx2.Response(202)
        elif method == "tools/list":
            result = {"resultType": "complete", "tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
        elif behavior["interaction"]:
            result = {
                "resultType": "input_required",
                "inputRequests": {
                    "connect": {
                        "method": "elicitation/create",
                        "params": {
                            "mode": "url",
                            "url": "https://gateway.test/connect?nonce=secret",
                            "message": "Connect your account",
                        },
                    }
                },
                "requestState": "do-not-replay",
            }
        else:
            result = {"resultType": "complete", "content": [{"type": "text", "text": "success"}], "isError": False}
        result.update({"ttlMs": 0, "cacheScope": "private"})
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    mocker.patch.object(
        external_mcp.httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: real_client(
            transport=httpx2.MockTransport(handle),
            **kwargs,
        ),
    )
    return behavior, requests


async def test_modern_requests_carry_identity_and_capabilities_without_handshake(wire):
    _, requests = wire
    result = await external_mcp.call_tool(proxy(), "read", {}, user("alice"))
    assert result.text == "success"
    assert requests[0][0]["method"] == "server/discover"
    assert all(body["method"] not in {"initialize", "notifications/initialized"} for body, _ in requests)
    for body, headers in requests:
        assert headers["mcp-protocol-version"] == "2026-07-28"
        assert headers["x-target-user-id"] == "alice"
        assert "mcp-session-id" not in headers
        assert body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]["elicitation"] == {"url": {}}


@pytest.mark.parametrize("mode", ["legacy", "legacy_versions", "legacy_result", 400, 404, 405])
async def test_legacy_fallback_is_bounded_and_resets_protocol_headers(wire, mode):
    behavior, requests = wire
    behavior["probe"] = mode
    await external_mcp.list_proxy_tools(proxy(), user())
    methods = [body["method"] for body, _ in requests]
    assert methods == ["server/discover", "initialize", "notifications/initialized", "tools/list"]
    assert requests[-1][1]["mcp-protocol-version"] == "2025-11-25"
    assert "io.modelcontextprotocol/clientCapabilities" not in requests[-1][0].get("params", {}).get("_meta", {})


@pytest.mark.parametrize("mode", [401, 403, 429, 500, "server_error", "timeout", "disjoint", "disjoint_result"])
async def test_failures_do_not_downgrade_or_execute_tools(wire, mode):
    behavior, requests = wire
    behavior["probe"] = mode
    with pytest.raises(external_mcp.ExternalMCPError):
        await external_mcp.call_tool(proxy(), "write", {}, user())
    assert [body["method"] for body, _ in requests] == ["server/discover"]
    saved = external_mcp_connections.report_store.record_external_mcp_connection.call_args.args[0]
    expected = {401: "service_authentication_failed", 403: "permission_denied"}.get(mode, "unavailable")
    assert saved["status"] == expected


async def test_explicit_legacy_skips_probe(wire):
    _, requests = wire
    await external_mcp.list_proxy_tools(proxy(protocol_mode="legacy"), user())
    assert requests[0][0]["method"] == "initialize"


async def test_modern_version_retry_is_bounded(wire):
    behavior, requests = wire
    behavior["probe"] = "retry"
    with pytest.raises(external_mcp.ExternalMCPError):
        await external_mcp.call_tool(proxy(), "write", {}, user())
    assert [body["method"] for body, _ in requests] == ["server/discover", "server/discover"]


async def test_negotiation_obeys_operation_deadline(wire):
    behavior, requests = wire
    behavior["probe"] = "slow"
    with pytest.raises(external_mcp.ExternalMCPError):
        await external_mcp.call_tool(proxy(read_timeout_seconds=0.05), "write", {}, user())
    assert [body["method"] for body, _ in requests] == ["server/discover"]


async def test_sse_always_initializes(mocker):
    session = AsyncMock()
    mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")

    @asynccontextmanager
    async def transport(*args):
        yield (None, None)

    mocker.patch.object(external_mcp, "_transport", transport)
    mocker.patch.object(external_mcp, "ClientSession").return_value.__aenter__.return_value = session
    async with external_mcp._session(proxy(transport="sse"), user()):
        pass
    session.initialize.assert_awaited_once()
    session.negotiate.assert_not_awaited()


async def test_modern_elicitation_is_persisted_without_continuation(wire):
    behavior, requests = wire
    behavior["interaction"] = True
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked) as caught:
        await external_mcp.call_tool(proxy(), "read", {}, user())
    assert caught.value.status == "interaction_required"
    assert caught.value.elicitations[0].elicitation_id is None
    assert sum(body["method"] == "tools/call" for body, _ in requests) == 1
    assert all("inputResponses" not in body.get("params", {}) for body, _ in requests)


async def test_modern_metadata_and_fresh_owner_sessions(wire, mocker):
    _, requests = wire
    mocker.patch.object(
        external_mcp.settings,
        "MCP_EXTERNAL_PLUGIN_URL_MATCH_MODE",
        external_mcp.settings.ExternalPluginURLMatchMode.STRICT,
    )
    for owner in ["alice", "bob"]:
        await external_mcp.list_proxy_tools(proxy(), user(owner))
    assert [headers["x-target-user-id"] for body, headers in requests if body["method"] == "server/discover"] == [
        "alice",
        "bob",
    ]
    assert external_mcp._advertised_upstream_urls["gateway"] == frozenset({"https://upstream.test/mcp"})
