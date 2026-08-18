"""Temporal Schedule desired-state reconciliation, shared by schedule kinds.

Seizu has two independently-permissioned scheduling surfaces — configurable
workflows and scheduled chats — that look separate to users but need identical
trigger semantics: structured hourly/daily/monthly specs, watch-scan polling,
run-now, pause-on-disabled, and phase-stable intervals. This module owns those
semantics once; :class:`ScheduleKind` binds them to a record type.

Each kind adapts its stored record to :class:`ScheduleRecord` so kind-specific
fields (e.g. a workflow's legacy ``frequency``) stay in the adapter rather than
leaking into the reconciler.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
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
from reporting.schema.reporting_config import ScheduleSpec
from reporting.services import telemetry
from reporting.services.schedule_spec import run_requested, schedule_due

logger = logging.getLogger(__name__)

_client: Client | None = None
_client_lock = asyncio.Lock()


async def get_client() -> Client:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                telemetry.configure()
                _client = await Client.connect(
                    settings.TEMPORAL_ADDRESS,
                    namespace=settings.TEMPORAL_NAMESPACE,
                    # Carries trace context into the workflows started here, so a
                    # turn and the steps it fans out to share one trace (AGT-026).
                    interceptors=telemetry.temporal_interceptors(),
                )
    return _client


@dataclass(frozen=True)
class ScheduleRecord:
    """The normalized view of a stored schedule the reconciler operates on."""

    record_id: str
    enabled: bool
    schedule: ScheduleSpec | None
    created_at: str
    updated_at: str
    watch_scans: list[object] = field(default_factory=list)
    last_run_at: str | None = None
    last_scheduled_at: str | None = None
    run_requested_at: str | None = None

    @property
    def has_trigger(self) -> bool:
        return self.schedule is not None or bool(self.watch_scans)


@dataclass(frozen=True)
class ScheduleKind:
    """Binds the reconciler to one record type's storage and workflow types."""

    name: str
    schedule_id_prefix: str
    run_id_prefix: str
    watch_poll_run_id_prefix: str
    workflow_type: str
    watch_poll_workflow_type: str
    # Builds the workflow argument started by the Schedule action or run-now.
    build_invocation: Callable[..., object]
    list_records: Callable[[], Awaitable[list[ScheduleRecord]]]
    get_record: Callable[[str], Awaitable[ScheduleRecord | None]]
    set_sync_status: Callable[..., Awaitable[None]]
    # Consumes a pending run-now request by advancing last_scheduled_at.
    acquire_lock: Callable[[str, str | None], Awaitable[bool]]
    # Workflow runs serialize via a mutex child workflow, so their Schedule
    # allows overlap; chats have no cross-trigger serialization need and skip.
    overlap: ScheduleOverlapPolicy = ScheduleOverlapPolicy.ALLOW_ALL

    def schedule_id(self, record_id: str) -> str:
        return f"{self.schedule_id_prefix}{record_id}"


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
    # Temporal has no "clamp to the month's last day" rule, so any day >= 28
    # over-fires across 28-31 and the load activity drops the extra firings
    # with schedule_due().
    exact = [day for day in spec.days_of_month if day <= 27]
    candidates = sorted(set(exact + (list(range(28, 32)) if any(day >= 28 for day in spec.days_of_month) else [])))
    return [
        ScheduleCalendarSpec(
            day_of_month=[_range(day) for day in candidates],
            **common,
        )
    ]


def _interval_anchor(record: ScheduleRecord) -> datetime:
    """Return the stable phase anchor for interval-based schedules.

    The legacy scheduler measured intervals from the preceding run instead of
    fixed wall-clock boundaries. Re-anchoring on the recorded completion also
    prevents a restart from creating an immediate run followed by an
    epoch-aligned run a few minutes later.
    """
    value = record.last_run_at or record.updated_at or record.created_at
    anchor = datetime.fromisoformat(value)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=UTC)
    return anchor.astimezone(UTC)


def _interval_spec(record: ScheduleRecord, every: timedelta) -> TemporalScheduleSpec:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    offset = (_interval_anchor(record) - epoch) % every
    return TemporalScheduleSpec(intervals=[ScheduleIntervalSpec(every=every, offset=offset)])


def _temporal_spec(record: ScheduleRecord) -> TemporalScheduleSpec:
    if record.watch_scans:
        return _interval_spec(
            record,
            timedelta(seconds=settings.WORKFLOW_WATCH_POLL_SECONDS),
        )
    spec = record.schedule
    if spec is None:
        # Definitions without an automatic trigger retain a paused placeholder
        # schedule so run-now remains available and reconciliation is uniform.
        return TemporalScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(days=36500))])
    if spec.type == "interval":
        return _interval_spec(record, timedelta(minutes=spec.interval_minutes or 1))
    if spec.type == "hourly":
        return _interval_spec(record, timedelta(hours=spec.interval_hours or 1))
    return TemporalScheduleSpec(calendars=_calendar(spec), time_zone_name="UTC")


def _trigger_immediately(record: ScheduleRecord, now: datetime | None = None) -> bool:
    if not record.enabled:
        return False
    if record.watch_scans:
        return True
    spec = record.schedule
    if spec is None or spec.type not in ("interval", "hourly"):
        return False
    return schedule_due(spec, record.last_run_at, record.created_at, now=now)


def build_schedule(kind: ScheduleKind, record: ScheduleRecord) -> Schedule:
    is_watch = bool(record.watch_scans)
    return Schedule(
        action=ScheduleActionStartWorkflow(
            kind.watch_poll_workflow_type if is_watch else kind.workflow_type,
            kind.build_invocation(record.record_id, manual=False),
            id=(
                f"{kind.watch_poll_run_id_prefix}{record.record_id}"
                if is_watch
                else f"{kind.run_id_prefix}{record.record_id}"
            ),
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        ),
        spec=_temporal_spec(record),
        policy=SchedulePolicy(
            overlap=kind.overlap,
            catchup_window=timedelta(days=365),
            pause_on_failure=False,
        ),
        state=ScheduleState(
            paused=not record.enabled or not record.has_trigger,
            note="Managed by Seizu",
        ),
    )


async def reconcile(kind: ScheduleKind, record: ScheduleRecord) -> None:
    if not record.has_trigger:
        # Manual/trigger-only definitions have no Temporal Schedule. Remove a
        # previously configured schedule when the trigger is changed to manual.
        await delete_schedule(kind, record.record_id)
        await kind.set_sync_status(
            record.record_id,
            "synced",
            synced_at=datetime.now(tz=UTC).isoformat(),
        )
        return
    client = await get_client()
    handle = client.get_schedule_handle(kind.schedule_id(record.record_id))
    schedule = build_schedule(kind, record)
    try:
        await client.create_schedule(
            kind.schedule_id(record.record_id),
            schedule,
            trigger_immediately=_trigger_immediately(record),
        )
    except ScheduleAlreadyRunningError:
        await handle.update(lambda _input: ScheduleUpdate(schedule=schedule))
    await kind.set_sync_status(
        record.record_id,
        "synced",
        synced_at=datetime.now(tz=UTC).isoformat(),
    )


async def reconcile_by_id(kind: ScheduleKind, record_id: str) -> None:
    record = await kind.get_record(record_id)
    if record is None:
        return
    try:
        if run_requested(record.run_requested_at, record.last_scheduled_at):
            assert record.run_requested_at is not None
            await run_now(kind, record_id, request_key=record.run_requested_at)
            await kind.acquire_lock(record_id, record.last_scheduled_at)
        await reconcile(kind, record)
    except Exception as exc:
        logger.exception(
            "Schedule reconciliation failed",
            extra={"kind": kind.name, "record_id": record_id},
        )
        await kind.set_sync_status(record_id, "error", error=str(exc))


async def reconcile_all(kind: ScheduleKind) -> None:
    records = await kind.list_records()
    if not records:
        return
    try:
        await get_client()
    except Exception as exc:
        logger.exception(
            "Schedule reconciliation could not reach Temporal",
            extra={"kind": kind.name},
        )
        for record in records:
            await kind.set_sync_status(record.record_id, "error", error=str(exc))
        return
    for record in records:
        await reconcile_by_id(kind, record.record_id)


async def delete_schedule(kind: ScheduleKind, record_id: str) -> None:
    import temporalio.service

    try:
        client = await get_client()
        await client.get_schedule_handle(kind.schedule_id(record_id)).delete()
    except temporalio.service.RPCError as exc:
        if exc.status == temporalio.service.RPCStatusCode.NOT_FOUND:
            return
        raise


async def run_now(
    kind: ScheduleKind,
    record_id: str,
    *,
    request_key: str | None = None,
) -> tuple[str, str | None]:
    """Start a run outside the schedule, idempotently per ``request_key``."""
    client = await get_client()
    suffix = request_key or f"{datetime.now(tz=UTC).isoformat()}:{uuid4().hex}"
    temporal_workflow_id = f"{kind.run_id_prefix}{record_id}:manual:{suffix}"
    try:
        handle = await client.start_workflow(
            kind.workflow_type,
            kind.build_invocation(record_id, manual=True),
            id=temporal_workflow_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(temporal_workflow_id)
    return temporal_workflow_id, handle.result_run_id
