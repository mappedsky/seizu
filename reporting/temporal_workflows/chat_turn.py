"""The Temporal workflow behind one interactive chat turn.

Interactive turns run here for the same reason scheduled ones do, and for one
more: a turn that outlives its HTTP request needs an owner that outlives the web
process. As a detached task it needed a lease to prove it was alive, a registry
to find it, crash detection to notice it was gone, and a finalizer for the case
where it was cancelled before it started. The workflow is all of those -- it
exists, it is addressable by a name derived from the turn id, and Temporal is
what guarantees it reaches an end.

Workflow code is deterministic; the turn itself is an activity
(``activities.run_chat_turn``), like every other I/O in this package (WF-001).
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import finalize_chat_turn, run_chat_turn
    from reporting.temporal_workflows.shared import ChatTurnInvocation, ChatTurnRunResult

RUN_ID_PREFIX = "seizu-chat-turn:"


def workflow_id_for(turn_id: str) -> str:
    """The workflow that owns a turn, derived from the turn's own id.

    Deterministic, so stopping a turn needs no stored handle: the id the client
    already holds names the workflow.
    """
    return f"{RUN_ID_PREFIX}{turn_id}"


@workflow.defn(name="seizu_chat_turn")
class ChatTurnWorkflow:
    @workflow.run
    async def run(self, invocation: ChatTurnInvocation) -> ChatTurnRunResult:
        """Produce one turn, and make sure it ends in a terminal state.

        **Two paths reach terminal, deliberately.** The activity finalizes its
        own turn, including when it is cancelled -- it is ordinary Python and
        owns the log it has been writing. This workflow finalizes only when the
        activity did not get to: it died with its worker, or timed out, so no
        code of ours ran at the end. Between them there is no way for a turn to
        sit at "running" forever, which is what a reader waits on and what
        keeps the thread from admitting another.
        """
        try:
            return await workflow.execute_activity(
                run_chat_turn,
                invocation,
                start_to_close_timeout=timedelta(seconds=invocation.timeout_seconds + 30),
                heartbeat_timeout=timedelta(seconds=180),
                # A turn is expensive and not idempotent: a retry re-bills it
                # and appends a second answer to the same log. What running here
                # buys is that the turn survives its request and always reaches
                # an end -- not that it is repeated. Same reasoning as AGT-007.
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except Exception:
            # The activity failed or its worker vanished. The workflow itself is
            # healthy here, so this is an ordinary activity call.
            await workflow.execute_activity(
                finalize_chat_turn,
                invocation,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise
