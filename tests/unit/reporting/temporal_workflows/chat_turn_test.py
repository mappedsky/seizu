"""The chat turn workflow's guarantee: a turn always reaches a terminal state.

A turn stuck at "running" is not a cosmetic problem. A reader tails it until its
own deadline, the thread admits no successor until the lease lapses, and
deleting the conversation refuses with a 503 the whole time. So the cases worth
testing here are the ones where our own code does *not* get to run at the end.
"""

import asyncio

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.chat_turn import ChatTurnWorkflow, workflow_id_for
from reporting.temporal_workflows.shared import ChatTurnInvocation, ChatTurnRunResult

#: Turns the fake activities finalized, by turn id.
_FINALIZED: list[str] = []


def _invocation(**kwargs) -> ChatTurnInvocation:
    defaults = dict(
        turn_id="turn-1",
        timeout_seconds=60,
    )
    defaults.update(kwargs)
    return ChatTurnInvocation(**defaults)


@activity.defn(name="finalize_chat_turn")
async def _finalize(invocation: ChatTurnInvocation) -> None:
    _FINALIZED.append(invocation.turn_id)


@activity.defn(name="run_chat_turn")
async def _run_ok(invocation: ChatTurnInvocation) -> ChatTurnRunResult:
    return ChatTurnRunResult(status="completed", last_seq=3)


@activity.defn(name="run_chat_turn")
async def _run_failing(invocation: ChatTurnInvocation) -> ChatTurnRunResult:
    raise ApplicationError("the worker fell over", non_retryable=True)


@activity.defn(name="run_chat_turn")
async def _run_blocking(invocation: ChatTurnInvocation) -> ChatTurnRunResult:
    # Stands in for an activity that is scheduled but never gets to finish --
    # the case where no code of ours runs at the end.
    await asyncio.sleep(600)
    raise AssertionError("should not be reached")  # pragma: no cover


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FINALIZED.clear()


async def test_a_finished_turn_is_not_finalized_twice() -> None:
    """The activity closes its own turn, so the workflow must stay out of it."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChatTurnWorkflow],
            activities=[_run_ok, _finalize],
        ):
            result = await env.client.execute_workflow(
                ChatTurnWorkflow.run,
                _invocation(),
                id=workflow_id_for("turn-1"),
                task_queue="test-q",
            )

    assert result.status == "completed"
    assert _FINALIZED == []


async def test_a_turn_whose_activity_failed_is_finalized() -> None:
    """The activity died without closing its log, so the workflow closes it."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChatTurnWorkflow],
            activities=[_run_failing, _finalize],
        ):
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    ChatTurnWorkflow.run,
                    _invocation(turn_id="turn-2"),
                    id=workflow_id_for("turn-2"),
                    task_queue="test-q",
                )

    assert _FINALIZED == ["turn-2"], "a failed turn was left recorded as running"


async def test_a_cancelled_turn_is_finalized() -> None:
    """Stop and session deletion both cancel the workflow, and the cancel can
    land while the activity is merely scheduled -- so none of the activity's own
    cleanup runs. Cancellation arrives as `CancelledError`, which is not an
    `Exception`, so a handler catching `Exception` misses exactly this case and
    leaves the turn running for the rest of its lease: unreadable, unstoppable,
    and blocking both a successor and the delete that is waiting on it."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=[ChatTurnWorkflow],
            activities=[_run_blocking, _finalize],
        ):
            handle = await env.client.start_workflow(
                ChatTurnWorkflow.run,
                _invocation(turn_id="turn-3"),
                id=workflow_id_for("turn-3"),
                task_queue="test-q",
            )
            # Let the workflow reach the activity before stopping it.
            await asyncio.sleep(0.2)
            await handle.cancel()
            with pytest.raises(WorkflowFailureError):
                await handle.result()

    assert _FINALIZED == ["turn-3"], "a cancelled turn was left recorded as running"
