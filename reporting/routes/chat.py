import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import StreamingResponse
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
    ChatStreamRequest,
    ChatTurnConflictError,
    CreateChatSessionRequest,
    UpdateChatSessionRequest,
)
from reporting.services import chat_turns, report_store
from reporting.services.chat_graph import delete_thread_messages, load_thread_messages
from reporting.services.chat_messages import created_at, message_text

logger = logging.getLogger(__name__)
router = APIRouter()
# Detail kinds preserved in history so a reloaded turn keeps its full trace —
# single-agent (thinking/skill/tool) plus the orchestration trace.
_HISTORY_DETAIL_KINDS = frozenset({"thinking", "skill", "tool", "routing", "plan", "step", "verify", "subagent"})


async def _claim_chat_session_for_turn(user_id: str, thread_id: str) -> str:
    """Record the turn's activity before it starts, and report whether it may.

    Awaited rather than fired into a background task: the timestamp this writes
    is what stops the session reaper retiring the conversation
    (:mod:`reporting.services.session_reaper`), and a task that has not run yet
    protects nothing.

    Three outcomes, and the last is why this cannot fail open. A successful
    write is the turn's half of the retirement handshake; ``None`` means the
    session is gone or already claimed, so its checkpoint and sandbox are being
    deleted. **A store failure means we do not know which** -- and proceeding on
    "probably fine" is exactly the case where a turn runs against state being
    torn down underneath it. Refusing costs a retry; guessing costs the
    conversation.
    """
    try:
        return "ok" if await report_store.touch_chat_session(user_id, thread_id) else "retired"
    except Exception:
        logger.exception("Failed to update chat session timestamp", extra={"thread_id": thread_id})
        return "unavailable"


_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}


def _stream_response(source: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(source, media_type="text/event-stream", headers=_STREAM_HEADERS)


@router.post(
    "/api/v1/chat/stream",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent event stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_chat(
    body: ChatStreamRequest,
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> StreamingResponse:
    """Start a chat turn and stream it as an AI SDK UI Message Stream.

    The turn is produced independently of this request (see
    :mod:`reporting.services.chat_turns`); what this returns is a reader over
    the turn's event log, which is exactly what ``GET`` returns to a client that
    reconnects.
    """
    if body.bypass_confirmations and Permission.CHAT_BYPASS_PERMISSIONS.value not in current.permissions:
        raise HTTPException(
            status_code=403,
            detail=f"Missing permissions: {Permission.CHAT_BYPASS_PERMISSIONS.value}",
        )
    session = await report_store.get_chat_session(current.user.user_id, body.thread_id)
    if session is not None and session.origin != "interactive":
        # Headless-run transcripts are read-only: history stays viewable, but
        # the conversation cannot be continued from the web UI.
        raise HTTPException(status_code=403, detail="Headless chat sessions are read-only")
    return _stream_response(_start_and_stream(body, current))


async def _start_and_stream(body: ChatStreamRequest, current: CurrentUser) -> AsyncIterator[str]:
    session = await report_store.get_chat_session(current.user.user_id, body.thread_id)
    if session is None:
        async for frame in _stream_error("Session not found"):
            yield frame
        return
    # Awaited before the turn is created, not alongside it: this write is the
    # turn's half of the retirement handshake (SBX-011), and a turn that started
    # before it landed could be running against state being torn down.
    admission = await _claim_chat_session_for_turn(current.user.user_id, body.thread_id)
    if admission != "ok":
        # Retryable when the store was unreachable; permanent when the session
        # is being retired. The wording is the only thing telling the user
        # which, so it has to differ.
        async for frame in _stream_error(
            "This conversation has been retired"
            if admission == "retired"
            else "Could not start this turn; please try again"
        ):
            yield frame
        return
    try:
        turn = await chat_turns.start_turn(body, current)
    except ChatTurnConflictError:
        # The client has a turn running it is not watching. Telling it to
        # reconnect is more useful than starting a second one, which would
        # interleave two answers into the same conversation.
        async for frame in _stream_error("This conversation already has a turn in progress"):
            yield frame
        return
    except Exception:
        logger.exception("Failed to start chat turn", extra={"thread_id": body.thread_id})
        async for frame in _stream_error("Chat stream failed"):
            yield frame
        return
    async for frame in chat_turns.tail_turn(turn.turn_id):
        yield frame


async def _stream_error(message: str) -> AsyncIterator[str]:
    yield chat_turns.sse_frame({"type": "error", "errorText": message})
    yield chat_turns.sse_frame({"type": "finish", "finishReason": "error"})
    yield "data: [DONE]\n\n"


@router.get(
    "/api/v1/chat/stream/{thread_id}",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent event stream replaying the thread's running turn",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        204: {"description": "No turn is running for this thread"},
    },
)
async def reconnect_chat_stream(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Reattach to a thread's running turn, replaying it from the start.

    The replay begins at sequence zero rather than at a client-supplied cursor:
    the AI SDK's reconnect protocol carries no offset, and the turn's stable
    ``messageId`` is what lets the client rebuild the message rather than append
    a second one. ``204`` means there is nothing to reattach to, which the SDK
    reads as "the response already finished".
    """
    turn = await report_store.get_active_chat_turn(current.user.user_id, thread_id)
    if turn is None:
        return Response(status_code=204)
    return _stream_response(chat_turns.tail_turn(turn.turn_id))


@router.post("/api/v1/chat/stream/{thread_id}/cancel", status_code=204)
async def cancel_chat_stream(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> Response:
    """Stop the thread's running turn.

    Closing the SSE connection is not enough: the turn is produced beside the
    request, so a client that only hangs up leaves it running -- still spending
    tokens and still able to execute the actions it had lined up. Stop has to
    say so explicitly. Idempotent: no running turn is a 204, not an error.
    """
    turn = await report_store.request_chat_turn_cancel(current.user.user_id, thread_id)
    if turn is not None:
        # Fast path when the producer is on this worker; otherwise the flag
        # above reaches it at its next heartbeat.
        chat_turns.cancel_local_producer(turn.turn_id)
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


@router.delete("/api/v1/chat/sessions/{thread_id}", status_code=204)
async def delete_chat_session(
    thread_id: str = Path(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN),
    current: CurrentUser = Depends(require_permission(Permission.CHAT_USE)),
) -> None:
    """Delete a chat session."""
    # Stop the turn, and wait for it to have stopped, before removing what it
    # is writing into. Deleting first leaves a producer running against a
    # conversation that no longer exists, recreating checkpoint state and
    # appending batches behind the cascade that just ran.
    #
    # A failure here is *not* swallowed: without knowing the turn is stopped,
    # deleting is the race this exists to avoid, and a retryable error is a
    # better outcome than a half-deleted conversation.
    try:
        canceled = await report_store.request_chat_turn_cancel(current.user.user_id, thread_id)
    except Exception as exc:
        logger.exception("Failed to cancel the running turn before deletion", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Failed to delete chat session") from exc
    if canceled is not None:
        chat_turns.cancel_local_producer(canceled.turn_id)
        if not await chat_turns.await_turn_stopped(canceled.turn_id, settings.CHAT_TURN_STOP_WAIT_SECONDS):
            # It has been told and has not stopped yet. Deleting anyway is
            # what the user asked for, and the producer collects the batches
            # it wrote in the meantime once it finds its header gone.
            logger.warning(
                "Deleting a session whose turn has not stopped yet",
                extra={"thread_id": thread_id, "turn_id": canceled.turn_id},
            )
    try:
        deleted = await report_store.delete_chat_session(current.user.user_id, thread_id)
    except Exception as exc:
        logger.exception("Failed to delete chat session", extra={"thread_id": thread_id})
        raise HTTPException(status_code=503, detail="Failed to delete chat session") from exc
    if deleted:
        try:
            await delete_thread_messages(current, thread_id)
        except Exception:
            logger.exception("Failed to delete chat session checkpoints", extra={"thread_id": thread_id})
