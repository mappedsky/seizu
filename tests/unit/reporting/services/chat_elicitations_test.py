"""Interactive elicitation security and continuation contracts."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.types import CallToolResult, ElicitResult, InputRequiredResult, TextContent, Tool, ToolAnnotations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from reporting.authnz.permissions import Permission
from reporting.schema.chat_elicitations import ElicitationResponse, validate_response, validate_schema
from reporting.services import chat_elicitations as service
from reporting.services import external_mcp
from reporting.services.external_mcp_elicitation import ClientSession
from reporting.services.report_store import elicitations as store
from reporting.services.report_store.sql import ChatSessionRecord
from tests.unit.reporting.services.external_mcp_gateway_test import proxy, user

SCHEMA = {"type": "object", "properties": {"answer": {"type": "string", "maxLength": 100}}, "required": ["answer"]}


def input_result(schema=None, **extra):
    return InputRequiredResult.model_validate(
        {
            "resultType": "input_required",
            "requestState": "opaque-state",
            "inputRequests": {
                "input-1": {
                    "method": "elicitation/create",
                    "params": {
                        "mode": "form",
                        "message": "External question",
                        "requestedSchema": schema or SCHEMA,
                    },
                }
            },
            **extra,
        }
    )


@pytest.fixture
async def database(tmp_path, mocker):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'inputs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    mocker.patch.object(store, "_get_engine", return_value=engine)
    async with AsyncSession(engine) as session:
        session.add(
            ChatSessionRecord(
                user_id="owner",
                thread_id="123",
                title="test",
                created_at=datetime.now(UTC).isoformat(),
                updated_at=datetime.now(UTC).isoformat(),
            )
        )
        await session.commit()
    mocker.patch.object(service, "interactive_context", return_value=("123", "turn-1"))
    mocker.patch("reporting.settings.MCP_EXTERNAL_ELICITATION_ENABLED", True)
    yield engine
    await engine.dispose()


@pytest.mark.parametrize(
    "field",
    [
        {"type": "object", "properties": {}},
        {"type": "array"},
        {"$ref": "https://untrusted.test"},
        {"type": "string", "maxLength": 4097},
        {"type": "string", "enum": ["x"] * 65},
        {"type": "string", "enum": ["x" * 4097]},
        {"type": "number", "minimum": float("inf")},
        {"type": "string", "pattern": "(a+)+$"},
        {"type": "string", "description": "x" * 1001},
    ],
)
async def test_rejects_untrusted_schema(field):
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": {"answer": field}})


async def test_field_count_and_response_validation(mocker):
    mocker.patch("reporting.settings.CHAT_ELICITATION_MAX_FIELDS", 1)
    with pytest.raises(ValueError):
        validate_schema({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "boolean"}}})
    for content in ({}, {"answer": True}, {"answer": "x" * 101}, {"answer": "ok", "unknown": "x"}):
        with pytest.raises(ValueError):
            validate_response(SCHEMA, ElicitationResponse(action="accept", content=content))
    validate_response(SCHEMA, ElicitationResponse(action="accept", content={"answer": "ok"}))


@pytest.mark.parametrize(
    "version,enabled,expected",
    [
        ("2025-11-25", True, False),
        ("2026-07-28", False, False),
        ("2026-07-28", True, True),
    ],
)
async def test_capabilities(version, enabled, expected):
    callback = AsyncMock(return_value=ElicitResult(action="cancel"))
    session = ClientSession(dispatcher=MagicMock(), elicitation_callback=callback)
    token = service.form_capability.set(enabled)
    try:
        capability = session._build_capabilities(version).elicitation
        assert capability.url is not None
        assert (capability.form is not None) == expected
    finally:
        service.form_capability.reset(token)


async def test_owner_once_and_group_claim(database):
    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {"scope": "original"}, user(), input_result())
    assert await store.get(ids[0], "other") is None
    record = await store.get(ids[0], "owner")
    assert record is not None
    answer = ElicitationResponse(action="accept", content={"answer": "private-answer"})
    assert sum(await asyncio.gather(store.respond(record, answer), store.respond(record, answer))) == 1
    public = (await store.list_for_thread("owner", "123"))[0].model_dump_json()
    assert "private-answer" not in public and "opaque-state" not in public and "original" not in public
    claims = await asyncio.gather(store.group(record, claim=True), store.group(record, claim=True))
    assert sum(bool(claim) for claim in claims) == 1
    assert (await store.get(ids[0], "owner")).response_json is None


async def test_exact_continuation_preserves_upstream_result(database, mocker):
    p = proxy(require_confirmation=False, elicitation={"form": True})
    owner = user()
    owner.permissions = frozenset({Permission.CHAT_TOOLS_CALL.value})
    ids = await service.park(p, "read", {"scope": "original"}, owner, input_result())
    record = await store.get(ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "private-answer"}))
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        return_value=[
            Tool(
                name="ext__gateway__read",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(read_only_hint=True),
            )
        ],
    )
    call = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="received private-answer")]))

    @asynccontextmanager
    async def session(*args):
        yield type("Session", (), {"call_tool": call})()

    mocker.patch.object(external_mcp, "_session", session)
    kind, output = await service.resume(ids[0], owner, "123")
    assert kind == "run" and output == "received private-answer"
    assert call.call_args.args == ("read", {"scope": "original"})
    assert call.call_args.kwargs["request_state"] == "opaque-state"
    assert call.call_args.kwargs["input_responses"]["input-1"].content == {"answer": "private-answer"}
    assert (await service.resume(ids[0], owner, "123"))[0] == "abort"
    assert call.call_count == 1


async def test_resume_checks_permissions_and_thread(database, mocker):
    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {}, user(), input_result())
    record = await store.get(ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "private"}))
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))
    call = mocker.patch.object(external_mcp, "call_tool")
    assert (await service.resume(ids[0], user(), "wrong"))[0] == "abort"
    assert (await service.resume(ids[0], user(), "123"))[0] == "abort"
    call.assert_not_called()
    assert (await store.get(ids[0], "owner")).status == "accepted"


async def test_expiry_and_unsupported_request(database, mocker):
    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {}, user(), input_result())
    record = await store.get(ids[0], "owner")
    from sqlalchemy import update

    from reporting.services.report_store.sql import ChatElicitationRecord

    async with AsyncSession(database) as session:
        await session.execute(update(ChatElicitationRecord).values(expires_at="2000-01-01T00:00:00+00:00"))
        await session.commit()
    assert not await store.respond(record, ElicitationResponse(action="decline"))
    assert (await service.resume(ids[0], user(), "123"))[0] == "abort"
    with pytest.raises(ValueError, match="unsupported"):
        await service.park(p, "read", {}, user(), input_result(inputRequests={"roots": {"method": "roots/list"}}))


async def test_turn_limit_is_bounded(database):
    p = proxy(elicitation={"form": True})
    for _ in range(16):
        await service.park(p, "read", {}, user(), input_result())
    with pytest.raises(ValueError, match="limit"):
        await service.park(p, "read", {}, user(), input_result())


async def test_modern_wire_parks_then_resumes_with_protocol_responses(database, mocker):
    import httpx2

    from reporting.services import external_mcp_connections

    p = proxy(require_confirmation=False, elicitation={"form": True})
    owner = user()
    owner.permissions = frozenset({Permission.CHAT_TOOLS_CALL.value})
    requests = []
    real_client = httpx2.AsyncClient
    mocker.patch.object(external_mcp_connections.report_store, "record_external_mcp_connection")
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))

    def handle(request):
        if not request.content:
            return httpx2.Response(405)
        body = json.loads(request.content)
        requests.append(body)
        if body["method"] == "server/discover":
            result = {"resultType": "complete", "supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}}
        elif body["method"] == "tools/list":
            result = {
                "resultType": "complete",
                "tools": [{"name": "read", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}],
            }
        elif not body["params"].get("inputResponses"):
            result = input_result().model_dump(mode="json", by_alias=True, exclude_none=True)
        else:
            assert body["params"]["inputResponses"]["input-1"]["content"] == {"answer": "wire-secret"}
            assert body["params"]["requestState"] == "opaque-state"
            result = {"resultType": "complete", "content": [{"type": "text", "text": "done wire-secret"}]}
        result.update({"ttlMs": 0, "cacheScope": "private"})
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    mocker.patch.object(
        external_mcp.httpx2,
        "AsyncClient",
        side_effect=lambda **kwargs: real_client(transport=httpx2.MockTransport(handle), **kwargs),
    )
    with pytest.raises(service.InputRequired) as caught:
        await external_mcp.call_tool(p, "read", {"scope": "same"}, owner, interactive=True)
    record = await store.get(caught.value.ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "wire-secret"}))
    kind, output = await service.resume(record.elicitation_id, owner, "123")
    assert kind == "run" and output == "done wire-secret"
    calls = [body for body in requests if body["method"] == "tools/call"]
    assert len(calls) == 2
    assert all(
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]["elicitation"] == {"form": {}, "url": {}}
        for body in calls
    )
    listing = next(body for body in requests if body["method"] == "tools/list")
    assert listing["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]["elicitation"] == {"url": {}}


async def test_later_delegation_consumes_only_the_matching_call(database):
    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {"scope": "same"}, user(), input_result(), delegated=True)
    record = await store.get(ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "private"}))
    assert not await service.continuation(p, "different", {"scope": "same"}, user(), delegated=True)
    assert not await service.continuation(p, "read", {"scope": "other"}, user(), delegated=True)
    resumed = await service.continuation(p, "read", {"scope": "same"}, user(), delegated=True)
    assert resumed["input_responses"]["input-1"].content == {"answer": "private"}
    assert not await service.continuation(p, "read", {"scope": "same"}, user(), delegated=True)


async def test_resumed_delegation_rate_limit_never_becomes_a_fresh_call(database, mocker):
    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {}, user(), input_result(), delegated=True)
    record = await store.get(ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "private"}))
    call = AsyncMock(
        return_value=CallToolResult(
            is_error=True,
            content=[TextContent(type="text", text="Rate limit exceeded. Retry after 0s")],
        )
    )

    @asynccontextmanager
    async def session(*args):
        yield type("Session", (), {"call_tool": call})()

    mocker.patch.object(external_mcp, "_session", session)
    mocker.patch("reporting.settings.MCP_EXTERNAL_RATE_LIMIT_RETRIES", 2)
    result = await external_mcp.call_tool(p, "read", {}, user())
    assert result.is_error and result.continuation_used
    assert call.await_count == 1


@pytest.mark.parametrize("decision", ["approved", "denied"])
async def test_resume_confirmation_remains_linked_to_continuation(database, mocker, decision):
    p = proxy(elicitation={"form": True}, require_confirmation=True)
    owner = user()
    owner.permissions = frozenset({Permission.CHAT_TOOLS_CALL.value})
    mocker.patch("reporting.services.report_store.sql._get_engine", return_value=database)
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        return_value=[
            Tool(
                name="ext__gateway__read",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(destructive_hint=True),
            )
        ],
    )
    ids = await service.park(p, "read", {"scope": "original"}, owner, input_result())
    record = await store.get(ids[0], "owner")
    await store.respond(record, ElicitationResponse(action="accept", content={"answer": "private"}))
    call = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="completed")]))

    @asynccontextmanager
    async def session(*args):
        yield type("Session", (), {"call_tool": call})()

    mocker.patch.object(external_mcp, "_session", session)
    kind, text = await service.resume(ids[0], owner, "123")
    assert kind == "wait"
    confirmation_id = json.loads(text)["confirmation_id"]
    assert (await store.for_confirmation(confirmation_id, "owner", "123")).group_id == record.group_id
    call.assert_not_called()
    await service.report_store.decide_action_confirmation(confirmation_id, "owner", decision)
    kind, _ = await service.resume(ids[0], owner, "123")
    assert kind == ("run" if decision == "approved" else "abort")
    if decision == "approved":
        assert call.await_count == 1
        assert call.call_args.kwargs["request_state"] == "opaque-state"
        assert call.call_args.args[1] == {"scope": "original"}
    else:
        call.assert_not_called()


async def test_group_waits_for_all_answers_and_consumes_together(database):
    result = input_result()
    request = next(iter(result.input_requests.values()))
    result.input_requests["input-2"] = request
    ids = await service.park(proxy(elicitation={"form": True}), "read", {}, user(), result)
    first, second = [await store.get(eid, "owner") for eid in ids]
    await store.respond(first, ElicitationResponse(action="accept", content={"answer": "a"}))
    assert not await store.group(first, claim=True)
    await store.respond(second, ElicitationResponse(action="decline"))
    claims = await asyncio.gather(store.group(first, claim=True), store.group(second, claim=True))
    assert sorted(len(claim) for claim in claims) == [0, 2]


async def test_remote_worker_context_does_not_require_a_langgraph_task(mocker):
    mocker.patch.object(service, "get_config", side_effect=RuntimeError("No graph task"))
    assert service.interactive_context() is None
    token = service.worker_config.set({"configurable": {"client_thread_id": "123", "turn_id": "turn-1"}})
    try:
        assert service.interactive_context() == ("123", "turn-1")
    finally:
        service.worker_config.reset(token)
    assert service.interactive_context() is None


async def test_coordinator_does_not_repeat_completed_parallel_calls(mocker):
    from reporting.services import chat_orchestrator

    resume = mocker.patch.object(service, "resume", side_effect=[("run", "first result"), ("wait", "Answer second")])
    plan = [{"id": "step", "status": "awaiting"}]
    results = [
        {
            "step_id": "step",
            "awaiting_elicitation": True,
            "elicitation_id": "first",
            "elicitation_ids": ["first", "second"],
        }
    ]
    update = await chat_orchestrator._resume_awaiting_steps(plan, results, 1, user(), "123", lambda event: None)
    assert update["plan"][0]["status"] == "awaiting"
    assert update["step_results"][0]["elicitation_ids"] == ["second"]
    resume.reset_mock(side_effect=True)
    resume.return_value = ("run", "second result")
    finished = await chat_orchestrator._resume_awaiting_steps(
        update["plan"], update["step_results"], 1, user(), "123", lambda event: None
    )
    resume.assert_awaited_once_with("second", user(), "123")
    assert finished["plan"][0]["status"] == "ran"
    assert finished["step_results"][0]["output"] == "first result\n\nsecond result"


@pytest.mark.parametrize("kind,status", [("wait", "awaiting"), ("run", "ran"), ("abort", "failed")])
async def test_coordinator_resumes_parked_remote_step(mocker, kind, status):
    from reporting.services import chat_orchestrator

    mocker.patch.object(service, "resume", return_value=(kind, "safe outcome"))
    result = await chat_orchestrator._resume_awaiting_steps(
        [{"id": "remote-step", "status": "awaiting"}],
        [{"step_id": "remote-step", "awaiting_elicitation": True, "elicitation_id": "input-1"}],
        1,
        user(),
        "123",
        lambda event: None,
    )
    assert result["plan"][0]["status"] == status
    if kind != "wait":
        assert result["plan"][0]["no_retry"] is True
    assert not result["step_results"][0].get("confirmation_executed")


async def test_owner_routes_rehydrate_and_validate(database, mocker):
    from httpx import ASGITransport, AsyncClient

    from reporting.app import create_app
    from reporting.authnz import get_current_user
    from reporting.schema.chat import ChatSessionItem

    p = proxy(elicitation={"form": True})
    ids = await service.park(p, "read", {}, user(), input_result())
    mocker.patch("reporting.settings.CHAT_ENABLED", True)
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))
    mocker.patch.object(
        service.report_store,
        "get_chat_session",
        return_value=ChatSessionItem(
            thread_id="123",
            title="test",
            created_at="now",
            updated_at="now",
        ),
    )
    owner = user()
    owner.permissions = frozenset({Permission.CHAT_USE.value})
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: owner
    path = f"/api/v1/chat/elicitations/{ids[0]}/response"
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.post(path, json={"action": "accept", "content": {}})).status_code == 422
        assert (
            await client.post(path, json={"action": "accept", "content": {"answer": "route-secret"}})
        ).status_code == 200
        listing = await client.get("/api/v1/chat/elicitations?thread_id=123")
        assert listing.status_code == 200 and listing.json()["elicitations"][0]["status"] == "accepted"
        assert "route-secret" not in listing.text and "opaque-state" not in listing.text
        assert (await client.post(path, json={"action": "cancel"})).status_code == 409
        owner.user.user_id = "different-owner"
        assert (await client.post(path, json={"action": "cancel"})).status_code == 404


async def test_form_echo_preserves_the_complete_result(database, mocker):
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "count": {"type": "integer"}},
        "required": ["answer"],
    }
    p = proxy(require_confirmation=False, elicitation={"form": True})
    owner = user()
    owner.permissions = frozenset({Permission.CHAT_TOOLS_CALL.value})
    ids = await service.park(p, "read", {"scope": "original"}, owner, input_result(schema=schema))
    record = await store.get(ids[0], "owner")
    await store.respond(
        record, ElicitationResponse(action="accept", content={"answer": "private-answer-text", "count": 1})
    )
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(p, "read"))
    mocker.patch.object(
        external_mcp,
        "list_proxy_tools",
        return_value=[
            Tool(
                name="ext__gateway__read",
                input_schema={"type": "object"},
                annotations=ToolAnnotations(read_only_hint=True),
            )
        ],
    )
    # The upstream echoes the free-text answer, and separately reports numbers
    # and prose that merely contain the submitted digit.
    upstream = '{"round": 1, "rounds": 1, "chars": 10, "digest": "b5bea41b", "echo": "private-answer-text"}'
    call = AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text=upstream)]))

    @asynccontextmanager
    async def session(*args):
        yield type("Session", (), {"call_tool": call})()

    mocker.patch.object(external_mcp, "_session", session)
    kind, output = await service.resume(ids[0], owner, "123")

    assert kind == "run"
    assert output == upstream
    assert json.loads(output) == {
        "round": 1,
        "rounds": 1,
        "chars": 10,
        "digest": "b5bea41b",
        "echo": "private-answer-text",
    }
