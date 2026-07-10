"""Scheduled chats worker: ``python -m reporting.scheduled_chats``.

Polls the store for due scheduled chats and runs each as a headless agent
session owned by the schedule's creator (their RBAC permissions apply;
confirmations are bypassed only when they hold ``chat:bypass_permissions``).
Triggers are a structured schedule (hourly/daily/monthly, see
``ChatScheduleSpec``) or ``watch_scans`` against Cartography SyncMetadata
updates.
"""

import asyncio
import logging

from reporting import (
    settings,
    setup_logging,  # noqa:F401
)
from reporting.authnz.headless import HeadlessIdentityError, resolve_stored_user
from reporting.schema.chat import ScheduledChatItem
from reporting.schema.reporting_config import ScheduledQueryWatchScan
from reporting.services import headless_chat, report_store
from reporting.services.reporting_neo4j import check_watch_scan_triggered
from reporting.services.schedule_spec import run_requested, schedule_due
from reporting.worker_bootstrap import chat_worker_resources, install_shutdown_handlers

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    install_shutdown_handlers(_shutdown_event, logger)


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
    # A pending "run now" request runs even when the schedule is disabled, so
    # owners can test a schedule before enabling it.
    manual_run = run_requested(item.run_requested_at, item.last_scheduled_at)
    if manual_run:
        logger.info("Manual run requested", extra={"scheduled_chat_id": sc_id})
    elif not item.enabled:
        logger.debug("Skipping disabled scheduled chat", extra={"scheduled_chat_id": sc_id})
        return
    elif not await _is_triggered(item):
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
            origin="scheduled",
            scheduled_chat_id=sc_id,
        )
        logger.info(
            "Scheduled chat run finished",
            extra={
                "type": "AUDIT",
                "scheduled_chat_id": sc_id,
                "thread_id": result.thread_id,
                "user": current_user.user.user_id,
                "status": result.status,
                "budget": result.budget,
            },
        )
        status = {"completed": "success", "failed": "failure"}.get(result.status, result.status)
        if status == "failure":
            await report_store.record_scheduled_chat_result(
                sc_id,
                status,
                error=f"Headless run ended with status: {result.status}",
            )
        else:
            await report_store.record_scheduled_chat_result(sc_id, status)
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
    async with chat_worker_resources():
        while not _shutdown_event.is_set():
            logger.debug("Checking scheduled chats...")
            items = await report_store.list_scheduled_chats()
            for item in items:
                await run_scheduled_chat(item)
            if not _shutdown_event.is_set():
                await asyncio.sleep(settings.CHAT_SCHEDULES_POLL_SECONDS)


def main() -> None:
    if settings.CHAT_ENABLED and settings.CHAT_SCHEDULES_ENABLED:
        asyncio.run(_run_worker())
    else:
        logger.info("Scheduled chats are disabled (CHAT_ENABLED/CHAT_SCHEDULES_ENABLED); worker exiting")


if __name__ == "__main__":
    main()
