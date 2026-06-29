from typing import Any
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessageChunk, HumanMessage

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import User
from reporting.services import chat_graph, chat_orchestrator
from reporting.services.chat_budget import BudgetController, initial_budget_ledger
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
        "action_kind": "auto",
        "required_action": "",
        "required_arguments": {},
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
        stream_text: str | list[str] = "final answer",
        finish_reason: str | None = None,
    ) -> None:
        self.route = route
        self.plan_steps = plan_steps
        self.verdict_passed = verdict_passed
        self.stream_text = stream_text
        self.finish_reason = finish_reason
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
        metadata = {"finish_reason": self.finish_reason} if self.finish_reason else {}
        if isinstance(self.stream_text, list):
            index = min(self.astream_calls - 1, len(self.stream_text) - 1)
            content = self.stream_text[index]
        else:
            content = self.stream_text
        yield AIMessageChunk(content=content, response_metadata=metadata)


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
            _PlannedStep(
                id="s1",
                goal="a",
                depends_on=["s1", "ghost"],
                action_kind="skill",
                required_action="investigation__triage",
                required_arguments={"org": "mappedsky"},
            ),
            _PlannedStep(id="s2", goal="b", depends_on=["s1"], action_kind="answer"),
        ]
    )
    assert plan[0]["depends_on"] == []
    assert plan[1]["depends_on"] == ["s1"]
    assert plan[0]["action_kind"] == "skill"
    assert plan[0]["required_action"] == "investigation__triage"
    assert plan[0]["required_arguments"] == {"org": "mappedsky"}
    assert plan[1]["action_kind"] == "answer"
    assert all(step["status"] == "pending" for step in plan)


async def test_planner_records_structured_output_fallback_as_run_error(mocker):
    invoke = mocker.patch(
        "reporting.services.chat_orchestrator._structured_invoke",
        new_callable=AsyncMock,
        side_effect=ValueError(
            "Model did not return a JSON object for _Plan after 2 attempts "
            "(chars=0, finish_reason=length; chars=0, finish_reason=length)"
        ),
    )
    mocker.patch("reporting.services.chat_orchestrator._list_chat_prompts", new_callable=AsyncMock, return_value=[])
    mocker.patch("reporting.services.chat_orchestrator.get_stream_writer", return_value=lambda _event: None)
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_PLANNER_MAX_TOKENS", 4096)

    result = await chat_orchestrator.planner_node(
        {"messages": [HumanMessage(content="investigate and report")]},
        {"configurable": {"current_user": _user()}},
    )

    assert len(result["plan"]) == 1
    assert result["run_errors"][0].startswith("Planner structured output failed:")
    assert invoke.await_args.kwargs["max_output_tokens"] == 4096


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


def test_step_tool_specs_enforces_required_action_contract():
    specs = [
        chat_graph.ChatToolSpec(
            name="investigation__triage",
            kind="skill",
            description="Triage",
            input_schema={"type": "object"},
        ),
        chat_graph.ChatToolSpec(
            name="github_security__org_overview",
            kind="tool",
            description="Overview",
            input_schema={"type": "object"},
        ),
    ]

    selected, error = chat_orchestrator._step_tool_specs(
        specs,
        _step("s1", action_kind="skill", required_action="investigation__triage"),
    )
    assert error is None
    assert [spec.name for spec in selected] == ["investigation__triage"]

    selected, error = chat_orchestrator._step_tool_specs(
        specs,
        _step("s2", action_kind="answer", suggested_tools=["github_security__org_overview"]),
    )
    assert error is None
    assert selected == []

    selected, error = chat_orchestrator._step_tool_specs(
        specs,
        _step("s3", action_kind="tool", required_action="investigation__triage"),
    )
    assert selected == []
    assert "not available" in str(error)


def test_apply_planned_arguments_fills_omitted_and_keeps_worker_values():
    spec = chat_graph.ChatToolSpec(
        name="github_security__repo_risk_summary",
        kind="tool",
        description="Repo risks",
        input_schema={"type": "object"},
    )
    step = _step(
        "s1",
        action_kind="tool",
        required_action="github_security__repo_risk_summary",
        required_arguments={"org": "mappedsky"},
    )

    # Omitted arg is filled from the planner's value.
    filled = chat_orchestrator._apply_planned_arguments(
        step, [chat_graph.ToolCallRequest(id="c1", name=spec.name, arguments={}, spec=spec)]
    )
    assert filled[0].arguments == {"org": "mappedsky"}

    # The worker's explicit value always wins — never overridden, never rejected.
    kept = chat_orchestrator._apply_planned_arguments(
        step, [chat_graph.ToolCallRequest(id="c1", name=spec.name, arguments={"org": "other"}, spec=spec)]
    )
    assert kept[0].arguments == {"org": "other"}


def test_apply_planned_arguments_does_not_clobber_dependency_derived_values():
    # The planner can only template a derived value ("<from s2>"); the worker
    # supplies the real one, and that must survive (this is the s3 regression).
    spec = chat_graph.ChatToolSpec(
        name="attack_path__trace", kind="skill", description="trace", input_schema={"type": "object"}
    )
    step = _step(
        "s3",
        action_kind="skill",
        required_action="attack_path__trace",
        required_arguments={"vulnerability_ids": ["<from s2>"], "depth": 3},
    )
    request = chat_graph.ToolCallRequest(
        id="c1", name=spec.name, arguments={"vulnerability_ids": ["CVE-2023-41419"]}, spec=spec
    )

    applied = chat_orchestrator._apply_planned_arguments(step, [request])
    # Worker's real CVE kept; the concrete static arg (depth) still filled.
    assert applied[0].arguments == {"vulnerability_ids": ["CVE-2023-41419"], "depth": 3}


async def test_worker_step_fails_when_required_action_is_not_called():
    model = _OrchestratorFakeModel(stream_text="I will pull the repo risk snapshot next.")
    details: list[dict[str, Any]] = []
    step = _step(
        "s1",
        action_kind="tool",
        required_action="github_security__repo_risk_summary",
        success_criteria="Repo risk summary was retrieved.",
    )
    tool_specs = [
        chat_graph.ChatToolSpec(
            name="github_security__repo_risk_summary",
            kind="tool",
            description="Repo risks",
            input_schema={"type": "object"},
        )
    ]

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=model,
        current_user=_user(),
        session_key="thread",
        config={"configurable": {}},
        tool_specs=tool_specs,
        # The required tool is already disclosed; the failure is that the worker
        # narrated instead of calling it (not that the tool was unavailable).
        disclosed_names={"github_security__repo_risk_summary"},
        writer=lambda event: details.append(event),
    )

    assert result["output"] == ""
    assert "required structured action" in result["execution_error"]
    passed, reason = await chat_orchestrator._verify_step(step, result, {"configurable": {}})
    assert passed is False
    assert "required structured action" in reason


async def test_worker_step_can_call_tools_a_skill_discloses(mocker):
    # A skill step starts with only the skill spec; rendering the skill discloses
    # its sub-tools, which must then become callable within the same step.
    skill = chat_graph.ChatToolSpec(
        name="github_security", kind="skill", description="overview", input_schema={"type": "object"}
    )
    sub_tool = chat_graph.ChatToolSpec(
        name="github_security__org_overview", kind="tool", description="org overview", input_schema={"type": "object"}
    )
    tool_specs = [skill, sub_tool]
    step = _step("s1", action_kind="skill", required_action="github_security", success_criteria="findings")

    class _ScriptedToolModel:
        def __init__(self, responses: list) -> None:
            self.responses = responses
            self.calls = 0

        def bind_tools(self, _tools: Any) -> "_ScriptedToolModel":
            return self

        async def astream(self, _input: Any, config: Any = None, **_kwargs: Any):
            index = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            yield self.responses[index]

    from langchain_core.messages import AIMessage

    model = _ScriptedToolModel(
        [
            AIMessage(content="", tool_calls=[{"name": "github_security", "args": {}, "id": "c1"}]),
            AIMessage(content="", tool_calls=[{"name": "github_security__org_overview", "args": {}, "id": "c2"}]),
            AIMessage(content="Found 2 critical CVEs: CVE-1, CVE-2."),
        ]
    )

    async def _fake_batch(batch, current_user, *, session_key=None, batch_id=None, **_kw):
        out = []
        for req in batch:
            if req.name == "github_security":
                out.append(
                    chat_graph.ToolCallResult(
                        request=req,
                        content='{"workflow": "call org_overview"}',
                        tools_required=("github_security__org_overview",),
                    )
                )
            else:
                out.append(chat_graph.ToolCallResult(request=req, content='{"critical": 2}'))
        return out

    mocker.patch("reporting.services.chat_orchestrator._run_tool_call_batch", _fake_batch)

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=model,
        current_user=_user(),
        session_key="thread",
        config={"configurable": {}},
        tool_specs=tool_specs,
        writer=lambda event: None,
    )

    # The disclosed sub-tool was reachable, so the step produced real findings
    # instead of stalling on an uncallable tool.
    assert result.get("execution_error") in (None, "")
    assert result["blocked"] is None
    assert "github_security" in result["tools_used"]
    assert "github_security__org_overview" in result["tools_used"]
    assert result["output"] == "Found 2 critical CVEs: CVE-1, CVE-2."
    # The disclosure propagates so dependent steps inherit it.
    assert result["disclosed_tools"] == ["github_security__org_overview"]


async def test_worker_step_always_disclosed_tools_available_after_skill_renders(mocker):
    # Regression: always-disclosed tools (e.g. sandbox__delegate) must be in the
    # model's tool list from the very first worker turn even when _step_tool_specs
    # restricts active_specs to only the required skill spec.  Previously, a skill
    # with tools_required=[] would render, and the follow-up sandbox__delegate call
    # would fail because sandbox__delegate was absent from available.
    skill = chat_graph.ChatToolSpec(
        name="cve_response__cve_severity_analysis",
        kind="skill",
        description="CVE analysis",
        input_schema={"type": "object"},
    )
    sandbox = chat_graph.ChatToolSpec(
        name="sandbox__delegate",
        kind="tool",
        description="Delegate to sandbox",
        input_schema={"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
    )
    tool_specs = [skill, sandbox]
    step = _step("s1", action_kind="skill", required_action="cve_response__cve_severity_analysis")

    class _ScriptedToolModel:
        def __init__(self, responses: list) -> None:
            self.responses = responses
            self.calls = 0

        def bind_tools(self, _tools: Any) -> "_ScriptedToolModel":
            return self

        async def astream(self, _input: Any, config: Any = None, **_kwargs: Any):
            index = min(self.calls, len(self.responses) - 1)
            self.calls += 1
            yield self.responses[index]

    from langchain_core.messages import AIMessage

    model = _ScriptedToolModel(
        [
            # Turn 1: call the skill
            AIMessage(content="", tool_calls=[{"name": "cve_response__cve_severity_analysis", "args": {}, "id": "c1"}]),
            # Turn 2: skill rendered with tools_required=[]; model follows up with sandbox__delegate
            AIMessage(
                content="", tool_calls=[{"name": "sandbox__delegate", "args": {"task": "compute stats"}, "id": "c2"}]
            ),
            AIMessage(content="## CVE Risk Distribution\n..."),
        ]
    )

    async def _fake_batch(batch, current_user, *, session_key=None, batch_id=None, **_kw):
        out = []
        for req in batch:
            if req.name == "cve_response__cve_severity_analysis":
                # Skill renders with no tools_required
                out.append(
                    chat_graph.ToolCallResult(
                        request=req, content="call sandbox__delegate with task=...", tools_required=()
                    )
                )
            else:
                out.append(chat_graph.ToolCallResult(request=req, content='{"result": "stats computed"}'))
        return out

    mocker.patch("reporting.services.chat_orchestrator._run_tool_call_batch", _fake_batch)
    mocker.patch(
        "reporting.services.mcp_builtins.always_disclosed_tool_names", return_value=frozenset({"sandbox__delegate"})
    )

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=model,
        current_user=_user(),
        session_key="thread",
        config={"configurable": {}},
        tool_specs=tool_specs,
        writer=lambda event: None,
    )

    assert result.get("execution_error") in (None, "")
    assert "sandbox__delegate" in result["tools_used"]
    assert result["output"] == "## CVE Risk Distribution\n..."


def test_match_action_spec_resolves_short_skill_id():
    skill = chat_graph.ChatToolSpec(
        name="github_security_investigations__github_org_security_overview",
        kind="skill",
        description="overview",
        input_schema={"type": "object"},
    )
    # The planner referenced the skill by its short id, not the full slug.
    assert chat_orchestrator._match_action_spec([skill], "skill", "github_org_security_overview") is skill
    assert (
        chat_orchestrator._match_action_spec(
            [skill], "skill", "github_security_investigations__github_org_security_overview"
        )
        is skill
    )
    # Wrong kind / unknown name does not resolve.
    assert chat_orchestrator._match_action_spec([skill], "tool", "github_org_security_overview") is None
    assert chat_orchestrator._match_action_spec([skill], "skill", "nope") is None


def test_step_tool_specs_accepts_short_skill_id_and_reports_canonical():
    skill = chat_graph.ChatToolSpec(
        name="github_security_investigations__github_org_security_overview",
        kind="skill",
        description="overview",
        input_schema={"type": "object"},
    )
    step = _step("s1", action_kind="skill", required_action="github_org_security_overview")
    specs, error = chat_orchestrator._step_tool_specs([skill], step)
    assert error is None
    assert [s.name for s in specs] == ["github_security_investigations__github_org_security_overview"]


async def test_worker_step_cannot_call_undisclosed_tool_under_progressive_disclosure(mocker):
    # A bare tool step whose tool no skill has disclosed is not callable.
    sub_tool = chat_graph.ChatToolSpec(
        name="graph__query", kind="tool", description="run cypher", input_schema={"type": "object"}
    )
    step = _step("s1", action_kind="tool", required_action="graph__query", success_criteria="rows")

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=object(),  # never invoked: the contract fails before any model call
        current_user=_user(),
        session_key="thread",
        config={"configurable": {}},
        tool_specs=[sub_tool],
        disclosed_names=set(),
        progressive=True,
        writer=lambda event: None,
    )

    assert result["output"] == ""
    assert "not available" in result["execution_error"]


async def test_worker_step_synthesizes_when_action_budget_exhausted(mocker):
    # A worker that keeps calling tools until its budget runs out must still
    # produce a result (forced synthesis) rather than reporting "no output".
    from langchain_core.messages import AIMessage

    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS", 2)
    spec = chat_graph.ChatToolSpec(name="t__one", kind="tool", description="x", input_schema={"type": "object"})
    step = _step("s1")  # action_kind="auto", no required action

    class _BudgetModel:
        def __init__(self) -> None:
            self.calls = 0

        def bind_tools(self, _tools: Any) -> "_BudgetModel":
            return self

        async def astream(self, _input: Any, config: Any = None, **_kwargs: Any):
            self.calls += 1
            if self.calls <= 2:
                yield AIMessage(content="", tool_calls=[{"name": "t__one", "args": {}, "id": f"c{self.calls}"}])
            else:
                # The forced-synthesis turn (called with no tools).
                yield AIMessage(content="Validated the queries but ran out of budget before applying updates.")

    async def _fake_batch(batch, current_user, *, session_key=None, batch_id=None, **_kw):
        return [chat_graph.ToolCallResult(request=req, content="{}") for req in batch]

    mocker.patch("reporting.services.chat_orchestrator._run_tool_call_batch", _fake_batch)

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=_BudgetModel(),
        current_user=_user(),
        session_key="thread",
        config={"configurable": {}},
        tool_specs=[spec],
        disclosed_names={"t__one"},
        progressive=True,
        writer=lambda event: None,
    )

    assert result["blocked"] is None
    assert result.get("execution_error") in (None, "")
    assert result["tools_used"] == ["t__one", "t__one"]  # stopped at the budget
    assert "ran out of budget" in result["output"]  # forced synthesis, not empty


async def test_budgeted_headless_worker_is_not_stopped_by_per_step_action_guard(mocker):
    from langchain_core.messages import AIMessage

    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_WORKER_MAX_ACTIONS", 2)
    spec = chat_graph.ChatToolSpec(name="t__one", kind="tool", description="x", input_schema={"type": "object"})
    step = _step("s1")

    class _PlanModel:
        def __init__(self) -> None:
            self.calls = 0

        def bind_tools(self, _tools: Any) -> "_PlanModel":
            return self

        async def astream(self, _input: Any, config: Any = None, **_kwargs: Any):
            self.calls += 1
            if self.calls <= 3:
                yield AIMessage(content="", tool_calls=[{"name": "t__one", "args": {}, "id": f"c{self.calls}"}])
            else:
                yield AIMessage(content="Plan step complete.")

    async def _fake_batch(batch, current_user, *, session_key=None, batch_id=None, **_kw):
        return [chat_graph.ToolCallResult(request=req, content="{}") for req in batch]

    mocker.patch("reporting.services.chat_orchestrator._run_tool_call_batch", _fake_batch)
    ledger = initial_budget_ledger()
    ledger.update(
        {
            "token_limit": 1_000_000,
            "reserve_tokens": 0,
            "soft_limit_ratio": 1.0,
            "max_llm_calls": 20,
            "reserve_llm_calls": 2,
        }
    )
    controller = BudgetController(ledger)

    result = await chat_orchestrator._run_worker_step(
        step,
        plan=[step],
        results=[],
        model=_PlanModel(),
        current_user=_user(),
        session_key="thread",
        config={"configurable": {"budget_controller": controller, "headless": True}},
        tool_specs=[spec],
        disclosed_names={"t__one"},
        progressive=True,
        writer=lambda event: None,
    )

    assert result["tools_used"] == ["t__one", "t__one", "t__one"]
    assert result["output"] == "Plan step complete."


def test_orchestration_details_carry_step_hierarchy():
    plan = [_step("s1", goal="gather"), _step("s2", goal="summarize", depends_on=["s1"])]
    plan[0]["goal"] = "gather"
    plan[1]["goal"] = "summarize"
    results = [
        {
            "step_id": "s1",
            "output": "found data",
            "tools_used": ["github_security", "github_security__org_overview"],
            "verified": True,
            "verify_reason": "ok",
        },
        {"step_id": "s2", "output": "summary", "tools_used": [], "verified": True, "verify_reason": "ok"},
    ]
    details = chat_orchestrator._orchestration_details(plan, results)

    # Step and its tool/verify entries all carry the same step_id for nesting.
    s1_step = next(d for d in details if d["kind"] == "step" and d.get("step_id") == "s1")
    assert s1_step["status"] == "completed"
    tool_details = [d for d in details if d["kind"] == "tool" and d.get("step_id") == "s1"]
    assert {d["title"] for d in tool_details} == {
        "Tool: github_security",
        "Tool: github_security__org_overview",
    }
    s1_verify = next(d for d in details if d["kind"] == "verify" and d.get("step_id") == "s1")
    assert s1_verify["status"] == "completed"


def test_orchestration_details_replay_tool_details_with_children():
    """When a step persisted full tool_details (incl. subagent children), the
    reconstructed trace replays them verbatim instead of name-only — so a reloaded
    orchestrator turn shows the same nested subagent section it showed live."""
    plan = [_step("s1", goal="gather")]
    plan[0]["goal"] = "gather"
    subagent_detail = {
        "kind": "subagent",
        "title": "Tool: sandbox__delegate",
        "status": "completed",
        "step_id": "s1",
        "detail_id": "tc-1",
        "arguments": "task: crunch numbers",
        "body": "done",
        "children": [
            {
                "kind": "tool",
                "title": "Sandbox: run_python",
                "status": "completed",
                "detail_id": "sb-1",
                "body": "42",
            }
        ],
    }
    results = [
        {
            "step_id": "s1",
            "output": "done",
            "tools_used": ["sandbox__delegate"],
            "tool_details": [subagent_detail],
            "verified": True,
            "verify_reason": "ok",
        }
    ]
    details = chat_orchestrator._orchestration_details(plan, results)

    replayed = next(d for d in details if d.get("detail_id") == "tc-1")
    assert replayed["kind"] == "subagent"
    assert replayed["step_id"] == "s1"
    assert [c["title"] for c in replayed["children"]] == ["Sandbox: run_python"]
    # The name-only fallback entry must NOT also appear (no duplication).
    assert not [d for d in details if d.get("title") == "Tool: sandbox__delegate" and "children" not in d]


def test_orchestration_details_fall_back_to_names_without_tool_details():
    """Results persisted before tool_details existed still reconstruct name-only."""
    plan = [_step("s1", goal="gather")]
    plan[0]["goal"] = "gather"
    results = [{"step_id": "s1", "output": "done", "tools_used": ["graph__query"]}]
    details = chat_orchestrator._orchestration_details(plan, results)
    tool_entries = [d for d in details if d["kind"] == "tool" and d.get("step_id") == "s1"]
    assert [d["title"] for d in tool_entries] == ["Tool: graph__query"]


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


def test_headless_turn_uses_same_router_decision_as_interactive(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    state = {"messages": [HumanMessage(content="inspect one repository")]}

    assert chat_orchestrator._forced_route(state, {"configurable": {}}) is None
    assert chat_orchestrator._forced_route(state, {"configurable": {"headless": True}}) is None


async def test_router_resumes_in_flight_plan(mocker):
    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    state = {"messages": [HumanMessage(content="continue")], "plan": [_step("s2", "pending")]}
    result = await chat_orchestrator.router_node(state, {"configurable": {}})
    assert result == {"route": "orchestrate"}


async def test_router_uses_json_fallback_when_structured_output_fails(mocker):
    class _BrokenStructured:
        async def ainvoke(self, _messages, config=None):
            raise RuntimeError("structured output unavailable")

    class _JsonRouteModel:
        def with_structured_output(self, _schema):
            return _BrokenStructured()

        async def astream(self, _input, config=None, **kwargs):
            yield AIMessageChunk(content='{"route": "orchestrate", "reason": "multi-step"}')

    mocker.patch("reporting.settings.CHAT_ORCHESTRATOR_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROVIDER", "openai")
    mocker.patch("reporting.services.chat_orchestrator.get_chat_model", return_value=_JsonRouteModel())
    mocker.patch("reporting.services.chat_orchestrator.get_stream_writer", return_value=lambda _event: None)

    result = await chat_orchestrator.router_node(
        {"messages": [HumanMessage(content="find a vuln, then trace attack paths")]},
        {"configurable": {}},
    )

    assert result == {"route": "orchestrate"}


async def test_router_pins_continuation_turn_to_simple(mocker):
    # The structured router is scripted to say "orchestrate"; the continuation
    # short-circuit must win, so the planner never runs and the single-agent path
    # keeps extending the prior answer (and emits the cut-off / Continue signal).
    model = _OrchestratorFakeModel(route="orchestrate")
    _patch_common(mocker, model)
    msg = HumanMessage(content="continue", additional_kwargs={"continue_response": True})
    result = await chat_orchestrator.router_node({"messages": [msg]}, {"configurable": {}})
    assert result == {"route": "simple"}


async def test_router_pins_simple_confirmation_resume_to_simple(mocker):
    # A confirmation resume with no in-flight plan belongs to the single-agent
    # resume path, not the orchestrator — even though the router is told to
    # orchestrate.
    model = _OrchestratorFakeModel(route="orchestrate")
    _patch_common(mocker, model)
    msg = HumanMessage(content="approved", additional_kwargs={"resume_confirmation_id": "c1"})
    result = await chat_orchestrator.router_node({"messages": [msg]}, {"configurable": {}})
    assert result == {"route": "simple"}


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


async def test_orchestrated_synthesis_cutoff_emits_continue_signal(mocker):
    # A synthesis truncated by the output limit must emit the same finish_reason
    # and cut-off notice as the single-agent path, so "Continue response" appears.
    model = _OrchestratorFakeModel(stream_text="partial synthesis", finish_reason="length")
    _patch_common(mocker, model)

    chunks = await _run_graph(model, "thread-orch-cutoff")

    assert {"kind": "finish_reason", "finish_reason": "length"} in chunks
    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "hit its output limit" in streamed


async def test_orchestrated_synthesis_retries_internal_action_transcript(mocker):
    model = _OrchestratorFakeModel(
        plan_steps=[_PlannedStep(id="s1", goal="trace attack path", success_criteria="has path")],
        stream_text=[
            "Entry path: public DNS to vulnerable Lambda.",
            "Seizu ran 1 action:\n\n`attack_paths__entry_paths_backward` with arguments `{}` returned: []",
            "CVE-2024-34069 is remotely exploitable through public DNS to the vulnerable Lambda.",
        ],
    )
    _patch_common(mocker, model)

    chunks = await _run_graph(model, "thread-orch-transcript-retry", text="trace attack paths")

    streamed = "".join(chunk["content"] for chunk in chunks if chunk["kind"] == "token")
    assert "Seizu ran 1 action" not in streamed
    assert "CVE-2024-34069 is remotely exploitable" in streamed


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
    # Flagged so the verifier auto-passes it (no re-verify/retry of an applied change).
    assert result["confirmation_executed"] is True


async def test_verify_step_auto_passes_executed_confirmation():
    # A step whose approved action executed must pass without re-judging the raw
    # tool output (and without a model call), so it is never retried.
    step = _step("s1", success_criteria="the tools are updated to use indexed queries")
    result = {"step_id": "s1", "output": '{"tool_id": "trace", "version": 2}', "confirmation_executed": True}

    passed, reason = await chat_orchestrator._verify_step(step, result, {"configurable": {}})

    assert passed is True
    assert "approved action" in reason.lower()


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
