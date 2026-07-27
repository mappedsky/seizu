"""The Temporal workflow behind a scheduled chat.

Deliberately much smaller than ``ConfiguredWorkflow``: a scheduled chat is one
prompt and one run, with no stages, named outputs, or post-completion
triggers. Overlap is handled by the Schedule's ``SKIP`` policy rather than a
mutex child workflow, so there is no cross-run coordination here.

Workflow code is deterministic; all I/O lives in ``activities.py``.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import (
        check_scheduled_chat_watch,
        load_scheduled_chat,
        record_scheduled_chat_run_result,
        run_scheduled_chat_session,
    )
    from reporting.temporal_workflows.shared import (
        ScheduledChatDefinition,
        ScheduledChatInvocation,
        ScheduledChatRunResult,
    )

RUN_ID_PREFIX = "seizu-scheduled-chat:"
WATCH_POLL_RUN_ID_PREFIX = "seizu-scheduled-chat-poll:"


_FAILURE_CAUSE_DEPTH = 4


def _failure_text(exc: BaseException) -> str:
    """Return the root cause's message.

    ``str(ActivityError)`` is only "Activity task failed"; the schedule's
    recorded errors are shown to owners, so unwind to the real message the way
    the polling worker used to record it.
    """
    current: BaseException | None = exc
    for _ in range(_FAILURE_CAUSE_DEPTH):
        if current is None or current.__cause__ is None:
            break
        current = current.__cause__
    text = str(current).strip() if current is not None else ""
    return (text or exc.__class__.__name__)[:2000]


async def _record(definition: ScheduledChatDefinition, status: str, error: str | None) -> None:
    await workflow.execute_activity(
        record_scheduled_chat_run_result,
        {
            "scheduled_chat_id": definition.scheduled_chat_id,
            "status": status,
            "error": error,
        },
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )


@workflow.defn(name="seizu_scheduled_chat")
class ScheduledChatWorkflow:
    @workflow.run
    async def run(self, invocation: ScheduledChatInvocation) -> ScheduledChatRunResult:
        definition = await workflow.execute_activity(
            load_scheduled_chat,
            invocation,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if definition.skipped_reason is not None:
            # Skipped runs are not failures and are not recorded on the
            # schedule; the run simply doesn't happen.
            return ScheduledChatRunResult(status="skipped", error=definition.skipped_reason)

        try:
            result = await workflow.execute_activity(
                run_scheduled_chat_session,
                definition,
                start_to_close_timeout=timedelta(seconds=definition.timeout_seconds + 30),
                heartbeat_timeout=timedelta(seconds=180),
                # A chat run is expensive and non-idempotent: a retry re-bills a
                # full agent session and creates a second transcript.
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except BaseException as exc:
            await _record(definition, "failure", _failure_text(exc))
            raise

        await _record(definition, result.status, result.error)
        return result


@workflow.defn(name="seizu_scheduled_chat_watch_poll")
class ScheduledChatWatchPoll:
    """Poll SyncMetadata and create a visible child only for an actual run."""

    @workflow.run
    async def run(self, invocation: ScheduledChatInvocation) -> ScheduledChatRunResult:
        triggered = await workflow.execute_activity(
            check_scheduled_chat_watch,
            invocation,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        if not triggered:
            return ScheduledChatRunResult(status="skipped", error="watch scan unchanged")

        poll_id = workflow.info().workflow_id
        poll_prefix = f"{WATCH_POLL_RUN_ID_PREFIX}{invocation.scheduled_chat_id}"
        suffix = poll_id.removeprefix(poll_prefix).lstrip("-:")
        actual_id = f"{RUN_ID_PREFIX}{invocation.scheduled_chat_id}:run:{suffix}"
        return await workflow.execute_child_workflow(
            "seizu_scheduled_chat",
            ScheduledChatInvocation(
                scheduled_chat_id=invocation.scheduled_chat_id,
                watch_checked=True,
            ),
            id=actual_id,
        )
