from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError
from reporting.scheduled_query_modules import agent_chat
from reporting.schema.report_config import ScheduledQueryItem, User
from reporting.schema.reporting_config import ScheduledQueryAction
from reporting.services.headless_chat import HeadlessChatResult

_NOW = "2024-01-01T00:00:00+00:00"


def _action(**overrides):
    config = {
        "prompt": "Summarize these findings",
        **overrides,
    }
    return ScheduledQueryAction(action_type="agent_chat", action_config=config)


def _item() -> ScheduledQueryItem:
    return ScheduledQueryItem(
        scheduled_query_id="sq-1",
        name="Weekly findings digest",
        cypher="MATCH (n) RETURN n",
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user-1",
        last_scheduled_at=_NOW,
    )


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
            role="seizu-editor",
        ),
        jwt_claims={},
        permissions=frozenset({"chat:bypass_permissions"}),
    )


def test_action_name():
    assert agent_chat.action_name() == "agent_chat"


def test_required_permission():
    assert agent_chat.required_permission() == "chat:bypass_permissions"


def test_action_config_schema_has_required_prompt():
    schema = {field.name: field for field in agent_chat.action_config_schema()}
    assert schema["prompt"].required is True
    assert schema["prompt"].type == "text"


def test_handle_results_no_results(mocker):
    run_chat = mocker.patch("reporting.services.headless_chat.run_headless_chat")
    agent_chat.handle_results("sq-1", _action(), [])
    run_chat.assert_not_called()


def test_handle_results_runs_headless_agent(mocker):
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    mocker.patch(
        "reporting.authnz.headless.resolve_stored_user",
        mocker.AsyncMock(return_value=_current_user()),
    )
    run_chat = mocker.patch(
        "reporting.services.headless_chat.run_headless_chat",
        mocker.AsyncMock(return_value=HeadlessChatResult(thread_id="12345", summary="done")),
    )
    results = [{"details": {"repo": "org/app", "cve_id": "CVE-1"}}]

    agent_chat.handle_results("sq-1", _action(), results)

    kwargs = run_chat.await_args.kwargs
    assert kwargs["prompt"].startswith("Summarize these findings")
    assert '"cve_id": "CVE-1"' in kwargs["prompt"]
    assert "Weekly findings digest" in kwargs["title"]


def test_handle_results_skips_archived_creator(mocker):
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    mocker.patch(
        "reporting.authnz.headless.resolve_stored_user",
        mocker.AsyncMock(side_effect=HeadlessIdentityError("archived")),
    )
    run_chat = mocker.patch("reporting.services.headless_chat.run_headless_chat")
    results = [{"details": {"repo": "org/app"}}]

    agent_chat.handle_results("sq-1", _action(), results)

    run_chat.assert_not_called()


def test_handle_results_truncates_rows(mocker):
    mocker.patch(
        "reporting.services.report_store.get_scheduled_query",
        mocker.AsyncMock(return_value=_item()),
    )
    mocker.patch(
        "reporting.authnz.headless.resolve_stored_user",
        mocker.AsyncMock(return_value=_current_user()),
    )
    run_chat = mocker.patch(
        "reporting.services.headless_chat.run_headless_chat",
        mocker.AsyncMock(return_value=HeadlessChatResult(thread_id="12345", summary="done")),
    )
    results = [{"details": {"i": i}} for i in range(5)]

    agent_chat.handle_results("sq-1", _action(max_rows=2), results)

    prompt = run_chat.await_args.kwargs["prompt"]
    assert '"i": 1' in prompt
    assert '"i": 4' not in prompt
