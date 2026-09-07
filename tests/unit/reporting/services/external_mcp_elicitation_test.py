"""Protocol-version coverage and owner-scoped URL recovery boundaries."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitRequestURLParams, InputRequiredResult

from reporting.services import external_mcp, external_mcp_connections
from reporting.services.external_mcp_elicitation import ClientSession, required_elicitations, safe_elicitation
from tests.unit.reporting.services.external_mcp_gateway_test import proxy, user


async def test_real_streamable_http_session_parses_legacy_error_and_advertises_url_only(mocker):
    saved = mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")
    real_client = httpx2.AsyncClient
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "initialize":
            assert body["params"]["capabilities"]["elicitation"] == {"url": {}}
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gateway", "version": "1"},
            }
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        if body["method"].startswith("notifications/"):
            return httpx2.Response(202)
        return httpx2.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "error": {
                    "code": -32042,
                    "message": "Interaction required",
                    "data": {"elicitations": [params().model_dump(by_alias=True)]},
                },
            },
        )

    mocker.patch.object(
        external_mcp.httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: real_client(
            transport=httpx2.MockTransport(handle),
            **kwargs,
        ),
    )
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked) as caught:
        await external_mcp.list_proxy_tools(proxy(), user())
    assert caught.value.status == "interaction_required"
    assert len(caught.value.elicitations) == 1
    assert sum(request["method"] == "tools/list" for request in requests) == 1
    assert saved.call_args.args[0]["status"] == "interaction_required"


def params(url="https://gateway.test/connect?nonce=private-nonce"):
    return ElicitRequestURLParams(url=url, message="Connect your upstream account", elicitation_id="opaque-id")


async def test_real_session_cancels_server_elicitation_and_records_recovery(mocker):
    saved = mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")
    real_client = httpx2.AsyncClient
    answered = asyncio.Event()
    calls = []

    class ElicitationStream(httpx2.AsyncByteStream):
        def __init__(self, request_id):
            self.request_id = request_id

        async def __aiter__(self):
            request = {
                "jsonrpc": "2.0",
                "id": "elicit",
                "method": "elicitation/create",
                "params": params().model_dump(by_alias=True),
            }
            yield f"event: message\ndata: {json.dumps(request)}\n\n".encode()
            await answered.wait()
            result = {"jsonrpc": "2.0", "id": self.request_id, "result": {"content": [], "isError": True}}
            yield f"event: message\ndata: {json.dumps(result)}\n\n".encode()

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("id") == "elicit":
            assert body["result"]["action"] == "cancel"
            answered.set()
            return httpx2.Response(202)
        if body["method"] == "initialize":
            return httpx2.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "gateway", "version": "1"},
                    },
                },
            )
        if body["method"].startswith("notifications/"):
            return httpx2.Response(202)
        if body["method"] == "tools/list":
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})
        return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=ElicitationStream(body["id"]))

    mocker.patch.object(
        external_mcp.httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: real_client(
            transport=httpx2.MockTransport(handle),
            **kwargs,
        ),
    )
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked) as caught:
        await asyncio.wait_for(external_mcp.call_tool(proxy(), "write", {}, user()), timeout=5)
    assert caught.value.status == "interaction_required"
    assert len(caught.value.elicitations) == 1
    assert sum(call.get("method") == "tools/call" for call in calls) == 1
    assert saved.call_args.args[0]["status"] == "interaction_required"


@pytest.mark.parametrize("version", ["2025-11-25", "2026-07-28"])
def test_only_url_capability_is_advertised(version):
    async def callback(context, request):
        pass

    session = ClientSession(None, None, dispatcher=object(), elicitation_callback=callback)
    assert session._build_capabilities(version).model_dump(by_alias=True, exclude_none=True)["elicitation"] == {
        "url": {}
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/connect",
        "javascript:alert(1)",
        "//gateway.test/connect",
        "https://gateway.test:444/connect",
        "https://gateway.test:0/connect",
        "http://gateway.test/connect",
        "https://user@gateway.test/connect",
        "https://gateway.test/connect#token",
        "https://gateway.test\\@evil.test/",
        "https://gateway.test/\nconnect",
        "https://gateway.test:bad/connect",
        "https://gateway.test/" + "x" * 4096,
    ],
)
def test_unsafe_urls_are_not_exposed(url):
    assert safe_elicitation(proxy(), params(url)) is None


def test_bounded_and_grouped_legacy_errors():
    request = params().model_dump(by_alias=True)
    error = MCPError(-32042, "not logged", {"elicitations": [request]})
    assert required_elicitations(ExceptionGroup("transport", [error])) == [params()]
    assert required_elicitations(MCPError(-32042, "bad", {"elicitations": [request] * 9})) == []
    assert required_elicitations(MCPError(-32042, "bad", {"elicitations": [{"url": "bad"}]})) == []
    assert required_elicitations(MCPError(-32000, "other")) is None


@pytest.mark.parametrize("mode", ["legacy_error", "callback", "input_required"])
async def test_protocol_recovery_is_persisted_without_acceptance_or_tool_replay(mocker, mode):
    saved = mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")
    callback = None

    @asynccontextmanager
    async def transport(*args):
        yield (None, None)

    session = mocker.AsyncMock()
    session.__aenter__.return_value = session

    def factory(*args, **kwargs):
        nonlocal callback
        callback = kwargs["elicitation_callback"]
        return session

    async def call(*args, **kwargs):
        if mode == "legacy_error":
            raise ExceptionGroup(
                "transport", [MCPError(-32042, "private-nonce", {"elicitations": [params().model_dump(by_alias=True)]})]
            )
        if mode == "callback":
            assert (await callback(None, params())).action == "cancel"
            return mocker.Mock(content=[], structured_content=None, is_error=True)
        return InputRequiredResult.model_validate(
            {
                "resultType": "input_required",
                "inputRequests": {
                    "request-1": {"method": "elicitation/create", "params": params().model_dump(by_alias=True)}
                },
                "requestState": "must-not-be-replayed",
            }
        )

    session.call_tool.side_effect = call
    mocker.patch.object(external_mcp, "_transport", transport)
    mocker.patch.object(external_mcp, "ClientSession", side_effect=factory)
    with pytest.raises(external_mcp.ExternalMCPGatewayBlocked) as caught:
        await external_mcp.call_tool(proxy(), "write", {}, user())
    assert caught.value.status == "interaction_required"
    assert "private-nonce" not in str(caught.value)
    assert "private-nonce" not in json.dumps(external_mcp.authentication_payload(caught.value))
    assert external_mcp.authentication_payload(caught.value)["authentication_required"] is False
    session.call_tool.assert_awaited_once()
    observation = saved.call_args.args[0]
    assert observation["status"] == "interaction_required"
    assert observation["user_id"] == "owner"
    assert json.loads(observation["elicitations_json"])[0]["elicitation_id"] == "opaque-id"


async def test_owner_scoping_expiry_and_revalidation(mocker):
    config = proxy()
    row = {
        "proxy_name": config.name,
        "fingerprint": external_mcp_connections.fingerprint(config),
        "status": "interaction_required",
        "observed_at": datetime.now(UTC).isoformat(),
        "elicitations_json": json.dumps([safe_elicitation(config, params()).model_dump()]),
    }
    read = mocker.patch.object(
        external_mcp_connections.report_store, "list_external_mcp_connections", return_value=[row]
    )
    assert len((await external_mcp_connections.list_connections([config], user()))[0].elicitations) == 1
    read.assert_awaited_once_with("owner")
    row["observed_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert not (await external_mcp_connections.list_connections([config], user()))[0].elicitations
    row["observed_at"] = datetime.now(UTC).isoformat()
    row["elicitations_json"] = json.dumps([{"url": "https://evil.test", "message": "unsafe"}])
    assert not (await external_mcp_connections.list_connections([config], user()))[0].elicitations
