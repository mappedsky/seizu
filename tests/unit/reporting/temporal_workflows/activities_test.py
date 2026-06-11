import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError
from reporting.schema.report_config import User
from reporting.services.headless_chat import HeadlessChatResult
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
        permissions=frozenset({"chat:skills:call", "skills:render", "chat:bypass_permissions"}),
    )


def _input() -> RepoChatInput:
    return RepoChatInput(
        repo="org/app",
        cves=[{"repo": "org/app", "cve_id": "CVE-2026-0001"}],
        creator_user_id="user-1",
        scheduled_query_id="sq-1",
    )


async def test_run_repo_cve_chat(mocker):
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
    run_chat = mocker.patch(
        "reporting.services.headless_chat.run_headless_chat",
        mocker.AsyncMock(return_value=HeadlessChatResult(thread_id="12345", summary="Report created")),
    )

    result = await ActivityEnvironment().run(run_repo_cve_chat, _input())

    assert result.repo == "org/app"
    assert result.thread_id == "12345"
    assert result.summary == "Report created"
    assert result.error is None

    kwargs = run_chat.await_args.kwargs
    assert kwargs["prompt"] == "Evaluate CVEs for org/app"
    assert kwargs["disclosed_tools"] == ["cve_analysis__get_cve", "reports__create_version"]
    assert "CVE report – org/app" in kwargs["title"]


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
    run_chat = mocker.patch("reporting.services.headless_chat.run_headless_chat")

    with pytest.raises(ApplicationError) as exc_info:
        await ActivityEnvironment().run(run_repo_cve_chat, _input())
    assert exc_info.value.non_retryable is True
    run_chat.assert_not_called()
