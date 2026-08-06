"""Tests for the ``sandbox__delegate`` MCP built-in."""

import json
from contextlib import ExitStack, asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from mcp.types import Prompt, Tool

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.report_config import User
from reporting.services import chat_budget, episodic_memory, mcp_runtime
from reporting.services.chat_budget import BudgetController, BudgetExceeded, initial_budget_ledger
from reporting.services.mcp_builtins import find_builtin, list_builtin_tools
from reporting.services.mcp_builtins.sandbox import (
    SandboxBackend,
    _build_sandbox_tools,
    _build_seizu_tools,
    _get_sandbox_model,
    _handle_delegate,
    _subagent_prompt,
    _ToolMessageNormalizingModel,
    _wrap_with_detail_events,
)
from reporting.services.sandbox_backend import _E2BSandboxBackend, open_backend

_NOW = "2024-01-01T00:00:00+00:00"


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="u1",
            sub="u1",
            iss="dev",
            email="u1@example.com",
            display_name="u1",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


def _make_fake_backend() -> MagicMock:
    """Return a mock that satisfies the SandboxBackend protocol."""
    backend = MagicMock()
    backend.run_python = AsyncMock(return_value="hello world")
    backend.run_bash = AsyncMock(return_value="")
    backend.read_file = AsyncMock(return_value="file content")
    backend.write_file = AsyncMock(return_value="Wrote 12 bytes to /tmp/test.txt")
    backend.list_files = AsyncMock(return_value="")
    backend.run_bash_streaming = AsyncMock(return_value="")
    backend.get_host = AsyncMock(return_value="host")
    backend.get_traffic_access_token = AsyncMock(return_value="")
    # Assigned rather than left to MagicMock's auto-attributes: the protocol
    # check reads members with getattr_static, which only sees what was set.
    backend.sandbox_id = "sandbox-test"
    return backend


def _make_fake_agent_result(text: str) -> dict[str, Any]:
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = []
    return {"messages": [msg]}


def _open_backend_ctx(fake_backend: MagicMock) -> Any:
    """Return a patched _open_backend that yields fake_backend."""

    @asynccontextmanager
    async def _ctx(**_kwargs: Any):  # type: ignore[misc]
        yield fake_backend

    return _ctx


# ---------------------------------------------------------------------------
# SandboxBackend protocol
# ---------------------------------------------------------------------------


def test_fake_backend_satisfies_protocol() -> None:
    """The mock backend should be recognised as a SandboxBackend at runtime."""
    assert isinstance(_make_fake_backend(), SandboxBackend)


# ---------------------------------------------------------------------------
# _E2BSandboxBackend.run_python — stdout/stderr capture
# ---------------------------------------------------------------------------


def _make_execution(*, stdout: list[str], stderr: list[str], text: str | None = None, error: Any = None) -> MagicMock:
    logs = MagicMock()
    logs.stdout = stdout
    logs.stderr = stderr
    execution = MagicMock()
    execution.logs = logs
    execution.text = text
    execution.error = error
    return execution


async def test_run_python_captures_stdout() -> None:
    """print() output (logs.stdout) must appear in the returned string."""
    sdk = MagicMock()
    sdk.run_code = AsyncMock(return_value=_make_execution(stdout=["hello\n", "world\n"], stderr=[]))
    result = await _E2BSandboxBackend(sdk).run_python("print('hello\\nworld')")
    assert "hello" in result
    assert "world" in result


async def test_run_python_captures_stderr() -> None:
    """stderr output (logs.stderr) must appear in the returned string."""
    sdk = MagicMock()
    sdk.run_code = AsyncMock(return_value=_make_execution(stdout=[], stderr=["warning\n"]))
    result = await _E2BSandboxBackend(sdk).run_python("import sys; print('warning', file=sys.stderr)")
    assert "warning" in result
    assert "stderr" in result


async def test_run_python_captures_expression_result() -> None:
    """execution.text (return-value display) is included alongside stdout."""
    sdk = MagicMock()
    sdk.run_code = AsyncMock(return_value=_make_execution(stdout=["line\n"], stderr=[], text="42"))
    result = await _E2BSandboxBackend(sdk).run_python("print('line'); 6*7")
    assert "line" in result
    assert "42" in result


async def test_run_python_no_output_fallback() -> None:
    """Empty execution returns the sentinel string instead of an empty string."""
    sdk = MagicMock()
    sdk.run_code = AsyncMock(return_value=_make_execution(stdout=[], stderr=[]))
    result = await _E2BSandboxBackend(sdk).run_python("x = 1")
    assert result == "(no output)"


# ---------------------------------------------------------------------------
# Registry visibility
# ---------------------------------------------------------------------------


def test_sandbox_delegate_absent_from_default_listing() -> None:
    """MCP server path: sandbox__delegate must not appear (chat_only)."""
    tools = list_builtin_tools()
    assert not any(t.name == "sandbox__delegate" for t in tools)


def test_sandbox_delegate_present_with_include_chat_only() -> None:
    """Chat agent path: sandbox__delegate appears when include_chat_only=True."""
    # The tool is gated by SANDBOX_ENABLED (default false), so pin it on to keep
    # the registry assertions independent of the ambient environment.
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tools = list_builtin_tools(include_chat_only=True)
    assert any(t.name == "sandbox__delegate" for t in tools)


def test_find_builtin_excludes_sandbox_delegate_by_default() -> None:
    assert find_builtin("sandbox__delegate") is None


def test_find_builtin_includes_sandbox_delegate_with_flag() -> None:
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert tool.group == "sandbox"


def test_sandbox_delegate_has_required_permissions() -> None:
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert Permission.SANDBOX_DELEGATE.value in tool.required_permissions


def test_sandbox_delegate_is_chat_safe_without_confirmation() -> None:
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert tool.chat_safe_without_confirmation is True
    assert tool.confirmation is None


def test_sandbox_delegate_is_always_disclosed() -> None:
    from reporting.services.mcp_builtins import always_disclosed_tool_names

    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tool = find_builtin("sandbox__delegate", include_chat_only=True)
        assert tool is not None
        assert tool.always_disclosed is True
        assert "sandbox__delegate" in always_disclosed_tool_names()


def test_always_disclosed_respects_enabled() -> None:
    """always_disclosed_tool_names() must honor each tool's enabled() gate, so a
    disabled sandbox does not linger in the internal always-disclosed set."""
    from reporting.services.mcp_builtins import always_disclosed_tool_names

    with patch("reporting.settings.SANDBOX_ENABLED", False):
        assert "sandbox__delegate" not in always_disclosed_tool_names()
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        assert "sandbox__delegate" in always_disclosed_tool_names()


# ---------------------------------------------------------------------------
# Handler: disabled / misconfigured state
# ---------------------------------------------------------------------------


def test_sandbox_delegate_absent_when_disabled() -> None:
    """sandbox__delegate must not appear in any listing when SANDBOX_ENABLED=false."""
    with patch("reporting.settings.SANDBOX_ENABLED", False):
        all_tools = list_builtin_tools(include_chat_only=True)
        assert not any(t.name == "sandbox__delegate" for t in all_tools)
        assert find_builtin("sandbox__delegate", include_chat_only=True) is None


def test_sandbox_delegate_present_when_enabled() -> None:
    """sandbox__delegate appears in listings when SANDBOX_ENABLED=true."""
    with patch("reporting.settings.SANDBOX_ENABLED", True):
        tool = find_builtin("sandbox__delegate", include_chat_only=True)
        assert tool is not None


async def test_handler_returns_error_for_mock_provider() -> None:
    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "mock"),
    ):
        result = await _handle_delegate({"task": "print hello"}, _current_user())
    assert "error" in result
    assert "mock" in result["error"]


# ---------------------------------------------------------------------------
# Handler: successful delegation
# ---------------------------------------------------------------------------


async def test_handler_returns_agent_result() -> None:
    fake_backend = _make_fake_backend()
    fake_result = _make_fake_agent_result("Computed result: 42")

    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch(
            "reporting.services.mcp_builtins.sandbox.open_backend",
            new=_open_backend_ctx(fake_backend),
        ),
        patch(
            "reporting.services.mcp_builtins.sandbox.create_react_agent",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_result)),
        ),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        result = await _handle_delegate({"task": "compute something"}, _current_user())

    assert result.get("result") == "Computed result: 42"


async def test_handler_truncates_long_output() -> None:
    long_output = "x" * 100_000
    fake_backend = _make_fake_backend()
    fake_result = _make_fake_agent_result(long_output)

    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 1_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch(
            "reporting.services.mcp_builtins.sandbox.open_backend",
            new=_open_backend_ctx(fake_backend),
        ),
        patch(
            "reporting.services.mcp_builtins.sandbox.create_react_agent",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_result)),
        ),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        result = await _handle_delegate({"task": "generate lots of output"}, _current_user())

    output = result.get("result", "")
    assert len(output.encode()) <= 1_000 + len("\n[truncated]")
    assert output.endswith("[truncated]")


async def test_handler_passes_context_in_prompt() -> None:
    fake_backend = _make_fake_backend()
    fake_result = _make_fake_agent_result("done")
    captured_messages: list[Any] = []

    async def fake_ainvoke(inputs: dict[str, Any]) -> dict[str, Any]:
        captured_messages.extend(inputs.get("messages", []))
        return fake_result

    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch(
            "reporting.services.mcp_builtins.sandbox.open_backend",
            new=_open_backend_ctx(fake_backend),
        ),
        patch(
            "reporting.services.mcp_builtins.sandbox.create_react_agent",
            return_value=MagicMock(ainvoke=fake_ainvoke),
        ),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        await _handle_delegate({"task": "analyze it", "context": "data: 1,2,3"}, _current_user())

    assert captured_messages
    prompt_text = captured_messages[0].content
    assert "data: 1,2,3" in prompt_text
    assert "analyze it" in prompt_text


async def test_handler_returns_error_on_timeout() -> None:
    @asynccontextmanager
    async def slow_backend(**_kwargs: Any):  # type: ignore[misc]
        await AsyncMock(side_effect=TimeoutError())()
        yield _make_fake_backend()

    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 0),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch("reporting.services.mcp_builtins.sandbox.open_backend", new=slow_backend),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        result = await _handle_delegate({"task": "slow task"}, _current_user())

    assert "error" in result
    assert "timed out" in result["error"]


async def test_handler_persists_inner_tool_events_through_real_agent() -> None:
    """End-to-end: an inner sandbox tool call runs through a *real*
    ``create_react_agent`` and its completed event lands in the accumulator the
    outer node captured, tagged with the outer tool call's id.

    The other handler tests mock ``create_react_agent`` away, so this is the only
    coverage of the actual path that persists inner-tool details (the path whose
    breakage made sandbox sub-calls vanish on reload): the accumulator must be
    captured at the handler boundary and threaded into the wrapped tools, not read
    from a contextvar inside the inner graph's execution.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    from reporting.services.chat_graph import _child_detail_event_accumulator, _current_tool_detail_id

    class _FakeToolCaller(BaseChatModel):
        """Calls ``run_python`` once, then returns a final answer.

        Stateless: it decides which turn it is by whether the message history
        already contains a tool result, so it is safe to reuse across calls.
        """

        @property
        def _llm_type(self) -> str:
            return "fake-tool-caller"

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

        def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
            if any(isinstance(m, ToolMessage) for m in messages):
                msg = AIMessage(content="all done")
            else:
                msg = AIMessage(
                    content="",
                    tool_calls=[{"name": "run_python", "args": {"code": "print(1)"}, "id": "c1"}],
                )
            return ChatResult(generations=[ChatGeneration(message=msg)])

        async def _agenerate(
            self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
        ) -> ChatResult:
            return self._generate(messages, stop, run_manager, **kwargs)

    fake_backend = _make_fake_backend()
    accumulator: dict[str, list[dict[str, Any]]] = {}
    _child_detail_event_accumulator.set(accumulator)
    _current_tool_detail_id.set("outer-sandbox-1")
    try:
        with (
            patch("reporting.settings.SANDBOX_ENABLED", True),
            patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
            patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
            patch("reporting.settings.SANDBOX_DOMAIN", ""),
            patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
            patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
            patch(
                "reporting.services.mcp_builtins.sandbox.open_backend",
                new=_open_backend_ctx(fake_backend),
            ),
            patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=_FakeToolCaller()),
        ):
            # current_user=None keeps the inner agent's toolset to just the sandbox
            # execution tools, so the only tool call is run_python.
            result = await _handle_delegate({"task": "run some code"}, None)
    finally:
        _child_detail_event_accumulator.set(None)
        _current_tool_detail_id.set(None)

    assert result.get("result") == "all done"
    # The inner run_python call landed as a nested child under the outer tool
    # call's detail id, so the outer node persists it inside the subagent entry
    # and the frontend nests it after a reload.
    children = accumulator.get("outer-sandbox-1")
    assert children is not None
    assert len(children) == 1
    assert children[0]["title"] == "Sandbox: run_python"
    assert children[0]["status"] == "completed"
    fake_backend.run_python.assert_awaited_once()


# ---------------------------------------------------------------------------
# Seizu tool injection — sandbox inner agent gets all permitted tools
# ---------------------------------------------------------------------------


def _disclosure(enabled: bool) -> Any:
    """Progressive disclosure decides how the sub-agent reaches unbound tools.

    Off, it searches tools directly; on, only through a skill that declares
    them. Every discovery assertion below has to say which mode it means.
    """
    return patch("reporting.settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE", enabled)


def _skill(name: str, description: str, tools_required: list[str]) -> Prompt:
    return Prompt(
        name=name,
        title=name,
        description=description,
        _meta={mcp_runtime.SKILL_TOOLS_META_KEY: tools_required},
        arguments=[],
    )


async def test_build_seizu_tools_binds_the_core_and_offers_the_rest_by_search() -> None:
    """The inner agent used to be handed every chat-safe tool in the deployment.

    Measured at 58 tools and ~3,800 tokens of schema re-sent on every inner LLM
    call -- about a fifth of a delegation's spend -- to describe a catalogue a
    delegation typically uses one or two of. It now gets the read-only graph
    core plus whatever the conversation disclosed, and reaches the rest through
    find_seizu_tools/call_seizu_tool. RBAC is unchanged either way.
    """
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object", "properties": {}}),
    ]
    with (
        _disclosure(False),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
    ):
        tools = await _build_seizu_tools(_current_user())

    assert {t.name for t in tools} == {"graph__query", "find_seizu_tools", "call_seizu_tool"}


async def test_build_seizu_tools_binds_what_the_conversation_disclosed() -> None:
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object", "properties": {}}),
        Tool(name="roles__list", description="Roles", inputSchema={"type": "object", "properties": {}}),
    ]
    with (
        _disclosure(False),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
    ):
        tools = await _build_seizu_tools(_current_user(), disclosed=frozenset({"reports__get"}))

    assert {t.name for t in tools} == {"graph__query", "reports__get", "find_seizu_tools", "call_seizu_tool"}


async def test_naming_tools_on_the_delegation_narrows_as_well_as_widens() -> None:
    """The delegating model knows the task, so naming the tools directs the
    sub-agent instead of leaving it to work out which one to use -- and keeps
    the disclosed set it does not need out of its context."""
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object", "properties": {}}),
        Tool(name="cve_analysis__get_cve", description="CVE", inputSchema={"type": "object", "properties": {}}),
    ]
    with patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)):
        tools = await _build_seizu_tools(
            _current_user(),
            disclosed=frozenset({"reports__get"}),
            requested=["cve_analysis__get_cve"],
        )

    names = {t.name for t in tools}
    assert "cve_analysis__get_cve" in names
    assert "graph__query" in names  # the core is always there
    assert "reports__get" not in names  # disclosed, but not what this task needs


async def test_a_requested_tool_that_does_not_exist_is_ignored_not_fatal() -> None:
    """A delegating model that guesses a name should not break the delegation;
    the RBAC-filtered listing stays the authority on what exists."""
    fake_tools = [Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}})]
    with patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)):
        tools = await _build_seizu_tools(_current_user(), requested=["no_such__tool"])

    assert "graph__query" in {t.name for t in tools}


async def test_an_unbound_tool_is_findable_and_callable() -> None:
    """Narrowing must cost a round trip, not a capability."""
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(
            name="cve_analysis__get_recent_cves",
            description="Recent CVEs by severity",
            inputSchema={"type": "object", "properties": {"limit": {"type": "integer"}}, "required": []},
        ),
    ]
    with (
        _disclosure(False),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
        patch("reporting.services.mcp_runtime.call_tool_for_chat", AsyncMock(return_value=_outcome('{"rows": 1}'))),
    ):
        tools = await _build_seizu_tools(_current_user())
        by_name = {t.name: t for t in tools}

        found = await by_name["find_seizu_tools"].coroutine(query="recent cves")
        assert "cve_analysis__get_recent_cves" in found

        called = await by_name["call_seizu_tool"].coroutine(
            name="cve_analysis__get_recent_cves", arguments_json='{"limit": 5}'
        )
        assert called == '{"rows": 1}'

        # A bound tool is not reachable this way; it is already in the list.
        assert "unknown tool" in await by_name["call_seizu_tool"].coroutine(name="graph__query")
        assert "not valid JSON" in await by_name["call_seizu_tool"].coroutine(
            name="cve_analysis__get_recent_cves", arguments_json="{oops"
        )


async def test_under_progressive_disclosure_the_subagent_searches_skills_not_tools() -> None:
    """Free-text tool search reaches anything RBAC permits by string match.

    That is the disclosure model the setting exists to switch off, so with it on
    the sub-agent gets the same skill-mediated route the planner has.
    """
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="cve_analysis__get_recent_cves", description="CVEs", inputSchema={"type": "object"}),
    ]
    skills = [_skill("cve__triage", "Triage recent CVEs", ["cve_analysis__get_recent_cves"])]
    with (
        _disclosure(True),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
    ):
        tools = await _build_seizu_tools(_current_user(), skills_available=skills)

    names = {t.name for t in tools}
    assert names == {"graph__query", "find_seizu_skills", "load_seizu_skill", "call_seizu_tool"}
    assert "find_seizu_tools" not in names


async def test_a_skill_unlocks_the_tools_its_author_declared() -> None:
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="cve_analysis__get_recent_cves", description="Recent CVEs", inputSchema={"type": "object"}),
    ]
    skills = [_skill("cve__triage", "Triage recent CVEs", ["cve_analysis__get_recent_cves"])]
    rendered = mcp_runtime.ChatActionOutcome(
        text="Step 1: list the CVEs.", blocked=None, tools_required=("cve_analysis__get_recent_cves",)
    )
    with (
        _disclosure(True),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
        patch("reporting.services.mcp_runtime.render_prompt_for_chat", AsyncMock(return_value=rendered)),
        patch("reporting.services.mcp_runtime.call_tool_for_chat", AsyncMock(return_value=_outcome('{"rows": 1}'))),
    ):
        by_name = {t.name: t for t in await _build_seizu_tools(_current_user(), skills_available=skills)}

        # Locked until the skill that uses it is loaded.
        before = await by_name["call_seizu_tool"].coroutine(name="cve_analysis__get_recent_cves")
        assert "no skill you have loaded uses it" in before

        found = await by_name["find_seizu_skills"].coroutine(query="cve triage")
        assert "cve__triage" in found

        loaded = await by_name["load_seizu_skill"].coroutine(name="cve__triage")
        assert "Step 1: list the CVEs." in loaded
        assert "cve_analysis__get_recent_cves" in loaded

        after = await by_name["call_seizu_tool"].coroutine(
            name="cve_analysis__get_recent_cves", arguments_json='{"limit": 5}'
        )
        assert after == '{"rows": 1}'


async def test_a_skill_that_declares_no_tools_says_so() -> None:
    """Otherwise the sub-agent discovers the gap one failed call at a time."""
    fake_tools = [Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}})]
    fake_tools.append(Tool(name="reports__get", description="Get report", inputSchema={"type": "object"}))
    skills = [_skill("cve__severity", "Severity breakdown", [])]
    rendered = mcp_runtime.ChatActionOutcome(text="Compute the distribution.", blocked=None, tools_required=())
    with (
        _disclosure(True),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
        patch("reporting.services.mcp_runtime.render_prompt_for_chat", AsyncMock(return_value=rendered)),
    ):
        by_name = {t.name: t for t in await _build_seizu_tools(_current_user(), skills_available=skills)}
        loaded = await by_name["load_seizu_skill"].coroutine(name="cve__severity")

    assert "Compute the distribution." in loaded
    assert "unlocked no additional tools" in loaded


async def test_a_skill_declaration_cannot_widen_rbac() -> None:
    """tools_required is a disclosure request; the RBAC-filtered listing answers it."""
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object"}),
    ]
    skills = [_skill("admin__audit", "Audit roles", ["roles__delete"])]
    rendered = mcp_runtime.ChatActionOutcome(text="Audit.", blocked=None, tools_required=("roles__delete",))
    with (
        _disclosure(True),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
        patch("reporting.services.mcp_runtime.render_prompt_for_chat", AsyncMock(return_value=rendered)),
    ):
        by_name = {t.name: t for t in await _build_seizu_tools(_current_user(), skills_available=skills)}
        await by_name["load_seizu_skill"].coroutine(name="admin__audit")
        blocked = await by_name["call_seizu_tool"].coroutine(name="roles__delete")

    # Not reachable for this user, so the declaration unlocked nothing.
    assert "unknown tool" in blocked


async def test_with_no_skills_the_subagent_gets_no_discovery_tools_at_all() -> None:
    """A deployment that has authored no skills gets the bound set and nothing else.

    Two discovery tools that can never find anything are pure schema cost, and
    the delegating model naming tools stays the route in that case.
    """
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object"}),
    ]
    with (
        _disclosure(True),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)),
    ):
        tools = await _build_seizu_tools(_current_user(), skills_available=[])

    assert {t.name for t in tools} == {"graph__query"}


async def test_the_subagent_is_told_the_discovery_route_it_actually_has() -> None:
    """A prompt naming find_seizu_tools when only skill discovery is bound sends
    the sub-agent after a tool that is not there."""
    assert "find_seizu_tools" in _subagent_prompt({"graph__query", "find_seizu_tools", "call_seizu_tool"})
    skills_prompt = _subagent_prompt({"graph__query", "find_seizu_skills", "load_seizu_skill", "call_seizu_tool"})
    assert "find_seizu_skills" in skills_prompt
    assert "find_seizu_tools" not in skills_prompt
    bare = _subagent_prompt({"graph__query"})
    assert "find_seizu_tools" not in bare
    assert "find_seizu_skills" not in bare
    assert "graph__query with Cypher" in bare


async def test_build_seizu_tools_coerces_integer_params() -> None:
    """Integer params from the MCP schema must be typed int so Pydantic coerces
    the LLM's string output ("10") to int before the value reaches Neo4j."""
    int_tool = Tool(
        name="my_toolset__search",
        description="Search",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "max rows"},
                "query": {"type": "string", "description": "search text"},
            },
            "required": ["query"],
        },
    )
    with patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=[int_tool])):
        tools = await _build_seizu_tools(_current_user(), requested=["my_toolset__search"])

    schema_cls = next(t for t in tools if t.name == "my_toolset__search").args_schema
    assert schema_cls is not None
    # Pydantic should coerce the LLM's string "10" to int 10.
    instance = schema_cls(query="test", limit="10")
    assert instance.limit == 10
    assert isinstance(instance.limit, int)


async def test_build_seizu_tools_excludes_sandbox_delegate() -> None:
    # sandbox__delegate must never be passed to the inner agent (prevent recursion).
    all_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(
            name="sandbox__delegate",
            description="Delegate to sandbox",
            inputSchema={"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
        ),
    ]
    with patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=all_tools)):
        tools = await _build_seizu_tools(_current_user())

    assert len(tools) == 1
    assert tools[0].name == "graph__query"


async def test_build_seizu_tools_requests_confirmation_gated_excluded() -> None:
    """The inner agent must never be handed confirmation-gated mutating tools, so
    the listing is requested with exclude_confirmation_gated=True."""
    list_mock = AsyncMock(return_value=[])
    with patch("reporting.services.mcp_runtime.list_tools_for_user", list_mock):
        await _build_seizu_tools(_current_user())

    assert list_mock.await_args is not None
    assert list_mock.await_args.kwargs["exclude_confirmation_gated"] is True
    assert list_mock.await_args.kwargs["chat_safe_only"] is True


async def test_build_seizu_tools_bounds_tool_results() -> None:
    """Seizu tools handed to the subagent bound their results (rows/bytes) and
    byte-cap the text, so a huge graph__query result can't blow up the inner model."""
    seizu_tool = Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}})
    big_outcome = MagicMock(blocked=None, text="y" * 10_000)
    call_mock = AsyncMock(return_value=big_outcome)
    with (
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=[seizu_tool])),
        patch("reporting.services.mcp_runtime.call_tool_for_chat", call_mock),
        patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 50),
        patch("reporting.settings.CHAT_TOOL_RESULT_MAX_BYTES", 4_000),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 100),
    ):
        tools = await _build_seizu_tools(_current_user())
        out = await tools[0].coroutine()  # type: ignore[misc]

    # Bounds were passed through to the underlying chat tool call...
    assert call_mock.await_args.kwargs["result_max_rows"] == 50
    assert call_mock.await_args.kwargs["result_max_bytes"] == 4_000
    # ...and the text was byte-capped as a final guard.
    assert len(out.encode()) <= 100 + len("\n[truncated]")
    assert out.endswith("[truncated]")


async def test_build_sandbox_tools_caps_result_bytes() -> None:
    """Every inner tool result is byte-capped before reaching the inner agent, not
    just the final answer — a large read can't blow up the inner model's context."""
    backend = _make_fake_backend()
    backend.run_python = AsyncMock(return_value="x" * 10_000)
    # The cap is read at call time, so the patch must wrap the await, not just build.
    with patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 100):
        tools = _build_sandbox_tools(backend)
        run_python = next(t for t in tools if t.name == "run_python")
        out = await run_python.coroutine(code="print('x' * 10000)")  # type: ignore[misc]

    assert len(out.encode()) <= 100 + len("\n[truncated]")
    assert out.endswith("[truncated]")


async def test_handler_injects_seizu_tools_into_inner_agent() -> None:
    # The inner create_react_agent receives both sandbox execution tools and
    # the full set of permitted Seizu tools.
    fake_backend = _make_fake_backend()
    fake_result = _make_fake_agent_result("result")
    captured_tools: list[Any] = []

    def fake_create_react_agent(*, model: Any, tools: list[Any], prompt: Any = None) -> Any:
        captured_tools.extend(tools)
        return MagicMock(ainvoke=AsyncMock(return_value=fake_result))

    seizu_tool = Tool(
        name="graph__query",
        description="Run a Cypher query",
        inputSchema={"type": "object", "properties": {"cypher": {"type": "string"}}, "required": ["cypher"]},
    )
    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch("reporting.services.mcp_builtins.sandbox.open_backend", new=_open_backend_ctx(fake_backend)),
        patch("reporting.services.mcp_builtins.sandbox.create_react_agent", side_effect=fake_create_react_agent),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=[seizu_tool])),
    ):
        await _handle_delegate({"task": "analyze graph"}, _current_user())

    tool_names = {t.name for t in captured_tools}
    assert "run_python" in tool_names
    assert "graph__query" in tool_names


# ---------------------------------------------------------------------------
# _wrap_with_detail_events — surfaces inner-agent tool calls in the detail stream
# ---------------------------------------------------------------------------


async def test_wrap_with_detail_events_emits_subagent_section_per_call() -> None:
    """Each inner call (re)emits the one subagent section under the outer detail
    id, with the child row transitioning running -> completed."""
    events: list[Any] = []
    children: list[dict[str, Any]] = []

    async def echo(message: str) -> str:
        return f"echoed: {message}"

    original = StructuredTool.from_function(coroutine=echo, name="echo", description="echo")
    (wrapped,) = _wrap_with_detail_events([original], events.append, parent_id="outer-1", children=children)

    result = await wrapped.coroutine(message="hi")  # type: ignore[misc]
    assert result == "echoed: hi"

    # Two frames — one when the child starts, one when it completes — both
    # addressing the single subagent section by the outer detail id.
    assert len(events) == 2
    for event in events:
        assert event["id"] == "outer-1"
        assert event["data"]["kind"] == "subagent"
        assert event["data"]["detail_id"] == "outer-1"
        assert len(event["data"]["children"]) == 1
    assert events[0]["data"]["children"][0]["status"] == "running"
    completed_child = events[1]["data"]["children"][0]
    assert completed_child["status"] == "completed"
    assert completed_child["title"] == "Sandbox: echo"
    assert "hi" in completed_child["body"]
    assert "message: hi" in completed_child["arguments"]

    # The shared children list holds the final completed row for the outer node
    # to persist into the subagent entry.
    assert len(children) == 1
    assert children[0]["status"] == "completed"


async def test_wrap_with_detail_events_snapshots_children_per_frame() -> None:
    """Each emitted frame snapshots children, so the in-place completed mutation
    cannot retroactively rewrite an already-emitted running frame."""
    events: list[Any] = []
    children: list[dict[str, Any]] = []

    async def echo(x: str) -> str:
        return x

    original = StructuredTool.from_function(coroutine=echo, name="echo", description="echo")
    (wrapped,) = _wrap_with_detail_events([original], events.append, parent_id="p", children=children)
    await wrapped.coroutine(x="one")  # type: ignore[misc]

    assert events[0]["data"]["children"][0]["status"] == "running"


async def test_wrap_with_detail_events_emits_error_child() -> None:
    """A raising tool marks its child row 'error', re-emits the section, re-raises."""
    events: list[Any] = []
    children: list[dict[str, Any]] = []

    async def boom() -> str:
        raise ValueError("exploded")

    original = StructuredTool.from_function(coroutine=boom, name="boom", description="boom")
    (wrapped,) = _wrap_with_detail_events([original], events.append, parent_id="p", children=children)

    with pytest.raises(ValueError, match="exploded"):
        await wrapped.coroutine()  # type: ignore[misc]

    assert events[-1]["data"]["children"][0]["status"] == "error"
    assert "exploded" in events[-1]["data"]["children"][0]["body"]
    assert children[0]["status"] == "error"


async def test_wrap_with_detail_events_no_parent_id_is_silent() -> None:
    """Without a parent_id (e.g. outside a streaming context) nothing is emitted."""
    events: list[Any] = []

    async def noop() -> str:
        return "ok"

    original = StructuredTool.from_function(coroutine=noop, name="noop", description="noop")
    (wrapped,) = _wrap_with_detail_events([original], events.append)
    result = await wrapped.coroutine()  # type: ignore[misc]
    assert result == "ok"
    assert events == []


async def test_wrap_with_detail_events_preserves_tool_without_coroutine() -> None:
    """Tools with no coroutine (sync-only) are passed through unchanged."""
    events: list[Any] = []

    def sync_fn(x: int) -> int:
        return x + 1

    original = StructuredTool.from_function(func=sync_fn, name="sync", description="sync")
    (passthrough,) = _wrap_with_detail_events([original], events.append)
    assert passthrough is original
    assert events == []


# ---------------------------------------------------------------------------
# _ToolMessageNormalizingModel — works around LiteLLM DeepSeek transformer bug
# ---------------------------------------------------------------------------


async def test_normalizing_model_converts_string_tool_message_content() -> None:
    """String ToolMessage.content is rewritten to [{"type":"text","text":...}] before the inner model sees it."""
    from langchain_core.messages import HumanMessage, ToolMessage

    captured: list[Any] = []

    async def fake_ainvoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        captured.extend(input if isinstance(input, list) else [])
        return MagicMock(content="response")

    inner = MagicMock()
    inner.ainvoke = fake_ainvoke
    model = _ToolMessageNormalizingModel(inner)

    await model.ainvoke([HumanMessage(content="hello"), ToolMessage(content="tool result", tool_call_id="tc1")])

    tool_msgs = [m for m in captured if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == [{"type": "text", "text": "tool result"}]


async def test_normalizing_model_passthrough_list_tool_message_content() -> None:
    """ToolMessage.content already in list form is not double-wrapped."""
    from langchain_core.messages import ToolMessage

    captured: list[Any] = []

    async def fake_ainvoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        captured.extend(input if isinstance(input, list) else [])
        return MagicMock(content="response")

    inner = MagicMock()
    inner.ainvoke = fake_ainvoke
    model = _ToolMessageNormalizingModel(inner)

    original_content: list[Any] = [{"type": "text", "text": "already a block"}]
    await model.ainvoke([ToolMessage(content=original_content, tool_call_id="tc1")])

    tool_msgs = [m for m in captured if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content == original_content


async def test_normalizing_model_converts_list_of_strings_in_any_message() -> None:
    """Any message with list content containing plain strings has strings wrapped in text blocks."""
    from langchain_core.messages import AIMessage

    captured: list[Any] = []

    async def fake_ainvoke(input: Any, config: Any = None, **kwargs: Any) -> Any:
        captured.extend(input if isinstance(input, list) else [])
        return MagicMock(content="response")

    inner = MagicMock()
    inner.ainvoke = fake_ainvoke
    model = _ToolMessageNormalizingModel(inner)

    await model.ainvoke([AIMessage(content=["thinking text", "response text"])])  # type: ignore[arg-type]

    ai_msgs = [m for m in captured if isinstance(m, AIMessage)]
    assert len(ai_msgs) == 1
    assert ai_msgs[0].content == [
        {"type": "text", "text": "thinking text"},
        {"type": "text", "text": "response text"},
    ]


def test_normalizing_model_bind_tools_returns_wrapped_model() -> None:
    """bind_tools returns another _ToolMessageNormalizingModel so normalization survives create_react_agent's bind."""
    inner = MagicMock()
    inner.bind_tools = MagicMock(return_value=MagicMock())
    model = _ToolMessageNormalizingModel(inner)

    bound = model.bind_tools([])
    assert isinstance(bound, _ToolMessageNormalizingModel)


def test_get_sandbox_model_returns_normalizing_model() -> None:
    """_get_sandbox_model always wraps the base model in _ToolMessageNormalizingModel."""
    with (
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch("reporting.services.chat_graph.get_chat_model", return_value=MagicMock()),
    ):
        model = _get_sandbox_model()

    assert isinstance(model, _ToolMessageNormalizingModel)


async def test_e2b_run_bash_streaming_passes_per_command_envs() -> None:
    """Per-command envs go to commands.run for that one command only — this is
    the primitive the remediation service's credential phase-isolation rests on."""
    chunks: list[str] = []
    sdk = MagicMock()

    async def _run(cmd: str, **kwargs: Any) -> Any:
        assert kwargs["envs"] == {"GH_TOKEN": "tok"}
        kwargs["on_stdout"]("line\n")
        return MagicMock()

    sdk.commands.run = AsyncMock(side_effect=_run)
    out = await _E2BSandboxBackend(sdk).run_bash_streaming(
        "echo line", timeout_seconds=5, on_output=chunks.append, envs={"GH_TOKEN": "tok"}
    )
    assert out == "line\n"
    assert chunks == ["line\n"]


def _capture_open_backend(fake_backend: MagicMock, captured: dict[str, Any]) -> Any:
    """Return a patched _open_backend that yields fake_backend and records kwargs."""

    @asynccontextmanager
    async def _ctx(**kwargs: Any):  # type: ignore[misc]
        captured.update(kwargs)
        yield fake_backend

    return _ctx


def _patch_async_sandbox(
    created: dict[str, Any],
    *,
    connected: dict[str, Any] | None = None,
    killed: list[Any] | None = None,
    paused: list[Any] | None = None,
    connect_error: Exception | None = None,
) -> Any:
    """Patch the lazily-imported e2b AsyncSandbox and record what it was asked to do."""
    connected = {} if connected is None else connected
    killed = [] if killed is None else killed
    paused = [] if paused is None else paused

    class _FakeSandbox:
        sandbox_id = "sandbox-fake"

        async def __aenter__(self) -> "_FakeSandbox":
            return self

        async def __aexit__(self, *_a: Any) -> bool:
            return False

        async def kill(self) -> bool:
            killed.append(self)
            return True

        async def pause(self, keep_memory: bool = True) -> bool:
            paused.append({"sandbox": self, "keep_memory": keep_memory})
            return True

    async def _create(**kwargs: Any) -> "_FakeSandbox":
        created.update(kwargs)
        return _FakeSandbox()

    async def _connect(sandbox_id: str, **kwargs: Any) -> "_FakeSandbox":
        connected.update({"sandbox_id": sandbox_id, **kwargs})
        if connect_error is not None:
            raise connect_error
        resumed = _FakeSandbox()
        resumed.sandbox_id = sandbox_id
        return resumed

    module = MagicMock()
    module.AsyncSandbox.create = AsyncMock(side_effect=_create)
    module.AsyncSandbox.connect = AsyncMock(side_effect=_connect)
    return patch.dict("sys.modules", {"e2b_code_interpreter": module})


async def test_open_backend_passes_template_on_e2b_cloud() -> None:
    """On E2B cloud (no domain), the template reaches AsyncSandbox.create."""
    created: dict[str, Any] = {}
    with _patch_async_sandbox(created), patch("reporting.settings.SANDBOX_ALLOW_INTERNET", False):
        async with open_backend(api_key="k", domain="", template="claude"):
            pass
    assert created["template"] == "claude"


async def test_open_backend_ignores_template_when_self_hosted() -> None:
    """Templates are an E2B-cloud feature; a self-hosted domain must not get one,
    so the base-image install path still works on OpenKruise Agents."""
    created: dict[str, Any] = {}
    with _patch_async_sandbox(created), patch("reporting.settings.SANDBOX_ALLOW_INTERNET", False):
        async with open_backend(api_key="k", domain="sandbox.internal", template="claude"):
            pass
    assert "template" not in created
    assert created["domain"] == "sandbox.internal"


async def test_open_backend_destroys_the_sandbox_by_default() -> None:
    """The old `async with sandbox` did this; nothing may quietly stop doing it."""
    killed: list[Any] = []
    paused: list[Any] = []
    with _patch_async_sandbox({}, killed=killed, paused=paused):
        async with open_backend(api_key="k", domain=""):
            pass
    assert len(killed) == 1
    assert paused == []


async def test_open_backend_suspends_instead_of_killing_when_asked() -> None:
    """Suspension is what lets the next turn find this turn's data still there."""
    killed: list[Any] = []
    paused: list[Any] = []
    with _patch_async_sandbox({}, killed=killed, paused=paused):
        async with open_backend(api_key="k", domain="", suspend_on_exit=True):
            pass
    assert len(paused) == 1
    assert killed == []


async def test_open_backend_decides_suspension_at_exit_when_given_a_callable() -> None:
    """A turn only knows on the way out whether its sandbox is worth keeping."""
    keep = True
    killed: list[Any] = []
    paused: list[Any] = []
    with _patch_async_sandbox({}, killed=killed, paused=paused):
        async with open_backend(api_key="k", domain="", suspend_on_exit=lambda: keep):
            keep = False
    assert killed and not paused


async def test_open_backend_resumes_an_existing_sandbox() -> None:
    connected: dict[str, Any] = {}
    created: dict[str, Any] = {}
    with _patch_async_sandbox(created, connected=connected):
        async with open_backend(api_key="k", domain="", timeout_seconds=1_800, resume_sandbox_id="sbx-1") as backend:
            # Same id back: the caller compares these to decide whether files it
            # remembers writing are still there.
            assert backend.sandbox_id == "sbx-1"
    assert connected["sandbox_id"] == "sbx-1"
    assert connected["timeout"] == 1_800
    assert created == {}  # nothing new was made


async def test_a_sandbox_that_cannot_be_resumed_is_replaced_not_raised() -> None:
    """Expiry and reaping are ordinary. A fresh sandbox with a different id is
    the recovery, and the differing id is how callers learn the files are gone."""
    from e2b import SandboxNotFoundException

    created: dict[str, Any] = {}
    with _patch_async_sandbox(created, connect_error=SandboxNotFoundException("gone")):
        async with open_backend(api_key="k", domain="", resume_sandbox_id="sbx-dead") as backend:
            assert backend.sandbox_id != "sbx-dead"
    assert created  # a replacement was created


async def test_a_transient_resume_failure_is_not_treated_as_a_dead_sandbox() -> None:
    """Replacing on a timeout or rate limit leaves the old sandbox paused and
    alive while its id is overwritten by the replacement's -- alive, paid for,
    and permanently unreachable. Only "it is gone" may replace."""
    from e2b import RateLimitException

    created: dict[str, Any] = {}
    with _patch_async_sandbox(created, connect_error=RateLimitException("slow down")):
        with pytest.raises(RateLimitException):
            async with open_backend(api_key="k", domain="", resume_sandbox_id="sbx-live"):
                pass
    assert not created  # nothing was created to overwrite it with


async def test_suspending_keeps_only_the_filesystem() -> None:
    """A memory snapshot would carry untrusted processes across turns and pay
    provider storage for them; only the disk carries data between turns."""
    paused: list[Any] = []
    with _patch_async_sandbox({}, paused=paused):
        async with open_backend(api_key="k", domain="", suspend_on_exit=True):
            pass
    assert paused and paused[0]["keep_memory"] is False


async def test_teardown_reports_whether_the_sandbox_was_really_suspended() -> None:
    """A pause can fail and fall back to a kill, and the caller storing the id
    has to know the difference."""
    outcomes: list[bool] = []

    class _UnpausableSandbox:
        sandbox_id = "sbx-1"
        killed = False

        async def pause(self, keep_memory: bool = True) -> bool:
            raise RuntimeError("provider refused")

        async def kill(self) -> bool:
            type(self).killed = True
            return True

    module = MagicMock()
    module.AsyncSandbox.create = AsyncMock(return_value=_UnpausableSandbox())
    with patch.dict("sys.modules", {"e2b_code_interpreter": module}):
        async with open_backend(api_key="k", domain="", suspend_on_exit=True, on_teardown=outcomes.append):
            pass

    assert outcomes == [False]
    assert _UnpausableSandbox.killed is True


async def test_open_backend_defaults_unchanged_for_delegate_path() -> None:
    """The plain delegate path must keep passing no allow_internet/timeout
    override, so the pre-existing security defaults still apply."""
    fake_backend = _make_fake_backend()
    captured: dict[str, Any] = {}
    fake_result = _make_fake_agent_result("ok")
    with (
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch(
            "reporting.services.mcp_builtins.sandbox.open_backend",
            new=_capture_open_backend(fake_backend, captured),
        ),
        patch(
            "reporting.services.mcp_builtins.sandbox.create_react_agent",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_result)),
        ),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        await _handle_delegate({"task": "compute"}, _current_user())

    assert "envs" not in captured
    assert "allow_internet" not in captured
    assert "timeout_seconds" not in captured


# --- Episodic carry between delegations ----------------------------------------


def _sandbox_patches(fake_backend: Any, ainvoke: Any) -> list[Any]:
    return [
        patch("reporting.settings.SANDBOX_ENABLED", True),
        patch("reporting.settings.CHAT_LLM_PROVIDER", "anthropic"),
        patch("reporting.settings.SANDBOX_API_KEY", "test-key"),
        patch("reporting.settings.SANDBOX_DOMAIN", ""),
        patch("reporting.settings.SANDBOX_TIMEOUT_SECONDS", 30),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
        patch("reporting.settings.SANDBOX_LLM_MODEL", ""),
        patch("reporting.services.mcp_builtins.sandbox.open_backend", new=_open_backend_ctx(fake_backend)),
        patch("reporting.services.mcp_builtins.sandbox.create_react_agent", return_value=MagicMock(ainvoke=ainvoke)),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ]


async def test_a_later_delegation_sees_what_an_earlier_one_found() -> None:
    """The carry that stops each fresh subagent re-deriving the same ground."""
    fake_backend = _make_fake_backend()
    prompts: list[str] = []
    outcomes = iter(["The graph has 412 CVE nodes.", "second done"])

    async def fake_ainvoke(inputs: dict[str, Any]) -> dict[str, Any]:
        prompts.append(inputs["messages"][0].content)
        return _make_fake_agent_result(next(outcomes))

    episodic_memory.start_episode_log()
    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, fake_ainvoke):
            stack.enter_context(item)
        await _handle_delegate({"task": "count the CVE nodes"}, _current_user())
        await _handle_delegate({"task": "list the critical ones"}, _current_user())

    assert "412 CVE nodes" not in prompts[0]  # nothing to carry yet
    assert "412 CVE nodes" in prompts[1]
    assert "count the CVE nodes" in prompts[1]
    # Framed as prior results so the subagent does not read them as its task.
    assert "not instructions" in prompts[1]


async def test_a_failed_delegation_is_not_recorded_as_covered_ground() -> None:
    fake_backend = _make_fake_backend()
    prompts: list[str] = []
    calls = {"n": 0}

    async def fake_ainvoke(inputs: dict[str, Any]) -> dict[str, Any]:
        prompts.append(inputs["messages"][0].content)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("sandbox exploded")
        return _make_fake_agent_result("second done")

    episodic_memory.start_episode_log()
    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, fake_ainvoke):
            stack.enter_context(item)
        failed = await _handle_delegate({"task": "count the CVE nodes"}, _current_user())
        await _handle_delegate({"task": "list the critical ones"}, _current_user())

    assert "error" in failed
    log = episodic_memory.current_episode_log()
    assert log is not None and len(log) == 1
    # Recording a failure would teach the next subagent the ground was covered.
    assert "count the CVE nodes" not in prompts[1]


async def test_delegation_works_with_no_ambient_log() -> None:
    fake_backend = _make_fake_backend()
    episodic_memory.clear_episode_log()
    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, AsyncMock(return_value=_make_fake_agent_result("done"))):
            stack.enter_context(item)
        result = await _handle_delegate({"task": "do a thing"}, _current_user())

    assert result["result"] == "done"


# --- Sandbox spend metering ----------------------------------------------------


class _MeteredModel:
    """Records usage the way a real provider does."""

    def __init__(self, *, input_tokens: int = 500, output_tokens: int = 100) -> None:
        self.calls = 0
        self.usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    def bind_tools(self, _tools: Any, **_kw: Any) -> "_MeteredModel":
        return self

    async def ainvoke(self, _input: Any, config: Any = None, **_kw: Any) -> Any:
        self.calls += 1
        return AIMessage(content="inner answer", usage_metadata=self.usage)


async def test_sandbox_subagent_spend_reaches_the_run_ledger() -> None:
    """Regression: inner calls used to bill nobody, so a delegation-heavy turn
    reported a comfortable ledger while making thousands of LLM calls."""
    controller = BudgetController(initial_budget_ledger())
    wrapped = _ToolMessageNormalizingModel(_MeteredModel(input_tokens=500, output_tokens=100))
    chat_budget.set_current_budget_controller(controller)
    try:
        await wrapped.ainvoke([HumanMessage(content="do a thing")])
        await wrapped.ainvoke([HumanMessage(content="do another")])
    finally:
        chat_budget.set_current_budget_controller(None)

    ledger = controller.snapshot()
    assert ledger["total_tokens"] == 2 * 600
    assert ledger["llm_calls"] == 2
    # Billed to its own phase so sandbox spend is legible next to the outer loop.
    assert ledger["phases"]["sandbox_subagent"]["total_tokens"] == 1200


async def test_sandbox_subagent_stops_when_the_run_budget_is_spent() -> None:
    ledger = initial_budget_ledger()
    ledger.update({"token_limit": 2_000, "reserve_tokens": 0, "max_llm_calls": 0})
    controller = BudgetController(ledger)
    model = _MeteredModel(input_tokens=800, output_tokens=200)
    wrapped = _ToolMessageNormalizingModel(model)

    chat_budget.set_current_budget_controller(controller)
    try:
        with pytest.raises(BudgetExceeded):
            for _ in range(20):
                await wrapped.ainvoke([HumanMessage(content="keep going")])
    finally:
        chat_budget.set_current_budget_controller(None)

    # It stopped rather than spending indefinitely.
    assert model.calls < 20


async def test_sandbox_metering_is_inert_without_a_controller() -> None:
    model = _MeteredModel()
    wrapped = _ToolMessageNormalizingModel(model)
    chat_budget.set_current_budget_controller(None)

    result = await wrapped.ainvoke([HumanMessage(content="no ledger here")])

    assert result.content == "inner answer"
    assert model.calls == 1


async def test_a_failed_inner_call_releases_its_reservation() -> None:
    class _Boom:
        def bind_tools(self, _tools: Any, **_kw: Any) -> "_Boom":
            return self

        async def ainvoke(self, _input: Any, config: Any = None, **_kw: Any) -> Any:
            raise RuntimeError("provider down")

    controller = BudgetController(initial_budget_ledger())
    wrapped = _ToolMessageNormalizingModel(_Boom())
    chat_budget.set_current_budget_controller(controller)
    try:
        with pytest.raises(RuntimeError):
            await wrapped.ainvoke([HumanMessage(content="x")])
    finally:
        chat_budget.set_current_budget_controller(None)

    # A leaked reservation would permanently shrink the run's headroom.
    assert controller.snapshot()["llm_calls"] == 0
    await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, phase="after")


# --- Oversized results routed to the sandbox filesystem ------------------------


def _rows_json(n: int) -> str:
    return json.dumps({"results": [{"cve": f"CVE-2026-{i:05d}", "score": 7.5} for i in range(n)]})


def _outcome(text: str) -> Any:
    return SimpleNamespace(blocked=None, text=text)


async def _seizu_tool(backend: Any, mocker: Any, *, result: str, name: str = "graph__query", purpose: str = "") -> Any:
    mocker.patch(
        "reporting.services.mcp_runtime.list_tools_for_user",
        new=AsyncMock(
            return_value=[
                Tool(
                    name=name,
                    description="Run a query",
                    inputSchema={
                        "type": "object",
                        "properties": {"query": {"type": "string", "description": "cypher"}},
                        "required": ["query"],
                    },
                )
            ]
        ),
    )
    mocker.patch(
        "reporting.services.mcp_runtime.call_tool_for_chat",
        new=AsyncMock(return_value=_outcome(result)),
    )
    tools = await _build_seizu_tools(_current_user(), backend, purpose=purpose)
    return tools[0]


async def test_a_result_over_the_row_cap_goes_to_a_file(mocker) -> None:
    """Rows must trigger it as well as bytes.

    The fetch deliberately exceeds both bounds so oversize can be detected, which
    leaves the row cap as the only thing keeping an inline result small in row
    terms. Triggering on bytes alone was measured at 880 inner calls and a
    581-character answer, against 124 and a complete one.
    """
    backend = _make_fake_backend()
    backend.write_file = AsyncMock(return_value="ok")
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    mocker.patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 5_000_000)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(5_000))

    returned = await tool.coroutine(query="q")

    backend.write_file.assert_awaited_once()
    receipt = json.loads(returned)
    assert receipt["status"] == "too_large_to_return"
    assert receipt["rows"] == 5_000


async def test_a_result_over_the_byte_cap_goes_to_a_file(mocker) -> None:
    backend = _make_fake_backend()
    backend.write_file = AsyncMock(return_value="ok")
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100_000)
    mocker.patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 1_000)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(200))

    returned = await tool.coroutine(query="q")

    backend.write_file.assert_awaited_once()
    assert json.loads(returned)["status"] == "too_large_to_return"


async def test_a_result_that_fits_is_returned_unchanged(mocker) -> None:
    backend = _make_fake_backend()
    backend.write_file = AsyncMock(return_value="ok")
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(3))

    returned = await tool.coroutine(query="q")

    # Where the data fits, nothing about the old behaviour changes.
    backend.write_file.assert_not_awaited()
    assert len(json.loads(returned)["results"]) == 3


async def test_the_agent_is_given_no_choice_about_the_file(mocker) -> None:
    """The correction of the first attempt: routing is the system's decision.

    Offered the choice, the model took it for every call, read none of the files
    back, and re-queried instead at several times the spend.
    """
    backend = _make_fake_backend()
    tool = await _seizu_tool(backend, mocker, result=_rows_json(3))

    properties = tool.args_schema.model_json_schema()["properties"]
    assert set(properties) == {"query"}


async def test_a_failed_write_falls_back_to_the_truncated_result(mocker) -> None:
    backend = _make_fake_backend()
    backend.write_file = AsyncMock(side_effect=RuntimeError("disk full"))
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(500))

    returned = await tool.coroutine(query="q")

    # Exactly what would have happened without this path, so a write failure
    # costs nothing beyond the rows that never fit anyway.
    assert "CVE-2026-00000" in returned
    assert "too_large_to_return" not in returned


async def test_without_a_backend_the_context_caps_still_apply(mocker) -> None:
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    mocker.patch("reporting.settings.SANDBOX_FILE_RESULT_MAX_ROWS", 50_000)
    tool = await _seizu_tool(None, mocker, result=_rows_json(3))
    call = mocker.patch(
        "reporting.services.mcp_runtime.call_tool_for_chat",
        new=AsyncMock(return_value=_outcome(_rows_json(3))),
    )

    await tool.coroutine(query="q")

    # Nowhere to put an oversized result, so do not fetch one.
    assert call.await_args.kwargs["result_max_rows"] == 100


async def test_with_a_backend_the_fetch_is_raised_to_the_file_bounds(mocker) -> None:
    backend = _make_fake_backend()
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    mocker.patch("reporting.settings.SANDBOX_FILE_RESULT_MAX_ROWS", 50_000)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(3))
    call = mocker.patch(
        "reporting.services.mcp_runtime.call_tool_for_chat",
        new=AsyncMock(return_value=_outcome(_rows_json(3))),
    )

    await tool.coroutine(query="q")

    # Whether a result is oversized cannot be known before fetching it.
    assert call.await_args.kwargs["result_max_rows"] == 50_000


# --- read_file honesty ---------------------------------------------------------


async def test_read_file_returns_a_file_that_fits_verbatim() -> None:
    """read_file is no longer the default but stays available at preview 0."""
    backend = _make_fake_backend()
    backend.read_file = AsyncMock(return_value="small contents")
    with (
        patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 0),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 50_000),
    ):
        tools = {t.name: t for t in _build_sandbox_tools(backend)}
        assert await tools["read_file"].coroutine(path="/tmp/x") == "small contents"


async def test_read_file_says_so_when_it_cannot_return_the_whole_file() -> None:
    """Regression: a bare [truncated] marker let an agent believe it had the lot.

    The same silent-truncation shape that made a cut-off model answer look
    complete -- here it would have been a tenth of a result file.
    """
    backend = _make_fake_backend()
    backend.read_file = AsyncMock(return_value="x" * 500_000)
    with (
        patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 0),
        patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 1_000),
    ):
        tools = {t.name: t for t in _build_sandbox_tools(backend)}
        returned = await tools["read_file"].coroutine(path="/tmp/big.json")

    assert "500000 bytes" in returned
    assert "NOT the whole file" in returned
    assert "run_python" in returned


# --- Sub-agent guidance --------------------------------------------------------


async def test_the_subagent_is_told_how_the_data_is_shaped() -> None:
    """It had no system prompt at all, so it re-derived all of this every run."""
    backend = _make_fake_backend()
    captured: dict[str, Any] = {}

    def fake_agent(*, model: Any, tools: list[Any], prompt: Any = None) -> Any:
        captured["prompt"] = prompt
        return MagicMock(ainvoke=AsyncMock(return_value=_make_fake_agent_result("done")))

    with ExitStack() as stack:
        for item in _sandbox_patches(backend, AsyncMock(return_value=_make_fake_agent_result("done"))):
            stack.enter_context(item)
        stack.enter_context(patch("reporting.services.mcp_builtins.sandbox.create_react_agent", side_effect=fake_agent))
        await _handle_delegate({"task": "count them"}, _current_user())

    prompt = captured["prompt"]
    assert "too_large_to_return" in prompt  # what an oversized result looks like
    assert "json.load(open(path))" in prompt  # and how to use one
    assert "run_python" in prompt
    assert '"results"' in prompt  # the shape a query result arrives in


async def test_the_subagent_is_told_what_it_may_spend() -> None:
    controller = BudgetController(initial_budget_ledger())
    controller.open_scope("worker:s1", 100_000)
    chat_budget.set_current_budget_scope("worker:s1")
    chat_budget.set_current_budget_controller(controller)
    captured: dict[str, Any] = {}

    def fake_agent(*, model: Any, tools: list[Any], prompt: Any = None) -> Any:
        captured["prompt"] = prompt
        return MagicMock(ainvoke=AsyncMock(return_value=_make_fake_agent_result("done")))

    try:
        with ExitStack() as stack:
            for item in _sandbox_patches(backend := _make_fake_backend(), AsyncMock()):
                stack.enter_context(item)
            stack.enter_context(
                patch("reporting.services.mcp_builtins.sandbox.create_react_agent", side_effect=fake_agent)
            )
            await _handle_delegate({"task": "count them"}, _current_user())
    finally:
        chat_budget.set_current_budget_scope("")
        chat_budget.set_current_budget_controller(None)

    assert "100000 tokens are available" in captured["prompt"]
    assert backend is not None


async def test_a_nearly_spent_scope_tells_the_subagent_to_finish() -> None:
    """A limit it cannot see is one it cannot plan against."""
    controller = BudgetController(initial_budget_ledger())
    # Past its fair share (the soft threshold) but well inside the hard bound.
    controller.open_scope("worker:s1", 10_000, soft_tokens=500)
    reservation = await controller.reserve(estimated_input_tokens=1, estimated_output_tokens=1, scope="worker:s1")
    await controller.commit(reservation, input_tokens=800, output_tokens=0, cost_usd=0.0, usage_estimated=False)
    assert controller.scope_soft_limit_reached("worker:s1")
    assert not controller.scope_exhausted("worker:s1")

    chat_budget.set_current_budget_scope("worker:s1")
    chat_budget.set_current_budget_controller(controller)
    captured: dict[str, Any] = {}

    def fake_agent(*, model: Any, tools: list[Any], prompt: Any = None) -> Any:
        captured["prompt"] = prompt
        return MagicMock(ainvoke=AsyncMock(return_value=_make_fake_agent_result("done")))

    try:
        with ExitStack() as stack:
            for item in _sandbox_patches(_make_fake_backend(), AsyncMock()):
                stack.enter_context(item)
            stack.enter_context(
                patch("reporting.services.mcp_builtins.sandbox.create_react_agent", side_effect=fake_agent)
            )
            await _handle_delegate({"task": "count them"}, _current_user())
    finally:
        chat_budget.set_current_budget_scope("")
        chat_budget.set_current_budget_controller(None)

    assert "return your findings now" in captured["prompt"]
    assert "loses everything you have not yet reported" in captured["prompt"]


async def test_delegations_in_a_step_reuse_one_sandbox(mocker) -> None:
    """Files written by one delegation survive to the next, which is the point."""
    shared = _make_fake_backend()
    opened: list[Any] = []

    class _Session:
        async def backend(self) -> Any:
            opened.append(shared)
            return shared

    mocker.patch(
        "reporting.services.mcp_builtins.sandbox.sandbox_session.current_sandbox_session",
        return_value=_Session(),
    )
    private = _make_fake_backend()
    with ExitStack() as stack:
        for item in _sandbox_patches(private, AsyncMock(return_value=_make_fake_agent_result("done"))):
            stack.enter_context(item)
        await _handle_delegate({"task": "one"}, _current_user())
        await _handle_delegate({"task": "two"}, _current_user())

    assert opened == [shared, shared]  # the session's sandbox, both times


async def test_a_delegation_without_a_session_still_opens_its_own(mocker) -> None:
    """The MCP path and anything outside a chat step keep working unchanged."""
    mocker.patch(
        "reporting.services.mcp_builtins.sandbox.sandbox_session.current_sandbox_session",
        return_value=None,
    )
    backend = _make_fake_backend()
    with ExitStack() as stack:
        for item in _sandbox_patches(backend, AsyncMock(return_value=_make_fake_agent_result("done"))):
            stack.enter_context(item)
        result = await _handle_delegate({"task": "standalone"}, _current_user())

    assert result["result"] == "done"


async def test_a_budget_stop_is_reported_as_such_not_as_a_crash(mocker) -> None:
    """It was caught by the generic handler and reported as "Sandbox task failed".

    Indistinguishable from a broken sandbox, so the caller could not do the one
    sensible thing -- stop and report what it has.
    """
    backend = _make_fake_backend()
    mocker.patch(
        "reporting.services.mcp_builtins.sandbox.sandbox_session.current_sandbox_session",
        return_value=None,
    )
    failing = AsyncMock(side_effect=BudgetExceeded("The run token budget is reserved for final synthesis."))

    with ExitStack() as stack:
        for item in _sandbox_patches(backend, failing):
            stack.enter_context(item)
        result = await _handle_delegate({"task": "keep going"}, _current_user())

    assert "Stopped:" in result["error"]
    assert "report what you have already gathered" in result["error"]
    assert "see server logs" not in result["error"]


async def test_preview_replaces_read_file_when_a_preview_budget_is_set() -> None:
    backend = _make_fake_backend()
    with patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 2_000):
        names = {t.name for t in _build_sandbox_tools(backend)}
    assert "preview_file" in names and "read_file" not in names

    with patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 0):
        names = {t.name for t in _build_sandbox_tools(backend)}
    assert "read_file" in names and "preview_file" not in names


async def test_preview_of_a_large_file_returns_shape_not_contents() -> None:
    backend = _make_fake_backend()
    backend.read_file = AsyncMock(return_value=_rows_json(5_000))
    with patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 500):
        tools = {t.name: t for t in _build_sandbox_tools(backend)}
        returned = await tools["preview_file"].coroutine(path="/tmp/big.json")

    summary = json.loads(returned.split("\n\n")[0])
    assert summary["rows"] == 5_000
    assert summary["columns"] == ["cve", "score"]
    assert "run_python" in summary["preview_only"]
    assert len(returned) < 2_000


async def test_preview_returns_a_small_file_whole() -> None:
    backend = _make_fake_backend()
    backend.read_file = AsyncMock(return_value="small contents")
    with patch("reporting.settings.SANDBOX_PREVIEW_MAX_BYTES", 2_000):
        tools = {t.name: t for t in _build_sandbox_tools(backend)}
        assert await tools["preview_file"].coroutine(path="/tmp/x") == "small contents"


async def test_a_saved_result_is_recorded_for_later_turns(mocker) -> None:
    """The receipt covers this delegation; the ledger entry is what survives it.

    The file is still in the sandbox next turn, and without a record of it that
    turn re-runs the query that produced it — which is the whole cost this path
    exists to avoid.
    """
    from reporting.services import episodic_memory

    ledger = episodic_memory.start_session_ledger(None)
    backend = _make_fake_backend()
    backend.sandbox_id = "sbx-1"
    backend.write_file = AsyncMock(return_value="ok")
    mocker.patch("reporting.settings.CHAT_TOOL_RESULT_MAX_ROWS", 100)
    mocker.patch("reporting.settings.SANDBOX_MAX_OUTPUT_BYTES", 5_000_000)
    tool = await _seizu_tool(backend, mocker, result=_rows_json(5_000), purpose="find every critical CVE")

    saved_to = json.loads(await tool.coroutine(query="q"))["saved_to"]

    receipt = ledger.receipts[0]
    assert receipt.path == saved_to
    assert receipt.source == "graph__query"
    assert receipt.purpose == "find every critical CVE"
    assert receipt.rows == 5_000
    # Bound to the sandbox it lives in: a later turn that could not resume this
    # sandbox must not be told the file is there.
    assert receipt.sandbox_id == "sbx-1"
    assert ledger.render_receipts("sbx-2", 4_000) == ""
    episodic_memory.clear_session_ledger()


async def test_a_result_small_enough_to_return_records_nothing(mocker) -> None:
    from reporting.services import episodic_memory

    ledger = episodic_memory.start_session_ledger(None)
    backend = _make_fake_backend()
    tool = await _seizu_tool(backend, mocker, result=_rows_json(2))

    await tool.coroutine(query="q")

    assert ledger.receipts == []
    episodic_memory.clear_session_ledger()


async def test_a_reasoning_sub_agents_answer_is_read_as_text_not_stringified() -> None:
    """A reasoning model returns content as blocks, not a string.

    Stringifying it returned the repr of the whole list -- every thinking
    fragment, quoted, with the actual answer at the end -- which went to the
    caller and into the recall every later sub-agent reads, at many times the
    tokens of the text it was meant to carry.
    """
    fake_backend = _make_fake_backend()
    blocks = [
        {"type": "thinking", "thinking": "weighing the options at some length"},
        {"type": "text", "text": "There are 412 CVE nodes."},
    ]

    async def fake_ainvoke(_inputs: dict[str, Any]) -> dict[str, Any]:
        message = MagicMock()
        message.content = blocks
        message.tool_calls = []
        return {"messages": [message]}

    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, fake_ainvoke):
            stack.enter_context(item)
        result = await _handle_delegate({"task": "count them"}, _current_user())

    assert result == {"result": "There are 412 CVE nodes."}


async def test_a_later_turn_is_told_about_files_earlier_turns_saved() -> None:
    """The cross-turn half of the carry: the file is still in the sandbox, and
    a sub-agent that knows it is there reads it instead of re-fetching."""
    from reporting.services import episodic_memory

    ledger = episodic_memory.start_session_ledger(
        {
            "turn": 1,
            "episodes": [{"task": "count CVEs", "outcome": "There are 412 CVE nodes.", "turn": 1}],
            "receipts": [
                {
                    "path": "/tmp/seizu_results/graph__query_001.json",
                    "source": "graph__query",
                    "purpose": "every critical CVE",
                    "sandbox_id": "sbx-1",
                    "turn": 1,
                    "rows": 412,
                    "columns": ["cve_id"],
                }
            ],
        }
    )
    assert ledger.turn == 2
    episodic_memory.start_episode_log()

    fake_backend = _make_fake_backend()
    fake_backend.sandbox_id = "sbx-1"  # the resume succeeded
    prompts: list[str] = []

    async def fake_ainvoke(inputs: dict[str, Any]) -> dict[str, Any]:
        prompts.append(inputs["messages"][0].content)
        return _make_fake_agent_result("done")

    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, fake_ainvoke):
            stack.enter_context(item)
        await _handle_delegate({"task": "cross-check the criticals"}, _current_user())

        # ...and the same delegation in a sandbox that could not be resumed.
        prompts.clear()
        fake_backend.sandbox_id = "sbx-replacement"
        await _handle_delegate({"task": "cross-check the criticals"}, _current_user())

    replacement_prompt = prompts[0]
    assert "graph__query_001.json" not in replacement_prompt  # the file is gone with the sandbox
    assert "412 CVE nodes" in replacement_prompt  # but what it established still holds
    episodic_memory.clear_session_ledger()


async def test_the_delegation_inherits_the_conversations_disclosure(mocker) -> None:
    """The sub-agent works from what the conversation unlocked, not the catalogue.

    Its pool being the whole chat-safe set is also how a tool name the outer
    model had never been shown reached session memory and then the planner,
    which required it and had the step refused.
    """
    from reporting.services import chat_graph

    fake_backend = _make_fake_backend()
    captured: dict[str, Any] = {}

    async def _fake_build(current_user, backend=None, *, purpose="", disclosed=None, requested=None):
        captured["disclosed"] = disclosed
        captured["requested"] = requested
        return []

    async def fake_ainvoke(_inputs: dict[str, Any]) -> dict[str, Any]:
        return _make_fake_agent_result("done")

    chat_graph.set_disclosed_tools({"github_security__org_overview"})
    mocker.patch("reporting.services.mcp_builtins.sandbox._build_seizu_tools", _fake_build)
    with ExitStack() as stack:
        for item in _sandbox_patches(fake_backend, fake_ainvoke):
            stack.enter_context(item)
        await _handle_delegate(
            {"task": "count them", "tools": ["cve_analysis__get_cve", "  "]},
            _current_user(),
        )

    assert captured["disclosed"] == frozenset({"github_security__org_overview"})
    # Blank entries dropped, so a sloppy list does not bind an empty name.
    assert captured["requested"] == ["cve_analysis__get_cve"]
    chat_graph.set_disclosed_tools(())


async def test_the_sub_agent_call_carries_a_cache_breakpoint(mocker) -> None:
    """This path was left out when breakpoints were added, and it is where the
    tokens are: 200,761 of a measured turn's 246,210, read at 0%."""
    from langchain_core.messages import AIMessage, HumanMessage

    from reporting.services.mcp_builtins.sandbox import _ToolMessageNormalizingModel

    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    seen: list[Any] = []

    class _Inner:
        model_name = "anthropic/claude-sonnet-4-6"

        async def ainvoke(self, messages, config=None, **kwargs):
            seen.append(messages)
            return AIMessage(content="ok")

    wrapper = _ToolMessageNormalizingModel(_Inner())
    await wrapper.ainvoke([HumanMessage(content="system"), HumanMessage(content="latest")])

    assert seen[0][0].content == "system"  # untouched
    assert seen[0][-1].content[0]["cache_control"] == {"type": "ephemeral"}
