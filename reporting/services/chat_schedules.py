"""Temporal Schedule reconciliation and managed CRUD for scheduled chats.

Scheduled chats are a separate product surface from configurable workflows —
own store records, routes, permissions, and UI — but the trigger semantics are
identical, so this module binds them to the shared reconciler in
:mod:`reporting.services.schedule_reconciler` rather than reimplementing them.

Ownership is enforced by the route layer (schedules are personal); this module
assumes the caller already resolved authorization.
"""

from __future__ import annotations

from temporalio.client import ScheduleOverlapPolicy

from reporting.schema.chat import CreateScheduledChatRequest, ScheduledChatItem
from reporting.services import report_store, schedule_reconciler
from reporting.services.schedule_reconciler import ScheduleKind, ScheduleRecord
from reporting.temporal_workflows.shared import ScheduledChatInvocation

SCHEDULE_ID_PREFIX = "seizu-chat-schedule:"
RUN_ID_PREFIX = "seizu-scheduled-chat:"
WATCH_POLL_RUN_ID_PREFIX = "seizu-scheduled-chat-poll:"


class ScheduledChatNotFoundError(LookupError):
    """The scheduled chat does not exist."""


def to_record(item: ScheduledChatItem) -> ScheduleRecord:
    return ScheduleRecord(
        record_id=item.scheduled_chat_id,
        enabled=item.enabled,
        schedule=item.schedule,
        created_at=item.created_at,
        updated_at=item.updated_at,
        watch_scans=list(item.watch_scans),
        last_run_at=item.last_run_at,
        last_scheduled_at=item.last_scheduled_at,
        run_requested_at=item.run_requested_at,
    )


def _invocation(sc_id: str, *, manual: bool) -> ScheduledChatInvocation:
    return ScheduledChatInvocation(scheduled_chat_id=sc_id, manual=manual)


async def _list_records() -> list[ScheduleRecord]:
    return [to_record(item) for item in await report_store.list_scheduled_chats()]


async def _get_record(sc_id: str) -> ScheduleRecord | None:
    item = await report_store.get_scheduled_chat(sc_id)
    return None if item is None else to_record(item)


# Wrappers rather than direct references so the store is resolved per call.
async def _set_sync_status(sc_id: str, status: str, **kwargs: str | None) -> None:
    await report_store.set_chat_schedule_sync_status(sc_id, status, **kwargs)


async def _acquire_lock(sc_id: str, expected_last_scheduled_at: str | None) -> bool:
    return await report_store.acquire_scheduled_chat_lock(sc_id, expected_last_scheduled_at)


CHAT_KIND = ScheduleKind(
    name="scheduled_chat",
    schedule_id_prefix=SCHEDULE_ID_PREFIX,
    run_id_prefix=RUN_ID_PREFIX,
    watch_poll_run_id_prefix=WATCH_POLL_RUN_ID_PREFIX,
    workflow_type="seizu_scheduled_chat",
    watch_poll_workflow_type="seizu_scheduled_chat_watch_poll",
    build_invocation=_invocation,
    list_records=_list_records,
    get_record=_get_record,
    set_sync_status=_set_sync_status,
    acquire_lock=_acquire_lock,
    # Unlike workflows, a chat schedule has no cross-trigger serialization
    # requirement, so a still-running run simply skips the next firing rather
    # than queueing behind a mutex.
    overlap=ScheduleOverlapPolicy.SKIP,
)


def schedule_id(sc_id: str) -> str:
    return CHAT_KIND.schedule_id(sc_id)


async def reconcile_by_id(sc_id: str) -> None:
    await schedule_reconciler.reconcile_by_id(CHAT_KIND, sc_id)


async def reconcile_all() -> None:
    await schedule_reconciler.reconcile_all(CHAT_KIND)


async def delete_schedule(sc_id: str) -> None:
    await schedule_reconciler.delete_schedule(CHAT_KIND, sc_id)


async def run_now(sc_id: str, *, request_key: str | None = None) -> tuple[str, str | None]:
    return await schedule_reconciler.run_now(CHAT_KIND, sc_id, request_key=request_key)


async def create_managed(body: CreateScheduledChatRequest, owner_user_id: str) -> ScheduledChatItem:
    created = await report_store.create_scheduled_chat(
        name=body.name,
        prompt=body.prompt,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        created_by=owner_user_id,
    )
    await reconcile_by_id(created.scheduled_chat_id)
    refreshed = await report_store.get_scheduled_chat(created.scheduled_chat_id)
    return refreshed if refreshed is not None else created


async def update_managed(
    sc_id: str,
    body: CreateScheduledChatRequest,
    updated_by: str,
) -> ScheduledChatItem:
    updated = await report_store.update_scheduled_chat(
        sc_id=sc_id,
        name=body.name,
        prompt=body.prompt,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        updated_by=updated_by,
        comment=body.comment,
    )
    if updated is None:
        raise ScheduledChatNotFoundError("Scheduled chat not found")
    await reconcile_by_id(sc_id)
    refreshed = await report_store.get_scheduled_chat(sc_id)
    return refreshed if refreshed is not None else updated


async def delete_managed(sc_id: str) -> None:
    # Delete the external trigger first. If Temporal is unavailable the stored
    # schedule remains, making retry safe and preventing an orphan schedule.
    await delete_schedule(sc_id)
    if not await report_store.delete_scheduled_chat(sc_id):
        raise ScheduledChatNotFoundError("Scheduled chat not found")


async def run_managed(sc_id: str) -> str:
    """Record the run request, start the run, and return ``run_requested_at``.

    The request is persisted first so it is auditable and so a reconcile pass
    recovers it if the start below fails. Starting is idempotent per request
    key, so the recovery path cannot produce a second run.
    """
    requested_at = await report_store.request_scheduled_chat_run(sc_id)
    if requested_at is None:
        raise ScheduledChatNotFoundError("Scheduled chat not found")
    await run_now(sc_id, request_key=requested_at)
    return requested_at
