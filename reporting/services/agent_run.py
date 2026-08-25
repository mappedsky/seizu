"""Run one headless agent session on behalf of a stored user.

The single entry point shared by the two headless agent surfaces — scheduled
chats (``reporting.temporal_workflows.scheduled_chat``) and the ``agent_chat``
workflow module. Both look separate to users (own store, routes, permissions,
and UI) but converge here so identity resolution, prompt construction, the
untrusted-data boundary, and run-status normalization stay consistent.

This wraps :func:`reporting.services.headless_chat.run_headless_chat` rather
than replacing it: that function owns the chat session, the budget ledger, and
the LangGraph turn. What lives here is everything *around* the turn.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from reporting.authnz import CurrentUser
from reporting.authnz.headless import resolve_stored_user
from reporting.authnz.permissions import Permission
from reporting.schema.model_profiles import ResolvedModelProfile
from reporting.services import headless_chat, mcp_runtime, model_profiles

# Re-exported: the boundary moved to its own module so consumers across the
# import graph can share it, but callers here still reach it through agent_run.
from reporting.services.untrusted import untrusted_instruction, untrusted_payload

logger = logging.getLogger(__name__)

# Run statuses ``headless_chat`` reports, mapped onto the vocabulary the
# schedule/workflow records store. Anything else (partial, budget_exhausted,
# blocked) passes through unchanged.
_STATUS_MAP = {"completed": "success", "failed": "failure"}


class AgentRunError(Exception):
    """A run could not be started (blocked skill render, bad configuration)."""


@dataclass(frozen=True)
class AgentRunResult:
    thread_id: str
    summary: str
    # success | partial | budget_exhausted | blocked | failure
    status: str
    budget: dict[str, Any] | None = None
    error: str | None = None
    # The status headless_chat reported, before _STATUS_MAP was applied.
    # Callers whose stored result predates normalization keep using this.
    raw_status: str = ""


@dataclass(frozen=True)
class AgentRunRequest:
    """Everything needed to drive one agent session.

    ``rows`` are graph rows from an earlier workflow stage. They are untrusted
    prompt input: they are JSON-encoded and HTML-escaped inside a tagged block
    behind an explicit security-boundary preamble, never interpolated raw.
    ``skill`` renders a stored skill server-side (``skillset__skill``) and
    pre-unlocks its ``tools_required`` for progressive disclosure.
    """

    creator_user_id: str
    prompt: str
    title_prefix: str
    timeout_seconds: int
    origin: Literal["scheduled", "workflow"]
    scheduled_chat_id: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    untrusted_tag: str = "untrusted_graph_data"
    skill: str | None = None
    skill_arguments: dict[str, str] = field(default_factory=dict)
    model_profile_id: str | None = None
    resolved_model_profile: ResolvedModelProfile | None = None


def normalize_status(status: str) -> str:
    return _STATUS_MAP.get(status, status)


async def _build_prompt(
    request: AgentRunRequest,
    current_user: CurrentUser,
) -> tuple[str, list[str]]:
    """Return the first user message and the tools to pre-disclose.

    A configured skill is rendered server-side and appended to the operator's
    prompt, so the run gets both the operator's instructions and the skill body
    without spending a turn on the skill tool.
    """
    sections: list[str] = []
    disclosed_tools: list[str] = []

    if request.rows:
        sections.append(untrusted_instruction(request.untrusted_tag))
        sections.append(untrusted_payload(request.rows, request.untrusted_tag))

    sections.append(request.prompt)

    if request.skill:
        rendered = await mcp_runtime.render_prompt_for_chat(
            current_user,
            request.skill,
            dict(request.skill_arguments),
            gate_permission=Permission.CHAT_SKILLS_CALL,
        )
        if rendered.blocked is not None:
            raise AgentRunError(f"Skill {request.skill} render blocked: {rendered.blocked.value}")
        sections.append(rendered.text)
        disclosed_tools = list(rendered.tools_required)

    return "\n\n".join(section for section in sections if section), disclosed_tools


async def run_agent_session(
    request: AgentRunRequest,
    *,
    on_progress: Callable[[], None] | None = None,
) -> AgentRunResult:
    """Drive one agent turn as ``request.creator_user_id`` and normalize the result.

    Raises :class:`reporting.authnz.headless.HeadlessIdentityError` when the
    creator is missing or archived, and :class:`AgentRunError` when a
    configured skill cannot be rendered — callers turn both into
    non-retryable Temporal failures.

    ``on_progress`` is invoked per streamed chunk; activities pass
    ``activity.heartbeat`` so a long run doesn't trip its heartbeat timeout.
    """
    current_user = await resolve_stored_user(request.creator_user_id)
    prompt, disclosed_tools = await _build_prompt(request, current_user)
    if request.resolved_model_profile is not None:
        resolved_profile = request.resolved_model_profile
    elif request.model_profile_id is not None:
        resolved_profile = await model_profiles.resolve(request.model_profile_id)
    else:
        resolved_profile = model_profiles.environment_snapshot()

    logger.info(
        "Starting headless agent run",
        extra={
            "type": "AUDIT",
            "origin": request.origin,
            "scheduled_chat_id": request.scheduled_chat_id,
            "user": current_user.user.user_id,
        },
    )
    result = await headless_chat.run_headless_chat(
        current_user,
        prompt=prompt,
        title=headless_chat.session_title(request.title_prefix),
        timeout_seconds=request.timeout_seconds,
        disclosed_tools=disclosed_tools or None,
        on_chunk=on_progress,
        origin=request.origin,
        scheduled_chat_id=request.scheduled_chat_id,
        resolved_model_profile=resolved_profile,
    )
    status = normalize_status(result.status)
    return AgentRunResult(
        thread_id=result.thread_id,
        summary=result.summary,
        status=status,
        budget=result.budget,
        error=f"Headless run ended with status: {result.status}" if status == "failure" else None,
        raw_status=result.status,
    )
