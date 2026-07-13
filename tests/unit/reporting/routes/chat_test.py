import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.routes import chat
from reporting.schema.chat import ChatSessionItem
from reporting.schema.report_config import User
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


def _current_user(permissions: frozenset[str] = ALL_PERMISSIONS) -> CurrentUser:
    return CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=permissions)


def _make_app(current: CurrentUser | None = None):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current or _current_user()
    return app


def _patch_chat_sessions(mocker, existing: list[tuple[str, str]] | None = None):
    sessions: dict[tuple[str, str], ChatSessionItem] = {}
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

    mocker.patch("reporting.routes.chat.report_store.list_chat_sessions", list_chat_sessions)
    mocker.patch("reporting.routes.chat.report_store.get_chat_session", get_chat_session)
    mocker.patch("reporting.routes.chat.report_store.create_chat_session", create_chat_session)
    mocker.patch("reporting.routes.chat.report_store.touch_chat_session", touch_chat_session)
    mocker.patch("reporting.routes.chat.report_store.update_chat_session_title", update_chat_session_title)
    mocker.patch("reporting.routes.chat.report_store.delete_chat_session", delete_chat_session)
    return sessions


async def test_chat_stream_success(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1002")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
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
    mocker.patch("reporting.routes.chat.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app(_current_user(frozenset()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 403


async def test_chat_stream_bypass_requires_permission(mocker):
    mocker.patch("reporting.routes.chat.get_chat_graph")
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app(_current_user(frozenset({"chat:use"})))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1001", "bypass_confirmations": True},
        )

    assert response.status_code == 403
    assert "chat:bypass_permissions" in response.text


async def test_chat_stream_bypass_flag_reaches_graph_config(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app(_current_user(frozenset({"chat:use", "chat:bypass_permissions"})))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1001", "bypass_confirmations": True},
        )

    assert response.status_code == 200
    _input, config, _mode = fake_graph.calls[0]
    assert config["configurable"]["bypass_confirmations"] is True


async def test_chat_stream_bypass_defaults_off(mocker):
    fake_graph = FakeChatGraph()
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=fake_graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1001")])
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 200
    _input, config, _mode = fake_graph.calls[0]
    assert config["configurable"]["bypass_confirmations"] is False


async def test_chat_stream_validates_body(mocker):
    mocker.patch("reporting.routes.chat.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "", "thread_id": "1001"},
        )

    assert response.status_code == 422


async def test_chat_stream_rejects_missing_session_before_graph_write(mocker):
    graph = mocker.patch("reporting.routes.chat.get_chat_graph")
    _patch_chat_sessions(mocker)
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "9999"},
        )

    assert response.status_code == 200
    assert '"type":"start"' not in response.text
    assert '"errorText":"Session not found"' in response.text
    assert '"finishReason":"error"' in response.text
    assert '"type":"text-start"' not in response.text
    graph.assert_not_called()


async def test_chat_history_round_trips_persisted_messages(mocker):
    """Stream a turn, then fetch history from the same checkpoint-backed graph."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    # The stream endpoint and load_thread_messages resolve get_chat_graph
    # through different module bindings; patch both so they share one graph.
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1003")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await client.post(
            "/api/v1/chat/stream",
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


async def test_chat_history_hides_and_collapses_continue_response_turn(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1010")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1010"},
        )
        assert first.status_code == 200
        continuation = await client.post(
            "/api/v1/chat/stream",
            json={"thread_id": "1010", "continue_response": True},
        )
        assert continuation.status_code == 200
        history = await client.get("/api/v1/chat/history", params={"thread_id": "1010"})

    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["text"] == "Hi"
    assert messages[1]["text"].startswith("I received your message: Hi")
    assert messages[1]["metadata"] is None


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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1007")])

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.dependency_overrides[get_current_user] = lambda: _current_user()
        await client.post("/api/v1/chat/stream", json={"message": "Hi", "thread_id": "1007"})

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
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1008")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Delete this", "thread_id": "1008"},
        )
        assert stream.status_code == 200
        before_delete = await client.get("/api/v1/chat/history", params={"thread_id": "1008"})
        assert before_delete.status_code == 200
        assert before_delete.json()["messages"]

        deleted = await client.delete("/api/v1/chat/sessions/1008")
        assert deleted.status_code == 204
        after_delete = await client.get("/api/v1/chat/history", params={"thread_id": "1008"})
        stream_after_delete = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Still there?", "thread_id": "1008"},
        )

    assert after_delete.status_code == 404
    assert stream_after_delete.status_code == 200
    assert '"errorText":"Session not found"' in stream_after_delete.text


async def test_chat_delete_is_idempotent_for_missing_session(mocker):
    _patch_chat_sessions(mocker)
    delete_messages = mocker.patch("reporting.routes.chat.delete_thread_messages")
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/9999")

    assert response.status_code == 204
    delete_messages.assert_not_called()


async def test_chat_delete_ignores_checkpoint_cleanup_failure(mocker):
    _patch_chat_sessions(mocker, [("test-user-id", "1010")])
    mocker.patch("reporting.routes.chat.delete_thread_messages", side_effect=RuntimeError("cleanup failed"))
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1010")

    assert response.status_code == 204


async def test_chat_delete_store_failure_returns_503(mocker):
    _patch_chat_sessions(mocker, [("test-user-id", "1011")])
    mocker.patch("reporting.routes.chat.report_store.delete_chat_session", side_effect=RuntimeError("contention"))
    mocker.patch("reporting.routes.chat.delete_thread_messages")
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/sessions/1011")

    assert response.status_code == 503


async def test_chat_stream_no_longer_treats_slash_text_as_command(mocker):
    """Slash-looking text is just chat input; native UI tooling will own actions."""
    from langgraph.checkpoint.memory import MemorySaver

    from reporting.services.chat_graph import build_chat_graph

    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    graph = build_chat_graph(MemorySaver())
    mocker.patch("reporting.routes.chat.get_chat_graph", return_value=graph)
    mocker.patch("reporting.services.chat_graph.get_chat_graph", return_value=graph)
    _patch_chat_sessions(mocker, [("test-user-id", "1009")])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        stream = await client.post(
            "/api/v1/chat/stream",
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
    assert "/api/v1/chat/stream" in paths
    assert "/api/v1/chat/history" in paths


def test_chat_routes_absent_when_disabled(mocker):
    mocker.patch("reporting.settings.CHAT_ENABLED", False)
    paths = {getattr(route, "path", None) for route in create_app().routes}
    assert "/api/v1/chat/stream" not in paths
    assert "/api/v1/chat/history" not in paths


async def test_chat_history_requires_chat_permission(mocker):
    mocker.patch("reporting.routes.chat.get_chat_graph")
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
    mocker.patch("reporting.routes.chat.get_chat_graph")
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
        response = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Hi", "thread_id": "1001"},
        )

    assert response.status_code == 403
    assert "read-only" in response.text
