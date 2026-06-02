from typing import Any
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessageChunk, HumanMessage

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import User
from reporting.services import chat_graph, chat_orchestrator
from reporting.services.chat_graph import _ConfirmResolution
from reporting.services.chat_orchestrator import _Plan, _PlannedStep, _RouteDecision, _Verdict

_NOW = "2024-01-01T00:00:00+00:00"


def _user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.TOOLS_CALL.value,
                Permission.CHAT_SKILLS_CALL.value,
                Permission.SKILLS_RENDER.value,
            }
        ),
    )


def _step(step_id: str, status: str = "pending", depends_on: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "goal": f"goal {step_id}",
        "depends_on": depends_on or [],
        "suggested_tools": [],
        "success_criteria": "",
        "status": status,
        **extra,
    }


class _Structured:
    def __init__(self, result: Any) -> None:
        self.result = result

    async def ainvoke(self, _messages: Any, config: Any = None) -> Any:
        return self.result


class _OrchestratorFakeModel:
    """Fake LangChain model: scripted structured outputs + scripted astream text."""

    def __init__(
        self,
        *,
        route: str = "orchestrate",
        plan_steps: list[_PlannedStep] | None = None,
        verdict_passed: bool = True,
        stream_text: str = "final answer",
    ) -> None:
        self.route = route
        self.plan_steps = plan_steps
        self.verdict_passed = verdict_passed
        self.stream_text = stream_text
        self.astream_calls = 0

    def with_structured_output(self, schema: type) -> _Structured:
        if schema is _RouteDecision:
            return _Structured(_RouteDecision(route=self.route, reason="because"))
        if schema is _Plan:
            steps = self.plan_steps or [_PlannedStep(id="s1", goal="do it", success_criteria="done")]
            return _Structured(_Plan(steps=steps))
        if schema is _Verdict:
            return _Structured(_Verdict(passed=self.verdict_passed, reason="verdict"))
        raise AssertionError(f"unexpected schema {schema!r}")

    def bind_tools(self, _tools: Any) -> "_OrchestratorFakeModel":
        return self

    async def astream(self, _input: Any, config: Any = None, **_kwargs: Any):
        self.astream_calls += 1
        yield AIMessageChunk(content=self.stream_text)


def _patch_common(mocker: Any, model: _OrchestratorFakeModel) -> None:
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.services.chat_orchestrator.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])


async def _run_graph(model: _OrchestratorFakeModel, thread_id: str, text: str = "do a multi step task") -> list[dict]:
    from langgraph.checkpoint.memory import MemorySaver

    graph = chat_graph.build_chat_graph(MemorySaver())
    return [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content=text)]},
            {"configurable": {"thread_id": thread_id, "client_thread_id": thread_id, "current_user": _user()}},
            stream_mode="custom",
        )
    ]


# --- Pure routing / helper logic ----------------------------------------------


def test_route_from_router_maps_route_field():
    assert chat_orchestrator.route_from_router({"route": "orchestrate", "messages": []}) == "planner"
    assert chat_orchestrator.route_from_router({"route": "simple", "messages": []}) == "chat_agent"
    assert chat_orchestrator.route_from_router({"messages": []}) == "chat_agent"


def test_route_from_dispatcher_goes_to_verifier_only_when_steps_ran():
    assert chat_orchestrator.route_from_dispatcher({"plan": [_step("s1", "ran")], "messages": []}) == "verifier"
    assert chat_orchestrator.route_from_dispatcher({"plan": [_step("s1", "passed")], "messages": []}) == "synthesizer"
    assert chat_orchestrator.route_from_dispatcher({"plan": [], "messages": []}) == "synthesizer"


def test_route_from_verifier_bounds_retries(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_MAX_ITERATIONS", 3)
    failed = {"plan": [_step("s1", "failed")], "messages": []}
    assert chat_orchestrator.route_from_verifier({**failed, "iteration": 0}) == "dispatcher"
    assert chat_orchestrator.route_from_verifier({**failed, "iteration": 3}) == "synthesizer"
    assert chat_orchestrator.route_from_verifier({"plan": [_step("s1", "passed")], "iteration": 0}) == "synthesizer"
    assert chat_orchestrator.route_from_verifier({"plan": [_step("s1", "pending")], "iteration": 0}) == "dispatcher"


def test_runnable_steps_respects_dependencies():
    plan = [_step("s1", "passed"), _step("s2", "pending", depends_on=["s1"]), _step("s3", "pending", depends_on=["s2"])]
    runnable = [step["id"] for step in chat_orchestrator._runnable_steps(plan)]
    assert runnable == ["s2"]  # s3 blocked on s2 which has not passed


def test_init_plan_drops_dangling_and_self_dependencies():
    plan = chat_orchestrator._init_plan(
        [
            _PlannedStep(id="s1", goal="a", depends_on=["s1", "ghost"]),
            _PlannedStep(id="s2", goal="b", depends_on=["s1"]),
        ]
    )
    assert plan[0]["depends_on"] == []
    assert plan[1]["depends_on"] == ["s1"]
    assert all(step["status"] == "pending" for step in plan)


def test_merge_results_replaces_by_step_id():
    merged = chat_orchestrator._merge_results(
        [{"step_id": "s1", "output": "old"}],
        [{"step_id": "s1", "output": "new"}, {"step_id": "s2", "output": "x"}],
    )
    by_id = {result["step_id"]: result["output"] for result in merged}
    assert by_id == {"s1": "new", "s2": "x"}


def test_has_pending_plan():
    assert chat_orchestrator._has_pending_plan({"plan": [_step("s1", "pending")], "messages": []})
    assert chat_orchestrator._has_pending_plan({"plan": [_step("s1", "ran")], "messages": []})
    assert not chat_orchestrator._has_pending_plan({"plan": [_step("s1", "passed")], "messages": []})
    assert not chat_orchestrator._has_pending_plan({"messages": []})


# --- Router short-circuits (no LLM call) --------------------------------------


async def test_router_short_circuits_when_disabled(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", False)
    result = await chat_orchestrator.router_node({"messages": [HumanMessage(content="hi")]}, {"configurable": {}})
    assert result == {"route": "simple"}


async def test_router_short_circuits_for_mock_provider(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "mock")
    result = await chat_orchestrator.router_node({"messages": [HumanMessage(content="hi")]}, {"configurable": {}})
    assert result == {"route": "simple"}


async def test_router_resumes_in_flight_plan(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    state = {"messages": [HumanMessage(content="continue")], "plan": [_step("s2", "pending")]}
    result = await chat_orchestrator.router_node(state, {"configurable": {}})
    assert result == {"route": "orchestrate"}


# --- Full graph integration ---------------------------------------------------


async def test_orchestrated_turn_plans_dispatches_verifies_and_synthesizes(mocker):
    model = _OrchestratorFakeModel(
        plan_steps=[
            _PlannedStep(id="s1", goal="gather", success_criteria="has data"),
            _PlannedStep(id="s2", goal="summarize", depends_on=["s1"], success_criteria="summary"),
        ],
        stream_text="final synthesized answer",
    )
    _patch_common(mocker, model)

    chunks = await _run_graph(model, "thread-orch-happy")

    details = [chunk["data"] for chunk in chunks if chunk["kind"] == "detail"]
    detail_kinds = [data["kind"] for data in details]
    assert "routing" in detail_kinds
    assert "plan" in detail_kinds
    # Each step emits a running + completed detail; two steps -> two completed.
    completed_steps = [d for d in details if d["kind"] == "step" and d["status"] == "completed"]
    assert len(completed_steps) == 2
    assert detail_kinds.count("verify") == 2

    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "final synthesized answer" in streamed


async def test_orchestrated_turn_persists_trace_and_clears_state(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    model = _OrchestratorFakeModel(stream_text="answer")
    _patch_common(mocker, model)
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "thread-orch-trace", "client_thread_id": "c", "current_user": _user()}}

    async for _ in graph.astream(
        {"messages": [HumanMessage(content="multi step please")]}, config, stream_mode="custom"
    ):
        pass

    state = await graph.aget_state({"configurable": {"thread_id": "thread-orch-trace"}})
    last = state.values["messages"][-1]
    details = last.response_metadata["seizu_details"]
    assert any(detail["kind"] == "plan" for detail in details)
    # Transient orchestration state is cleared after synthesis.
    assert state.values.get("plan") == []
    assert state.values.get("step_results") == []


async def test_persistently_failing_step_terminates_within_iteration_budget(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_MAX_ITERATIONS", 2)
    model = _OrchestratorFakeModel(
        plan_steps=[_PlannedStep(id="s1", goal="never passes", success_criteria="impossible")],
        verdict_passed=False,
        stream_text="best effort summary",
    )
    _patch_common(mocker, model)

    chunks = await _run_graph(model, "thread-orch-fail")

    # Worker ran the initial attempt plus MAX_ITERATIONS retries, then stopped —
    # plus one synthesizer astream. No infinite loop.
    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "best effort summary" in streamed
    verify_details = [c for c in chunks if c["kind"] == "detail" and c["data"]["kind"] == "verify"]
    assert len(verify_details) == 3  # initial + 2 retries, all failing


# --- Confirmation pause / resume (Phase 4) ------------------------------------


def test_confirmation_id_from_content():
    assert chat_orchestrator._confirmation_id_from_content('{"confirmation_id": "c1"}') == "c1"
    assert chat_orchestrator._confirmation_id_from_content('{"other": 1}') is None
    assert chat_orchestrator._confirmation_id_from_content("not json") is None


def test_route_from_dispatcher_pauses_on_awaiting_step():
    state = {"plan": [_step("s1", "awaiting"), _step("s2", "ran")], "messages": []}
    assert chat_orchestrator.route_from_dispatcher(state) == "confirmation_pause"


def test_has_pending_plan_includes_awaiting():
    assert chat_orchestrator._has_pending_plan({"plan": [_step("s1", "awaiting")], "messages": []})


async def test_resume_awaiting_steps_runs_approved_action(mocker):
    mocker.patch(
        "reporting.services.chat_orchestrator._collect_confirmations_to_run",
        new_callable=AsyncMock,
        return_value=([object()], _ConfirmResolution("run")),
    )
    mocker.patch(
        "reporting.services.chat_orchestrator._execute_confirmations",
        new_callable=AsyncMock,
        return_value=([("mutate_tool", "mutated ok")], [], []),
    )
    plan = [_step("s1", "awaiting")]
    results = [{"step_id": "s1", "confirmation_id": "c1", "awaiting_confirmation": True}]

    out = await chat_orchestrator._resume_awaiting_steps(plan, results, 0, _user(), "thread", lambda _d: None)

    assert out["plan"][0]["status"] == "ran"
    result = {r["step_id"]: r for r in out["step_results"]}["s1"]
    assert "mutated ok" in result["output"]
    assert not result.get("awaiting_confirmation")


async def test_resume_awaiting_steps_keeps_waiting(mocker):
    mocker.patch(
        "reporting.services.chat_orchestrator._collect_confirmations_to_run",
        new_callable=AsyncMock,
        return_value=([], _ConfirmResolution("wait", "Waiting for 1 more approval")),
    )
    plan = [_step("s1", "awaiting")]
    results = [{"step_id": "s1", "confirmation_id": "c1", "awaiting_confirmation": True}]

    out = await chat_orchestrator._resume_awaiting_steps(plan, results, 0, _user(), "thread", lambda _d: None)

    assert out["plan"][0]["status"] == "awaiting"  # still parked


async def test_resume_awaiting_steps_aborts_on_denied(mocker):
    mocker.patch(
        "reporting.services.chat_orchestrator._collect_confirmations_to_run",
        new_callable=AsyncMock,
        return_value=([], _ConfirmResolution("abort", "That action is not approved, so Seizu did not run it.")),
    )
    plan = [_step("s1", "awaiting")]
    results = [{"step_id": "s1", "confirmation_id": "c1", "awaiting_confirmation": True}]

    out = await chat_orchestrator._resume_awaiting_steps(plan, results, 0, _user(), "thread", lambda _d: None)

    step = out["plan"][0]
    assert step["status"] == "failed"
    assert step["no_retry"] is True  # denied actions are terminal; never re-prompt


async def test_confirmation_pause_then_resume_completes(mocker):
    from langgraph.checkpoint.memory import MemorySaver

    model = _OrchestratorFakeModel(stream_text="done after approval")
    _patch_common(mocker, model)
    awaiting_result = {
        "step_id": "s1",
        "goal": "do it",
        "success_criteria": "done",
        "output": "needs approval",
        "tools_used": ["mutate_tool"],
        "awaiting_confirmation": True,
        "confirmation_id": "c1",
        "confirmation_message": "Approval needed: http://confirm/c1",
    }
    worker = mocker.patch(
        "reporting.services.chat_orchestrator._run_worker_step",
        new_callable=AsyncMock,
        return_value=awaiting_result,
    )
    graph = chat_graph.build_chat_graph(MemorySaver())
    config = {"configurable": {"thread_id": "t-pause", "client_thread_id": "t-pause", "current_user": _user()}}

    # Turn 1: the worker parks on an action confirmation; the plan pauses.
    chunks1 = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="please mutate something")]}, config, stream_mode="custom"
        )
    ]
    streamed1 = "".join(chunk["content"] for chunk in chunks1 if chunk["kind"] == "token")
    assert "Approval needed" in streamed1
    state1 = await graph.aget_state({"configurable": {"thread_id": "t-pause"}})
    assert state1.values["plan"][0]["status"] == "awaiting"  # persisted, not cleared

    # Turn 2: the user approves; resume runs the action and finishes the plan.
    mocker.patch(
        "reporting.services.chat_orchestrator._collect_confirmations_to_run",
        new_callable=AsyncMock,
        return_value=([object()], _ConfirmResolution("run")),
    )
    mocker.patch(
        "reporting.services.chat_orchestrator._execute_confirmations",
        new_callable=AsyncMock,
        return_value=([("mutate_tool", "mutated ok")], [], []),
    )
    worker.reset_mock()
    resume_msg = HumanMessage(content="approved", additional_kwargs={"resume_confirmation_id": "c1"})
    chunks2 = [chunk async for chunk in graph.astream({"messages": [resume_msg]}, config, stream_mode="custom")]

    streamed2 = "".join(chunk["content"] for chunk in chunks2 if chunk["kind"] == "token")
    assert "done after approval" in streamed2
    worker.assert_not_called()  # the resumed step is not re-run by a worker
    state2 = await graph.aget_state({"configurable": {"thread_id": "t-pause"}})
    assert state2.values.get("plan") == []  # cleared after synthesis


async def test_disabled_orchestrator_uses_simple_path(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", False)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    model = _OrchestratorFakeModel(stream_text="simple reply")
    mocker.patch("reporting.services.chat_graph.get_chat_model", return_value=model)
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_prompts_for_user", return_value=[])
    mocker.patch("reporting.services.chat_graph.mcp_runtime.list_tools_for_user", return_value=[])

    chunks = await _run_graph(model, "thread-simple")

    detail_kinds = [chunk["data"]["kind"] for chunk in chunks if chunk["kind"] == "detail"]
    assert "plan" not in detail_kinds and "routing" not in detail_kinds
    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "simple reply" in streamed
