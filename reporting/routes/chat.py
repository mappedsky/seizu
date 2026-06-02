import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
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
    CreateChatSessionRequest,
    UpdateChatSessionRequest,
)
from reporting.services import report_store
from reporting.services.chat_graph import (
    ChatState,
    delete_thread_messages,
    get_chat_graph,
    load_thread_messages,
    namespaced_thread_id,
)
from reporting.services.chat_messages import CONTINUATION_MARKDOC, MessageTag, message_text, tag_message

logger = logging.getLogger(__name__)
router = APIRouter()
_CONTINUE_RESPONSE_PROMPT = (
    "Continue the previous assistant response from where it stopped because of the output limit. "
    "Do not repeat earlier content."
)


async def _touch_chat_session_later(user_id: str, thread_id: str) -> None:
    try:
        await report_store.touch_chat_session(user_id, thread_id)
    except Exception:
        logger.exception("Failed to update chat session timestamp", extra={"thread_id": thread_id})


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
    """Stream a chat response as an AI SDK UI Message Stream."""
    return StreamingResponse(
        _stream_chat_response(body, current),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )


async def _stream_chat_response(body: ChatStreamRequest, current: CurrentUser) -> AsyncIterator[str]:
    message_id = (
        body.continue_message_id if body.continue_response and body.continue_message_id else f"msg_{uuid.uuid4().hex}"
    )
    text_id = f"text_{uuid.uuid4().hex}"
    text_started = False
    finish_reason = "stop"

    try:
        yield _sse_data({"type": "start", "messageId": message_id})
        session = await report_store.get_chat_session(current.user.user_id, body.thread_id)
        if session is None:
            yield _sse_data({"type": "error", "errorText": "Session not found"})
            yield _sse_data({"type": "finish", "finishReason": "error"})
            yield "data: [DONE]\n\n"
            return
        asyncio.create_task(_touch_chat_session_later(current.user.user_id, body.thread_id))
        yield _sse_data({"type": "text-start", "id": text_id})
        text_started = True
        if body.continue_response:
            yield _sse_data({"type": "text-delta", "id": text_id, "delta": CONTINUATION_MARKDOC})
        async for event in _token_source(body, current):
            if event["type"] == "token":
                delta = event["content"]
                if delta:
                    yield _sse_data({"type": "text-delta", "id": text_id, "delta": delta})
            elif event["type"] == "finish_reason":
                finish_reason = event["finish_reason"]
            elif event["type"] == "detail":
                detail_id = event["id"]
                detail_data = event["data"]
                if isinstance(detail_id, str) and isinstance(detail_data, dict):
                    yield _sse_data({"type": "data-seizu-detail", "id": detail_id, "data": detail_data})
    except Exception:
        logger.exception("Chat stream failed")
        if text_started:
            yield _sse_data({"type": "text-end", "id": text_id})
        yield _sse_data({"type": "error", "errorText": "Chat stream failed"})
        yield _sse_data({"type": "finish", "finishReason": "error"})
        yield "data: [DONE]\n\n"
        return

    yield _sse_data({"type": "text-end", "id": text_id})
    finish_payload: dict[str, Any] = {
        "type": "finish",
        "finishReason": finish_reason,
        "messageMetadata": {
            "finish_reason": finish_reason,
            "response_cut_off": finish_reason == "length",
        },
    }
    yield _sse_data(finish_payload)
    yield "data: [DONE]\n\n"


async def _token_source(body: ChatStreamRequest, current: CurrentUser) -> AsyncIterator[dict[str, Any]]:
    graph = get_chat_graph()
    if body.resume_confirmation_id:
        resume_message = HumanMessage(
            content=f"Resume approved confirmation {body.resume_confirmation_id}",
            id=f"msg_{uuid.uuid4().hex}",
            additional_kwargs={"resume_confirmation_id": body.resume_confirmation_id},
        )
        tag_message(resume_message, MessageTag.EPHEMERAL)
        graph_input: ChatState = {"messages": [resume_message]}
    elif body.continue_response:
        continue_message = HumanMessage(
            content=_CONTINUE_RESPONSE_PROMPT,
            id=f"msg_{uuid.uuid4().hex}",
            additional_kwargs={"continue_response": True},
        )
        tag_message(continue_message, MessageTag.EPHEMERAL)
        graph_input = {"messages": [continue_message]}
    else:
        graph_input = {"messages": [HumanMessage(content=body.message, id=f"msg_{uuid.uuid4().hex}")]}
    config = {
        "configurable": {
            "current_user": current,
            "thread_id": namespaced_thread_id(current, body.thread_id),
            "client_thread_id": body.thread_id,
        }
    }
    async for chunk in graph.astream(graph_input, config, stream_mode="custom"):
        if isinstance(chunk, dict) and chunk.get("kind") == "token":
            delta = chunk.get("content")
            if isinstance(delta, str):
                yield {"type": "token", "content": delta}
        elif isinstance(chunk, dict) and chunk.get("kind") == "finish_reason":
            finish_reason = chunk.get("finish_reason")
            if finish_reason == "length":
                yield {"type": "finish_reason", "finish_reason": "length"}
        elif isinstance(chunk, dict) and chunk.get("kind") == "detail":
            detail_id = chunk.get("id")
            detail_data = chunk.get("data")
            if isinstance(detail_id, str) and isinstance(detail_data, dict):
                yield {"type": "detail", "id": detail_id, "data": detail_data}


def _sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


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

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        # Prefer the authoritative persisted signal set at write time.
        if response_metadata.get("seizu_output_limit"):
            metadata.update({"finish_reason": "length", "response_cut_off": True})
        details = response_metadata.get("seizu_details")
        if isinstance(details, list):
            safe_details: list[dict[str, object]] = []
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                title = detail.get("title")
                kind = detail.get("kind")
                if not isinstance(title, str) or kind not in {"thinking", "skill", "tool"}:
                    continue
                safe_detail: dict[str, object] = {"kind": kind, "title": title}
                for key in ("status", "arguments", "body"):
                    value = detail.get(key)
                    if isinstance(value, str):
                        safe_detail[key] = value
                safe_details.append(safe_detail)
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
