from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from reporting.schema.chat import CreateScheduledChatRequest, ScheduledChatItem
from reporting.services import chat_schedules, schedule_reconciler

_NOW = "2026-01-01T00:00:00+00:00"


def _item(**updates) -> ScheduledChatItem:
    values = {
        "scheduled_chat_id": "sc-1",
        "name": "Daily digest",
        "prompt": "Summarize new findings",
        "schedule": {"type": "daily", "days_of_week": [0], "hour": 9},
        "created_at": _NOW,
        "updated_at": _NOW,
        "created_by": "user-1",
    }
    values.update(updates)
    return ScheduledChatItem.model_validate(values)


def _build(item: ScheduledChatItem):
    return schedule_reconciler.build_schedule(chat_schedules.CHAT_KIND, chat_schedules.to_record(item))


def test_schedule_targets_the_scheduled_chat_workflow():
    schedule = _build(_item())

    assert schedule.action.workflow == "seizu_scheduled_chat"
    assert schedule.action.id == "seizu-scheduled-chat:sc-1"
    assert schedule.action.args[0].scheduled_chat_id == "sc-1"
    assert schedule.action.args[0].manual is False
    assert chat_schedules.schedule_id("sc-1") == "seizu-chat-schedule:sc-1"


def test_overlapping_runs_skip_rather_than_queue():
    """Chats have no cross-trigger serialization need, unlike workflows."""
    assert _build(_item()).policy.overlap.name == "SKIP"


def test_watch_scans_use_the_poll_workflow():
    schedule = _build(_item(schedule=None, watch_scans=[{"grouptype": "GitHubRepository"}]))

    assert schedule.action.workflow == "seizu_scheduled_chat_watch_poll"
    assert schedule.action.id == "seizu-scheduled-chat-poll:sc-1"


def test_daily_spec_shifts_to_temporal_weekday_numbering():
    # Seizu/Python Monday=0; Temporal calendar Sunday=0.
    schedule = _build(_item(schedule={"type": "daily", "days_of_week": [0, 6], "hour": 9}))

    calendar = schedule.spec.calendars[0]
    assert {r.start for r in calendar.day_of_week} == {1, 0}
    assert calendar.hour[0].start == 9


def test_monthly_spec_overfires_late_days_for_the_clamp_rule():
    schedule = _build(_item(schedule={"type": "monthly", "days_of_month": [1, 31]}))

    days = {r.start for r in schedule.spec.calendars[0].day_of_month}
    assert days == {1, 28, 29, 30, 31}


def test_hourly_spec_is_anchored_to_the_last_run():
    schedule = _build(
        _item(
            schedule={"type": "hourly", "interval_hours": 12},
            last_run_at="2026-07-16T11:49:08+00:00",
        )
    )

    interval = schedule.spec.intervals[0]
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    assert interval.every == timedelta(hours=12)
    assert interval.offset == (datetime(2026, 7, 16, 11, 49, 8, tzinfo=UTC) - epoch) % timedelta(hours=12)


def test_disabled_schedule_is_paused_not_deleted():
    schedule = _build(_item(enabled=False))

    assert schedule.state.paused is True
    assert schedule.action.workflow == "seizu_scheduled_chat"


async def test_delete_removes_the_schedule_before_the_record(mocker):
    """If Temporal is down the record survives, so a retry is safe."""
    calls: list[str] = []
    mocker.patch.object(
        chat_schedules,
        "delete_schedule",
        new=mocker.AsyncMock(side_effect=lambda _id: calls.append("schedule")),
    )
    mocker.patch.object(
        chat_schedules.report_store,
        "delete_scheduled_chat",
        new=mocker.AsyncMock(side_effect=lambda _id: calls.append("record") or True),
    )

    await chat_schedules.delete_managed("sc-1")

    assert calls == ["schedule", "record"]


async def test_delete_raises_when_the_record_is_missing(mocker):
    mocker.patch.object(chat_schedules, "delete_schedule", new=mocker.AsyncMock())
    mocker.patch.object(
        chat_schedules.report_store,
        "delete_scheduled_chat",
        new=mocker.AsyncMock(return_value=False),
    )

    with pytest.raises(chat_schedules.ScheduledChatNotFoundError):
        await chat_schedules.delete_managed("sc-1")


async def test_create_reconciles_then_returns_the_refreshed_record(mocker):
    created = _item()
    refreshed = _item(schedule_sync_status="synced")
    mocker.patch.object(
        chat_schedules.report_store,
        "create_scheduled_chat",
        new=mocker.AsyncMock(return_value=created),
    )
    reconcile = mocker.patch.object(chat_schedules, "reconcile_by_id", new=mocker.AsyncMock())
    mocker.patch.object(
        chat_schedules.report_store,
        "get_scheduled_chat",
        new=mocker.AsyncMock(return_value=refreshed),
    )

    result = await chat_schedules.create_managed(
        CreateScheduledChatRequest(
            name="Daily digest",
            prompt="Summarize new findings",
            schedule={"type": "daily", "days_of_week": [0], "hour": 9},
        ),
        "user-1",
    )

    reconcile.assert_awaited_once_with("sc-1")
    assert result.schedule_sync_status == "synced"


async def test_run_now_starts_immediately_keyed_on_the_request(mocker):
    mocker.patch.object(
        chat_schedules.report_store,
        "request_scheduled_chat_run",
        new=mocker.AsyncMock(return_value="2026-01-02T00:00:00+00:00"),
    )
    run_now = mocker.patch.object(chat_schedules, "run_now", new=mocker.AsyncMock())

    requested_at = await chat_schedules.run_managed("sc-1")

    assert requested_at == "2026-01-02T00:00:00+00:00"
    # The request key makes recovery by a later reconcile pass idempotent.
    run_now.assert_awaited_once_with("sc-1", request_key="2026-01-02T00:00:00+00:00")


async def test_run_now_raises_for_a_missing_schedule(mocker):
    mocker.patch.object(
        chat_schedules.report_store,
        "request_scheduled_chat_run",
        new=mocker.AsyncMock(return_value=None),
    )
    run_now = mocker.patch.object(chat_schedules, "run_now", new=mocker.AsyncMock())

    with pytest.raises(chat_schedules.ScheduledChatNotFoundError):
        await chat_schedules.run_managed("sc-1")
    run_now.assert_not_called()


async def test_reconcile_consumes_a_pending_run_request(mocker):
    item = _item(run_requested_at="2026-01-02T00:00:00+00:00")
    mocker.patch.object(
        chat_schedules.report_store,
        "get_scheduled_chat",
        new=mocker.AsyncMock(return_value=item),
    )
    run_now = mocker.patch.object(schedule_reconciler, "run_now", new=mocker.AsyncMock())
    lock = mocker.patch.object(
        chat_schedules.report_store,
        "acquire_scheduled_chat_lock",
        new=mocker.AsyncMock(return_value=True),
    )
    mocker.patch.object(schedule_reconciler, "reconcile", new=mocker.AsyncMock())

    await chat_schedules.reconcile_by_id("sc-1")

    run_now.assert_awaited_once_with(chat_schedules.CHAT_KIND, "sc-1", request_key="2026-01-02T00:00:00+00:00")
    lock.assert_awaited_once()


async def test_reconcile_failure_is_recorded_on_the_record(mocker):
    mocker.patch.object(
        chat_schedules.report_store,
        "get_scheduled_chat",
        new=mocker.AsyncMock(return_value=_item()),
    )
    mocker.patch.object(
        schedule_reconciler,
        "reconcile",
        new=mocker.AsyncMock(side_effect=RuntimeError("offline")),
    )
    status = mocker.patch.object(
        chat_schedules.report_store,
        "set_chat_schedule_sync_status",
        new=mocker.AsyncMock(),
    )

    await chat_schedules.reconcile_by_id("sc-1")

    status.assert_awaited_once_with("sc-1", "error", error="offline")


async def test_reconcile_all_marks_every_record_when_temporal_is_unreachable(mocker):
    mocker.patch.object(
        chat_schedules.report_store,
        "list_scheduled_chats",
        new=mocker.AsyncMock(return_value=[_item()]),
    )
    mocker.patch.object(
        schedule_reconciler,
        "get_client",
        new=mocker.AsyncMock(side_effect=RuntimeError("offline")),
    )
    status = mocker.patch.object(
        chat_schedules.report_store,
        "set_chat_schedule_sync_status",
        new=mocker.AsyncMock(),
    )

    await chat_schedules.reconcile_all()

    status.assert_awaited_once_with("sc-1", "error", error="offline")


async def test_run_now_builds_a_manual_invocation(mocker):
    handle = SimpleNamespace(result_run_id="run-1")
    client = mocker.Mock(start_workflow=mocker.AsyncMock(return_value=handle))
    mocker.patch.object(schedule_reconciler, "get_client", new=mocker.AsyncMock(return_value=client))

    workflow_id, run_id = await chat_schedules.run_now("sc-1", request_key="key")

    assert workflow_id == "seizu-scheduled-chat:sc-1:manual:key"
    assert run_id == "run-1"
    assert client.start_workflow.await_args.args[0] == "seizu_scheduled_chat"
    invocation = client.start_workflow.await_args.args[1]
    assert invocation.scheduled_chat_id == "sc-1"
    assert invocation.manual is True
