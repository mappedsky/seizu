import pytest
from langchain_core.messages import AIMessage, HumanMessage
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError
from reporting.schema.chat import ChatSessionItem
from reporting.schema.report_config import User
from reporting.services.mcp_runtime import ChatActionOutcome, ChatBlockReason
from reporting.temporal_workflows.activities import run_repo_cve_chat
from reporting.temporal_workflows.shared import RepoChatInput

_NOW = "2024-01-01T00:00:00+00:00"


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
            role="seizu-admin",
        ),
        jwt_claims={},
        permissions=frozenset({"chat:skills:call", "skills:render"}),
    )


def _input() -> RepoChatInput:
    return RepoChatInput(
        repo="org/app",
        cves=[{"repo": "org/app", "cve_id": "CVE-2026-0001"}],
        creator_user_id="user-1",
        scheduled_query_id="sq-1",
        confirmation_bypass_tools=["reports__create_version"],
    )


class _FakeGraph:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def astream(self, graph_input, config, stream_mode):
        self.calls.append((graph_input, config, stream_mode))

        async def _gen():
            yield {"kind": "token", "content": "hello"}

        return _gen()


def _patch_happy_path(mocker):
    mocker.patch(
        "reporting.temporal_workflows.activities.resolve_stored_user",
        mocker.AsyncMock(return_value=_current_user()),
    )
    mocker.patch(
        "reporting.services.mcp_runtime.render_prompt_for_chat",
        mocker.AsyncMock(
            return_value=ChatActionOutcome(
                text="Evaluate CVEs for org/app",
                blocked=None,
                tools_required=("cve_analysis__get_cve", "reports__create_version"),
            )
        ),
    )
    mocker.patch(
        "reporting.services.report_store.create_chat_session",
        mocker.AsyncMock(
            return_value=ChatSessionItem(thread_id="12345", title="CVE report", created_at=_NOW, updated_at=_NOW)
        ),
    )
    touch = mocker.patch("reporting.services.report_store.touch_chat_session", mocker.AsyncMock())
    graph = _FakeGraph()
    mocker.patch("reporting.temporal_workflows.activities.get_chat_graph", return_value=graph)
    mocker.patch(
        "reporting.temporal_workflows.activities.load_thread_messages",
        mocker.AsyncMock(
            return_value=[
                HumanMessage(content="Evaluate CVEs for org/app"),
                AIMessage(content="Report created: CVE Findings – org/app"),
            ]
        ),
    )
    return graph, touch


async def test_run_repo_cve_chat(mocker):
    graph, touch = _patch_happy_path(mocker)

    result = await ActivityEnvironment().run(run_repo_cve_chat, _input())

    assert result.repo == "org/app"
    assert result.thread_id == "12345"
    assert result.summary == "Report created: CVE Findings – org/app"
    assert result.error is None
    touch.assert_awaited_once_with("user-1", "12345")

    graph_input, config, stream_mode = graph.calls[0]
    assert stream_mode == "custom"
    first_message = graph_input["messages"][0]
    assert isinstance(first_message, HumanMessage)
    assert first_message.content == "Evaluate CVEs for org/app"
    assert graph_input["disclosed_tools"] == ["cve_analysis__get_cve", "reports__create_version"]
    configurable = config["configurable"]
    assert configurable["client_thread_id"] == "12345"
    assert configurable["thread_id"] == "user:user-1:thread:12345"
    assert configurable["confirmation_bypass_tools"] == ("reports__create_version",)


async def test_identity_failure_is_non_retryable(mocker):
    mocker.patch(
        "reporting.temporal_workflows.activities.resolve_stored_user",
        mocker.AsyncMock(side_effect=HeadlessIdentityError("User 'user-1' is archived")),
    )

    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(run_repo_cve_chat, _input())
    assert exc_info.value.non_retryable is True


async def test_blocked_skill_render_is_non_retryable(mocker):
    mocker.patch(
        "reporting.temporal_workflows.activities.resolve_stored_user",
        mocker.AsyncMock(return_value=_current_user()),
    )
    mocker.patch(
        "reporting.services.mcp_runtime.render_prompt_for_chat",
        mocker.AsyncMock(return_value=ChatActionOutcome(text="denied", blocked=ChatBlockReason.PERMISSION_DENIED)),
    )
    create_session = mocker.patch("reporting.services.report_store.create_chat_session")

    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(run_repo_cve_chat, _input())
    assert exc_info.value.non_retryable is True
    create_session.assert_not_called()
