"""Tests for the ``sandbox__delegate`` MCP built-in."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import Tool

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.report_config import User
from reporting.services.mcp_builtins import find_builtin, list_builtin_tools
from reporting.services.mcp_builtins.sandbox import (
    SandboxBackend,
    _build_seizu_tools,
    _get_sandbox_model,
    _handle_delegate,
    _ToolMessageNormalizingModel,
)

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
# Registry visibility
# ---------------------------------------------------------------------------


def test_sandbox_delegate_absent_from_default_listing() -> None:
    """MCP server path: sandbox__delegate must not appear (chat_only)."""
    tools = list_builtin_tools()
    assert not any(t.name == "sandbox__delegate" for t in tools)


def test_sandbox_delegate_present_with_include_chat_only() -> None:
    """Chat agent path: sandbox__delegate appears when include_chat_only=True."""
    tools = list_builtin_tools(include_chat_only=True)
    assert any(t.name == "sandbox__delegate" for t in tools)


def test_find_builtin_excludes_sandbox_delegate_by_default() -> None:
    assert find_builtin("sandbox__delegate") is None


def test_find_builtin_includes_sandbox_delegate_with_flag() -> None:
    tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert tool.group == "sandbox"


def test_sandbox_delegate_has_required_permissions() -> None:
    tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert Permission.SANDBOX_DELEGATE.value in tool.required_permissions


def test_sandbox_delegate_is_chat_safe_without_confirmation() -> None:
    tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert tool.chat_safe_without_confirmation is True
    assert tool.confirmation is None


def test_sandbox_delegate_is_always_disclosed() -> None:
    from reporting.services.mcp_builtins import always_disclosed_tool_names

    tool = find_builtin("sandbox__delegate", include_chat_only=True)
    assert tool is not None
    assert tool.always_disclosed is True
    assert "sandbox__delegate" in always_disclosed_tool_names()


# ---------------------------------------------------------------------------
# Handler: disabled / misconfigured state
# ---------------------------------------------------------------------------


async def test_handler_returns_error_when_disabled() -> None:
    with patch("reporting.settings.SANDBOX_ENABLED", False):
        result = await _handle_delegate({"task": "print hello"}, _current_user())
    assert "error" in result
    assert "SANDBOX_ENABLED" in result["error"]


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
            "reporting.services.mcp_builtins.sandbox._open_backend",
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
            "reporting.services.mcp_builtins.sandbox._open_backend",
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
            "reporting.services.mcp_builtins.sandbox._open_backend",
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
        patch("reporting.services.mcp_builtins.sandbox._open_backend", new=slow_backend),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
    ):
        result = await _handle_delegate({"task": "slow task"}, _current_user())

    assert "error" in result
    assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# Seizu tool injection — sandbox inner agent gets all permitted tools
# ---------------------------------------------------------------------------


async def test_build_seizu_tools_returns_all_permitted_tools() -> None:
    # The sandbox inner agent receives all chat-safe tools regardless of what
    # the outer model's progressive disclosure state happens to be.
    fake_tools = [
        Tool(name="graph__query", description="Query", inputSchema={"type": "object", "properties": {}}),
        Tool(name="reports__get", description="Get report", inputSchema={"type": "object", "properties": {}}),
    ]
    with patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=fake_tools)):
        tools = await _build_seizu_tools(_current_user())

    assert len(tools) == 2
    assert {t.name for t in tools} == {"graph__query", "reports__get"}


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


async def test_handler_injects_seizu_tools_into_inner_agent() -> None:
    # The inner create_react_agent receives both sandbox execution tools and
    # the full set of permitted Seizu tools.
    fake_backend = _make_fake_backend()
    fake_result = _make_fake_agent_result("result")
    captured_tools: list[Any] = []

    def fake_create_react_agent(*, model: Any, tools: list[Any]) -> Any:
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
        patch("reporting.services.mcp_builtins.sandbox._open_backend", new=_open_backend_ctx(fake_backend)),
        patch("reporting.services.mcp_builtins.sandbox.create_react_agent", side_effect=fake_create_react_agent),
        patch("reporting.services.mcp_builtins.sandbox._get_sandbox_model", return_value=MagicMock()),
        patch("reporting.services.mcp_runtime.list_tools_for_user", AsyncMock(return_value=[seizu_tool])),
    ):
        await _handle_delegate({"task": "analyze graph"}, _current_user())

    tool_names = {t.name for t in captured_tools}
    assert "run_python" in tool_names
    assert "graph__query" in tool_names


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
