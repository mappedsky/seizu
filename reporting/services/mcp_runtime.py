"""In-process MCP runtime helpers shared by MCP transport and chat agents."""

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import neo4j.exceptions
from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool
from pydantic import ValidationError

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.routes.query import _serialize_neo4j_value
from reporting.schema.confirmations import ConfirmationSource
from reporting.schema.mcp_config import render_skill_prompt, validate_tool_arguments
from reporting.services import action_confirmations, report_store, reporting_neo4j
from reporting.services.mcp_builtins import find_builtin, list_builtin_tools
from reporting.services.mcp_builtins.base import BuiltinTool

logger = logging.getLogger(__name__)


class ChatBlockReason(StrEnum):
    """Structured reason that the chat layer should stop a tool/skill turn.

    The MCP wire format only carries free-form ``TextContent``, so when chat
    re-uses the MCP runtime it cannot tell a permission-denied error apart
    from a tool's natural text output by inspecting the body. This enum is
    the structured signal that travels alongside the body for the chat path
    so the chat layer never has to string-match on error messages.
    """

    PERMISSION_DENIED = "permission_denied"
    NOT_AVAILABLE = "not_available"
    CONFIRMATION_REQUIRED = "confirmation_required"


@dataclass(frozen=True)
class ChatActionOutcome:
    """Chat-specific result for a tool call or skill render.

    ``text`` is the user-visible body (JSON-serialized tool result, rendered
    skill text, or the error message). ``blocked`` is ``None`` for normal
    results and one of :class:`ChatBlockReason` when the runtime refused the
    call — the chat agent breaks the turn and surfaces a structured notice
    instead of letting the model retry against an authorization wall.
    ``tools_required`` carries stored skill metadata for strict progressive
    disclosure; it is empty for normal tool calls.
    """

    text: str
    blocked: ChatBlockReason | None = None
    tools_required: tuple[str, ...] = ()


_PARAM_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "integer": "integer",
    "float": "number",
    "boolean": "boolean",
}

# Non-mutating permissions: reads/inspection, skill rendering (returns text),
# and tool calls (user-defined tools run Cypher that is validated read-only at
# create/update time — strictly more constrained than the arbitrary read
# queries QUERY_EXECUTE already allows). A built-in is exposed to chat only
# when *every* permission it requires is in this set, so a newly added tool
# guarded by a write/delete (or any unrecognised) permission is excluded from
# chat by default. This is fail-closed: forgetting to classify a new mutating
# tool hides it from chat rather than silently exposing it, which a denylist
# would.
_CHAT_SAFE_PERMISSIONS: frozenset[str] = frozenset(
    {
        Permission.REPORTS_READ.value,
        Permission.QUERY_EXECUTE.value,
        Permission.QUERY_VALIDATE.value,
        Permission.QUERY_HISTORY_READ.value,
        Permission.TOOLSETS_READ.value,
        Permission.TOOLS_READ.value,
        Permission.TOOLS_CALL.value,
        Permission.SKILLSETS_READ.value,
        Permission.SKILLS_READ.value,
        Permission.SKILLS_RENDER.value,
        Permission.SCHEDULED_QUERIES_READ.value,
        Permission.USERS_READ.value,
        Permission.ROLES_READ.value,
    }
)


def build_input_schema(parameters: list[Any]) -> dict[str, Any]:
    """Convert a list of ToolParamDef to a JSON Schema object."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in parameters:
        schema_type = _PARAM_TYPE_MAP.get(p.type, "string")
        prop: dict[str, Any] = {"type": schema_type}
        if p.description:
            prop["description"] = p.description
        if p.default is not None:
            prop["default"] = p.default
        properties[p.name] = prop
        if p.required:
            required.append(p.name)
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def text_response(payload: Any) -> list[TextContent]:
    """Serialize *payload* to JSON and wrap it as a single MCP TextContent."""
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


def _neo4j_error_message(exc: neo4j.exceptions.Neo4jError) -> str:
    """A concise, bounded human-readable message from a Neo4j error."""
    message = getattr(exc, "message", None) or str(exc)
    return message[:500]


def _format_validation_error(exc: Exception) -> str:
    """Turn a Pydantic ValidationError (or ValueError) into a compact, model-
    actionable message: one ``field: reason`` per line."""
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "input"
            parts.append(f"{loc}: {err.get('msg', 'invalid value')}")
        return "Invalid arguments — " + "; ".join(parts)
    return f"Invalid arguments — {exc}"


def missing_permissions(required: list[str], granted: frozenset[str]) -> list[str]:
    return [p for p in required if p not in granted]


def parse_user_defined_name(name: str) -> tuple[str, str] | None:
    parts = name.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _is_chat_safe_builtin(builtin: BuiltinTool) -> bool:
    # Read-only builtins pass through directly. Mutating builtins with a
    # confirmation callback are also safe — the confirmation IS the safety gate.
    if builtin.confirmation is not None:
        return True
    # Explicit exceptions are rare and must be documented on the tool
    # registration. Today this is limited to reports__create/reports__clone:
    # new reports are private by default and do not modify existing resources.
    if builtin.chat_safe_without_confirmation:
        return True
    return bool(builtin.required_permissions) and set(builtin.required_permissions) <= _CHAT_SAFE_PERMISSIONS


def _permissions(current_user: CurrentUser | None, permissions: frozenset[str] | None) -> frozenset[str]:
    if permissions is not None:
        return permissions
    return current_user.permissions if current_user is not None else frozenset()


def _missing_required_arguments(input_schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    required = input_schema.get("required")
    if not isinstance(required, list):
        return []
    return [name for name in required if isinstance(name, str) and name not in args]


async def list_tools_for_user(
    current_user: CurrentUser | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
    chat_safe_only: bool = False,
    include_chat_only: bool = False,
    exclude_confirmation_gated: bool = False,
) -> list[Tool]:
    """List the tools a user may call.

    ``exclude_confirmation_gated`` drops only built-ins that carry a confirmation
    resolver — i.e. mutating tools whose safety depends on a human approving them.
    It is for autonomous callers that cannot drive the confirmation round-trip
    (the sandbox subagent), so they get read-only built-ins, the explicit
    no-confirmation exceptions, and all user-defined tools (which have no
    confirmation gate today), but never a tool that would execute ungated. It is a
    per-tool flag check, not a blanket category exclusion: if user-defined tools
    ever gain a confirmation gate, they fall under the same filter automatically.
    """
    perms = _permissions(current_user, permissions)
    if gate_permission and gate_permission.value not in perms:
        return []

    tools: list[Tool] = []
    for builtin in list_builtin_tools(include_chat_only=include_chat_only):
        if chat_safe_only and not _is_chat_safe_builtin(builtin):
            continue
        if exclude_confirmation_gated and builtin.confirmation is not None:
            continue
        if missing_permissions(builtin.required_permissions, perms):
            continue
        tools.append(
            Tool(
                name=builtin.name,
                description=builtin.description,
                inputSchema=builtin.input_schema,
            )
        )

    if Permission.TOOLS_CALL.value in perms:
        try:
            enabled_tools = await report_store.list_enabled_tools()
            for tool in enabled_tools:
                tools.append(
                    Tool(
                        name=f"{tool.toolset_id}__{tool.tool_id}",
                        description=tool.description or f"{tool.name} tool",
                        inputSchema=build_input_schema(tool.parameters),
                    )
                )
        except Exception:
            logger.exception("Failed to load tools from store for MCP listing")

    return tools


async def call_tool_for_user(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
    chat_safe_only: bool = False,
    result_max_rows: int | None = None,
    result_max_bytes: int | None = None,
    confirmation_source: ConfirmationSource | None = None,
    confirmation_session_key: str | None = None,
) -> list[TextContent]:
    """MCP-shaped tool call. Use ``call_tool_for_chat`` from the chat agent."""
    content, _blocked = await _call_tool_core(
        current_user,
        name,
        arguments,
        gate_permission=gate_permission,
        permissions=permissions,
        chat_safe_only=chat_safe_only,
        result_max_rows=result_max_rows,
        result_max_bytes=result_max_bytes,
        confirmation_source=confirmation_source,
        confirmation_session_key=confirmation_session_key,
    )
    return content


async def call_tool_for_chat(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
    chat_safe_only: bool = False,
    include_chat_only: bool = False,
    result_max_rows: int | None = None,
    result_max_bytes: int | None = None,
    confirmation_source: ConfirmationSource | None = None,
    confirmation_session_key: str | None = None,
    confirmation_batch_id: str | None = None,
    bypass_confirmations: bool = False,
    confirmation_pre_approved: bool = False,
) -> ChatActionOutcome:
    """Chat-oriented tool call returning the body together with a block reason.

    Identical to :func:`call_tool_for_user` for execution, but the chat agent
    needs to distinguish authorization failures from a tool's natural output
    so it can stop the turn instead of letting the model retry. The block
    reason is the structured replacement for matching error strings.

    ``bypass_confirmations`` skips the action-confirmation gate entirely. It
    is only honored for callers holding the ``chat:bypass_permissions``
    permission (anyone else is blocked); every bypassed execution is
    audit-logged. Used by the chat UI's bypass mode and by headless agent
    runs (scheduled queries, Temporal workflows).

    ``confirmation_pre_approved`` runs a confirmation-gated tool *without* the
    gate because the caller has already verified the confirmation is approved
    and atomically claimed it for execution. It is internal-only (the
    post-approval executor in ``chat_graph._execute_confirmations``) and is not
    permission-gated — the approval already happened through the normal flow.
    Distinct from ``bypass_confirmations``, which skips the gate before any
    approval and requires ``chat:bypass_permissions``.

    ``include_chat_only`` exposes tools marked ``chat_only=True`` (e.g.
    ``sandbox__delegate``) that are invisible on the MCP server endpoint.
    """
    content, blocked = await _call_tool_core(
        current_user,
        name,
        arguments,
        gate_permission=gate_permission,
        permissions=permissions,
        chat_safe_only=chat_safe_only,
        include_chat_only=include_chat_only,
        result_max_rows=result_max_rows,
        result_max_bytes=result_max_bytes,
        confirmation_source=confirmation_source,
        confirmation_session_key=confirmation_session_key,
        confirmation_batch_id=confirmation_batch_id,
        confirmation_pre_approved=confirmation_pre_approved,
        bypass_confirmations=bypass_confirmations,
    )
    return ChatActionOutcome(text=_text_content_to_string(content), blocked=blocked)


async def _call_tool_core(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
    chat_safe_only: bool = False,
    include_chat_only: bool = False,
    result_max_rows: int | None = None,
    result_max_bytes: int | None = None,
    confirmation_source: ConfirmationSource | None = None,
    confirmation_session_key: str | None = None,
    confirmation_batch_id: str | None = None,
    bypass_confirmations: bool = False,
    confirmation_pre_approved: bool = False,
) -> tuple[list[TextContent], ChatBlockReason | None]:
    args = arguments or {}
    perms = _permissions(current_user, permissions)
    if gate_permission and gate_permission.value not in perms:
        return (
            text_response({"error": f"Permission denied: {gate_permission.value}"}),
            ChatBlockReason.PERMISSION_DENIED,
        )

    builtin = find_builtin(name, include_chat_only=include_chat_only)
    if builtin is not None:
        if chat_safe_only and not _is_chat_safe_builtin(builtin):
            return (
                text_response({"error": f"Tool '{name}' is not available to chat"}),
                ChatBlockReason.NOT_AVAILABLE,
            )
        missing = missing_permissions(builtin.required_permissions, perms)
        if missing:
            return (
                text_response({"error": f"Permission denied: {', '.join(missing)}"}),
                ChatBlockReason.PERMISSION_DENIED,
            )
        missing_args = _missing_required_arguments(builtin.input_schema, args)
        if missing_args:
            return text_response({"error": f"Missing required argument(s): {', '.join(missing_args)}"}), None
        if builtin.confirmation is not None and bypass_confirmations:
            # Bypass mode (chat UI bypass toggle or a headless agent run):
            # honored only for callers holding chat:bypass_permissions, and
            # every bypassed execution is audit-logged.
            if current_user is None:
                return text_response(
                    {"error": "Confirmation bypass requires an authenticated user"}
                ), ChatBlockReason.PERMISSION_DENIED
            if Permission.CHAT_BYPASS_PERMISSIONS.value not in perms:
                return text_response(
                    {"error": f"Permission denied: {Permission.CHAT_BYPASS_PERMISSIONS.value}"}
                ), ChatBlockReason.PERMISSION_DENIED
            logger.info(
                "Action confirmation bypassed",
                extra={
                    "type": "AUDIT",
                    "tool": name,
                    "user": current_user.user.user_id,
                    "source": "bypass",
                },
            )
        elif builtin.confirmation is not None and confirmation_pre_approved:
            # Pre-approved execution: the caller (the post-approval executor in
            # chat_graph) already verified the confirmation is approved and
            # atomically claimed it, so the gate must NOT run again — re-running it
            # here would create a fresh pending confirmation or, without a source,
            # hit the fail-closed branch below. Fall through to the handler.
            if current_user is None:
                return text_response(
                    {"error": "Confirmation execution requires an authenticated user"}
                ), ChatBlockReason.PERMISSION_DENIED
        elif builtin.confirmation is not None and confirmation_source is None:
            # Fail-closed: a confirmation-gated mutating tool was reached without a
            # confirmation source and without bypass. This means the caller did not
            # plumb the confirmation context (e.g. an autonomous subagent), so there
            # is no one to approve the action — refuse rather than execute it ungated.
            # Both real entry points always pass a source (chat="chat", MCP="mcp"),
            # so this only fires on an internal caller that forgot to, and is a
            # defense-in-depth backstop behind tool-list filtering at the call site.
            logger.warning(
                "Refused confirmation-gated tool reached without a confirmation source",
                extra={"type": "AUDIT", "tool": name},
            )
            return text_response(
                {"error": f"Tool '{name}' requires action confirmation, which is unavailable in this context"}
            ), ChatBlockReason.PERMISSION_DENIED
        elif builtin.confirmation is not None and confirmation_source is not None:
            # Fail-closed: a mutating tool was reached via a source that requires
            # confirmation but no session key is available to scope the record.
            if confirmation_session_key is None:
                return text_response(
                    {"error": f"Tool '{name}' requires a session key for confirmation"}
                ), ChatBlockReason.PERMISSION_DENIED
            if current_user is None:
                return text_response(
                    {"error": "Confirmation requires an authenticated user"}
                ), ChatBlockReason.PERMISSION_DENIED
            target = await builtin.confirmation(args, current_user)
            if target is not None:
                confirmation = await action_confirmations.ensure_confirmation(
                    user_id=current_user.user.user_id,
                    source=confirmation_source,
                    session_key=confirmation_session_key,
                    tool_name=name,
                    target=target,
                    arguments=args,
                    batch_id=confirmation_batch_id,
                )
                if confirmation is not None:
                    if confirmation.status == "executed":
                        # A concurrent caller already claimed this approval; treat as
                        # completed so the LLM does not retry and create a duplicate.
                        return text_response(
                            {"notice": f"Tool '{name}' was already executed by a concurrent request."}
                        ), None
                    payload = action_confirmations.confirmation_required_payload(confirmation)
                    if confirmation.status == "denied":
                        payload["error"] = "Action was denied for this confirmation window"
                    return text_response(payload), ChatBlockReason.CONFIRMATION_REQUIRED
        try:
            result = await builtin.handler(args, current_user)
            return (
                _bounded_text_response(result, max_rows=result_max_rows, max_bytes=result_max_bytes),
                None,
            )
        except (ValidationError, ValueError) as exc:
            # Bad arguments (e.g. a malformed tools_required entry) are the
            # caller's fault, not a server fault: return the validation detail so
            # the model can fix the call and retry, and log at warning without a
            # stack trace so it doesn't read as an unhandled crash.
            message = _format_validation_error(exc)
            logger.warning("Invalid arguments for built-in MCP tool %s: %s", name, message)
            return text_response({"error": message}), None
        except Exception:
            logger.exception("Failed to execute built-in MCP tool %s", name)
            return text_response({"error": f"Failed to execute tool '{name}'"}), None

    if Permission.TOOLS_CALL.value not in perms:
        return (
            text_response({"error": f"Permission denied: {Permission.TOOLS_CALL.value}"}),
            ChatBlockReason.PERMISSION_DENIED,
        )
    try:
        parsed_name = parse_user_defined_name(name)
        target_tool = await report_store.get_enabled_tool(parsed_name[0], parsed_name[1]) if parsed_name else None
        if target_tool is None:
            return text_response({"error": f"Tool '{name}' not found"}), None

        arg_errors = validate_tool_arguments(target_tool.parameters, args)
        if arg_errors:
            return text_response({"errors": arg_errors}), None

        params_with_defaults = {p.name: p.default for p in target_tool.parameters}
        params_with_defaults.update(args)

        results = await reporting_neo4j.run_query(target_tool.cypher, parameters=params_with_defaults)
        serialized = [{key: _serialize_neo4j_value(value) for key, value in record.items()} for record in results]
        return (
            _bounded_text_response(serialized, max_rows=result_max_rows, max_bytes=result_max_bytes),
            None,
        )
    except neo4j.exceptions.Neo4jError as exc:
        # The tool's own cypher/parameters are wrong (e.g. a missing parameter
        # because the query references $x that the tool doesn't declare, or a
        # syntax error) — a client error in user-defined content, not a server
        # fault. Log it concisely (no traceback) and surface the database message
        # so the caller can see why and fix the tool, rather than a vague failure.
        message = _neo4j_error_message(exc)
        logger.warning("MCP tool %s query failed: %s", name, message)
        return text_response({"error": f"Tool '{name}' query failed: {message}"}), None
    except Exception:
        logger.exception("Failed to execute MCP tool %s", name)
        return text_response({"error": f"Failed to execute tool '{name}'"}), None


def _text_content_to_string(content: list[TextContent]) -> str:
    return "\n\n".join(item.text for item in content if hasattr(item, "text"))


async def list_prompts_for_user(
    current_user: CurrentUser | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
) -> list[Prompt]:
    perms = _permissions(current_user, permissions)
    if gate_permission and gate_permission.value not in perms:
        return []
    if Permission.SKILLS_RENDER.value not in perms:
        return []

    prompts: list[Prompt] = []
    try:
        enabled_skills = await report_store.list_enabled_skills()
        for skill in enabled_skills:
            prompts.append(
                Prompt(
                    name=f"{skill.skillset_id}__{skill.skill_id}",
                    title=skill.name,
                    description=_skill_prompt_description(skill),
                    arguments=[
                        PromptArgument(
                            name=p.name,
                            description=p.description or None,
                            required=p.required and p.default is None,
                        )
                        for p in skill.parameters
                    ],
                )
            )
    except Exception:
        logger.exception("Failed to load skills from store for MCP prompt listing")
    return prompts


def _skill_prompt_description(skill: Any) -> str:
    description = skill.description or f"{skill.name} skill"
    triggers = [trigger for trigger in getattr(skill, "triggers", []) if isinstance(trigger, str) and trigger]
    if not triggers:
        return description
    rendered_triggers = "; ".join(triggers[:8])
    suffix = f" Use when the request matches these trigger phrases: {rendered_triggers}."
    if len(triggers) > 8:
        suffix = f"{suffix[:-1]}; and {len(triggers) - 8} more."
    return f"{description}{suffix}"


async def get_prompt_for_user(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, str] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
) -> GetPromptResult:
    """MCP-shaped prompt render. Use ``render_prompt_for_chat`` from the chat agent."""
    result, _blocked, _tools_required = await _get_prompt_core(
        current_user,
        name,
        arguments,
        gate_permission=gate_permission,
        permissions=permissions,
    )
    return result


async def render_prompt_for_chat(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, str] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
) -> ChatActionOutcome:
    """Chat-oriented skill render returning the body together with a block reason."""
    result, blocked, tools_required = await _get_prompt_core(
        current_user,
        name,
        arguments,
        gate_permission=gate_permission,
        permissions=permissions,
    )
    text = "\n\n".join(t for m in result.messages if (t := _prompt_message_text(m.content)))
    return ChatActionOutcome(text=text, blocked=blocked, tools_required=tools_required)


async def _get_prompt_core(
    current_user: CurrentUser | None,
    name: str,
    arguments: dict[str, str] | None,
    *,
    gate_permission: Permission | None = None,
    permissions: frozenset[str] | None = None,
) -> tuple[GetPromptResult, ChatBlockReason | None, tuple[str, ...]]:
    perms = _permissions(current_user, permissions)
    if gate_permission and gate_permission.value not in perms:
        return _permission_denied_prompt(gate_permission.value), ChatBlockReason.PERMISSION_DENIED, ()
    if Permission.SKILLS_RENDER.value not in perms:
        return _permission_denied_prompt(Permission.SKILLS_RENDER.value), ChatBlockReason.PERMISSION_DENIED, ()

    try:
        parsed_name = parse_user_defined_name(name)
        target_skill = await report_store.get_enabled_skill(parsed_name[0], parsed_name[1]) if parsed_name else None
        if target_skill is None:
            return (
                GetPromptResult(
                    description="Skill not found",
                    messages=[
                        PromptMessage(
                            role="user",
                            content=TextContent(type="text", text=f"Skill '{name}' not found"),
                        )
                    ],
                ),
                None,
                (),
            )
        rendered, errors = render_skill_prompt(
            target_skill.parameters,
            target_skill.template,
            arguments or {},
            target_skill.triggers,
            target_skill.tools_required,
        )
        text = rendered if rendered is not None else json.dumps({"errors": errors}, indent=2)
        return (
            GetPromptResult(
                description=target_skill.description or target_skill.name,
                messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))],
            ),
            None,
            tuple(target_skill.tools_required),
        )
    except Exception:
        logger.exception("Failed to render MCP prompt %s", name)
        return (
            GetPromptResult(
                description="Skill render failed",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=f"Failed to render skill '{name}'"),
                    )
                ],
            ),
            None,
            (),
        )


def _prompt_message_text(content: Any) -> str | None:
    text = getattr(content, "text", None)
    return text if isinstance(text, str) else None


def _permission_denied_prompt(permission: str) -> GetPromptResult:
    return GetPromptResult(
        description="Permission denied",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=f"Permission denied: {permission}"),
            )
        ],
    )


def _bounded_text_response(
    payload: Any,
    *,
    max_rows: int | None,
    max_bytes: int | None,
) -> list[TextContent]:
    """Serialize a tool result for chat, bounding by rows then bytes.

    Over the row cap, keep the first ``max_rows`` rows. Over the byte cap, shed
    whole rows from the end until the JSON fits, so the caller still gets as
    many complete rows as possible (with a truncation marker) instead of
    nothing — only falling back to an error marker when not even one row fits
    (a single oversized row, or a non-list payload that can't be row-shed).
    """
    rows = payload if isinstance(payload, list) else None
    capped: list[Any] | None
    if rows is not None and max_rows is not None and max_rows > 0 and len(rows) > max_rows:
        capped = rows[:max_rows]
        bounded: Any = _row_limit_payload(capped, max_rows=max_rows)
    else:
        capped = rows
        bounded = payload

    text = json.dumps(bounded, indent=2, default=str)
    if max_bytes is None or max_bytes <= 0 or len(text.encode("utf-8")) <= max_bytes:
        return [TextContent(type="text", text=text)]

    # Over the byte budget: shed whole rows if the payload is a list.
    if capped is None:
        return _emit(_byte_limit_error(max_bytes))
    total = len(rows) if rows is not None else len(capped)
    keep = _rows_within_byte_budget(capped, total=total, max_bytes=max_bytes)
    if keep <= 0:
        return _emit(_byte_limit_error(max_bytes))
    return _emit(_byte_limit_payload(capped[:keep], total=total, returned=keep, max_bytes=max_bytes))


def _row_limit_payload(rows: list[Any], *, max_rows: int) -> dict[str, Any]:
    return {"results": rows, "truncated": True, "truncated_reason": "row_limit", "max_rows": max_rows}


def _byte_limit_payload(rows: list[Any], *, total: int, returned: int, max_bytes: int) -> dict[str, Any]:
    return {
        "results": rows,
        "truncated": True,
        "truncated_reason": "byte_limit",
        "returned": returned,
        "total_rows": total,
        "max_bytes": max_bytes,
    }


def _byte_limit_error(max_bytes: int) -> dict[str, Any]:
    return {
        "error": "Tool result exceeded chat size limit",
        "truncated": True,
        "truncated_reason": "byte_limit",
        "max_bytes": max_bytes,
    }


def _rows_within_byte_budget(rows: list[Any], *, total: int, max_bytes: int) -> int:
    """Largest k such that the byte-limit payload for rows[:k] fits max_bytes."""
    lo, hi, best = 0, len(rows), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _byte_limit_payload(rows[:mid], total=total, returned=mid, max_bytes=max_bytes)
        if len(json.dumps(candidate, indent=2, default=str).encode("utf-8")) <= max_bytes:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def _emit(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]
