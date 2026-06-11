"""Scheduled query action that runs the chat agent headlessly with a prompt.

The agent session runs as the scheduled query's creator, with the query
results appended to the configured prompt as JSON context. Configuring this
action requires the ``chat:bypass_permissions`` permission (enforced at
scheduled query create/update); at run time, confirmations are bypassed only
when the creator's stored role still grants that permission.

The chat checkpointer is loop-bound, so ``setup()`` captures the worker's
event loop and ``handle_results`` (which runs in a thread) schedules the
session back onto it.
"""

import asyncio
import json
import logging
from typing import Any

from reporting import settings
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import ActionConfigFieldDef
from reporting.schema.reporting_config import ScheduledQueryAction

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None


def action_name() -> str:
    return "agent_chat"


def required_permission() -> str:
    return Permission.CHAT_BYPASS_PERMISSIONS.value


def action_config_schema() -> list[ActionConfigFieldDef]:
    return [
        ActionConfigFieldDef(
            name="prompt",
            label="Prompt",
            type="text",
            required=True,
            description=(
                "Instructions for the headless agent run. The query results are appended"
                " to this prompt as JSON context."
            ),
        ),
        ActionConfigFieldDef(
            name="session_title",
            label="Session title",
            type="string",
            required=False,
            description="Chat session title prefix. Defaults to the scheduled query name.",
        ),
        ActionConfigFieldDef(
            name="max_rows",
            label="Max result rows",
            type="number",
            required=False,
            default=settings.AGENT_CHAT_MAX_RESULT_ROWS,
            description="Result rows beyond this limit are dropped from the prompt context.",
        ),
        ActionConfigFieldDef(
            name="query_return_attribute",
            label="Query return attribute",
            type="string",
            required=False,
            description="Top-level attribute of each result row that contains the data map.",
            default="details",
        ),
    ]


async def setup() -> None:
    global _main_loop
    if not settings.CHAT_ENABLED:
        logger.warning("agent_chat action loaded but CHAT_ENABLED is false; runs will be skipped")
        return
    from reporting.services.chat_graph import initialize_chat_checkpoints

    _main_loop = asyncio.get_running_loop()
    await initialize_chat_checkpoints()


def handle_results(scheduled_query_id: str, action: ScheduledQueryAction, results: list[dict[str, Any]]) -> None:
    if not results:
        return
    if not settings.CHAT_ENABLED:
        logger.error(
            "Skipping agent_chat action: CHAT_ENABLED is false",
            extra={"scheduled_query_id": scheduled_query_id},
        )
        return
    coro = _run_agent(scheduled_query_id, action, results)
    # handle_results runs via asyncio.to_thread. The chat checkpointer is
    # bound to the worker loop captured in setup(), so run the session there;
    # fall back to a private loop when setup() didn't run (tests).
    if _main_loop is not None and _main_loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, _main_loop).result()
    else:
        asyncio.run(coro)


def _build_prompt(action: ScheduledQueryAction, results: list[dict[str, Any]], scheduled_query_id: str) -> str:
    prompt = str(action.action_config.get("prompt", "")).strip()
    attr = action.action_config.get("query_return_attribute", "details")
    max_rows = int(action.action_config.get("max_rows") or settings.AGENT_CHAT_MAX_RESULT_ROWS)
    rows = [result[attr] for result in results if attr in result]
    if len(rows) > max_rows:
        logger.warning(
            "Truncating scheduled query results for agent_chat prompt",
            extra={
                "scheduled_query_id": scheduled_query_id,
                "result_count": len(rows),
                "max_rows": max_rows,
            },
        )
        rows = rows[:max_rows]
    rows_json = json.dumps(rows, default=str)
    return f"{prompt}\n\nScheduled query results (JSON):\n```json\n{rows_json}\n```"


async def _run_agent(
    scheduled_query_id: str,
    action: ScheduledQueryAction,
    results: list[dict[str, Any]],
) -> None:
    from reporting.authnz.headless import HeadlessIdentityError, resolve_stored_user
    from reporting.services import headless_chat, report_store

    item = await report_store.get_scheduled_query(scheduled_query_id)
    if item is None:
        logger.error(
            "Scheduled query not found; cannot resolve creator identity",
            extra={"scheduled_query_id": scheduled_query_id},
        )
        return
    try:
        current_user = await resolve_stored_user(item.created_by)
    except HeadlessIdentityError as exc:
        logger.error(
            "Skipping agent_chat action: %s",
            exc,
            extra={"scheduled_query_id": scheduled_query_id},
        )
        return

    title_prefix = str(action.action_config.get("session_title") or item.name)
    result = await headless_chat.run_headless_chat(
        current_user,
        prompt=_build_prompt(action, results, scheduled_query_id),
        title=headless_chat.session_title(title_prefix),
        timeout_seconds=settings.AGENT_CHAT_TIMEOUT_SECONDS,
    )
    logger.info(
        "agent_chat run finished",
        extra={
            "type": "AUDIT",
            "scheduled_query_id": scheduled_query_id,
            "thread_id": result.thread_id,
            "user": current_user.user.user_id,
        },
    )
