"""Temporal activities — all I/O for Seizu workflows lives here.

Activities run in the worker process (``reporting.temporal_worker``), outside
the workflow sandbox, so they may use the chat graph, the report store, and
MCP runtime freely.
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from langchain_core.messages import AIMessage, HumanMessage
from temporalio import activity
from temporalio.exceptions import ApplicationError

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError, resolve_stored_user
from reporting.authnz.permissions import Permission
from reporting.services import mcp_runtime, report_store
from reporting.services.chat_graph import (
    ChatState,
    get_chat_graph,
    load_thread_messages,
    namespaced_thread_id,
)
from reporting.services.chat_messages import message_text
from reporting.temporal_workflows.shared import RepoChatInput, RepoChatResult

logger = logging.getLogger(__name__)

_CVE_SKILLSET_ID = "cve_response"
_CVE_SKILL_ID = "cve_repo_assessment"


@activity.defn
async def run_repo_cve_chat(input: RepoChatInput) -> RepoChatResult:
    """Run an AI chat session evaluating one repository's new CVEs.

    The session runs as the scheduled query's creator (their RBAC permissions
    apply to every tool call) with the workflow's declared confirmation
    bypasses; the rendered CVE assessment skill is the first user message and
    instructs the agent to create/update the repository's findings report.
    """
    try:
        current_user = await resolve_stored_user(input.creator_user_id)
    except HeadlessIdentityError as exc:
        raise ApplicationError(str(exc), non_retryable=True) from exc

    skill_name = f"{_CVE_SKILLSET_ID}__{_CVE_SKILL_ID}"
    rendered = await mcp_runtime.render_prompt_for_chat(
        current_user,
        skill_name,
        {"repo": input.repo, "cves": json.dumps(input.cves)},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )
    if rendered.blocked is not None:
        raise ApplicationError(
            f"Skill {skill_name} render blocked: {rendered.blocked.value}",
            non_retryable=True,
        )

    date_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    session = await report_store.create_chat_session(
        current_user.user.user_id,
        f"CVE report – {input.repo} – {date_str}",
    )

    graph = get_chat_graph()
    config = {
        "configurable": {
            "current_user": current_user,
            "thread_id": namespaced_thread_id(current_user, session.thread_id),
            "client_thread_id": session.thread_id,
            "confirmation_bypass_tools": tuple(input.confirmation_bypass_tools),
        }
    }
    graph_input: ChatState = {
        "messages": [HumanMessage(content=rendered.text, id=f"msg_{uuid.uuid4().hex}")],
        # The skill is rendered server-side rather than via the skill tool, so
        # pre-unlock its tools_required for progressive disclosure.
        "disclosed_tools": list(rendered.tools_required),
    }

    logger.info(
        "Starting workflow chat session",
        extra={
            "type": "AUDIT",
            "scheduled_query_id": input.scheduled_query_id,
            "repo": input.repo,
            "thread_id": session.thread_id,
            "user": current_user.user.user_id,
        },
    )
    # Belt-and-braces inner timeout under the activity's start_to_close timeout.
    async with asyncio.timeout(settings.TEMPORAL_CHAT_ACTIVITY_TIMEOUT_SECONDS):
        async for _chunk in graph.astream(graph_input, config, stream_mode="custom"):
            activity.heartbeat()

    summary = await _final_assistant_message(current_user, session.thread_id)
    await report_store.touch_chat_session(current_user.user.user_id, session.thread_id)
    return RepoChatResult(repo=input.repo, thread_id=session.thread_id, summary=summary)


async def _final_assistant_message(current_user: CurrentUser, thread_id: str) -> str:
    messages = await load_thread_messages(current_user, thread_id, limit=settings.CHAT_HISTORY_LIMIT)
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message_text(message.content)
    return ""
