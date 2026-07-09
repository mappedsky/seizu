"""CRUD for scheduled chats: recurring headless agent runs owned by a user.

Registered only when CHAT_ENABLED and CHAT_SCHEDULES_ENABLED are both true.
Schedules are personal: every route operates on the requesting user's own
schedules, and the worker runs each schedule as its owner.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from reporting import settings
from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.chat import (
    ChatHistoryResponse,
    ChatSessionsResponse,
    CreateScheduledChatRequest,
    ScheduledChatItem,
    ScheduledChatRunRequestedResponse,
    ScheduledChatsResponse,
    ScheduledChatVersion,
    ScheduledChatVersionListResponse,
)
from reporting.services import report_store
from reporting.services.chat_graph import load_thread_messages

logger = logging.getLogger(__name__)

router = APIRouter()


def _can_read_all(current: CurrentUser) -> bool:
    return Permission.CHAT_SCHEDULE_READ_ALL.value in current.permissions


async def _owned_schedule(sc_id: str, current: CurrentUser, *, readonly: bool = False) -> ScheduledChatItem:
    """Resolve a schedule the user may act on.

    Owners can always access their own schedules. Read paths
    (``readonly=True``) additionally allow holders of
    ``chat:schedule:read_all``; mutations stay owner-only. Other users'
    schedules are hidden entirely (404, not 403).
    """
    item = await report_store.get_scheduled_chat(sc_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    if item.created_by != current.user.user_id and not (readonly and _can_read_all(current)):
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    return item


@router.get("/api/v1/chat/schedules", response_model=ScheduledChatsResponse)
async def list_scheduled_chats(
    all: bool = Query(default=False, description="List every user's schedules (requires chat:schedule:read_all)."),
    user_id: str | None = Query(default=None, description="Filter to one owner (requires chat:schedule:read_all)."),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatsResponse:
    """List scheduled chats.

    Defaults to the requesting user's own schedules. With
    ``chat:schedule:read_all``, ``all=true`` lists every user's schedules and
    ``user_id`` filters to a specific owner.
    """
    if (all or (user_id is not None and user_id != current.user.user_id)) and not _can_read_all(current):
        raise HTTPException(
            status_code=403,
            detail=f"Missing permissions: {Permission.CHAT_SCHEDULE_READ_ALL.value}",
        )
    if user_id is not None:
        owner: str | None = user_id
    elif all:
        owner = None
    else:
        owner = current.user.user_id
    schedules = await report_store.list_scheduled_chats(user_id=owner)
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
    """Return a scheduled chat (own, or any with chat:schedule:read_all)."""
    return await _owned_schedule(sc_id, current, readonly=True)


@router.get("/api/v1/chat/schedules/{sc_id}/sessions", response_model=ChatSessionsResponse)
async def list_scheduled_chat_sessions(
    sc_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ChatSessionsResponse:
    """List the run sessions a scheduled chat has produced, newest first."""
    item = await _owned_schedule(sc_id, current, readonly=True)
    # Run sessions live under the schedule owner's session partition.
    sessions = await report_store.list_scheduled_chat_sessions(item.created_by, sc_id, limit)
    return ChatSessionsResponse(sessions=sessions)


@router.get(
    "/api/v1/chat/schedules/{sc_id}/sessions/{thread_id}/history",
    response_model=ChatHistoryResponse,
)
async def get_scheduled_chat_session_history(
    sc_id: str,
    thread_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ChatHistoryResponse:
    """Return the read-only transcript of one of a schedule's run sessions.

    Unlike ``GET /api/v1/chat/history`` (which reads the caller's own
    threads), this resolves the thread under the schedule owner's namespace so
    holders of ``chat:schedule:read_all`` can review other users' runs.
    """
    # Shares the message mapping with the interactive history endpoint.
    from reporting.routes.chat import _to_history_message

    item = await _owned_schedule(sc_id, current, readonly=True)
    session = await report_store.get_chat_session(item.created_by, thread_id)
    if session is None or session.scheduled_chat_id != sc_id:
        raise HTTPException(status_code=404, detail="Run session not found")
    owner_user = await report_store.get_user(item.created_by)
    if owner_user is None:
        raise HTTPException(status_code=404, detail="Schedule owner not found")
    owner = CurrentUser(user=owner_user, jwt_claims={}, permissions=frozenset())
    messages = await load_thread_messages(owner, thread_id, limit=settings.CHAT_HISTORY_LIMIT)
    return ChatHistoryResponse(
        messages=[message for index, raw in enumerate(messages) if (message := _to_history_message(raw, index))]
    )


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


@router.post(
    "/api/v1/chat/schedules/{sc_id}/run",
    response_model=ScheduledChatRunRequestedResponse,
    status_code=202,
)
async def run_scheduled_chat(
    sc_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatRunRequestedResponse:
    """Request an immediate run of one of the requesting user's scheduled chats.

    The worker picks the request up on its next poll and runs the schedule
    (as its owner) even if it is disabled, so it can be tested before
    enabling.
    """
    await _owned_schedule(sc_id, current)
    run_requested_at = await report_store.request_scheduled_chat_run(sc_id)
    if run_requested_at is None:
        raise HTTPException(status_code=404, detail="Scheduled chat not found")
    logger.info(
        "Scheduled chat run requested",
        extra={"type": "AUDIT", "scheduled_chat_id": sc_id, "user": current.user.user_id},
    )
    return ScheduledChatRunRequestedResponse(scheduled_chat_id=sc_id, run_requested_at=run_requested_at)


@router.get("/api/v1/chat/schedules/{sc_id}/versions", response_model=ScheduledChatVersionListResponse)
async def list_scheduled_chat_versions(
    sc_id: str,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatVersionListResponse:
    """Return version history for a scheduled chat (own, or any with chat:schedule:read_all)."""
    await _owned_schedule(sc_id, current, readonly=True)
    versions = await report_store.list_scheduled_chat_versions(sc_id)
    return ScheduledChatVersionListResponse(versions=versions)


@router.get("/api/v1/chat/schedules/{sc_id}/versions/{version}", response_model=ScheduledChatVersion)
async def get_scheduled_chat_version(
    sc_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_SCHEDULE)),
) -> ScheduledChatVersion:
    """Return a specific version of a scheduled chat (own, or any with chat:schedule:read_all)."""
    await _owned_schedule(sc_id, current, readonly=True)
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
