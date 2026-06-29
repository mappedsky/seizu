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
import logging
import uuid
from dataclasses import replace
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.services import chat_graph, mcp_builtins
from reporting.services.chat_budget import BudgetController, BudgetExceeded, budget_controller_from_config
from reporting.services.chat_graph import (
    ChatState,
    ChatToolSpec,
    ToolCallResult,
    _action_transcript_retry_message,
    _ai_message_for_tool_results,
    _append_output_limit_notice,
    _auto_continue_answer,
    _blocked_tool_call_response,
    _chat_provider,
    _client_thread_id_from_config,
    _collect_confirmations_to_run,
    _confirmation_batch_id_for_requests,
    _current_user_from_config,
    _disclosed_tool_names_from_skill_results,
    _execute_confirmations,
    _internal_action_transcript_leaked,
    _invoke_structured_output,
    _is_continuation_turn,
    _last_user_request,
    _list_chat_prompts,
    _list_chat_tools,
    _llm_tool_name,
    _mcp_tool_specs,
    _resume_confirmation_id,
    _run_llm_tool_turn,
    _run_tool_call_batch,
    _skill_tool_specs,
    _tool_call_detail_data,
    _tool_call_requests,
    _trim_inner_loop_messages,
    _trim_messages,
    _truncate_text,
    _with_provider_tool_names,
    build_capability_context,
    finalize_assistant_message,
    get_chat_model,
)
from reporting.services.chat_messages import message_text
from reporting.services.mcp_runtime import ChatBlockReason

logger = logging.getLogger(__name__)

# Plan-step status lifecycle: pending -> ran (dispatcher) -> passed|failed
# (verifier). Failed steps may be reset to pending for a bounded retry.

_STEP_TOKEN_ESTIMATES = {"small": 4_000, "medium": 8_000, "large": 16_000}


def _safe_exception_text(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return _truncate_text(str(exc), 1000)
    return exc.__class__.__name__


def _budget_controller(config: RunnableConfig) -> BudgetController | None:
    return budget_controller_from_config(config)


def _budget_state(config: RunnableConfig) -> dict[str, Any]:
    controller = _budget_controller(config)
    return {"budget": controller.snapshot()} if controller is not None else {}


def _refresh_remaining_estimate(controller: BudgetController | None, plan: list[dict[str, Any]]) -> None:
    if controller is None:
        return
    unfinished = sum(
        int(step.get("estimated_tokens") or 0)
        for step in plan
        if step.get("status") in ("pending", "ran", "failed", "awaiting")
    )
    controller.set_estimated_remaining_tokens(unfinished)


# --- Structured-output schemas -------------------------------------------------


class _RouteDecision(BaseModel):
    route: Literal["simple", "orchestrate"]
    reason: str = ""


class _PlannedStep(BaseModel):
    id: str
    goal: str
    depends_on: list[str] = Field(default_factory=list)
    suggested_tools: list[str] = Field(default_factory=list)
    action_kind: Literal["auto", "answer", "skill", "tool"] = "auto"
    required_action: str = ""
    required_arguments: dict[str, Any] = Field(default_factory=dict)
    success_criteria: str = ""
    priority: Literal["required", "supporting", "optional"] = "required"
    complexity: Literal["small", "medium", "large"] = "medium"


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
    " and report', 'audit across the org', 'review GitHub security, choose the"
    " highest-risk remotely exploitable CVE, then trace attack paths'). Route to"
    ' "orchestrate" when later work depends on facts discovered by earlier'
    ' work. Choose "simple" for greetings, single lookups, clarifications, or'
    ' anything answerable in one focused step. Prefer "simple" only when the'
    " user is not asking for a chained workflow."
)

_PLANNER_PROMPT = (
    "You are the planner for a security-graph assistant. Produce a concise,"
    " ordered plan of independent-where-possible steps that, executed by"
    " sub-agents with the available tools/skills, fully answer the user's"
    " request. Each step needs a stable short id (e.g. 's1'), a concrete goal,"
    " depends_on listing the ids of steps whose output it needs, action_kind,"
    " required_action, optional required_arguments, and success_criteria. Use"
    ' action_kind="skill" when the step must render/run a skill, "tool" when'
    ' it must call a specific tool, and "answer" only for a synthesis/selection'
    " step that needs no live action. For skill/tool steps, required_action must"
    " be the exact listed skill/tool name; required_arguments should include"
    " known static arguments and must OMIT any value that has to be derived from"
    " a dependency result (do not put a placeholder like '<from s2>' — leave the"
    " argument out and the sub-agent will fill it). Put the same exact name in"
    " suggested_tools. Keep steps"
    " independent unless a real data dependency exists, so they can run in"
    " parallel. Mark each step priority as required, supporting, or optional,"
    " and complexity as small, medium, or large. Do not invent tools or mark a"
    " live-data step as answer."
)

_SYNTHESIZER_PROMPT = (
    "You are the synthesizer for a security-graph assistant. A plan was executed"
    " step-by-step; you are given each step's goal and result. Integrate them"
    " into one clear, well-structured answer to the user's original request."
    " Use only the step results as evidence; call out any step that failed or"
    " was incomplete. Do not call tools. Do not copy internal execution"
    " transcripts, tool names, tool arguments, or raw returned JSON; translate"
    " the evidence into conclusions, impact, and next actions."
)


def _worker_system_prompt(step: dict[str, Any]) -> str:
    base = chat_graph.build_system_prompt()
    criteria = step.get("success_criteria") or ""
    extra = f"\n\nYou are a sub-agent completing exactly ONE step of a larger plan. Step goal: {step.get('goal', '')}."
    if criteria:
        extra += f" Success criteria: {criteria}."
    action_kind = step.get("action_kind") or "auto"
    required_action = step.get("required_action") or ""
    required_arguments = step.get("required_arguments") or {}
    if action_kind in ("skill", "tool") and required_action:
        extra += (
            f" This step has a required {action_kind} action: `{required_action}`. You must call that exact"
            " structured action before returning a step result."
        )
        if required_arguments:
            extra += f" Required/static arguments: {_truncate_text(json.dumps(required_arguments, default=str), 1000)}."
    elif action_kind == "answer":
        extra += " This is an answer-only step: do not call tools; use the dependency context and return the result."
    extra += (
        " Use the available tools/skills to accomplish the goal, then return a"
        " concise factual result for this step only. Do not list internal action"
        " transcripts, tool names, arguments, or raw JSON unless the step goal"
        " explicitly requires raw data. Do not attempt other steps or restate"
        " the whole conversation."
    )
    return f"{base}{extra}"


def _worker_user_message(step: dict[str, Any], dependency_context: str) -> str:
    parts = [f"Complete this step: {step.get('goal', '')}"]
    if dependency_context:
        parts.append(f"\nRelevant results from prior steps:\n{dependency_context}")
    return "\n".join(parts)


def _worker_budget_exhausted_message() -> str:
    return (
        "You have used this step's tool-call budget. Do not call any more tools. Using the tool results already in "
        "this conversation, write a concise factual result for this step: what you accomplished, the concrete "
        "findings or values produced, and any part of the step's goal that was not completed and still remains."
    )


# --- Detail events -------------------------------------------------------------


def _emit(writer: Any, data: dict[str, Any], detail_id: str | None = None) -> None:
    # A stable id lets the UI reconcile successive states of the same detail
    # (e.g. a step going running -> completed) into one entry instead of leaving
    # a stale "running" duplicate behind.
    writer({"kind": "detail", "id": detail_id or f"detail_{uuid.uuid4().hex}", "data": data})


def _step_detail_id(step_id: str) -> str:
    return f"step-{step_id}"


def _verify_detail_id(step_id: str) -> str:
    return f"verify-{step_id}"


async def _structured_invoke(
    schema: type[BaseModel],
    messages: list[BaseMessage],
    config: RunnableConfig,
    *,
    role: str,
    allow_reserve: bool = False,
    max_output_tokens: int = 1024,
) -> BaseModel:
    controller = budget_controller_from_config(config)
    economy = bool(controller and controller.degraded and role in ("worker", "synthesizer"))
    return await _invoke_structured_output(
        get_chat_model(role, economy=economy),
        schema,
        messages,
        config,
        allow_reserve=allow_reserve,
        phase=role,
        max_output_tokens=max_output_tokens,
    )


# --- Router --------------------------------------------------------------------


def _forced_route(state: ChatState, config: RunnableConfig) -> str | None:
    """Deterministic routing that must not depend on the LLM classifier.

    Centralizes every special-turn short-circuit in one place so neither the
    router nor a future caller can forget one (the divergence that broke
    continuation). Returns ``"simple"``/``"orchestrate"`` for a special turn, or
    ``None`` to let the model classify a genuine new-task request.
    """
    if not settings.CHAT_ORCHESTRATOR_ENABLED:
        return "simple"
    # The mock provider has no real model; orchestration would have nothing to
    # call, so always take the simple path (which mock_agent_node handles).
    if _chat_provider() == "mock":
        return "simple"
    # An in-flight plan being resumed (e.g. after an action confirmation) must
    # continue on the orchestrated path rather than be re-routed from scratch.
    if _has_pending_plan(state):
        return "orchestrate"
    # Continuation ("continue this response") and simple confirmation-resume turns
    # are owned by the single-agent path: chat_agent_node extends the prior answer
    # (and emits the cut-off/finish-reason that drives the "Continue response"
    # button) or resumes one confirmed tool call. The planner would replan from
    # scratch and drop the continuation, so never route these to orchestrate.
    if _is_continuation_turn(state["messages"]) or _resume_confirmation_id(state["messages"]):
        return "simple"
    return None


async def router_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Classify the turn as simple (existing loop) or orchestrate (plan path)."""
    forced = _forced_route(state, config)
    if forced is not None:
        logger.info("chat router: forced route=%s", forced)
        return {"route": forced, **_budget_state(config)}

    user_text = _last_user_request(state["messages"])
    if not user_text.strip():
        return {"route": "simple", **_budget_state(config)}

    writer = get_stream_writer()
    try:
        decision = cast(
            _RouteDecision,
            await _structured_invoke(
                _RouteDecision,
                [SystemMessage(content=_ROUTER_PROMPT), HumanMessage(content=user_text)],
                config,
                role="router",
            ),
        )
    except Exception:
        # Any provider/structured-output failure degrades to the proven path.
        # Log it: a silent degrade here bypasses the whole orchestrator (plan +
        # verify), so an invisible router failure looks like the agent simply
        # ignoring a multi-step request.
        logger.warning("Router structured-output failed; degrading to the single-agent path", exc_info=True)
        return {"route": "simple", **_budget_state(config)}

    # Always-on so a run can be traced without reproducing a failure: this is the
    # single fact that explains whether a turn used the orchestrator or the
    # single-agent loop.
    logger.info("chat router: route=%s reason=%s", decision.route, _truncate_text(decision.reason, 300))
    _emit(
        writer,
        {
            "kind": "routing",
            "title": "Routing",
            "status": "completed",
            "route": decision.route,
            "body": decision.reason,
        },
        "routing",
    )
    return {"route": decision.route, **_budget_state(config)}


def route_from_router(state: ChatState) -> str:
    return "planner" if state.get("route") == "orchestrate" else "chat_agent"


# --- Planner -------------------------------------------------------------------


async def planner_node(state: ChatState, config: RunnableConfig) -> dict[str, Any]:
    """Produce the deterministic plan artifact for an orchestrated turn."""
    # Resuming an in-flight plan: keep it, don't replan.
    if _has_pending_plan(state):
        return {}

    current_user = _current_user_from_config(config)
    user_text = _last_user_request(state["messages"])
    writer = get_stream_writer()

    skills = await _list_chat_prompts(current_user)
    # Under progressive disclosure the planner sees skills and always-disclosed
    # tools (tools the model can always reach without a skill unlock, e.g.
    # sandbox__delegate) so it can plan their use from the start.
    always_disclosed_tools_for_capability: list[chat_graph.Tool] = []
    if settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE:
        capability_tools = None
        _always_disclosed_names = mcp_builtins.always_disclosed_tool_names()
        if _always_disclosed_names:
            _all_tools = await _list_chat_tools(current_user)
            always_disclosed_tools_for_capability = [t for t in _all_tools if t.name in _always_disclosed_names]
    else:
        capability_tools = await _list_chat_tools(current_user)
    capability = build_capability_context(
        skills,
        capability_tools,
        always_disclosed_tools=always_disclosed_tools_for_capability,
    )
    planner_system = f"{_PLANNER_PROMPT}\n\n{capability}" if capability else _PLANNER_PROMPT

    run_errors: list[str] = []
    try:
        plan_result = cast(
            _Plan,
            await _structured_invoke(
                _Plan,
                [SystemMessage(content=planner_system), HumanMessage(content=user_text)],
                config,
                role="planner",
                max_output_tokens=settings.CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS,
            ),
        )
        planned = plan_result.steps[: settings.CHAT_ORCHESTRATOR_MAX_STEPS]
    except BudgetExceeded as exc:
        controller = _budget_controller(config)
        if controller is not None:
            controller.begin_finalization(str(exc))
        planned = []
        run_errors = [str(exc)]
    except Exception as exc:
        logger.warning("Planner structured-output failed; falling back to a single-step plan", exc_info=True)
        planned = []
        run_errors = [f"Planner structured output failed: {_safe_exception_text(exc)}"]

    if not planned:
        # Fall back to a single step so the orchestrated path still answers.
        planned = [_PlannedStep(id="s1", goal=user_text, success_criteria="Answers the user's request.")]

    plan = _init_plan(planned)
    _refresh_remaining_estimate(_budget_controller(config), plan)
    _emit(
        writer,
        {
            "kind": "plan",
            "title": "Plan",
            "status": "completed",
            "steps": [{"id": s["id"], "goal": s["goal"], "depends_on": s["depends_on"]} for s in plan],
            "body": _plan_summary(plan),
        },
        "plan",
    )
    return {
        "plan": plan,
        "step_results": [],
        "iteration": 0,
        "run_errors": run_errors,
        **_budget_state(config),
    }


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
                "action_kind": step.action_kind,
                "required_action": step.required_action,
                "required_arguments": dict(step.required_arguments),
                "success_criteria": step.success_criteria,
                "priority": step.priority,
                "complexity": step.complexity,
                "estimated_tokens": _STEP_TOKEN_ESTIMATES[step.complexity],
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
    controller = _budget_controller(config)

    # Resume path: a prior turn paused this plan on an action confirmation inside
    # a step. Execute the now-approved action(s) and fold the result back in.
    resume_id = _resume_confirmation_id(state["messages"])
    if resume_id and any(step["status"] == "awaiting" for step in plan):
        return await _resume_awaiting_steps(plan, results, iteration, current_user, session_key, writer)

    if controller is not None and controller.degraded:
        for step in plan:
            if step["status"] == "pending" and step.get("priority") == "optional":
                step["status"] = "skipped"
                results = _merge_results(
                    results,
                    [
                        {
                            "step_id": step["id"],
                            "goal": step["goal"],
                            "output": "",
                            "tools_used": [],
                            "budget_skipped": True,
                            "verify_reason": "Optional step removed after the run crossed its soft budget limit.",
                        }
                    ],
                )

    if controller is not None and controller.finalizing:
        for step in plan:
            if step["status"] in ("pending", "failed"):
                step["status"] = "skipped"
                results = _merge_results(
                    results,
                    [
                        {
                            "step_id": step["id"],
                            "goal": step["goal"],
                            "output": "",
                            "tools_used": [],
                            "budget_exhausted": True,
                            "verify_reason": controller.snapshot().get("exhaustion_reason"),
                        }
                    ],
                )
        _refresh_remaining_estimate(controller, plan)
        return {"plan": plan, "step_results": results, "iteration": iteration, **_budget_state(config)}

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
        _refresh_remaining_estimate(controller, plan)
        return {"plan": plan, "step_results": results, "iteration": iteration, **_budget_state(config)}

    batch = runnable[: max(1, settings.CHAT_ORCHESTRATOR_MAX_PARALLEL)]
    for step in batch:
        step["status"] = "ran"

    model = get_chat_model("worker", economy=bool(controller and controller.degraded))
    tool_specs = await _worker_tool_specs(current_user)
    # Progressive disclosure carries across steps: tools a skill disclosed in an
    # earlier super-step stay callable for the dependent steps that follow.
    progressive = settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE
    disclosed_names = set(state.get("disclosed_tools") or []) if progressive else set()

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
                disclosed_names=disclosed_names,
                progressive=progressive,
                writer=writer,
            )
            for step in batch
        )
    )
    merged = _merge_results(results, list(new_results))
    for result in new_results:
        disclosed_names.update(result.get("disclosed_tools") or [])

    # A step whose worker hit a confirmation gate is parked as "awaiting" so the
    # plan pauses (rather than verifying/retrying) until the user approves.
    merged_by_id = {result["step_id"]: result for result in merged}
    for step in batch:
        if merged_by_id.get(step["id"], {}).get("awaiting_confirmation"):
            step["status"] = "awaiting"
    update: dict[str, Any] = {"plan": plan, "step_results": merged, "iteration": iteration}
    if progressive:
        update["disclosed_tools"] = sorted(disclosed_names)
    _refresh_remaining_estimate(controller, plan)
    update.update(_budget_state(config))
    return update


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
                # The approved action ran; the verifier auto-passes it so the plan
                # does not retry an already-applied change.
                result["confirmation_executed"] = True
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
    disclosed_names: set[str] | None = None,
    progressive: bool | None = None,
    writer: Any = None,
) -> dict[str, Any]:
    """Run one plan step as an isolated sub-agent; return its result dict.

    ``tool_specs`` is the full universe of skills + tools; under progressive
    disclosure only skills and already-``disclosed_names`` tools are callable at
    the start, with the rest unlocked when a rendered skill declares them.
    """
    step_id = str(step["id"])
    if progressive is None:
        progressive = settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE
    disclosed_names = set(disclosed_names or ())
    _always_disclosed_names = mcp_builtins.always_disclosed_tool_names() if progressive else frozenset()
    available_pool = (
        tool_specs
        if not progressive
        else [
            spec
            for spec in tool_specs
            if spec.kind == "skill" or spec.name in disclosed_names or spec.name in _always_disclosed_names
        ]
    )
    specs, contract_error = _step_tool_specs(available_pool, step)
    if contract_error:
        contract_result = _step_contract_error_result(step, contract_error)
        _emit(
            writer,
            {
                "kind": "step",
                "title": f"Step: {step['goal']}",
                "status": "blocked",
                "step_id": step_id,
                "body": contract_error,
            },
            _step_detail_id(step_id),
        )
        return contract_result
    # Normalize required_action to the canonical (fully-qualified) name the spec
    # resolved to, so the downstream "did it call the required action" and
    # argument-enforcement checks compare against the names that appear in
    # tools_used (which are always fully qualified).
    if step.get("action_kind") in ("skill", "tool") and specs:
        step = {**step, "required_action": specs[0].name}
    # Progressive disclosure inside the worker: a skill step starts with only the
    # skill spec, but rendering a skill discloses the tools it declares. Those must
    # become callable for the rest of this step, or a meta-skill (e.g. one whose
    # workflow says "call these sub-tools") can never reach its data — the skill
    # renders, the sub-tools stay invisible, and the step produces no findings.
    active_specs = list(specs)
    active_names = {spec.name for spec in active_specs}
    # Always-disclosed tools (e.g. sandbox__delegate) must be present from the
    # first turn of every worker step, even for single-action skill steps where
    # _step_tool_specs only returns the required spec.  Without this, a skill
    # that renders "call sandbox__delegate" would do so with sandbox__delegate
    # absent from the model's tool list — the model produces text instead of a
    # tool call and the step fails.
    for spec in available_pool:
        if spec.name in _always_disclosed_names and spec.name not in active_names:
            active_specs.append(spec)
            active_names.add(spec.name)
    newly_disclosed_names: set[str] = set()
    available = _with_provider_tool_names(active_specs)
    system_prompt = _worker_system_prompt(step)
    if step.get("retry_guidance"):
        system_prompt += f"\n\nA previous attempt was rejected: {step['retry_guidance']}. Address that this time."
    messages: list[BaseMessage] = [
        HumanMessage(content=_worker_user_message(step, _dependency_context(step, plan, results)))
    ]

    _emit(
        writer,
        {"kind": "step", "title": f"Step: {step['goal']}", "status": "running", "step_id": step_id, "body": ""},
        _step_detail_id(step_id),
    )

    action_count = 0
    step_input_tokens = 0
    step_output_tokens = 0
    step_cost_usd = 0.0
    output_text = ""
    blocked: ChatBlockReason | None = None
    tools_used: list[str] = []
    confirmation_blocked: list[ToolCallResult] = []
    required_action = str(step.get("required_action") or "")
    execution_error = ""
    budget_exhausted = False
    step_budget = int(step.get("estimated_tokens") or _STEP_TOKEN_ESTIMATES["medium"])
    controller = _budget_controller(config)
    action_limit = (
        None if controller is not None and controller.enabled else settings.CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS
    )
    while action_limit is None or action_count < action_limit:
        # Worker turns never stream user-visible tokens (writer=None); only the
        # synthesizer streams the final answer.
        step_degraded = step_input_tokens + step_output_tokens >= step_budget
        active_model = (
            get_chat_model("worker", economy=True)
            if (step_degraded or (controller is not None and controller.degraded))
            and settings.CHAT_LLM_ECONOMY_MODEL.strip()
            else model
        )
        try:
            turn = await _run_llm_tool_turn(
                active_model,
                system_prompt,
                messages,
                available,
                config,
                None,
                phase=f"worker:{step_id}",
            )
        except BudgetExceeded as exc:
            budget_exhausted = True
            execution_error = str(exc)
            if controller is not None:
                controller.begin_finalization(str(exc))
            break
        step_input_tokens += turn.input_tokens
        step_output_tokens += turn.output_tokens
        step_cost_usd += turn.cost_usd
        ai_message = turn.message
        requested = _tool_call_requests(ai_message, available)
        if not requested:
            output_text = message_text(ai_message.content)
            break
        remaining = len(requested) if action_limit is None else action_limit - action_count
        batch = requested[:remaining]
        batch = _apply_planned_arguments(step, batch)
        action_count += len(batch)
        batch_kwargs: dict[str, Any] = {}
        if chat_graph._bypass_confirmations_from_config(config):
            batch_kwargs["bypass_confirmations"] = True
        batch_results = await _run_tool_call_batch(
            batch,
            current_user,
            session_key=session_key,
            batch_id=_confirmation_batch_id_for_requests(batch),
            **batch_kwargs,
        )
        # Surface each tool/skill call as a detail tagged with this step, so the UI
        # can nest the calls under the step that made them.
        for result in batch_results:
            _emit(writer, {**_tool_call_detail_data(result), "step_id": step_id})
        tool_ai_message = _ai_message_for_tool_results(ai_message, batch_results)
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
                for result in batch_results
            ],
        ]
        context_limit = settings.CHAT_LLM_CONTEXT_MAX_CHARS
        if (controller is not None and controller.degraded) or step_degraded:
            context_limit = max(8_000, context_limit // 4)
        messages = _trim_inner_loop_messages(messages, max_chars=context_limit)
        for result in batch_results:
            tools_used.append(result.request.name)
            if result.blocked is not None:
                blocked = result.blocked
                output_text = output_text or result.content
                if result.blocked == ChatBlockReason.CONFIRMATION_REQUIRED:
                    confirmation_blocked.append(result)
        if blocked is not None:
            break
        # Surface any tools a rendered skill just disclosed so the next turn can
        # call them. Looked up from the full worker tool universe, not re-fetched;
        # the names also propagate to dependent steps via the dispatcher.
        newly_disclosed = _disclosed_tool_names_from_skill_results(batch_results)
        added = [spec for spec in tool_specs if spec.name in newly_disclosed and spec.name not in active_names]
        if added:
            active_specs.extend(added)
            active_names.update(spec.name for spec in added)
            newly_disclosed_names.update(spec.name for spec in added)
            available = _with_provider_tool_names(active_specs)

    if not execution_error and _step_requires_action(step) and required_action not in tools_used and blocked is None:
        execution_error = f"Step required structured action `{required_action}`, but the worker did not call it."
        output_text = ""

    # The worker took tool actions but never produced a final text result. This
    # can happen at the interactive loop guard or when the shared run budget
    # enters finalization. Summarize progress rather than returning an empty step.
    if not output_text.strip() and blocked is None and tools_used:
        summary_model = (
            get_chat_model("worker", economy=True)
            if controller is not None and controller.degraded and settings.CHAT_LLM_ECONOMY_MODEL.strip()
            else model
        )
        try:
            synthesis = await _run_llm_tool_turn(
                summary_model,
                f"{system_prompt}\n\n{_worker_budget_exhausted_message()}",
                messages,
                [],
                config,
                None,
                allow_reserve=budget_exhausted,
                phase=f"worker_summary:{step_id}",
                max_output_tokens=1024,
            )
            step_input_tokens += synthesis.input_tokens
            step_output_tokens += synthesis.output_tokens
            step_cost_usd += synthesis.cost_usd
            output_text = message_text(synthesis.message.content)
            if budget_exhausted:
                execution_error = ""
        except BudgetExceeded:
            pass

    step_result: dict[str, Any] = {
        "step_id": step["id"],
        "goal": step["goal"],
        "success_criteria": step.get("success_criteria", ""),
        "output": output_text,
        "tools_used": tools_used,
        "blocked": blocked.value if blocked is not None else None,
        "input_tokens": step_input_tokens,
        "output_tokens": step_output_tokens,
        "cost_usd": step_cost_usd,
        "estimated_tokens": step_budget,
    }
    if newly_disclosed_names:
        # Propagate to the dispatcher so dependent steps inherit the disclosure.
        step_result["disclosed_tools"] = sorted(newly_disclosed_names)
    if execution_error:
        step_result["execution_error"] = execution_error
    if budget_exhausted:
        step_result["budget_exhausted"] = True
    if confirmation_blocked:
        # The mutating tool created an ActionConfirmation; record what we need to
        # surface the approval prompt now and resume this step once approved.
        step_result["awaiting_confirmation"] = True
        step_result["confirmation_id"] = _confirmation_id_from_content(confirmation_blocked[0].content)
        step_result["confirmation_message"] = _blocked_tool_call_response(confirmation_blocked)
    if confirmation_blocked:
        step_status = "awaiting"  # parked on an approval; a wait, not a failure
    elif blocked is not None or execution_error:
        step_status = "blocked"
    else:
        step_status = "completed"
    _emit(
        writer,
        {
            "kind": "step",
            "title": f"Step: {step['goal']}",
            "status": step_status,
            "step_id": step_id,
            "body": _truncate_text(execution_error or output_text, 6000),
        },
        _step_detail_id(step_id),
    )
    return step_result


def _match_action_spec(tool_specs: list[ChatToolSpec], action_kind: str, required_action: str) -> ChatToolSpec | None:
    """Resolve a planner ``required_action`` to a concrete spec.

    Names are fully qualified (``skillset__skill`` / ``toolset__tool``), but the
    planner often references the short id (``github_org_security_overview``). Try
    an exact match first, then a unique match on the action part after ``__``.
    """
    candidates = [spec for spec in tool_specs if spec.kind == action_kind]
    exact = [spec for spec in candidates if spec.name == required_action]
    if exact:
        return exact[0]
    short = [spec for spec in candidates if spec.name.split("__", 1)[-1] == required_action]
    if len(short) == 1:
        return short[0]
    suffix = [spec for spec in candidates if spec.name.endswith(f"__{required_action}")]
    if len(suffix) == 1:
        return suffix[0]
    return None


def _step_tool_specs(tool_specs: list[ChatToolSpec], step: dict[str, Any]) -> tuple[list[ChatToolSpec], str | None]:
    action_kind = step.get("action_kind") or "auto"
    required_action = str(step.get("required_action") or "")
    if action_kind == "answer":
        return [], None
    if action_kind in ("skill", "tool") and required_action:
        spec = _match_action_spec(tool_specs, action_kind, required_action)
        if spec is None:
            return [], f"Required {action_kind} action `{required_action}` is not available to this chat session."
        return [spec], None
    if action_kind in ("skill", "tool") and not required_action:
        return [], f"Planner marked this as a {action_kind} step but did not provide required_action."
    return _scoped_tool_specs(tool_specs, step.get("suggested_tools") or []), None


def _step_requires_action(step: dict[str, Any]) -> bool:
    return (step.get("action_kind") in ("skill", "tool")) and bool(step.get("required_action"))


def _step_contract_error_result(step: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "step_id": step["id"],
        "goal": step["goal"],
        "success_criteria": step.get("success_criteria", ""),
        "output": "",
        "tools_used": [],
        "blocked": None,
        "execution_error": error,
    }


def _apply_planned_arguments(
    step: dict[str, Any], requests: list[chat_graph.ToolCallRequest]
) -> list[chat_graph.ToolCallRequest]:
    """Fill in planner-specified arguments the worker omitted — a hint, not a rule.

    The planner guesses arguments before execution; the worker sees the live tool
    schema and the dependency results, so its explicit values always win and are
    never overridden or rejected. We only supply an argument the worker left out
    (with ``setdefault``). Step correctness is the verifier's job.

    This deliberately replaces an earlier strict-match check: matching the
    planner's value exactly was both wrong (it blocked correct,
    dependency-derived values like a CVE id the planner could only template as
    "<from s2>") and brittle (it relied on guessing the planner's placeholder
    format to know what not to enforce).
    """
    required_action = str(step.get("required_action") or "")
    required_arguments = step.get("required_arguments") or {}
    if not required_action or not isinstance(required_arguments, dict) or not required_arguments:
        return requests

    applied: list[chat_graph.ToolCallRequest] = []
    for request in requests:
        if request.name != required_action:
            applied.append(request)
            continue
        merged = dict(request.arguments)
        for key, value in required_arguments.items():
            merged.setdefault(key, value)
        applied.append(replace(request, arguments=merged))
    return applied


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
                "step_id": str(step["id"]),
                "body": reason,
            },
            _verify_detail_id(str(step["id"])),
        )
    _refresh_remaining_estimate(_budget_controller(config), plan)
    return {"plan": plan, "step_results": results, **_budget_state(config)}


async def _verify_step(step: dict[str, Any], result: dict[str, Any], config: RunnableConfig) -> tuple[bool, str]:
    # A step whose mutating action the user explicitly approved and that executed
    # is done: do not LLM-re-judge the raw tool output (which reads as data, not a
    # success narrative) and do not retry — retrying re-runs the whole worker and
    # would re-attempt the already-applied change.
    if result.get("confirmation_executed"):
        return True, "User-approved action executed."
    # A blocked step (e.g. needs confirmation, permission denied) is never a pass.
    if result.get("blocked"):
        return False, f"Step was blocked: {result.get('blocked')}"
    if result.get("execution_error"):
        return False, str(result["execution_error"])
    if result.get("budget_exhausted"):
        return False, "Step stopped because the run budget entered finalization."
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
            await _structured_invoke(
                _Verdict,
                [HumanMessage(content=prompt)],
                config,
                role="verifier",
            ),
        )
    except BudgetExceeded as exc:
        controller = _budget_controller(config)
        if controller is not None:
            controller.begin_finalization(str(exc))
        return False, str(exc)
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
    user_text = _last_user_request(state["messages"])
    writer = get_stream_writer()
    controller = _budget_controller(config)
    model = get_chat_model("synthesizer", economy=bool(controller and controller.degraded))

    context = _synthesis_context(plan, results)
    messages: list[BaseMessage] = [HumanMessage(content=f"User request: {user_text}\n\n{context}")]
    try:
        turn = await _run_llm_tool_turn(
            model,
            _SYNTHESIZER_PROMPT,
            messages,
            [],
            config,
            None,
            allow_reserve=True,
            phase="synthesizer",
            max_output_tokens=min(settings.CHAT_LLM_MAX_TOKENS, 2048),
        )
        response = message_text(turn.message.content)
        streamed = turn.streamed
    except BudgetExceeded as exc:
        if controller is not None:
            controller.mark_exhausted(str(exc))
        turn = None
        response = ""
        streamed = ""
    output_limit = False
    details = _orchestration_details(plan, results)
    if response and turn is not None and _internal_action_transcript_leaked(response):
        retry_prompt = f"{_SYNTHESIZER_PROMPT}\n\n{_action_transcript_retry_message()}"
        turn = await _run_llm_tool_turn(
            model,
            retry_prompt,
            messages,
            [],
            config,
            None,
            allow_reserve=True,
            phase="synthesizer",
            max_output_tokens=min(settings.CHAT_LLM_MAX_TOKENS, 2048),
        )
        response = message_text(turn.message.content)
        streamed = turn.streamed
        details = [*details, *turn.details]
    if response:
        if writer is not None and not streamed:
            writer({"kind": "token", "content": response})
            streamed = response
        # Mirror the single-agent path: auto-continue a synthesis truncated by the
        # output limit, then only surface the cut-off notice if it is still
        # truncated after the continuation budget.
        try:
            response, appended, still_truncated, cont_details = await _auto_continue_answer(
                model,
                messages,
                _SYNTHESIZER_PROMPT,
                response,
                turn.finish_reason if turn is not None else None,
                config,
                writer,
                allow_reserve=True,
            )
        except BudgetExceeded as exc:
            if controller is not None:
                controller.mark_exhausted(str(exc))
            appended = ""
            still_truncated = True
            cont_details = ()
        streamed += appended
        details = [*details, *cont_details]
        response, output_limit = _append_output_limit_notice(response, "length" if still_truncated else None)
    else:
        response = _synthesis_fallback(plan, results)

    run_status = _terminal_status(plan, results, controller)
    budget_snapshot = controller.snapshot() if controller is not None else state.get("budget")
    run_errors = _terminal_errors(plan, results, controller, list(state.get("run_errors") or []))
    ai_message = finalize_assistant_message(
        response=response,
        streamed=streamed,
        writer=writer,
        details=details,
        output_limit=output_limit,
        extra_metadata={
            "seizu_run_status": run_status,
            **({"seizu_budget": budget_snapshot} if budget_snapshot else {}),
            **({"seizu_run_errors": run_errors} if run_errors else {}),
        },
    )
    # Clear transient orchestration state so completed runs don't bloat the
    # persisted thread/checkpoint.
    return {
        "messages": [*_trim_messages(state["messages"], ai_message), ai_message],
        "plan": [],
        "step_results": [],
        "iteration": 0,
        "run_errors": [],
        **_budget_state(config),
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


def _terminal_status(
    plan: list[dict[str, Any]],
    results: list[dict[str, Any]],
    controller: BudgetController | None,
) -> str:
    results_by_id = {result["step_id"]: result for result in results}
    if (controller is not None and controller.finalizing) or any(result.get("budget_exhausted") for result in results):
        return "budget_exhausted"
    if any(result.get("blocked") for result in results):
        return "blocked"
    for step in plan:
        if step.get("priority") == "optional":
            continue
        if step.get("status") != "passed":
            return "partial"
        result = results_by_id.get(step["id"], {})
        if result.get("execution_error") or result.get("budget_skipped"):
            return "partial"
    return "completed"


def _terminal_errors(
    plan: list[dict[str, Any]],
    results: list[dict[str, Any]],
    controller: BudgetController | None,
    existing: list[str],
) -> list[str]:
    errors = list(existing)
    if controller is not None and controller.finalizing:
        reason = controller.snapshot().get("exhaustion_reason")
        if isinstance(reason, str) and reason:
            errors.append(reason)
    results_by_id = {result["step_id"]: result for result in results}
    for step in plan:
        result = results_by_id.get(step["id"], {})
        if step.get("status") == "passed":
            continue
        reason = result.get("execution_error") or result.get("verify_reason")
        if not reason and result.get("blocked"):
            reason = f"Step was blocked: {result['blocked']}"
        if isinstance(reason, str) and reason:
            errors.append(f"{step['goal']}: {reason}")
    return list(dict.fromkeys(errors))[:20]


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
        step_id = str(step["id"])
        result = results_by_id.get(step["id"], {})
        if result.get("awaiting_confirmation"):
            step_status = "awaiting"
        elif result.get("blocked") or result.get("execution_error"):
            step_status = "blocked"
        else:
            step_status = "completed"
        details.append(
            {
                "kind": "step",
                "title": f"Step: {step['goal']}",
                "status": step_status,
                "step_id": step_id,
                "body": _truncate_text(str(result.get("execution_error") or result.get("output", "")), 6000),
            }
        )
        # Per-tool entries are reconstructed name-only (the step result keeps tool
        # names, not each call's args/output), enough to show what ran under the step.
        for tool_name in result.get("tools_used", []) or []:
            details.append({"kind": "tool", "title": f"Tool: {tool_name}", "status": "completed", "step_id": step_id})
        if "verified" in result:
            details.append(
                {
                    "kind": "verify",
                    "title": f"Verify: {step['goal']}",
                    "status": "completed" if result.get("verified") else "blocked",
                    "step_id": step_id,
                    "body": result.get("verify_reason", ""),
                }
            )
    return details


# --- Helpers -------------------------------------------------------------------


def _has_pending_plan(state: ChatState) -> bool:
    plan = state.get("plan") or []
    return any(step.get("status") in ("pending", "ran", "failed", "awaiting") for step in plan)
