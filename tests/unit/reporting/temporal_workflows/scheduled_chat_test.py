from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from reporting.temporal_workflows.scheduled_chat import ScheduledChatWatchPoll, ScheduledChatWorkflow
from reporting.temporal_workflows.shared import (
    ScheduledChatDefinition,
    ScheduledChatInvocation,
    ScheduledChatRunResult,
)

_RECORDED: list[dict[str, str | None]] = []


def _definition(**kwargs) -> ScheduledChatDefinition:
    defaults = dict(
        scheduled_chat_id="sc-1",
        creator_user_id="user-1",
        name="Daily digest",
        prompt="Summarize new findings",
        timeout_seconds=60,
        version=3,
    )
    defaults.update(kwargs)
    return ScheduledChatDefinition(**defaults)


@activity.defn(name="load_scheduled_chat")
async def _load(inv: ScheduledChatInvocation) -> ScheduledChatDefinition:
    return _definition()


@activity.defn(name="load_scheduled_chat")
async def _load_skipped(inv: ScheduledChatInvocation) -> ScheduledChatDefinition:
    return _definition(skipped_reason="disabled")


@activity.defn(name="run_scheduled_chat_session")
async def _run(definition: ScheduledChatDefinition) -> ScheduledChatRunResult:
    return ScheduledChatRunResult(status="success", thread_id="t1", summary="done")


@activity.defn(name="run_scheduled_chat_session")
async def _run_fails(definition: ScheduledChatDefinition) -> ScheduledChatRunResult:
    raise RuntimeError("chat blew up")


@activity.defn(name="record_scheduled_chat_run_result")
async def _record(input: dict[str, str | None]) -> None:
    _RECORDED.append(dict(input))


@activity.defn(name="check_scheduled_chat_watch")
async def _watch_triggered(inv: ScheduledChatInvocation) -> bool:
    return True


@activity.defn(name="check_scheduled_chat_watch")
async def _watch_unchanged(inv: ScheduledChatInvocation) -> bool:
    return False


async def _execute(workflow: str, activities: list, *, workflow_id: str, classes: list | None = None):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-q",
            workflows=classes or [ScheduledChatWorkflow],
            activities=activities,
        ):
            return await env.client.execute_workflow(
                workflow,
                ScheduledChatInvocation(scheduled_chat_id="sc-1"),
                id=workflow_id,
                task_queue="test-q",
            )


async def test_successful_run_records_its_status():
    _RECORDED.clear()
    result = await _execute(
        "seizu_scheduled_chat",
        [_load, _run, _record],
        workflow_id="chat-ok",
    )

    assert result["status"] == "success"
    assert result["thread_id"] == "t1"
    assert _RECORDED == [{"scheduled_chat_id": "sc-1", "status": "success", "error": None}]


async def test_skipped_run_is_not_recorded():
    """A skipped firing is not a run: it must not overwrite the last status."""
    _RECORDED.clear()
    result = await _execute(
        "seizu_scheduled_chat",
        [_load_skipped, _run, _record],
        workflow_id="chat-skipped",
    )

    assert result["status"] == "skipped"
    assert result["error"] == "disabled"
    assert _RECORDED == []


async def test_failed_run_records_failure_and_propagates():
    _RECORDED.clear()
    try:
        await _execute(
            "seizu_scheduled_chat",
            [_load, _run_fails, _record],
            workflow_id="chat-failed",
        )
    except Exception:
        pass
    else:  # pragma: no cover - the workflow must not swallow the failure
        raise AssertionError("expected the workflow to fail")

    assert len(_RECORDED) == 1
    assert _RECORDED[0]["scheduled_chat_id"] == "sc-1"
    assert _RECORDED[0]["status"] == "failure"
    assert "chat blew up" in str(_RECORDED[0]["error"])


async def test_watch_poll_skips_without_creating_a_run():
    _RECORDED.clear()
    result = await _execute(
        "seizu_scheduled_chat_watch_poll",
        [_watch_unchanged, _load, _run, _record],
        workflow_id="seizu-scheduled-chat-poll:sc-1-2026-07-27T09:00:00Z",
        classes=[ScheduledChatWatchPoll, ScheduledChatWorkflow],
    )

    assert result["status"] == "skipped"
    assert result["error"] == "watch scan unchanged"
    assert _RECORDED == []


async def test_watch_poll_starts_a_visible_run_when_triggered():
    _RECORDED.clear()
    result = await _execute(
        "seizu_scheduled_chat_watch_poll",
        [_watch_triggered, _load, _run, _record],
        workflow_id="seizu-scheduled-chat-poll:sc-1-2026-07-27T09:00:00Z",
        classes=[ScheduledChatWatchPoll, ScheduledChatWorkflow],
    )

    assert result["status"] == "success"
    assert _RECORDED == [{"scheduled_chat_id": "sc-1", "status": "success", "error": None}]
