"""Tests for the shared ScheduleSpec due-logic.

Covers the interval/minute additions used by workflows plus the
hourly/daily/monthly cases (including the monthly clamp-to-last-day rule)
that scheduled chats rely on.
"""

from datetime import datetime

import pytest

from reporting.schema.chat import ChatScheduleSpec
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


# ---------------------------------------------------------------------------
# hourly / daily / monthly (the granularity scheduled chats use)
# ---------------------------------------------------------------------------

_CHAT_CREATED = "2026-01-01T00:00:00+00:00"


def test_hourly_due_when_never_run():
    spec = ChatScheduleSpec(type="hourly", interval_hours=4)
    assert schedule_due(spec, None, _CHAT_CREATED, now=_now("2026-01-01T00:05:00+00:00")) is True


def test_hourly_respects_interval():
    spec = ChatScheduleSpec(type="hourly", interval_hours=4)
    last = "2026-01-02T10:00:00+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-02T13:59:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-02T14:00:00+00:00")) is True


def test_daily_runs_on_selected_day_at_hour():
    # 2026-01-05 is a Monday (weekday 0).
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    last = "2026-01-01T09:30:00+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-05T08:59:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-05T09:00:00+00:00")) is True


def test_daily_does_not_rerun_after_claim():
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    # Claimed at Monday 09:00:20; next due is the following Monday.
    last = "2026-01-05T09:00:20+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-05T15:00:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-12T09:00:00+00:00")) is True


def test_daily_new_schedule_waits_for_next_occurrence():
    # Created Tuesday; Mondays at 09:00 must not fire until next Monday.
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    created = "2026-01-06T12:00:00+00:00"  # Tuesday
    assert schedule_due(spec, None, created, now=_now("2026-01-07T12:00:00+00:00")) is False
    assert schedule_due(spec, None, created, now=_now("2026-01-12T09:00:00+00:00")) is True


def test_monthly_runs_on_selected_days():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[1, 15])
    last = "2026-01-01T00:00:10+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-14T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-01-15T00:00:00+00:00")) is True


def test_monthly_day_31_clamps_to_last_day_of_month():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[31])
    # April has 30 days; the run lands on the 30th.
    last = "2026-03-31T00:00:10+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-04-29T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-04-30T00:00:00+00:00")) is True


def test_monthly_day_29_clamps_in_february():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[29])
    # 2026 is not a leap year; February runs on the 28th.
    last = "2026-01-29T00:00:10+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-02-27T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-02-28T00:00:00+00:00")) is True


def test_monthly_clamped_day_runs_once_not_twice():
    # Days {30, 31} in a 30-day month collapse to a single occurrence on the 30th.
    spec = ChatScheduleSpec(type="monthly", days_of_month=[30, 31])
    last = "2026-04-30T00:00:10+00:00"
    assert schedule_due(spec, last, _CHAT_CREATED, now=_now("2026-04-30T23:59:00+00:00")) is False


def test_schedule_spec_validation():
    import pytest

    with pytest.raises(ValueError):
        ChatScheduleSpec(type="hourly")
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="daily", days_of_week=[])
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="daily", days_of_week=[7], hour=9)
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="monthly", days_of_month=[0])
    assert ChatScheduleSpec(type="monthly", days_of_month=[29, 31]).days_of_month == [29, 31]


def test_monthly_year_rollback():
    # January: day 31 hasn't happened yet → look back to Dec 31 of previous year.
    spec = ChatScheduleSpec(type="monthly", days_of_month=[31])
    # last run was Dec 31 — the Dec occurrence is already claimed, so not due.
    assert (
        schedule_due(spec, "2025-12-31T00:00:10+00:00", _CHAT_CREATED, now=_now("2026-01-15T00:00:00+00:00")) is False
    )
    # no prior run (None) → floor=created_at which is before Dec 31 → due.
    assert schedule_due(spec, None, "2025-12-01T00:00:00+00:00", now=_now("2026-01-15T00:00:00+00:00")) is True
