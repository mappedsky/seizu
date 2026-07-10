"""Tests for the shared ScheduleSpec due-logic (interval + minute additions).

The hourly/daily/monthly cases shared with scheduled chats are covered in
``tests/unit/reporting/scheduled_chats_test.py``.
"""

from datetime import datetime

import pytest

from reporting.schema.reporting_config import ScheduleSpec
from reporting.services.schedule_spec import run_requested, schedule_due

_CREATED = "2025-12-01T00:00:00+00:00"


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_interval_due_when_never_run():
    spec = ScheduleSpec(type="interval", interval_minutes=5)
    assert schedule_due(spec, None, _CREATED, now=_now("2026-01-01T00:00:00+00:00")) is True


def test_interval_respects_minutes():
    spec = ScheduleSpec(type="interval", interval_minutes=15)
    last = "2026-01-02T12:00:00+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-02T12:14:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-02T12:15:00+00:00")) is True


def test_daily_honors_minute_of_hour():
    # 2026-01-05 is a Monday.
    spec = ScheduleSpec(type="daily", days_of_week=[0], hour=9, minute=30)
    last = "2026-01-04T00:00:00+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-05T09:29:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-05T09:30:00+00:00")) is True


def test_interval_requires_interval_minutes():
    with pytest.raises(ValueError, match="interval_minutes"):
        ScheduleSpec(type="interval")


def test_run_requested_pending():
    assert run_requested("2026-01-01T00:00:00+00:00", None) is True
    assert run_requested("2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00") is True


def test_run_requested_absent_or_claimed():
    assert run_requested(None, None) is False
    assert run_requested(None, "2026-01-01T00:00:00+00:00") is False
    # The claim (lock) advanced last_scheduled_at past the request.
    assert run_requested("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00") is False


def test_monthly_honors_hour_and_minute():
    spec = ScheduleSpec(type="monthly", days_of_month=[15], hour=6, minute=15)
    last = "2026-01-01T00:00:00+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-15T06:14:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-15T06:15:00+00:00")) is True


def test_yaml_scheduled_query_rejects_multiple_triggers():
    from seizu_schema.reporting_config import ScheduledQuery

    with pytest.raises(ValueError, match="mutually exclusive"):
        ScheduledQuery(
            name="x",
            cypher="RETURN 1",
            frequency=5,
            watch_scans=[{"grouptype": "CVE"}],
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        ScheduledQuery(
            name="x",
            cypher="RETURN 1",
            schedule={"type": "interval", "interval_minutes": 5},
            watch_scans=[{"grouptype": "CVE"}],
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        ScheduledQuery(
            name="x",
            cypher="RETURN 1",
            frequency=0,
            schedule={"type": "interval", "interval_minutes": 5},
        )
