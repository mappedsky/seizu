"""Run a chat-agent session headlessly on behalf of a stored user.

Shared by the headless agent surfaces — scheduled chats and Temporal workflow
activities. The session persists as a regular ``ChatSessionItem`` owned by
the acting user, so the full transcript is reviewable in their chat UI
afterwards.

Confirmations: when the acting user holds ``chat:bypass_permissions``, the
turn runs with action confirmations bypassed (mcp_runtime re-checks the
permission and audit-logs each bypassed call). Otherwise confirmation-gated
tools fail closed for the run, and the headless system-prompt addendum tells
the model to note the block and move on.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.services import report_store
from reporting.services.chat_graph import (
    ChatState,
    get_chat_graph,
    load_thread_messages,
    namespaced_thread_id,
)
from reporting.services.chat_messages import message_text

logger = logging.getLogger(__name__)


@dataclass
class HeadlessChatResult:
    thread_id: str
    summary: str


def session_title(prefix: str) -> str:
    return f"{prefix} – {datetime.now(tz=UTC).strftime('%Y-%m-%d')}"


async def run_headless_chat(
    current_user: CurrentUser,
    *,
    prompt: str,
    title: str,
    timeout_seconds: int,
    disclosed_tools: list[str] | None = None,
    on_chunk: Callable[[], None] | None = None,
) -> HeadlessChatResult:
    """Drive one full agent turn for ``current_user`` and return its summary.

    ``on_chunk`` is invoked per streamed chunk (e.g. a Temporal activity
    heartbeat). ``disclosed_tools`` pre-unlocks tools under progressive
    disclosure when the prompt is a server-side rendered skill.
    """
    bypass = Permission.CHAT_BYPASS_PERMISSIONS.value in current_user.permissions
    session = await report_store.create_chat_session(current_user.user.user_id, title)

    graph = get_chat_graph()
    config = {
        "configurable": {
            "current_user": current_user,
            "thread_id": namespaced_thread_id(current_user, session.thread_id),
            "client_thread_id": session.thread_id,
            "headless": True,
            "bypass_confirmations": bypass,
        }
    }
    graph_input: ChatState = {"messages": [HumanMessage(content=prompt, id=f"msg_{uuid.uuid4().hex}")]}
    if disclosed_tools:
        graph_input["disclosed_tools"] = list(disclosed_tools)

    logger.info(
        "Starting headless chat session",
        extra={
            "type": "AUDIT",
            "thread_id": session.thread_id,
            "user": current_user.user.user_id,
            "bypass_confirmations": bypass,
        },
    )
    async with asyncio.timeout(timeout_seconds):
        async for _chunk in graph.astream(graph_input, config, stream_mode="custom"):
            if on_chunk is not None:
                on_chunk()

    summary = await _final_assistant_message(current_user, session.thread_id)
    await report_store.touch_chat_session(current_user.user.user_id, session.thread_id)
    return HeadlessChatResult(thread_id=session.thread_id, summary=summary)


async def _final_assistant_message(current_user: CurrentUser, thread_id: str) -> str:
    messages = await load_thread_messages(current_user, thread_id, limit=settings.CHAT_HISTORY_LIMIT)
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message_text(message.content)
    return ""
