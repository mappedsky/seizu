"""Temporal Schedule desired-state reconciliation for configurable workflows."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleState,
    ScheduleUpdate,
)
from temporalio.client import (
    ScheduleSpec as TemporalScheduleSpec,
)
from temporalio.exceptions import WorkflowAlreadyStartedError

from reporting import settings
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import ScheduleSpec
from reporting.services import report_store
from reporting.services.schedule_spec import run_requested, schedule_due
from reporting.temporal_workflows.shared import ConfiguredWorkflowInvocation

logger = logging.getLogger(__name__)

SCHEDULE_ID_PREFIX = "seizu-workflow-schedule:"
RUN_ID_PREFIX = "seizu-workflow:"
WATCH_POLL_RUN_ID_PREFIX = "seizu-workflow-poll:"

_client: Client | None = None
_client_lock = asyncio.Lock()


async def get_client() -> Client:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = await Client.connect(
                    settings.TEMPORAL_ADDRESS,
                    namespace=settings.TEMPORAL_NAMESPACE,
                )
    return _client


def schedule_id(workflow_id: str) -> str:
    return f"{SCHEDULE_ID_PREFIX}{workflow_id}"


def _range(value: int) -> ScheduleRange:
    return ScheduleRange(start=value, end=value)


def _calendar(spec: ScheduleSpec) -> list[ScheduleCalendarSpec]:
    common = {"hour": [_range(spec.hour)], "minute": [_range(spec.minute)]}
    if spec.type == "daily":
        # Temporal calendar Sunday=0; Seizu/Python Monday=0.
        return [
            ScheduleCalendarSpec(
                day_of_week=[_range((day + 1) % 7) for day in spec.days_of_week],
                **common,
            )
        ]
    exact = [day for day in spec.days_of_month if day <= 27]
    candidates = sorted(set(exact + (list(range(28, 32)) if any(day >= 28 for day in spec.days_of_month) else [])))
    return [
        ScheduleCalendarSpec(
            day_of_month=[_range(day) for day in candidates],
            **common,
        )
    ]


def _interval_anchor(item: ScheduledQueryItem) -> datetime:
    """Return the stable phase anchor for interval-based schedules.

    The legacy scheduler measured intervals from the preceding run instead of
    fixed wall-clock boundaries. Re-anchoring on the recorded completion also
    prevents a restart from creating an immediate run followed by an
    epoch-aligned run a few minutes later.
    """
    value = item.last_run_at or item.updated_at or item.created_at
    anchor = datetime.fromisoformat(value)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return anchor.astimezone(UTC)


def _interval_spec(item: ScheduledQueryItem, every: timedelta) -> TemporalScheduleSpec:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    offset = (_interval_anchor(item) - epoch) % every
    return TemporalScheduleSpec(intervals=[ScheduleIntervalSpec(every=every, offset=offset)])


def _temporal_spec(item: ScheduledQueryItem) -> TemporalScheduleSpec:
    if item.watch_scans:
        return _interval_spec(
            item,
            timedelta(seconds=settings.WORKFLOW_WATCH_POLL_SECONDS),
        )
    spec = item.schedule
    if spec is None and item.frequency is not None:
        spec = ScheduleSpec(type="interval", interval_minutes=item.frequency)
    if spec is None:
        # Definitions without an automatic trigger retain a paused placeholder
        # schedule so run-now remains available and reconciliation is uniform.
        return TemporalScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=36500))])
    if spec.type == "interval":
        return _interval_spec(item, timedelta(minutes=spec.interval_minutes or 1))
    if spec.type == "hourly":
        return _interval_spec(item, timedelta(hours=spec.interval_hours or 1))
    return TemporalScheduleSpec(calendars=_calendar(spec), time_zone_name="UTC")


def _trigger_immediately(item: ScheduledQueryItem, now: datetime | None = None) -> bool:
    if not item.enabled:
        return False
    if item.watch_scans:
        return True
    spec = item.schedule
    if spec is None and item.frequency is not None:
        spec = ScheduleSpec(type="interval", interval_minutes=item.frequency)
    if spec is None or spec.type not in ("interval", "hourly"):
        return False
    return schedule_due(spec, item.last_run_at, item.created_at, now=now)


def build_schedule(item: ScheduledQueryItem) -> Schedule:
    is_watch = bool(item.watch_scans)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            "seizu_configured_workflow_watch_poll" if is_watch else "seizu_configured_workflow",
            ConfiguredWorkflowInvocation(workflow_id=item.scheduled_query_id),
            id=(
                f"{WATCH_POLL_RUN_ID_PREFIX}{item.scheduled_query_id}"
                if is_watch
                else f"{RUN_ID_PREFIX}{item.scheduled_query_id}"
            ),
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        ),
        spec=_temporal_spec(item),
        policy=SchedulePolicy(
            # Global overlap handling lives in ConfiguredWorkflow so manual,
            # watch, and scheduled triggers share one running/one waiting cap.
            overlap=ScheduleOverlapPolicy.ALLOW_ALL,
            catchup_window=timedelta(days=365),
            pause_on_failure=False,
        ),
        state=ScheduleState(
            paused=not item.enabled or (item.schedule is None and not item.watch_scans and item.frequency is None),
            note="Managed by Seizu",
        ),
    )


async def reconcile(item: ScheduledQueryItem) -> None:
    client = await get_client()
    handle = client.get_schedule_handle(schedule_id(item.scheduled_query_id))
    schedule = build_schedule(item)
    try:
        await client.create_schedule(
            schedule_id(item.scheduled_query_id),
            schedule,
            trigger_immediately=_trigger_immediately(item),
        )
    except ScheduleAlreadyRunningError:
        await handle.update(lambda _input: ScheduleUpdate(schedule=schedule))
    synced_at = datetime.now(tz=UTC).isoformat()
    await report_store.set_workflow_schedule_sync_status(
        item.scheduled_query_id,
        "synced",
        synced_at=synced_at,
    )


async def reconcile_by_id(workflow_id: str) -> None:
    item = await report_store.get_scheduled_query(workflow_id)
    if item is None:
        return
    try:
        if run_requested(item.run_requested_at, item.last_scheduled_at):
            assert item.run_requested_at is not None
            await run_now(workflow_id, request_key=item.run_requested_at)
            await report_store.acquire_scheduled_query_lock(
                workflow_id,
                item.last_scheduled_at,
            )
        await reconcile(item)
    except Exception as exc:
        logger.exception("Workflow schedule reconciliation failed", extra={"workflow_id": workflow_id})
        await report_store.set_workflow_schedule_sync_status(
            workflow_id,
            "error",
            error=str(exc),
        )


async def reconcile_all() -> None:
    items = await report_store.list_scheduled_queries()
    if not items:
        return
    try:
        await get_client()
    except Exception as exc:
        logger.exception("Workflow Schedule reconciliation could not reach Temporal")
        for item in items:
            await report_store.set_workflow_schedule_sync_status(
                item.scheduled_query_id,
                "error",
                error=str(exc),
            )
        return
    for item in items:
        await reconcile_by_id(item.scheduled_query_id)


async def delete_schedule(workflow_id: str) -> None:
    import temporalio.service

    try:
        client = await get_client()
        await client.get_schedule_handle(schedule_id(workflow_id)).delete()
    except temporalio.service.RPCError as exc:
        if exc.status == temporalio.service.RPCStatusCode.NOT_FOUND:
            return
        raise


async def run_now(
    workflow_id: str,
    *,
    request_key: str | None = None,
) -> tuple[str, str | None]:
    client = await get_client()
    suffix = request_key or f"{datetime.now(tz=UTC).isoformat()}:{uuid4().hex}"
    temporal_workflow_id = f"{RUN_ID_PREFIX}{workflow_id}:manual:{suffix}"
    try:
        handle = await client.start_workflow(
            "seizu_configured_workflow",
            ConfiguredWorkflowInvocation(workflow_id=workflow_id, manual=True),
            id=temporal_workflow_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(temporal_workflow_id)
    return temporal_workflow_id, handle.result_run_id
