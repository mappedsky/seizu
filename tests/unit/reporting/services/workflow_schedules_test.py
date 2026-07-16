from datetime import UTC, datetime, timedelta

from reporting.schema.report_config import ScheduledQueryItem
from reporting.services import workflow_schedules


def _item(**updates):
    values = {
        "scheduled_query_id": "workflow-1",
        "name": "Workflow",
        "cypher": "",
        "inputs": {"query": {"type": "query", "cypher": "RETURN 1"}},
        "activities": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "user-1",
    }
    values.update(updates)
    return ScheduledQueryItem.model_validate(values)


def test_build_interval_schedule_uses_buffer_one():
    schedule = workflow_schedules.build_schedule(_item(schedule={"type": "interval", "interval_minutes": 15}))

    assert schedule.spec.intervals[0].every.total_seconds() == 900
    assert schedule.policy.overlap.name == "BUFFER_ONE"
    assert schedule.state.paused is False


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
