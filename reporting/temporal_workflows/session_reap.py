"""The Temporal workflow behind session reaping.

A Schedule rather than a loop inside the worker process, because worker
replicas are ordinary: every replica running its own timer would list the whole
sandbox account and race the others over each kill. Temporal already owns
run-once-per-interval — a Schedule with a fixed workflow id and
``ScheduleOverlapPolicy.SKIP`` gives exactly one sweep at a time across every
replica, and a sweep that outruns its interval is skipped rather than stacked.

Workflow code is deterministic; the sweep itself is an activity.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import reap_idle_sessions
    from reporting.temporal_workflows.shared import SessionReapResult

#: Fixed ids: one schedule and one workflow id per deployment, by construction.
SCHEDULE_ID = "seizu-session-reap"
WORKFLOW_ID = "seizu-session-reap"
WORKFLOW_TYPE = "seizu_session_reap"


@workflow.defn(name=WORKFLOW_TYPE)
class SessionReapWorkflow:
    """One sweep: idle sessions, then orphan sandboxes."""

    @workflow.run
    async def run(self) -> SessionReapResult:
        return await workflow.execute_activity(
            reap_idle_sessions,
            # Generous, because a sweep's cost is dominated by provider calls
            # for sandboxes it is destroying -- work that must not be abandoned
            # part-way through on a deployment with a real backlog.
            start_to_close_timeout=timedelta(minutes=30),
            # One attempt: the next scheduled sweep is the retry, and it will
            # see whatever this one did not finish. Retrying immediately just
            # re-lists an account that has not changed.
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
