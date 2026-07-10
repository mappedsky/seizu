"""Due-logic for structured schedules (``ScheduleSpec``).

Shared by the scheduled-queries and scheduled-chats workers. A schedule is
evaluated against the last *claimed* run time (``last_scheduled_at``), so
lock acquisition marks a run as taken and the same occurrence never fires
twice.
"""

import calendar
from datetime import UTC, datetime, time, timedelta

from reporting.schema.reporting_config import ScheduleSpec


def _latest_daily_occurrence(spec: ScheduleSpec, now: datetime) -> datetime | None:
    """Most recent selected-weekday-at-hour:minute occurrence that is <= now (UTC)."""
    for offset in range(8):
        day = (now - timedelta(days=offset)).date()
        if day.weekday() not in spec.days_of_week:
            continue
        occurrence = datetime.combine(day, time(hour=spec.hour, minute=spec.minute), tzinfo=UTC)
        if occurrence <= now:
            return occurrence
    return None


def _latest_monthly_occurrence(spec: ScheduleSpec, now: datetime) -> datetime | None:
    """Most recent selected-day-of-month occurrence (at hour:minute UTC,
    default 00:00) that is <= now.

    A selected day a month doesn't have is clamped to that month's last day,
    so 31 runs on Apr 30, Feb 28/29, etc.
    """
    year, month = now.year, now.month
    for _ in range(3):
        last_day = calendar.monthrange(year, month)[1]
        effective_days = {min(day, last_day) for day in spec.days_of_month}
        candidates = [
            occurrence
            for day in effective_days
            if (occurrence := datetime(year, month, day, spec.hour, spec.minute, tzinfo=UTC)) <= now
        ]
        if candidates:
            return max(candidates)
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return None


def schedule_due(
    spec: ScheduleSpec,
    last_scheduled_at: str | None,
    created_at: str,
    now: datetime | None = None,
) -> bool:
    """Whether a schedule spec is due, given the last claimed run time.

    Interval/hourly schedules run immediately when never run. Daily/monthly
    schedules wait for the first selected occurrence after the schedule was
    created (creating one on Tuesday for "Mondays at 09:00" must not fire
    until next Monday).
    """
    now = now or datetime.now(tz=UTC)
    if spec.type in ("interval", "hourly"):
        if last_scheduled_at is None:
            return True
        last = datetime.fromisoformat(last_scheduled_at)
        if spec.type == "interval":
            interval = timedelta(minutes=spec.interval_minutes or 1)
        else:
            interval = timedelta(hours=spec.interval_hours or 1)
        return now >= last + interval
    if spec.type == "daily":
        occurrence = _latest_daily_occurrence(spec, now)
    else:
        occurrence = _latest_monthly_occurrence(spec, now)
    if occurrence is None:
        return False
    floor = datetime.fromisoformat(last_scheduled_at or created_at)
    return floor < occurrence


def run_requested(run_requested_at: str | None, last_scheduled_at: str | None) -> bool:
    """Whether a pending "run now" request should trigger a run.

    A request is pending until a run is claimed after it was made; the claim
    (lock acquisition) advances ``last_scheduled_at`` past it, so the request
    needs no explicit clearing.
    """
    if run_requested_at is None:
        return False
    if last_scheduled_at is None:
        return True
    return datetime.fromisoformat(run_requested_at) > datetime.fromisoformat(last_scheduled_at)
