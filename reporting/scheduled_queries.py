import asyncio
import inspect
import logging
from datetime import datetime, timedelta
from typing import Any

from reporting import (
    scheduled_query_modules,
    settings,
    setup_logging,  # noqa:F401
)
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import (
    ScheduledQuery,
    ScheduledQueryAction,
    ScheduledQueryParam,
    ScheduledQueryWatchScan,
)
from reporting.services import report_store
from reporting.services.reporting_neo4j import check_watch_scan_triggered, run_query_with_retry
from reporting.worker_bootstrap import initialize_report_store, install_shutdown_handlers

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event = asyncio.Event()


def _bootstrap() -> None:
    install_shutdown_handlers(_shutdown_event, logger)


def _item_to_scheduled_query(item: ScheduledQueryItem) -> ScheduledQuery:
    """Convert a DB ScheduledQueryItem into a ScheduledQuery model for the worker."""
    return ScheduledQuery(
        name=item.name,
        cypher=item.cypher,
        params=[ScheduledQueryParam(**p) for p in item.params],
        frequency=item.frequency,
        watch_scans=[ScheduledQueryWatchScan(**ws) for ws in item.watch_scans],
        enabled=item.enabled,
        actions=[ScheduledQueryAction(**a) for a in item.actions],
    )


def _frequency_triggered(last_scheduled_at: str | None, frequency: int) -> bool:
    if last_scheduled_at is None:
        return True
    last = datetime.fromisoformat(last_scheduled_at)
    return datetime.now(tz=last.tzinfo) > last + timedelta(minutes=frequency)


async def _is_triggered(item: ScheduledQueryItem, sq: ScheduledQuery) -> bool:
    """Return True if any trigger condition is met for this scheduled query."""
    if sq.frequency and _frequency_triggered(item.last_scheduled_at, sq.frequency):
        logger.debug(
            "Frequency trigger fired",
            extra={"scheduled_query_id": item.scheduled_query_id},
        )
        return True
    if sq.watch_scans and await check_watch_scan_triggered(item.last_scheduled_at, sq.watch_scans):
        logger.debug(
            "Watch scan trigger fired",
            extra={"scheduled_query_id": item.scheduled_query_id},
        )
        return True
    return False


async def schedule_query(item: ScheduledQueryItem) -> None:
    sq = _item_to_scheduled_query(item)
    sq_id = item.scheduled_query_id
    if not sq.enabled:
        logger.debug("Skipping disabled query", extra={"scheduled_query_id": sq_id})
        return
    logger.debug("Checking query", extra={"scheduled_query_id": sq_id})
    if not await _is_triggered(item, sq):
        logger.debug("No trigger for query", extra={"scheduled_query_id": sq_id})
        return
    if not await report_store.acquire_scheduled_query_lock(sq_id, item.last_scheduled_at):
        logger.debug("Could not acquire lock for query", extra={"scheduled_query_id": sq_id})
        return
    logger.debug("Got lock for query", extra={"scheduled_query_id": sq_id})
    try:
        query_str = sq.cypher
        params = {d.name: d.value for d in sq.params}
        results = await run_query_with_retry(query_str, params)
        for action in sq.actions:
            await _handle_results(sq_id, action, results)
        try:
            await report_store.record_scheduled_query_result(sq_id, "success")
        except Exception:
            logger.exception(
                "Failed to record query result",
                extra={"scheduled_query_id": sq_id},
            )
    except Exception as exc:
        logger.exception(
            "Failed to run actions for query",
            extra={"scheduled_query_id": sq_id},
        )
        try:
            await report_store.record_scheduled_query_result(sq_id, "failure", error=str(exc))
        except Exception:
            logger.exception(
                "Failed to record query result",
                extra={"scheduled_query_id": sq_id},
            )


async def _handle_results(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    action_type = action.action_type
    if not action_type:
        logger.error(
            "Skipping misconfigured scheduled query",
            extra={
                "scheduled_query_id": scheduled_query_id,
                "misconfiguration": "missing action_type",
            },
        )
        return
    if action_type not in scheduled_query_modules.get_module_names():
        logger.error(
            "Skipping results for scheduled query that is using an action that is not"
            " configured in SCHEDULED_QUERY_MODULES",
            extra={
                "scheduled_query_id": scheduled_query_id,
                "action": action_type,
            },
        )
        return
    module = scheduled_query_modules.get_module(action_type)
    if inspect.iscoroutinefunction(module.handle_results):
        await module.handle_results(scheduled_query_id, action, results)
    else:
        await asyncio.to_thread(module.handle_results, scheduled_query_id, action, results)


async def _schedule_queries() -> None:
    _bootstrap()
    await initialize_report_store()
    await scheduled_query_modules.load_modules()
    while not _shutdown_event.is_set():
        logger.debug("Checking queries to schedule...")
        sq_items = await report_store.list_scheduled_queries()
        for item in sq_items:
            await schedule_query(item)
        if not _shutdown_event.is_set():
            await asyncio.sleep(settings.SCHEDULED_QUERY_FREQUENCY)


def main() -> None:
    if settings.ENABLE_SCHEDULED_QUERIES:
        asyncio.run(_schedule_queries())


if __name__ == "__main__":
    main()
