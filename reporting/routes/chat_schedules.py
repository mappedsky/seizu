"""CRUD for scheduled chats: recurring headless agent runs owned by a user.

Registered only when CHAT_ENABLED and CHAT_SCHEDULES_ENABLED are both true.
Schedules are personal: every route operates on the requesting user's own
schedules, and the worker runs each schedule as its owner.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.chat import CreateScheduledChatRequest, ScheduledChatItem, ScheduledChatsResponse
from reporting.services import report_store

logger = logging.getLogger(__name__)

router = APIRouter()


async def _owned_schedule(sc_id: str, current: CurrentUser) -> ScheduledChatItem:
    item = await report_store.get_scheduled_chat(sc_id)
    if item is None or item.created_by != current.user.user_id:
        # Hide other users' schedules entirely (404, not 403).
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    return item


@router.get("/api/v1/chat/schedules", response_model=ScheduledChatsResponse)
async def list_scheduled_chats(
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatsResponse:
    """List the requesting user's scheduled chats."""
    schedules = await report_store.list_scheduled_chats(user_id=current.user.user_id)
    return ScheduledChatsResponse(schedules=schedules)


@router.post("/api/v1/chat/schedules", response_model=ScheduledChatItem, status_code=201)
async def create_scheduled_chat(
    body: CreateScheduledChatRequest,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatItem:
    """Create a scheduled chat owned by the requesting user."""
    item = await report_store.create_scheduled_chat(
        name=body.name,
        prompt=body.prompt,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
        created_by=current.user.user_id,
    )
    logger.info(
        "Scheduled chat created",
        extra={"type": "AUDIT", "scheduled_chat_id": item.scheduled_chat_id, "user": current.user.user_id},
    )
    return item


@router.get("/api/v1/chat/schedules/{sc_id}", response_model=ScheduledChatItem)
async def get_scheduled_chat(
    sc_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatItem:
    """Return one of the requesting user's scheduled chats."""
    return await _owned_schedule(sc_id, current)


@router.put("/api/v1/chat/schedules/{sc_id}", response_model=ScheduledChatItem)
async def update_scheduled_chat(
    sc_id: str,
    body: CreateScheduledChatRequest,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatItem:
    """Update one of the requesting user's scheduled chats."""
    await _owned_schedule(sc_id, current)
    item = await report_store.update_scheduled_chat(
        sc_id=sc_id,
        name=body.name,
        prompt=body.prompt,
        schedule=body.schedule.model_dump() if body.schedule else None,
        watch_scans=body.watch_scans,
        enabled=body.enabled,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    logger.info(
        "Scheduled chat updated",
        extra={"type": "AUDIT", "scheduled_chat_id": sc_id, "user": current.user.user_id},
    )
    return item


@router.delete("/api/v1/chat/schedules/{sc_id}", status_code=204)
async def delete_scheduled_chat(
    sc_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> None:
    """Delete one of the requesting user's scheduled chats."""
    await _owned_schedule(sc_id, current)
    await report_store.delete_scheduled_chat(sc_id)
    logger.info(
        "Scheduled chat deleted",
        extra={"type": "AUDIT", "scheduled_chat_id": sc_id, "user": current.user.user_id},
    )
