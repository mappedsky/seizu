"""Scheduled chats worker: ``python -m reporting.scheduled_chats``.

Polls the store for due scheduled chats and runs each as a headless agent
session owned by the schedule's creator (their RBAC permissions apply;
confirmations are bypassed only when they hold ``chat:bypass_permissions``).
Triggers are a structured schedule (hourly/daily/monthly, see
``ChatScheduleSpec``) or ``watch_scans`` against Cartography SyncMetadata
updates.
"""

import asyncio
import calendar
import logging
import signal
from datetime import UTC, datetime, time, timedelta
from typing import Any

from reporting import (
    settings,
    setup_logging,  # noqa:F401
)
from reporting.authnz.headless import HeadlessIdentityError, resolve_stored_user
from reporting.schema.chat import ChatScheduleSpec, ScheduledChatItem
from reporting.schema.reporting_config import ScheduledQueryWatchScan
from reporting.services import headless_chat, report_store
from reporting.services.chat_graph import close_chat_checkpoints, initialize_chat_checkpoints
from reporting.services.reporting_neo4j import check_watch_scan_triggered

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    def finalizer(sig: int, frame: Any) -> None:
        logger.info("SIGTERM caught, shutting down")
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, finalizer)


def _latest_daily_occurrence(spec: ChatScheduleSpec, now: datetime) -> datetime | None:
    """Most recent selected-weekday-at-hour occurrence that is <= now (UTC)."""
    for offset in range(8):
        day = (now - timedelta(days=offset)).date()
        if day.weekday() not in spec.days_of_week:
            continue
        occurrence = datetime.combine(day, time(hour=spec.hour), tzinfo=UTC)
        if occurrence <= now:
            return occurrence
    return None


def _latest_monthly_occurrence(spec: ChatScheduleSpec, now: datetime) -> datetime | None:
    """Most recent selected-day-of-month occurrence (00:00 UTC) that is <= now.

    A selected day a month doesn't have is clamped to that month's last day,
    so 31 runs on Apr 30, Feb 28/29, etc.
    """
    year, month = now.year, now.month
    for _ in range(3):
        last_day = calendar.monthrange(year, month)[1]
        effective_days = {min(day, last_day) for day in spec.days_of_month}
        candidates = [
            occurrence for day in effective_days if (occurrence := datetime(year, month, day, tzinfo=UTC)) <= now
        ]
        if candidates:
            return max(candidates)
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return None


def schedule_due(
    spec: ChatScheduleSpec,
    last_scheduled_at: str | None,
    created_at: str,
    now: datetime | None = None,
) -> bool:
    """Whether a schedule spec is due, given the last claimed run time.

    Hourly schedules run immediately when never run. Daily/monthly schedules
    wait for the first selected occurrence after the schedule was created
    (creating one on Tuesday for "Mondays at 09:00" must not fire until next
    Monday).
    """
    now = now or datetime.now(tz=UTC)
    if spec.type == "hourly":
        if last_scheduled_at is None:
            return True
        last = datetime.fromisoformat(last_scheduled_at)
        return now >= last + timedelta(hours=spec.interval_hours or 1)
    if spec.type == "daily":
        occurrence = _latest_daily_occurrence(spec, now)
    else:
        occurrence = _latest_monthly_occurrence(spec, now)
    if occurrence is None:
        return False
    floor = datetime.fromisoformat(last_scheduled_at or created_at)
    return floor < occurrence


async def _is_triggered(item: ScheduledChatItem) -> bool:
    if item.schedule and schedule_due(item.schedule, item.last_scheduled_at, item.created_at):
        logger.debug("Schedule trigger fired", extra={"scheduled_chat_id": item.scheduled_chat_id})
        return True
    if item.watch_scans:
        watch_scans = [ScheduledQueryWatchScan(**ws) for ws in item.watch_scans]
        if await check_watch_scan_triggered(item.last_scheduled_at, watch_scans):
            logger.debug("Watch scan trigger fired", extra={"scheduled_chat_id": item.scheduled_chat_id})
            return True
    return False


async def run_scheduled_chat(item: ScheduledChatItem) -> None:
    sc_id = item.scheduled_chat_id
    if not item.enabled:
        logger.debug("Skipping disabled scheduled chat", extra={"scheduled_chat_id": sc_id})
        return
    if not await _is_triggered(item):
        return
    if not await report_store.acquire_scheduled_chat_lock(sc_id, item.last_scheduled_at):
        logger.debug("Could not acquire lock for scheduled chat", extra={"scheduled_chat_id": sc_id})
        return
    try:
        current_user = await resolve_stored_user(item.created_by)
        result = await headless_chat.run_headless_chat(
            current_user,
            prompt=item.prompt,
            title=headless_chat.session_title(item.name),
            timeout_seconds=settings.CHAT_SCHEDULE_TIMEOUT_SECONDS,
        )
        logger.info(
            "Scheduled chat run finished",
            extra={
                "type": "AUDIT",
                "scheduled_chat_id": sc_id,
                "thread_id": result.thread_id,
                "user": current_user.user.user_id,
            },
        )
        await report_store.record_scheduled_chat_result(sc_id, "success")
    except HeadlessIdentityError as exc:
        logger.error("Skipping scheduled chat: %s", exc, extra={"scheduled_chat_id": sc_id})
        await _record_failure(sc_id, str(exc))
    except Exception as exc:
        logger.exception("Scheduled chat run failed", extra={"scheduled_chat_id": sc_id})
        await _record_failure(sc_id, str(exc))


async def _record_failure(sc_id: str, error: str) -> None:
    try:
        await report_store.record_scheduled_chat_result(sc_id, "failure", error=error)
    except Exception:
        logger.exception("Failed to record scheduled chat result", extra={"scheduled_chat_id": sc_id})


async def _run_worker() -> None:
    _bootstrap()
    should_init = settings.DYNAMODB_CREATE_TABLE or (settings.REPORT_STORE_BACKEND == "sqlmodel")
    if should_init:
        await report_store.initialize()
    await initialize_chat_checkpoints()
    try:
        while not _shutdown_event.is_set():
            logger.debug("Checking scheduled chats...")
            items = await report_store.list_scheduled_chats()
            for item in items:
                await run_scheduled_chat(item)
            if not _shutdown_event.is_set():
                await asyncio.sleep(settings.CHAT_SCHEDULES_POLL_SECONDS)
    finally:
        await close_chat_checkpoints()


def main() -> None:
    if settings.CHAT_ENABLED and settings.CHAT_SCHEDULES_ENABLED:
        asyncio.run(_run_worker())
    else:
        logger.info("Scheduled chats are disabled (CHAT_ENABLED/CHAT_SCHEDULES_ENABLED); worker exiting")


if __name__ == "__main__":
    main()
