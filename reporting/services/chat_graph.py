import asyncio
import hashlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Annotated, Any, Literal, NotRequired, Protocol

import botocore.config
from botocore.exceptions import ClientError
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph_checkpoint_aws import DynamoDBSaver
from mcp.types import Prompt, Tool
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from typing_extensions import TypedDict

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.services import action_confirmations, mcp_builtins, mcp_runtime, report_store
from reporting.services.chat_budget import (
    BudgetExceeded,
    budget_controller_from_config,
    estimate_tokens,
    usage_cost_usd,
)
from reporting.services.chat_messages import (
    CONTINUATION_MARKDOC,
    MessageTag,
    drop_tagged,
    has_tag,
    message_text,
    strip_chat_ui_markers,
    tag_message,
)
from reporting.services.mcp_runtime import ChatBlockReason
from reporting.utils.sql import build_database_url

logger = logging.getLogger(__name__)

# Carries the outer chat agent's currently-disclosed tool names into builtin


class ChatState(TypedDict):
    messages: Annotated[list[Any], add_messages]
    # Orchestration state (plan -> dispatch -> verify). All optional so the
    # simple single-agent path never has to populate them; they round-trip
    # through the checkpointer so an orchestrated turn can resume mid-plan.
    # Each uses the default overwrite reducer: the dispatcher is the sole writer
    # of ``plan``/``step_results`` per super-step (worker parallelism happens
    # inside the node via asyncio), so no concurrent-write reducer is needed.
    route: NotRequired[str]  # "simple" | "orchestrate"
    plan: NotRequired[list[dict[str, Any]]]  # serialized PlanStep dicts
    step_results: NotRequired[list[dict[str, Any]]]  # per-step worker outputs
    iteration: NotRequired[int]  # verify-driven retry cycles consumed
    budget: NotRequired[dict[str, Any]]  # serializable per-turn run budget ledger
    run_errors: NotRequired[list[str]]  # non-fatal orchestration/runtime diagnostics
    # Tool names the conversation has already unlocked under progressive
    # disclosure. ``disclosed_tool_names`` is otherwise a per-turn local, so a
    # turn that ended mid-flow (rate limit, output cap) would lose the tools a
    # rendered skill had surfaced; persisting the set here lets the next turn
    # call them without re-rendering the disclosing skill. Overwrite reducer:
    # ``chat_agent_node`` is the sole writer and writes back the full union.
    disclosed_tools: NotRequired[list[str]]


class ChatGraph(Protocol):
    def astream(
        self,
        input: ChatState,
        config: dict[str, Any],
        *,
        stream_mode: str,
    ) -> Any: ...

    def aget_state(self, config: dict[str, Any]) -> Any: ...


class ChatModel(Protocol):
    def astream(
        self,
        input: list[BaseMessage],
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]: ...


_chat_graph: ChatGraph | None = None
_chat_checkpoint_pool: AsyncConnectionPool | None = None


@dataclass(frozen=True)
class ChatToolSpec:
    name: str
    kind: Literal["skill", "tool"]
    description: str
    input_schema: dict[str, Any]
    llm_name: str | None = None


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]
    spec: ChatToolSpec


@dataclass(frozen=True)
class ToolCallResult:
    request: ToolCallRequest
    content: str
    blocked: ChatBlockReason | None = None
    tools_required: tuple[str, ...] = ()


@dataclass(frozen=True)
class LLMTurnResult:
    message: AIMessage
    streamed: str
    finish_reason: str | None = None
    details: tuple[dict[str, Any], ...] = ()
    # True when the model emitted raw tool-call protocol markup as text instead
    # of a structured tool call (seen with some DeepSeek models). The markup is
    # withheld from the user; the caller retries the turn once, then degrades.
    tool_markup_leaked: bool = False
    # Best-effort tool names parsed out of that leaked markup, so the retry can
    # tell the model specifically which tool it reached for and how to unlock it.
    leaked_tool_names: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    usage_estimated: bool = False


@dataclass(frozen=True)
class AnswerBudget:
    min_words: int
    max_words: int
    max_bullets: int
    max_tables: int


# Provider sentinel. "mock" keeps chat deterministic and keyless; every other
# value routes through LiteLLM, so the supported provider/model surface is
# whatever LiteLLM supports rather than a fixed in-code allowlist. LiteLLM owns
# the cross-provider quirks (base_url/api_key handling, DeepSeek-shape endpoints,
# reasoning_content normalization) that Seizu used to special-case here.
_MOCK_PROVIDER = "mock"

# Structural completion signal for the tool loop. Once Seizu has run an action,
# the model finishes the turn by calling this synthetic tool with the final
# answer (rather than us classifying plain text as "done"). Exposed post-action
# only, so trivial pre-action replies keep streaming directly. The loop
# intercepts the call by name and never dispatches it to MCP.
_FINAL_ANSWER_TOOL = "respond_to_user"

# Appended to the system prompt for headless (automated) turns — scheduled
# query agent runs and Temporal workflow sessions — where no human can answer.
_HEADLESS_PROMPT_ADDENDUM = (
    "This conversation is an automated headless run: no human is present and nobody can reply. "
    "Never ask the user for confirmation, clarification, or approval, and never wait for input. "
    "Carry out the task exactly as directed by the prompt and any rendered skills, making "
    "reasonable decisions yourself where a skill leaves room. If an action is blocked because it "
    "requires interactive confirmation, note it in your summary and move on rather than retrying. "
    "Finish with a concise summary of what you did and found."
)


def namespaced_thread_id(current_user: CurrentUser, thread_id: str) -> str:
    """Scope a client-supplied thread id to the authenticated user.

    The user id prefix is server-derived, so a client cannot reach another
    user's thread by guessing the thread id.
    """
    return f"user:{current_user.user.user_id}:thread:{thread_id}"


async def load_thread_messages(current_user: CurrentUser, thread_id: str, *, limit: int) -> list[Any]:
    """Return the persisted LangChain messages for a user's chat thread.

    Ephemeral-tagged messages are filtered out here so they never reach the
    history API or (in future) the LLM context, even if some code path persists
    one — the tag is the single enforcement point.
    """
    graph = get_chat_graph()
    config = {"configurable": {"thread_id": namespaced_thread_id(current_user, thread_id)}}
    state = await graph.aget_state(config)
    values = getattr(state, "values", None) or {}
    messages = values.get("messages", [])
    if not isinstance(messages, list):
        return []
    visible = _collapse_ephemeral_continuations(messages)
    return visible[-limit:] if limit > 0 else []


def _collapse_ephemeral_continuations(messages: list[Any]) -> list[Any]:
    visible: list[Any] = []
    merge_next_ai = False
    for message in messages:
        if has_tag(message, MessageTag.EPHEMERAL):
            additional_kwargs = getattr(message, "additional_kwargs", None)
            if isinstance(additional_kwargs, dict) and additional_kwargs.get("continue_response") is True:
                merge_next_ai = True
            continue
        if merge_next_ai and isinstance(message, AIMessage):
            merge_next_ai = False
            if visible and isinstance(visible[-1], AIMessage):
                visible[-1] = _merge_ai_continuation(visible[-1], message)
            # When the merge target is absent (e.g. trimmed by the message cap),
            # discard the orphaned continuation rather than appending it as a
            # confusing standalone message.
            continue
        else:
            merge_next_ai = False
        visible.append(message)
    return visible


def _merge_ai_continuation(previous: AIMessage, continuation: AIMessage) -> AIMessage:
    previous_text = _strip_output_limit_notice(message_text(previous.content))
    continuation_text = message_text(continuation.content).lstrip()
    if not previous_text:
        merged_text = continuation_text
    elif not continuation_text:
        merged_text = previous_text
    else:
        merged_text = f"{previous_text}{CONTINUATION_MARKDOC}{continuation_text}"
    previous_details = previous.response_metadata.get("seizu_details", [])
    continuation_details = continuation.response_metadata.get("seizu_details", [])
    merged_details = (
        [
            *previous_details,
            *continuation_details,
        ]
        if isinstance(previous_details, list) and isinstance(continuation_details, list)
        else []
    )
    return AIMessage(
        content=merged_text,
        id=previous.id,
        additional_kwargs={**previous.additional_kwargs, **continuation.additional_kwargs},
        response_metadata={
            **previous.response_metadata,
            **continuation.response_metadata,
            **({"seizu_details": merged_details} if merged_details else {}),
        },
    )


def _strip_output_limit_notice(response: str) -> str:
    return response.replace(_OUTPUT_LIMIT_NOTICE, "").replace(_OUTPUT_LIMIT_SUMMARY_NOTICE, "").rstrip()


async def delete_thread_messages(current_user: CurrentUser, thread_id: str) -> None:
    """Permanently delete persisted LangGraph state for a user's chat thread."""
    graph = get_chat_graph()
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None:
        raise RuntimeError("Chat graph does not expose a checkpointer")
    namespaced_id = namespaced_thread_id(current_user, thread_id)
    async_delete = getattr(checkpointer, "adelete_thread", None)
    if callable(async_delete):
        await async_delete(namespaced_id)
        return
    sync_delete = getattr(checkpointer, "delete_thread", None)
    if callable(sync_delete):
        await asyncio.to_thread(sync_delete, namespaced_id)
        return
    raise RuntimeError("Chat checkpointer does not support thread deletion")


async def mock_agent_node(state: ChatState, _config: RunnableConfig) -> ChatState:
    last_user_message = _last_user_text(state["messages"])
    response = f"I received your message: {last_user_message}"
    ai_message = AIMessage(content=response, id=f"msg_{uuid.uuid4().hex}")
    writer = get_stream_writer()

    # The mock has no real model behind it; emit small chunks with a sleep so
    # the dev SSE path still feels like a stream and exercises the same client
    # code as a real provider.
    for chunk in _chunk_text(response):
        writer({"kind": "token", "content": chunk})
        await asyncio.sleep(0.03)

    return {"messages": [*_trim_messages(state["messages"], ai_message), ai_message]}


async def chat_agent_node(state: ChatState, config: RunnableConfig) -> ChatState:
    provider = _chat_provider()
    current_user = _current_user_from_config(config)
    resume_confirmation_id = _resume_confirmation_id(state["messages"])
    if resume_confirmation_id:
        return await _resume_confirmed_tool_turn(state, config, current_user, resume_confirmation_id)
    if provider == "mock":
        return await mock_agent_node(state, config)

    messages = _llm_context_messages(state["messages"])
    model = get_chat_model()
    writer = get_stream_writer()
    base_system_prompt = build_system_prompt(provider, current_user)
    if _headless_from_config(config):
        base_system_prompt = f"{base_system_prompt}\n\n{_HEADLESS_PROMPT_ADDENDUM}"

    # One listing per turn — every consumer below (capability context, skill
    # specs, tool specs) works off this snapshot. No cross-turn cache: each
    # turn sees the live store, and a single ``list_enabled_*`` call covers
    # every read the turn needs.
    skills = await _list_chat_prompts(current_user)
    tools: list[Tool] = []
    progressive_disclosure = settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE
    _always_disclosed_names = mcp_builtins.always_disclosed_tool_names() if progressive_disclosure else frozenset()
    # Carry forward tools the conversation already unlocked in earlier turns so a
    # resumed/follow-up turn can call them directly (the in-turn disclosure set
    # is otherwise reset each turn — see ChatState.disclosed_tools).
    disclosed_tool_names: set[str] = set(state.get("disclosed_tools") or []) if progressive_disclosure else set()
    if not progressive_disclosure:
        tools = await _list_chat_tools(current_user)
    elif disclosed_tool_names or _always_disclosed_names:
        # Resolve the persisted names against the live store; names whose tool
        # no longer exists simply drop out.  Also fetch when always-disclosed
        # tools exist so they can appear in the capability context.
        tools = await _list_chat_tools(current_user)

    always_disclosed_tools = [t for t in tools if t.name in _always_disclosed_names] if progressive_disclosure else []
    capability_context = build_capability_context(
        skills,
        tools if not progressive_disclosure else None,
        always_disclosed_tools=always_disclosed_tools,
    )
    if capability_context:
        base_system_prompt = f"{base_system_prompt}\n\n{capability_context}"

    skill_specs = _skill_tool_specs(skills)
    tool_specs: list[ChatToolSpec]
    if not progressive_disclosure:
        tool_specs = _mcp_tool_specs(tools)
    else:
        tool_specs = _disclosed_tool_specs(tools, disclosed_tool_names | _always_disclosed_names)

    action_count = 0
    action_summaries: list[str] = []
    executed_action_keys: set[str] = set()
    empty_retry_used = False
    repeated_action_retry_used = False
    tool_markup_retry_used = False
    terminal_response_retry_used = False
    response_is_broken = False
    response_hit_output_limit = False
    streamed_response = ""
    detail_events: list[dict[str, Any]] = []
    # Retry guidance for the *next* LLM call is appended to the system prompt
    # so it never appears as a mid-conversation SystemMessage (which several
    # provider adapters — most notably ChatAnthropic — discourage).
    pending_system_addendum = ""

    response = ""
    streamed_in_last_turn = ""
    while action_count < settings.CHAT_LLM_MAX_AUTO_ACTIONS:
        turn_system_prompt = _combined_system_prompt(base_system_prompt, pending_system_addendum)
        pending_system_addendum = ""
        post_action = bool(action_summaries)
        available_specs = _with_provider_tool_names([*skill_specs, *tool_specs, *_terminal_specs(post_action)])
        # Pre-action prose streams live (the fast path for a direct answer). Once
        # Seizu has run actions the model must finish through a structured
        # respond_to_user call; post-action prose is a stall, not streamed, so we
        # never ship un-retractable text.
        turn_writer = None if post_action else writer
        turn_result = await _run_llm_tool_turn(
            model,
            turn_system_prompt,
            messages,
            available_specs,
            config,
            turn_writer,
        )
        ai_message = turn_result.message
        streamed_in_last_turn = turn_result.streamed
        streamed_response = streamed_in_last_turn
        detail_events.extend(turn_result.details)
        if turn_result.tool_markup_leaked:
            # The model wrote raw tool-call markup as text (already withheld from
            # the user). Retry once with corrective guidance, then degrade.
            if not tool_markup_retry_used:
                tool_markup_retry_used = True
                pending_system_addendum = _tool_markup_retry_message(turn_result.leaked_tool_names)
                continue
            response = _tool_markup_fallback()
            response_is_broken = True
            break
        unavailable = _unavailable_tool_call_results(ai_message, available_specs)
        if unavailable:
            action_summaries.append(_tool_call_user_summary(unavailable))
            response = _blocked_tool_call_response(unavailable)
            break

        requested = _tool_call_requests(ai_message, available_specs)
        finish_requests = [request for request in requested if request.name == _FINAL_ANSWER_TOOL]
        requested = [request for request in requested if request.name != _FINAL_ANSWER_TOOL]
        if not requested:
            # Structural completion: an explicit respond_to_user call (or, for a
            # pre-action turn, plain text) is the terminal answer. Post-action
            # plain text that skipped respond_to_user is a stall — nudge once to
            # act-or-finish, then accept whatever the model returns next.
            if finish_requests:
                response = _final_answer_text(finish_requests[0]) or message_text(ai_message.content)
            else:
                response = message_text(ai_message.content)
                if response and post_action and not terminal_response_retry_used:
                    terminal_response_retry_used = True
                    pending_system_addendum = _terminal_stall_retry_message(action_summaries)
                    response = ""
                    continue
            if response:
                if post_action and not streamed_response and writer is not None:
                    writer({"kind": "token", "content": response})
                    streamed_response = response
                response, appended, still_truncated, cont_details = await _auto_continue_answer(
                    model, messages, turn_system_prompt, response, turn_result.finish_reason, config, writer
                )
                streamed_response += appended
                detail_events.extend(cont_details)
                response, response_hit_output_limit = _append_output_limit_notice(
                    response, "length" if still_truncated else None, action_summaries
                )
                break
            if action_summaries:
                # Empty post-action turn: fall through to the forced synthesis below.
                break
            if not empty_retry_used:
                empty_retry_used = True
                pending_system_addendum = _initial_empty_response_retry_message()
                continue
            break

        remaining = settings.CHAT_LLM_MAX_AUTO_ACTIONS - action_count
        batch = requested[:remaining]
        repeated = [request for request in batch if _tool_request_key(request) in executed_action_keys]
        batch = [request for request in batch if _tool_request_key(request) not in executed_action_keys]
        if not batch:
            if repeated:
                if not repeated_action_retry_used:
                    repeated_action_retry_used = True
                    pending_system_addendum = _repeated_tool_call_retry_message(repeated, action_summaries)
                    continue
                response = _repeated_tool_call_fallback(repeated, action_summaries)
                response_is_broken = True
                break
            break

        # Prose emitted alongside a tool call is plan narration, not an answer.
        # Record it as a thinking detail so it survives a reload as narration
        # rather than leaking into the persisted answer body.
        narration = message_text(ai_message.content).strip()
        if narration:
            narration_detail = _planning_narration_detail_data(narration)
            detail_events.append(narration_detail)
            writer({"kind": "detail", "id": f"detail_{uuid.uuid4().hex}", "data": narration_detail})

        action_count += len(batch)
        executed_action_keys.update(_tool_request_key(request) for request in batch)
        batch_id = _confirmation_batch_id_for_requests(batch)
        results = await _run_tool_call_batch(
            batch,
            current_user,
            session_key=_client_thread_id_from_config(config),
            batch_id=batch_id,
            bypass_confirmations=_bypass_confirmations_from_config(config),
        )
        for result in results:
            detail_data = _tool_call_detail_data(result)
            detail_events.append(detail_data)
            writer({"kind": "detail", "id": f"detail_{uuid.uuid4().hex}", "data": detail_data})
        action_summaries.append(_tool_call_user_summary(results))
        blocked_results = _blocked_tool_call_results(results)
        tool_ai_message = _ai_message_for_tool_results(ai_message, results)
        messages = [
            *messages,
            tool_ai_message,
            *[
                ToolMessage(
                    content=result.content,
                    name=_llm_tool_name(result.request.spec),
                    tool_call_id=result.request.id,
                    id=f"msg_{uuid.uuid4().hex}",
                )
                for result in results
            ],
        ]
        messages = _trim_inner_loop_messages(messages, max_chars=settings.CHAT_LLM_CONTEXT_MAX_CHARS)
        if blocked_results:
            response = _blocked_tool_call_response(blocked_results)
            break
        if progressive_disclosure:
            newly_disclosed = _disclosed_tool_names_from_skill_results(results)
            if newly_disclosed:
                disclosed_tool_names.update(newly_disclosed)
                if not tools:
                    tools = await _list_chat_tools(current_user)
                tool_specs = _disclosed_tool_specs(tools, disclosed_tool_names | _always_disclosed_names)

    if not response and action_summaries and not response_is_broken:
        synthesis_system_prompt = _combined_system_prompt(
            base_system_prompt, _final_synthesis_retry_message(action_summaries)
        )
        turn_result = await _run_llm_tool_turn(model, synthesis_system_prompt, messages, [], config, None)
        final_message = turn_result.message
        streamed_in_last_turn = turn_result.streamed
        streamed_response = streamed_in_last_turn
        detail_events.extend(turn_result.details)
        response = message_text(final_message.content)
        if response and _internal_action_transcript_leaked(response):
            retry_prompt = _combined_system_prompt(
                synthesis_system_prompt,
                _action_transcript_retry_message(),
            )
            turn_result = await _run_llm_tool_turn(model, retry_prompt, messages, [], config, None)
            final_message = turn_result.message
            streamed_in_last_turn = turn_result.streamed
            streamed_response = streamed_in_last_turn
            detail_events.extend(turn_result.details)
            response = message_text(final_message.content)
        if response:
            if writer is not None and not streamed_response:
                writer({"kind": "token", "content": response})
                streamed_response = response
            response, appended, still_truncated, cont_details = await _auto_continue_answer(
                model, messages, synthesis_system_prompt, response, turn_result.finish_reason, config, writer
            )
            streamed_response += appended
            detail_events.extend(cont_details)
            response, response_hit_output_limit = _append_output_limit_notice(
                response, "length" if still_truncated else None, action_summaries
            )
        else:
            response_is_broken = True

    if not response:
        response = _empty_response_fallback(action_summaries)
        response_is_broken = True

    ai_message = finalize_assistant_message(
        response=response,
        streamed=streamed_response,
        writer=writer,
        details=detail_events,
        output_limit=response_hit_output_limit,
        broken=response_is_broken,
        extra_metadata=_run_metadata(config, state, failed=response_is_broken),
    )
    state_update: ChatState = {"messages": [*_trim_messages(state["messages"], ai_message), ai_message]}
    if progressive_disclosure and disclosed_tool_names:
        # Persist the union so a later turn (including one resuming after an
        # interrupted turn) keeps tools this conversation already unlocked.
        state_update["disclosed_tools"] = sorted(disclosed_tool_names)
    return state_update


def _run_metadata(config: RunnableConfig, state: ChatState, *, failed: bool) -> dict[str, Any]:
    controller = budget_controller_from_config(config)
    if controller is not None and controller.finalizing:
        status = "budget_exhausted"
    else:
        status = "failed" if failed else "completed"
    errors = list(state.get("run_errors") or [])
    if failed:
        errors.append("The assistant could not produce a complete response.")
    return {
        "seizu_run_status": status,
        **({"seizu_budget": controller.snapshot()} if controller is not None else {}),
        **({"seizu_run_errors": errors} if errors else {}),
    }


def finalize_assistant_message(
    *,
    response: str,
    streamed: str,
    writer: Callable[[dict[str, Any]], None] | None,
    details: list[dict[str, Any]] | None = None,
    output_limit: bool = False,
    broken: bool = False,
    extra_metadata: dict[str, Any] | None = None,
) -> AIMessage:
    """Reconcile streamed text with the final response and build the persisted
    assistant message.

    Shared by the single-agent terminal (``chat_agent_node``) and the
    orchestrated terminal (``synthesizer_node``) so the "Continue response"
    signal (``finish_reason``/``seizu_output_limit``) and the ``seizu_details``
    trace are emitted identically regardless of which path produced the turn.

    Streaming contract: LLM text is already on the wire as chunks arrived;
    synthetic fallbacks and any tail not yet streamed are emitted here as a
    single delta, and the persisted content is synced to match the wire.
    """
    if writer is not None and response and response != streamed:
        if not streamed:
            writer({"kind": "token", "content": response})
        elif response.startswith(streamed):
            tail = _stream_tail(streamed, response[len(streamed) :])
            writer({"kind": "token", "content": tail})
            response = f"{streamed}{tail}"
        else:
            tail = _stream_tail(streamed, response, separator="\n\n")
            writer({"kind": "token", "content": tail})
            response = f"{streamed}{tail}"
    if output_limit and writer is not None:
        writer({"kind": "finish_reason", "finish_reason": "length"})

    response_metadata: dict[str, Any] = dict(extra_metadata or {})
    if details:
        response_metadata["seizu_details"] = details
    if output_limit:
        response_metadata["seizu_output_limit"] = True
    ai_message = AIMessage(
        content=response,
        id=f"msg_{uuid.uuid4().hex}",
        response_metadata=response_metadata,
    )
    if broken:
        tag_message(ai_message, MessageTag.BROKEN)
    return ai_message


def _stream_tail(streamed: str, tail: str, *, separator: str = " ") -> str:
    """Return a stream delta that does not jam two text segments together."""
    if not streamed or not tail:
        return tail
    if streamed[-1].isspace() or tail[0].isspace():
        return tail
    if streamed[-1] in "([{/" or tail[0] in ".,;:!?)]}/":
        return tail
    return f"{separator}{tail}"


def _combined_system_prompt(base: str, addendum: str) -> str:
    if not addendum:
        return base
    return f"{base}\n\n{addendum}"


# --- Auto-continuation of length-truncated answers ----------------------------

# How much of the prior answer's tail to quote back as the exact continuation
# point, and how many leading continuation chars to buffer while trimming any
# text the model repeats from that tail.
_CONTINUATION_CONTEXT_CHARS = 600
_CONTINUATION_OVERLAP_CAP = 400


def _trim_overlap(prior: str, continuation: str) -> str:
    """Drop the longest prefix of *continuation* that repeats the tail of *prior*.

    Models often re-emit the last sentence or two when asked to continue; trimming
    the verbatim overlap makes the stitched seam invisible. Exact-match only, so
    genuinely new content is never deleted.
    """
    limit = min(len(prior), len(continuation), _CONTINUATION_OVERLAP_CAP)
    for size in range(limit, 0, -1):
        if prior.endswith(continuation[:size]):
            return continuation[size:]
    return continuation


class _ContinuationStitcher:
    """Wraps a continuation turn's token stream: holds back the leading window
    until the overlap with the prior answer is trimmed, then passes tokens through
    live. ``emitted`` is the (trimmed) text actually sent to the user."""

    def __init__(self, prior: str, writer: Callable[[dict[str, Any]], None]) -> None:
        self._prior = prior
        self._writer = writer
        self._buffer = ""
        self._resolved = False
        self.emitted = ""

    def feed_token(self, delta: str) -> None:
        if self._resolved:
            self._emit(delta)
            return
        self._buffer += delta
        if len(self._buffer) >= _CONTINUATION_OVERLAP_CAP:
            self._resolve()

    def _resolve(self) -> None:
        self._resolved = True
        trimmed = _trim_overlap(self._prior, self._buffer)
        self._buffer = ""
        self._emit(trimmed)

    def _emit(self, text: str) -> None:
        if text:
            self._writer({"kind": "token", "content": text})
            self.emitted += text

    def flush(self) -> str:
        if not self._resolved:
            self._resolve()
        return self.emitted


def _stitch_writer(
    real_writer: Callable[[dict[str, Any]], None], prior: str
) -> tuple[Callable[[dict[str, Any]], None], _ContinuationStitcher]:
    stitcher = _ContinuationStitcher(prior, real_writer)

    def wrapped(chunk: dict[str, Any]) -> None:
        if isinstance(chunk, dict) and chunk.get("kind") == "token":
            stitcher.feed_token(str(chunk.get("content", "")))
        else:
            real_writer(chunk)

    return wrapped, stitcher


def _continuation_prompt(prior_tail: str) -> str:
    return (
        "Your previous message was cut off by the output limit. Continue it from exactly where it stopped — "
        "resume mid-sentence or mid-structure if needed. Do not repeat any text you already wrote, do not add a "
        "preamble, recap, or closing remark, and do not restate headings or list markers already shown. If you were "
        "inside a code block, table, or list, continue it seamlessly.\n\n"
        f"The end of what you have written so far (continue immediately after this):\n{prior_tail}"
    )


async def _auto_continue_answer(
    model: ChatModel,
    context_messages: list[BaseMessage],
    system_prompt: str,
    response: str,
    finish_reason: str | None,
    config: RunnableConfig,
    writer: Callable[[dict[str, Any]], None] | None,
    *,
    allow_reserve: bool = False,
) -> tuple[str, str, bool, tuple[dict[str, Any], ...]]:
    """Auto-continue an output-limit-truncated answer, stitching the pieces.

    Loops up to ``CHAT_LLM_MAX_CONTINUATIONS`` while the model keeps hitting the
    length limit, trims the repeated overlap at each seam, and stops early if a
    continuation adds no new text (the no-progress / anti-loop guard) or the
    stitched response would exceed ``CHAT_LLM_MAX_RESPONSE_CHARS``. Returns the
    full response, the text appended (so the caller can extend ``streamed``),
    whether it is *still* truncated after the budget, and any reasoning details.
    Only runs when streaming (``writer`` set); sub-agent/worker turns skip it.
    """
    max_loops = settings.CHAT_LLM_MAX_CONTINUATIONS
    max_chars = settings.CHAT_LLM_MAX_RESPONSE_CHARS
    details: list[dict[str, Any]] = []
    appended = ""
    loops = 0
    while (
        writer is not None
        and max_loops > 0
        and _is_output_limit_finish_reason(finish_reason)
        and loops < max_loops
        and (max_chars <= 0 or len(response) < max_chars)
    ):
        loops += 1
        prior_tail = response[-_CONTINUATION_CONTEXT_CHARS:]
        continuation_messages: list[BaseMessage] = [
            *context_messages,
            AIMessage(content=response, id=f"msg_{uuid.uuid4().hex}"),
            HumanMessage(content=_continuation_prompt(prior_tail), id=f"msg_{uuid.uuid4().hex}"),
        ]
        stitch_writer, stitcher = _stitch_writer(writer, response)
        turn = await _run_llm_tool_turn(
            model,
            system_prompt,
            continuation_messages,
            [],
            config,
            stitch_writer,
            allow_reserve=allow_reserve,
            phase="continuation",
        )
        added = stitcher.flush()
        details.extend(turn.details)
        finish_reason = turn.finish_reason
        if not added.strip():
            break  # no progress -> stop rather than loop
        response += added
        appended += added
    still_truncated = _is_output_limit_finish_reason(finish_reason)
    return response, appended, still_truncated, tuple(details)


@dataclass(frozen=True)
class _ConfirmResolution:
    # "run": ``to_run`` holds approved confirmations to execute now.
    # "wait": still pending approvals in the batch — surface message, don't run.
    # "abort": denied/expired/not-found/already-executed — surface message, don't run.
    kind: Literal["run", "wait", "abort"]
    message: str = ""


async def _collect_confirmations_to_run(
    confirmation_id: str,
    authed_user: CurrentUser,
    client_thread_id: str | None,
) -> tuple[list[ActionConfirmation], _ConfirmResolution]:
    """Validate a confirmation and collect the approved batch to execute.

    Centralizes the security-sensitive checks (ownership, expiry, approval,
    batch completeness) shared by the single-agent resume path and the
    orchestrated mid-plan resume path, so the rules cannot drift between them.
    """
    confirmation = await report_store.get_action_confirmation(confirmation_id, user_id=authed_user.user.user_id)
    if confirmation is None:
        return [], _ConfirmResolution("abort", "Seizu could not find that action confirmation.")
    if confirmation.source != "chat" or confirmation.session_key != client_thread_id:
        return [], _ConfirmResolution(
            "abort", "That action confirmation does not belong to this chat thread, so Seizu did not run it."
        )
    if action_confirmations.is_expired(confirmation) and confirmation.status != "executed":
        return [], _ConfirmResolution("abort", "That action confirmation has expired, so Seizu did not run it.")
    if confirmation.status not in ("approved", "executed"):
        return [], _ConfirmResolution("abort", "That action is not approved, so Seizu did not run it.")

    # Collect the full batch — all confirmations that share the same batch_id.
    # If this is a legacy confirmation with no batch_id, run only it.
    batch_id = confirmation.batch_id
    if batch_id:
        batch = await report_store.list_batch_action_confirmations(
            user_id=authed_user.user.user_id,
            batch_id=batch_id,
        )
        batch = [c for c in batch if c.source == "chat" and c.session_key == client_thread_id]
        pending = [c for c in batch if c.status == "pending" and not action_confirmations.is_expired(c)]
        if pending:
            n = len(pending)
            noun = "approval" if n == 1 else "approvals"
            return [], _ConfirmResolution(
                "wait",
                f"Waiting for {n} more {noun} before proceeding. "
                "Use the confirmation panel or URLs to approve the remaining actions.",
            )
        denied = [c for c in batch if c.status == "denied"]
        # Only count a confirmation as blocking-expired when it still needed a
        # decision — executed items have already run and must not abort the batch.
        expired = [c for c in batch if c.status in ("pending", "approved") and action_confirmations.is_expired(c)]
        if denied or expired:
            reason = "denied" if denied else "expired"
            return [], _ConfirmResolution(
                "abort", f"One or more actions in this approval batch were {reason}, so Seizu did not run the batch."
            )
        to_run = [c for c in batch if c.status == "approved"]
    else:
        to_run = [confirmation] if confirmation.status == "approved" else []

    if not to_run:
        return [], _ConfirmResolution("abort", "All actions in this batch have already been executed.")
    return to_run, _ConfirmResolution("run")


async def _execute_confirmations(
    to_run: list[ActionConfirmation],
    authed_user: CurrentUser,
) -> tuple[list[tuple[str, str]], list[str], list[dict[str, Any]]]:
    """Atomically claim and execute approved confirmations.

    Returns ``(outcomes, errors, detail_events)`` where ``outcomes`` are
    ``(tool_name, result_text)`` pairs. The claim step ensures each approved
    action runs at most once even under concurrent resumes.
    """
    outcomes: list[tuple[str, str]] = []  # (tool_name, result_text)
    errors: list[str] = []
    detail_events: list[dict[str, Any]] = []

    async def _run_one(c: ActionConfirmation) -> None:
        claimed = await report_store.claim_action_confirmation_for_execution(
            c.confirmation_id,
            authed_user.user.user_id,
        )
        if claimed is None:
            errors.append(f"`{c.tool_name}`: action was already executed or is no longer approved")
            return
        outcome = await mcp_runtime.call_tool_for_chat(
            authed_user,
            c.tool_name,
            c.arguments,
            gate_permission=Permission.CHAT_TOOLS_CALL,
            chat_safe_only=True,
            include_chat_only=True,
            result_max_rows=settings.CHAT_TOOL_RESULT_MAX_ROWS,
            result_max_bytes=settings.CHAT_TOOL_RESULT_MAX_BYTES,
        )
        if outcome.blocked is not None:
            errors.append(f"`{c.tool_name}`: {_blocked_tool_call_body(outcome.text)}")
            detail_events.append(_confirmation_tool_detail_data(c, outcome.text, status="blocked"))
        elif error_text := _tool_result_error_text(outcome.text):
            errors.append(f"`{c.tool_name}`: {error_text}")
            detail_events.append(_confirmation_tool_detail_data(c, outcome.text, status="blocked"))
        else:
            outcomes.append((c.tool_name, outcome.text))
            detail_events.append(_confirmation_tool_detail_data(c, outcome.text, status="completed"))

    max_parallel = settings.CHAT_LLM_MAX_PARALLEL_TOOL_CALLS
    semaphore = asyncio.Semaphore(max_parallel) if max_parallel > 0 else None

    async def _run_one_limited(c: ActionConfirmation) -> None:
        if semaphore is None:
            await _run_one(c)
            return
        async with semaphore:
            await _run_one(c)

    await asyncio.gather(*(_run_one_limited(c) for c in to_run))
    return outcomes, errors, detail_events


async def _resume_confirmed_tool_turn(
    state: ChatState,
    config: RunnableConfig,
    current_user: CurrentUser | None,
    confirmation_id: str,
) -> ChatState:
    writer = get_stream_writer()
    if current_user is None:
        response = "Seizu could not resume the action because the current user is not authenticated."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)

    # Capture as a non-optional type for the shared helpers below.
    authed_user: CurrentUser = current_user
    client_thread_id = _client_thread_id_from_config(config)
    to_run, resolution = await _collect_confirmations_to_run(confirmation_id, authed_user, client_thread_id)
    if resolution.kind != "run":
        writer({"kind": "token", "content": resolution.message})
        return _chat_state_with_ai_response(state, resolution.message)

    action_word = "action" if len(to_run) == 1 else "actions"
    writer({"kind": "token", "content": f"Running approved {action_word}...\n\n"})

    outcomes, errors, detail_events = await _execute_confirmations(to_run, authed_user)
    for detail_data in detail_events:
        writer({"kind": "detail", "id": f"detail_{uuid.uuid4().hex}", "data": detail_data})

    if errors and not outcomes:
        response = "The approved action(s) could not be executed:\n" + "\n".join(f"- {e}" for e in errors)
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response, details=detail_events)

    provider = _chat_provider()
    combined_results = "\n\n".join(f"`{name}`:\n{_truncate_text(text, 6000)}" for name, text in outcomes)
    if errors:
        combined_results += "\n\nThe following actions could not be executed:\n" + "\n".join(f"- {e}" for e in errors)

    if provider == "mock":
        response = "Approved action(s) completed."
        if errors:
            response += "\n\nSome approved actions could not be executed."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response, details=detail_events)

    model = get_chat_model()
    n_ran = len(outcomes)
    summary_note = (
        "The user approved the pending Seizu actions. Summarize the completed tool results concisely."
        if n_ran > 1
        else "The user approved the pending Seizu action. Summarize the completed tool result concisely."
    )
    system_prompt = _combined_system_prompt(
        build_system_prompt(provider, current_user),
        f"{summary_note} Do not call additional tools in this resume turn.",
    )
    context = [
        *_llm_context_messages(state["messages"]),
        HumanMessage(
            content=f"Approved Seizu tool(s) ran with result(s):\n\n{_truncate_text(combined_results, 12000)}",
            id=f"msg_{uuid.uuid4().hex}",
        ),
    ]
    turn_result = await _run_llm_tool_turn(model, system_prompt, context, [], config, writer)
    detail_events.extend(turn_result.details)
    response = message_text(turn_result.message.content) or (
        f"Approved action(s) completed.\n\nResult:\n{_truncate_text(combined_results, 4000)}"
    )
    response, hit_output_limit = _append_output_limit_notice(response, turn_result.finish_reason)
    streamed = turn_result.streamed
    if response and response != streamed:
        if not streamed:
            writer({"kind": "token", "content": response})
        elif response.startswith(streamed):
            writer({"kind": "token", "content": _stream_tail(streamed, response[len(streamed) :])})
        else:
            writer({"kind": "token", "content": _stream_tail(streamed, response, separator="\n\n")})
    if hit_output_limit:
        writer({"kind": "finish_reason", "finish_reason": "length"})
    return _chat_state_with_ai_response(state, response, details=detail_events, output_limit=hit_output_limit)


def _chat_state_with_ai_response(
    state: ChatState,
    response: str,
    *,
    details: list[dict[str, Any]] | None = None,
    output_limit: bool = False,
) -> ChatState:
    response_metadata: dict[str, Any] = {}
    if details:
        response_metadata["seizu_details"] = details
    if output_limit:
        response_metadata["seizu_output_limit"] = True
    ai_message = AIMessage(
        content=response,
        id=f"msg_{uuid.uuid4().hex}",
        response_metadata=response_metadata,
    )
    return {"messages": [*_trim_messages(state["messages"], ai_message), ai_message]}


# DeepSeek-family models sometimes emit their tool-call protocol tokens as
# literal text instead of the API parsing them into structured ``tool_calls``
# (observed with deepseek-v4-pro, whose markup looks like ``<｜｜DSML｜｜invoke
# name=...>``). Every such marker starts ``<`` or ``</`` immediately followed by
# the fullwidth vertical bar U+FF5C, which never appears that way in legitimate
# assistant markdown — so it is a reliable, low-false-positive signal.
# Note: ｜ is the fullwidth bar, NOT an ASCII '|' — an ASCII pipe here would
# turn the pattern into the alternation "</" or "" and match everywhere.
_TOOL_MARKUP_RE = re.compile("</?｜")


def _strip_tool_markup(text: str) -> str:
    """Drop everything from the first leaked tool-call marker onward."""
    match = _TOOL_MARKUP_RE.search(text)
    return text[: match.start()] if match else text


# Seizu tool/skill names follow the ``group__action`` convention, which never
# occurs in ordinary prose — so scanning the leaked region for it recovers the
# tool the model tried to call without parsing the proprietary markup grammar.
_TOOL_NAME_RE = re.compile(r"[a-z][a-z0-9_]*__[a-z0-9_]+")


def _leaked_tool_names(text: str) -> tuple[str, ...]:
    """Best-effort tool names the model tried to call in leaked markup."""
    match = _TOOL_MARKUP_RE.search(text)
    region = text[match.start() :] if match else text
    seen: list[str] = []
    for name in _TOOL_NAME_RE.findall(region):
        if name not in seen:
            seen.append(name)
    return tuple(seen[:5])


class _ToolMarkupFilter:
    """Withhold leaked tool-call protocol markup from the streamed output.

    Holds back a trailing partial ``<...`` so a marker split across stream chunks
    is never shown, and once a marker appears suppresses everything after it.
    ``detected`` reports whether any markup was seen.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self.detected = False

    def feed(self, delta: str) -> str:
        if self.detected:
            return ""
        self._buffer += delta
        match = _TOOL_MARKUP_RE.search(self._buffer)
        if match:
            self.detected = True
            safe = self._buffer[: match.start()]
            self._buffer = ""
            return safe
        # Hold back only a dangling unclosed ``<...`` (a possible marker prefix).
        cut = self._buffer.rfind("<")
        if cut != -1 and ">" not in self._buffer[cut:]:
            safe, self._buffer = self._buffer[:cut], self._buffer[cut:]
            return safe
        safe, self._buffer = self._buffer, ""
        return safe

    def flush(self) -> str:
        if self.detected:
            return ""
        safe, self._buffer = self._buffer, ""
        return safe


async def _run_llm_tool_turn(
    model: ChatModel,
    system_prompt: str,
    messages: list[BaseMessage],
    tools: list[ChatToolSpec],
    config: RunnableConfig,
    writer: Callable[[dict[str, Any]], None] | None = None,
    *,
    allow_reserve: bool = False,
    phase: str = "worker",
    max_output_tokens: int | None = None,
) -> LLMTurnResult:
    """Run one LLM turn, streaming text deltas via *writer* as they arrive.

    Streaming policy: text deltas stream as they arrive, even when tools are
    available. Tool-call chunks are still detected so the caller can avoid
    treating them as user-visible text, but the visible response should progress
    while details and tool activity are emitted.

    Returns the merged message, the concatenation of deltas already shipped,
    and any provider finish reason observed while streaming. The streamed text
    lets the caller avoid re-emitting the final response when it matches.
    """
    runnable = model
    bind_tools = getattr(model, "bind_tools", None)
    tool_schemas = [_langchain_tool_schema(tool) for tool in tools]
    if tools and callable(bind_tools):
        runnable = bind_tools(tool_schemas)

    controller = budget_controller_from_config(config)
    estimated_input = estimate_tokens(model, system_prompt, messages, tool_schemas)
    estimated_output = max_output_tokens if max_output_tokens is not None else settings.CHAT_LLM_MAX_TOKENS
    reservation = None
    if controller is not None:
        reservation = await controller.reserve(
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=max(1, estimated_output),
            estimated_cost_usd=usage_cost_usd(model, estimated_input, max(1, estimated_output)),
            allow_reserve=allow_reserve,
            phase=phase,
        )

    merged: Any | None = None
    reasoning_detail_id = f"detail_{uuid.uuid4().hex}"
    reasoning_text = ""
    reasoning_detail_data: dict[str, Any] | None = None
    reasoning_detail_started = False
    streamed = ""
    finish_reason: str | None = None
    stream_text = writer is not None
    markup_filter = _ToolMarkupFilter()
    try:
        invocation_kwargs = {"max_tokens": max_output_tokens} if max_output_tokens is not None else {}
        async for chunk in runnable.astream(
            [SystemMessage(content=system_prompt), *messages],
            config=config,
            **invocation_kwargs,
        ):
            finish_reason = _chunk_finish_reason(chunk) or finish_reason
            reasoning_delta = _chunk_reasoning_delta(chunk)
            if reasoning_delta and writer is not None:
                reasoning_text = _truncate_text(f"{reasoning_text}{reasoning_delta}", 6000)
                reasoning_detail_data = {
                    "kind": "thinking",
                    "title": "Thinking",
                    "status": "completed",
                    "body": reasoning_text,
                }
                if not reasoning_detail_started:
                    reasoning_detail_started = True
                    writer(
                        {
                            "kind": "detail",
                            "id": reasoning_detail_id,
                            "data": {
                                "kind": "thinking",
                                "title": "Thinking",
                                "status": "running",
                            },
                        }
                    )
            if stream_text and writer is not None:
                delta = message_text(getattr(chunk, "content", ""))
                if delta:
                    safe = markup_filter.feed(delta)
                    if safe:
                        writer({"kind": "token", "content": safe})
                        streamed += safe
            merged = chunk if merged is None else merged + chunk
    except Exception:
        if controller is not None and reservation is not None:
            await controller.release(reservation)
        raise
    if stream_text and writer is not None:
        tail = markup_filter.flush()
        if tail:
            writer({"kind": "token", "content": tail})
            streamed += tail
    if reasoning_detail_started and reasoning_detail_data is not None and writer is not None:
        writer({"kind": "detail", "id": reasoning_detail_id, "data": reasoning_detail_data})

    merged_text = message_text(getattr(merged, "content", "")) if merged is not None else ""
    usage = getattr(merged, "usage_metadata", None)
    usage_estimated = not isinstance(usage, dict) or not usage.get("total_tokens")
    input_tokens = int(usage.get("input_tokens", 0)) if isinstance(usage, dict) else 0
    output_tokens = int(usage.get("output_tokens", 0)) if isinstance(usage, dict) else 0
    if usage_estimated:
        input_tokens = estimated_input
        output_tokens = estimate_tokens(model, "", [AIMessage(content=merged_text)], [])
    cost_usd = usage_cost_usd(model, input_tokens, output_tokens)
    if controller is not None and reservation is not None:
        await controller.commit(
            reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            usage_estimated=usage_estimated,
        )
    tool_markup_leaked = markup_filter.detected or bool(_TOOL_MARKUP_RE.search(merged_text))
    leaked_tool_names = _leaked_tool_names(merged_text) if tool_markup_leaked else ()

    if isinstance(merged, AIMessage):
        if tool_markup_leaked:
            merged.content = _strip_tool_markup(merged_text)
        return LLMTurnResult(
            message=_strip_reasoning_context(merged),
            streamed=streamed,
            finish_reason=finish_reason or _chunk_finish_reason(merged),
            details=(reasoning_detail_data,) if reasoning_detail_data else (),
            tool_markup_leaked=tool_markup_leaked,
            leaked_tool_names=leaked_tool_names,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            usage_estimated=usage_estimated,
        )
    fallback = AIMessage(
        content=_strip_tool_markup(merged_text),
        tool_calls=list(getattr(merged, "tool_calls", []) or []),
        invalid_tool_calls=list(getattr(merged, "invalid_tool_calls", []) or []),
        id=getattr(merged, "id", None),
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    return LLMTurnResult(
        message=_strip_reasoning_context(fallback),
        streamed=streamed,
        finish_reason=finish_reason or _chunk_finish_reason(merged),
        details=(reasoning_detail_data,) if reasoning_detail_data else (),
        tool_markup_leaked=tool_markup_leaked,
        leaked_tool_names=leaked_tool_names,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        usage_estimated=usage_estimated,
    )


def _strip_reasoning_context(message: AIMessage) -> AIMessage:
    """Normalize an assistant message before it is reused as model context.

    LiteLLM surfaces reasoning two ways: ``additional_kwargs["reasoning_content"]``
    (DeepSeek/OpenAI shape) and injected ``{"type": "thinking"}`` blocks in list
    content (Anthropic shape). During streaming, chunk-merge concatenates the
    list-shaped reasoning with the plain-string answer delta into a *mixed* list
    (thinking dicts plus bare answer-text strings). Some providers reject that
    when it is re-sent in the tool loop — e.g. DeepSeek's content-list-to-str
    conversion calls ``.get("text")`` on every element and crashes on a bare str.

    Reasoning is normally a UI-only diagnostic (streamed separately as a thinking
    detail), but DeepSeek thinking mode requires an assistant message that
    performed a tool call to be replayed with its original ``reasoning_content``.
    Therefore: flatten list content back to the plain answer text for every
    provider, strip reasoning from normal assistant messages, and preserve or
    reconstruct ``reasoning_content`` only on assistant tool-call messages.
    """
    has_tool_calls = bool(message.tool_calls or message.additional_kwargs.get("tool_calls"))
    reasoning_content = _chunk_reasoning_delta(message)
    additional_kwargs = dict(message.additional_kwargs)
    if has_tool_calls:
        if reasoning_content:
            additional_kwargs["reasoning_content"] = reasoning_content
    else:
        additional_kwargs.pop("reasoning_content", None)
    message.additional_kwargs = additional_kwargs
    if isinstance(message.content, list):
        message.content = message_text(message.content)
    return message


def _chunk_reasoning_delta(chunk: Any) -> str:
    additional_kwargs = getattr(chunk, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        reasoning_content = additional_kwargs.get("reasoning_content")
        if isinstance(reasoning_content, str):
            return reasoning_content
    content = getattr(chunk, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in ("thinking", "reasoning"):
                continue
            text = item.get("text") or item.get("thinking") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    return ""


_OUTPUT_LIMIT_NOTICE = (
    "\n\n> Response stopped because the model hit its output limit. Ask me to continue from here if you need the rest."
)
_OUTPUT_LIMIT_SUMMARY_NOTICE = (
    "\n\nSeizu completed tool work before the cutoff, but the final answer may be incomplete."
)


def _append_output_limit_notice(
    response: str,
    finish_reason: str | None,
    action_summaries: list[str] | None = None,
) -> tuple[str, bool]:
    if not _is_output_limit_finish_reason(finish_reason):
        return response, False
    if _OUTPUT_LIMIT_NOTICE in response:
        return response, True
    summary_notice = _OUTPUT_LIMIT_SUMMARY_NOTICE if action_summaries else ""
    return f"{response.rstrip()}{_OUTPUT_LIMIT_NOTICE}{summary_notice}", True


def _is_output_limit_finish_reason(finish_reason: str | None) -> bool:
    if not finish_reason:
        return False
    normalized = finish_reason.strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in {
        "length",
        "max_tokens",
        "max_output_tokens",
        "model_length",
        "output_limit",
        "token_limit",
    }


def _chunk_finish_reason(chunk: Any) -> str | None:
    if chunk is None:
        return None
    if isinstance(chunk, dict):
        return _finish_reason_from_mapping(chunk)
    for attr in ("response_metadata", "generation_info", "additional_kwargs"):
        metadata = getattr(chunk, attr, None)
        if isinstance(metadata, dict):
            finish_reason = _finish_reason_from_mapping(metadata)
            if finish_reason:
                return finish_reason
    direct = getattr(chunk, "finish_reason", None) or getattr(chunk, "finishReason", None)
    return direct if isinstance(direct, str) and direct else None


def _finish_reason_from_mapping(mapping: dict[str, Any]) -> str | None:
    for key in (
        "finish_reason",
        "finishReason",
        "stop_reason",
        "completion_reason",
        "done_reason",
    ):
        value = mapping.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _trim_inner_loop_messages(messages: list[BaseMessage], *, max_chars: int) -> list[BaseMessage]:
    """Cap the inner-turn message list by total character count.

    Tool results are bounded per call by ``CHAT_TOOL_RESULT_MAX_BYTES``, but
    nothing else stops the loop from accumulating up to
    ``CHAT_LLM_MAX_AUTO_ACTIONS`` × that cap into the next LLM call. This drops
    oldest AI+ToolMessage turn pairs from the head once the accumulated text
    exceeds the cap, keeping the user's original turn at index 0 (when it is a
    ``HumanMessage``) and the most recent tool exchange intact.
    """
    if max_chars <= 0 or len(messages) <= 4:
        return messages
    total = sum(_message_context_size(m) for m in messages)
    if total <= max_chars:
        return messages

    preserve_head = isinstance(messages[0], HumanMessage)
    head: list[BaseMessage] = [messages[0]] if preserve_head else []
    body = messages[1:] if preserve_head else messages[:]

    # Drop AIMessage + its trailing ToolMessages as a unit; orphaning tool
    # results breaks every provider's tool-call protocol.
    while body and total > max_chars:
        if not isinstance(body[0], AIMessage):
            dropped = body.pop(0)
            total -= _message_context_size(dropped)
            continue
        dropped = body.pop(0)
        total -= _message_context_size(dropped)
        while body and isinstance(body[0], ToolMessage):
            tool_dropped = body.pop(0)
            total -= _message_context_size(tool_dropped)

    return [*head, *body]


def _message_context_size(message: BaseMessage) -> int:
    size = len(message_text(getattr(message, "content", "")))
    if isinstance(message, AIMessage):
        if message.tool_calls:
            size += len(_json_dump(message.tool_calls))
        if message.invalid_tool_calls:
            size += len(_json_dump(message.invalid_tool_calls))
        raw_tool_calls = message.additional_kwargs.get("tool_calls")
        if raw_tool_calls:
            size += len(_json_dump(raw_tool_calls))
    if isinstance(message, ToolMessage):
        size += len(message.tool_call_id)
        if message.name:
            size += len(message.name)
    return size


_PROVIDER_TOOL_NAME_MAX_LEN = 64
_PROVIDER_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def _with_provider_tool_names(specs: list[ChatToolSpec]) -> list[ChatToolSpec]:
    """Attach provider-safe names while keeping Seizu/MCP names canonical.

    Most Seizu tool names are already provider-compatible. Very long names are
    mapped for this LLM turn only; execution and user-visible status continue to
    use ``spec.name``.
    """
    used: set[str] = set()
    mapped: list[ChatToolSpec] = []
    for spec in specs:
        llm_name = _provider_tool_name(spec, used)
        used.add(llm_name)
        mapped.append(replace(spec, llm_name=llm_name if llm_name != spec.name else None))
    return mapped


def _provider_tool_name(spec: ChatToolSpec, used: set[str]) -> str:
    if _PROVIDER_TOOL_NAME_RE.fullmatch(spec.name) and spec.name not in used:
        return spec.name

    prefix = "seizu_s" if spec.kind == "skill" else "seizu_t"
    digest = hashlib.sha256(spec.name.encode("utf-8")).hexdigest()[:10]
    slug = _provider_tool_name_slug(spec.name)
    base = f"{prefix}_{digest}"
    available = _PROVIDER_TOOL_NAME_MAX_LEN - len(base) - 1
    candidate = f"{base}_{slug[:available]}" if available > 0 and slug else base
    if candidate not in used:
        return candidate

    for index in range(1, 1000):
        suffix = f"_{index}"
        trimmed = candidate[: _PROVIDER_TOOL_NAME_MAX_LEN - len(suffix)]
        fallback = f"{trimmed}{suffix}"
        if fallback not in used:
            return fallback
    raise RuntimeError("Unable to allocate unique LLM tool name")


def _provider_tool_name_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_-").lower()
    if not slug:
        return ""
    if not re.match(r"^[A-Za-z_]", slug):
        slug = f"t_{slug}"
    return slug


def _llm_tool_name(tool: ChatToolSpec) -> str:
    return tool.llm_name or tool.name


def _llm_tool_description(tool: ChatToolSpec) -> str:
    description = tool.description or tool.name
    if tool.llm_name and tool.llm_name != tool.name:
        return f"Seizu {tool.kind} `{tool.name}`. {description}"
    return description


def _langchain_tool_schema(tool: ChatToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": _llm_tool_name(tool),
            "description": _llm_tool_description(tool),
            "parameters": _json_schema_object(tool.input_schema),
        },
    }


def _json_schema_object(schema: dict[str, Any]) -> dict[str, Any]:
    result = dict(schema)
    result.setdefault("type", "object")
    result.setdefault("properties", {})
    return result


def _skill_tool_specs(skills: list[Prompt]) -> list[ChatToolSpec]:
    return [
        ChatToolSpec(
            name=prompt.name,
            kind="skill",
            description=prompt.description or f"{prompt.name} skill",
            input_schema=_prompt_input_schema(prompt),
        )
        for prompt in skills
    ]


def _terminal_specs(post_action: bool) -> list[ChatToolSpec]:
    """The respond_to_user finish tool, exposed only after an action has run."""
    if not post_action:
        return []
    return [
        ChatToolSpec(
            name=_FINAL_ANSWER_TOOL,
            kind="tool",
            description=(
                "Deliver your complete final answer to the user and end the turn. Call this once you have enough "
                "information from the tools/skills you already ran. Put the full user-facing answer in `answer`; do "
                "not call any other tool in the same step. If you still need live data, call that tool instead."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "The complete, user-facing final answer for this turn.",
                    }
                },
                "required": ["answer"],
            },
        )
    ]


def _final_answer_text(request: ToolCallRequest) -> str:
    answer = request.arguments.get("answer")
    return answer.strip() if isinstance(answer, str) else ""


def _mcp_tool_specs(tools: list[Tool]) -> list[ChatToolSpec]:
    return [
        ChatToolSpec(
            name=tool.name,
            kind="tool",
            description=tool.description or f"{tool.name} tool",
            input_schema=tool.inputSchema if isinstance(tool.inputSchema, dict) else {"type": "object"},
        )
        for tool in tools
    ]


async def _list_chat_tools(current_user: CurrentUser | None) -> list[Tool]:
    return await mcp_runtime.list_tools_for_user(
        current_user,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        include_chat_only=True,
    )


async def _list_chat_prompts(current_user: CurrentUser | None) -> list[Prompt]:
    return await mcp_runtime.list_prompts_for_user(
        current_user,
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )


def _prompt_input_schema(prompt: Prompt) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for argument in prompt.arguments or []:
        properties[argument.name] = {
            "type": "string",
            "description": argument.description or argument.name,
        }
        if argument.required:
            required.append(argument.name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _tool_call_requests(message: AIMessage, specs: list[ChatToolSpec]) -> list[ToolCallRequest]:
    by_name = {_llm_tool_name(spec): spec for spec in specs}
    requests: list[ToolCallRequest] = []
    for index, call in enumerate(getattr(message, "tool_calls", []) or []):
        llm_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        if not isinstance(llm_name, str) or llm_name not in by_name:
            continue
        spec = by_name[llm_name]
        raw_args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
        args = raw_args if isinstance(raw_args, dict) else {}
        call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
        requests.append(
            ToolCallRequest(
                id=str(call_id or f"call_{index}_{uuid.uuid4().hex}"),
                name=spec.name,
                arguments=args,
                spec=spec,
            )
        )
    return requests


def _unavailable_tool_call_results(message: AIMessage, specs: list[ChatToolSpec]) -> list[ToolCallResult]:
    available_names = {_llm_tool_name(spec) for spec in specs}
    results: list[ToolCallResult] = []
    for index, call in enumerate(getattr(message, "tool_calls", []) or []):
        name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
        if not isinstance(name, str) or name in available_names:
            continue
        raw_args = call.get("args") if isinstance(call, dict) else getattr(call, "args", {})
        args = raw_args if isinstance(raw_args, dict) else {}
        call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
        request = ToolCallRequest(
            id=str(call_id or f"call_{index}_{uuid.uuid4().hex}"),
            name=name,
            arguments=args,
            spec=ChatToolSpec(
                name=name,
                kind="tool",
                description="Unavailable in this chat context",
                input_schema={"type": "object"},
            ),
        )
        results.append(
            ToolCallResult(
                request=request,
                content=_json_dump({"error": f"Tool '{name}' is not available in this chat context"}),
                blocked=ChatBlockReason.NOT_AVAILABLE,
            )
        )
    return results


def _tool_request_key(request: ToolCallRequest) -> str:
    return f"{request.spec.kind}:{request.name}:{_json_dump(request.arguments)}"


async def _run_tool_call_batch(
    requests: list[ToolCallRequest],
    current_user: CurrentUser | None,
    session_key: str | None = None,
    batch_id: str | None = None,
    bypass_confirmations: bool = False,
) -> list[ToolCallResult]:
    max_parallel = settings.CHAT_LLM_MAX_PARALLEL_TOOL_CALLS
    semaphore = asyncio.Semaphore(max_parallel) if max_parallel > 0 else None

    async def run_one(request: ToolCallRequest) -> ToolCallResult:
        if semaphore is None:
            return await _run_tool_call(
                request,
                current_user,
                session_key=session_key,
                batch_id=batch_id,
                bypass_confirmations=bypass_confirmations,
            )
        async with semaphore:
            return await _run_tool_call(
                request,
                current_user,
                session_key=session_key,
                batch_id=batch_id,
                bypass_confirmations=bypass_confirmations,
            )

    return list(await asyncio.gather(*(run_one(request) for request in requests)))


def _ai_message_for_tool_results(message: AIMessage, results: list[ToolCallResult]) -> AIMessage:
    """Return an assistant message whose tool calls match the ToolMessages emitted.

    Providers require every assistant ``tool_call_id`` to be followed by exactly
    one tool response. Seizu may execute only a subset of the model's requested
    calls (auto-action limit, repeated-call filtering), so never pass through the
    original full ``tool_calls`` list when building the next in-turn context.
    """
    result_ids = {result.request.id for result in results}
    tool_calls = [
        call for call in (message.tool_calls or []) if isinstance(call, dict) and str(call.get("id", "")) in result_ids
    ]
    additional_kwargs = {key: value for key, value in message.additional_kwargs.items() if key != "tool_calls"}
    raw_tool_calls = message.additional_kwargs.get("tool_calls")
    if isinstance(raw_tool_calls, list):
        filtered_raw = [
            call for call in raw_tool_calls if isinstance(call, dict) and str(call.get("id", "")) in result_ids
        ]
        if filtered_raw:
            additional_kwargs["tool_calls"] = filtered_raw
    return AIMessage(
        content=message.content,
        id=message.id,
        additional_kwargs=additional_kwargs,
        response_metadata=message.response_metadata,
        tool_calls=tool_calls,
    )


def _confirmation_batch_id_for_requests(requests: list[ToolCallRequest]) -> str | None:
    """Only group true multi-action approvals into a confirmation batch.

    A one-action "batch" creates stale/confusing batch links after execution,
    while adding no value over the confirmation panel's single-item approval.
    """
    return report_store.generate_id() if len(requests) > 1 else None


async def _run_tool_call(
    request: ToolCallRequest,
    current_user: CurrentUser | None,
    *,
    session_key: str | None = None,
    batch_id: str | None = None,
    bypass_confirmations: bool = False,
) -> ToolCallResult:
    if request.spec.kind == "skill":
        string_arguments = {key: str(value) for key, value in request.arguments.items()}
        outcome = await mcp_runtime.render_prompt_for_chat(
            current_user,
            request.name,
            string_arguments,
            gate_permission=Permission.CHAT_SKILLS_CALL,
        )
        content = _idempotent_success_content(request, outcome.text)
        return ToolCallResult(
            request=request,
            content=content,
            blocked=outcome.blocked,
            tools_required=outcome.tools_required,
        )

    call_kwargs: dict[str, Any] = {
        "gate_permission": Permission.CHAT_TOOLS_CALL,
        "chat_safe_only": True,
        "include_chat_only": True,
        "result_max_rows": settings.CHAT_TOOL_RESULT_MAX_ROWS,
        "result_max_bytes": settings.CHAT_TOOL_RESULT_MAX_BYTES,
    }
    if bypass_confirmations:
        # Bypass mode: no confirmation records are created; mcp_runtime
        # enforces chat:bypass_permissions and audit-logs each execution.
        call_kwargs["bypass_confirmations"] = True
    else:
        call_kwargs.update(
            {
                "confirmation_source": "chat",
                "confirmation_session_key": session_key,
                "confirmation_batch_id": batch_id,
            }
        )
    outcome = await mcp_runtime.call_tool_for_chat(
        current_user,
        request.name,
        request.arguments,
        **call_kwargs,
    )
    return ToolCallResult(
        request=request,
        content=_idempotent_success_content(request, outcome.text),
        blocked=outcome.blocked,
    )


def build_system_prompt(provider: str | None = None, current_user: CurrentUser | None = None) -> str:
    if settings.CHAT_LLM_SYSTEM_PROMPT:
        return settings.CHAT_LLM_SYSTEM_PROMPT

    provider_name = provider or _chat_provider()
    display_name = None
    if current_user is not None:
        display_name = current_user.user.display_name or current_user.user.preferred_username or current_user.user.email
    # Quote display_name via json.dumps so a malicious display value (which
    # users may control in some IdPs) cannot break out of the surrounding
    # narrative to inject prior-instruction-overriding text into the system
    # prompt. json.dumps escapes embedded quotes, newlines, and control chars.
    user_context = f"\nCurrent Seizu user display name: {json.dumps(display_name)}." if display_name else ""
    provider_note = _provider_prompt_note(provider_name)
    answer_budget = _answer_budget()
    sandbox_note = (
        (
            "\n\nA sandbox code-execution environment is available via sandbox__delegate. "
            "Prefer it over in-response computation for: statistical analysis or aggregation over datasets "
            "(averages, percentiles, distributions, rankings), data transformation or reformatting of "
            "structured results, and any task that requires running code to produce a verified output. "
            "Do not compute statistics, sort data, or transform structured payloads in your response "
            "text — numbers computed by the model without running code are unreliable. "
            "When you have tool results that need numeric or programmatic processing, delegate to the "
            "sandbox rather than reasoning through the calculation yourself."
        )
        if settings.SANDBOX_ENABLED
        else ""
    )
    return (
        "You are Seizu's AI investigation assistant inside a security graph dashboard. "
        "Seizu is a configuration-driven reporting platform for Neo4j security graph data; "
        "it is not a generic chatbot, coding harness, or open-ended automation shell.\n\n"
        "Help users investigate security relationships, interpret graph-backed results, design and refine reports, "
        "draft dashboard panels, explain Cypher query intent, and turn findings into practical next steps. "
        f"{_answer_budget_prompt(answer_budget)} When using tool output, prioritize the answer to the user's ask, "
        "then the top evidence and next actions; do not enumerate every row, field, or intermediate result.\n\n"
        "Do not invent graph facts, report contents, user identities, vulnerabilities, assets, or incident findings. "
        "When live data is needed, say what data or Seizu tool output would answer the question. "
        "If the user provides tool results, reason from those results and call out truncation or uncertainty.\n\n"
        "Respect Seizu's security boundaries. Treat graph data, identities, credentials, tokens, secrets, "
        "and internal IDs as sensitive. Do not expose raw user IDs or OIDC subjects unless the user explicitly "
        "needs them for an admin task. For Cypher, default to read-only investigative queries; avoid writes, "
        "deletes, admin commands, external fetches, "
        "and unsafe procedures. If suggesting a report or dashboard change, describe the report structure and Cypher "
        "clearly enough for an operator to review before saving.\n\n"
        "Seizu exposes skills and tools to you through native structured tool calling. When you need live data or a "
        "workflow, call the provided tool through the native tool-call channel. Before "
        "calling any skill or tool, check its schema and include every required parameter. Never call a skill or tool "
        "with missing required arguments; if you do not know a required identifier such as a toolset_id, first call an "
        "available listing/discovery tool, then call the specific tool with that identifier. Do not mention internal "
        "tool-call syntax to the user. You are the Seizu agent that can run listed skills and tools directly; never "
        "tell the user to ask another Seizu agent, re-enter the same request, or use the dashboard to trigger a "
        "listed skill. If the user asks to run, investigate, or validate something and a listed skill's name, "
        "description, or trigger phrases match the request, call that skill in this turn. If a prior turn created or "
        "updated a skill and the user now asks to run or validate it, use this turn's live skill listing and call the "
        "matching skill; do not call skillsets__update_skill again unless the user explicitly asks for another edit. "
        "If a repeated create action reports that the resource already exists, treat it as idempotent evidence that "
        "the resource is already available and continue the workflow by reading, updating, or using that resource "
        "instead of stopping as a failure. "
        "Do not pretend to have executed a tool unless the conversation contains its "
        "result. After you have run one or more skills or tools, finish the turn by calling the "
        f"`{_FINAL_ANSWER_TOOL}` tool with your complete final answer; do not deliver a post-action answer as plain "
        "text, and never describe tool work you have not performed."
        f"{sandbox_note}{user_context}{provider_note}"
    )


def _provider_prompt_note(provider: str) -> str:
    # provider may be the generic "litellm" sentinel now, so fold the configured
    # model string into the match (e.g. "anthropic/claude-...") and key the
    # family-specific note off whichever signal is present.
    hint = f"{provider} {settings.CHAT_LLM_MODEL}".lower()
    if "anthropic" in hint or "claude" in hint:
        return "\nFor Claude, keep the final answer direct and avoid prefilling or hidden chain-of-thought."
    if "gemini" in hint or "vertex" in hint:
        return "\nFor Gemini, preserve structured report suggestions as compact headings and bullet lists."
    if "deepseek" in hint:
        return "\nFor DeepSeek, keep reasoning concise and surface only the conclusion, evidence, and next action."
    if "openai" in hint or "gpt" in hint or "/o1" in hint or "/o3" in hint:
        return (
            "\nFor OpenAI, use a developer-instruction style: follow these Seizu constraints "
            "over generic assistant behavior."
        )
    return ""


def _answer_budget(max_tokens: int | None = None) -> AnswerBudget:
    tokens = settings.CHAT_LLM_MAX_TOKENS if max_tokens is None else max_tokens
    # The token cap includes formatting and non-word tokens, so budget visible
    # prose conservatively at roughly 0.3 words per output token.
    effective_tokens = tokens if tokens > 0 else 2048
    max_words = max(150, min(1600, round(effective_tokens * 0.3 / 50) * 50))
    min_words = max(75, round(max_words * 0.5 / 25) * 25)
    max_bullets = max(4, min(16, round(effective_tokens / 256)))
    max_tables = 1 if effective_tokens < 3072 else 2
    return AnswerBudget(
        min_words=min_words,
        max_words=max_words,
        max_bullets=max_bullets,
        max_tables=max_tables,
    )


def _answer_budget_prompt(budget: AnswerBudget) -> str:
    table_noun = "table" if budget.max_tables == 1 else "tables"
    table_count = "one" if budget.max_tables == 1 else str(budget.max_tables)
    return (
        "Prefer concise, evidence-oriented answers with clear assumptions and limits. "
        f"Given the configured output budget, keep normal answers under about {budget.max_words} words unless the "
        f"user explicitly asks for a full report. Use at most {budget.max_bullets} bullets and at most "
        f"{table_count} compact {table_noun}, only when tables are materially clearer than bullets."
    )


def _tool_call_user_summary(results: list[ToolCallResult]) -> str:
    if len(results) > 1:
        rendered_results = "\n\n".join(
            (
                f"- `{result.request.name}` with arguments `{_json_dump(result.request.arguments)}` returned:\n"
                f"{_truncate_text(result.content, 1800)}"
            )
            for result in results
        )
        return f"Seizu ran {len(results)} actions in parallel:\n\n{rendered_results}"
    result = results[0]
    action = "rendered skill" if result.request.spec.kind == "skill" else "ran tool"
    return (
        f"Seizu {action} `{result.request.name}` with arguments `{_json_dump(result.request.arguments)}`.\n\n"
        f"Result:\n{_truncate_text(result.content, 4000)}"
    )


def _tool_call_detail_data(result: ToolCallResult) -> dict[str, Any]:
    action = "Skill" if result.request.spec.kind == "skill" else "Tool"
    # A confirmation gate is a genuine wait (the UI shows it as "awaiting"); any
    # other block is a failure ("blocked").
    if result.blocked == ChatBlockReason.CONFIRMATION_REQUIRED:
        status = "awaiting"
    elif result.blocked is not None:
        status = "blocked"
    else:
        status = "completed"
    return {
        "kind": result.request.spec.kind,
        "title": f"{action}: {result.request.name}",
        "status": status,
        "arguments": _truncate_text(_json_dump(result.request.arguments), 3000),
        "body": _truncate_text(result.content, 6000),
    }


def _planning_narration_detail_data(text: str) -> dict[str, Any]:
    """Render prose that accompanied a tool call as a 'thinking' detail.

    Such prose ("I'll start by investigating…") is the model narrating its plan,
    not an answer. Keeping it out of the answer body — and recording it as a
    thinking detail — means a page reload shows the same narration-as-thinking
    the live stream did, instead of a stray sentence in the assistant bubble.
    """
    return {
        "kind": "thinking",
        "title": "Planning",
        "status": "completed",
        "body": _truncate_text(text.strip(), 4000),
    }


def _confirmation_tool_detail_data(confirmation: ActionConfirmation, content: str, *, status: str) -> dict[str, Any]:
    return {
        "kind": "tool",
        "title": f"Tool: {confirmation.tool_name}",
        "status": status,
        "arguments": _truncate_text(_json_dump(confirmation.arguments), 3000),
        "body": _truncate_text(content, 6000),
    }


def _blocked_tool_call_results(results: list[ToolCallResult]) -> list[ToolCallResult]:
    return [result for result in results if result.blocked is not None]


def _idempotent_success_content(request: ToolCallRequest, content: str) -> str:
    if not _is_create_action(request):
        return content
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(data, dict):
        return content
    error = data.get("error")
    if not isinstance(error, str) or "already exists" not in error.lower():
        return content
    return _json_dump(
        {
            "ok": True,
            "idempotent": True,
            "message": (
                f"{error}. Treating this as already completed, likely from an earlier "
                "successful attempt before the response was cut off."
            ),
            "original_result": data,
        }
    )


def _is_create_action(request: ToolCallRequest) -> bool:
    name = request.name.lower()
    action = name.split("__", 1)[1] if "__" in name else name
    return action == "create" or action.startswith("create_")


def _disclosed_tool_names_from_skill_results(results: list[ToolCallResult]) -> set[str]:
    disclosed: set[str] = set()
    for result in results:
        if result.request.spec.kind == "skill" and result.blocked is None:
            disclosed.update(result.tools_required)
    return disclosed


def _disclosed_tool_specs(tools: list[Tool], disclosed: set[str]) -> list[ChatToolSpec]:
    """Tool specs for the subset of ``tools`` already disclosed to the model."""
    return _mcp_tool_specs([tool for tool in tools if tool.name in disclosed])


def _blocked_tool_call_response(results: list[ToolCallResult]) -> str:
    if any(result.blocked == ChatBlockReason.CONFIRMATION_REQUIRED for result in results):
        pending = [result for result in results if _confirmation_status(result.content) == "pending"]
        if not pending:
            return (
                "This action needed approval, but the confirmation has already been decided or has expired. "
                "Nothing was executed."
            )
        n = len(pending)
        noun = "action" if n == 1 else f"{n} actions"
        lines = [
            f"Approval needed for {noun}. Use the confirmations panel in this chat to allow or deny. "
            "After all pending approvals are allowed, the conversation will resume automatically."
        ]
        for result in pending:
            lines.append(f"- {_blocked_tool_call_body(result.content)}")
        return "\n".join(lines)
    lines = [
        "Seizu blocked the requested action because the tool or skill is not available to this chat session, "
        "or the current user/agent permissions do not allow it."
    ]
    for result in results:
        lines.append(
            f"- `{result.request.name}` was blocked "
            f"({_blocked_tool_call_reason_label(result.blocked)}): {_blocked_tool_call_body(result.content)}"
        )
    lines.append("No blocked action was executed.")
    return "\n".join(lines)


def _blocked_tool_call_reason_label(reason: ChatBlockReason | None) -> str:
    if reason == ChatBlockReason.PERMISSION_DENIED:
        return "permission denied"
    if reason == ChatBlockReason.NOT_AVAILABLE:
        return "not available in this chat session"
    if reason == ChatBlockReason.CONFIRMATION_REQUIRED:
        return "confirmation required"
    return "blocked"


def _confirmation_status(content: str) -> str | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("confirmation_required") is not True:
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def _blocked_tool_call_body(content: str) -> str:
    """Extract the error message embedded in a chat-block result body, if any.

    Block results come from ``mcp_runtime`` with the explicit block reason on
    ``ToolCallResult.blocked``; this helper just picks the human-friendly
    error string out of the JSON body for user display. The decision to flag a
    result as blocked is *not* made here.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content.strip() or "blocked"
    if isinstance(data, dict) and isinstance(data.get("error"), str):
        return data["error"]
    if isinstance(data, dict) and data.get("confirmation_required") is True:
        status = data.get("status")
        if status == "denied":
            return "Action was denied for this confirmation window."
        return "Approval required."
    return content.strip() or "blocked"


def _tool_result_error_text(content: str) -> str | None:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error")
    return error if isinstance(error, str) and error.strip() else None


# Providers disagree on native structured output (the OpenAI ``response_format``
# shape). DeepSeek, for one, rejects it with 400 "response_format type is
# unavailable". We try the native path once per model; on a definitive rejection
# we remember it and route every later decision straight to the JSON-prompt
# fallback, so an unsupported provider pays one failing round-trip per process
# instead of one on every router/planner/verifier call.
_structured_output_native_ok: dict[int, bool] = {}


def _structured_output_unsupported(exc: Exception) -> bool:
    """True when a provider definitively rejects native structured output.

    Distinguished from a transient error (timeout, 5xx) so we only disable the
    native path for a real capability gap, not a blip.
    """
    text = str(exc).lower()
    return "response_format" in text or exc.__class__.__name__ in ("BadRequestError", "UnsupportedParamsError")


async def _invoke_structured_output(
    model: Any,
    schema: type[BaseModel],
    messages: list[BaseMessage],
    config: RunnableConfig,
    *,
    allow_reserve: bool = False,
    phase: str = "structured",
    max_output_tokens: int = 1024,
) -> BaseModel:
    """Invoke *model* for a structured decision with a JSON fallback.

    Some LiteLLM/provider combinations are inconsistent with LangChain's
    ``with_structured_output`` wrapper. The fallback still delegates the
    semantic decision to the LLM; code only validates and parses the JSON object.
    """
    structured = getattr(model, "with_structured_output", None)
    if callable(structured) and _structured_output_native_ok.get(id(model), True):
        controller = budget_controller_from_config(config)
        estimated_input = estimate_tokens(model, schema.__name__, messages, [])
        reservation = None
        try:
            if controller is not None:
                reservation = await controller.reserve(
                    estimated_input_tokens=estimated_input,
                    estimated_output_tokens=max_output_tokens,
                    estimated_cost_usd=usage_cost_usd(model, estimated_input, max_output_tokens),
                    allow_reserve=allow_reserve,
                    phase=phase,
                )
            result = await structured(schema).ainvoke(messages, config=config)
            output_text = _json_dump(result.model_dump() if isinstance(result, BaseModel) else result)
            output_tokens = estimate_tokens(model, "", [AIMessage(content=output_text)], [])
            if controller is not None and reservation is not None:
                await controller.commit(
                    reservation,
                    input_tokens=estimated_input,
                    output_tokens=output_tokens,
                    cost_usd=usage_cost_usd(model, estimated_input, output_tokens),
                    usage_estimated=True,
                )
            _structured_output_native_ok[id(model)] = True
            if isinstance(result, schema):
                return result
            if isinstance(result, dict):
                return schema.model_validate(result)
        except Exception as exc:
            if controller is not None and reservation is not None:
                await controller.release(reservation)
            if isinstance(exc, BudgetExceeded):
                raise
            if _structured_output_unsupported(exc):
                # Expected capability gap: log once, concisely, and stop trying
                # the native path for this model.
                if _structured_output_native_ok.get(id(model), True):
                    logger.info("Native structured output unavailable for this provider; using JSON-prompt fallback")
                _structured_output_native_ok[id(model)] = False
            else:
                logger.info(
                    "with_structured_output failed for %s; falling back to JSON parsing",
                    schema.__name__,
                    exc_info=True,
                )

    schema_json = _json_dump(schema.model_json_schema())
    system_prompt = (
        "Return only a valid JSON object matching this JSON schema. Do not include markdown, prose, or code fences. "
        "Ensure the final response content contains the complete JSON object; "
        "do not leave it only in hidden reasoning.\n\n"
        f"JSON schema:\n{schema_json}"
    )
    attempt_diagnostics: list[str] = []
    turn = await _run_llm_tool_turn(
        model,
        system_prompt,
        messages,
        [],
        config,
        None,
        allow_reserve=allow_reserve,
        phase=phase,
        max_output_tokens=max_output_tokens,
    )
    response_text = message_text(turn.message.content)
    attempt_diagnostics.append(f"chars={len(response_text)}, finish_reason={turn.finish_reason or 'unknown'}")
    parsed = _structured_from_text(schema, response_text)
    if parsed is not None:
        return parsed
    # Reasoning models sometimes bury the object in analysis on the first ask;
    # one firmer retry usually lands a clean object.
    retry_prompt = (
        "Output the JSON object only — no analysis, no explanation, no markdown code fences. "
        f"Return a single JSON object matching this schema:\n{schema_json}"
    )
    turn = await _run_llm_tool_turn(
        model,
        retry_prompt,
        messages,
        [],
        config,
        None,
        allow_reserve=allow_reserve,
        phase=phase,
        max_output_tokens=max_output_tokens,
    )
    response_text = message_text(turn.message.content)
    attempt_diagnostics.append(f"chars={len(response_text)}, finish_reason={turn.finish_reason or 'unknown'}")
    parsed = _structured_from_text(schema, response_text)
    if parsed is not None:
        return parsed
    raise ValueError(
        f"Model did not return a JSON object for {schema.__name__} "
        f"after {len(attempt_diagnostics)} attempts ({'; '.join(attempt_diagnostics)})"
    )


def _structured_from_text(schema: type[BaseModel], text: str) -> BaseModel | None:
    """Validate the first JSON object in *text* that matches *schema*.

    Candidates are tried richest-first, because a reasoning model often emits
    small stray brace groups in the prose around the real, larger payload.
    """
    for candidate in _json_objects_from_text(text):
        try:
            return schema.model_validate(candidate)
        except Exception:
            continue
    return None


def _json_objects_from_text(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    seen: set[str] = set()
    objects: list[dict[str, Any]] = []
    for candidate in [text, *_fenced_code_blocks(text), *_balanced_brace_objects(text)]:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        key = _json_dump(data)
        if key not in seen:
            seen.add(key)
            objects.append(data)
    objects.sort(key=lambda obj: len(_json_dump(obj)), reverse=True)
    return objects


def _fenced_code_blocks(text: str) -> list[str]:
    return [match.group(1) for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)]


def _balanced_brace_objects(text: str) -> list[str]:
    """Every top-level balanced ``{...}`` span, ignoring braces inside strings."""
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start : index + 1])
                start = -1
    return objects


def _terminal_stall_retry_message(action_summaries: list[str]) -> str:
    """Nudge a post-action turn that returned plain text instead of finishing.

    The model either still needs live data — in which case it should make the
    next structured tool/skill call — or it is done, in which case it should
    deliver the answer through the respond_to_user tool. We do not classify the
    prose; we just give the model one structured chance to act or finish before
    accepting whatever it returns next.
    """
    return (
        "You returned plain text after Seizu already ran one or more actions, without finishing the turn. "
        f"If the user's request needs more live data, make the next structured skill/tool call now. If you have "
        f"enough evidence, deliver the complete final answer by calling the `{_FINAL_ANSWER_TOOL}` tool with the "
        "full answer in `answer`. Do not describe future work in plain text.\n\n"
        f"Completed action summaries so far:\n{_truncate_text(chr(10).join(action_summaries), 10000)}"
    )


def _repeated_tool_call_retry_message(requests: list[ToolCallRequest], action_summaries: list[str]) -> str:
    repeated = ", ".join(f"`{request.name}` with arguments `{_json_dump(request.arguments)}`" for request in requests)
    prior = (
        "\n\nMost recent completed action:\n" + _truncate_text(action_summaries[-1], 5000) if action_summaries else ""
    )
    all_results = (
        "\n\nAll completed action summaries so far:\n" + _truncate_text("\n\n".join(action_summaries), 10000)
        if action_summaries
        else ""
    )
    return (
        "You requested an action Seizu has already run in this turn: "
        f"{repeated}. Do not repeat the same skill or tool call. Use the existing result to answer the user, or call "
        "a different tool only if it adds new required evidence. If the user's request has another step, continue "
        "with that different step using data from the completed result. If you have enough evidence, provide the "
        f"final synthesis now.{prior}{all_results}"
    )


def _repeated_tool_call_fallback(requests: list[ToolCallRequest], action_summaries: list[str]) -> str:
    repeated = ", ".join(f"`{request.name}`" for request in requests)
    details = " Seizu had already completed tool work for this turn." if action_summaries else ""
    return (
        "I stopped because the model repeatedly requested the same internal action instead of producing a final "
        f"answer. Repeated action: {repeated}.{details}"
    )


def _initial_empty_response_retry_message() -> str:
    return (
        "Your previous response was empty before Seizu could run any skill or tool. Retry now. Either answer the "
        "user directly, or use a structured skill/tool call with every required argument. Do not return an empty "
        "response."
    )


def _tool_markup_retry_message(leaked_tool_names: tuple[str, ...] = ()) -> str:
    if leaked_tool_names:
        names = ", ".join(f"`{name}`" for name in leaked_tool_names)
        return (
            f"You wrote a tool call for {names} as plain text, so nothing ran — that tool is not available to call "
            "directly right now. Tools are disclosed on demand: to use one, first call the skill that provides it "
            "(rendering a skill exposes the tools it declares), then call the tool with the structured tool-calling "
            "mechanism. Never write tool-call tokens, tags, or XML-like markup in your message text. If no available "
            "skill provides what you need, use the skills and tools already available, or answer in plain language."
        )
    return (
        "Your previous reply wrote raw tool-call markup as plain text instead of invoking a tool, so nothing ran. "
        "To call a tool, use the structured tool-calling mechanism provided by the API — never write tool-call "
        "tokens, tags, or XML-like markup in your message text. If you do not need a tool, answer in plain language."
    )


def _tool_markup_fallback() -> str:
    return (
        "I couldn't complete that request: the model returned an unusable tool call. Please try again, "
        "or rephrase the request."
    )


_INTERNAL_ACTION_TRANSCRIPT_RE = re.compile(
    r"(^|\n)\s*(?:"
    r"Seizu ran \d+ actions?(?: in parallel)?[:.]|"
    r"Seizu (?:ran tool|rendered skill) `[^`]+`|"
    r"-?\s*`[a-z][a-z0-9_]*__[a-z0-9_]+` with arguments "
    r")",
    re.IGNORECASE,
)


def _internal_action_transcript_leaked(text: str) -> bool:
    """True when a final draft copied Seizu's internal action transcript."""
    return bool(_INTERNAL_ACTION_TRANSCRIPT_RE.search(text))


def _action_transcript_retry_message() -> str:
    return (
        "Your previous draft copied Seizu's internal action transcript instead of answering the user. Rewrite it as "
        "a user-facing answer. Do not say 'Seizu ran', do not list tool names, do not show tool arguments, and do not "
        "paste raw returned JSON. Use the tool results only as evidence. Lead with the conclusion, then summarize the "
        "most important evidence, impact, and recommended next actions."
    )


def _final_synthesis_retry_message(action_summaries: list[str]) -> str:
    joined_summaries = "\n".join(action_summaries)
    budget = _answer_budget()
    table_noun = "table" if budget.max_tables == 1 else "tables"
    table_count = "one" if budget.max_tables == 1 else str(budget.max_tables)
    return (
        "Seizu has finished running the requested tool calls for this turn. Do not call any more tools. Provide the "
        "final answer to the user using the action results below. Be selective: lead with the direct answer, include "
        "only the most decision-relevant evidence, and avoid dumping full tables, exhaustive breakdowns, tool names, "
        "tool arguments, or raw returned JSON. Target "
        f"{budget.min_words}-{budget.max_words} words, at most {budget.max_bullets} bullets, and at most "
        f"{table_count} compact {table_noun}. Note uncertainty or truncation only when it "
        f"changes the conclusion or next action.\n\n{_truncate_text(joined_summaries, 12000)}"
    )


def _empty_response_fallback(action_summaries: list[str]) -> str:
    if not action_summaries:
        return (
            "The model returned an empty response after retrying, and Seizu did not run any skill or tool for this "
            "turn. Try rephrasing the request or starting a new chat thread."
        )
    return (
        "I ran the Seizu workflow, but the model did not return a final synthesis after the last action. "
        "The tool output was kept out of chat history; try asking for a concise summary again."
    )


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated]"


def _json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def build_capability_context(
    skills: list[Prompt],
    tools: list[Tool] | None,
    always_disclosed_tools: list[Tool] | None = None,
) -> str:
    """Build the capability section of the system prompt from already-listed data.

    The caller fetches the listings once per chat turn and threads them through
    here — keeps the hot path to a single store roundtrip per listing instead
    of re-listing once per consumer (capability context, skill specs, tool
    specs). Pass ``tools=None`` to render the progressive-disclosure variant
    (skills + always-disclosed tools only).
    """
    if tools is None:
        return _progressive_capability_context(skills, always_disclosed_tools or [])
    return _full_capability_context(skills, tools)


def _progressive_capability_context(
    skills: list[Prompt],
    always_disclosed_tools: list[Tool] | None = None,
) -> str:
    if not skills and not always_disclosed_tools:
        return ""
    header = (
        "Capability discovery mode: progressive disclosure is enabled. You are initially given structured skill "
        "tools. When the task needs a workflow, call the relevant skill tool with every required argument. Seizu will "
        "execute it internally and return the rendered skill to you. Rendered skills describe which tools to use and "
        "how to use them; after a skill is rendered, Seizu will expose only the chat-safe structured tools that the "
        "skill declares as required. Do not rely on tools that have not been disclosed by a rendered skill, by prior "
        "conversation context, or listed below as always available. Skill descriptions can include trigger phrases; "
        "if the current user request matches a trigger phrase, call that skill now instead of describing how to "
        "trigger it."
    )
    sections: list[str] = [header]
    if skills:
        sections.append(f"Available skills:\n{_format_skills(skills)}")
    if always_disclosed_tools:
        sections.append(f"Always-available tools:\n{_format_tools(always_disclosed_tools)}")
    return "\n\n".join(sections)


def _full_capability_context(skills: list[Prompt], tools: list[Tool]) -> str:
    sections: list[str] = [
        "Capability discovery mode: progressive disclosure is disabled. You are given both chat-safe tools and "
        "skills up front, similar to a normal MCP listing. Use native structured tool calls and include every "
        "required argument shown for the selected skill or tool. Skill descriptions can include trigger phrases; "
        "if the current user request matches a trigger phrase, call that skill now instead of describing how to "
        "trigger it."
    ]
    if skills:
        sections.append(f"Available skills:\n{_format_skills(skills)}")
    if tools:
        sections.append(f"Available tools:\n{_format_tools(tools)}")
    return "\n\n".join(sections) if len(sections) > 1 else ""


def _format_skills(skills: list[Prompt]) -> str:
    lines: list[str] = []
    for skill in skills[:30]:
        args = _prompt_arguments(skill)
        description = skill.description or "No description"
        line = f"- {skill.name}: {description}"
        if args:
            line = f"{line} Args: {args}"
        lines.append(line)
    if len(skills) > 30:
        lines.append(f"- ...and {len(skills) - 30} more")
    return "\n".join(lines)


def _format_tools(tools: list[Tool]) -> str:
    lines: list[str] = []
    for tool in tools[:30]:
        description = tool.description or "No description"
        line = f"- {tool.name}: {description}"
        args = _tool_arguments(tool)
        if args:
            line = f"{line} Args: {args}"
        lines.append(line)
    if len(tools) > 30:
        lines.append(f"- ...and {len(tools) - 30} more")
    return "\n".join(lines)


def _prompt_arguments(prompt: Prompt) -> str:
    arguments = prompt.arguments or []
    if not arguments:
        return ""
    formatted: list[str] = []
    for argument in arguments:
        suffix = " required" if argument.required else " optional"
        formatted.append(f"{argument.name} ({suffix.strip()})")
    return ", ".join(formatted)


def _tool_arguments(tool: Tool) -> str:
    input_schema = tool.inputSchema
    properties = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(properties, dict):
        return ""
    required_raw = input_schema.get("required") if isinstance(input_schema, dict) else None
    required = set(required_raw) if isinstance(required_raw, list) else set()
    formatted = []
    for name in list(properties.keys())[:12]:
        suffix = " required" if name in required else " optional"
        formatted.append(f"{name} ({suffix.strip()})")
    if len(properties) > 12:
        formatted.append("...")
    return ", ".join(formatted)


def _current_user_from_config(config: RunnableConfig) -> CurrentUser | None:
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    current_user = configurable.get("current_user")
    return current_user if isinstance(current_user, CurrentUser) else None


def _client_thread_id_from_config(config: RunnableConfig) -> str | None:
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("client_thread_id")
    return thread_id if isinstance(thread_id, str) else None


def _bypass_confirmations_from_config(config: RunnableConfig) -> bool:
    """Whether this turn runs with action confirmations bypassed.

    Set by the chat route (UI bypass mode) or by headless callers, both of
    which verify the user holds ``chat:bypass_permissions`` first; mcp_runtime
    re-checks the permission on every bypassed call.
    """
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return False
    return configurable.get("bypass_confirmations") is True


def _headless_from_config(config: RunnableConfig) -> bool:
    """Whether this turn is an automated run with no human present."""
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        return False
    return configurable.get("headless") is True


def _resume_confirmation_id(messages: list[Any]) -> str | None:
    # Only inspect the most recent HumanMessage — it is always the one that
    # triggered this graph turn.  Stopping at the first HumanMessage prevents
    # a persisted ephemeral resume message from a prior turn being picked up
    # again and re-executing an already-completed confirmation.
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None)
        if isinstance(additional_kwargs, dict):
            confirmation_id = additional_kwargs.get("resume_confirmation_id")
            if isinstance(confirmation_id, str) and confirmation_id:
                return confirmation_id
        return None
    return None


def _is_continuation_turn(messages: list[Any]) -> bool:
    """True when this turn is a "continue the previous response" request.

    Like ``_resume_confirmation_id``, only the most recent HumanMessage counts —
    it is the message that triggered this graph turn.
    """
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue
        additional_kwargs = getattr(message, "additional_kwargs", None)
        return bool(isinstance(additional_kwargs, dict) and additional_kwargs.get("continue_response") is True)
    return False


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _last_user_request(messages: list[Any]) -> str:
    """The last user message that is an actual request, not a control directive.

    Confirmation-resume and "continue response" turns are injected as synthetic
    HumanMessages (e.g. "Resume approved confirmation <id>") carrying a marker in
    ``additional_kwargs``. They drive the graph but are not the user's ask, so
    answering them literally — the orchestrator synthesizer describing how to
    "query the approval system" for a confirmation id — is wrong. Skip them and
    return the request that actually spawned the work.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            kwargs = getattr(message, "additional_kwargs", None) or {}
            if isinstance(kwargs, dict) and (kwargs.get("resume_confirmation_id") or kwargs.get("continue_response")):
                continue
            return str(message.content)
        if isinstance(message, dict) and message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _llm_context_messages(messages: list[Any]) -> list[BaseMessage]:
    filtered = drop_tagged(messages, MessageTag.EPHEMERAL, MessageTag.BROKEN)
    context: list[BaseMessage] = []
    for message in filtered:
        if isinstance(message, HumanMessage):
            context.append(HumanMessage(content=message.content, id=message.id))
        elif isinstance(message, AIMessage) and message_text(message.content) and not _is_broken_ai_message(message):
            context.append(AIMessage(content=strip_chat_ui_markers(message_text(message.content)), id=message.id))

    max_messages = settings.CHAT_LLM_CONTEXT_MAX_MESSAGES
    if max_messages > 0:
        context = context[-max_messages:]

    max_chars = settings.CHAT_LLM_CONTEXT_MAX_CHARS
    if max_chars <= 0:
        return context

    retained: list[BaseMessage] = []
    total_chars = 0
    for message in reversed(context):
        text_len = len(message_text(message.content))
        if retained and total_chars + text_len > max_chars:
            break
        retained.append(message)
        total_chars += text_len
    return list(reversed(retained))


def _is_broken_ai_message(message: AIMessage) -> bool:
    if has_tag(message, MessageTag.BROKEN):
        return True
    text = message_text(message.content)
    normalized = " ".join(text.lower().split())
    return any(
        marker in normalized
        for marker in (
            "the model returned an empty response",
            "did not return a final synthesis",
            "did not run any skill or tool",
            "configured automatic action limit",
        )
    )


def _trim_messages(existing_messages: list[Any], new_message: AIMessage) -> list[RemoveMessage]:
    max_messages = settings.CHAT_MAX_PERSISTED_MESSAGES
    if max_messages <= 0:
        return []
    combined = [*existing_messages, new_message]
    remove_count = len(combined) - max_messages
    if remove_count <= 0:
        return []
    # Keep the retained window starting at a user turn: dropping an odd number
    # of messages can leave a leading assistant message orphaned from its
    # prompt, so shed it too. Never touches the just-produced message (the last
    # element), so we always retain at least the current turn.
    while remove_count < len(combined) - 1 and isinstance(combined[remove_count], AIMessage):
        remove_count += 1
    removals: list[RemoveMessage] = []
    for message in combined[:remove_count]:
        message_id = getattr(message, "id", None)
        if isinstance(message_id, str) and message_id:
            removals.append(RemoveMessage(id=message_id))
    return removals


def _chunk_text(text: str, chunk_size: int = 8) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def build_chat_graph(checkpointer: Any) -> ChatGraph:
    # Local import breaks the import cycle: chat_orchestrator imports the shared
    # turn/tool helpers from this module at import time, while this module only
    # needs the orchestrator nodes here, at graph-build time.
    from reporting.services import chat_orchestrator as orchestrator

    graph = StateGraph(ChatState)
    graph.add_node("router", orchestrator.router_node)
    graph.add_node("chat_agent", chat_agent_node)
    graph.add_node("planner", orchestrator.planner_node)
    graph.add_node("dispatcher", orchestrator.dispatcher_node)
    graph.add_node("verifier", orchestrator.verifier_node)
    graph.add_node("synthesizer", orchestrator.synthesizer_node)
    graph.add_node("confirmation_pause", orchestrator.confirmation_pause_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        orchestrator.route_from_router,
        {"planner": "planner", "chat_agent": "chat_agent"},
    )
    graph.add_edge("chat_agent", END)
    graph.add_edge("planner", "dispatcher")
    graph.add_conditional_edges(
        "dispatcher",
        orchestrator.route_from_dispatcher,
        {
            "verifier": "verifier",
            "synthesizer": "synthesizer",
            "confirmation_pause": "confirmation_pause",
        },
    )
    graph.add_edge("confirmation_pause", END)
    graph.add_conditional_edges(
        "verifier",
        orchestrator.route_from_verifier,
        {"dispatcher": "dispatcher", "synthesizer": "synthesizer"},
    )
    graph.add_edge("synthesizer", END)
    return graph.compile(checkpointer=checkpointer)


def get_chat_graph() -> ChatGraph:
    global _chat_graph
    if _chat_graph is not None:
        return _chat_graph
    backend = _chat_checkpoint_backend()
    if backend == "postgres":
        raise RuntimeError("PostgreSQL chat checkpoints were not initialized during application startup")
    _chat_graph = build_chat_graph(_build_dynamodb_checkpointer())
    return _chat_graph


@lru_cache(maxsize=16)
def get_chat_model(role: str = "default", economy: bool = False) -> ChatModel:
    provider = _chat_provider()
    if provider == _MOCK_PROVIDER:
        raise RuntimeError("CHAT_LLM_PROVIDER=mock does not use a real chat model")
    from langchain_litellm import ChatLiteLLM

    model_id = _model_for_role(role, economy=economy)
    kwargs: dict[str, Any] = {
        "model": _litellm_model_id(provider, model_id),
        "temperature": settings.CHAT_LLM_TEMPERATURE,
        "request_timeout": settings.CHAT_LLM_TIMEOUT_SECONDS,
        "max_retries": settings.CHAT_LLM_MAX_RETRIES,
        # Seizu always consumes the model through .astream(); make the streaming
        # intent explicit so LiteLLM emits stream=true on the wire.
        "streaming": True,
    }
    if settings.CHAT_LLM_MAX_TOKENS > 0:
        kwargs["max_tokens"] = settings.CHAT_LLM_MAX_TOKENS
    api_key = settings.CHAT_LLM_API_KEY or _legacy_provider_api_key(provider)
    if api_key:
        kwargs["api_key"] = api_key
    # base_url applies to every provider now: LiteLLM routes any model through an
    # OpenAI-compatible gateway (e.g. a self-hosted LiteLLM proxy) when api_base
    # is set, so the proxy is an opt-in deployment rather than a hard dependency.
    if settings.CHAT_LLM_BASE_URL:
        kwargs["api_base"] = settings.CHAT_LLM_BASE_URL
    return ChatLiteLLM(**kwargs)


def _model_for_role(role: str, *, economy: bool = False) -> str:
    if economy and settings.CHAT_LLM_ECONOMY_MODEL.strip():
        return settings.CHAT_LLM_ECONOMY_MODEL.strip()
    role_models = {
        "planner": settings.CHAT_LLM_PLANNER_MODEL,
        "router": settings.CHAT_LLM_PLANNER_MODEL,
        "worker": settings.CHAT_LLM_WORKER_MODEL,
        "verifier": settings.CHAT_LLM_VERIFIER_MODEL,
        "synthesizer": settings.CHAT_LLM_SYNTHESIZER_MODEL,
    }
    return role_models.get(role, "").strip() or settings.CHAT_LLM_MODEL.strip()


def _chat_provider() -> str:
    """Resolve the configured provider sentinel.

    "mock" is the only special value; any other (including the explicit
    "litellm") routes through LiteLLM. The provider is no longer constrained to a
    fixed allowlist — provider/model choice is delegated to LiteLLM via the
    CHAT_LLM_MODEL string.
    """
    return settings.CHAT_LLM_PROVIDER.strip().lower() or "litellm"


# Legacy single-provider names map onto LiteLLM's provider namespace so existing
# CHAT_LLM_PROVIDER=<name> + bare CHAT_LLM_MODEL deployments keep working without
# config changes. New deployments can instead set a fully-qualified model string
# (e.g. "anthropic/claude-3-5-sonnet-latest") and leave CHAT_LLM_PROVIDER unset.
_LEGACY_PROVIDER_NAMESPACE = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "deepseek": "deepseek",
}


def _litellm_model_id(provider: str, configured_model: str | None = None) -> str:
    model = (configured_model if configured_model is not None else settings.CHAT_LLM_MODEL).strip()
    if not model:
        raise ValueError(
            "CHAT_LLM_MODEL is required when CHAT_LLM_PROVIDER is not 'mock'. Set it to a LiteLLM model "
            "identifier, optionally namespaced by provider (e.g. 'openai/gpt-4o', "
            "'anthropic/claude-3-5-sonnet-latest', 'gemini/gemini-2.0-flash', 'deepseek/deepseek-reasoner'). "
            "The mock provider is the only one that runs without a model."
        )
    # Already provider-qualified, or the operator opted into fully-qualified
    # model strings by leaving the provider as the generic sentinel.
    if "/" in model or provider in ("", "litellm", _MOCK_PROVIDER):
        return model
    namespace = _LEGACY_PROVIDER_NAMESPACE.get(provider, provider)
    return f"{namespace}/{model}"


def _legacy_provider_api_key(provider: str) -> str:
    """Best-effort map of legacy per-provider key settings.

    When CHAT_LLM_API_KEY is unset and a legacy provider name is used, forward
    the matching provider key. For the generic "litellm" sentinel this returns
    "" and LiteLLM falls back to its own provider-specific env var lookup
    (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY, ...).
    """
    return {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "gemini": settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY,
        "deepseek": settings.DEEPSEEK_API_KEY,
    }.get(provider, "")


def validate_chat_llm_config() -> None:
    """Fail-fast validation called at startup when chat is enabled.

    Raises ``ValueError`` if a real provider is selected without CHAT_LLM_MODEL,
    catching a missing model that previously surfaced only on the first request.
    """
    provider = _chat_provider()
    if provider == _MOCK_PROVIDER:
        return
    _litellm_model_id(provider)
    for configured_model in (
        settings.CHAT_LLM_PLANNER_MODEL,
        settings.CHAT_LLM_WORKER_MODEL,
        settings.CHAT_LLM_VERIFIER_MODEL,
        settings.CHAT_LLM_SYNTHESIZER_MODEL,
        settings.CHAT_LLM_ECONOMY_MODEL,
    ):
        if configured_model.strip():
            _litellm_model_id(provider, configured_model)


def _chat_checkpoint_backend() -> Literal["dynamodb", "postgres"]:
    backend = settings.CHAT_CHECKPOINT_BACKEND.strip().lower()
    if backend == "dynamodb":
        return "dynamodb"
    if backend in {"postgres", "postgresql", "sql"}:
        return "postgres"
    raise ValueError(
        f"Unknown chat checkpoint backend: {settings.CHAT_CHECKPOINT_BACKEND!r}. "
        "Supported values are 'dynamodb' and 'postgres'."
    )


def _build_dynamodb_checkpointer() -> DynamoDBSaver:
    # DynamoDBSaver is boto3-based (no async DynamoDB saver ships in
    # langgraph-checkpoint-aws), but its async methods wrap the sync calls in
    # run_in_executor, so checkpoint I/O is offloaded to a threadpool and does
    # not block the event loop — keep using it under the async graph.
    ttl_seconds = settings.CHAT_CHECKPOINT_TTL_SECONDS or None
    s3_offload_config = None
    if settings.CHAT_CHECKPOINT_S3_BUCKET:
        s3_offload_config = {
            "bucket_name": settings.CHAT_CHECKPOINT_S3_BUCKET,
            "endpoint_url": settings.CHAT_CHECKPOINT_S3_ENDPOINT_URL or None,
            "key_prefix": settings.CHAT_CHECKPOINT_S3_KEY_PREFIX or None,
        }
    return DynamoDBSaver(
        table_name=settings.CHAT_CHECKPOINT_TABLE_NAME,
        region_name=settings.DYNAMODB_REGION,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL or None,
        boto_config=_aws_config(),
        ttl_seconds=ttl_seconds,
        enable_checkpoint_compression=settings.CHAT_CHECKPOINT_ENABLE_COMPRESSION,
        s3_offload_config=s3_offload_config,
    )


async def initialize_chat_checkpoints() -> None:
    backend = _chat_checkpoint_backend()
    if backend == "dynamodb" and settings.CHAT_CHECKPOINT_CREATE_TABLE:
        await asyncio.to_thread(_initialize_chat_checkpoints_sync)
    elif backend == "postgres":
        await _initialize_postgres_chat_checkpoints()


async def close_chat_checkpoints() -> None:
    global _chat_checkpoint_pool, _chat_graph
    pool = _chat_checkpoint_pool
    _chat_checkpoint_pool = None
    _chat_graph = None
    if pool is not None:
        await pool.close()


async def _initialize_postgres_chat_checkpoints() -> None:
    global _chat_checkpoint_pool, _chat_graph
    if _chat_checkpoint_pool is not None:
        return

    url = _postgres_checkpoint_url()
    min_size = settings.CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE
    max_size = settings.CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE
    if min_size < 0 or max_size < 1 or min_size > max_size:
        raise ValueError(
            "CHAT_CHECKPOINT_DATABASE_POOL_MIN_SIZE and CHAT_CHECKPOINT_DATABASE_POOL_MAX_SIZE "
            "must satisfy 0 <= min_size <= max_size and max_size >= 1"
        )

    pool = AsyncConnectionPool(
        conninfo=url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    try:
        await pool.open()
        await pool.wait()
        if settings.CHAT_CHECKPOINT_CREATE_TABLE:
            await _setup_postgres_checkpointer(pool)
        checkpointer = AsyncPostgresSaver(pool)
    except Exception:
        await pool.close()
        raise

    _chat_checkpoint_pool = pool
    _chat_graph = build_chat_graph(checkpointer)


async def _setup_postgres_checkpointer(pool: AsyncConnectionPool) -> None:
    # Gunicorn workers run lifespan concurrently. LangGraph records each
    # migration version with a primary-key insert, so serialize setup across
    # workers to avoid a first-deploy migration race. Poll with the non-blocking
    # lock function: a blocking advisory-lock query can hold a virtual
    # transaction that conflicts with another worker's migration DDL.
    async with pool.connection() as connection:
        while True:
            cursor = await connection.execute(
                "SELECT pg_try_advisory_lock(hashtextextended('seizu-chat-checkpoint-setup', 0)) AS acquired"
            )
            row = await cursor.fetchone()
            if row is not None and row["acquired"]:
                break
            await asyncio.sleep(0.1)
        try:
            await AsyncPostgresSaver(connection).setup()
        finally:
            await connection.execute("SELECT pg_advisory_unlock(hashtextextended('seizu-chat-checkpoint-setup', 0))")


def _postgres_checkpoint_url() -> str:
    url = build_database_url(
        settings.CHAT_CHECKPOINT_DATABASE_URL.strip(),
        user=settings.CHAT_CHECKPOINT_DATABASE_USER,
        password=settings.CHAT_CHECKPOINT_DATABASE_PASSWORD,
    )
    if url.get_backend_name() != "postgresql":
        raise ValueError("CHAT_CHECKPOINT_DATABASE_URL must be a PostgreSQL URL when CHAT_CHECKPOINT_BACKEND=postgres")
    url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _initialize_chat_checkpoints_sync() -> None:
    checkpointer = _build_dynamodb_checkpointer()
    client = checkpointer.client
    table_name = settings.CHAT_CHECKPOINT_TABLE_NAME

    try:
        client.describe_table(TableName=table_name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        try:
            client.create_table(
                TableName=table_name,
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
        except ClientError as create_exc:
            if create_exc.response["Error"]["Code"] != "ResourceInUseException":
                raise
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)

    if settings.CHAT_CHECKPOINT_TTL_SECONDS:
        try:
            client.update_time_to_live(
                TableName=table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
            )
        except ClientError as ttl_exc:
            error = ttl_exc.response["Error"]
            message = error.get("Message", "")
            if error["Code"] != "ValidationException" or (
                "already enabled" not in message and "being enabled" not in message
            ):
                raise


def _aws_config() -> botocore.config.Config:
    config_kwargs: dict[str, Any] = {
        "connect_timeout": settings.AWS_CONNECT_TIMEOUT,
        "read_timeout": settings.AWS_READ_TIMEOUT,
    }
    # Path-style addressing is required for S3-compatible endpoints like MinIO
    # in development; against real AWS S3 leave the default (virtual-hosted),
    # which is the recommended/forward-compatible style.
    if settings.CHAT_CHECKPOINT_S3_ENDPOINT_URL:
        config_kwargs["s3"] = {"addressing_style": "path"}
    return botocore.config.Config(**config_kwargs)
