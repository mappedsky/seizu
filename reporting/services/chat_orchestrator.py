"""Plan -> dispatch -> verify orchestration for the Seizu chat agent.

This module adds an *orchestrator-workers* path alongside the existing
single-agent (gather -> act) loop in :mod:`reporting.services.chat_graph`. A
cheap router classifies each turn; complex, multi-step requests are decomposed
by a planner into an explicit, ordered plan, executed step-by-step by scoped
sub-agent workers (run in parallel when steps are independent), checked by a
verify gate with bounded retry, and finally integrated by a synthesizer.

Design notes:

* **Why a separate module.** The orchestrator reuses the turn/tool primitives
  in ``chat_graph`` (``_run_llm_tool_turn``, ``_run_tool_call_batch``,
  ``_list_chat_*``) rather than re-implementing them. ``chat_graph`` imports
  these nodes lazily inside ``build_chat_graph`` to avoid an import cycle.
* **Sub-agent isolation.** Each worker runs its own short ReAct loop over a
  *scoped* message window (the step goal plus only its dependencies' outputs),
  never the full chat history. Worker scratch lives in local variables and is
  never written to ``ChatState["messages"]``, so it never persists or re-enters
  future model context — only the synthesizer's final answer is persisted.
* **Parallelism.** "Parallel when safe" is realized by running an independent
  batch of steps concurrently with ``asyncio.gather`` *inside* the dispatcher
  node, rather than fanning out to separate graph nodes via ``Send``. This keeps
  the dispatcher the sole writer of ``plan``/``step_results`` per super-step, so
  state uses plain overwrite reducers with no concurrent-write hazard.
* **Streaming.** Only the synthesizer emits user-visible ``token`` chunks. The
  router/planner/dispatcher/verifier emit ``detail`` chunks (routing/plan/step/
  verify) on the same channel the UI already renders as ``data-seizu-detail``.
"""

import asyncio
import json
import uuid
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.services import chat_graph
from reporting.services.chat_graph import (
    ChatState,
    ChatToolSpec,
    ToolCallResult,
    _blocked_tool_call_response,
    _chat_provider,
    _client_thread_id_from_config,
    _collect_confirmations_to_run,
    _current_user_from_config,
    _execute_confirmations,
    _last_user_text,
    _list_chat_prompts,
    _list_chat_tools,
    _llm_tool_name,
    _mcp_tool_specs,
    _resume_confirmation_id,
    _run_llm_tool_turn,
    _run_tool_call_batch,
    _skill_tool_specs,
    _tool_call_requests,
    _trim_inner_loop_messages,
    _trim_messages,
    _truncate_text,
    _with_provider_tool_names,
    build_capability_context,
    get_chat_model,
)
from reporting.services.chat_messages import message_text
from reporting.services.mcp_runtime import ChatBlockReason

# Plan-step status lifecycle: pending -> ran (dispatcher) -> passed|failed
# (verifier). Failed steps may be reset to pending for a bounded retry.


# --- Structured-output schemas -------------------------------------------------


class _RouteDecision(BaseModel):
    route: Literal["simple", "orchestrate"]
    reason: str = ""


class _PlannedStep(BaseModel):
    id: str
    goal: str
    depends_on: list[str] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    success_criteria: str = ""


class _Plan(BaseModel):
    steps: list[_PlannedStep] = Field(default_factory=list)


class _Verdict(BaseModel):
    passed: bool
    reason: str = ""


# --- Prompts -------------------------------------------------------------------


_ROUTER_PROMPT = (
    "You route a security-graph assistant's turns. Decide whether the user's"
    " latest message needs multi-step orchestration.\n"
    'Choose "orchestrate" when the request is multi-step, spans several'
    " resources, or chains work (e.g. 'find X, then summarize Y', 'investigate"
    " and report', 'audit across the org'). Choose \"simple\" for greetings,"
    " single lookups, clarifications, or anything answerable in one focused"
    ' step. Prefer "simple" when unsure — orchestration costs more.'
)

_PLANNER_PROMPT = (
    "You are the planner for a security-graph assistant. Produce a concise,"
    " ordered plan of independent-where-possible steps that, executed by"
    " sub-agents with the available tools/skills, fully answer the user's"
    " request. Each step needs a stable short id (e.g. 's1'), a concrete goal,"
    " a success_criteria the result will be checked against, optional"
    " suggested_tools (exact tool/skill names), and depends_on listing the ids"
    " of steps whose output it needs. Keep steps independent unless a real data"
    " dependency exists, so they can run in parallel. Do not invent tools."
)

_SYNTHESIZER_PROMPT = (
    "You are the synthesizer for a security-graph assistant. A plan was executed"
    " step-by-step; you are given each step's goal and result. Integrate them"
    " into one clear, well-structured answer to the user's original request."
    " Use only the step results as evidence; call out any step that failed or"
    " was incomplete. Do not call tools."
)


def _worker_system_prompt(step: dict[str, Any]) -> str:
    base = chat_graph.build_system_prompt()
    criteria = step.get("success_criteria") or ""
    extra = f"\n\nYou are a sub-agent completing exactly ONE step of a larger plan. Step goal: {step.get('goal', '')}."
    if criteria:
        extra += f" Success criteria: {criteria}."
    extra += (
        " Use the available tools/skills to accomplish the goal, then return a"
        " concise factual result for this step only. Do not attempt other"
        " steps or restate the whole conversation."
    )
    return f"{base}{extra}"


def _worker_user_message(step: dict[str, Any], dependency_context: str) -> str:
    parts = [f"Complete this step: {step.get('goal', '')}"]
    if dependency_context:
        parts.append(f"\nRelevant results from prior steps:\n{dependency_context}")
    return "\n".join(parts)


# --- Detail events -------------------------------------------------------------


def _emit(writer: Any, data: dict[str, Any]) -> None:
    writer({"kind": "detail", "id": f"detail_{uuid.uuid4().hex}", "data": data})


def _structured_model(schema: type[BaseModel]) -> Any:
    # get_chat_model() is typed as the minimal ChatModel protocol (astream only);
    # structured output lives on the concrete LangChain model, hence the cast.
    return cast(Any, get_chat_model()).with_structured_output(schema)


# --- Router --------------------------------------------------------------------


async def router_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Classify the turn as simple (existing loop) or orchestrate (plan path)."""
    if not settings.CHAT_ORCHESTRATOR_ENABLED:
        return {"route": "simple"}
    # The mock provider has no real model; orchestration would have nothing to
    # call, so always take the simple path (which mock_agent_node handles).
    if _chat_provider() == "mock":
        return {"route": "simple"}
    # An in-flight plan being resumed (e.g. after an action confirmation) must
    # continue on the orchestrated path rather than be re-routed from scratch.
    if _has_pending_plan(state):
        return {"route": "orchestrate"}

    user_text = _last_user_text(state["messages"])
    if not user_text.strip():
        return {"route": "simple"}

    writer = get_stream_writer()
    try:
        decision = cast(
            _RouteDecision,
            await _structured_model(_RouteDecision).ainvoke(
                [SystemMessage(content=_ROUTER_PROMPT), HumanMessage(content=user_text)],
                config=config,
            ),
        )
    except Exception:
        # Any provider/structured-output failure degrades to the proven path.
        return {"route": "simple"}

    _emit(
        writer,
        {
            "kind": "routing",
            "title": "Routing",
            "status": "completed",
            "route": decision.route,
            "body": decision.reason,
        },
    )
    return {"route": decision.route}


def route_from_router(state: ChatState) -> str:
    return "planner" if state.get("route") == "orchestrate" else "chat_agent"


# --- Planner -------------------------------------------------------------------


async def planner_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Produce the deterministic plan artifact for an orchestrated turn."""
    # Resuming an in-flight plan: keep it, don't replan.
    if _has_pending_plan(state):
        return {}

    current_user = _current_user_from_config(config)
    user_text = _last_user_text(state["messages"])
    writer = get_stream_writer()

    skills = await _list_chat_prompts(current_user)
    tools = await _list_chat_tools(current_user)
    capability = build_capability_context(skills, tools)
    planner_system = f"{_PLANNER_PROMPT}\n\n{capability}" if capability else _PLANNER_PROMPT

    try:
        plan_result = cast(
            _Plan,
            await _structured_model(_Plan).ainvoke(
                [SystemMessage(content=planner_system), HumanMessage(content=user_text)],
                config=config,
            ),
        )
        planned = plan_result.steps[: settings.CHAT_ORCHESTRATOR_MAX_STEPS]
    except Exception:
        planned = []

    if not planned:
        # Fall back to a single step so the orchestrated path still answers.
        planned = [_PlannedStep(id="s1", goal=user_text, success_criteria="Answers the user's request.")]

    plan = _init_plan(planned)
    _emit(
        writer,
        {
            "kind": "plan",
            "title": "Plan",
            "status": "completed",
            "steps": [{"id": s["id"], "goal": s["goal"], "depends_on": s["depends_on"]} for s in plan],
            "body": _plan_summary(plan),
        },
    )
    return {"plan": plan, "step_results": [], "iteration": 0}


def _init_plan(planned: list[_PlannedStep]) -> list[dict[str, Any]]:
    ids = {step.id for step in planned}
    plan: list[dict[str, Any]] = []
    for step in planned:
        plan.append(
            {
                "id": step.id,
                "goal": step.goal,
                # Drop dangling dependencies so a bad reference can't deadlock
                # the runnable-step selection.
                "depends_on": [dep for dep in step.depends_on if dep in ids and dep != step.id],
                "suggested_tools": list(step.suggested_tools),
                "success_criteria": step.success_criteria,
                "status": "pending",
            }
        )
    return plan


def _plan_summary(plan: list[dict[str, Any]]) -> str:
    return "\n".join(f"{index + 1}. {step['goal']}" for index, step in enumerate(plan))


# --- Dispatcher ----------------------------------------------------------------


async def dispatcher_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Run the next batch of runnable steps as scoped sub-agent workers."""
    plan = [dict(step) for step in state.get("plan") or []]
    results = list(state.get("step_results") or [])
    iteration = int(state.get("iteration") or 0)
    current_user = _current_user_from_config(config)
    session_key = _client_thread_id_from_config(config)
    writer = get_stream_writer()

    # Resume path: a prior turn paused this plan on an action confirmation inside
    # a step. Execute the now-approved action(s) and fold the result back in.
    resume_id = _resume_confirmation_id(state["messages"])
    if resume_id and any(step["status"] == "awaiting" for step in plan):
        return await _resume_awaiting_steps(plan, results, iteration, current_user, session_key, writer)

    # Retry path: if the verifier failed retryable steps and budget remains,
    # reset them to pending (carrying the failure reason) and consume one cycle.
    # Steps flagged ``no_retry`` (denied/expired confirmations) are terminal so
    # we never re-prompt the user for an action they already declined.
    failed = [step for step in plan if step["status"] == "failed" and not step.get("no_retry")]
    if failed and iteration < settings.CHAT_ORCHESTRATOR_MAX_ITERATIONS:
        iteration += 1
        results_by_id = {result["step_id"]: result for result in results}
        for step in plan:
            if step["status"] == "failed" and not step.get("no_retry"):
                reason = (results_by_id.get(step["id"], {}) or {}).get("verify_reason", "")
                if reason:
                    step["retry_guidance"] = reason
                step["status"] = "pending"

    runnable = _runnable_steps(plan)
    if not runnable:
        return {"plan": plan, "iteration": iteration}

    batch = runnable[: max(1, settings.CHAT_ORCHESTRATOR_MAX_PARALLEL)]
    for step in batch:
        step["status"] = "ran"

    model = get_chat_model()
    tool_specs = await _worker_tool_specs(current_user)

    new_results = await asyncio.gather(
        *(
            _run_worker_step(
                step,
                plan=plan,
                results=results,
                model=model,
                current_user=current_user,
                session_key=session_key,
                config=config,
                tool_specs=tool_specs,
                writer=writer,
            )
            for step in batch
        )
    )
    merged = _merge_results(results, list(new_results))

    # A step whose worker hit a confirmation gate is parked as "awaiting" so the
    # plan pauses (rather than verifying/retrying) until the user approves.
    merged_by_id = {result["step_id"]: result for result in merged}
    for step in batch:
        if merged_by_id.get(step["id"], {}).get("awaiting_confirmation"):
            step["status"] = "awaiting"
    return {"plan": plan, "step_results": merged, "iteration": iteration}


def route_from_dispatcher(state: ChatState) -> str:
    plan = state.get("plan") or []
    if any(step["status"] == "awaiting" for step in plan):
        return "confirmation_pause"  # halt until the user approves the action
    if any(step["status"] == "ran" for step in plan):
        return "verifier"
    # Nothing runnable remains (all passed, terminal failures, or unsatisfiable
    # dependency) -> synthesize an answer from what completed.
    return "synthesizer"


async def _resume_awaiting_steps(
    plan: list[dict[str, Any]],
    results: list[dict[str, Any]],
    iteration: int,
    current_user: CurrentUser | None,
    session_key: str | None,
    writer: Any,
) -> dict[str, Any]:
    """Execute approved confirmations for parked steps and re-enter the plan.

    Each awaiting step is resolved independently against its own confirmation:
    approved -> run it and mark the step ``ran`` (the verifier judges the
    result); denied/expired -> terminal failure (no re-prompt); still pending ->
    stays ``awaiting`` so the plan pauses again. Reuses the shared, security-
    checked confirmation helpers from ``chat_graph`` so resume rules cannot drift.
    """
    results_by_id = {result["step_id"]: result for result in results}
    for step in plan:
        if step["status"] != "awaiting":
            continue
        result = results_by_id.setdefault(step["id"], {"step_id": step["id"]})
        confirmation_id = result.get("confirmation_id")
        if current_user is None or not confirmation_id:
            step["status"] = "failed"
            step["no_retry"] = True
            result["verify_reason"] = "Could not resume the confirmed action (missing user or confirmation id)."
            continue

        to_run, resolution = await _collect_confirmations_to_run(confirmation_id, current_user, session_key)
        if resolution.kind == "run":
            outcomes, errors, detail_events = await _execute_confirmations(to_run, current_user)
            for detail_data in detail_events:
                _emit(writer, detail_data)
            if outcomes:
                combined = "\n\n".join(f"{name}:\n{_truncate_text(text, 4000)}" for name, text in outcomes)
                result["output"] = combined
                result["blocked"] = None
                result.pop("awaiting_confirmation", None)
                step["status"] = "ran"
            else:
                step["status"] = "failed"
                step["no_retry"] = True
                result["verify_reason"] = "Approved action(s) could not be executed: " + "; ".join(errors)
        elif resolution.kind == "wait":
            # Other approvals in the batch are still outstanding; keep pausing.
            result["confirmation_message"] = resolution.message
        else:  # abort: denied / expired / not found
            step["status"] = "failed"
            step["no_retry"] = True
            result["verify_reason"] = resolution.message

    return {"plan": plan, "step_results": list(results_by_id.values()), "iteration": iteration}


def _confirmation_id_from_content(content: str) -> str | None:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict):
        confirmation_id = data.get("confirmation_id")
        return confirmation_id if isinstance(confirmation_id, str) else None
    return None


def _runnable_steps(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {step["id"]: step for step in plan}
    runnable: list[dict[str, Any]] = []
    for step in plan:
        if step["status"] != "pending":
            continue
        deps = step.get("depends_on") or []
        if all(by_id.get(dep, {}).get("status") == "passed" for dep in deps):
            runnable.append(step)
    return runnable


def _merge_results(existing: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {result["step_id"]: result for result in existing}
    for result in new:
        by_id[result["step_id"]] = result
    return list(by_id.values())


async def _worker_tool_specs(current_user: CurrentUser | None) -> list[ChatToolSpec]:
    skills = await _list_chat_prompts(current_user)
    tools = await _list_chat_tools(current_user)
    return [*_skill_tool_specs(skills), *_mcp_tool_specs(tools)]


def _dependency_context(step: dict[str, Any], plan: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    goals = {item["id"]: item["goal"] for item in plan}
    results_by_id = {result["step_id"]: result for result in results}
    blocks: list[str] = []
    for dep in step.get("depends_on") or []:
        result = results_by_id.get(dep)
        if result and result.get("output"):
            blocks.append(f"- Step {dep} ({goals.get(dep, '')}):\n{_truncate_text(result['output'], 2000)}")
    return "\n".join(blocks)


async def _run_worker_step(
    step: dict[str, Any],
    *,
    plan: list[dict[str, Any]],
    results: list[dict[str, Any]],
    model: Any,
    current_user: CurrentUser | None,
    session_key: str | None,
    config: RunnableConfig,
    tool_specs: list[ChatToolSpec],
    writer: Any,
) -> dict[str, Any]:
    """Run one plan step as an isolated sub-agent; return its result dict."""
    specs = _scoped_tool_specs(tool_specs, step.get("suggested_tools") or [])
    available = _with_provider_tool_names(specs)
    system_prompt = _worker_system_prompt(step)
    if step.get("retry_guidance"):
        system_prompt += f"\n\nA previous attempt was rejected: {step['retry_guidance']}. Address that this time."
    messages: list[BaseMessage] = [
        HumanMessage(content=_worker_user_message(step, _dependency_context(step, plan, results)))
    ]

    _emit(writer, {"kind": "step", "title": f"Step: {step['goal']}", "status": "running", "body": ""})

    action_count = 0
    output_text = ""
    blocked: ChatBlockReason | None = None
    tools_used: list[str] = []
    confirmation_blocked: list[ToolCallResult] = []
    while action_count < settings.CHAT_LLM_MAX_AUTO_ACTIONS:
        # Worker turns never stream user-visible tokens (writer=None); only the
        # synthesizer streams the final answer.
        turn = await _run_llm_tool_turn(model, system_prompt, messages, available, config, None)
        ai_message = turn.message
        requested = _tool_call_requests(ai_message, available)
        if not requested:
            output_text = message_text(ai_message.content)
            break
        remaining = settings.CHAT_LLM_MAX_AUTO_ACTIONS - action_count
        batch = requested[:remaining]
        action_count += len(batch)
        batch_results = await _run_tool_call_batch(
            batch, current_user, session_key=session_key, batch_id=uuid.uuid4().hex
        )
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
                for result in batch_results
            ],
        ]
        messages = _trim_inner_loop_messages(messages, max_chars=settings.CHAT_LLM_CONTEXT_MAX_CHARS)
        for result in batch_results:
            tools_used.append(result.request.name)
            if result.blocked is not None:
                blocked = result.blocked
                output_text = output_text or result.content
                if result.blocked == ChatBlockReason.CONFIRMATION_REQUIRED:
                    confirmation_blocked.append(result)
        if blocked is not None:
            break

    step_result: dict[str, Any] = {
        "step_id": step["id"],
        "goal": step["goal"],
        "success_criteria": step.get("success_criteria", ""),
        "output": output_text,
        "tools_used": tools_used,
        "blocked": blocked.value if blocked is not None else None,
    }
    if confirmation_blocked:
        # The mutating tool created an ActionConfirmation; record what we need to
        # surface the approval prompt now and resume this step once approved.
        step_result["awaiting_confirmation"] = True
        step_result["confirmation_id"] = _confirmation_id_from_content(confirmation_blocked[0].content)
        step_result["confirmation_message"] = _blocked_tool_call_response(confirmation_blocked)
    _emit(
        writer,
        {
            "kind": "step",
            "title": f"Step: {step['goal']}",
            "status": "blocked" if blocked is not None else "completed",
            "body": _truncate_text(output_text, 6000),
        },
    )
    return step_result


def _scoped_tool_specs(tool_specs: list[ChatToolSpec], suggested: list[str]) -> list[ChatToolSpec]:
    if not suggested:
        return tool_specs
    wanted = set(suggested)
    scoped = [spec for spec in tool_specs if spec.name in wanted]
    # If the planner's hints match nothing real, fall back to the full set
    # rather than leaving the worker with no tools.
    return scoped or tool_specs


# --- Verifier ------------------------------------------------------------------


async def verifier_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Check each freshly-run step against its success criteria."""
    plan = [dict(step) for step in state.get("plan") or []]
    results = [dict(result) for result in state.get("step_results") or []]
    results_by_id = {result["step_id"]: result for result in results}
    writer = get_stream_writer()

    for step in plan:
        if step["status"] != "ran":
            continue
        result = results_by_id.get(step["id"], {})
        passed, reason = await _verify_step(step, result, config)
        step["status"] = "passed" if passed else "failed"
        result["verified"] = passed
        result["verify_reason"] = reason
        _emit(
            writer,
            {
                "kind": "verify",
                "title": f"Verify: {step['goal']}",
                "status": "completed" if passed else "blocked",
                "body": reason,
            },
        )
    return {"plan": plan, "step_results": results}


async def _verify_step(step: dict[str, Any], result: dict[str, Any], config: RunnableConfig) -> tuple[bool, str]:
    # A blocked step (e.g. needs confirmation, permission denied) is never a pass.
    if result.get("blocked"):
        return False, f"Step was blocked: {result.get('blocked')}"
    output = result.get("output") or ""
    if not output.strip():
        return False, "Step produced no output."
    criteria = step.get("success_criteria") or "The result accomplishes the step goal."
    prompt = (
        "Judge whether the step result satisfies the success criteria. Be"
        " lenient about formatting but strict about substance.\n\n"
        f"Goal: {step.get('goal', '')}\nSuccess criteria: {criteria}\n\n"
        f"Result:\n{_truncate_text(output, 4000)}"
    )
    try:
        verdict = cast(
            _Verdict,
            await _structured_model(_Verdict).ainvoke([HumanMessage(content=prompt)], config=config),
        )
    except Exception:
        # If verification itself fails, accept the step rather than loop.
        return True, "Verification unavailable; accepted."
    return verdict.passed, verdict.reason


def route_from_verifier(state: ChatState) -> str:
    plan = state.get("plan") or []
    iteration = int(state.get("iteration") or 0)
    if any(step["status"] == "failed" for step in plan):
        if iteration < settings.CHAT_ORCHESTRATOR_MAX_ITERATIONS:
            return "dispatcher"  # bounded retry (dispatcher resets failed steps)
        return "synthesizer"  # budget exhausted: answer with what passed
    if any(step["status"] == "pending" for step in plan):
        return "dispatcher"  # more steps remain
    return "synthesizer"


# --- Synthesizer ---------------------------------------------------------------


async def synthesizer_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Integrate step results into the final, streamed answer."""
    plan = state.get("plan") or []
    results = state.get("step_results") or []
    user_text = _last_user_text(state["messages"])
    writer = get_stream_writer()
    model = get_chat_model()

    context = _synthesis_context(plan, results)
    messages: list[BaseMessage] = [HumanMessage(content=f"User request: {user_text}\n\n{context}")]
    turn = await _run_llm_tool_turn(model, _SYNTHESIZER_PROMPT, messages, [], config, writer)
    response = message_text(turn.message.content)
    if not response:
        response = _synthesis_fallback(plan, results)
        if not turn.streamed:
            writer({"kind": "token", "content": response})

    ai_message = AIMessage(
        content=response,
        id=f"msg_{uuid.uuid4().hex}",
        response_metadata={"seizu_details": _orchestration_details(plan, results)},
    )
    # Clear transient orchestration state so completed runs don't bloat the
    # persisted thread/checkpoint.
    return {
        "messages": [*_trim_messages(state["messages"], ai_message), ai_message],
        "plan": [],
        "step_results": [],
        "iteration": 0,
    }


async def confirmation_pause_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Halt the plan and surface the pending action approval to the user.

    Deliberately does NOT clear ``plan``/``step_results``: they persist in the
    checkpoint so the next turn (carrying ``resume_confirmation_id``) resumes the
    parked steps via the dispatcher.
    """
    plan = state.get("plan") or []
    results = state.get("step_results") or []
    writer = get_stream_writer()
    results_by_id = {result["step_id"]: result for result in results}

    messages: list[str] = []
    for step in plan:
        if step["status"] == "awaiting":
            message = results_by_id.get(step["id"], {}).get("confirmation_message")
            if message:
                messages.append(message)
    # dict.fromkeys dedupes a shared batch URL surfaced by multiple steps.
    response = "\n\n".join(dict.fromkeys(messages)) or "Approval is needed before I can continue this plan."
    writer({"kind": "token", "content": response})

    ai_message = AIMessage(
        content=response,
        id=f"msg_{uuid.uuid4().hex}",
        response_metadata={"seizu_details": _orchestration_details(plan, results)},
    )
    return {"messages": [*_trim_messages(state["messages"], ai_message), ai_message]}


def _synthesis_context(plan: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    results_by_id = {result["step_id"]: result for result in results}
    blocks: list[str] = []
    for step in plan:
        result = results_by_id.get(step["id"], {})
        status = step.get("status", "")
        output = result.get("output") or "(no output)"
        blocks.append(f"### Step {step['id']} — {step['goal']} [{status}]\n{_truncate_text(output, 4000)}")
    return "Executed plan and results:\n\n" + "\n\n".join(blocks)


def _synthesis_fallback(plan: list[dict[str, Any]], results: list[dict[str, Any]]) -> str:
    passed = sum(1 for step in plan if step.get("status") == "passed")
    return (
        f"I ran a {len(plan)}-step plan ({passed} step(s) verified) but could not produce a"
        " final summary. Here is what each step found:\n\n" + _synthesis_context(plan, results)
    )


def _orchestration_details(plan: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild the orchestration trace for history replay from persisted state."""
    results_by_id = {result["step_id"]: result for result in results}
    details: list[dict[str, Any]] = [
        {
            "kind": "plan",
            "title": "Plan",
            "status": "completed",
            "steps": [{"id": step["id"], "goal": step["goal"], "depends_on": step["depends_on"]} for step in plan],
            "body": _plan_summary(plan),
        }
    ]
    for step in plan:
        result = results_by_id.get(step["id"], {})
        details.append(
            {
                "kind": "step",
                "title": f"Step: {step['goal']}",
                "status": "blocked" if result.get("blocked") else "completed",
                "body": _truncate_text(result.get("output", ""), 6000),
            }
        )
        if "verified" in result:
            details.append(
                {
                    "kind": "verify",
                    "title": f"Verify: {step['goal']}",
                    "status": "completed" if result.get("verified") else "blocked",
                    "body": result.get("verify_reason", ""),
                }
            )
    return details


# --- Helpers -------------------------------------------------------------------


def _has_pending_plan(state: ChatState) -> bool:
    plan = state.get("plan") or []
    return any(step.get("status") in ("pending", "ran", "failed", "awaiting") for step in plan)
