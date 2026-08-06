"""Built-in ``sandbox__delegate`` tool — isolated code execution via a pluggable sandbox.

The chat agent uses this tool to delegate tasks that require running code or
manipulating files.  Each call runs a scoped ``create_react_agent`` over a
:class:`SandboxBackend`, taken from the conversation's
:mod:`~reporting.services.sandbox_session` when one is ambient — so files an
earlier delegation, or an earlier *turn*, left behind are still there — and
opened privately for the call otherwise.

This tool is ``chat_only=True``: it never appears in the MCP server's tool
listing and cannot be called by external MCP clients.  Isolation is the safety
mechanism — no confirmation gate is needed because the sandbox has no access to
Seizu's internal services or credentials.

The :class:`SandboxBackend` protocol and :func:`open_backend` are also consumed
by :mod:`reporting.services.sandbox_remediation` (the CVE dependency remediation
Temporal workflow), which drives the sandbox directly rather than through this
chat tool.

**Adding a new sandbox provider** — implement :class:`SandboxBackend` and open it
inside :func:`open_backend`.  No other code needs to change: the skill interface,
the inner agent, and all tests are backend-agnostic.

Enabled via ``SANDBOX_ENABLED=true``.  Requires a valid ``SANDBOX_API_KEY`` when
using E2B's cloud endpoint; leave the key empty for self-hosted deployments
(e.g. OpenKruise Agents) that use internal auth.  Point ``SANDBOX_DOMAIN`` at the
sandbox service hostname to switch from E2B's cloud to a self-hosted instance.
"""

import asyncio
import itertools
import json
import logging
import uuid
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.prebuilt import create_react_agent

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.services import chat_budget, chat_context, episodic_memory, sandbox_session
from reporting.services.chat_messages import message_text
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool
from reporting.services.sandbox_backend import SandboxBackend, open_backend

logger = logging.getLogger(__name__)

GROUP = "sandbox"
# Distinct ledger phase so sandbox spend is legible next to the outer loop.
_SANDBOX_BUDGET_PHASE = "sandbox_subagent"


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
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional exact names of the Seizu tools this task needs (e.g. "
                '["cve_analysis__get_recent_cves"]). Naming them directs the '
                "sub-agent instead of leaving it to work out which tool to use, "
                "and keeps the rest out of its context. It can still search for "
                "others if the task turns out to need them. Omit when the task "
                "only processes data you are already passing in."
            ),
        },
    },
    "required": ["task"],
}


def _build_sandbox_tools(backend: SandboxBackend) -> list[Any]:
    """Build LangChain StructuredTools from a :class:`SandboxBackend`.

    The tool names and descriptions are fixed regardless of which backend is
    active, so the inner agent's behaviour is consistent across providers.  Every
    result is byte-capped (``SANDBOX_MAX_OUTPUT_BYTES``) before it reaches the
    inner agent, so a single noisy command or large file read can't blow up its
    context.
    """
    from reporting import settings

    def _cap(text: str) -> str:
        return _truncate_bytes(text, settings.SANDBOX_MAX_OUTPUT_BYTES)

    async def run_python(code: str) -> str:
        """Run Python code in the sandbox and return stdout, stderr, and any text output."""
        return _cap(await backend.run_python(code))

    async def run_bash(cmd: str) -> str:
        """Run a shell command in the sandbox and return stdout and stderr."""
        return _cap(await backend.run_bash(cmd))

    async def preview_file(path: str) -> str:
        """Inspect a file: its size, shape, and beginning.

        Returns small files whole. For anything larger this returns a summary
        and the first part only — it is for working out how to process a file,
        not for loading one. Use run_python to work with the full contents.
        """
        return _file_preview(path, await backend.read_file(path))

    async def read_file(path: str) -> str:
        """Read the contents of a file in the sandbox filesystem."""
        content = await backend.read_file(path)
        size = len(content.encode())
        if size <= settings.SANDBOX_MAX_OUTPUT_BYTES:
            return content
        # Say what was lost and what to do instead. The bare `[truncated]`
        # marker this used to return is easy to read past: an agent that asked
        # for a 500KB result file got a tenth of it and no reason to think it
        # had anything less than the whole thing -- the same silent-truncation
        # failure that made a cut-off model answer look complete.
        head = _truncate_bytes(content, settings.SANDBOX_MAX_OUTPUT_BYTES)
        return (
            f"[{path} is {size} bytes, larger than the {settings.SANDBOX_MAX_OUTPUT_BYTES} that can be read into "
            "context. The first part follows, but it is NOT the whole file. To use all of it, process the file in "
            "code with run_python instead of reading it.]\n" + head
        )

    async def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox filesystem."""
        return _cap(await backend.write_file(path, content))

    async def list_files(path: str = "/") -> str:
        """List files and directories at a path in the sandbox filesystem."""
        return _cap(await backend.list_files(path))

    return [
        StructuredTool.from_function(coroutine=run_python, name="run_python", description=run_python.__doc__ or ""),
        StructuredTool.from_function(coroutine=run_bash, name="run_bash", description=run_bash.__doc__ or ""),
        # preview_file when a preview budget is set, read_file otherwise. Both
        # exist because the earlier verdict on preview_file was rendered while
        # every delegation got a fresh sandbox: it told the agent to process a
        # file with run_python, and across delegations that file did not exist.
        # The comparison is only meaningful now sandboxes are shared per step.
        (
            StructuredTool.from_function(
                coroutine=preview_file, name="preview_file", description=preview_file.__doc__ or ""
            )
            if settings.SANDBOX_PREVIEW_MAX_BYTES > 0
            else StructuredTool.from_function(
                coroutine=read_file, name="read_file", description=read_file.__doc__ or ""
            )
        ),
        StructuredTool.from_function(coroutine=write_file, name="write_file", description=write_file.__doc__ or ""),
        StructuredTool.from_function(coroutine=list_files, name="list_files", description=list_files.__doc__ or ""),
    ]


# Where oversized results land in the sandbox. A fixed directory and a running
# number keep paths predictable in a transcript.
_RESULT_DIR = "/tmp/seizu_results"
# Rows returned per sample in a receipt: enough to show the shape, not the data.
_RECEIPT_SAMPLE_ROWS = 2


def _result_rows(text: str) -> list[Any] | None:
    """Best-effort row list from a tool result, for describing it in a receipt.

    Tool results are strings by contract, so this parses rather than assumes.
    Returning ``None`` simply means the receipt describes bytes instead of rows.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("results", "rows", "records", "items", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return value
    return None


def _file_preview(path: str, content: str) -> str:
    """Describe a file compactly enough that code can be written against it.

    A file inside the preview budget comes back whole, so nothing changes for
    the small files an agent writes itself. Beyond it the agent gets shape --
    size, line count, JSON structure, columns -- and only the beginning, so a
    result file written to keep data out of context cannot be pulled straight
    back into it.
    """
    size = len(content.encode())
    budget = max(0, settings.SANDBOX_PREVIEW_MAX_BYTES)
    if size <= budget:
        return content

    summary: dict[str, Any] = {"path": path, "bytes": size, "lines": content.count("\n") + 1}
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        parsed = None
    if parsed is not None:
        summary["json"] = type(parsed).__name__
        rows = _result_rows(content)
        if rows is not None:
            summary["rows"] = len(rows)
            columns = _result_columns(rows)
            if columns:
                summary["columns"] = columns
    summary["preview_only"] = (
        f"This is a {size}-byte file and only its first {budget} bytes follow. To use the whole file, process it "
        "in code with run_python (e.g. json.load(open(path))) rather than previewing it again."
    )
    return json.dumps(summary, default=str) + "\n\n" + _truncate_bytes(content, budget)


def _file_result_receipt(path: str, text: str) -> str:
    """Describe a result too large to return, which was written to the sandbox.

    Carries shape (row count, columns, a couple of samples) so the agent can
    write code against the file without having read it. The wording states the
    situation rather than recommending a habit: this result *cannot* be
    returned, so reading the file is the only way to see it, and re-running the
    query will produce the same outcome.
    """
    receipt: dict[str, Any] = {
        "status": "too_large_to_return",
        "saved_to": path,
        "bytes": len(text.encode()),
    }
    rows = _result_rows(text)
    if rows is not None:
        receipt["rows"] = len(rows)
        columns = _result_columns(rows)
        if columns:
            receipt["columns"] = columns
        receipt["sample"] = rows[:_RECEIPT_SAMPLE_ROWS]
    receipt["next_step"] = (
        f"The full result is in {path}; only the sample above was returned. Read or process the file with "
        "run_python (e.g. json.load(open(path))). Re-running this call will return this same receipt."
    )
    return json.dumps(receipt, default=str)


# Bound for every delegation regardless of disclosure. A sub-agent exists to
# fetch and compute, and these are the tools that "fetch" means when no specific
# one has been named; without them a narrowed delegation would have to spend a
# discovery round trip to do the most ordinary thing it does. All read-only.
_CORE_TOOL_NAMES = frozenset({"graph__query", "graph__schema", "graph__validate_query", "graph__explain"})
# How many matches find_seizu_tools returns, and how much of each description.
_FIND_TOOLS_LIMIT = 12
_FIND_TOOLS_DESC_MAX = 200


def _bound_tool_names(
    reachable: list[Any],
    disclosed: frozenset[str] | None,
    requested: list[str] | None,
) -> set[str]:
    """Which of the reachable tools get bound as typed tools for this delegation.

    ``requested`` narrows as well as widens; an unknown name is ignored, since
    the RBAC-filtered ``reachable`` list is the authority on what exists. See
    SBX-003 in docs/root/dev/decisions/sandbox.md.
    """
    names = {tool.name for tool in reachable}
    bound = {name for name in _CORE_TOOL_NAMES if name in names}
    asked = {name for name in (requested or []) if name in names}
    if asked:
        return bound | asked
    return bound | {name for name in (disclosed or frozenset()) if name in names}


def _result_columns(rows: list[Any] | None) -> list[str]:
    columns: list[str] = []
    for row in rows or []:
        if isinstance(row, dict):
            for key in row:
                if key not in columns:
                    columns.append(key)
    return columns


def _record_receipt(
    path: str,
    tool_name: str,
    purpose: str,
    backend: SandboxBackend,
    rows: list[Any] | None,
) -> None:
    """Tell the conversation's ledger that this data is now on disk.

    The receipt covers the rest of *this* delegation; the ledger entry is what
    survives the turn. See SBX-008 in docs/root/dev/decisions/sandbox.md.
    """
    ledger = episodic_memory.current_session_ledger()
    if ledger is None:
        return
    ledger.record_receipt(
        path=path,
        source=tool_name,
        purpose=purpose,
        sandbox_id=getattr(backend, "sandbox_id", "") or "",
        rows=len(rows) if rows is not None else None,
        columns=_result_columns(rows),
    )


async def _build_seizu_tools(
    current_user: CurrentUser,
    backend: SandboxBackend | None = None,
    *,
    purpose: str = "",
    disclosed: frozenset[str] | None = None,
    requested: list[str] | None = None,
    skills_available: list[Any] | None = None,
) -> list[Any]:
    """Build LangChain StructuredTools wrapping the Seizu MCP tools the sandbox
    inner agent may call.

    RBAC is the boundary: ``chat_safe_only=True`` + ``CHAT_TOOLS_CALL`` decide
    what this user may reach at all, and nothing below widens that.
    Confirmation-gated mutating tools are excluded — the sub-agent cannot drive
    the interactive confirmation round-trip, so those stay with the outer agent.

    What is *bound* is narrower: ``_CORE_TOOL_NAMES`` + ``disclosed`` +
    ``requested``, where ``requested`` narrows as well as widens. How the
    sub-agent reaches anything else follows ``CHAT_LLM_PROGRESSIVE_DISCLOSURE``
    — tool search when off, skill search and render when on.

    Given a ``backend``, a result too large to return is written into the
    sandbox filesystem, replaced by a receipt, and recorded in the session
    ledger under ``purpose`` so a later turn is told the data already exists.

    Read SBX-002, SBX-003 and SBX-004 in
    ``docs/root/dev/decisions/sandbox.md`` before changing what is bound, how
    discovery works, or what triggers a result file. Each was arrived at by
    measurement and the obvious alternative is the one that was rejected.
    """
    from pydantic import Field, create_model

    from reporting.services import mcp_runtime

    all_tools = await mcp_runtime.list_tools_for_user(
        current_user,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        include_chat_only=False,
        exclude_confirmation_gated=True,
    )
    # Never pass sandbox__delegate to the inner agent (prevents recursive delegation).
    reachable = [t for t in all_tools if t.name != "sandbox__delegate"]
    seizu_tools = [t for t in reachable if t.name in _bound_tool_names(reachable, disclosed, requested)]

    _JSON_TYPE_TO_PY: dict[str, type] = {"integer": int, "number": float, "boolean": bool}
    # Shared by every tool in this delegation so paths stay distinct.
    _result_seq = itertools.count(1)

    async def _invoke(tool_name: str, arguments: dict[str, Any]) -> str:
        """Run one Seizu tool for the sub-agent, however it was reached.

        Shared by the typed per-tool wrappers and the generic
        ``call_seizu_tool`` escape hatch, so a tool discovered at runtime
        gets the same result bounds, the same oversized-result file, and the
        same receipt as one that was bound up front.
        """
        from reporting import settings as _settings
        from reporting.services import mcp_runtime as _rt

        # Fetch to the file bounds when a sandbox exists, because whether a
        # result is oversized cannot be known before fetching it, and the
        # source caps would have already discarded the excess. Without a
        # backend there is nowhere to put it, so keep the context caps.
        if backend is None:
            max_rows = _settings.CHAT_TOOL_RESULT_MAX_ROWS
            max_bytes = _settings.CHAT_TOOL_RESULT_MAX_BYTES
        else:
            max_rows = max(_settings.CHAT_TOOL_RESULT_MAX_ROWS, _settings.SANDBOX_FILE_RESULT_MAX_ROWS)
            max_bytes = max(_settings.CHAT_TOOL_RESULT_MAX_BYTES, _settings.SANDBOX_FILE_RESULT_MAX_BYTES)

        outcome = await _rt.call_tool_for_chat(
            current_user,
            tool_name,
            arguments,
            gate_permission=Permission.CHAT_TOOLS_CALL,
            chat_safe_only=True,
            result_max_rows=max_rows,
            result_max_bytes=max_bytes,
        )
        if outcome.blocked:
            return f"[blocked: {outcome.blocked}]"
        text = outcome.text or "(no output)"

        # Size decides, not the model, and both bounds are load-bearing --
        # see SBX-002 in docs/root/dev/decisions/sandbox.md. Neither the
        # trigger nor the row cap survives being "simplified".
        rows = _result_rows(text)
        oversized = len(text.encode()) > _settings.SANDBOX_MAX_OUTPUT_BYTES or (
            rows is not None and len(rows) > _settings.CHAT_TOOL_RESULT_MAX_ROWS
        )
        if backend is None or not oversized:
            return _truncate_bytes(text, _settings.SANDBOX_MAX_OUTPUT_BYTES)

        # Unique per call, not just per delegation: delegations in one step
        # now share a filesystem and can run concurrently, so a per-builder
        # counter would collide.
        path = f"{_RESULT_DIR}/{tool_name}_{next(_result_seq):03d}_{uuid.uuid4().hex[:8]}.json"
        try:
            await backend.write_file(path, text)
        except Exception:
            # Returning the truncated result is exactly what would have
            # happened without this path, so a write failure costs nothing
            # beyond the rows that never fit.
            logger.warning("sandbox: could not write %s result to %s", tool_name, path, exc_info=True)
            return _truncate_bytes(text, _settings.SANDBOX_MAX_OUTPUT_BYTES)
        _record_receipt(path, tool_name, purpose, backend, rows)
        return _file_result_receipt(path, text)

    result: list[Any] = []
    for tool in seizu_tools:
        schema: dict[str, Any] = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required") or [])
        fields: dict[str, Any] = {}
        for prop_name, prop_info in properties.items():
            desc = str(prop_info.get("description", ""))
            py_type: type = _JSON_TYPE_TO_PY.get(prop_info.get("type", "string"), str)
            if prop_name in required:
                fields[prop_name] = (py_type, Field(..., description=desc))
            else:
                fields[prop_name] = (py_type | None, Field(None, description=desc))
        args_schema = create_model("_Input", **fields) if fields else None

        tool_name = tool.name

        async def call(_tool_name: str = tool_name, **kwargs: Any) -> str:
            return await _invoke(_tool_name, kwargs)

        result.append(
            StructuredTool.from_function(
                coroutine=call,
                name=tool_name,
                description=tool.description or tool_name,
                **({"args_schema": args_schema} if args_schema else {}),
            )
        )

    bound_names = {tool.name for tool in seizu_tools}
    undiscovered = [tool for tool in reachable if tool.name not in bound_names]
    if not undiscovered:
        return result
    if not _settings_progressive_disclosure():
        result.extend(_discovery_tools(undiscovered, _invoke))
        return result
    skills = list(skills_available if skills_available is not None else _turn_skills())
    if skills:
        result.extend(_skill_discovery_tools(current_user, skills, undiscovered, _invoke))
    return result


def _settings_progressive_disclosure() -> bool:
    from reporting import settings as _settings

    return bool(_settings.CHAT_LLM_PROGRESSIVE_DISCLOSURE)


def _turn_skills() -> tuple[Any, ...]:
    """The turn's skill listing, set by whichever chat path spawned this delegation.

    Ambient rather than an argument for the same reason the disclosure set is:
    the listing belongs to the turn, and re-reading it per delegation would
    break the one-listing-per-turn rule. Empty outside a chat turn, which
    simply leaves the sub-agent with its bound tools.
    """
    from reporting.services.chat_graph import current_available_skills

    return current_available_skills()


def _skill_discovery_tools(
    current_user: CurrentUser,
    skills: list[Any],
    undiscovered: list[Any],
    invoke: Any,
) -> list[Any]:
    """Skill-mediated access to the tools that were not bound for this delegation.

    The progressive-disclosure counterpart to ``_discovery_tools``: the
    sub-agent searches *skills*, loads one, and may then call the tools its
    author declared. Nothing here widens RBAC -- ``undiscovered`` is already the
    chat-safe, gate-excluded set, so a declaration naming anything outside it
    unlocks nothing.

    Deliberately more limiting than free-text tool search; see SBX-004 in
    docs/root/dev/decisions/sandbox.md.
    """
    by_name = {tool.name: tool for tool in undiscovered}
    skills_by_name = {skill.name: skill for skill in skills}
    # Grows as skills are loaded. A set rather than a rebind so the closures
    # below share one view, and so a skill loaded for one sub-task keeps its
    # tools callable for the rest of the delegation.
    unlocked: set[str] = set()

    async def find_seizu_skills(query: str) -> str:
        """Search Seizu skills for a workflow that covers what you need.

        Returns matching skill names and what they do. Load one with
        load_seizu_skill to get its instructions and unlock the tools it uses.
        """
        terms = [term for term in query.lower().split() if term]
        scored: list[tuple[int, Any]] = []
        for skill in skills:
            haystack = f"{skill.name} {getattr(skill, 'title', '') or ''} {skill.description or ''}".lower()
            hits = sum(1 for term in terms if term in haystack)
            if hits or not terms:
                scored.append((hits, skill))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        if not scored:
            return (
                f"No skills match {query!r}. Work with the tools you already have, "
                "and say what you could not determine rather than assuming the data does not exist."
            )
        lines = [
            json.dumps(
                {
                    "name": skill.name,
                    "description": _truncate((skill.description or "").strip(), _FIND_TOOLS_DESC_MAX),
                    "arguments": [
                        {"name": argument.name, "required": bool(argument.required)}
                        for argument in (skill.arguments or [])
                    ],
                },
                default=str,
            )
            for _, skill in scored[:_FIND_TOOLS_LIMIT]
        ]
        return "\n".join(lines)

    async def load_seizu_skill(name: str, arguments_json: str = "{}") -> str:
        """Load a skill found with find_seizu_skills, by name.

        Returns the skill's instructions and unlocks the tools it uses, which
        you then run with call_seizu_tool. arguments_json is a JSON object of
        the skill's arguments, e.g. {"severity": "CRITICAL"}.
        """
        from reporting.services import mcp_runtime as _rt

        if name not in skills_by_name:
            return f"[unknown skill {name!r}. Use find_seizu_skills to see what is available.]"
        try:
            arguments = json.loads(arguments_json or "{}")
        except ValueError as exc:
            return f"[arguments_json is not valid JSON: {exc}]"
        if not isinstance(arguments, dict):
            return '[arguments_json must be a JSON object, e.g. {"severity": "CRITICAL"}]'
        outcome = await _rt.render_prompt_for_chat(
            current_user,
            name,
            {key: str(value) for key, value in arguments.items()},
            gate_permission=Permission.CHAT_SKILLS_CALL,
        )
        if outcome.blocked:
            return f"[blocked: {outcome.blocked}]"
        # Declared names that are not reachable are dropped rather than
        # reported: the skill author's list is a disclosure request, and RBAC
        # already decided the answer.
        newly = {tool_name for tool_name in outcome.tools_required if tool_name in by_name}
        unlocked.update(newly)
        body = outcome.text or "(skill returned no instructions)"
        if not newly:
            # Said plainly rather than left silent: a skill whose author
            # declared no tools still reads as though it has some, and the
            # sub-agent would otherwise discover the gap one failed
            # call_seizu_tool at a time. Measured on one deployment, 1 of 10
            # skills declares nothing.
            return (
                f"{body}\n\n[This skill unlocked no additional tools. Follow it with the tools you "
                "already have — graph__query with Cypher is the general-purpose route.]"
            )
        available = ", ".join(sorted(newly))
        return f"{body}\n\n[Tools now callable with call_seizu_tool: {available}]"

    async def call_seizu_tool(name: str, arguments_json: str = "{}") -> str:
        """Call a tool unlocked by a skill you loaded, by name.

        arguments_json is a JSON object of the tool's arguments, e.g.
        {"limit": 10}. Tools already in your tool list should be called
        directly instead; this is for the ones a loaded skill unlocked.
        """
        if name not in unlocked:
            if name in by_name:
                return (
                    f"[{name!r} exists but no skill you have loaded uses it. Load the skill that "
                    "covers this workflow with find_seizu_skills, or work with the tools you have.]"
                )
            return (
                f"[unknown tool {name!r}. Call it directly if it is already in your tool list, "
                "or use find_seizu_skills to find a skill that provides what you need.]"
            )
        try:
            arguments = json.loads(arguments_json or "{}")
        except ValueError as exc:
            return f"[arguments_json is not valid JSON: {exc}]"
        if not isinstance(arguments, dict):
            return '[arguments_json must be a JSON object, e.g. {"limit": 10}]'
        return await invoke(name, arguments)

    return [
        StructuredTool.from_function(
            coroutine=find_seizu_skills, name="find_seizu_skills", description=find_seizu_skills.__doc__ or ""
        ),
        StructuredTool.from_function(
            coroutine=load_seizu_skill, name="load_seizu_skill", description=load_seizu_skill.__doc__ or ""
        ),
        StructuredTool.from_function(
            coroutine=call_seizu_tool, name="call_seizu_tool", description=call_seizu_tool.__doc__ or ""
        ),
    ]


def _discovery_tools(undiscovered: list[Any], invoke: Any) -> list[Any]:
    """Search-and-call access to the tools that were not bound for this delegation.

    The non-progressive-disclosure route: one round trip to find a tool instead
    of failing, over the same RBAC-filtered set the sub-agent could already
    reach. See SBX-004 in docs/root/dev/decisions/sandbox.md.
    """
    by_name = {tool.name: tool for tool in undiscovered}

    async def find_seizu_tools(query: str) -> str:
        """Search Seizu tools that are not already in your tool list.

        Returns matching tool names, descriptions and argument schemas. Use it
        when the task needs data none of your current tools provide; then call
        the tool with call_seizu_tool.
        """
        terms = [term for term in query.lower().split() if term]
        scored: list[tuple[int, Any]] = []
        for tool in undiscovered:
            haystack = f"{tool.name} {tool.description or ''}".lower()
            hits = sum(1 for term in terms if term in haystack)
            if hits or not terms:
                scored.append((hits, tool))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        if not scored:
            return f"No other tools match {query!r}. You already have the ones you need, or the data is not available."
        lines = [
            json.dumps(
                {
                    "name": tool.name,
                    "description": _truncate((tool.description or "").strip(), _FIND_TOOLS_DESC_MAX),
                    "arguments": (tool.inputSchema or {}).get("properties", {}),
                    "required": (tool.inputSchema or {}).get("required", []),
                },
                default=str,
            )
            for _, tool in scored[:_FIND_TOOLS_LIMIT]
        ]
        return "\n".join(lines)

    async def call_seizu_tool(name: str, arguments_json: str = "{}") -> str:
        """Call a Seizu tool found with find_seizu_tools, by name.

        arguments_json is a JSON object of the tool's arguments, e.g.
        {"limit": 10}. Tools already in your tool list should be called
        directly instead; this is for the ones that are not.
        """
        if name not in by_name:
            return (
                f"[unknown tool {name!r}. Call it directly if it is already in your tool list, "
                "or use find_seizu_tools to see what else is available.]"
            )
        try:
            arguments = json.loads(arguments_json or "{}")
        except ValueError as exc:
            return f"[arguments_json is not valid JSON: {exc}]"
        if not isinstance(arguments, dict):
            return '[arguments_json must be a JSON object, e.g. {"limit": 10}]'
        return await invoke(name, arguments)

    return [
        StructuredTool.from_function(
            coroutine=find_seizu_tools, name="find_seizu_tools", description=find_seizu_tools.__doc__ or ""
        ),
        StructuredTool.from_function(
            coroutine=call_seizu_tool, name="call_seizu_tool", description=call_seizu_tool.__doc__ or ""
        ),
    ]


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
        return self._model.invoke(self._cacheable(self._normalize(input)), config, **kwargs)

    def _cacheable(self, normalized: Any) -> Any:
        """Roll a cache breakpoint along the sub-agent's growing message list.

        Every inner call re-sends the whole conversation so far, which is the
        shape prompt caching exists for -- but this path never saw a breakpoint,
        and on a provider that caches nothing without one it read zero. It is
        where the tokens are: 200,761 of a measured turn's 246,210.
        """
        if not isinstance(normalized, list):
            return normalized
        # The sub-agent's system prompt and tools are inside the list and the
        # bound model, so the message sequence is the whole fingerprint here.
        # Keyed per *delegation*: every delegation opens with the same system
        # prompt, so the lineage check cannot separate them and one delegation
        # would be diffed against the last one's unrelated task.
        from reporting.services.chat_graph import _current_tool_detail_id

        delegation = _current_tool_detail_id.get() or chat_budget.current_budget_scope() or "delegation"
        chat_context.log_cache_divergence(f"sandbox:{delegation}", self._model, "", "", normalized)
        return chat_context.with_message_cache_breakpoints(self._model, normalized)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        normalized = self._cacheable(self._normalize(input))
        controller = chat_budget.current_budget_controller()
        if controller is None:
            return await self._model.ainvoke(normalized, config=config, **kwargs)
        scope = chat_budget.current_budget_scope()

        # Every inner LLM call funnels through here, so this is the one place
        # the sandbox subagent's spend can be seen at all. Reserving (rather
        # than only recording afterwards) is what makes an exhausted run stop
        # delegating instead of continuing to spend invisibly.
        messages = normalized if isinstance(normalized, list) else []
        estimated_input = chat_budget.estimate_tokens(self._model, "", messages, [])
        estimated_output = settings.CHAT_LLM_MAX_TOKENS
        reservation = await controller.reserve(
            estimated_input_tokens=estimated_input,
            estimated_output_tokens=estimated_output,
            # Reserve the cost too, matching the outer LLM path. Without it a
            # deployment budgeting on cost alone (CHAT_RUN_COST_BUDGET_USD with
            # the token dimension disabled) authorizes every sandbox call at
            # zero, so concurrent calls can overshoot the ceiling before any of
            # them records what it spent.
            estimated_cost_usd=controller.project_cost_usd(self._model, estimated_input, estimated_output),
            # Scope, so this counts against the delegating step's ceiling. A
            # step's own counter cannot see this spend -- it happens below the
            # outer loop -- so without the scope a step could spend hundreds of
            # thousands of tokens here while its local total stayed small, and
            # starve every sibling step. Phase is a child of the scope so the
            # ledger shows where it went.
            scope=scope,
            phase=f"{scope}:{_SANDBOX_BUDGET_PHASE}" if scope else _SANDBOX_BUDGET_PHASE,
        )
        settled = False
        try:
            response = await self._model.ainvoke(normalized, config=config, **kwargs)
        finally:
            if not settled:
                # BaseException, not Exception: every delegation runs under
                # asyncio.wait_for(SANDBOX_TIMEOUT_SECONDS), so cancellation is
                # the routine ending, not an exotic one. Catching only Exception
                # leaked the reservation on every timeout, and because scope
                # authorization counts in-flight reservations, each leak
                # permanently consumed part of the step's ceiling. Discarding is
                # synchronous because awaiting a lock while being cancelled can
                # itself be interrupted.
                controller.discard(reservation)
        usage = chat_budget.usage_from_message(response)
        input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        estimated = not usage.reported
        if estimated:
            # No provider usage: bill the estimate rather than nothing, so an
            # unreported call still moves the ledger.
            input_tokens, output_tokens = estimated_input, 0
        settled = True
        await controller.commit(
            reservation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            # The sub-agent loop is where cached input dominates: it re-sends a
            # growing prefix on every call, and the provider serves nearly all
            # of it from cache after the first.
            cost_usd=chat_budget.usage_cost_usd(
                self._model,
                input_tokens,
                output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_creation_tokens=usage.cache_creation_tokens,
            ),
            usage_estimated=estimated,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
        )
        return response


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


_SANDBOX_TITLE = "Tool: sandbox__delegate"
_CHILD_BODY_MAX = 600
_CHILD_ARGS_MAX = 600


def _wrap_with_detail_events(
    tools: list[StructuredTool],
    writer: Any,
    parent_id: str | None = None,
    children: list[dict[str, Any]] | None = None,
) -> list[StructuredTool]:
    """Re-wrap each inner-agent tool so its calls nest inside the outer subagent entry.

    The outer chat loop pre-emits one detail entry for the ``sandbox__delegate``
    call (id == ``parent_id``).  Each inner tool call appends a child detail dict
    to the shared ``children`` list and re-emits that same outer entry — now as a
    ``subagent`` kind carrying the growing ``children`` array — under the *same*
    ``parent_id``.  Because every update reuses one detail id, the AI SDK reconciles
    them into a single section that fills in live as the subagent works, instead of
    a flurry of sibling rows that reorder and vanish.

    ``writer`` and ``children`` must both be captured from the outer LangGraph
    context *before* the inner ``create_react_agent`` graph starts and passed in
    here.  LangGraph resets the ``get_stream_writer`` contextvar when it begins its
    own execution, so reading it from inside the wrapped tool would yield a no-op
    writer that drops events; ``children`` is captured the same way so persistence
    never depends on a contextvar surviving the inner graph's execution.  The outer
    node reads the same ``children`` list back to attach it to the persisted entry,
    so the whole run survives a page reload.

    With no ``parent_id`` (e.g. outside a streaming context, in tests) the tools run
    untouched apart from the bookkeeping, and nothing is emitted.
    """

    def _emit_section(status: str) -> None:
        if not parent_id:
            return
        data: dict[str, Any] = {
            "kind": "subagent",
            "title": _SANDBOX_TITLE,
            "status": status,
            "detail_id": parent_id,
            # Snapshot the children so a later in-place mutation can't retroactively
            # alter an already-emitted frame.
            "children": [dict(child) for child in (children or [])],
        }
        writer({"kind": "detail", "id": parent_id, "data": data})

    result: list[StructuredTool] = []
    for tool in tools:
        original = tool.coroutine
        if original is None:
            result.append(tool)
            continue
        tool_name = tool.name

        async def _wrapped(
            _orig: Any = original,
            _name: str = tool_name,
            **kwargs: Any,
        ) -> Any:
            child: dict[str, Any] = {
                "kind": "tool",
                "title": f"Sandbox: {_name}",
                "status": "running",
                "detail_id": f"sandbox_{uuid.uuid4().hex}",
                "arguments": _truncate(_format_args(kwargs), _CHILD_ARGS_MAX),
            }
            if children is not None:
                children.append(child)
            _emit_section("running")
            try:
                out = await _orig(**kwargs)
            except Exception as exc:
                child["status"] = "error"
                child["body"] = _truncate(str(exc), _CHILD_BODY_MAX)
                _emit_section("running")
                raise
            child["status"] = "completed"
            child["body"] = _truncate(str(out) if out is not None else "", _CHILD_BODY_MAX)
            _emit_section("running")
            return out

        create_kwargs: dict[str, Any] = {
            "coroutine": _wrapped,
            "name": tool.name,
            "description": tool.description or tool.name,
        }
        if tool.args_schema is not None:
            create_kwargs["args_schema"] = tool.args_schema
        result.append(StructuredTool.from_function(**create_kwargs))
    return result


def _format_args(kwargs: dict[str, Any]) -> str:
    """Render inner-tool arguments compactly for a child detail entry."""
    parts: list[str] = []
    for key, value in kwargs.items():
        text = value if isinstance(value, str) else repr(value)
        parts.append(f"{key}: {text}")
    return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Cap text to ``max_bytes`` UTF-8 bytes with a ``[truncated]`` marker.

    Applied to every inner tool result before it is fed back to the sandbox agent
    (not just the final answer), so a large file read or noisy command can't blow
    up the inner model's context, memory, latency, or provider spend.
    """
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode(errors="replace") + "\n[truncated]"


# The inner agent had no system prompt at all: it was handed tools and a task and
# left to work out the shape of the data, the cost of its choices, and what to do
# with an oversized result, every single time. Measured runs showed exactly that
# -- schema re-introspection on every delegation, hundreds of repeat queries, and
# results pulled into context that code could have processed.
_SUBAGENT_PROMPT = """You are a sub-agent working inside an ephemeral sandbox for one task.

You exist so data is handled by code rather than by a model. Fetch what you need, \
process it with run_python, and return conclusions. Pulling rows into your own context \
to reason over them is the expensive path and usually the wrong one.

What the data looks like:
- Seizu tools return JSON. Query-style results are an object with a "results" list of \
  row objects, often alongside "warnings".
- A result too large to return is written to a file instead, and you get \
  {"status": "too_large_to_return", "saved_to": "<path>", "rows": N, "columns": [...], \
  "sample": [...]}. The rows are in that file, not in the receipt.
- When you get such a receipt, read the file in code: json.load(open(path))["results"] \
  gives the rows. Do not preview or re-run the query to try to see them; the receipt is \
  what the call returns.
- A result that carries "truncated": true is incomplete. Say so rather than treating it \
  as the whole set.
- The sandbox is shared with the rest of this conversation, including earlier turns. Files \
  listed to you as already saved are really there: read one instead of fetching its data \
  again. If a listed file turns out to be missing, fetch what you need and say so.
__DISCOVERY__

How to work:
- Query once, save what you get, and compute over it. Repeating a query to see more of it \
  costs more than processing what you already have.
- Aggregate, filter, join and count in run_python, not by reading rows yourself.
- Prefer a purpose-built tool over raw Cypher when one covers the question.
- Return the findings and the numbers behind them. Do not return transcripts, row dumps, \
  or a description of what you tried."""


_DISCOVERY_PLACEHOLDER = "__DISCOVERY__"

# What the sub-agent is told about reaching beyond its bound tools. Has to match
# what it was actually given: a prompt naming find_seizu_tools when only skill
# discovery is bound spends turns calling a tool that is not there.
_DISCOVERY_CLAUSE_TOOLS = """\
- Your tool list is the tools this task was expected to need, not everything Seizu has. If \
  none of them provides the data, search with find_seizu_tools and run what you find with \
  call_seizu_tool. Do that rather than concluding the data does not exist."""

_DISCOVERY_CLAUSE_SKILLS = """\
- Your tool list is the tools this task was expected to need, not everything Seizu has. If \
  none of them provides the data, search with find_seizu_skills for a skill covering the \
  workflow, load it with load_seizu_skill, and run the tools it unlocks with call_seizu_tool. \
  Do that rather than concluding the data does not exist.
- If no skill covers it, graph__query with Cypher is the general-purpose route. Say what you \
  could not determine; do not guess at numbers."""

_DISCOVERY_CLAUSE_NONE = """\
- Your tool list is the tools this task was expected to need. If none of them provides the \
  data, use graph__query with Cypher, and say what you could not determine rather than \
  guessing at numbers."""


def _subagent_prompt(tool_names: set[str]) -> str:
    """The sub-agent system prompt, describing the discovery route it actually has."""
    if "find_seizu_tools" in tool_names:
        discovery = _DISCOVERY_CLAUSE_TOOLS
    elif "find_seizu_skills" in tool_names:
        discovery = _DISCOVERY_CLAUSE_SKILLS
    else:
        discovery = _DISCOVERY_CLAUSE_NONE
    # replace(), not format(): the prompt contains literal JSON braces.
    return _SUBAGENT_PROMPT.replace(_DISCOVERY_PLACEHOLDER, discovery)


def _budget_note(remaining: int | None, *, wrap_up: bool) -> str:
    """Tell the sub-agent what it may spend, in terms it can act on."""
    if remaining is None:
        return ""
    if wrap_up:
        return (
            f"\n\nBudget: about {remaining} tokens remain for this step and most of the allowance is "
            "already spent. Stop gathering, work with what you have, and return your findings now. "
            "Being cut off mid-task loses everything you have not yet reported."
        )
    return (
        f"\n\nBudget: about {remaining} tokens are available for this step, shared with any other work "
        "it does. Spend them on code rather than on reading data into context."
    )


async def _handle_delegate(args: dict[str, Any], current_user: CurrentUser | None) -> Any:
    from reporting import settings
    from reporting.services.chat_graph import (
        _child_detail_event_accumulator,
        _current_tool_detail_id,
        current_disclosed_tools,
    )

    # Capture the outer chat graph's stream writer before starting the inner
    # create_react_agent, which resets the LangGraph contextvar.  Falls back to
    # a no-op outside a LangGraph streaming context (e.g. in tests).
    try:
        writer: Any = get_stream_writer()
    except RuntimeError:
        writer = lambda _: None  # noqa: E731

    # The outer chat loop pre-emits a "running" detail for this tool call with this
    # ID; we reuse it as the subagent section's id so inner-tool details fill in
    # under the already-visible sandbox__delegate row rather than as sibling rows.
    parent_id: str | None = _current_tool_detail_id.get()

    # Grab (and own) the children list for this detail id from the outer node's
    # accumulator here — alongside writer and parent_id — rather than from inside
    # the wrapped tools; see _wrap_with_detail_events for why neither the writer nor
    # this list may be read through a contextvar after the inner graph starts.  The
    # outer node reads this same list back to persist the nested children.
    accumulator = _child_detail_event_accumulator.get()
    children: list[dict[str, Any]] | None = (
        accumulator.setdefault(parent_id, []) if (accumulator is not None and parent_id) else None
    )

    if settings.CHAT_LLM_PROVIDER == "mock":
        return {"error": "sandbox__delegate requires a real LLM provider (CHAT_LLM_PROVIDER=mock)"}

    task = str(args.get("task", "")).strip()
    context = str(args.get("context", "")).strip()
    # What the delegating model asked for, and what the conversation has already
    # unlocked. Together these decide which Seizu tools are bound for this
    # delegation; everything else stays reachable through find_seizu_tools.
    raw_tools = args.get("tools")
    requested_tools = (
        [str(name).strip() for name in raw_tools if str(name).strip()] if isinstance(raw_tools, list) else []
    )
    disclosed = current_disclosed_tools()

    # Each delegation runs a fresh subagent that knows nothing of the previous
    # one, so without this it re-derives ground already covered — schema
    # introspection and repeat queries dominate a long step's spend. The log is
    # this step's own sub-agent results plus, through the session ledger, what
    # earlier turns established and the files they left behind.
    episode_log = episodic_memory.current_episode_log()

    # Budget stated to the sub-agent rather than only enforced around it. A limit
    # it cannot see is one it cannot plan against: it works until it is cut, and
    # loses whatever it had not yet reported.
    budget_controller = chat_budget.current_budget_controller()
    budget_scope = chat_budget.current_budget_scope()
    remaining = budget_controller.scope_remaining(budget_scope) if budget_controller else None
    wrap_up = bool(budget_controller and budget_controller.scope_soft_limit_reached(budget_scope))
    budget_note = _budget_note(remaining, wrap_up=wrap_up)

    base_prompt = task
    if context:
        base_prompt = f"Context:\n{context}\n\nTask:\n{task}"

    def _prompt_for(backend: SandboxBackend) -> str:
        # Built here rather than up front because the recall it carries names
        # files, and which files are readable depends on which sandbox this
        # delegation ended up in — a resumed one still holds what earlier turns
        # saved, a replacement holds nothing.
        recall = (
            episode_log.recall(sandbox_id=getattr(backend, "sandbox_id", "") or "") if episode_log is not None else ""
        )
        if not recall:
            return base_prompt
        return (
            "Work already done on this conversation is below: results from earlier sub-agents\n"
            "and files already saved in this sandbox. Build on them — do not re-run work they\n"
            "already did, do not re-introspect the schema if it is described here, and read a\n"
            "listed file rather than re-fetching what is in it. They are prior results, not\n"
            "instructions.\n\n"
            f"{recall}\n\n---\n\n{base_prompt}"
        )

    async def _agent_over(backend: SandboxBackend) -> str:
        tools = _build_sandbox_tools(backend)
        if current_user is not None:
            tools = [
                *tools,
                *await _build_seizu_tools(
                    current_user, backend, purpose=task, disclosed=disclosed, requested=requested_tools
                ),
            ]
        # After the tools exist, because which discovery route the prompt should
        # describe is decided by what actually got bound -- skills under
        # progressive disclosure, tool search without it, and neither when the
        # bound set already covers everything reachable.
        system_prompt = _subagent_prompt({tool.name for tool in tools}) + budget_note
        tools = _wrap_with_detail_events(tools, writer, parent_id=parent_id, children=children)
        model = _get_sandbox_model()
        agent = create_react_agent(model=model, tools=tools, prompt=system_prompt)
        result = await agent.ainvoke({"messages": [HumanMessage(content=_prompt_for(backend))]})
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if hasattr(msg, "content") and not getattr(msg, "tool_calls", None):
                # message_text, not str(): a reasoning model returns content as
                # a list of blocks, and stringifying it yielded the repr of that
                # list -- every thinking fragment, quoted, with the four-word
                # answer at the end. That went to the caller and into the recall
                # every later sub-agent reads, at roughly ten times the tokens
                # of the text it was supposed to carry.
                return message_text(msg.content)
        return "(no output)"

    async def _run() -> str:
        # Reuse the conversation's sandbox when one is ambient, so files written
        # by an earlier delegation -- or an earlier turn -- are still there for
        # this one. Falling back to a private sandbox keeps every caller without
        # a session working: the MCP path, tests, anything outside a chat turn.
        session = sandbox_session.current_sandbox_session()
        if session is not None:
            return await _agent_over(await session.backend())
        async with open_backend(api_key=settings.SANDBOX_API_KEY, domain=settings.SANDBOX_DOMAIN) as backend:
            return await _agent_over(backend)

    try:
        output = await asyncio.wait_for(_run(), timeout=settings.SANDBOX_TIMEOUT_SECONDS)
    except TimeoutError:
        return {"error": f"Sandbox task timed out after {settings.SANDBOX_TIMEOUT_SECONDS}s"}
    except chat_budget.BudgetExceeded as exc:
        # Expected, not a fault: the step spent its allowance mid-delegation.
        # The generic handler logged this as a crash with a full traceback and
        # told the caller "Sandbox task failed", which is both noisy and
        # unactionable -- indistinguishable from a broken sandbox, so the only
        # sensible response (stop and report) was not available to it.
        logger.info("sandbox__delegate stopped on budget: %s", exc)
        return {
            "error": (
                f"Stopped: {exc} This step cannot fund more delegation. Do not retry this or start new "
                "work; report what you have already gathered."
            )
        }
    except Exception:
        logger.exception("sandbox__delegate failed")
        return {"error": "Sandbox task failed — see server logs for details"}

    result_text = _truncate_bytes(output, settings.SANDBOX_MAX_OUTPUT_BYTES)
    # Record only on success: a timeout or crash returns above, and logging a
    # failure as a "result" would teach the next sub-agent that the ground was
    # already covered when it was not.
    if episode_log is not None:
        episode_log.append(task, result_text)
    return {"result": result_text}


def _sandbox_enabled() -> bool:
    from reporting import settings

    return settings.SANDBOX_ENABLED


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
                "can answer directly — prefer those first. Direct the sub-agent: say "
                "what to produce, name the tools the task needs in `tools`, and name "
                "any already-saved file it should read rather than re-fetching. It "
                "plans its own steps, so the less it has to infer, the less it spends."
            ),
            input_schema=_INPUT_SCHEMA,
            required_permissions=[Permission.SANDBOX_DELEGATE.value],
            handler=_handle_delegate,
            # enabled: omit the tool from all listings when SANDBOX_ENABLED=false
            # so the model never sees it and call-time errors are never reached.
            enabled=_sandbox_enabled,
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
        ),
    ],
)
