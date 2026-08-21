"""In-process MCP runtime helpers shared by MCP transport and chat agents."""

import json
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import jsonschema
import neo4j.exceptions
from mcp.types import GetPromptResult, Prompt, PromptArgument, PromptMessage, TextContent, Tool, ToolAnnotations
from pydantic import ValidationError

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.routes.query import _serialize_neo4j_value
from reporting.schema.confirmations import ActionConfirmationTarget, ConfirmationSource
from reporting.schema.mcp_config import render_skill_prompt
from reporting.services import action_confirmations, external_mcp, report_store, reporting_neo4j, telemetry
from reporting.services.mcp_builtins import find_builtin, list_builtin_tools
from reporting.services.mcp_builtins.base import BuiltinTool
from reporting.services.payload_bounds import json_size_bytes, largest_prefix_within_bytes
from reporting.services.result_limits import (
    ResultLimits,
    Truncation,
    reset_current_result_limits,
    set_current_result_limits,
    stream_truncation,
)

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
    AUTHENTICATION_REQUIRED = "authentication_required"


@dataclass(frozen=True)
class ToolCallOutcome:
    """An MCP tool call's body, and whether the call failed outright.

    ``is_error`` maps to ``CallToolResult.is_error`` on the wire. It marks a
    call that could not be honoured -- arguments the schema rejects, a
    misconfigured tool, an unexpected fault -- as opposed to a tool that ran and
    returned an unwelcome answer, which is an ordinary result. The 1.x SDK
    wrapper drew that line; carrying it here keeps it after the 2.x callbacks
    stopped doing so.
    """

    content: list[TextContent]
    is_error: bool = False


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
        Permission.SPACES_READ.value,
        Permission.WORKFLOWS_READ.value,
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
    # registration. Today: the sandbox delegation tool, plus reports__create,
    # which produces a private report except when it files into a space -- that
    # case is caught by its resolver above.
    if builtin.chat_safe_without_confirmation:
        return True
    return bool(builtin.required_permissions) and set(builtin.required_permissions) <= _CHAT_SAFE_PERMISSIONS


def _permissions(current_user: CurrentUser | None, permissions: frozenset[str] | None) -> frozenset[str]:
    if permissions is not None:
        return permissions
    return current_user.permissions if current_user is not None else frozenset()


class _ToolFailure(Exception):
    """A call that failed rather than returned — reported with ``is_error``.

    MCP draws a line between a tool that *ran* and produced an unwelcome answer
    ("report not found", "permission denied", "confirmation required") and a
    call that could not be honoured at all. The first is an ordinary result; the
    second is a result flagged ``is_error``. The 1.x SDK drew that line for us
    via ``_make_error_result``; carrying it explicitly keeps the distinction
    after the 2.x callbacks stopped doing so.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        # Carries either shape the runtime emits: a single "error", or the
        # "errors" list a user-defined tool's parameter check produces.
        detail = payload.get("error") or payload.get("errors") or "tool call failed"
        super().__init__(str(detail))
        self.payload = payload


def _normalize_arguments(parameters: list[Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Put schema-valid values into the type the query needs.

    JSON Schema counts ``2.0`` as an integer -- a number with no fractional
    part -- so a client validating against the schema Seizu published in
    ``tools/list`` may legitimately send one, and Neo4j will not take a float
    where the Cypher uses an integer (``LIMIT $n``). Normalizing is the only
    honest answer: refusing it would mean advertising a contract and then
    rejecting a value that contract allows.

    This is not the coercion the schema check exists to stop. Nothing here turns
    a *string* into a number -- ``"5"`` is still refused by the gate above, as is
    ``2.5`` -- and only a parameter the tool itself declared as an integer is
    touched. ``bool`` is an ``int`` subclass but never a ``float``, so ``True``
    cannot reach this conversion.
    """
    normalized = dict(arguments)
    for param in parameters:
        if param.type != "integer":
            continue
        value = normalized.get(param.name)
        if isinstance(value, float) and value.is_integer():
            normalized[param.name] = int(value)
    return normalized


def _validate_arguments(input_schema: dict[str, Any], args: dict[str, Any]) -> None:
    """Check *args* against the tool's advertised JSON Schema, or raise.

    Runs ahead of the confirmation gate and of execution, because the schema is
    the contract the tool published in ``tools/list`` and nothing downstream
    re-checks it. Until MCP 2.0 the SDK's ``@server.call_tool()`` wrapper ran
    exactly this against every tool it dispatched, built-in or user-defined.

    Checking types rather than only ``required`` is the point, and the gap it
    closes is not cosmetic. Both kinds of tool quietly accepted values their
    advertised schema forbids, by different routes:

    - Built-in handlers parse with pydantic, which *coerces*: ``"pinned":
      "false"`` against a ``boolean`` schema became ``False`` and unpinned the
      report. Malformed values also reached the confirmation resolvers, which
      parse the same arguments and raised out of the call entirely.
    - User-defined tools ran the store's ``validate_tool_arguments``, which
      computes a coerced value and then discards it, so ``"5"`` for an
      ``integer`` passed the check and reached Neo4j as the *string* ``"5"``.
      That validator no longer gates this path at all: it is looser than the
      advertised schema for every type except an integral float, which
      :func:`_normalize_arguments` handles.

    A wrongly typed argument must not be able to change what a mutation does, or
    what a query matches. Runs for chat as well as MCP so the two cannot diverge
    on what a tool will accept.
    """
    try:
        jsonschema.validate(instance=args, schema=input_schema)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        detail = f"{location}: {exc.message}" if location else exc.message
        raise _ToolFailure({"error": f"Input validation error: {detail}"}) from exc
    except jsonschema.SchemaError as exc:
        # A malformed schema is our bug, not the caller's -- but it is still a
        # broken type guard, and letting the call through would restore exactly
        # the coercion this check exists to stop, on a tool that might mutate.
        # Invalid validation policy fails closed.
        logger.exception("Tool schema is not valid JSON Schema; refusing the call")
        raise _ToolFailure({"error": "Tool is misconfigured: its input schema is not valid JSON Schema"}) from exc


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
        # A tool carrying both a resolver and the no-confirmation exception is
        # gated only for some argument shapes (reports__create gates on filing
        # into a space). It stays listed: the call-time gate below still refuses
        # the gated shape, so excluding the tool entirely would cost the safe
        # shape for nothing. A tool gated for every call (reports__clone) has no
        # such flag and is dropped here, since none of its calls could proceed.
        if (
            exclude_confirmation_gated
            and builtin.confirmation is not None
            and not builtin.chat_safe_without_confirmation
        ):
            continue
        if missing_permissions(builtin.required_permissions, perms):
            continue
        tools.append(
            Tool(
                name=builtin.name,
                description=builtin.description,
                input_schema=builtin.input_schema,
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
                        input_schema=build_input_schema(tool.parameters),
                    )
                )
        except Exception:
            logger.exception("Failed to load tools from store for MCP listing")

    # External proxies are an agent capability, not a transparent federation of
    # Seizu's own MCP endpoint. Keep them on the chat-safe path so an MCP client
    # connecting to Seizu does not unexpectedly inherit a second trust domain.
    if chat_safe_only:
        tools.extend(
            await external_mcp.list_tools_for_user(
                current_user,
                exclude_confirmation_gated=exclude_confirmation_gated,
            )
        )

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
) -> ToolCallOutcome:
    """MCP-shaped tool call. Use ``call_tool_for_chat`` from the chat agent."""
    content, _blocked, is_error = await _guarded(
        name,
        _call_tool_core(
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
        ),
    )
    return ToolCallOutcome(content=content, is_error=is_error)


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
    external_tool_annotations: ToolAnnotations | None = None,
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
    content, blocked, _is_error = await _guarded(
        name,
        _call_tool_core(
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
            external_tool_annotations=external_tool_annotations,
        ),
    )
    return ChatActionOutcome(text=_text_content_to_string(content), blocked=blocked)


async def _guarded(
    name: str, call: Coroutine[Any, Any, tuple[list[TextContent], ChatBlockReason | None]]
) -> tuple[list[TextContent], ChatBlockReason | None, bool]:
    """Never let a tool call raise out of the runtime; say when one failed.

    Until MCP 2.0 the SDK's ``@server.call_tool()`` wrapper turned *any*
    exception from a handler into an ``is_error`` result. The 2.x constructor
    callbacks do not, so an escape now becomes a JSON-RPC protocol error, which
    reads to a client as a broken server rather than a failed call.

    ``_call_tool_core`` guards its handler invocation but not the three awaits
    ahead of it -- the two confirmation resolvers and the write that records a
    pending confirmation -- so a resolver parsing bad arguments, or the
    confirmation store being unreachable, would escape. Argument validation now
    stops the first of those at the door; this is the backstop for the rest.

    The third element is ``is_error``: true only when the call could not be
    honoured at all, which is the same line the 1.x wrapper drew. A tool that
    ran and reported "not found" or "permission denied" returns an ordinary
    result, because it *did* answer.
    """
    # Traced here because this is where every tool call's outcome is known --
    # built-in, user-defined and external alike. ``outcome`` is a string rather
    # than a flag for the same reason ``stopped_by`` is on a step span: "it
    # failed" and "it was refused" are different questions (AGT-026, AGT-029).
    with telemetry.span(f"tool {name}", tool=name) as current:
        try:
            content, blocked = await call
        except _ToolFailure as failure:
            telemetry.set_attributes(
                current,
                outcome="error",
                # A tool's own error text: the rate limit, the quota, the
                # upstream message. Content, so opt-in.
                error_text=telemetry.content(json.dumps(failure.payload, default=str), 400),
            )
            return text_response(failure.payload), None, True
        except Exception as exc:
            logger.exception("Unhandled error calling MCP tool %s", name)
            telemetry.set_attributes(current, outcome="error", error_type=exc.__class__.__name__)
            return text_response({"error": f"Failed to execute tool '{name}'"}), None, True
        telemetry.set_attributes(current, outcome=blocked.value if blocked is not None else "ok")
        return content, blocked, False


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
    external_tool_annotations: ToolAnnotations | None = None,
) -> tuple[list[TextContent], ChatBlockReason | None]:
    args = arguments or {}
    perms = _permissions(current_user, permissions)
    if gate_permission and gate_permission.value not in perms:
        return (
            text_response({"error": f"Permission denied: {gate_permission.value}"}),
            ChatBlockReason.PERMISSION_DENIED,
        )

    external = external_mcp.parse_namespaced_tool_name(name)
    if external is not None:
        proxy, remote_name = external
        if not chat_safe_only:
            return (
                text_response({"error": f"Tool '{name}' is only available to the Seizu agent"}),
                ChatBlockReason.NOT_AVAILABLE,
            )
        if current_user is None:
            return (
                text_response({"error": "External MCP tools require an authenticated user"}),
                ChatBlockReason.PERMISSION_DENIED,
            )

        needs_confirmation = external_mcp.tool_requires_confirmation(
            proxy,
            remote_name,
            external_tool_annotations,
        )
        if needs_confirmation and bypass_confirmations:
            if Permission.CHAT_BYPASS_PERMISSIONS.value not in perms:
                return text_response(
                    {"error": f"Permission denied: {Permission.CHAT_BYPASS_PERMISSIONS.value}"}
                ), ChatBlockReason.PERMISSION_DENIED
            logger.info(
                "External MCP action confirmation bypassed",
                extra={
                    "type": "AUDIT",
                    "tool": name,
                    "proxy": proxy.name,
                    "user": current_user.user.user_id,
                    "source": "bypass",
                },
            )
        elif needs_confirmation and confirmation_pre_approved:
            pass
        elif needs_confirmation and confirmation_source is None:
            logger.warning(
                "Refused external MCP tool reached without a confirmation source",
                extra={"type": "AUDIT", "tool": name, "proxy": proxy.name},
            )
            return text_response(
                {"error": f"Tool '{name}' requires action confirmation, which is unavailable in this context"}
            ), ChatBlockReason.PERMISSION_DENIED
        elif needs_confirmation:
            if confirmation_session_key is None:
                return text_response(
                    {"error": f"Tool '{name}' requires a session key for confirmation"}
                ), ChatBlockReason.PERMISSION_DENIED
            assert confirmation_source is not None
            confirmation = await action_confirmations.ensure_confirmation(
                user_id=current_user.user.user_id,
                source=confirmation_source,
                session_key=confirmation_session_key,
                tool_name=name,
                target=ActionConfirmationTarget(
                    action="call",
                    resource_type="external_mcp_tool",
                    resource_id=name,
                ),
                arguments=args,
                batch_id=confirmation_batch_id,
            )
            if confirmation is not None:
                if confirmation.status == "executed":
                    return text_response(
                        {"notice": f"Tool '{name}' was already executed by a concurrent request."}
                    ), None
                payload = action_confirmations.confirmation_required_payload(confirmation)
                if confirmation.status == "denied":
                    payload["error"] = "Action was denied for this confirmation window"
                return text_response(payload), ChatBlockReason.CONFIRMATION_REQUIRED

        try:
            result = await external_mcp.call_tool(
                proxy,
                remote_name,
                args,
                current_user,
                max_bytes=_effective_limits(result_max_rows, result_max_bytes).max_bytes,
            )
        except external_mcp.ExternalMCPAuthenticationRequired as exc:
            return text_response(external_mcp.authentication_payload(exc)), ChatBlockReason.AUTHENTICATION_REQUIRED
        except external_mcp.ExternalMCPError as exc:
            raise _ToolFailure({"error": str(exc)}) from exc
        if result.is_error:
            raise _ToolFailure({"error": result.text})
        return [TextContent(type="text", text=result.text)], None

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
        _validate_arguments(builtin.input_schema, args)
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
            #
            # Ask the resolver first: several resolvers gate only some argument
            # shapes (reports__create gates on filing into a space,
            # reports__update_visibility only when it carries an access change),
            # and refusing a call the resolver would have waved through would deny
            # the safe shape for no gain. A resolver that returns a target here
            # still gets refused, because there is nobody to approve it.
            if await builtin.confirmation(args, current_user) is not None:
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
            # Publish the row cap first. _bounded_text_response only trims what a
            # handler already built, so a broad query would be fully fetched and
            # serialized before any limit applied -- fast, unbounded, and
            # reachable by any authenticated caller. A handler that reads this
            # can stop at the source instead.
            # One set of limits, used twice: as the source bound a handler can
            # stream to, and as the bound on the response actually emitted.
            # Publishing them only to the context var left the final call using
            # the caller's raw arguments -- None for a normal MCP call -- so
            # everything except graph__query came back unbounded, and even it
            # was bounded by row count at the source rather than by the size of
            # the response that was sent.
            limits = _effective_limits(result_max_rows, result_max_bytes)
            token = set_current_result_limits(limits)
            try:
                result = await builtin.handler(args, current_user)
            finally:
                reset_current_result_limits(token)
            return (
                _bounded_text_response(
                    result,
                    max_rows=limits.max_rows,
                    max_bytes=limits.max_bytes,
                    collection_key=builtin.collection_key,
                ),
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

        # The schema this tool advertised in tools/list is the only gate, which
        # is what the SDK checked before 2.0. It is stricter than the store's
        # validate_tool_arguments for every type the two disagree on -- that one
        # accepts "5", "false" and the like, then passes the *original* string
        # to Cypher -- except for an integral float, which is normalized below
        # rather than refused, since the advertised schema permits it.
        _validate_arguments(build_input_schema(target_tool.parameters), args)

        params_with_defaults = {p.name: p.default for p in target_tool.parameters}
        params_with_defaults.update(args)
        params_with_defaults = _normalize_arguments(target_tool.parameters, params_with_defaults)

        # A chat caller states its bounds; anyone else gets the MCP contract,
        # which is far looser. Inheriting the chat caps here silently truncated
        # ordinary MCP calls at 100 rows and contradicted their documented
        # behaviour.
        limits = _effective_limits(result_max_rows, result_max_bytes)
        serialized, reason = await reporting_neo4j.run_query_streamed(
            target_tool.cypher,
            params_with_defaults,
            max_rows=limits.max_rows,
            max_bytes=limits.max_bytes,
            serialize=lambda record: {key: _serialize_neo4j_value(value) for key, value in record.items()},
        )
        # Keep the un-truncated shape a bare list, which is what MCP clients
        # consume; only a truncated result gains the envelope. The marker names
        # the bound that actually stopped it -- reporting a byte stop as a row
        # limit points a client at the wrong remedy.
        bounded: Any = (
            _rebuild(serialized, None, serialized, stream_truncation(reason, serialized, limits).fields())
            if reason
            else serialized
        )
        return (
            _bounded_text_response(
                bounded,
                max_rows=limits.max_rows,
                max_bytes=limits.max_bytes,
                # Built a few lines up, so the key is known rather than guessed.
                collection_key=_USER_TOOL_ROWS_KEY,
            ),
            None,
        )
    except _ToolFailure:
        # A rejected argument is not an execution failure to be relabelled;
        # let it reach _guarded, which reports it with is_error.
        raise
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


SKILL_TOOLS_META_KEY = "seizu_tools_required"


def declared_tool_names(prompts: list[Prompt], only: set[str] | None = None) -> frozenset[str]:
    """Tools the listed skills declare they need, from the listing itself.

    A skill's ``tools_required`` is its author stating exactly which tools the
    workflow uses, so it is an authoritative disclosure list -- there is nothing
    to learn by waiting for the skill to render before honouring it. Waiting
    costs real money: the tool list heads the provider's cached prefix, so
    unlocking tools mid-turn invalidates everything behind them (measured:
    3 -> 11 tools made the next call read 0 of 4,853 cacheable tokens).

    Read off the prompts the turn already listed rather than re-reading the
    store, so this stays within the one-listing-per-turn rule -- and inherits
    that listing's permission gating for free. This is a *disclosure* set, not
    an authorization one: every call still passes the same RBAC checks.

    ``only`` restricts it to named skills, which is how a caller keeps the set
    relevant. Every enabled skill's declaration unioned together is not "what
    this turn needs" but "what the catalogue can do": measured on one
    deployment, a CVE question would carry all 23 skill-authoring tools, taking
    a single-agent turn from 1 bound tool (343 tokens) to 43 (4,666).
    """
    names: set[str] = set()
    for prompt in prompts:
        if only is not None and prompt.name not in only:
            continue
        meta = getattr(prompt, "meta", None)
        required = meta.get(SKILL_TOOLS_META_KEY) if isinstance(meta, dict) else None
        if isinstance(required, list):
            names.update(str(name) for name in required)
    return frozenset(names)


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
                    # Carried on the listing so a caller can honour the author's
                    # declaration without a second store read. The attribute is
                    # `meta`, aliased to the wire's `_meta`.
                    meta={SKILL_TOOLS_META_KEY: list(skill.tools_required or ())},
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


# Keys under which a tool result carries its rows. ``graph__query`` returns
# ``{"results": [...], "warnings": [...]}``, and user-defined tools follow the
# same shape, so treating only a top-level list as rows meant the row cap never
# applied to the tools most likely to return thousands of them.
def _effective_limits(max_rows: int | None, max_bytes: int | None) -> ResultLimits:
    """What actually bounds this call.

    A chat caller states its own, far tighter, bounds. Anyone else gets the MCP
    contract rather than nothing: a caller passing no limits is an MCP client,
    not a request to be unbounded.
    """
    # `is not None`, not truthiness: the streaming helper defines 0 as
    # unbounded, so a caller explicitly disabling a dimension must not have the
    # MCP default quietly put back in its place.
    if max_rows is not None or max_bytes is not None:
        return ResultLimits(max_rows=max_rows, max_bytes=max_bytes)
    return ResultLimits.for_mcp()


# The field the runtime puts rows in when it builds an envelope itself: a
# user-defined tool's result, and the truncation envelope around it. Named once
# so the builder and the limiter cannot disagree about it.
#
# This replaced a tuple of names to match against -- results, rows, records,
# items, data -- of which only the first was ever produced here. The rest could
# only ever have fired on a payload that happened to use the word, which is the
# same accident that had `permissions` on a role treated as rows. Every caller
# now states its key instead.
_USER_TOOL_ROWS_KEY = "results"


def _payload_rows_and_key(payload: Any, collection_key: str | None = None) -> tuple[list[Any] | None, str | None]:
    """The rows in a tool result, and the key holding them if it is a mapping.

    ``collection_key`` is stated by the caller -- a built-in declares it, and
    the user-defined tool path builds its own envelope and so knows it outright.
    Nothing is matched by name.

    A payload whose key is not stated is not row-bounded: returned whole or
    refused whole, never silently shortened in the wrong place.
    """
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        if collection_key:
            value = payload.get(collection_key)
            if isinstance(value, list):
                return value, collection_key
    return None, None


def _rebuild(payload: Any, key: str | None, rows: list[Any], marker: dict[str, Any]) -> Any:
    """Put capped rows back where they came from, keeping the rest of the payload.

    A mapping result carries more than its rows -- ``graph__query`` returns
    validator ``warnings`` alongside them -- and replacing the whole payload with
    a bare rows envelope would discard that silently at exactly the moment the
    caller is being told something was cut.
    """
    if key is None:
        return {_USER_TOOL_ROWS_KEY: rows, **marker}
    return {**payload, key: rows, **marker}


def _bounded_text_response(
    payload: Any,
    *,
    max_rows: int | None,
    max_bytes: int | None,
    collection_key: str | None = None,
) -> list[TextContent]:
    """Serialize a tool result for chat, bounding by rows then bytes.

    Over the row cap, keep the first ``max_rows`` rows. Over the byte cap, shed
    whole rows from the end until the JSON fits, so the caller still gets as
    many complete rows as possible (with a truncation marker) instead of
    nothing — only falling back to an error marker when not even one row fits
    (a single oversized row, or a non-list payload that can't be row-shed).
    """
    rows, row_key = _payload_rows_and_key(payload, collection_key)
    prior = _prior_truncation(payload)

    def render(kept: list[Any], state: Truncation) -> Any:
        return _rebuild(payload, row_key, kept, state.fields())

    if rows is None:
        # Nothing row-shaped to shorten: emit or fail whole.
        text = json.dumps(payload, indent=2, default=str)
        if max_bytes is None or max_bytes <= 0 or json_size_bytes(payload, indent=2) <= max_bytes:
            return [TextContent(type="text", text=text)]
        return _emit(_byte_limit_error(max_bytes))

    # A result can be cut twice: once at the source, and again here when the
    # assembled payload -- indentation, envelope, sibling fields -- exceeds the
    # byte budget. Carry what the source reported rather than overwriting it.
    base = Truncation(
        reasons=tuple(prior),
        returned=len(rows),
        source_rows=len(rows),
        source_complete=not prior,
    )
    capped = rows
    state = base
    if max_rows is not None and max_rows > 0 and len(rows) > max_rows:
        capped = rows[:max_rows]
        state = base.with_reason("row_limit", max_rows=max_rows)
        state = Truncation(
            reasons=state.reasons,
            returned=len(capped),
            source_rows=base.source_rows,
            source_complete=base.source_complete,
            max_rows=max_rows,
            max_bytes=state.max_bytes,
        )

    bounded: Any = render(capped, state) if state.reasons else payload
    if max_bytes is None or max_bytes <= 0 or json_size_bytes(bounded, indent=2) <= max_bytes:
        return [TextContent(type="text", text=json.dumps(bounded, indent=2, default=str))]

    # Over the byte budget: shed whole rows. The search must measure exactly
    # what will be emitted -- sizing a smaller envelope than the final one is
    # how responses came to exceed the budget the search exists to enforce.
    def render_shed(kept: list[Any]) -> Any:
        # Do not repeat a reason the source already reported. The streamer counts
        # compact bytes while the response is sized indented, so a byte-stopped
        # read routinely exceeds the budget again here -- listing "byte_limit"
        # twice describes one cause as two.
        shed_reasons = state.reasons if state.reasons[-1:] == ("byte_limit",) else (*state.reasons, "byte_limit")
        return render(
            kept,
            Truncation(
                reasons=shed_reasons,
                returned=len(kept),
                source_rows=base.source_rows,
                source_complete=base.source_complete,
                max_rows=state.max_rows,
                max_bytes=max_bytes,
            ),
        )

    keep = largest_prefix_within_bytes(capped, max_bytes=max_bytes, envelope=render_shed, indent=2)
    if keep <= 0:
        return _emit(_byte_limit_error(max_bytes))
    return _emit(render_shed(capped[:keep]))


def _prior_truncation(payload: Any) -> list[str]:
    """Reasons a payload was already truncated before it reached this bound."""
    if not isinstance(payload, dict) or not payload.get("truncated"):
        return []
    reasons = payload.get("truncated_reasons")
    if isinstance(reasons, list) and reasons:
        return [str(reason) for reason in reasons]
    single = payload.get("truncated_reason")
    return [str(single)] if single else ["unknown"]


def _byte_limit_error(max_bytes: int) -> dict[str, Any]:
    """Said when not even one row fits.

    This is the one response that can exceed ``max_bytes``: it is a fixed
    message, and a budget smaller than the message leaves nothing to shorten.
    Every response carrying data is bounded exactly. Callers configuring a
    budget in the low hundreds of bytes should expect this floor.
    """
    return {
        "error": "Tool result exceeded chat size limit",
        "truncated": True,
        "truncated_reasons": ["byte_limit"],
        "max_bytes": max_bytes,
    }


def _emit(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]
