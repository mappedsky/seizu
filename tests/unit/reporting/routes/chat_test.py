import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.routes import chat
from reporting.schema.chat import (
    ChatSessionItem,
    ChatTurnAdmission,
    ChatTurnEventBatch,
    ChatTurnEventPage,
    ChatTurnItem,
    ChatTurnRequest,
    ExpiredChatTurn,
)
from reporting.schema.report_config import User
from reporting.services import chat_turns
from reporting.services.chat_budget import BudgetController

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="test@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)


class FakeChatGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    async def astream(
        self,
        input: dict[str, Any],
        config: dict[str, Any],
        *,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((input, config, stream_mode))
        yield {"kind": "token", "content": "Hello"}
        yield {"kind": "token", "content": " there"}


class FakeCutoffChatGraph(FakeChatGraph):
    async def astream(
        self,
        input: dict[str, Any],
        config: dict[str, Any],
        *,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((input, config, stream_mode))
        yield {"kind": "token", "content": "Partial answer"}
        yield {"kind": "finish_reason", "finish_reason": "length"}


class FakeDetailChatGraph(FakeChatGraph):
    async def astream(
        self,
        input: dict[str, Any],
        config: dict[str, Any],
        *,
        stream_mode: str,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((input, config, stream_mode))
        yield {
            "kind": "detail",
            "id": "detail_1",
            "data": {
                "kind": "tool",
                "title": "Tool: graph__schema",
                "status": "completed",
                "arguments": "{}",
                "body": '{"labels":["CVE"]}',
            },
        }
        yield {"kind": "token", "content": "Schema has CVEs."}


@pytest.fixture(autouse=True)
def _chat_enabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", True)


#: Sessions the current test has, shared so admission can refuse a thread that
#: has none -- the store makes that decision in the same write, so the fake has
#: to as well.
_SESSIONS: dict[tuple[str, str], ChatSessionItem] = {}


@pytest.fixture(autouse=True)
def _chat_turn_log(mocker):
    """An in-memory stand-in for turn admission and the event log.

    Every stream test needs one: the response body is a *reader* over this log,
    and admission is what decides whether there is anything to read. Flush and
    poll intervals drop to a tick so a test is not paced by the production
    cadence.
    """
    mocker.patch("reporting.settings.CHAT_TURN_FLUSH_MS", 1)
    mocker.patch("reporting.settings.CHAT_TURN_POLL_MS", 1)
    mocker.patch("reporting.settings.CHAT_TURN_POLL_MAX_MS", 1)
    mocker.patch("reporting.settings.CHAT_TURN_HEARTBEAT_SECONDS", 1)
    mocker.patch("reporting.settings.CHAT_TURN_STOP_WAIT_SECONDS", 0.3)
    mocker.patch("reporting.services.chat_turns._last_sweep_monotonic", 0.0)
    mocker.patch("reporting.settings.CHAT_TURN_SWEEP_INTERVAL_SECONDS", 0.0)

    turns: dict[str, ChatTurnItem] = {}
    events: dict[str, dict[int, str]] = {}
    counter = 0

    async def admit_chat_turn(user_id, thread_id, message_id, text_id, idempotency_key=None):
        nonlocal counter
        if idempotency_key is not None:
            for turn in turns.values():
                if (turn.user_id, turn.thread_id, turn.idempotency_key) == (
                    user_id,
                    thread_id,
                    idempotency_key,
                ):
                    return ChatTurnAdmission(outcome="existing", turn=turn)
        if (user_id, thread_id) not in _SESSIONS:
            # Admission is the turn's half of the retirement handshake: no
            # session, no turn.
            return ChatTurnAdmission(outcome="retired")
        for turn in turns.values():
            if turn.user_id == user_id and turn.thread_id == thread_id and turn.status == "running":
                return ChatTurnAdmission(outcome="busy")
        counter += 1
        turn = ChatTurnItem(
            turn_id=f"turn-{counter}",
            thread_id=thread_id,
            user_id=user_id,
            message_id=message_id,
            text_id=text_id,
            idempotency_key=idempotency_key,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            expires_at="2099-01-01T00:00:00+00:00",
        )
        turns[turn.turn_id] = turn
        events[turn.turn_id] = {}
        return ChatTurnAdmission(outcome="created", turn=turn)

    async def append_chat_turn_events(turn_id: str, seq: int, parts_json: str) -> bool:
        batches = events.setdefault(turn_id, {})
        if seq in batches:
            return False
        batches[seq] = parts_json
        return True

    async def read_chat_turn_events(turn_id: str, after_seq: int, limit: int):
        turn = turns.get(turn_id)
        if turn is None:
            return None
        batches = []
        expected = after_seq + 1
        while len(batches) < limit and expected in events.get(turn_id, {}):
            batches.append(ChatTurnEventBatch(seq=expected, parts_json=events[turn_id][expected]))
            expected += 1
        return ChatTurnEventPage(turn=turn, batches=batches)

    async def finish_chat_turn(turn_id: str, status: str, last_seq: int):
        turn = turns.get(turn_id)
        if turn is None:
            return None
        turns[turn_id] = turn.model_copy(update={"status": status, "last_seq": last_seq})
        return turns[turn_id]

    async def get_active_chat_turn(user_id: str, thread_id: str):
        for turn in turns.values():
            if turn.user_id == user_id and turn.thread_id == thread_id and turn.status == "running":
                return turn
        return None

    async def get_chat_turn(turn_id: str, user_id: str | None = None):
        turn = turns.get(turn_id)
        if turn is None or (user_id is not None and turn.user_id != user_id):
            return None
        return turn

    async def request_chat_turn_cancel(turn_id: str, user_id: str):
        turn = turns.get(turn_id)
        if turn is None or turn.user_id != user_id or turn.status != "running":
            return None
        turns[turn_id] = turn.model_copy(update={"cancel_requested": True})
        return turns[turn_id]

    async def list_expired_chat_turns(expired_before: str, limit: int):
        return [
            ExpiredChatTurn(turn_id=t.turn_id, user_id=t.user_id, thread_id=t.thread_id, expires_at=t.expires_at)
            for t in turns.values()
            if t.expires_at <= expired_before
        ][:limit]

    async def delete_chat_turn(turn_id: str) -> bool:
        events.pop(turn_id, None)
        return turns.pop(turn_id, None) is not None

    for name, impl in {
        "admit_chat_turn": admit_chat_turn,
        "append_chat_turn_events": append_chat_turn_events,
        "read_chat_turn_events": read_chat_turn_events,
        "finish_chat_turn": finish_chat_turn,
        "get_chat_turn": get_chat_turn,
        "list_expired_chat_turns": list_expired_chat_turns,
        "delete_chat_turn": delete_chat_turn,
    }.items():
        mocker.patch(f"reporting.services.chat_turns.report_store.{name}", impl)
    for name, impl in {
        "get_active_chat_turn": get_active_chat_turn,
        "get_chat_turn": get_chat_turn,
        "request_chat_turn_cancel": request_chat_turn_cancel,
    }.items():
        mocker.patch(f"reporting.routes.chat.report_store.{name}", impl)
    return turns


@pytest.fixture(autouse=True)
def _fake_temporal(mocker, _chat_turn_log):
    """Stand in for Temporal by running the turn's activity inline.

    The workflow is what owns a turn in production; here the point is that the
    route admits, hands the turn to a producer, and reads back what it wrote --
    so the producer runs as a task rather than on a worker, and cancelling the
    "workflow" cancels that task.
    """
    running: dict[str, asyncio.Task[Any]] = {}

    class _Handle:
        def __init__(self, workflow_id: str) -> None:
            self._workflow_id = workflow_id

        async def cancel(self) -> None:
            task = running.get(self._workflow_id)
            if task is not None and not task.done():
                task.cancel()

    starts: list[str] = []

    class _Client:
        async def start_workflow(self, _name, invocation, *, id, task_queue):  # noqa: A002
            # Appended, never keyed: a second start against the same workflow id
            # is exactly the bug worth catching, and a dict would hide it.
            starts.append(id)
            turn = _chat_turn_log[invocation.turn_id]
            body = ChatTurnRequest(
                message=invocation.message,
                resume_confirmation_id=invocation.resume_confirmation_id,
                continue_response=invocation.continue_response,
                bypass_confirmations=invocation.bypass_confirmations,
            )
            running[id] = asyncio.create_task(
                chat_turns.produce_turn(turn, invocation.thread_id, body, _current_user())
            )

        def get_workflow_handle(self, workflow_id: str) -> "_Handle":
            return _Handle(workflow_id)

    client = _Client()

    async def _get_one() -> "_Client":
        return client

    mocker.patch("reporting.services.chat_turns.schedule_reconciler.get_client", _get_one)
    # The client is exposed so a test can make the handoff fail; `running` maps
    # workflow id to the task standing in for it.
    return {"client": client, "running": running, "starts": starts}


def _current_user(permissions: frozenset[str] = ALL_PERMISSIONS) -> CurrentUser:
    return CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=permissions)


def _make_app(current: CurrentUser | None = None):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current or _current_user()
    return app


async def _admit(client: AsyncClient, body: dict[str, Any]):
    """Ask for a turn. Half of what one `client.post` used to do."""
    payload = dict(body)
    thread_id = payload.pop("thread_id")
    return await client.post(f"/api/v1/chat/threads/{thread_id}/turns", json=payload)


async def _attach(client: AsyncClient, turn_id: str):
    return await client.get(f"/api/v1/chat/turns/{turn_id}/stream")


async def _send(client: AsyncClient, json: dict[str, Any], **kwargs: Any):
    """Send a message the way the browser does: admit a turn, then attach.

    Returns the *admission* response when admission refuses, and the stream
    otherwise, so a test can assert on whichever of the two halves it is about.
    """
    admission = await _admit(client, json)
    if admission.status_code >= 400:
        return admission
    turn_id = admission.json()["turn_id"]
    return await _attach(client, turn_id)


def _require_turn(admission: ChatTurnAdmission) -> ChatTurnItem:
    """Unwrap an admission a test expects to have succeeded."""
    assert admission.turn is not None, f"admission was {admission.outcome}"
    return admission.turn


async def _open_turn(user_id: str, thread_id: str, message_id: str = "msg_9", text_id: str = "text_9"):
    """Put a running turn in the log without going through a route."""
    admission = await chat_turns.report_store.admit_chat_turn(user_id, thread_id, message_id, text_id, None)
    assert admission.turn is not None
    return admission.turn


async def _reconnect(client: AsyncClient, thread_id: str):
    """Reattach the way a reloaded client does: find the active turn, then read it."""
    active = await client.get(f"/api/v1/chat/threads/{thread_id}/turns/active")
    if active.status_code != 200:
        return active
    return await _attach(client, active.json()["turn_id"])


def _patch_chat_sessions(mocker, existing: list[tuple[str, str]] | None = None):
    sessions: dict[tuple[str, str], ChatSessionItem] = _SESSIONS
    sessions.clear()
    counter = 0
    id_counter = 1000

    def _now() -> str:
        nonlocal counter
        counter += 1
        return f"2024-01-01T00:00:{counter:02d}+00:00"

    for user_id, thread_id in existing or []:
        now = _now()
        sessions[(user_id, thread_id)] = ChatSessionItem(
            thread_id=thread_id,
            title="",
            created_at=now,
            updated_at=now,
        )

    async def list_chat_sessions(user_id: str, limit: int) -> list[ChatSessionItem]:
        return sorted(
            [session for (owner, _), session in sessions.items() if owner == user_id],
            key=lambda session: session.updated_at,
            reverse=True,
        )[:limit]

    async def get_chat_session(user_id: str, thread_id: str) -> ChatSessionItem | None:
        return sessions.get((user_id, thread_id))

    async def create_chat_session(user_id: str, title: str) -> ChatSessionItem:
        nonlocal id_counter
        id_counter += 1
        thread_id = str(id_counter)
        now = _now()
        session = ChatSessionItem(thread_id=thread_id, title=title, created_at=now, updated_at=now)
        sessions[(user_id, thread_id)] = session
        return session

    async def touch_chat_session(user_id: str, thread_id: str) -> ChatSessionItem | None:
        existing_session = sessions.get((user_id, thread_id))
        if existing_session is None:
            return None
        updated = existing_session.model_copy(update={"updated_at": _now()})
        sessions[(user_id, thread_id)] = updated
        return updated

    async def update_chat_session_title(user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        existing_session = sessions.get((user_id, thread_id))
        if existing_session is None:
            return None
        updated = existing_session.model_copy(update={"title": title, "updated_at": _now()})
        sessions[(user_id, thread_id)] = updated
        return updated

    async def delete_chat_session(user_id: str, thread_id: str) -> bool:
        return sessions.pop((user_id, thread_id), None) is not None

    async def claim_chat_session_for_retirement(user_id: str, thread_id: str, expected_updated_at: str) -> bool:
        existing_session = sessions.get((user_id, thread_id))
        return existing_session is not None and existing_session.updated_at == expected_updated_at

    mocker.patch("reporting.routes.chat.report_store.list_chat_sessions", list_chat_sessions)
    mocker.patch("reporting.routes.chat.report_store.get_chat_session", get_chat_session)
    mocker.patch("reporting.routes.chat.report_store.create_chat_session", create_chat_session)
    mocker.patch("reporting.routes.chat.report_store.touch_chat_session", touch_chat_session)
    mocker.patch("reporting.routes.chat.report_store.update_chat_session_title", update_chat_session_title)
    mocker.patch("reporting.routes.chat.report_store.delete_chat_session", delete_chat_session)
    mocker.patch(
        "reporting.routes.chat.report_store.claim_chat_session_for_retirement",
        claim_chat_session_for_retirement,
    )
    return sessions


async def test_chat_stream_refuses_a_session_claimed_for_retirement(mocker):
    """The reaper claims a session before destroying its checkpoint and sandbox,
    and a claimed session admits no turn. Starting one anyway would run it
    against state that is being deleted underneath it."""
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    mocker.patch(
        "reporting.services.chat_turns.report_store.admit_chat_turn",
        AsyncMock(return_value=ChatTurnAdmission(outcome="retired")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _admit(client, {"message": "Hi", "thread_id": "1001"})

    # A refusal is the answer to the request that asked for the turn, not a
    # frame inside a stream that was opened anyway.
    assert response.status_code == 404
    assert "retired" in response.json()["error"]
    assert fake_graph.calls == []


async def test_admission_and_turn_creation_are_one_write(mocker):
    """Two writes leave a window: a delete can read the fresh timestamp, claim
    the session, see no running turn and cascade — all between them — and the
    turn is then created against a conversation that no longer exists. So the
    route must not touch the session separately at all."""
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    touched = AsyncMock()
    mocker.patch("reporting.routes.chat.report_store.touch_chat_session", touched)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(client, json={"message": "Hi", "thread_id": "1001"})

    assert '"type":"start"' in response.text
    touched.assert_not_awaited()


async def test_a_store_failure_refuses_the_turn_rather_than_guessing(mocker):
    """Admission is a store write, and a failure means we do not know whether
    the conversation is being torn down. Refusing costs a retry; guessing costs
    the conversation."""
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    mocker.patch(
        "reporting.services.chat_turns.report_store.admit_chat_turn",
        AsyncMock(side_effect=RuntimeError("store down")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _admit(client, {"message": "Hi", "thread_id": "1001"})

    assert response.status_code == 503
    # Distinguishable from retirement: this one is worth retrying.
    assert "try again" in response.json()["error"]
    assert "retired" not in response.json()["error"]
    assert fake_graph.calls == []


async def test_chat_stream_success(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    body = response.text
    assert '"type":"start"' in body
    assert '"type":"text-start"' in body
    assert '"delta":"Hello"' in body
    assert '"delta":" there"' in body
    assert '"finishReason":"stop"' in body
    assert "data: [DONE]" in body

    graph_input, config, stream_mode = fake_graph.calls[0]
    assert config["configurable"]["thread_id"] == "user:test-user-id:thread:1001"
    assert config["configurable"]["current_user"].user.user_id == "test-user-id"
    assert stream_mode == "custom"
    assert graph_input["messages"][0].content == "Hi"
    controller = config["configurable"]["budget_controller"]
    assert isinstance(controller, BudgetController)
    assert graph_input["budget"] == controller.snapshot()


async def test_chat_stream_surfaces_output_limit_finish_reason(mocker):
    fake_graph = FakeCutoffChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 200
    body = response.text
    assert '"delta":"Partial answer"' in body
    assert '"finishReason":"length"' in body
    assert '"response_cut_off":true' in body
    assert '"messageMetadata":{"finish_reason":"length","response_cut_off":true}' in body


async def test_chat_stream_continuation_reuses_message_id_and_emits_marker(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={
                "thread_id": "1001",
                "continue_response": True,
                "continue_message_id": "assistant-message-1",
            },
        )

    assert response.status_code == 200
    body = response.text
    assert '"messageId":"assistant-message-1"' in body
    assert '"delta":"\\n\\n{% continuation /%}\\n\\n"' in body
    graph_input, _, _ = fake_graph.calls[0]
    assert graph_input["messages"][0].additional_kwargs["continue_response"] is True


def test_history_message_metadata_marks_output_limit_notice():
    message = type(
        "Message",
        (),
        {
            "response_metadata": {
                "seizu_details": [
                    {
                        "kind": "tool",
                        "title": "Tool: graph__schema",
                        "status": "completed",
                        "arguments": "{}",
                        "body": '{"labels":["CVE"]}',
                    }
                ]
            }
        },
    )()
    metadata = chat._history_message_metadata(
        message,
        "assistant",
        "Partial.\n\n> Response stopped because the model hit its output limit. Ask me to continue from here.",
    )

    assert metadata == {
        "finish_reason": "length",
        "response_cut_off": True,
        "details": [
            {
                "kind": "tool",
                "title": "Tool: graph__schema",
                "status": "completed",
                "arguments": "{}",
                "body": '{"labels":["CVE"]}',
            }
        ],
    }
    assert (
        chat._history_message_metadata(
            type("Message", (), {})(),
            "user",
            "Response stopped because the model hit its output limit",
        )
        is None
    )


def test_history_message_metadata_keeps_orchestration_detail_kinds():
    message = type(
        "Message",
        (),
        {
            "response_metadata": {
                "seizu_details": [
                    {"kind": "routing", "title": "Routing", "status": "completed", "route": "orchestrate"},
                    {"kind": "plan", "title": "Plan", "status": "completed"},
                    {"kind": "step", "title": "Step: gather", "status": "completed", "step_id": "s1"},
                    {
                        "kind": "tool",
                        "title": "Tool: github_security__org_overview",
                        "status": "completed",
                        "step_id": "s1",
                    },
                    {"kind": "verify", "title": "Verify: gather", "status": "completed", "step_id": "s1"},
                    {"kind": "bogus", "title": "ignored"},
                ]
            }
        },
    )()
    metadata = chat._history_message_metadata(message, "assistant", "An orchestrated answer.")

    assert metadata is not None
    details = metadata["details"]
    assert [d["kind"] for d in details] == ["routing", "plan", "step", "tool", "verify"]
    # step_id and route survive so the UI can rebuild the hierarchy on reload.
    assert details[0]["route"] == "orchestrate"
    assert details[2]["step_id"] == "s1"
    assert details[3]["step_id"] == "s1"


def test_history_message_metadata_prefers_seizu_output_limit_over_text():
    """seizu_output_limit in response_metadata takes precedence over text matching."""
    message = type(
        "Message",
        (),
        {"response_metadata": {"seizu_output_limit": True}},
    )()
    metadata = chat._history_message_metadata(message, "assistant", "No notice phrase here.")
    assert metadata is not None
    assert metadata["finish_reason"] == "length"
    assert metadata["response_cut_off"] is True


def test_history_message_metadata_falls_back_to_text_when_no_signal():
    """Text-based fallback still works for messages persisted before seizu_output_limit."""
    message = type("Message", (), {"response_metadata": {}})()
    text = "partial\n\n> Response stopped because the model hit its output limit. Ask me to continue."
    metadata = chat._history_message_metadata(message, "assistant", text)
    assert metadata is not None
    assert metadata["finish_reason"] == "length"


def test_history_message_metadata_no_false_positive_from_seizu_output_limit_absent():
    """Message with no output_limit signal and no notice phrase has no finish_reason."""
    message = type("Message", (), {"response_metadata": {}})()
    metadata = chat._history_message_metadata(message, "assistant", "Normal response.")
    assert metadata is None


def test_history_message_metadata_includes_run_status_errors_and_budget():
    message = type(
        "Message",
        (),
        {
            "response_metadata": {
                "seizu_run_status": "partial",
                "seizu_run_errors": ["Planner fallback"],
                "seizu_budget": {
                    "mode": "finalizing",
                    "total_tokens": 12_345,
                    "cost_usd": 0.12,
                    "llm_calls": 9,
                    "exhaustion_reason": "The run token budget is reserved for final synthesis.",
                    "phases": {
                        "planner": {"total_tokens": 1200, "llm_calls": 1, "internal": "not exposed"},
                    },
                    "internal": "not exposed",
                },
            }
        },
    )()

    metadata = chat._history_message_metadata(message, "assistant", "Partial result.")

    assert metadata == {
        "run_status": "partial",
        "run_errors": ["Planner fallback"],
        "budget": {
            "mode": "finalizing",
            "total_tokens": 12_345,
            "cost_usd": 0.12,
            "llm_calls": 9,
            "exhaustion_reason": "The run token budget is reserved for final synthesis.",
            "phases": {
                "planner": {"total_tokens": 1200, "llm_calls": 1},
            },
        },
    }


async def test_chat_stream_emits_detail_data_parts(mocker):
    fake_graph = FakeDetailChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 200
    body = response.text
    assert '"type":"data-seizu-detail"' in body
    assert '"id":"detail_1"' in body
    assert '"title":"Tool: graph__schema"' in body
    assert '"delta":"Schema has CVEs."' in body


async def test_chat_stream_with_real_graph_emits_tokens(mocker):
    """Exercise the real compiled LangGraph so a change in LangGraph's custom
    stream output shape (which the FakeChatGraph can't catch) is detected."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1002")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1002"},
        )

    assert response.status_code == 200
    body = response.text
    # The mock agent streams "I received your message: Hi" in 8-char chunks.
    deltas = "".join(
        json.loads(line[len("data: ") :])["delta"]
        for line in body.splitlines()
        if line.startswith("data: ") and '"text-delta"' in line
    )
    assert deltas == "I received your message: Hi"
    assert '"finishReason":"stop"' in body


async def test_chat_stream_requires_chat_permission(mocker):
    mocker.patch("reporting.services.chat_turns.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app(_current_user(frozenset()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 403


async def test_chat_stream_bypass_requires_permission(mocker):
    mocker.patch("reporting.services.chat_turns.get_chat_graph")
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app(_current_user(frozenset({"chat:use"})))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001", "bypass_confirmations": True},
        )

    assert response.status_code == 403
    assert "chat:bypass_permissions" in response.text


async def test_chat_stream_bypass_flag_reaches_graph_config(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app(_current_user(frozenset({"chat:use", "chat:bypass_permissions"})))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001", "bypass_confirmations": True},
        )

    assert response.status_code == 200
    _input, config, _mode = fake_graph.calls[0]
    assert config["configurable"]["bypass_confirmations"] is True


async def test_chat_stream_bypass_defaults_off(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 200
    _input, config, _mode = fake_graph.calls[0]
    assert config["configurable"]["bypass_confirmations"] is False


async def test_chat_stream_validates_body(mocker):
    mocker.patch("reporting.services.chat_turns.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "", "thread_id": "1001"},
        )

    assert response.status_code == 422


async def test_chat_stream_rejects_missing_session_before_graph_write(mocker):
    graph = mocker.patch("reporting.services.chat_turns.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _admit(client, {"message": "Hi", "thread_id": "9999"})

    assert response.status_code == 404
    assert response.json()["error"] == "Session not found"
    graph.assert_not_called()


async def test_chat_history_round_trips_persisted_messages(mocker):
    """Stream a turn, then fetch history from the same checkpoint-backed graph."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    # The stream endpoint and load_thread_messages resolve get_chat_graph
    # through different module bindings; patch both so they share one graph.
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1003")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await _send(
            client,
            json={"message": "Hi", "thread_id": "1003"},
        )
        assert stream.status_code == 200
        history = await client.get("/api/v1/chat/history", params={"thread_id": "1003"})

    assert history.status_code == 200
    messages = history.json()["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["text"] == "Hi"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["text"] == "I received your message: Hi"
    assert all(message["id"] for message in messages)


async def test_chat_history_timestamps_both_turns(mocker):
    """Every persisted turn comes back with the time it was recorded."""
    from datetime import datetime

    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1013")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _send(client, json={"message": "Hi", "thread_id": "1013"})
        assert first.status_code == 200
        second = await _send(client, json={"message": "Again", "thread_id": "1013"})
        assert second.status_code == 200
        history = await client.get("/api/v1/chat/history", params={"thread_id": "1013"})

    messages = history.json()["messages"]
    stamps = [message["metadata"]["created_at"] for message in messages]
    assert len(stamps) == 4
    # Parseable, ordered, and — the point of stamping only unseen ids — the first
    # turn keeps its own time when the second turn rewrites the message list.
    parsed = [datetime.fromisoformat(stamp) for stamp in stamps]
    assert parsed == sorted(parsed)
    assert all(stamp.tzinfo is not None for stamp in parsed)


async def test_chat_history_omits_timestamp_for_unstamped_messages(mocker):
    """A message persisted before timestamps existed reports no time."""
    from langchain_core.messages import AIMessage, HumanMessage

    from reporting.routes.chat import _to_history_message

    user = _to_history_message(HumanMessage(content="Hi", id="m1"), 0)
    assistant = _to_history_message(AIMessage(content="Hello", id="m2"), 1)
    assert user is not None and assistant is not None
    assert user.metadata is None
    assert assistant.metadata is None


async def test_chat_history_hides_and_collapses_continue_response_turn(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1010")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _send(
            client,
            json={"message": "Hi", "thread_id": "1010"},
        )
        assert first.status_code == 200
        continuation = await _send(
            client,
            json={"thread_id": "1010", "continue_response": True},
        )
        assert continuation.status_code == 200
        history = await client.get("/api/v1/chat/history", params={"thread_id": "1010"})

    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "Hi"
    assert messages[1]["text"].startswith("I received your message: Hi")
    # Collapsed back into one answer: no cut-off marker survives, and the merged
    # message keeps the timestamp of the turn it continues rather than gaining a
    # second one.
    assert set(messages[1]["metadata"]) == {"created_at"}


async def test_chat_sessions_list_sorts_by_updated_at(mocker):
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        old_session = await client.post("/api/v1/chat/sessions", json={"title": "Old"})
        new_session = await client.post("/api/v1/chat/sessions", json={"title": "New"})
        assert old_session.status_code == 201
        assert new_session.status_code == 201
        old_thread_id = old_session.json()["thread_id"]
        new_thread_id = new_session.json()["thread_id"]
        renamed_old = await client.patch(f"/api/v1/chat/sessions/{old_thread_id}", json={"title": "Renamed old"})
        assert renamed_old.status_code == 200
        response = await client.get("/api/v1/chat/sessions", params={"limit": 10})

    assert response.status_code == 200
    assert [session["thread_id"] for session in response.json()["sessions"]] == [old_thread_id, new_thread_id]


async def test_create_chat_session_rejects_client_thread_id(mocker):
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/sessions", json={"thread_id": "legacy", "title": "Legacy"})

    assert response.status_code == 422


async def test_get_chat_session_returns_only_owned_session(mocker):
    _patch_chat_sessions(mocker, [("test-user-id", "1005"), ("other-user-id", "1006")])
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owned = await client.get("/api/v1/chat/sessions/1005")
        other = await client.get("/api/v1/chat/sessions/1006")

    assert owned.status_code == 200
    assert owned.json()["thread_id"] == "1005"
    assert other.status_code == 404


async def test_chat_history_isolated_per_user(mocker):
    """A thread id is scoped to the user, so another user sees no history."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1007")])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.dependency_overrides[get_current_user] = lambda: _current_user()
        await _send(client, json={"message": "Hi", "thread_id": "1007"})

        other = CurrentUser(
            user=User(
                user_id="other-user-id",
                sub="sub-other",
                iss="https://idp.example.com",
                email="other@example.com",
                created_at="2024-01-01T00:00:00+00:00",
                last_login="2024-01-01T00:00:00+00:00",
            ),
            jwt_claims={},
            permissions=ALL_PERMISSIONS,
        )
        app.dependency_overrides[get_current_user] = lambda: other
        history = await client.get("/api/v1/chat/history", params={"thread_id": "1007"})

    assert history.status_code == 404


async def test_chat_delete_removes_session_and_persisted_history(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1008")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await _send(
            client,
            json={"message": "Delete this", "thread_id": "1008"},
        )
        assert stream.status_code == 200
        before_delete = await client.get("/api/v1/chat/history", params={"thread_id": "1008"})
        assert before_delete.status_code == 200
        assert before_delete.json()["messages"]

        deleted = await client.delete("/api/v1/chat/sessions/1008")
        assert deleted.status_code == 204
        after_delete = await client.get("/api/v1/chat/history", params={"thread_id": "1008"})
        send_after_delete = await _admit(client, {"message": "Still there?", "thread_id": "1008"})

    assert after_delete.status_code == 404
    assert send_after_delete.status_code == 404
    assert send_after_delete.json()["error"] == "Session not found"


async def test_chat_delete_is_idempotent_for_missing_session(mocker):
    _patch_chat_sessions(mocker)
    delete_state = mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", AsyncMock())
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/9999")

    assert response.status_code == 204
    delete_state.assert_not_awaited()


async def test_chat_delete_reports_a_failed_checkpoint_cleanup(mocker):
    """It used to swallow this and return 204 -- having already deleted the
    session record, which was the only thing that made the thread findable. The
    transcript stayed stored forever with nothing left to retry from, and
    nothing said so."""
    _patch_chat_sessions(mocker, [("test-user-id", "1010")])
    mocker.patch(
        "reporting.routes.chat.session_reaper.delete_session_state",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    )
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1010")

    assert response.status_code == 503


async def test_chat_delete_store_failure_returns_503(mocker):
    _patch_chat_sessions(mocker, [("test-user-id", "1011")])
    mocker.patch(
        "reporting.routes.chat.session_reaper.delete_session_state",
        AsyncMock(side_effect=RuntimeError("contention")),
    )
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1011")

    assert response.status_code == 503


async def test_the_session_record_is_deleted_last(mocker):
    """The record is the thread's tombstone: removing it before the checkpoint
    means a failure leaves the transcript with nothing to retry from."""
    order: list[str] = []
    _patch_chat_sessions(mocker, [("test-user-id", "1012")])

    async def _delete_state(user_id: str, thread_id: str) -> None:
        order.append("checkpoint")
        order.append("record")

    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", _delete_state)
    mocker.patch(
        "reporting.routes.chat.report_store.delete_chat_session",
        AsyncMock(side_effect=AssertionError("the route must not delete the record itself")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.delete("/api/v1/chat/sessions/1012")).status_code == 204

    assert order == ["checkpoint", "record"]


async def test_chat_stream_no_longer_treats_slash_text_as_command(mocker):
    """Slash-looking text is just chat input; native UI tooling will own actions."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1009")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await _send(
            client,
            json={"message": "/tools", "thread_id": "1009"},
        )
        assert stream.status_code == 200
        deltas = "".join(
            json.loads(line[len("data: ") :])["delta"]
            for line in stream.text.splitlines()
            if line.startswith("data: ") and '"text-delta"' in line
        )
        assert deltas == "I received your message: /tools"

        history = await client.get("/api/v1/chat/history", params={"thread_id": "1009"})

    assert history.status_code == 200
    assert [message["text"] for message in history.json()["messages"]] == ["/tools", "I received your message: /tools"]


def test_chat_routes_registered_when_enabled():
    paths = {getattr(route, "path", None) for route in create_app().routes}
    assert "/api/v1/chat/threads/{thread_id}/turns" in paths
    assert "/api/v1/chat/threads/{thread_id}/turns/active" in paths
    assert "/api/v1/chat/turns/{turn_id}/stream" in paths
    assert "/api/v1/chat/turns/{turn_id}/cancel" in paths
    assert "/api/v1/chat/history" in paths


def test_chat_routes_absent_when_disabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", False)
    paths = {getattr(route, "path", None) for route in create_app().routes}
    assert "/api/v1/chat/threads/{thread_id}/turns" not in paths
    assert "/api/v1/chat/turns/{turn_id}/stream" not in paths
    assert "/api/v1/chat/history" not in paths


async def test_chat_history_requires_chat_permission(mocker):
    mocker.patch("reporting.services.chat_turns.get_chat_graph")
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app(_current_user(frozenset()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/history", params={"thread_id": "1001"})

    assert response.status_code == 403


async def test_update_chat_session_title_returns_404_when_not_found(mocker):
    _patch_chat_sessions(mocker)  # no sessions seeded → update returns None
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/v1/chat/sessions/99999",
            json={"title": "New name"},
        )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("origin", "scheduled_chat_id"),
    [("scheduled", "sc-1"), ("workflow", None)],
)
async def test_chat_stream_rejects_headless_sessions(mocker, origin, scheduled_chat_id):
    mocker.patch("reporting.services.chat_turns.get_chat_graph")
    headless = ChatSessionItem(
        thread_id="1001",
        title="Digest – 2026-06-11",
        created_at="2024-01-01T00:00:01+00:00",
        updated_at="2024-01-01T00:00:01+00:00",
        origin=origin,
        scheduled_chat_id=scheduled_chat_id,
    )
    mocker.patch(
        "reporting.routes.chat.report_store.get_chat_session",
        mocker.AsyncMock(return_value=headless),
    )
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(
            client,
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 403
    assert "read-only" in response.text


# ---------------------------------------------------------------------------
# Reconnecting to a running turn
# ---------------------------------------------------------------------------


async def test_a_replay_is_byte_identical_to_the_original_delivery(mocker, _chat_turn_log):
    """The producer renders the frames once and both deliveries read them back,
    so there is no second rendering path that can drift from the first."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        live = await _send(client, json={"message": "Hi", "thread_id": "1001"})
        # The turn is finished, so the store still holds its log even though
        # nothing is producing into it any more.
        turn_id = next(iter(_chat_turn_log))
        replay = "".join([frame async for frame in chat_turns.tail_turn(turn_id)])

    assert replay == live.text


async def test_a_replay_reuses_the_message_id_so_the_client_rebuilds_one_message(mocker, _chat_turn_log):
    """A fresh id would read to the client as a second assistant message rather
    than the same one being rebuilt."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(client, json={"message": "Hi", "thread_id": "1001"})

    turn = next(iter(_chat_turn_log.values()))
    assert f'"messageId":"{turn.message_id}"' in response.text
    assert f'"id":"{turn.text_id}"' in response.text


async def test_reconnect_returns_204_when_no_turn_is_running(mocker):
    """The AI SDK reads 204 as "the response already finished" and stops trying."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _reconnect(client, "1001")

    assert response.status_code == 204


async def test_reconnect_streams_a_running_turn_from_its_first_frame(mocker, _chat_turn_log):
    """The SDK's reconnect protocol carries no cursor, so the replay has to
    start at the beginning of the turn -- including the ``start`` frame, which
    is what tells the client which message it is rebuilding."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = await _open_turn("test-user-id", "1001", "msg_9", "text_9")
    await chat_turns.report_store.append_chat_turn_events(
        turn.turn_id, 1, '[{"type":"start","messageId":"msg_9"},{"type":"text-start","id":"text_9"}]'
    )
    await chat_turns.report_store.append_chat_turn_events(
        turn.turn_id, 2, '[{"type":"text-delta","id":"text_9","delta":"Half a"}]'
    )

    # The turn has to be running when the route resolves it, or there would be
    # nothing to reconnect to -- and it has to finish afterwards, or the reader
    # would tail it forever. Sequencing the two on the lookup keeps that exact
    # order without depending on scheduling.
    resolved = asyncio.Event()
    lookup = chat.report_store.get_active_chat_turn

    async def _resolving_lookup(user_id: str, thread_id: str):
        found = await lookup(user_id, thread_id)
        resolved.set()
        return found

    async def _finish_once_resolved() -> None:
        await resolved.wait()
        await chat_turns.report_store.finish_chat_turn(turn.turn_id, "completed", 2)

    mocker.patch("reporting.routes.chat.report_store.get_active_chat_turn", _resolving_lookup)
    finisher = asyncio.create_task(_finish_once_resolved())

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _reconnect(client, "1001")
    await finisher

    assert response.status_code == 200
    assert '"messageId":"msg_9"' in response.text
    assert '"delta":"Half a"' in response.text
    assert "data: [DONE]" in response.text


async def test_reconnect_cannot_reach_another_users_turn(mocker, _chat_turn_log):
    """The turn is looked up by (user, thread), so a guessed thread id resolves
    to nothing rather than to someone else's conversation."""
    _patch_chat_sessions(mocker, [("someone-else", "1001")])
    await _open_turn("someone-else", "1001", "msg_9", "text_9")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _reconnect(client, "1001")

    assert response.status_code == 204


async def test_reconnect_requires_chat_permission(mocker):
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app(_current_user(frozenset()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _reconnect(client, "1001")

    assert response.status_code == 403


async def test_a_failed_handoff_is_repaired_by_retrying(mocker, _chat_turn_log, _fake_temporal):
    """The store commits the turn before the workflow is started, so a handoff
    that fails leaves a turn recorded as running with nothing producing it. It
    holds the thread and no reader can ever finish. Retrying the same request
    resolves to that same turn, so ensuring the workflow has to happen for
    `existing` as well -- otherwise the only repair is waiting out the lease."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    started: list[str] = []
    fail_once = {"pending": True}
    real_start = _fake_temporal["client"].start_workflow

    async def _flaky_start(name, invocation, *, id, task_queue):  # noqa: A002
        if fail_once["pending"]:
            fail_once["pending"] = False
            raise RuntimeError("temporal unreachable")
        started.append(id)
        await real_start(name, invocation, id=id, task_queue=task_queue)

    mocker.patch.object(_fake_temporal["client"], "start_workflow", _flaky_start)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {"message": "Hi", "thread_id": "1001", "idempotency_key": "ik_retry"}
        first = await _admit(client, body)
        assert first.status_code == 503

        # The same request again, exactly as a client retrying would send it.
        second = await _admit(client, body)

    assert second.status_code == 200, "the retry should resolve to the turn already admitted"
    assert second.json()["status"] == "existing"
    assert started == [f"seizu-chat-turn:{second.json()['turn_id']}"], (
        "the retry did not start the workflow the stranded turn was waiting for"
    )


async def test_a_repeat_after_the_turn_finished_does_not_start_it_again(mocker, _chat_turn_log, _fake_temporal):
    """A retry can arrive after the turn it names has already completed. Its own
    workflow id is free again by then, so ensuring it blindly would run the whole
    turn a second time and bill a second answer into the same log."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        body = {"message": "Hi", "thread_id": "1001", "idempotency_key": "ik_finished"}
        first = await _admit(client, body)
        turn_id = first.json()["turn_id"]
        await chat_turns.report_store.finish_chat_turn(turn_id, "completed", 2)

        starts_before = len(_fake_temporal["starts"])
        again = await _admit(client, body)

    assert again.status_code == 200
    assert again.json()["turn_id"] == turn_id
    assert len(_fake_temporal["starts"]) == starts_before, "the finished turn was produced a second time"


async def test_a_second_turn_on_a_busy_thread_is_refused_rather_than_started(mocker, _chat_turn_log):
    """Two producers would interleave two answers into one conversation; the
    client is told to reconnect to the turn it already has."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    await _open_turn("test-user-id", "1001", "msg_9", "text_9")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _admit(client, {"message": "Hi", "thread_id": "1001"})

    assert response.status_code == 409
    assert "already has a turn in progress" in response.json()["error"]


async def test_a_failing_turn_still_closes_the_stream(mocker, _chat_turn_log):
    """A producer that raises must still write the terminal frames and status,
    or every reader tailing it hangs until the deadline."""

    class ExplodingGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            self.calls.append((input, config, stream_mode))
            raise RuntimeError("boom")
            yield  # pragma: no cover - makes this an async generator

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=ExplodingGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _send(client, json={"message": "Hi", "thread_id": "1001"})

    assert '"type":"error"' in response.text
    assert '"finishReason":"error"' in response.text
    assert "data: [DONE]" in response.text
    assert next(iter(_chat_turn_log.values())).status == "failed"


async def test_the_turn_runs_without_anyone_reading_it(mocker, _chat_turn_log):
    """The whole point of the change: the producer is detached from the request,
    so the work neither stops nor is lost when the connection watching it goes
    away. Driven without an HTTP client because there is deliberately no client
    involved."""
    release = asyncio.Event()

    class SlowGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            self.calls.append((input, config, stream_mode))
            await release.wait()
            yield {"kind": "token", "content": "Finished anyway"}

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=SlowGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    admission = await chat_turns.start_turn("1001", ChatTurnRequest(message="Hi"), _current_user())
    turn = admission.turn
    assert turn is not None

    # Nothing is tailing this turn, and it still runs to completion.
    release.set()
    for _ in range(500):
        if _chat_turn_log[turn.turn_id].status != "running":
            break
        await asyncio.sleep(0.01)

    assert _chat_turn_log[turn.turn_id].status == "completed"
    replay = "".join([frame async for frame in chat_turns.tail_turn(turn.turn_id)])
    assert '"delta":"Finished anyway"' in replay


async def test_expired_turn_logs_are_swept_after_a_turn(mocker, _chat_turn_log):
    """A log belongs to a turn, not a session, so the turns of a conversation
    nobody deletes would accumulate. Sweeping from the producer keeps this off
    the request and out of a scheduler that deployments may not run."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001"), ("test-user-id", "2002")])
    stale = await _open_turn("test-user-id", "2002", "msg_old", "text_old")
    _chat_turn_log[stale.turn_id] = _chat_turn_log[stale.turn_id].model_copy(
        update={"status": "completed", "expires_at": "2020-01-01T00:00:00+00:00"}
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _send(client, json={"message": "Hi", "thread_id": "1001"})

    assert stale.turn_id not in _chat_turn_log
    # The turn that just ran is still inside its reconnect window.
    assert len(_chat_turn_log) == 1


# ---------------------------------------------------------------------------
# Stopping a turn
# ---------------------------------------------------------------------------


async def test_stop_ends_the_turn_and_not_just_the_reader(mocker, _chat_turn_log):
    """Closing the connection is not enough now that the turn runs beside the
    request: without an explicit stop it keeps generating and can still run the
    actions it had lined up."""
    release = asyncio.Event()
    reached_second_chunk = False

    class SlowGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            nonlocal reached_second_chunk
            self.calls.append((input, config, stream_mode))
            yield {"kind": "token", "content": "Working"}
            await release.wait()
            reached_second_chunk = True
            yield {"kind": "token", "content": " and still going"}

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=SlowGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = _require_turn(await chat_turns.start_turn("1001", ChatTurnRequest(message="Hi"), _current_user()))

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/chat/turns/{turn.turn_id}/cancel")
    assert response.status_code == 204

    release.set()
    for _ in range(500):
        if _chat_turn_log[turn.turn_id].status != "running":
            break
        await asyncio.sleep(0.01)

    assert _chat_turn_log[turn.turn_id].status == "canceled"
    replay = "".join([frame async for frame in chat_turns.tail_turn(turn.turn_id)])
    assert '"delta":"Working"' in replay
    assert " and still going" not in replay
    assert "data: [DONE]" in replay


async def test_cancel_is_idempotent_when_nothing_is_running(mocker, _chat_turn_log):
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/turns/turn-gone/cancel")

    assert response.status_code == 204


async def test_cancel_cannot_reach_another_users_turn(mocker, _chat_turn_log):
    _patch_chat_sessions(mocker, [("someone-else", "1001")])
    turn = await _open_turn("someone-else", "1001", "msg_9", "text_9")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/chat/turns/{turn.turn_id}/cancel")
        assert response.status_code == 204

    assert _chat_turn_log[turn.turn_id].cancel_requested is False


async def test_cancel_requires_chat_permission(mocker, _chat_turn_log):
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app(_current_user(frozenset()))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/turns/turn-1/cancel")

    assert response.status_code == 403


async def test_deleting_a_session_closes_it_to_new_turns_first(mocker, _chat_turn_log):
    """Cancelling alone is not enough: the cancelled turn releases its mutex
    when it stops, and another tab can start a successor before the cascade
    runs. The retirement claim (SBX-011) shuts the door atomically."""
    order: list[str] = []
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    await _open_turn("test-user-id", "1001")

    claim = chat.report_store.claim_chat_session_for_retirement
    cancel = chat.report_store.request_chat_turn_cancel

    async def _recording_claim(user_id: str, thread_id: str, expected_updated_at: str):
        order.append("claim")
        return await claim(user_id, thread_id, expected_updated_at)

    async def _recording_cancel(turn_id: str, user_id: str):
        order.append("cancel")
        result = await cancel(turn_id, user_id)
        # Stand in for the workflow noticing and stopping, so the wait that
        # follows the cancel is not what this test is measuring.
        await chat_turns.report_store.finish_chat_turn(turn_id, "canceled", 0)
        return result

    async def _recording_delete(user_id: str, thread_id: str) -> None:
        order.append("delete")

    mocker.patch("reporting.routes.chat.report_store.claim_chat_session_for_retirement", _recording_claim)
    mocker.patch("reporting.routes.chat.report_store.request_chat_turn_cancel", _recording_cancel)
    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", _recording_delete)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.delete("/api/v1/chat/sessions/1001")).status_code == 204

    assert order == ["claim", "cancel", "delete"]


async def test_deletion_refuses_when_the_turn_will_not_stop(mocker, _chat_turn_log):
    """Deleting anyway leaves the producer recreating checkpoint state behind
    the cascade, which no cleanup undoes. The session stays claimed, so nothing
    new can start and the retry is a plain repeat."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    # A turn with no producer: nothing will ever move it out of "running".
    await _open_turn("test-user-id", "1001", "msg_9", "text_9")
    deleted = AsyncMock()
    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", deleted)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1001")

    assert response.status_code == 503
    deleted.assert_not_awaited()


async def test_deletion_refuses_when_a_turn_starts_under_the_claim(mocker, _chat_turn_log):
    """The claim is conditional on the timestamp read a moment earlier, so a
    turn that started in between makes it fail rather than delete over it."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    deleted = AsyncMock()
    mocker.patch(
        "reporting.routes.chat.report_store.claim_chat_session_for_retirement",
        AsyncMock(return_value=False),
    )
    # Watch the cascade the route calls, or the assertion below holds no matter
    # what the route does.
    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", deleted)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1001")

    assert response.status_code == 503
    deleted.assert_not_awaited()


async def test_a_cancelled_producer_still_records_its_terminal_state(mocker, _chat_turn_log):
    """Cancelling the workflow cancels the activity, and that arrives inside the
    turn as an ordinary `CancelledError`. Swallowing it is not enough: without
    clearing it, every cleanup await is re-cancelled the moment it suspends and
    the turn is left reading as running with nothing left to finish it."""
    started = asyncio.Event()

    class BlockedGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            self.calls.append((input, config, stream_mode))
            yield {"kind": "token", "content": "Working"}
            started.set()
            # Never returns on its own; only cancellation ends this turn.
            await asyncio.Event().wait()

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=BlockedGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = _require_turn(await chat_turns.start_turn("1001", ChatTurnRequest(message="Hi"), _current_user()))
    await asyncio.wait_for(started.wait(), timeout=5)

    await chat_turns.cancel_turn(turn.turn_id)

    for _ in range(500):
        if _chat_turn_log[turn.turn_id].status != "running":
            break
        await asyncio.sleep(0.01)

    assert _chat_turn_log[turn.turn_id].status == "canceled"
    assert _chat_turn_log[turn.turn_id].last_seq is not None
    replay = "".join([frame async for frame in chat_turns.tail_turn(turn.turn_id)])
    assert '"delta":"Working"' in replay
    assert "data: [DONE]" in replay


async def test_cancel_interrupts_a_turn_blocked_mid_call(mocker, _chat_turn_log):
    """A turn is most likely to be stopped exactly while it is blocked on a slow
    model call or tool. Only checking the flag between chunks would let that call
    finish first -- side effects included -- and on another worker the flag is
    the only channel, so it has to interrupt rather than ask."""
    started = asyncio.Event()
    tool_completed = False

    class BlockedGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            nonlocal tool_completed
            self.calls.append((input, config, stream_mode))
            yield {"kind": "token", "content": "Calling a tool"}
            started.set()
            # Stands in for a long tool call that yields nothing while it runs.
            await asyncio.sleep(30)
            tool_completed = True
            yield {"kind": "token", "content": " done"}

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=BlockedGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = _require_turn(await chat_turns.start_turn("1001", ChatTurnRequest(message="Hi"), _current_user()))
    await asyncio.wait_for(started.wait(), timeout=5)

    # Flag only: no local task cancel, which is what another worker sees.
    await chat_turns.report_store.request_chat_turn_cancel(turn.turn_id, "test-user-id")

    for _ in range(500):
        if _chat_turn_log[turn.turn_id].status != "running":
            break
        await asyncio.sleep(0.01)

    assert _chat_turn_log[turn.turn_id].status == "canceled"
    assert tool_completed is False, "the blocked call was allowed to finish first"


async def test_deleting_a_session_waits_for_the_turn_to_stop(mocker, _chat_turn_log):
    """Cascading while the producer is still running lets it append batches and
    recreate checkpoint state behind the delete that just ran."""
    observed: list[str] = []
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = await _open_turn("test-user-id", "1001", "msg_9", "text_9")

    async def _recording_delete(user_id: str, thread_id: str) -> None:
        observed.append(_chat_turn_log[turn.turn_id].status)

    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", _recording_delete)

    # Nothing is producing this turn, so it only reaches a terminal state
    # because something else finishes it -- stand in for the producer noticing.
    async def _finish_soon() -> None:
        await asyncio.sleep(0.05)
        await chat_turns.report_store.finish_chat_turn(turn.turn_id, "canceled", 0)

    finisher = asyncio.create_task(_finish_soon())

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.delete("/api/v1/chat/sessions/1001")).status_code == 204
    await finisher

    assert observed == ["canceled"], "the cascade ran while the turn was still running"


async def test_deletion_refuses_rather_than_racing_when_cancel_fails(mocker, _chat_turn_log):
    """Without knowing the turn is stopped, deleting is the race this exists to
    avoid; a retryable error beats a half-deleted conversation."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    # A *running* turn, or the route short-circuits past the cancel entirely and
    # this passes without ever reaching what it is about.
    await _open_turn("test-user-id", "1001")
    deleted = AsyncMock()
    mocker.patch(
        "reporting.routes.chat.report_store.request_chat_turn_cancel",
        AsyncMock(side_effect=RuntimeError("store down")),
    )
    # The cascade the route actually calls. Patching the store method underneath
    # it instead leaves the real one running against a real backend.
    mocker.patch("reporting.routes.chat.session_reaper.delete_session_state", deleted)

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1001")

    assert response.status_code == 503
    deleted.assert_not_awaited()


async def test_a_stale_stop_cannot_cancel_a_successor_turn(mocker, _chat_turn_log):
    """A Stop can be delayed or retried. By the time it lands the turn it was
    aimed at may have finished and the user started another, so naming the
    thread alone would stop the wrong one."""
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    first = await _open_turn("test-user-id", "1001", "msg_1", "text_1")
    await chat_turns.report_store.finish_chat_turn(first.turn_id, "completed", 1)
    second = await _open_turn("test-user-id", "1001", "msg_2", "text_2")

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # The retry of the stop aimed at the *first* turn finally arrives.
        response = await client.post(f"/api/v1/chat/turns/{first.turn_id}/cancel")

    assert response.status_code == 204
    assert _chat_turn_log[second.turn_id].cancel_requested is False


async def test_the_turn_id_comes_back_before_anything_streams(mocker, _chat_turn_log):
    """The client can only address a stop at a turn it has been told about, and
    it is told by the request that asked for the turn -- so it holds the id from
    the moment it sends, not from the first frame it manages to read."""
    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=FakeChatGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admission = await _admit(client, {"message": "Hi", "thread_id": "1001"})

    assert admission.status_code == 201
    assert admission.json() == {"turn_id": next(iter(_chat_turn_log)), "status": "created"}


async def test_a_repeated_cancel_cannot_interrupt_terminal_cleanup(mocker, _chat_turn_log):
    """The turn clears its own cancellation before its cleanup runs, so a second
    cancel -- from a retried request, or the store flag the first one set --
    would land inside that cleanup and leave the turn recorded as running
    forever."""
    started = asyncio.Event()

    class BlockedGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            self.calls.append((input, config, stream_mode))
            yield {"kind": "token", "content": "Working"}
            started.set()
            await asyncio.sleep(30)

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=BlockedGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    turn = _require_turn(await chat_turns.start_turn("1001", ChatTurnRequest(message="Hi"), _current_user()))
    await asyncio.wait_for(started.wait(), timeout=5)

    # Every repeat, from whatever source, must land harmlessly while the first
    # is still unwinding.
    await chat_turns.cancel_turn(turn.turn_id)
    await chat_turns.cancel_turn(turn.turn_id)
    await chat_turns.cancel_turn(turn.turn_id)

    for _ in range(500):
        if _chat_turn_log[turn.turn_id].status != "running":
            break
        await asyncio.sleep(0.01)

    assert _chat_turn_log[turn.turn_id].status == "canceled"
    assert _chat_turn_log[turn.turn_id].last_seq is not None


async def test_stop_works_between_admission_and_the_first_frame(mocker, _chat_turn_log):
    """Stop is enabled from `submitted`, before any frame has arrived. Admission
    is what closes that window: the id exists before the stream does, so the
    whole period the user can press Stop is a period they can be obeyed in."""
    started = asyncio.Event()

    class BlockedGraph(FakeChatGraph):
        async def astream(self, input, config, *, stream_mode):
            self.calls.append((input, config, stream_mode))
            started.set()
            await asyncio.sleep(30)
            yield {"kind": "token", "content": "too late"}

    mocker.patch("reporting.services.chat_turns.get_chat_graph", return_value=BlockedGraph())
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admission = await _admit(client, {"message": "Hi", "thread_id": "1001"})
        turn_id = admission.json()["turn_id"]
        await asyncio.wait_for(started.wait(), timeout=5)
        # Nothing has been attached to yet, and the stop still lands.
        response = await client.post(f"/api/v1/chat/turns/{turn_id}/cancel")
    assert response.status_code == 204

    for _ in range(500):
        if _chat_turn_log[turn_id].status != "running":
            break
        await asyncio.sleep(0.01)
    assert _chat_turn_log[turn_id].status == "canceled"
