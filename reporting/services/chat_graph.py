import asyncio
import hashlib
import json
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
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph_checkpoint_aws import DynamoDBSaver
from mcp.types import Prompt, Tool
from typing_extensions import TypedDict

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.services import action_confirmations, mcp_runtime, report_store
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


@dataclass(frozen=True)
class AnswerBudget:
    min_words: int
    max_words: int
    max_bullets: int
    max_tables: int


class SeizuChatDeepSeekMixin:
    """Expose DeepSeek's OpenAI-API-shape ``reasoning_content`` field.

    DeepSeek emits its hidden chain-of-thought in the OpenAI Chat Completions
    streaming shape under ``choices[0].delta.reasoning_content``. The current
    ``langchain-deepseek`` adapter pulls that into ``AIMessageChunk.additional_kwargs``
    on the way down. We keep it available long enough to render UI diagnostics,
    but strip it before the message is reused as model context.

    Scope: DeepSeek only (and OpenAI-shape gateways routed to DeepSeek). It does
    not handle Anthropic ``ThinkingBlock`` or Gemini reasoning parts, which use
    different transport shapes.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> Any:
        generation_chunk = super()._convert_chunk_to_generation_chunk(  # type: ignore[misc]
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        reasoning_content = _deepseek_reasoning_content_delta(chunk)
        if reasoning_content and generation_chunk is not None and isinstance(generation_chunk.message, AIMessageChunk):
            generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning_content
        return generation_chunk


_VALID_CHAT_PROVIDERS = frozenset({"mock", "openai", "anthropic", "gemini", "deepseek"})


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

    # One listing per turn — every consumer below (capability context, skill
    # specs, tool specs) works off this snapshot. No cross-turn cache: each
    # turn sees the live store, and a single ``list_enabled_*`` call covers
    # every read the turn needs.
    skills = await _list_chat_prompts(current_user)
    tools: list[Tool] = []
    progressive_disclosure = settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE
    disclosed_tool_names: set[str] = set()
    if not progressive_disclosure:
        tools = await _list_chat_tools(current_user)

    capability_context = build_capability_context(skills, tools if not progressive_disclosure else None)
    if capability_context:
        base_system_prompt = f"{base_system_prompt}\n\n{capability_context}"

    skill_specs = _skill_tool_specs(skills)
    tool_specs: list[ChatToolSpec] = _mcp_tool_specs(tools) if not progressive_disclosure else []

    action_count = 0
    action_summaries: list[str] = []
    executed_action_keys: set[str] = set()
    empty_retry_used = False
    repeated_action_retry_used = False
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
        available_specs = _with_provider_tool_names([*skill_specs, *tool_specs])
        turn_result = await _run_llm_tool_turn(
            model,
            turn_system_prompt,
            messages,
            available_specs,
            config,
            writer,
        )
        ai_message = turn_result.message
        streamed_in_last_turn = turn_result.streamed
        streamed_response = streamed_in_last_turn
        detail_events.extend(turn_result.details)
        unavailable = _unavailable_tool_call_results(ai_message, available_specs)
        if unavailable:
            action_summaries.append(_tool_call_user_summary(unavailable))
            response = _blocked_tool_call_response(unavailable)
            break

        requested = _tool_call_requests(ai_message, available_specs)
        if not requested:
            response = message_text(ai_message.content)
            if response:
                response, response_hit_output_limit = _append_output_limit_notice(
                    response, turn_result.finish_reason, action_summaries
                )
                break
            if action_summaries:
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
                    messages = [*messages, ai_message]
                    pending_system_addendum = _repeated_tool_call_retry_message(repeated, action_summaries)
                    continue
                response = _repeated_tool_call_fallback(repeated, action_summaries)
                response_is_broken = True
                break
            break

        action_count += len(batch)
        executed_action_keys.update(_tool_request_key(request) for request in batch)
        batch_id = uuid.uuid4().hex
        results = await _run_tool_call_batch(
            batch,
            current_user,
            session_key=_client_thread_id_from_config(config),
            batch_id=batch_id,
        )
        for result in results:
            detail_data = _tool_call_detail_data(result)
            detail_events.append(detail_data)
            writer({"kind": "detail", "id": f"detail_{uuid.uuid4().hex}", "data": detail_data})
        action_summaries.append(_tool_call_user_summary(results))
        blocked_results = _blocked_tool_call_results(results)
        messages = [
            *messages,
            ai_message,
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
                available_by_name = {tool.name: tool for tool in tools}
                tool_specs = _mcp_tool_specs(
                    [tool for name, tool in available_by_name.items() if name in disclosed_tool_names]
                )

    if not response and action_summaries and not response_is_broken:
        synthesis_system_prompt = _combined_system_prompt(
            base_system_prompt, _final_synthesis_retry_message(action_summaries)
        )
        turn_result = await _run_llm_tool_turn(model, synthesis_system_prompt, messages, [], config, writer)
        final_message = turn_result.message
        streamed_in_last_turn = turn_result.streamed
        streamed_response = streamed_in_last_turn
        detail_events.extend(turn_result.details)
        response = message_text(final_message.content)
        if response:
            response, response_hit_output_limit = _append_output_limit_notice(
                response, turn_result.finish_reason, action_summaries
            )
        else:
            response_is_broken = True

    if not response:
        response = _empty_response_fallback(action_summaries)
        response_is_broken = True

    # Streaming contract: the LLM-produced text is written to the writer as the
    # chunks arrive, so it is already on the wire. Synthetic fallbacks (and any
    # final tail not already streamed) are emitted here as a single delta.
    if response and response != streamed_response:
        if not streamed_response:
            writer({"kind": "token", "content": response})
        elif response.startswith(streamed_response):
            writer({"kind": "token", "content": response[len(streamed_response) :]})
        else:
            tail = f"\n\n{response}"
            writer({"kind": "token", "content": tail})
            # Sync the persisted content with what was sent on the wire so that
            # history replay matches the user-visible conversation. This matters
            # when an earlier turn already streamed text (e.g. "Let me check…")
            # before a blocked or fallback response was generated.
            if streamed_response:
                response = f"{streamed_response}{tail}"
    if response_hit_output_limit:
        writer({"kind": "finish_reason", "finish_reason": "length"})

    response_metadata: dict[str, Any] = {}
    if detail_events:
        response_metadata["seizu_details"] = detail_events
    if response_hit_output_limit:
        response_metadata["seizu_output_limit"] = True
    ai_message = AIMessage(
        content=response,
        id=f"msg_{uuid.uuid4().hex}",
        response_metadata=response_metadata,
    )
    if response_is_broken:
        tag_message(ai_message, MessageTag.BROKEN)
    return {"messages": [*_trim_messages(state["messages"], ai_message), ai_message]}


def _combined_system_prompt(base: str, addendum: str) -> str:
    if not addendum:
        return base
    return f"{base}\n\n{addendum}"


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

    # Capture as a non-optional type for use inside nested closures below.
    authed_user: CurrentUser = current_user
    confirmation = await report_store.get_action_confirmation(confirmation_id, user_id=authed_user.user.user_id)
    if confirmation is None:
        response = "Seizu could not find that action confirmation."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)
    client_thread_id = _client_thread_id_from_config(config)
    if confirmation.source != "chat" or confirmation.session_key != client_thread_id:
        response = "That action confirmation does not belong to this chat thread, so Seizu did not run it."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)
    if action_confirmations.is_expired(confirmation) and confirmation.status != "executed":
        response = "That action confirmation has expired, so Seizu did not run it."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)
    if confirmation.status not in ("approved", "executed"):
        response = "That action is not approved, so Seizu did not run it."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)

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
            response = (
                f"Waiting for {n} more {noun} before proceeding. "
                "Use the confirmation panel or URLs to approve the remaining actions."
            )
            writer({"kind": "token", "content": response})
            return _chat_state_with_ai_response(state, response)
        denied = [c for c in batch if c.status == "denied"]
        # Only count a confirmation as blocking-expired when it still needed a
        # decision — executed items have already run and must not abort the batch.
        expired = [c for c in batch if c.status in ("pending", "approved") and action_confirmations.is_expired(c)]
        if denied or expired:
            reason = "denied" if denied else "expired"
            response = f"One or more actions in this approval batch were {reason}, so Seizu did not run the batch."
            writer({"kind": "token", "content": response})
            return _chat_state_with_ai_response(state, response)
        to_run = [c for c in batch if c.status == "approved"]
    else:
        to_run = [confirmation] if confirmation.status == "approved" else []

    if not to_run:
        response = "All actions in this batch have already been executed."
        writer({"kind": "token", "content": response})
        return _chat_state_with_ai_response(state, response)

    action_word = "action" if len(to_run) == 1 else "actions"
    writer({"kind": "token", "content": f"Running approved {action_word}...\n\n"})

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
            result_max_rows=settings.CHAT_TOOL_RESULT_MAX_ROWS,
            result_max_bytes=settings.CHAT_TOOL_RESULT_MAX_BYTES,
        )
        if outcome.blocked is not None:
            errors.append(f"`{c.tool_name}`: {_blocked_tool_call_body(outcome.text)}")
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
            writer({"kind": "token", "content": response[len(streamed) :]})
        else:
            writer({"kind": "token", "content": f"\n\n{response}"})
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


async def _run_llm_tool_turn(
    model: ChatModel,
    system_prompt: str,
    messages: list[BaseMessage],
    tools: list[ChatToolSpec],
    config: RunnableConfig,
    writer: Callable[[dict[str, Any]], None] | None = None,
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
    if tools and callable(bind_tools):
        runnable = bind_tools([_langchain_tool_schema(tool) for tool in tools])

    merged: Any | None = None
    reasoning_detail_id = f"detail_{uuid.uuid4().hex}"
    reasoning_text = ""
    reasoning_detail_data: dict[str, Any] | None = None
    streamed = ""
    finish_reason: str | None = None
    stream_text = writer is not None
    async for chunk in runnable.astream(
        [SystemMessage(content=system_prompt), *messages],
        config=config,
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
            writer(
                {
                    "kind": "detail",
                    "id": reasoning_detail_id,
                    "data": {**reasoning_detail_data, "status": "running"},
                }
            )
        if stream_text and writer is not None:
            delta = message_text(getattr(chunk, "content", ""))
            if delta:
                writer({"kind": "token", "content": delta})
                streamed += delta
        merged = chunk if merged is None else merged + chunk

    if isinstance(merged, AIMessage):
        return LLMTurnResult(
            message=_strip_reasoning_context(merged),
            streamed=streamed,
            finish_reason=finish_reason or _chunk_finish_reason(merged),
            details=(reasoning_detail_data,) if reasoning_detail_data else (),
        )
    fallback = AIMessage(
        content=message_text(getattr(merged, "content", "")) if merged is not None else "",
        tool_calls=list(getattr(merged, "tool_calls", []) or []),
        invalid_tool_calls=list(getattr(merged, "invalid_tool_calls", []) or []),
        id=getattr(merged, "id", None),
    )
    return LLMTurnResult(
        message=_strip_reasoning_context(fallback),
        streamed=streamed,
        finish_reason=finish_reason or _chunk_finish_reason(merged),
        details=(reasoning_detail_data,) if reasoning_detail_data else (),
    )


def _strip_reasoning_context(message: AIMessage) -> AIMessage:
    """Remove provider thinking fields before message reuse in LLM context."""
    if "reasoning_content" not in message.additional_kwargs:
        return message
    message.additional_kwargs = {
        key: value for key, value in message.additional_kwargs.items() if key != "reasoning_content"
    }
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


def _deepseek_reasoning_content_delta(chunk: dict[str, Any]) -> str:
    choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
    if not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    reasoning_content = delta.get("reasoning_content")
    return reasoning_content if isinstance(reasoning_content, str) else ""


def _uses_deepseek_compatible_endpoint(model_name: str) -> bool:
    base_url_host = _hostname(settings.CHAT_LLM_BASE_URL).lower()
    if base_url_host == "deepseek.com" or base_url_host.endswith(".deepseek.com"):
        return True
    return model_name.lower().startswith("deepseek-")


def _hostname(url: str) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


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
) -> list[ToolCallResult]:
    max_parallel = settings.CHAT_LLM_MAX_PARALLEL_TOOL_CALLS
    semaphore = asyncio.Semaphore(max_parallel) if max_parallel > 0 else None

    async def run_one(request: ToolCallRequest) -> ToolCallResult:
        if semaphore is None:
            return await _run_tool_call(request, current_user, session_key=session_key, batch_id=batch_id)
        async with semaphore:
            return await _run_tool_call(request, current_user, session_key=session_key, batch_id=batch_id)

    return list(await asyncio.gather(*(run_one(request) for request in requests)))


async def _run_tool_call(
    request: ToolCallRequest,
    current_user: CurrentUser | None,
    *,
    session_key: str | None = None,
    batch_id: str | None = None,
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

    outcome = await mcp_runtime.call_tool_for_chat(
        current_user,
        request.name,
        request.arguments,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        result_max_rows=settings.CHAT_TOOL_RESULT_MAX_ROWS,
        result_max_bytes=settings.CHAT_TOOL_RESULT_MAX_BYTES,
        confirmation_source="chat",
        confirmation_session_key=session_key,
        confirmation_batch_id=batch_id,
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
        f"result.{user_context}{provider_note}"
    )


def _provider_prompt_note(provider: str) -> str:
    if provider == "anthropic":
        return "\nFor Claude, keep the final answer direct and avoid prefilling or hidden chain-of-thought."
    if provider == "gemini":
        return "\nFor Gemini, preserve structured report suggestions as compact headings and bullet lists."
    if provider == "deepseek":
        return "\nFor DeepSeek, keep reasoning concise and surface only the conclusion, evidence, and next action."
    if provider == "openai":
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
    status = "blocked" if result.blocked is not None else "completed"
    return {
        "kind": result.request.spec.kind,
        "title": f"{action}: {result.request.name}",
        "status": status,
        "arguments": _truncate_text(_json_dump(result.request.arguments), 3000),
        "body": _truncate_text(result.content, 6000),
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


def _common_batch_url(results: list[ToolCallResult]) -> str | None:
    """Return the shared batch_url if all results share the same non-None batch_id."""
    batch_url: str | None = None
    for result in results:
        try:
            data = json.loads(result.content)
        except json.JSONDecodeError:
            return None
        url = data.get("batch_url") if isinstance(data, dict) else None
        if not isinstance(url, str):
            return None
        if batch_url is None:
            batch_url = url
        elif batch_url != url:
            return None
    return batch_url


def _blocked_tool_call_response(results: list[ToolCallResult]) -> str:
    if any(result.blocked == ChatBlockReason.CONFIRMATION_REQUIRED for result in results):
        pending = [result for result in results if _confirmation_status(result.content) == "pending"]
        if not pending:
            return (
                "This action needed approval, but the confirmation has already been decided or has expired. "
                "Nothing was executed."
            )
        batch_url = _common_batch_url(pending)
        if batch_url:
            n = len(pending)
            noun = "action" if n == 1 else f"{n} actions"
            return (
                f"Approval needed for {noun}. Visit the confirmation page to allow or deny, "
                f"then the conversation will resume automatically: {batch_url}"
            )
        lines = ["Approval needed. Open the confirmation URL to allow or deny, then resume the conversation."]
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
        url = data.get("confirmation_url")
        return f"Approval required at {url}" if isinstance(url, str) else "Approval required."
    return content.strip() or "blocked"


def _repeated_tool_call_retry_message(requests: list[ToolCallRequest], action_summaries: list[str]) -> str:
    repeated = ", ".join(f"`{request.name}` with arguments `{_json_dump(request.arguments)}`" for request in requests)
    prior = (
        "\n\nMost recent completed action:\n" + _truncate_text(action_summaries[-1], 5000) if action_summaries else ""
    )
    return (
        "You requested an action Seizu has already run in this turn: "
        f"{repeated}. Do not repeat the same skill or tool call. Use the existing result to answer the user, or call "
        "a different tool only if it adds new required evidence. If you have enough evidence, provide the final "
        f"synthesis now.{prior}"
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


def _final_synthesis_retry_message(action_summaries: list[str]) -> str:
    joined_summaries = "\n".join(action_summaries)
    budget = _answer_budget()
    table_noun = "table" if budget.max_tables == 1 else "tables"
    table_count = "one" if budget.max_tables == 1 else str(budget.max_tables)
    return (
        "Seizu has finished running the requested tool calls for this turn. Do not call any more tools. Provide the "
        "final answer to the user using the action results below. Be selective: lead with the direct answer, include "
        "only the most decision-relevant evidence, and avoid dumping full tables or exhaustive breakdowns. Target "
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


def build_capability_context(skills: list[Prompt], tools: list[Tool] | None) -> str:
    """Build the capability section of the system prompt from already-listed data.

    The caller fetches the listings once per chat turn and threads them through
    here — keeps the hot path to a single store roundtrip per listing instead
    of re-listing once per consumer (capability context, skill specs, tool
    specs). Pass ``tools=None`` to render the progressive-disclosure variant
    (skills only).
    """
    if tools is None:
        return _progressive_capability_context(skills)
    return _full_capability_context(skills, tools)


def _progressive_capability_context(skills: list[Prompt]) -> str:
    if not skills:
        return ""
    return (
        "Capability discovery mode: progressive disclosure is enabled. You are initially given structured skill "
        "tools. When the task needs a workflow, call the relevant skill tool with every required argument. Seizu will "
        "execute it internally and return the rendered skill to you. Rendered skills describe which tools to use and "
        "how to use them; after a skill is rendered, Seizu will expose only the chat-safe structured tools that the "
        "skill declares as required. Do not rely on tools that have not been disclosed by a rendered skill or by prior "
        "conversation context. Skill descriptions can include trigger phrases; if the current user request matches a "
        "trigger phrase, call that skill now instead of describing how to trigger it.\n\n"
        f"Available skills:\n{_format_skills(skills)}"
    )


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


def _last_user_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
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
        {"verifier": "verifier", "synthesizer": "synthesizer"},
    )
    graph.add_conditional_edges(
        "verifier",
        orchestrator.route_from_verifier,
        {"dispatcher": "dispatcher", "synthesizer": "synthesizer"},
    )
    graph.add_edge("synthesizer", END)
    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_chat_graph() -> ChatGraph:
    return build_chat_graph(_build_checkpointer())


@lru_cache(maxsize=1)
def get_chat_model() -> ChatModel:
    provider = _chat_provider()
    if provider == "mock":
        raise RuntimeError("CHAT_LLM_PROVIDER=mock does not use a real chat model")
    model_name = _chat_model_name(provider)
    max_tokens = settings.CHAT_LLM_MAX_TOKENS if settings.CHAT_LLM_MAX_TOKENS > 0 else None

    if provider == "openai":
        kwargs = _chat_model_kwargs(settings.OPENAI_API_KEY, include_base_url=True)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        cls = _openai_chat_class(deepseek_compatible=_uses_deepseek_compatible_endpoint(model_name))
        return cls(model=model_name, **kwargs)

    if provider == "deepseek":
        kwargs = _chat_model_kwargs(settings.DEEPSEEK_API_KEY, include_base_url=True)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return _deepseek_chat_class()(model=model_name, **kwargs)

    if provider == "anthropic":
        kwargs = _chat_model_kwargs(settings.ANTHROPIC_API_KEY, include_base_url=True)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model_name, **kwargs)

    # gemini — ChatGoogleGenerativeAI does not accept a base_url kwarg, so the
    # operator-set CHAT_LLM_BASE_URL is intentionally not forwarded here.
    kwargs = _chat_model_kwargs(settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY, include_base_url=False)
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model=model_name, **kwargs)


@lru_cache(maxsize=2)
def _openai_chat_class(deepseek_compatible: bool) -> type:
    from langchain_openai import ChatOpenAI

    if not deepseek_compatible:
        return ChatOpenAI

    class SeizuChatOpenAI(SeizuChatDeepSeekMixin, ChatOpenAI):
        pass

    return SeizuChatOpenAI


@lru_cache(maxsize=1)
def _deepseek_chat_class() -> type:
    from langchain_deepseek import ChatDeepSeek

    class SeizuChatDeepSeek(SeizuChatDeepSeekMixin, ChatDeepSeek):
        pass

    return SeizuChatDeepSeek


def _chat_provider() -> str:
    provider = settings.CHAT_LLM_PROVIDER.strip().lower()
    if provider not in _VALID_CHAT_PROVIDERS:
        raise ValueError("CHAT_LLM_PROVIDER must be one of: " + ", ".join(sorted(_VALID_CHAT_PROVIDERS)))
    return provider


def _chat_model_name(provider: str) -> str:
    model = settings.CHAT_LLM_MODEL.strip()
    if not model:
        raise ValueError(
            f"CHAT_LLM_MODEL is required when CHAT_LLM_PROVIDER={provider!r}. Set it to a model identifier "
            "supported by the provider (e.g. an OpenAI/Anthropic/Gemini/DeepSeek model name). The mock provider "
            "is the only one that runs without a model."
        )
    return model


def _chat_model_kwargs(provider_api_key: str, *, include_base_url: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "temperature": settings.CHAT_LLM_TEMPERATURE,
        "timeout": settings.CHAT_LLM_TIMEOUT_SECONDS,
        "max_retries": settings.CHAT_LLM_MAX_RETRIES,
    }
    api_key = settings.CHAT_LLM_API_KEY or provider_api_key
    if api_key:
        kwargs["api_key"] = api_key
    if include_base_url and settings.CHAT_LLM_BASE_URL:
        kwargs["base_url"] = settings.CHAT_LLM_BASE_URL
    return kwargs


def validate_chat_llm_config() -> None:
    """Fail-fast validation called at startup when chat is enabled.

    Raises ``ValueError`` if ``CHAT_LLM_PROVIDER`` is unknown or, for a real
    provider, ``CHAT_LLM_MODEL`` is missing. Catches typos that previously
    surfaced only on the first user request.
    """
    provider = _chat_provider()
    if provider == "mock":
        return
    _chat_model_name(provider)


def _build_checkpointer() -> DynamoDBSaver:
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
    if settings.CHAT_CHECKPOINT_CREATE_TABLE:
        await asyncio.to_thread(_initialize_chat_checkpoints_sync)


def _initialize_chat_checkpoints_sync() -> None:
    checkpointer = _build_checkpointer()
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
