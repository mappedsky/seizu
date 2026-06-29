"""Built-in ``sandbox__delegate`` tool — isolated code execution via a pluggable sandbox.

The chat agent uses this tool to delegate tasks that require running code or
manipulating files.  A :class:`SandboxBackend` is opened per invocation, used as
the tool environment for a scoped ``create_react_agent``, then destroyed.

This tool is ``chat_only=True``: it never appears in the MCP server's tool
listing and cannot be called by external MCP clients.  Isolation is the safety
mechanism — no confirmation gate is needed because the sandbox has no access to
Seizu's internal services or credentials.

**Adding a new sandbox provider** — implement :class:`SandboxBackend` and open it
inside :func:`_open_backend`.  No other code needs to change: the skill interface,
the inner agent, and all tests are backend-agnostic.

Enabled via ``SANDBOX_ENABLED=true``.  Requires a valid ``SANDBOX_API_KEY`` when
using E2B's cloud endpoint; leave the key empty for self-hosted deployments
(e.g. OpenKruise Agents) that use internal auth.  Point ``SANDBOX_DOMAIN`` at the
sandbox service hostname to switch from E2B's cloud to a self-hosted instance.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool

logger = logging.getLogger(__name__)

GROUP = "sandbox"


@runtime_checkable
class SandboxBackend(Protocol):
    """Standard interface for a sandbox execution environment.

    Implement this protocol to add a new sandbox provider (E2B, Docker, Daytona,
    etc.) without changing any skill-facing or agent-facing code.  Each method
    returns a plain string so the inner agent always gets consistent output
    regardless of which backend is active.
    """

    async def run_python(self, code: str) -> str:
        """Run Python code and return stdout/stderr/result as text."""
        ...

    async def run_bash(self, cmd: str) -> str:
        """Run a shell command and return stdout/stderr as text."""
        ...

    async def read_file(self, path: str) -> str:
        """Return the contents of a file as text."""
        ...

    async def write_file(self, path: str, content: str) -> str:
        """Write content to a file; return a confirmation string."""
        ...

    async def list_files(self, path: str) -> str:
        """List files/directories at path; return a human-readable string."""
        ...


class _E2BSandboxBackend:
    """SandboxBackend backed by an ``e2b_code_interpreter.AsyncSandbox``."""

    def __init__(self, sandbox: Any) -> None:
        self._sandbox = sandbox

    async def run_python(self, code: str) -> str:
        execution = await self._sandbox.run_code(code)
        parts: list[str] = []
        if execution.text:
            parts.append(execution.text)
        if execution.error:
            parts.append(f"Error: {execution.error.name}: {execution.error.value}")
            if execution.error.traceback:
                parts.append(execution.error.traceback)
        return "\n".join(parts) if parts else "(no output)"

    async def run_bash(self, cmd: str) -> str:
        result = await self._sandbox.commands.run(cmd)
        parts: list[str] = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"stderr: {result.stderr}")
        return "\n".join(parts) if parts else "(no output)"

    async def read_file(self, path: str) -> str:
        content = await self._sandbox.files.read(path)
        return content if isinstance(content, str) else content.decode(errors="replace")

    async def write_file(self, path: str, content: str) -> str:
        await self._sandbox.files.write(path, content)
        return f"Wrote {len(content)} bytes to {path}"

    async def list_files(self, path: str = "/") -> str:
        entries = await self._sandbox.files.list(path)
        lines = [f"{'d' if e.type == 'dir' else 'f'}  {e.name}" for e in entries]
        return "\n".join(lines) if lines else "(empty)"


@asynccontextmanager
async def _open_backend(*, api_key: str, domain: str) -> AsyncIterator[SandboxBackend]:
    """Open a sandbox and yield a :class:`SandboxBackend` for it.

    This is the extension point for swapping providers.  To add a new backend:
    1. Implement :class:`SandboxBackend`.
    2. Add a ``SANDBOX_BACKEND`` setting (or branch on an existing one).
    3. Open the new backend here and ``yield`` it.

    Tests patch this function to inject any :class:`SandboxBackend` without
    touching provider SDKs.  The ``e2b_code_interpreter`` import stays lazy so
    importing this module never fails when the package is absent.
    """
    from e2b_code_interpreter import AsyncSandbox

    create_kwargs: dict[str, Any] = {}
    if api_key:
        create_kwargs["api_key"] = api_key
    if domain:
        # Custom endpoint (e.g. OpenKruise Agents): domain sets the API base URL
        # to https://api.<domain>; disable client-side key-format validation
        # because non-E2B deployments issue tokens that don't match "e2b_*".
        create_kwargs["domain"] = domain
        create_kwargs["validate_api_key"] = False
    sandbox = await AsyncSandbox.create(**create_kwargs)
    async with sandbox:
        yield _E2BSandboxBackend(sandbox)


_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "Natural-language description of the task for the sandbox agent "
                "to complete. Be specific about expected outputs."
            ),
        },
        "context": {
            "type": "string",
            "description": (
                "Optional data or context (e.g. query results, CSV content) to "
                "pass to the sandbox agent as starting material."
            ),
        },
    },
    "required": ["task"],
}


def _build_sandbox_tools(backend: SandboxBackend) -> list[Any]:
    """Build LangChain StructuredTools from a :class:`SandboxBackend`.

    The tool names and descriptions are fixed regardless of which backend is
    active, so the inner agent's behaviour is consistent across providers.
    """

    async def run_python(code: str) -> str:
        """Run Python code in the sandbox and return stdout, stderr, and any text output."""
        return await backend.run_python(code)

    async def run_bash(cmd: str) -> str:
        """Run a shell command in the sandbox and return stdout and stderr."""
        return await backend.run_bash(cmd)

    async def read_file(path: str) -> str:
        """Read the contents of a file in the sandbox filesystem."""
        return await backend.read_file(path)

    async def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox filesystem."""
        return await backend.write_file(path, content)

    async def list_files(path: str = "/") -> str:
        """List files and directories at a path in the sandbox filesystem."""
        return await backend.list_files(path)

    return [
        StructuredTool.from_function(coroutine=run_python, name="run_python", description=run_python.__doc__ or ""),
        StructuredTool.from_function(coroutine=run_bash, name="run_bash", description=run_bash.__doc__ or ""),
        StructuredTool.from_function(coroutine=read_file, name="read_file", description=read_file.__doc__ or ""),
        StructuredTool.from_function(coroutine=write_file, name="write_file", description=write_file.__doc__ or ""),
        StructuredTool.from_function(coroutine=list_files, name="list_files", description=list_files.__doc__ or ""),
    ]


async def _build_seizu_tools(current_user: CurrentUser) -> list[Any]:
    """Build LangChain StructuredTools wrapping all chat-safe Seizu MCP tools
    the current user has permission to call.

    The sandbox inner agent gets the full permitted tool set so it can fetch
    data and run computations without relying on the outer model to relay
    results through the ``context`` field.  Tool access is bounded by RBAC
    (``chat_safe_only=True`` + ``CHAT_TOOLS_CALL`` permission), not by the
    outer model's current progressive-disclosure state.
    """
    from pydantic import Field, create_model

    from reporting.services import mcp_runtime

    all_tools = await mcp_runtime.list_tools_for_user(
        current_user,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        include_chat_only=False,
    )
    # Never pass sandbox__delegate to the inner agent (prevents recursive delegation).
    seizu_tools = [t for t in all_tools if t.name != "sandbox__delegate"]

    result: list[Any] = []
    for tool in seizu_tools:
        schema: dict[str, Any] = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required") or [])
        fields: dict[str, Any] = {}
        for prop_name, prop_info in properties.items():
            desc = str(prop_info.get("description", ""))
            if prop_name in required:
                fields[prop_name] = (str, Field(..., description=desc))
            else:
                fields[prop_name] = (str | None, Field(None, description=desc))
        args_schema = create_model("_Input", **fields) if fields else None

        tool_name = tool.name

        async def call(_tool_name: str = tool_name, **kwargs: Any) -> str:
            from reporting.services import mcp_runtime as _rt

            outcome = await _rt.call_tool_for_chat(
                current_user,
                _tool_name,
                kwargs,
                gate_permission=Permission.CHAT_TOOLS_CALL,
                chat_safe_only=True,
            )
            if outcome.blocked:
                return f"[blocked: {outcome.blocked}]"
            return outcome.text or "(no output)"

        result.append(
            StructuredTool.from_function(
                coroutine=call,
                name=tool_name,
                description=tool.description or tool_name,
                **({"args_schema": args_schema} if args_schema else {}),
            )
        )
    return result


class _ToolMessageNormalizingModel(Runnable):  # type: ignore[type-arg]
    """Wraps a LangChain chat model to normalize ToolMessage content to list format.

    Some LiteLLM provider transformers (e.g. DeepSeek in LiteLLM ≥1.87) call
    ``convert_content_list_to_str`` unconditionally on all messages, which
    crashes when ``ToolMessage.content`` is a plain Python string — the
    transformer iterates over the string characters and calls ``.get("text")``
    on each one.  This wrapper converts any such string to
    ``[{"type": "text", "text": content}]`` before forwarding, which is a
    universally understood content-block format.

    Inherits from ``Runnable`` so that ``create_react_agent``'s internal
    ``prompt | model`` pipeline accepts it.  ``bind_tools`` wraps the result
    in another ``_ToolMessageNormalizingModel`` so the normalization persists
    after ``create_react_agent``'s ``model.bind_tools(tools)`` call.
    """

    def __init__(self, model: Any) -> None:
        self._model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._model, name)

    def _normalize(self, input: Any) -> Any:
        if not isinstance(input, list):
            return input
        normalized: list[Any] = []
        for msg in input:
            if not hasattr(msg, "content"):
                normalized.append(msg)
                continue
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                # String ToolMessage content → wrap in a text content block so the
                # DeepSeek LiteLLM transformer (which calls c.get("text") on every
                # element of a list) doesn't crash when it sees a plain string.
                msg = msg.model_copy(update={"content": [{"type": "text", "text": msg.content}]})
            elif isinstance(msg.content, list) and any(isinstance(c, str) for c in msg.content):
                # Any message whose content list contains raw strings needs the same
                # treatment — convert string elements to {"type":"text","text":"…"}.
                msg = msg.model_copy(
                    update={"content": [{"type": "text", "text": c} if isinstance(c, str) else c for c in msg.content]}
                )
            normalized.append(msg)
        return normalized

    def bind_tools(self, tools: list[Any], **kwargs: Any) -> "_ToolMessageNormalizingModel":
        return _ToolMessageNormalizingModel(self._model.bind_tools(tools, **kwargs))

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        return self._model.invoke(self._normalize(input), config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        return await self._model.ainvoke(self._normalize(input), config=config, **kwargs)


def _get_sandbox_model() -> "_ToolMessageNormalizingModel":
    """Return a LangChain chat model for the sandbox subagent."""
    from reporting import settings
    from reporting.services.chat_graph import get_chat_model

    if settings.SANDBOX_LLM_MODEL.strip():
        from langchain_litellm import ChatLiteLLM

        provider_model = settings.SANDBOX_LLM_MODEL.strip()
        kwargs: dict[str, Any] = {
            "model": provider_model,
            "temperature": settings.CHAT_LLM_TEMPERATURE,
            "request_timeout": settings.CHAT_LLM_TIMEOUT_SECONDS,
            "max_retries": settings.CHAT_LLM_MAX_RETRIES,
            "streaming": False,
        }
        if settings.CHAT_LLM_MAX_TOKENS > 0:
            kwargs["max_tokens"] = settings.CHAT_LLM_MAX_TOKENS
        if settings.CHAT_LLM_API_KEY:
            kwargs["api_key"] = settings.CHAT_LLM_API_KEY
        if settings.CHAT_LLM_BASE_URL:
            kwargs["api_base"] = settings.CHAT_LLM_BASE_URL
        return _ToolMessageNormalizingModel(ChatLiteLLM(**kwargs))

    return _ToolMessageNormalizingModel(get_chat_model(role="worker"))


async def _handle_delegate(args: dict[str, Any], current_user: CurrentUser | None) -> Any:
    from reporting import settings

    if not settings.SANDBOX_ENABLED:
        return {"error": "Sandbox is not enabled (SANDBOX_ENABLED=false)"}

    if settings.CHAT_LLM_PROVIDER == "mock":
        return {"error": "sandbox__delegate requires a real LLM provider (CHAT_LLM_PROVIDER=mock)"}

    task = str(args.get("task", "")).strip()
    context = str(args.get("context", "")).strip()

    prompt = task
    if context:
        prompt = f"Context:\n{context}\n\nTask:\n{task}"

    async def _run() -> str:
        async with _open_backend(api_key=settings.SANDBOX_API_KEY, domain=settings.SANDBOX_DOMAIN) as backend:
            tools = _build_sandbox_tools(backend)
            if current_user is not None:
                tools = [*tools, *await _build_seizu_tools(current_user)]
            model = _get_sandbox_model()
            agent = create_react_agent(model=model, tools=tools)
            result = await agent.ainvoke({"messages": [HumanMessage(content=prompt)]})
            messages = result.get("messages", [])
            for msg in reversed(messages):
                if hasattr(msg, "content") and not getattr(msg, "tool_calls", None):
                    content = msg.content
                    return content if isinstance(content, str) else str(content)
        return "(no output)"

    try:
        output = await asyncio.wait_for(_run(), timeout=settings.SANDBOX_TIMEOUT_SECONDS)
    except TimeoutError:
        return {"error": f"Sandbox task timed out after {settings.SANDBOX_TIMEOUT_SECONDS}s"}
    except Exception:
        logger.exception("sandbox__delegate failed")
        return {"error": "Sandbox task failed — see server logs for details"}

    max_bytes = settings.SANDBOX_MAX_OUTPUT_BYTES
    encoded = output.encode()
    if len(encoded) > max_bytes:
        output = encoded[:max_bytes].decode(errors="replace") + "\n[truncated]"

    return {"result": output}


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="sandbox__delegate",
            group=GROUP,
            description=(
                "Delegate a task requiring code execution or file operations to an "
                "isolated sandbox agent. The agent can run Python, execute shell "
                "commands, and read/write files. Returns a summary of what was done "
                "and any outputs. Use this when the task involves iterative "
                "computation, data transformation, chart or file generation, or "
                "scripting that cannot be expressed as a Cypher query or a single "
                "MCP tool call. Do not use for tasks a graph query or existing tool "
                "can answer directly — prefer those first."
            ),
            input_schema=_INPUT_SCHEMA,
            required_permissions=[Permission.SANDBOX_DELEGATE.value],
            handler=_handle_delegate,
            # chat_only: external MCP clients must not see this tool — the sandbox
            # is scoped to the chat session and isolated from Seizu's internals.
            chat_only=True,
            # chat_safe_without_confirmation: the sandbox is ephemeral and
            # network-isolated from Seizu's data stores, so isolation is the safety
            # mechanism rather than a confirmation gate (same rationale as
            # reports__create, which also creates isolated new resources).
            chat_safe_without_confirmation=True,
            # always_disclosed: the model should be able to decide independently
            # to use the sandbox for code execution or data processing, without a
            # skill having to explicitly unlock it — mirrors how general-purpose
            # execution tools (bash, text_editor) work in other agent harnesses.
            always_disclosed=True,
        )
    ],
)
