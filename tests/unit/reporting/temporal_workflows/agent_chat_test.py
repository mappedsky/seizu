import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows import WORKFLOW_REGISTRY, WorkflowInputContext
from reporting.temporal_workflows.agent_chat import AgentChatWorkflow
from reporting.temporal_workflows.agent_chat_config import build_input, config_fields, validate_config
from reporting.temporal_workflows.shared import AgentChatInput, AgentChatResult


def _context(action_config: dict, rows: list[dict] | None = None) -> WorkflowInputContext:
    return WorkflowInputContext(
        scheduled_query_id="wf-1",
        creator_user_id="user-1",
        rows=rows or [],
        chat_timeout_seconds=600,
        action_config=action_config,
    )


@activity.defn(name="run_agent_chat_session")
async def _run(input: AgentChatInput) -> AgentChatResult:
    return AgentChatResult(status="success", thread_id="t1", summary=f"ran: {input.prompt}")


async def test_workflow_returns_the_session_summary():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[AgentChatWorkflow],
            activities=[_run],
        ):
            result = await env.client.execute_workflow(
                "agent_chat",
                AgentChatInput(workflow_id="wf-1", creator_user_id="user-1", prompt="Check CVEs", timeout_seconds=60),
                id="agent-chat-1",
                task_queue="test-q",
            )

    assert result["status"] == "success"
    assert result["summary"] == "ran: Check CVEs"


def test_config_requires_a_prompt():
    assert validate_config({}) == "'prompt' is required"
    assert validate_config({"prompt": "   "}) == "'prompt' is required"
    assert validate_config({"prompt": "do the thing"}) is None


def test_config_rejects_a_malformed_skill_and_bad_timeout():
    assert "skillset__skill" in str(validate_config({"prompt": "x", "skill": "justaskill"}))
    assert "between 1 and 120" in str(validate_config({"prompt": "x", "timeout_minutes": 0}))
    assert validate_config({"prompt": "x", "skill": "cve_response__cve_repo_assessment"}) is None


def test_build_input_carries_prompt_rows_and_timeout():
    rows = [{"repo": "org/app"}]
    built = build_input(_context({"prompt": "Summarize", "timeout_minutes": 5}, rows))

    assert isinstance(built, AgentChatInput)
    assert built.prompt == "Summarize"
    assert built.creator_user_id == "user-1"
    assert built.timeout_seconds == 300
    assert built.rows == rows


def test_build_input_rejects_a_config_that_no_longer_validates():
    """Dispatch must fail rather than run a misconfigured agent session."""
    with pytest.raises(ValueError, match="'prompt' is required"):
        build_input(_context({}))


def test_registry_entry_is_rowless_and_flags_unclean_runs():
    spec = WORKFLOW_REGISTRY["agent_chat"]

    # An input reference is optional: a prompt alone is a valid run.
    assert spec.requires_rows is False
    assert spec.output_type is AgentChatResult
    assert spec.summary_status(AgentChatResult(status="success")) == "completed"
    assert spec.summary_status(AgentChatResult(status="partial")) == "completed"
    assert spec.summary_status(AgentChatResult(status="failure")) == "completed_with_errors"
    assert spec.summary_status(AgentChatResult(status="budget_exhausted")) == "completed_with_errors"


def test_config_fields_include_the_row_fields_rowless_specs_do_not_get():
    names = {field.name for field in config_fields()}
    assert {
        "prompt",
        "session_title",
        "model_profile_id",
        "skill",
        "timeout_minutes",
        "max_rows",
        "query_return_attribute",
    } == names
