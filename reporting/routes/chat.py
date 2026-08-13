import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from reporting import settings
from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.chat import (
    CHAT_THREAD_ID_PATTERN,
    ChatHistoryMessage,
    ChatHistoryResponse,
    ChatSessionItem,
    ChatSessionsResponse,
    ChatTurnAdmissionResponse,
    ChatTurnRequest,
    CreateChatSessionRequest,
    UpdateChatSessionRequest,
)
from reporting.services import chat_turns, report_store, session_reaper
from reporting.services.chat_graph import load_thread_messages
from reporting.services.chat_messages import created_at, message_text

logger = logging.getLogger(__name__)
router = APIRouter()
# Detail kinds preserved in history so a reloaded turn keeps its full trace —
# single-agent (thinking/skill/tool) plus the orchestration trace.
_HISTORY_DETAIL_KINDS = frozenset({"thinking", "skill", "tool", "routing", "plan", "step", "verify", "subagent"})


_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}


def _stream_response(source: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(source, media_type="text/event-stream", headers=_STREAM_HEADERS)


@router.post(
    "/api/v1/chat/threads/{thread_id}/turns",
    response_model=ChatTurnAdmissionResponse,
    status_code=201,
    responses={
        200: {"description": "This request was already admitted; the existing turn is returned"},
        409: {"description": "Another turn is already running on this thread"},
        404: {"description": "The conversation does not exist or is being deleted"},
    },
)
async def admit_chat_turn(
    body: ChatTurnRequest,
    response: Response,
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatTurnAdmissionResponse:
    """Admit a turn and return its id, without streaming anything.

    **Admission is its own request, answered before anything streams.** The
    client therefore holds a ``turn_id`` before it can possibly need one, so
    everything afterwards -- attaching, stopping -- names a turn that exists.
    Folding this into the stream was what forced a second identity for the
    window before the first frame, and a way to represent a stop against a turn
    that had not been created yet.

    ``idempotency_key`` makes a repeat resolve to the turn it already admitted,
    so a lost response is fixed by asking again.
    """
    if body.bypass_confirmations and Permission.CHAT_BYPASS_PERMISSIONS.value not in current.permissions:
        raise HTTPException(
            status_code=403,
            detail=f"Missing permissions: {Permission.CHAT_BYPASS_PERMISSIONS.value}",
        )
    session = await report_store.get_chat_session(current.user.user_id, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.origin != "interactive":
        # Headless-run transcripts are read-only: history stays viewable, but
        # the conversation cannot be continued from the web UI.
        raise HTTPException(status_code=403, detail="Headless chat sessions are read-only")

    try:
        admission = await chat_turns.start_turn(thread_id, body, current)
    except Exception as exc:
        # Admission is a store write, and a failure means we do not know whether
        # the conversation is being torn down underneath us. Refusing costs a
        # retry; guessing costs the conversation.
        logger.exception("Failed to admit a chat turn", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Could not start this turn; please try again") from exc
    if admission.outcome == "retired":
        raise HTTPException(status_code=404, detail="This conversation has been retired")
    if admission.outcome == "mismatched":
        raise HTTPException(
            status_code=409,
            detail="This idempotency key was used for a different request",
        )
    if admission.outcome == "busy":
        raise HTTPException(status_code=409, detail="This conversation already has a turn in progress")
    if admission.turn is None:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="Could not start this turn; please try again")
    if admission.outcome == "existing":
        response.status_code = 200
    return ChatTurnAdmissionResponse(turn_id=admission.turn.turn_id, status=admission.outcome)


@router.get(
    "/api/v1/chat/threads/{thread_id}/turns/active",
    response_model=ChatTurnAdmissionResponse,
    responses={204: {"description": "No turn is running for this thread"}},
)
async def active_chat_turn(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Return the thread's running turn, so a reloaded client can reattach.

    A client that reconnects has a thread but no turn id -- it never saw the
    admission that started it. This is the one place that resolves one to the
    other; everything else takes the id.
    """
    turn = await report_store.get_active_chat_turn(current.user.user_id, thread_id)
    if turn is None:
        return Response(status_code=204)
    return JSONResponse(ChatTurnAdmissionResponse(turn_id=turn.turn_id, status="existing").model_dump())


@router.get(
    "/api/v1/chat/turns/{turn_id}/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent event stream of the turn's event log",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        404: {"description": "No such turn for this user"},
    },
)
async def stream_chat_turn(
    turn_id: str = Path(min_length=1, max_length=64),
    after: str | None = Query(
        default=None,
        max_length=64,
        description="Cursor from the last frame this client received (its SSE id).",
    ),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Attach to a turn's event log.

    The same reader whether the turn was admitted a moment ago or is being
    picked up after a reload: the log is the only thing either case reads.

    With ``after``, delivery resumes from that cursor rather than replaying --
    which is what a client that merely lost its connection wants, since it still
    holds the message it was building. A reloaded page sends no cursor and gets
    the whole turn, because it has nothing to resume into.
    """
    turn = await report_store.get_chat_turn(turn_id, user_id=current.user.user_id)
    if turn is None:
        raise HTTPException(status_code=404, detail="Turn not found")
    return _stream_response(chat_turns.tail_turn(turn.turn_id, after))


@router.post("/api/v1/chat/turns/{turn_id}/cancel", status_code=204)
async def cancel_chat_turn(
    turn_id: str = Path(min_length=1, max_length=64),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Stop a turn.

    Closing the stream is not enough: the turn is produced elsewhere, so
    without being told it keeps generating and can still run the actions it had
    queued.

    **This only ever marks a turn that exists.** A client cannot name a turn
    before admission gives it one, so there is nothing here to create, park or
    reconcile. Idempotent: a turn that is already finished, or was never
    admitted, is a 204 rather than an error.
    """
    turn = await report_store.request_chat_turn_cancel(turn_id, current.user.user_id)
    if turn is not None:
        # The record carries the stop for the producer's own watch; cancelling
        # the workflow is what interrupts it mid-call rather than at the next
        # chunk boundary.
        await chat_turns.cancel_turn(turn.turn_id)
    return Response(status_code=204)


@router.get("/api/v1/chat/history", response_model=ChatHistoryResponse)
async def chat_history(
    thread_id: str = Query(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    limit: int = Query(default=settings.CHAT_HISTORY_LIMIT, ge=1, le=500),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatHistoryResponse:
    """Return the persisted messages for the caller's chat thread.

    Lets the SPA rehydrate a conversation after a reload, since the client-side
    message state is otherwise lost.
    """
    session = await report_store.get_chat_session(current.user.user_id, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    messages = await load_thread_messages(current, thread_id, limit=limit)
    return ChatHistoryResponse(
        messages=[message for index, item in enumerate(messages) if (message := _to_history_message(item, index))]
    )


def _to_history_message(message: Any, index: int) -> ChatHistoryMessage | None:
    if isinstance(message, HumanMessage):
        role: str = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    else:
        # Skip system/tool messages — the UI only renders the user/assistant turns.
        return None
    text = message_text(message.content)
    if not text:
        return None
    message_id = str(message.id) if message.id else f"{role}-{index}"
    metadata = _history_message_metadata(message, role, text)
    return ChatHistoryMessage(id=message_id, role=role, text=text, metadata=metadata)


def _history_message_metadata(message: Any, role: str, text: str) -> dict[str, object] | None:
    metadata: dict[str, object] = {}

    # Absent on messages persisted before timestamps were recorded; the UI simply
    # shows no time for those rather than guessing one.
    stamped_at = created_at(message)
    if stamped_at:
        metadata["created_at"] = stamped_at

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        # Prefer the authoritative persisted signal set at write time.
        if response_metadata.get("seizu_output_limit"):
            metadata.update({"finish_reason": "length", "response_cut_off": True})
        run_status = response_metadata.get("seizu_run_status")
        if isinstance(run_status, str):
            metadata["run_status"] = run_status
        run_errors = response_metadata.get("seizu_run_errors")
        if isinstance(run_errors, list):
            metadata["run_errors"] = [error for error in run_errors if isinstance(error, str) and error.strip()][:20]
        budget = response_metadata.get("seizu_budget")
        if isinstance(budget, dict):
            safe_budget: dict[str, object] = {
                key: value
                for key, value in budget.items()
                if key
                in {
                    "token_limit",
                    "cost_limit_usd",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cost_usd",
                    "llm_calls",
                    "usage_estimated",
                    "mode",
                    "exhaustion_reason",
                }
                and isinstance(value, (str, int, float, bool, type(None)))
            }
            phases = budget.get("phases")
            if isinstance(phases, dict):
                safe_phases: dict[str, dict[str, int | float]] = {}
                for phase, usage in list(phases.items())[:32]:
                    if not isinstance(phase, str) or not isinstance(usage, dict):
                        continue
                    safe_phases[phase] = {
                        key: value
                        for key, value in usage.items()
                        if key in {"input_tokens", "output_tokens", "total_tokens", "cost_usd", "llm_calls"}
                        and isinstance(value, (int, float))
                    }
                safe_budget["phases"] = safe_phases
            metadata["budget"] = safe_budget
        details = response_metadata.get("seizu_details")
        if isinstance(details, list):
            safe_details = [safe for detail in details if (safe := _safe_history_detail(detail)) is not None]
            if safe_details:
                metadata["details"] = safe_details

    # Fallback for messages persisted before seizu_output_limit was added: infer
    # from text content. This is intentionally a fallback — the text check is
    # brittle (an LLM quoting the notice phrase would trigger it), so new
    # messages write the authoritative seizu_output_limit field instead.
    if "finish_reason" not in metadata and role == "assistant":
        if "Response stopped because the model hit its output limit" in text:
            metadata.update({"finish_reason": "length", "response_cut_off": True})

    return metadata or None


def _safe_history_detail(detail: object, *, allow_children: bool = True) -> dict[str, object] | None:
    """Sanitize one persisted detail for the history API, recursing one level into
    a subagent's ``children``.

    Keeps the full orchestration trace (routing/plan/step/verify) and subagent
    entries, not just single-agent kinds, so an orchestrated or sandbox turn keeps
    its trace across a reload. ``step_id``/``route`` carry the hierarchy and routing
    decision; ``detail_id``/``parent_id`` carry the legacy flat grouping; ``children``
    carries the nested subagent rows.
    """
    if not isinstance(detail, dict):
        return None
    title = detail.get("title")
    kind = detail.get("kind")
    if not isinstance(title, str) or kind not in _HISTORY_DETAIL_KINDS:
        return None
    safe: dict[str, object] = {"kind": kind, "title": title}
    for key in ("status", "arguments", "body", "step_id", "route", "detail_id", "parent_id"):
        value = detail.get(key)
        if isinstance(value, str):
            safe[key] = value
    # Recurse once into a subagent's children; children are leaf rows, so a second
    # level is dropped to bound the structure.
    if allow_children:
        raw_children = detail.get("children")
        if isinstance(raw_children, list):
            safe_children = [
                child
                for item in raw_children
                if (child := _safe_history_detail(item, allow_children=False)) is not None
            ]
            if safe_children:
                safe["children"] = safe_children
    return safe


@router.get("/api/v1/chat/sessions", response_model=ChatSessionsResponse)
async def list_chat_sessions(
    limit: int = Query(default=50, ge=1, le=100),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatSessionsResponse:
    """Return all chat sessions for the current user, newest first."""
    sessions = await report_store.list_chat_sessions(current.user.user_id, limit=limit)
    return ChatSessionsResponse(
        sessions=sessions,
    )


@router.post("/api/v1/chat/sessions", response_model=ChatSessionItem, status_code=201)
async def create_chat_session(
    body: CreateChatSessionRequest,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatSessionItem:
    """Create a new chat session and return it."""
    return await report_store.create_chat_session(current.user.user_id, body.title)


@router.get("/api/v1/chat/sessions/{thread_id}", response_model=ChatSessionItem)
async def get_chat_session(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatSessionItem:
    """Return one chat session for the current user."""
    session = await report_store.get_chat_session(current.user.user_id, thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/api/v1/chat/sessions/{thread_id}", response_model=ChatSessionItem)
async def update_chat_session(
    body: UpdateChatSessionRequest,
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> ChatSessionItem:
    """Rename a chat session."""
    result = await report_store.update_chat_session_title(current.user.user_id, thread_id, body.title)
    if result is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return result


async def _close_session_for_deletion(user_id: str, thread_id: str) -> bool:
    """Claim a session and stop its turn, so deletion cannot race a producer.

    Raises 503 rather than proceeding on any uncertainty. The claim is
    re-claimable by design, so a caller that gets one can simply retry the
    delete; a conversation half-removed from under a running producer cannot be
    put back.
    """
    session = await report_store.get_chat_session(user_id, thread_id)
    if session is None:
        # Already gone. Deleting is idempotent, and there is no state left to
        # tear down under a session that never existed.
        return False
    try:
        claimed = await report_store.claim_chat_session_for_retirement(user_id, thread_id, session.updated_at)
    except Exception as exc:
        logger.exception("Failed to close a session for deletion", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Failed to delete chat session") from exc
    if not claimed:
        # A turn started between the read and the claim. Retrying picks up the
        # new timestamp; deleting now would race that turn.
        raise HTTPException(status_code=503, detail="Conversation is in use; try again")

    running = await report_store.get_active_chat_turn(user_id, thread_id)
    if running is None:
        return True
    try:
        await report_store.request_chat_turn_cancel(running.turn_id, user_id)
    except Exception as exc:
        logger.exception("Failed to cancel the running turn before deletion", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Failed to delete chat session") from exc
    await chat_turns.cancel_turn(running.turn_id)
    if not await chat_turns.await_turn_stopped(running.turn_id, settings.CHAT_TURN_STOP_WAIT_SECONDS):
        # Deleting now would leave the producer writing behind the cascade,
        # which no cleanup undoes. The session stays claimed, so nothing new can
        # start and the retry is a plain repeat.
        logger.warning(
            "Chat turn did not stop in time for deletion",
            extra={"thread_id": thread_id, "turn_id": running.turn_id},
        )
        raise HTTPException(status_code=503, detail="Conversation is still running; try again")
    return True


@router.delete("/api/v1/chat/sessions/{thread_id}", status_code=204)
async def delete_chat_session(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> None:
    """Delete a chat session.

    Close it to new turns, stop the one running, then destroy its state --
    checkpoint and sandbox first, the session record **last**.

    The claim is the reaper's own (SBX-011): it makes admission fail atomically,
    so no turn can start while this runs. Cancelling without it is not enough,
    because a cancelled turn releases its mutex when it stops and another tab
    could start a successor before the cascade.

    Deleting the record last is what makes a failure retryable. The record is
    the only thing that makes the thread findable: removing it first and then
    failing to delete the checkpoint would leave the transcript stored forever
    with nothing left to retry from -- and reporting 204 while that happened
    would not even say so.
    """
    if not await _close_session_for_deletion(current.user.user_id, thread_id):
        return
    try:
        await session_reaper.delete_session_state(current.user.user_id, thread_id)
    except Exception as exc:
        logger.exception("Failed to delete chat session", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Failed to delete chat session") from exc
