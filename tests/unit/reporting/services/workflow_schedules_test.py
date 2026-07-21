from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from temporalio.client import ScheduleAlreadyRunningError
from temporalio.exceptions import WorkflowAlreadyStartedError

from reporting.schema.report_config import ScheduledQueryItem
from reporting.services import workflow_schedules


def _item(**updates):
    values = {
        "scheduled_query_id": "workflow-1",
        "name": "Workflow",
        "cypher": "",
        "stages": [
            {
                "activities": [
                    {
                        "type": "query",
                        "output": "query",
                        "parameters": {"cypher": "RETURN 1"},
                    }
                ]
            }
        ],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "user-1",
    }
    values.update(updates)
    return ScheduledQueryItem.model_validate(values)


def test_build_interval_schedule_allows_global_overlap_limiter():
    schedule = workflow_schedules.build_schedule(_item(schedule={"type": "interval", "interval_minutes": 15}))

    assert schedule.spec.intervals[0].every.total_seconds() == 900
    assert schedule.policy.overlap.name == "ALLOW_ALL"
    assert schedule.state.paused is False


async def test_get_client_connects_once(mocker):
    workflow_schedules._client = None
    client = object()
    connect = mocker.patch.object(
        workflow_schedules.Client,
        "connect",
        new=mocker.AsyncMock(return_value=client),
    )
    try:
        assert await workflow_schedules.get_client() is client
        assert await workflow_schedules.get_client() is client
        connect.assert_awaited_once()
    finally:
        workflow_schedules._client = None


def test_interval_schedule_is_anchored_to_last_run():
    schedule = workflow_schedules.build_schedule(
        _item(
            schedule={"type": "hourly", "interval_hours": 12},
            last_run_at="2026-07-16T11:49:08+00:00",
        )
    )

    interval = schedule.spec.intervals[0]
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    expected = (datetime(2026, 7, 16, 11, 49, 8, tzinfo=UTC) - epoch) % timedelta(hours=12)
    assert interval.every == timedelta(hours=12)
    assert interval.offset == expected


def test_interval_schedule_without_run_is_anchored_to_definition_update():
    schedule = workflow_schedules.build_schedule(
        _item(
            schedule={"type": "interval", "interval_minutes": 15},
            updated_at="2026-01-01T00:07:00+00:00",
        )
    )

    assert schedule.spec.intervals[0].offset == timedelta(minutes=7)


def test_recent_interval_run_does_not_trigger_immediately():
    item = _item(
        schedule={"type": "hourly", "interval_hours": 12},
        last_run_at="2026-07-16T11:49:08+00:00",
    )

    assert workflow_schedules._trigger_immediately(item, now=datetime(2026, 7, 16, 12, tzinfo=UTC)) is False


def test_overdue_interval_run_triggers_immediately():
    item = _item(
        schedule={"type": "hourly", "interval_hours": 12},
        last_run_at="2026-07-16T11:49:08+00:00",
    )

    assert workflow_schedules._trigger_immediately(item, now=datetime(2026, 7, 17, tzinfo=UTC)) is True


def test_disabled_schedule_is_paused():
    schedule = workflow_schedules.build_schedule(
        _item(enabled=False, schedule={"type": "daily", "days_of_week": [0], "hour": 9})
    )

    assert schedule.state.paused is True


def test_watch_schedule_uses_poll_interval(mocker):
    mocker.patch.object(workflow_schedules.settings, "WORKFLOW_WATCH_POLL_SECONDS", 37)
    schedule = workflow_schedules.build_schedule(_item(watch_scans=[{"grouptype": "CVE"}]))

    assert schedule.spec.intervals[0].every.total_seconds() == 37
    assert schedule.action.workflow == "seizu_configured_workflow_watch_poll"
    assert schedule.action.id == "seizu-workflow-poll:workflow-1"


async def test_reconcile_all_marks_items_when_temporal_is_unavailable(mocker):
    item = _item(schedule={"type": "interval", "interval_minutes": 15})
    mocker.patch.object(
        workflow_schedules.report_store,
        "list_scheduled_queries",
        new=mocker.AsyncMock(return_value=[item]),
    )
    mocker.patch.object(
        workflow_schedules,
        "get_client",
        new=mocker.AsyncMock(side_effect=RuntimeError("offline")),
    )
    set_status = mocker.patch.object(
        workflow_schedules.report_store,
        "set_workflow_schedule_sync_status",
        new=mocker.AsyncMock(),
    )

    await workflow_schedules.reconcile_all()

    set_status.assert_awaited_once_with(
        "workflow-1",
        "error",
        error="offline",
    )


def test_calendar_and_placeholder_schedules():
    daily = workflow_schedules.build_schedule(
        _item(schedule={"type": "daily", "days_of_week": [0, 6], "hour": 9, "minute": 30})
    )
    assert [value.start for value in daily.spec.calendars[0].day_of_week] == [1, 0]
    monthly = workflow_schedules.build_schedule(
        _item(schedule={"type": "monthly", "days_of_month": [15, 31], "hour": 2})
    )
    assert {value.start for value in monthly.spec.calendars[0].day_of_month} == {15, 28, 29, 30, 31}
    placeholder = workflow_schedules.build_schedule(_item())
    assert placeholder.state.paused is True
    assert placeholder.spec.intervals[0].every == timedelta(days=36500)
    legacy = workflow_schedules.build_schedule(_item(frequency=10))
    assert legacy.spec.intervals[0].every == timedelta(minutes=10)


async def test_reconcile_creates_and_marks_synced(mocker):
    client = mocker.Mock()
    client.get_schedule_handle.return_value = mocker.Mock()
    client.create_schedule = mocker.AsyncMock()
    mocker.patch.object(workflow_schedules, "get_client", new=mocker.AsyncMock(return_value=client))
    status = mocker.patch.object(
        workflow_schedules.report_store,
        "set_workflow_schedule_sync_status",
        new=mocker.AsyncMock(),
    )
    await workflow_schedules.reconcile(_item(schedule={"type": "hourly", "interval_hours": 2}))
    assert client.create_schedule.await_args.kwargs["trigger_immediately"] is True
    assert status.await_args.args[:2] == ("workflow-1", "synced")


async def test_reconcile_updates_existing_schedule(mocker):
    handle = mocker.Mock(update=mocker.AsyncMock())
    client = mocker.Mock()
    client.get_schedule_handle.return_value = handle
    client.create_schedule = mocker.AsyncMock(side_effect=ScheduleAlreadyRunningError())
    mocker.patch.object(workflow_schedules, "get_client", new=mocker.AsyncMock(return_value=client))
    mocker.patch.object(
        workflow_schedules.report_store,
        "set_workflow_schedule_sync_status",
        new=mocker.AsyncMock(),
    )
    await workflow_schedules.reconcile(_item(schedule={"type": "hourly", "interval_hours": 2}))
    update = handle.update.await_args.args[0](None)
    assert update.schedule.spec.intervals[0].every == timedelta(hours=2)


async def test_reconcile_by_id_handles_missing_manual_and_error(mocker):
    get = mocker.patch.object(
        workflow_schedules.report_store,
        "get_scheduled_query",
        new=mocker.AsyncMock(return_value=None),
    )
    reconcile = mocker.patch.object(workflow_schedules, "reconcile", new=mocker.AsyncMock())
    await workflow_schedules.reconcile_by_id("missing")
    reconcile.assert_not_awaited()

    item = _item(run_requested_at="2026-01-02T00:00:00+00:00")
    get.return_value = item
    run = mocker.patch.object(workflow_schedules, "run_now", new=mocker.AsyncMock())
    lock = mocker.patch.object(
        workflow_schedules.report_store,
        "acquire_scheduled_query_lock",
        new=mocker.AsyncMock(),
    )
    await workflow_schedules.reconcile_by_id("workflow-1")
    run.assert_awaited_once_with("workflow-1", request_key=item.run_requested_at)
    lock.assert_awaited_once()

    reconcile.side_effect = RuntimeError("bad sync")
    status = mocker.patch.object(
        workflow_schedules.report_store,
        "set_workflow_schedule_sync_status",
        new=mocker.AsyncMock(),
    )
    await workflow_schedules.reconcile_by_id("workflow-1")
    status.assert_awaited_once_with("workflow-1", "error", error="bad sync")


async def test_reconcile_all_and_delete(mocker):
    item = _item()
    mocker.patch.object(
        workflow_schedules.report_store,
        "list_scheduled_queries",
        new=mocker.AsyncMock(return_value=[]),
    )
    get_client = mocker.patch.object(workflow_schedules, "get_client", new=mocker.AsyncMock())
    await workflow_schedules.reconcile_all()
    get_client.assert_not_awaited()

    workflow_schedules.report_store.list_scheduled_queries.return_value = [item]
    reconcile = mocker.patch.object(workflow_schedules, "reconcile_by_id", new=mocker.AsyncMock())
    await workflow_schedules.reconcile_all()
    reconcile.assert_awaited_once_with("workflow-1")

    handle = mocker.Mock(delete=mocker.AsyncMock())
    get_client.return_value = mocker.Mock(get_schedule_handle=mocker.Mock(return_value=handle))
    await workflow_schedules.delete_schedule("workflow-1")
    handle.delete.assert_awaited_once()
    get_client.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        await workflow_schedules.delete_schedule("workflow-1")


async def test_run_now_success_and_existing(mocker):
    handle = SimpleNamespace(result_run_id="run-1")
    client = mocker.Mock(start_workflow=mocker.AsyncMock(return_value=handle))
    mocker.patch.object(workflow_schedules, "get_client", new=mocker.AsyncMock(return_value=client))
    workflow_id, run_id = await workflow_schedules.run_now("workflow-1", request_key="request")
    assert workflow_id.endswith(":manual:request")
    assert run_id == "run-1"

    client.start_workflow.side_effect = WorkflowAlreadyStartedError(workflow_id, "seizu_configured_workflow")
    client.get_workflow_handle.return_value = handle
    assert await workflow_schedules.run_now("workflow-1", request_key="request") == (workflow_id, "run-1")
