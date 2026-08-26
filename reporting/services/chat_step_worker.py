"""Run one orchestrator plan step on a worker that holds none of the turn's state.

In-process, a plan step inherits nearly everything from the dispatcher that
started it: the conversation's sandbox, the run's budget ledger, the disclosed
tool set, the session ledger, the stream writer — all ambient, all context
variables. Distributed (AGT-018), none of that is there, and the difference is
the whole of this module: it rebuilds each of them from an explicit, versioned
payload, runs the *same* :func:`chat_orchestrator._run_worker_step`, and hands
back what the coordinator needs to fold the step into the plan.

Three of those rebuilds are security-relevant and none of them may be relaxed:

* **Identity is rebuilt here and intersected, never carried.** The payload names
  a ``user_id`` and the permission cap the turn was admitted under; the worker
  resolves the stored user and intersects (AGT-006, AGT-008). A resolved
  permission set travelling in a payload would be a permission set nobody
  re-checked.
* **The turn must still be running and still own its thread.** Otherwise a step
  scheduled before a cancellation would keep spending and keep writing into a log
  a reader has already been told is complete.
* **Confirmation gating and chat-safe filtering are untouched.** The step reaches
  tools through the same ``mcp_runtime`` path as an in-process one, so a
  mutating tool still fails closed (AGT-001).
"""

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.headless import resolve_stored_user
from reporting.authnz.permissions import Permission
from reporting.schema.model_profiles import ResolvedModelProfile
from reporting.services import (
    chat_budget,
    chat_models,
    chat_turns,
    episodic_memory,
    external_mcp,
    model_profiles,
    report_store,
    sandbox_session,
)
from reporting.services.chat_budget import BudgetController, grant_ledger
from reporting.temporal_workflows.shared import ChatWorkerStepInvocation, ChatWorkerStepOutcome

logger = logging.getLogger(__name__)

#: The payload shape this worker understands. A worker running older code must
#: refuse a newer payload rather than read fields it would misinterpret; adding
#: a field with a default does not change this number, changing what an existing
#: field means does.
SUPPORTED_INVOCATION_VERSION = 2


class StepInvocationRejected(RuntimeError):
    """The step must not run: stale payload, closed turn, or lost identity."""


class _DetailStream:
    """A synchronous stream writer that appends into the turn's event log.

    The orchestrator emits progress by calling a writer synchronously, and the
    log is written asynchronously, so the two are joined by a queue drained in
    the background. Batching and sequencing are the publisher's
    (:class:`chat_turns.ChatTurnPublisher`), which is what keeps a distributed
    step's frames byte-identical to an in-process one's and keeps ``seq`` dense
    across every producer of the turn.
    """

    def __init__(self, publisher: chat_turns.ChatTurnPublisher) -> None:
        self._publisher = publisher
        self._queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue()
        self._drain: asyncio.Task[None] | None = None

    def __call__(self, chunk: Any) -> None:
        # Rendered here, by the producer, exactly as the in-process path renders
        # it -- the log holds finished UI parts, never a shape a reader has to
        # interpret (AGT-008).
        parts = chat_turns.render_parts(chunk, "")
        if parts:
            self._queue.put_nowait(parts)

    async def __aenter__(self) -> "_DetailStream":
        self._drain = asyncio.create_task(self._drain_loop())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._drain is not None:
            self._drain.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain
        # Whatever is still queued belongs to work that has already happened, so
        # it is published rather than dropped: a step's last detail is usually
        # its result, which is the one a viewer is waiting for.
        while not self._queue.empty():
            await self._publisher.publish(self._queue.get_nowait())

    async def _drain_loop(self) -> None:
        while True:
            await self._publisher.publish(await self._queue.get())


async def _resolve_identity(invocation: ChatWorkerStepInvocation) -> CurrentUser:
    stored = await resolve_stored_user(invocation.user_id)
    effective = frozenset(stored.permissions) & frozenset(invocation.permission_cap)
    if invocation.bypass_confirmations and Permission.CHAT_BYPASS_PERMISSIONS.value not in effective:
        raise StepInvocationRejected("Missing permissions to bypass confirmations")
    return CurrentUser(user=stored.user, jwt_claims=stored.jwt_claims, permissions=effective)


async def _check_turn(invocation: ChatWorkerStepInvocation) -> None:
    turn = await report_store.get_chat_turn(invocation.turn_id)
    if turn is None:
        raise StepInvocationRejected("Chat turn no longer exists")
    if turn.status != "running" or turn.cancel_requested:
        raise StepInvocationRejected("Chat turn is no longer running")
    active = await report_store.get_active_chat_turn(turn.user_id, turn.thread_id)
    if active is None or active.turn_id != invocation.turn_id:
        # The turn's claim on the thread lapsed and a successor took it. Two
        # producers on one conversation is the thing every ownership check in
        # this system exists to prevent.
        raise StepInvocationRejected("Chat turn no longer owns its thread")


async def run_distributed_step(invocation: ChatWorkerStepInvocation) -> ChatWorkerStepOutcome:
    """Run one plan step and report its result, spend, and session memory."""
    if invocation.version != SUPPORTED_INVOCATION_VERSION:
        raise StepInvocationRejected(
            f"chat worker step payload version {invocation.version} is not supported by this worker"
        )
    await _check_turn(invocation)
    current = await _resolve_identity(invocation)

    # Imported here, not at module scope: chat_orchestrator pulls in the chat
    # graph and every model provider behind it, and this module is imported by
    # the activity table that the workflow sandbox also touches.
    from reporting.services import chat_graph, chat_orchestrator

    step = json.loads(invocation.step_json or "{}")
    plan = json.loads(invocation.plan_json or "[]")
    dependency_results = json.loads(invocation.dependency_results_json or "[]")
    session_memory = json.loads(invocation.session_memory_json) if invocation.session_memory_json else {}

    controller = BudgetController(
        grant_ledger(
            token_grant=invocation.token_grant,
            soft_token_grant=invocation.soft_token_grant,
            cost_grant_usd=invocation.cost_grant_usd,
            soft_cost_grant_usd=invocation.soft_cost_grant_usd,
            llm_call_grant=invocation.llm_call_grant,
        )
    )
    chat_budget.set_current_budget_controller(controller)
    config = chat_graph.build_turn_config(
        current,
        invocation.thread_id,
        budget_controller=controller,
        bypass_confirmations=invocation.bypass_confirmations,
        turn_id=invocation.turn_id,
    )

    ledger = episodic_memory.start_session_ledger(
        session_memory or None,
        # The step is part of the turn that scheduled it, not a turn of its own.
        # Without an explicit number ``from_state`` assumes it is being read a
        # turn later than it was written, which would relabel this very turn's
        # episodes as "established earlier in this conversation".
        turn=max(1, int(session_memory.get("turn") or 1)),
    )
    # A distributed step runs in its own activity, so it discovers the external
    # inventory for itself; scope it so the step pays for that once (AGT-038).
    external_mcp.begin_discovery_scope()
    if invocation.sandbox_id:
        # Attach, never open: the coordinating turn owns the conversation's
        # sandbox and is the only thing that may suspend it (SBX-015).
        sandbox_session.attach_sandbox_session(invocation.sandbox_id, thread=invocation.sandbox_thread)

    captured_profile = ResolvedModelProfile.model_validate(invocation.resolved_model_profile)
    spec = chat_models.ModelSpec.from_payload(
        captured_profile.spec_for("worker", economy=invocation.economy).model_dump(mode="json")
    )
    model = chat_graph.build_chat_model(spec)
    summary_spec = chat_models.ModelSpec.from_payload(
        captured_profile.spec_for("worker_summary", economy=invocation.economy).model_dump(mode="json")
    )
    summary_model = chat_graph.build_chat_model(summary_spec)
    with model_profiles.use(captured_profile):
        tool_specs, skill_tools, skill_prompts = await chat_orchestrator._worker_tool_specs(current)

    # Watching for a stop is not optional here, and it is not the coordinator's
    # cancel to deliver. The coordinator asks the fan-out workflow to cancel, but
    # that is best-effort across a process boundary and it may itself be in the
    # middle of being cancelled when it tries. A step that kept running after its
    # turn was stopped would go on spending on an answer nobody will read -- and
    # go on writing into a log a reader has been told is complete. Same reasoning
    # as the turn's own watch in AGT-008.
    publisher = chat_turns.ChatTurnPublisher(invocation.turn_id)
    result: dict[str, Any] = {}
    try:
        async with publisher, _DetailStream(publisher) as writer:

            async def _run_step() -> dict[str, Any]:
                with model_profiles.use(captured_profile):
                    return await chat_orchestrator._run_worker_step_with_session(
                        step,
                        plan=plan,
                        results=dependency_results,
                        conversation_context=invocation.conversation_context,
                        model=model,
                        current_user=current,
                        session_key=invocation.thread_id,
                        config=config,
                        tool_specs=tool_specs,
                        disclosed_names=set(invocation.disclosed_tools),
                        progressive=invocation.progressive,
                        writer=writer,
                        skill_tools=skill_tools,
                        skill_prompts=skill_prompts,
                        # The grant is the step's whole allowance: it cannot ask the
                        # run's controller for more from another process (AGT-018).
                        thresholds=chat_orchestrator._StepThresholds(
                            soft_tokens=invocation.soft_token_grant or invocation.token_grant,
                            ceiling_tokens=invocation.token_grant,
                            soft_cost_usd=invocation.soft_cost_grant_usd or invocation.cost_grant_usd,
                            ceiling_cost_usd=invocation.cost_grant_usd,
                        ),
                        summary_model=summary_model,
                    )

            work = asyncio.create_task(_run_step())
            stopped = asyncio.create_task(publisher.stopped.wait())
            done, _ = await asyncio.wait({work, stopped}, return_when=asyncio.FIRST_COMPLETED)
            stopped.cancel()
            if work in done:
                result = work.result()
            else:
                # Cancelling interrupts the step wherever it is, including
                # mid-model-call, so a queued tool action does not get to run.
                work.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await work
                raise StepInvocationRejected("Chat turn was stopped while this step was running")
    finally:
        if invocation.sandbox_id:
            # Detaches; the sandbox stays running for its owner.
            await sandbox_session.close_sandbox_session()
        chat_budget.set_current_budget_controller(None)
        episodic_memory.clear_session_ledger()
        episodic_memory.clear_episode_log()

    memory = ledger.to_state()
    result_json, result_ref = await _carry_result(invocation.turn_id, result)
    return ChatWorkerStepOutcome(
        step_id=str(step.get("id") or invocation.step_id),
        result_json=result_json,
        result_ref=result_ref,
        usage=controller.usage_report(),
        episodes=list(memory.get("episodes") or []),
        receipts=list(memory.get("receipts") or []),
    )


async def _carry_result(turn_id: str, result: dict[str, Any]) -> tuple[str, str]:
    """Return the result inline, or spill it and return a reference.

    A step's result carries every call it made and what each returned, bounded
    per call by ``CHAT_TOOL_RESULT_MAX_BYTES`` -- so a step that made thirty
    calls can be tens of megabytes, and Temporal would copy it into workflow
    history on the way out of the activity and again on the way into the
    coordinator. Truncating instead is not an option: the trace is the evidence
    the synthesizer answers from and the retry resumes from (AGT-013, AGT-014).
    """
    body = json.dumps(result, default=str)
    if len(body.encode("utf-8")) <= max(0, settings.CHAT_ORCHESTRATOR_DISTRIBUTED_INLINE_MAX_BYTES):
        return body, ""
    payload_id = f"step_{uuid.uuid4().hex}"
    await report_store.put_chat_turn_payload(turn_id, payload_id, body)
    return "", payload_id


async def read_step_result(turn_id: str, outcome: ChatWorkerStepOutcome) -> dict[str, Any] | None:
    """Resolve an outcome back to the step result the dispatcher merges.

    ``None`` when the step could not run at all, or when its spilled payload is
    gone -- both cases the caller turns into a recorded step failure rather than
    a silently empty result.
    """
    if outcome.error:
        return None
    if outcome.result_json:
        parsed = json.loads(outcome.result_json)
        return parsed if isinstance(parsed, dict) else None
    if not outcome.result_ref:
        return None
    body = await report_store.get_chat_turn_payload(turn_id, outcome.result_ref)
    if body is None:
        logger.warning(
            "A distributed chat step's result payload was missing",
            extra={"turn_id": turn_id, "payload_id": outcome.result_ref},
        )
        return None
    parsed = json.loads(body)
    return parsed if isinstance(parsed, dict) else None
