"""CRUD for scheduled chats: recurring headless agent runs owned by a user.

Registered only when CHAT_ENABLED and CHAT_SCHEDULES_ENABLED are both true.
Schedules are personal: every route operates on the requesting user's own
schedules, and the worker runs each schedule as its owner.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.chat import (
    ChatSessionsResponse,
    CreateScheduledChatRequest,
    ScheduledChatItem,
    ScheduledChatsResponse,
    ScheduledChatVersion,
    ScheduledChatVersionListResponse,
)
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


@router.get("/api/v1/chat/schedules/{sc_id}/sessions", response_model=ChatSessionsResponse)
async def list_scheduled_chat_sessions(
    sc_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ChatSessionsResponse:
    """List the run sessions a scheduled chat has produced, newest first."""
    await _owned_schedule(sc_id, current)
    sessions = await report_store.list_scheduled_chat_sessions(current.user.user_id, sc_id, limit)
    return ChatSessionsResponse(sessions=sessions)


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
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    logger.info(
        "Scheduled chat updated",
        extra={"type": "AUDIT", "scheduled_chat_id": sc_id, "user": current.user.user_id},
    )
    return item


@router.get("/api/v1/chat/schedules/{sc_id}/versions", response_model=ScheduledChatVersionListResponse)
async def list_scheduled_chat_versions(
    sc_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatVersionListResponse:
    """Return version history for one of the requesting user's scheduled chats."""
    await _owned_schedule(sc_id, current)
    versions = await report_store.list_scheduled_chat_versions(sc_id)
    return ScheduledChatVersionListResponse(versions=versions)


@router.get("/api/v1/chat/schedules/{sc_id}/versions/{version}", response_model=ScheduledChatVersion)
async def get_scheduled_chat_version(
    sc_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatVersion:
    """Return a specific version of one of the requesting user's scheduled chats."""
    await _owned_schedule(sc_id, current)
    item = await report_store.get_scheduled_chat_version(sc_id, version)
    if item is None:
        raise HTTPException(status_code=404, detail="Scheduled chat version not found")
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
