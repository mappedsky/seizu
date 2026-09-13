"""Interactive external MCP continuations; responses travel through protocol input."""

import json
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from mcp.types import ElicitRequest, ElicitRequestFormParams, ElicitRequestURLParams, ElicitResult, InputRequiredResult

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.chat_elicitations import ChatElicitation, ElicitationResponse, validate_schema
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.services import action_confirmations, external_mcp_connections, report_store
from reporting.services.external_mcp_elicitation import MAX_ELICITATIONS, safe_elicitation
from reporting.services.report_store import elicitations as store
from reporting.services.report_store.sql import ChatElicitationRecord

form_capability: ContextVar[bool] = ContextVar("elicitation_form_capability", default=False)
replay: ContextVar[ChatElicitationRecord | None] = ContextVar("elicitation_replay", default=None)
worker_config: ContextVar[RunnableConfig | None] = ContextVar("elicitation_worker_config", default=None)
worker_step_id: ContextVar[str | None] = ContextVar("elicitation_worker_step_id", default=None)


class InputRequired(Exception):
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__("External tool requires user input")


def interactive_context() -> tuple[str, str] | None:
    from reporting.services.chat_graph import _client_thread_id_from_config, _headless_from_config, turn_id_from_config

    try:
        config = worker_config.get()
        if config is None:
            config = get_config()
    except RuntimeError:
        return None
    thread_id = _client_thread_id_from_config(config)
    turn_id = turn_id_from_config(config)
    if _headless_from_config(config) or not thread_id or not turn_id:
        return None
    return thread_id, turn_id


def enabled(proxy: ExternalMCPProxy) -> bool:
    return settings.MCP_EXTERNAL_ELICITATION_ENABLED and interactive_context() is not None


def visible(item: ChatElicitation) -> bool:
    from reporting.services import external_mcp

    parsed = external_mcp.parse_namespaced_tool_name(item.tool_name)
    if not settings.MCP_EXTERNAL_ELICITATION_ENABLED or parsed is None:
        return False
    proxy = parsed[0]
    if item.proxy_fingerprint != external_mcp_connections.fingerprint(proxy):
        return False
    if item.kind == "form":
        return proxy.elicitation.form
    return (
        proxy.elicitation.url
        and item.url is not None
        and safe_elicitation(
            proxy,
            ElicitRequestURLParams(mode="url", url=item.url, elicitation_id=item.elicitation_id, message=item.message),
        )
        is not None
    )


async def park(
    proxy: ExternalMCPProxy,
    remote_name: str,
    arguments: dict[str, Any],
    user: CurrentUser,
    result: InputRequiredResult,
    *,
    delegated: bool = False,
) -> list[str]:
    context = interactive_context()
    if context is None:
        raise ValueError("Interactive context unavailable")
    requests = result.input_requests or {}
    if not 1 <= len(requests) <= MAX_ELICITATIONS:
        raise ValueError("Invalid input request count")
    private = json.dumps(
        {
            "request_state": result.request_state,
            "arguments": arguments,
            "proxy": proxy.model_dump(mode="json"),
            "remote_name": remote_name,
            "delegated": delegated,
        }
    )
    if len(private.encode()) > 64_000:
        raise ValueError("Oversized continuation")
    now = datetime.now(UTC)
    group_id = report_store.generate_id()
    records = []
    for request_id, request in requests.items():
        if not isinstance(request, ElicitRequest):
            raise ValueError("Sampling and roots input requests are unsupported")
        params = request.params
        schema = None
        url = None
        if isinstance(params, ElicitRequestFormParams) and proxy.elicitation.form:
            schema = validate_schema(params.requested_schema)
            kind = "form"
        elif isinstance(params, ElicitRequestURLParams) and proxy.elicitation.url:
            safe = safe_elicitation(proxy, params)
            if safe is None:
                raise ValueError("Untrusted elicitation URL")
            kind = "url"
            url = safe.url
        else:
            raise ValueError("Elicitation kind is disabled")
        if len(params.message) > 1000 or len(str(request_id)) > 256:
            raise ValueError("Oversized input request")
        item = ChatElicitation(
            elicitation_id=report_store.generate_id(),
            group_id=group_id,
            thread_id=context[0],
            turn_id=context[1],
            proxy_name=proxy.name,
            proxy_fingerprint=external_mcp_connections.fingerprint(proxy),
            tool_name=f"ext__{proxy.name}__{remote_name}",
            kind=kind,
            message=params.message,
            requested_schema=schema,
            url=url,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=max(1, settings.CHAT_ELICITATION_TTL_SECONDS))).isoformat(),
        )
        records.append(
            ChatElicitationRecord(
                elicitation_id=item.elicitation_id,
                group_id=group_id,
                user_id=user.user.user_id,
                thread_id=item.thread_id,
                turn_id=item.turn_id,
                created_at=item.created_at,
                expires_at=item.expires_at,
                public_json=item.model_dump_json(),
                arguments_hash=action_confirmations.arguments_hash(arguments),
                step_id=worker_step_id.get(),
                private_json=json.dumps({**json.loads(private), "request_id": request_id}),
            )
        )
    ids = [record.elicitation_id for record in records]
    await store.create(records)
    return ids


def resume_id(messages: list[Any]) -> str | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            value = message.additional_kwargs.get("resume_elicitation_id")
            return value if isinstance(value, str) else None
    return None


async def resume(elicitation_id: str, user: CurrentUser, thread_id: str | None) -> tuple[str, str]:
    """Return wait/run/abort and safe tool output after current authorization."""
    from reporting.services import external_mcp, mcp_runtime

    record = await store.get(elicitation_id, user.user.user_id)
    if record is None or record.thread_id != thread_id:
        return "abort", "Input request not found."
    rows = await store.group(record)
    statuses = [store.public(row).status for row in rows]
    if any(status in {"expired", "consumed"} for status in statuses):
        return "abort", "This input request has expired or was already consumed. Do not retry."
    if "pending" in statuses:
        return "wait", "Please answer the remaining input requests."
    if record.confirmation_id:
        confirmation = await report_store.get_action_confirmation(record.confirmation_id, user_id=user.user.user_id)
        if confirmation is None or action_confirmations.is_expired(confirmation) or confirmation.status == "denied":
            return "abort", "The required action approval was denied or expired. Do not retry."
    private = json.loads(record.private_json)
    parsed = external_mcp.parse_namespaced_tool_name(store.public(record).tool_name)
    if parsed is None or parsed[0].model_dump(mode="json") != private["proxy"] or not enabled(parsed[0]):
        return "abort", "The external proxy configuration changed or elicitation is disabled."
    if Permission.CHAT_TOOLS_CALL.value not in user.permissions:
        return "abort", "Permission denied: chat:tools:call"
    if private.get("delegated"):
        if any(status != "accepted" for status in statuses):
            await store.group(record, claim=True)
            return "abort", "The delegated input request was declined or cancelled. Do not retry."
        return (
            "run",
            "The owner answered the external input request. A new sandbox delegation can continue the task "
            "using the existing sandbox files and receipts. The previous delegation's loop has ended.",
        )
    try:
        tools = await external_mcp.list_proxy_tools(parsed[0], user)
    except external_mcp.ExternalMCPError:
        return "abort", "The external tool inventory is unavailable."
    tool = next((tool for tool in tools if tool.name == store.public(record).tool_name), None)
    if tool is None:
        return "abort", "The external tool is no longer available to this user."
    token = replay.set(record)
    try:
        outcome = await mcp_runtime.call_tool_for_chat(
            user,
            store.public(record).tool_name,
            private["arguments"],
            gate_permission=Permission.CHAT_TOOLS_CALL,
            chat_safe_only=True,
            include_chat_only=True,
            confirmation_source="chat",
            confirmation_session_key=thread_id,
            result_max_bytes=settings.CHAT_TOOL_RESULT_MAX_BYTES,
            external_tool_annotations=tool.annotations,
        )
    finally:
        replay.reset(token)
    if outcome.blocked == mcp_runtime.ChatBlockReason.INPUT_REQUIRED:
        return "wait", outcome.text
    if outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED:
        payload = json.loads(outcome.text)
        if payload.get("status") in {"denied", "expired"}:
            return "abort", "The required action approval was denied or expired. Do not retry."
        await store.bind_confirmation(record, payload["confirmation_id"])
        return "wait", outcome.text
    if outcome.blocked is not None:
        return "abort", outcome.text
    from reporting.services.chat_graph import _tool_result_error_text

    if _tool_result_error_text(outcome.text):
        return "abort", outcome.text
    if any(status != "accepted" for status in statuses):
        return "abort", "The external input request was declined or cancelled. Do not retry."
    return "run", outcome.text


async def resume_detail(elicitation_id: str, user: CurrentUser, kind: str, output: str) -> dict[str, Any] | None:
    """Describe a resumed call using the IDs of its original input group."""
    record = await store.get(elicitation_id, user.user.user_id)
    if record is None:
        return None
    rows = await store.group(record)
    return {
        "kind": "tool",
        "title": f"Tool: {store.public(record).tool_name}",
        "status": {"run": "completed", "wait": "awaiting", "abort": "blocked"}[kind],
        "body": output[:6000],
        "elicitation_ids": [row.elicitation_id for row in rows],
        "elicitation_resumed": True,
    }


async def continuation(
    proxy: ExternalMCPProxy,
    remote_name: str,
    arguments: dict[str, Any],
    user: CurrentUser,
    *,
    delegated: bool = False,
) -> dict[str, Any]:
    record = replay.get()
    if record is None and delegated and enabled(proxy):
        context = interactive_context()
        assert context is not None
        candidates = await store.matching_delegation(
            user.user.user_id, context[0], action_confirmations.arguments_hash(arguments)
        )
        for candidate in candidates:
            saved = json.loads(candidate.private_json)
            if (
                saved.get("delegated")
                and saved["proxy"] == proxy.model_dump(mode="json")
                and saved["remote_name"] == remote_name
            ):
                record = candidate
                break
    if record is None:
        return {}
    private = json.loads(record.private_json)
    if (
        record.user_id != user.user.user_id
        or private["proxy"] != proxy.model_dump(mode="json")
        or private["remote_name"] != remote_name
        or private["arguments"] != arguments
    ):
        raise ValueError("Continuation scope mismatch")
    rows = await store.group(record, claim=True)
    if not rows:
        raise ValueError("Continuation already consumed or unavailable")
    responses = [(row, ElicitationResponse.model_validate_json(row.response_json or "{}")) for row in rows]
    return {
        "request_state": private["request_state"],
        "input_responses": {
            json.loads(row.private_json)["request_id"]: ElicitResult(**response.model_dump())
            for row, response in responses
        },
    }
