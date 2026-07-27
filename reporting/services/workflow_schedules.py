"""Temporal Schedule reconciliation for configurable workflows.

The trigger semantics live in :mod:`reporting.services.schedule_reconciler`;
this module binds them to stored workflow definitions and keeps the
workflow-only surface (post-completion triggers, the legacy ``frequency``
field). The function names here are the ones callers already use.
"""

from __future__ import annotations

from datetime import datetime

from temporalio.client import Schedule, ScheduleOverlapPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from reporting import settings
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import ScheduleSpec
from reporting.services import report_store, schedule_reconciler
from reporting.services.schedule_reconciler import ScheduleKind, ScheduleRecord, get_client
from reporting.temporal_workflows.shared import ConfiguredWorkflowInvocation

SCHEDULE_ID_PREFIX = "seizu-workflow-schedule:"
RUN_ID_PREFIX = "seizu-workflow:"
WATCH_POLL_RUN_ID_PREFIX = "seizu-workflow-poll:"

__all__ = [
    "SCHEDULE_ID_PREFIX",
    "RUN_ID_PREFIX",
    "WATCH_POLL_RUN_ID_PREFIX",
    "WORKFLOW_KIND",
    "build_schedule",
    "delete_schedule",
    "get_client",
    "reconcile",
    "reconcile_all",
    "reconcile_by_id",
    "run_now",
    "run_triggered",
    "schedule_id",
    "to_record",
]


def to_record(item: ScheduledQueryItem) -> ScheduleRecord:
    """Adapt a stored definition, resolving the legacy ``frequency`` trigger."""
    spec = item.schedule
    if spec is None and item.frequency is not None:
        spec = ScheduleSpec(type="interval", interval_minutes=item.frequency)
    return ScheduleRecord(
        record_id=item.scheduled_query_id,
        enabled=item.enabled,
        schedule=spec,
        created_at=item.created_at,
        updated_at=item.updated_at,
        watch_scans=list(item.watch_scans),
        last_run_at=item.last_run_at,
        last_scheduled_at=item.last_scheduled_at,
        run_requested_at=item.run_requested_at,
    )


def _invocation(workflow_id: str, *, manual: bool) -> ConfiguredWorkflowInvocation:
    return ConfiguredWorkflowInvocation(workflow_id=workflow_id, manual=manual)


async def _list_records() -> list[ScheduleRecord]:
    return [to_record(item) for item in await report_store.list_scheduled_queries()]


async def _get_record(workflow_id: str) -> ScheduleRecord | None:
    item = await report_store.get_scheduled_query(workflow_id)
    return None if item is None else to_record(item)


# Wrappers rather than direct references so the store is resolved per call
# (the module-level ``report_store`` stays substitutable).
async def _set_sync_status(workflow_id: str, status: str, **kwargs: str | None) -> None:
    await report_store.set_workflow_schedule_sync_status(workflow_id, status, **kwargs)


async def _acquire_lock(workflow_id: str, expected_last_scheduled_at: str | None) -> bool:
    return await report_store.acquire_scheduled_query_lock(workflow_id, expected_last_scheduled_at)


WORKFLOW_KIND = ScheduleKind(
    name="workflow",
    schedule_id_prefix=SCHEDULE_ID_PREFIX,
    run_id_prefix=RUN_ID_PREFIX,
    watch_poll_run_id_prefix=WATCH_POLL_RUN_ID_PREFIX,
    workflow_type="seizu_configured_workflow",
    watch_poll_workflow_type="seizu_configured_workflow_watch_poll",
    build_invocation=_invocation,
    list_records=_list_records,
    get_record=_get_record,
    set_sync_status=_set_sync_status,
    acquire_lock=_acquire_lock,
    # Global overlap handling lives in ConfiguredWorkflow so manual, watch, and
    # scheduled triggers share one running/one waiting cap.
    overlap=ScheduleOverlapPolicy.ALLOW_ALL,
)


def schedule_id(workflow_id: str) -> str:
    return WORKFLOW_KIND.schedule_id(workflow_id)


def _trigger_immediately(item: ScheduledQueryItem, now: datetime | None = None) -> bool:
    """Whether reconciling this definition should fire a catch-up run now."""
    return schedule_reconciler._trigger_immediately(to_record(item), now=now)


def build_schedule(item: ScheduledQueryItem) -> Schedule:
    return schedule_reconciler.build_schedule(WORKFLOW_KIND, to_record(item))


async def reconcile(item: ScheduledQueryItem) -> None:
    await schedule_reconciler.reconcile(WORKFLOW_KIND, to_record(item))


async def reconcile_by_id(workflow_id: str) -> None:
    await schedule_reconciler.reconcile_by_id(WORKFLOW_KIND, workflow_id)


async def reconcile_all() -> None:
    await schedule_reconciler.reconcile_all(WORKFLOW_KIND)


async def delete_schedule(workflow_id: str) -> None:
    await schedule_reconciler.delete_schedule(WORKFLOW_KIND, workflow_id)


async def run_now(workflow_id: str, *, request_key: str | None = None) -> tuple[str, str | None]:
    return await schedule_reconciler.run_now(WORKFLOW_KIND, workflow_id, request_key=request_key)


async def run_triggered(
    workflow_id: str,
    *,
    source_workflow_id: str,
    source_run_id: str,
    lineage: list[str],
) -> tuple[str, str | None]:
    """Start an independent configured workflow after another completes."""

    client = await get_client()
    temporal_workflow_id = f"{RUN_ID_PREFIX}{workflow_id}:trigger:{source_workflow_id}:{source_run_id}"
    try:
        handle = await client.start_workflow(
            "seizu_configured_workflow",
            ConfiguredWorkflowInvocation(
                workflow_id=workflow_id,
                manual=True,
                trigger_lineage=lineage,
            ),
            id=temporal_workflow_id,
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(temporal_workflow_id)
    return temporal_workflow_id, handle.result_run_id
