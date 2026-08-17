"""Schedule one dispatch batch's plan steps as independent activities.

The turn itself is still a single activity (``seizu_chat_turn``): it routes,
plans, verifies and writes the answer, and that is deliberate — those stages are
sequential and share a model context, so distributing them would buy nothing and
cost a serialization boundary each. What *is* worth distributing is the middle:
a batch of independent plan steps, which today run as coroutines inside the
coordinating activity and therefore all live on one worker, share its CPU and
memory, and die together if it dies.

This workflow is the fan-out. It schedules one activity per step, so Temporal
places them across the fleet, times each out on its own clock, and contains a
failure to the step that had it. The coordinating activity starts this by id and
awaits it (WF-001 keeps the scheduling here, in workflow code, and every piece of
I/O in the activities it schedules).

**No retries, at either level.** A step is an expensive, non-idempotent LLM run
whose tools have already had their side effects; a second attempt re-bills it and
can re-apply them. Same rule as the turn itself (AGT-008) and scheduled chats
(AGT-007). A step that fails comes back as a failure the dispatcher records, and
the plan's own verify/retry cycle decides whether to run it again — which is a
decision made with the step's evidence in hand, not a blind replay.

Decisions: AGT-018 in ``docs/root/dev/decisions/chat-agent.md``.
"""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from reporting.temporal_workflows.activities import run_chat_worker_step
    from reporting.temporal_workflows.shared import (
        ChatStepFanoutInvocation,
        ChatStepFanoutResult,
        ChatWorkerStepInvocation,
        ChatWorkerStepOutcome,
    )

FANOUT_ID_PREFIX = "seizu-chat-fanout:"


def workflow_id_for(turn_id: str, batch_key: str) -> str:
    """The workflow that owns one dispatch batch of one turn.

    Derived rather than stored, for the same reason the turn's id is: the
    coordinator can name it again without having kept a handle, so a repeat
    resolves to the batch already running instead of starting a second one.
    """
    return f"{FANOUT_ID_PREFIX}{turn_id}:{batch_key}"


def _reason(error: BaseException) -> str:
    """The failure the step actually had, not the wrapper Temporal returns.

    An activity failure arrives as ``ActivityError: Activity task failed``,
    which says only that something went wrong -- the reason ("Chat turn no
    longer exists", a provider error) is one or two ``__cause__`` links down.
    The dispatcher records this string as the step's execution error, and the
    verifier and the user read it, so it has to name the cause.

    Pure, so it is safe in workflow code.
    """
    root: BaseException = error
    seen = 0
    while getattr(root, "__cause__", None) is not None and seen < 5:
        root = root.__cause__  # type: ignore[assignment]
        seen += 1
    return f"{type(root).__name__}: {root}"[:2000]


@workflow.defn(name="seizu_chat_step_fanout")
class ChatStepFanoutWorkflow:
    @workflow.run
    async def run(self, invocation: ChatStepFanoutInvocation) -> ChatStepFanoutResult:
        """Run every step of the batch concurrently and return all their outcomes.

        ``return_exceptions=True`` is what makes the batch a batch: one step
        failing must not cancel its siblings, because their findings are what the
        synthesizer answers from and a partial plan still produces an answer
        (AGT-012). A failure becomes an outcome carrying its error, so the
        dispatcher sees one entry per step either way.
        """
        settled = await asyncio.gather(
            *(self._run_step(step, invocation.step_timeout_seconds) for step in invocation.steps),
            return_exceptions=True,
        )
        outcomes: list[ChatWorkerStepOutcome] = []
        for step, result in zip(invocation.steps, settled, strict=True):
            # Discriminate on the *exception*, never on the result type. Inside
            # the workflow sandbox the imported ``ChatWorkerStepOutcome`` is not
            # the same class object an activity result deserializes into, so
            # ``isinstance(result, ChatWorkerStepOutcome)`` is False for a
            # perfectly good outcome -- which sent every *successful* step down
            # the failure branch and killed the whole turn. ``BaseException`` is
            # what ``gather(return_exceptions=True)`` actually returns for a
            # failure, and its identity is stable.
            if isinstance(result, BaseException):
                # Named, so the dispatcher can tell which step is missing
                # evidence rather than inferring it from a gap in the results.
                outcomes.append(ChatWorkerStepOutcome(step_id=step.step_id, error=_reason(result)))
            else:
                outcomes.append(result)
        return ChatStepFanoutResult(outcomes=outcomes)

    async def _run_step(self, step: ChatWorkerStepInvocation, timeout_seconds: int) -> ChatWorkerStepOutcome:
        return await workflow.execute_activity(
            run_chat_worker_step,
            step,
            start_to_close_timeout=timedelta(seconds=timeout_seconds),
            # A step is quietest when it is slowest -- a model call or an
            # external tool can run for minutes without producing anything --
            # so the activity heartbeats on a timer and this only says the
            # worker is alive. Same reasoning as the turn's own heartbeat.
            heartbeat_timeout=timedelta(seconds=180),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
