from langchain_core.messages import AIMessage, HumanMessage

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.chat import ChatSessionItem
from reporting.schema.report_config import User
from reporting.services import headless_chat

_NOW = "2024-01-01T00:00:00+00:00"


def _current_user(permissions: frozenset[str]) -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=permissions,
    )


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def astream(self, graph_input, config, stream_mode):
        self.calls.append((graph_input, config, stream_mode))

        async def _gen():
            yield {"kind": "token", "content": "hello"}

        return _gen()


def _patch_store(mocker):
    mocker.patch(
        "reporting.services.report_store.create_chat_session",
        mocker.AsyncMock(
            return_value=ChatSessionItem(thread_id="12345", title="Run", created_at=_NOW, updated_at=_NOW)
        ),
    )
    complete = mocker.patch("reporting.services.report_store.complete_chat_session_run", mocker.AsyncMock())
    graph = _FakeGraph()
    mocker.patch("reporting.services.headless_chat.get_chat_graph", return_value=graph)
    mocker.patch(
        "reporting.services.headless_chat.load_thread_messages",
        mocker.AsyncMock(
            return_value=[
                HumanMessage(content="do the thing"),
                AIMessage(content="done: summary"),
            ]
        ),
    )
    return graph, complete


async def test_run_headless_chat_with_bypass_permission(mocker):
    graph, complete = _patch_store(mocker)
    current = _current_user(frozenset({Permission.CHAT_BYPASS_PERMISSIONS.value}))

    result = await headless_chat.run_headless_chat(
        current,
        prompt="do the thing",
        title="Run – 2026-06-11",
        timeout_seconds=60,
        disclosed_tools=["reports__create"],
    )

    assert result.thread_id == "12345"
    assert result.summary == "done: summary"
    assert result.status == "completed"
    assert result.budget is not None
    complete.assert_awaited_once_with("user-1", "12345", "completed", [])

    graph_input, config, stream_mode = graph.calls[0]
    assert stream_mode == "custom"
    first_message = graph_input["messages"][0]
    assert isinstance(first_message, HumanMessage)
    assert first_message.content == "do the thing"
    assert graph_input["disclosed_tools"] == ["reports__create"]
    configurable = config["configurable"]
    assert configurable["headless"] is True
    assert configurable["bypass_confirmations"] is True
    assert configurable["client_thread_id"] == "12345"
    assert configurable["thread_id"] == "user:user-1:thread:12345"
    assert configurable["budget_controller"].snapshot() == graph_input["budget"]


async def test_run_headless_chat_without_bypass_permission(mocker):
    graph, _complete = _patch_store(mocker)
    current = _current_user(frozenset())

    await headless_chat.run_headless_chat(
        current,
        prompt="do the thing",
        title="Run",
        timeout_seconds=60,
    )

    _graph_input, config, _stream_mode = graph.calls[0]
    configurable = config["configurable"]
    assert configurable["headless"] is True
    assert configurable["bypass_confirmations"] is False
    assert "disclosed_tools" not in graph.calls[0][0]


async def test_scheduled_run_creates_scheduled_origin_session(mocker):
    graph, _complete = _patch_store(mocker)
    create = mocker.patch(
        "reporting.services.report_store.create_chat_session",
        mocker.AsyncMock(
            return_value=ChatSessionItem(
                thread_id="12345",
                title="Run",
                created_at=_NOW,
                updated_at=_NOW,
                origin="scheduled",
                scheduled_chat_id="sc-1",
            )
        ),
    )
    current = _current_user(frozenset())

    await headless_chat.run_headless_chat(
        current,
        prompt="do the thing",
        title="Run",
        timeout_seconds=60,
        scheduled_chat_id="sc-1",
    )

    create.assert_awaited_once_with("user-1", "Run", origin="scheduled", scheduled_chat_id="sc-1")
    assert graph.calls  # the session still runs normally


async def test_run_headless_chat_records_run_warnings(mocker):
    _graph, complete = _patch_store(mocker)
    mocker.patch(
        "reporting.services.headless_chat.load_thread_messages",
        mocker.AsyncMock(
            return_value=[
                AIMessage(
                    content="done",
                    response_metadata={
                        "seizu_run_status": "completed",
                        "seizu_run_errors": ["Planner structured output failed: safe diagnostics"],
                    },
                )
            ]
        ),
    )

    await headless_chat.run_headless_chat(
        _current_user(frozenset()),
        prompt="do the thing",
        title="Run",
        timeout_seconds=60,
    )

    complete.assert_awaited_once_with(
        "user-1",
        "12345",
        "completed",
        ["Planner structured output failed: safe diagnostics"],
    )


async def test_run_headless_chat_records_failure_before_reraising(mocker):
    graph, complete = _patch_store(mocker)

    async def _failed_stream():
        raise RuntimeError("provider unavailable")
        yield

    graph.astream = lambda *_args, **_kwargs: _failed_stream()

    try:
        await headless_chat.run_headless_chat(
            _current_user(frozenset()),
            prompt="do the thing",
            title="Run",
            timeout_seconds=60,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("run_headless_chat should re-raise the provider failure")

    complete.assert_awaited_once_with("user-1", "12345", "failed", ["provider unavailable"])
