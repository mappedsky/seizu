"""Distributing an orchestrated turn's plan steps across Temporal (AGT-018).

The rules being pinned here are the ones that cost money or correctness if they
drift: a batch is never both scheduled and re-run locally, grants cannot
collectively overspend, a worker rebuilds identity rather than trusting a
payload, and a step that never produced a result is recorded rather than
silently dropped from the plan the synthesizer answers from.
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.chat import ChatTurnCommand, ChatTurnItem
from reporting.schema.report_config import User
from reporting.services import chat_orchestrator, chat_step_worker, episodic_memory
from reporting.services.chat_budget import BudgetController, grant_ledger, initial_budget_ledger
from reporting.temporal_workflows.shared import ChatStepFanoutResult, ChatWorkerStepOutcome

_NOW = "2024-01-01T00:00:00+00:00"


def _user(permissions: set[str] | None = None) -> CurrentUser:
    return CurrentUser(
        user=User(user_id="user-1", sub="sub", iss="iss", created_at=_NOW, last_login=_NOW),
        jwt_claims={},
        permissions=frozenset(permissions or {Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}),
    )


def _step(step_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "goal": f"goal {step_id}",
        "depends_on": [],
        "status": "ran",
        "estimated_tokens": 8_000,
        **extra,
    }


def _config(turn_id: str = "turn-1") -> dict[str, Any]:
    return {
        "configurable": {
            "current_user": _user(),
            "thread_id": "user:user-1:thread:1001",
            "client_thread_id": "1001",
            "turn_id": turn_id,
        }
    }


def _turn(status: str = "running") -> ChatTurnItem:
    return ChatTurnItem(
        turn_id="turn-1",
        user_id="user-1",
        thread_id="1001",
        message_id="msg_1",
        text_id="text_1",
        idempotency_key="ik_00000001",
        command=ChatTurnCommand(message="hi", permission_cap=[Permission.CHAT_TOOLS_CALL.value], timeout_seconds=900),
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at="2099-01-01T00:00:00+00:00",
    )


# --- eligibility ---------------------------------------------------------------


def test_a_headless_run_is_never_distributed():
    """A headless run has no admitted turn, so no event log for a step to report
    into and no turn id to derive the fan-out from -- and it is already an
    activity of its own."""
    batch = [_step("s1"), _step("s2")]

    assert chat_orchestrator._distribution_eligible(batch, _config(turn_id="")) is False


def test_a_single_step_batch_stays_in_process():
    """Scheduling one step elsewhere buys nothing and costs a serialization
    boundary."""
    assert chat_orchestrator._distribution_eligible([_step("s1")], _config()) is False


def test_a_multi_step_interactive_batch_is_distributed():
    assert chat_orchestrator._distribution_eligible([_step("s1"), _step("s2")], _config()) is True


def test_the_setting_switches_distribution_off(mocker):
    mocker.patch.object(settings, "CHAT_ORCHESTRATOR_DISTRIBUTED_ENABLED", False)

    assert chat_orchestrator._distribution_eligible([_step("s1"), _step("s2")], _config()) is False


# --- grants --------------------------------------------------------------------


def test_grants_never_collectively_exceed_what_the_run_has_left():
    """The whole reason concurrent workers cannot overspend without a
    distributed transaction: the arithmetic happened before any of them started."""
    controller = BudgetController({**initial_budget_ledger(), "token_limit": 120_000, "reserve_tokens": 20_000})
    plan = [_step("s1"), _step("s2"), _step("s3")]
    remaining = controller.remaining_normal_tokens
    assert remaining is not None

    grants = [chat_orchestrator._grant_for(step, plan, controller, len(plan)) for step in plan]

    assert sum(grant.tokens for grant in grants) <= remaining


def test_a_grant_holds_back_a_reserve_for_the_step_to_report_from():
    """A step cut at its wall with nothing to say is the failure AGT-012 exists
    for, so the soft threshold sits below the hard one."""
    controller = BudgetController({**initial_budget_ledger(), "token_limit": 120_000, "reserve_tokens": 20_000})
    plan = [_step("s1"), _step("s2")]

    grant = chat_orchestrator._grant_for(plan[0], plan, controller, len(plan))

    assert 0 < grant.soft_tokens < grant.tokens


def test_a_grant_ledger_refuses_normal_work_past_its_soft_share():
    """The grant reproduces the run's two-threshold shape inside one slice: work
    stops at the soft line, the summary pass may use the rest."""
    controller = BudgetController(
        grant_ledger(token_grant=10_000, soft_token_grant=7_000, cost_grant_usd=0.0, llm_call_grant=0)
    )

    assert controller.remaining_normal_tokens == 7_000
    assert controller.snapshot()["token_limit"] == 10_000


async def test_absorbing_a_step_ledger_folds_its_spend_into_the_run():
    """The grant was the reservation; this is the commit, so the run's view stays
    complete even though the spending happened in another process."""
    run = BudgetController(initial_budget_ledger())
    run.open_scope("worker:s1", 50_000, 40_000)
    step = BudgetController(
        grant_ledger(token_grant=50_000, soft_token_grant=40_000, cost_grant_usd=0.0, llm_call_grant=0)
    )
    step._ledger.update(
        {"input_tokens": 900, "output_tokens": 100, "total_tokens": 1_000, "cost_usd": 0.25, "llm_calls": 3}
    )

    await run.absorb(step.usage_report(), scope="worker:s1")

    assert run.snapshot()["total_tokens"] == 1_000
    assert run.snapshot()["llm_calls"] == 3
    assert run.snapshot()["cost_usd"] == pytest.approx(0.25)
    assert run.scope_spend("worker:s1") == 1_000


def test_cost_and_call_budgets_are_granted_too():
    """A batch granted only tokens could exhaust the cost or call budget before
    any of it was absorbed back -- the run would find out after the money was
    spent rather than instead of spending it."""
    controller = BudgetController(
        {
            **initial_budget_ledger(),
            "token_limit": 120_000,
            "reserve_tokens": 20_000,
            "cost_limit_usd": 8.0,
            "cost_usd": 2.0,
            "max_llm_calls": 40,
            "llm_calls": 10,
        }
    )
    plan = [_step("s1"), _step("s2")]

    grants = [chat_orchestrator._grant_for(step, plan, controller, len(plan)) for step in plan]

    assert sum(grant.cost_usd for grant in grants) == pytest.approx(6.0)
    assert sum(grant.llm_calls for grant in grants) == 30


def test_an_unlimited_dimension_is_granted_as_unlimited():
    """Zero means "no limit configured", and must travel as zero rather than as
    a share of zero -- which would refuse the step's first call."""
    controller = BudgetController({**initial_budget_ledger(), "cost_limit_usd": 0.0, "max_llm_calls": 0})
    plan = [_step("s1"), _step("s2")]

    grant = chat_orchestrator._grant_for(plan[0], plan, controller, len(plan))

    assert (grant.cost_usd, grant.llm_calls) == (0.0, 0)


# --- dispatch ------------------------------------------------------------------


def _fanout_client(mocker, outcomes: list[ChatWorkerStepOutcome]) -> MagicMock:
    handle = MagicMock()
    handle.result = AsyncMock(return_value=ChatStepFanoutResult(outcomes=outcomes))
    handle.cancel = AsyncMock()
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=handle)
    mocker.patch("reporting.services.schedule_reconciler.get_client", AsyncMock(return_value=client))
    return client


async def test_a_distributed_batch_returns_its_steps_results(mocker):
    batch = [_step("s1"), _step("s2")]
    _fanout_client(
        mocker,
        [
            ChatWorkerStepOutcome(step_id="s1", result_json=json.dumps({"step_id": "s1", "output": "one"})),
            ChatWorkerStepOutcome(step_id="s2", result_json=json.dumps({"step_id": "s2", "output": "two"})),
        ],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    results = await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=batch,
        results=[],
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    assert [(r["step_id"], r["output"]) for r in results] == [("s1", "one"), ("s2", "two")]


async def test_the_fanout_result_is_asked_for_as_its_own_type(mocker):
    """Started by *name*, Temporal has no way to know what the result is and
    hands back the decoded JSON -- a plain dict, on which reading `.outcomes`
    raises. Only an end-to-end run catches this, so it is pinned here."""
    batch = [_step("s1"), _step("s2")]
    client = _fanout_client(
        mocker,
        [ChatWorkerStepOutcome(step_id="s1", result_json="{}"), ChatWorkerStepOutcome(step_id="s2", result_json="{}")],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=batch,
        results=[],
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    assert client.start_workflow.await_args.kwargs["result_type"] is ChatStepFanoutResult


async def test_a_step_that_produced_no_result_is_recorded_not_dropped(mocker):
    """A missing step would otherwise vanish from the plan the synthesizer
    answers from, and an absent finding reads as a negative one."""
    batch = [_step("s1")]
    _fanout_client(mocker, [ChatWorkerStepOutcome(step_id="s1", error="ActivityError: worker died")])
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    results = await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=batch,
        results=[],
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    assert results[0]["step_id"] == "s1"
    assert "worker died" in results[0]["execution_error"]


async def test_an_unreachable_temporal_falls_back_before_anything_is_scheduled(mocker):
    """Only safe because nothing ran: past the start call a local rerun would be
    a second paid execution of work already under way."""
    mocker.patch(
        "reporting.services.schedule_reconciler.get_client",
        AsyncMock(side_effect=RuntimeError("no temporal")),
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))
    batch = [_step("s1"), _step("s2")]

    with pytest.raises(chat_orchestrator._FanoutUnavailable):
        await chat_orchestrator._dispatch_batch_distributed(
            batch,
            plan=batch,
            results=[],
            conversation_context="",
            current_user=_user(),
            config=_config(),
            iteration=0,
            disclosed_names=set(),
            progressive=True,
            controller=None,
        )


async def test_a_failure_after_the_fanout_started_cancels_it_and_propagates(mocker):
    """It must not fall back: the steps are running somewhere, and re-running
    them locally would pay for the same work twice. The cancel is detached, so
    it survives the cancellation that usually causes it."""
    handle = MagicMock()
    handle.result = AsyncMock(side_effect=RuntimeError("lost the workflow"))
    handle.cancel = AsyncMock()
    client = MagicMock()
    client.start_workflow = AsyncMock(return_value=handle)
    mocker.patch("reporting.services.schedule_reconciler.get_client", AsyncMock(return_value=client))
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))
    batch = [_step("s1"), _step("s2")]

    with pytest.raises(RuntimeError, match="lost the workflow"):
        await chat_orchestrator._dispatch_batch_distributed(
            batch,
            plan=batch,
            results=[],
            conversation_context="",
            current_user=_user(),
            config=_config(),
            iteration=0,
            disclosed_names=set(),
            progressive=True,
            controller=None,
        )

    await asyncio.sleep(0)
    handle.cancel.assert_awaited_once()


async def test_the_batch_workflow_id_is_derived_from_the_batch(mocker):
    """Naming it again resolves to the batch already running instead of paying
    for a second copy of it."""
    batch = [_step("s2"), _step("s1")]
    client = _fanout_client(
        mocker,
        [ChatWorkerStepOutcome(step_id="s2", result_json="{}"), ChatWorkerStepOutcome(step_id="s1", result_json="{}")],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    for _ in range(2):
        await chat_orchestrator._dispatch_batch_distributed(
            batch,
            plan=batch,
            results=[],
            conversation_context="",
            current_user=_user(),
            config=_config(),
            iteration=1,
            disclosed_names=set(),
            progressive=True,
            controller=None,
        )

    ids = {call.kwargs["id"] for call in client.start_workflow.await_args_list}
    assert len(ids) == 1
    assert next(iter(ids)).startswith("seizu-chat-fanout:turn-1:1-")


async def test_only_a_permission_cap_travels_to_the_worker(mocker):
    """A resolved permission set in a payload would be one nobody re-checked;
    the worker resolves identity itself and intersects (AGT-006)."""
    batch = [_step("s1"), _step("s2")]
    client = _fanout_client(
        mocker,
        [ChatWorkerStepOutcome(step_id="s1", result_json="{}"), ChatWorkerStepOutcome(step_id="s2", result_json="{}")],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=batch,
        results=[],
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    invocation = client.start_workflow.await_args.args[1].steps[0]
    assert invocation.permission_cap == sorted(_user().permissions)
    assert invocation.user_id == "user-1"
    assert not hasattr(invocation, "permissions")


async def test_only_a_steps_own_dependencies_travel_with_it(mocker):
    """Every step of every batch would otherwise carry a copy of every other
    step's output through Temporal history."""
    batch = [_step("s3", depends_on=["s1"]), _step("s4", depends_on=[])]
    client = _fanout_client(
        mocker,
        [ChatWorkerStepOutcome(step_id="s3", result_json="{}"), ChatWorkerStepOutcome(step_id="s4", result_json="{}")],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))
    prior = [{"step_id": "s1", "output": "needed"}, {"step_id": "s2", "output": "irrelevant"}]

    await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=[*batch, _step("s1"), _step("s2")],
        results=prior,
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    steps = client.start_workflow.await_args.args[1].steps
    assert json.loads(steps[0].dependency_results_json) == [{"step_id": "s1", "output": "needed"}]
    assert json.loads(steps[1].dependency_results_json) == []


# --- the worker side -----------------------------------------------------------


async def test_a_worker_refuses_a_payload_newer_than_it_understands(mocker):
    """Misreading a field is worse than not running the step."""
    from reporting.temporal_workflows.shared import ChatWorkerStepInvocation

    with pytest.raises(chat_step_worker.StepInvocationRejected, match="newer"):
        await chat_step_worker.run_distributed_step(
            ChatWorkerStepInvocation(version=chat_step_worker.SUPPORTED_INVOCATION_VERSION + 1)
        )


async def test_a_worker_refuses_a_turn_that_is_no_longer_running(mocker):
    """A step scheduled just before a cancellation must not keep spending, nor
    write into a log a reader has been told is complete."""
    from reporting.temporal_workflows.shared import ChatWorkerStepInvocation

    mocker.patch.object(chat_step_worker.report_store, "get_chat_turn", AsyncMock(return_value=_turn("canceled")))

    with pytest.raises(chat_step_worker.StepInvocationRejected, match="no longer running"):
        await chat_step_worker.run_distributed_step(ChatWorkerStepInvocation(turn_id="turn-1"))


async def test_a_worker_refuses_a_turn_that_lost_its_thread(mocker):
    """Two producers on one conversation is what every ownership check exists to
    prevent."""
    from reporting.temporal_workflows.shared import ChatWorkerStepInvocation

    mocker.patch.object(chat_step_worker.report_store, "get_chat_turn", AsyncMock(return_value=_turn()))
    successor = _turn()
    successor.turn_id = "turn-2"
    mocker.patch.object(chat_step_worker.report_store, "get_active_chat_turn", AsyncMock(return_value=successor))

    with pytest.raises(chat_step_worker.StepInvocationRejected, match="no longer owns its thread"):
        await chat_step_worker.run_distributed_step(ChatWorkerStepInvocation(turn_id="turn-1"))


async def test_a_small_result_travels_inline(mocker):
    put = mocker.patch.object(chat_step_worker.report_store, "put_chat_turn_payload", AsyncMock())

    result_json, result_ref = await chat_step_worker._carry_result("turn-1", {"output": "small"})

    assert json.loads(result_json) == {"output": "small"}
    assert result_ref == ""
    put.assert_not_awaited()


async def test_an_oversized_result_is_spilled_and_passed_by_reference(mocker):
    """Truncating is not available: the trace is the evidence the synthesizer
    answers from and the retry resumes from."""
    mocker.patch.object(settings, "CHAT_ORCHESTRATOR_DISTRIBUTED_INLINE_MAX_BYTES", 64)
    put = mocker.patch.object(chat_step_worker.report_store, "put_chat_turn_payload", AsyncMock())
    result = {"output": "x" * 500}

    result_json, result_ref = await chat_step_worker._carry_result("turn-1", result)

    assert result_json == ""
    assert result_ref.startswith("step_")
    assert json.loads(put.await_args.args[2]) == result

    mocker.patch.object(
        chat_step_worker.report_store, "get_chat_turn_payload", AsyncMock(return_value=put.await_args.args[2])
    )
    read = await chat_step_worker.read_step_result("turn-1", ChatWorkerStepOutcome(result_ref=result_ref))
    assert read == result


async def test_a_vanished_payload_reads_as_no_result(mocker):
    mocker.patch.object(chat_step_worker.report_store, "get_chat_turn_payload", AsyncMock(return_value=None))

    assert await chat_step_worker.read_step_result("turn-1", ChatWorkerStepOutcome(result_ref="step_gone")) is None


# --- session memory ------------------------------------------------------------


def test_merging_a_worker_ledger_twice_adds_nothing_the_second_time():
    """Two ledgers shed entries independently once they are at their bounds, so
    merging by content is what keeps a step's work from being re-appended."""
    ledger = episodic_memory.SessionLedger(turn=1)
    ledger.append_episode("already known", "done")
    returned = {
        "episodes": [{"task": "already known", "outcome": "done"}, {"task": "new work", "outcome": "found it"}],
        "receipts": [
            {"path": "/home/user/seizu_results/a.json", "source": "step:s1", "purpose": "rows", "sandbox_id": "sbx"}
        ],
    }

    ledger.merge_state(returned)
    ledger.merge_state(returned)

    assert [episode.task for episode in ledger.episodes] == ["already known", "new work"]
    assert [receipt.path for receipt in ledger.receipts] == ["/home/user/seizu_results/a.json"]


# --- the fan-out workflow ------------------------------------------------------


def test_a_step_failure_names_its_cause_not_the_wrapper():
    """An activity failure arrives as "ActivityError: Activity task failed",
    which says only that something went wrong. The dispatcher records this
    string as the step's execution error and the verifier reads it, so it has to
    name the reason."""
    from reporting.temporal_workflows.chat_step_fanout import _reason

    root = chat_step_worker.StepInvocationRejected("Chat turn no longer exists")
    wrapped = RuntimeError("Activity task failed")
    wrapped.__cause__ = root

    assert _reason(wrapped) == "StepInvocationRejected: Chat turn no longer exists"


def test_a_cause_chain_that_loops_still_terminates():
    """Workflow code cannot hang: a deadlock detector fails the task at two
    seconds, and a self-referential cause would spin forever."""
    from reporting.temporal_workflows.chat_step_fanout import _reason

    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert _reason(first).startswith("RuntimeError:")


def test_the_fanout_discriminates_on_the_exception_not_the_result_type():
    """Inside the workflow sandbox the imported ChatWorkerStepOutcome is not the
    same class object an activity result deserializes into, so an isinstance
    check against it is False for a perfectly good outcome -- which sent every
    *successful* step down the failure branch and killed the whole turn. Only an
    end-to-end run with a succeeding step found it, because a smoke test with
    failing steps exercises the branch that was already correct.
    """
    import ast
    import pathlib

    source = pathlib.Path("reporting/temporal_workflows/chat_step_fanout.py").read_text()
    run = next(
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.AsyncFunctionDef) and node.name == "run"
    )
    checks = [
        node.args[1].id
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "isinstance"
        and isinstance(node.args[1], ast.Name)
    ]
    assert checks, "the fan-out must discriminate results from failures"
    assert "ChatWorkerStepOutcome" not in checks
    assert "BaseException" in checks


async def test_a_distributed_step_carries_its_summary_model_too(mocker):
    """A summary pass is a different job with a possibly different reasoning
    budget, but it must still run on the model the *turn* was admitted with --
    re-resolving it worker-side would read that worker's settings instead.

    The model is pinned rather than read from the environment: a deployment with
    none configured resolves an empty id, and the assertion would then pass or
    fail on whether a `.env` happened to be present.
    """
    mocker.patch.object(settings, "CHAT_LLM_MODEL", "some/model")
    batch = [_step("s1"), _step("s2")]
    client = _fanout_client(
        mocker,
        [ChatWorkerStepOutcome(step_id="s1", result_json="{}"), ChatWorkerStepOutcome(step_id="s2", result_json="{}")],
    )
    mocker.patch.object(chat_orchestrator, "_shared_sandbox_id", AsyncMock(return_value=""))

    await chat_orchestrator._dispatch_batch_distributed(
        batch,
        plan=batch,
        results=[],
        conversation_context="",
        current_user=_user(),
        config=_config(),
        iteration=0,
        disclosed_names=set(),
        progressive=True,
        controller=None,
    )

    invocation = client.start_workflow.await_args.args[1].steps[0]
    assert invocation.model_spec.get("model_id")
    assert invocation.summary_model_spec.get("model_id")
    assert invocation.summary_model_spec["role"] == "worker_summary"
