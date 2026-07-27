"""The agent_chat workflow module: run a prompt as an AI session in a stage.

The general-purpose counterpart to ``cve_repo_report``: instead of a hardcoded
assessment, the operator supplies the prompt. It consumes an earlier stage's
rows as untrusted evidence (optional) and publishes the session's summary as
its named output, so a later stage can act on it.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import run_agent_chat_session
    from reporting.temporal_workflows.shared import AgentChatInput, AgentChatResult


@workflow.defn(name="agent_chat")
class AgentChatWorkflow:
    @workflow.run
    async def run(self, input: AgentChatInput) -> AgentChatResult:
        return await workflow.execute_activity(
            run_agent_chat_session,
            input,
            start_to_close_timeout=timedelta(seconds=input.timeout_seconds + 30),
            heartbeat_timeout=timedelta(seconds=180),
            # A chat run is expensive and non-idempotent: a retry re-bills a
            # full agent session and creates a second transcript.
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
