import dataclasses
import json

from mcp.types import ToolAnnotations

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.mcp_config import SkillItem, ToolItem, ToolParamDef
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion, User
from reporting.services import action_confirmations, external_mcp, mcp_runtime, report_store

_NOW = "2024-01-01T00:00:00+00:00"
_LATER = "2099-01-01T00:30:00+00:00"


def _user(permissions: frozenset[str]) -> CurrentUser:
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
        permissions=permissions,
    )


def _tool() -> ToolItem:
    return ToolItem(
        tool_id="lookup",
        toolset_id="security",
        name="Lookup",
        description="Lookup security data",
        cypher="MATCH (n) RETURN n LIMIT $limit",
        parameters=[
            ToolParamDef(
                name="limit",
                type="integer",
                description="Maximum rows",
                required=False,
                default=1,
            )
        ],
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user-1",
    )


def _external_proxy(*, require_confirmation: bool = True) -> ExternalMCPProxy:
    return ExternalMCPProxy(
        name="drive",
        url="https://proxy.example/sse",
        auth_mode="header_delegation",
        header_mappings={"user_id": "X-Forwarded-User"},
        require_confirmation=require_confirmation,
    )


def _skill() -> SkillItem:
    return SkillItem(
        skill_id="summarize",
        skillset_id="security",
        name="Summarize",
        description="Summarize a topic",
        template="Summarize {% $topic %}.",
        parameters=[ToolParamDef(name="topic", type="string", required=True)],
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user-1",
    )


def _report_list_item() -> ReportListItem:
    return ReportListItem(
        report_id="report-1",
        name="My Report",
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user-1",
        updated_by="user-1",
        access=ReportAccess(scope="private"),
    )


def _report_version_in_space() -> ReportVersion:
    """A report filed in a space -- so a clone of it would be published."""
    return ReportVersion(
        report_id="r1",
        name="Member",
        version=1,
        config={"name": "Member", "rows": []},
        created_at=_NOW,
        created_by="user-1",
        report_created_by="user-1",
        report_updated_by="user-1",
        access=ReportAccess(scope="public"),
        space_id="sp1",
    )


def _confirmation(status: str = "pending") -> ActionConfirmation:
    return ActionConfirmation.model_validate(
        {
            "confirmation_id": "confirm-1",
            "user_id": "user-1",
            "source": "mcp",
            "session_key": "session-1",
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "r1",
            "arguments": {"report_id": "r1"},
            "arguments_hash": action_confirmations.arguments_hash({"report_id": "r1"}),
            "status": status,
            "created_at": _NOW,
            "expires_at": _LATER,
        }
    )


async def test_chat_tool_gate_blocks_listing_before_store_lookup(mocker):
    list_enabled_tools = mocker.patch("reporting.services.mcp_runtime.report_store.list_enabled_tools")
    current = _user(frozenset({Permission.TOOLS_CALL.value}))

    tools = await mcp_runtime.list_tools_for_user(
        current,
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert tools == []
    list_enabled_tools.assert_not_called()


async def test_chat_listing_includes_namespaced_external_tools(mocker):
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))
    external_tool = mcp_runtime.Tool(
        name="ext__drive__search",
        description="Search files",
        input_schema={"type": "object"},
    )
    list_external = mocker.patch.object(
        external_mcp,
        "list_tools_for_user",
        mocker.AsyncMock(return_value=[external_tool]),
    )

    tools = await mcp_runtime.list_tools_for_user(
        current,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    assert external_tool in tools
    list_external.assert_awaited_once_with(current, exclude_confirmation_gated=False)


async def test_external_tool_requires_confirmation_by_default(mocker):
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))
    proxy = _external_proxy()
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(proxy, "search"))
    confirmation = _confirmation()
    confirmation = confirmation.model_copy(
        update={"source": "chat", "tool_name": "ext__drive__search", "session_key": "thread-1"}
    )
    ensure = mocker.patch.object(
        action_confirmations,
        "ensure_confirmation",
        mocker.AsyncMock(return_value=confirmation),
    )
    call = mocker.patch.object(external_mcp, "call_tool", mocker.AsyncMock())

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "ext__drive__search",
        {"query": "budget"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="thread-1",
    )

    assert result.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED
    ensure.assert_awaited_once()
    call.assert_not_awaited()


async def test_external_read_only_tool_executes_with_current_user(mocker):
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))
    proxy = _external_proxy(require_confirmation=False)
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(proxy, "search"))
    call = mocker.patch.object(
        external_mcp,
        "call_tool",
        mocker.AsyncMock(return_value=external_mcp.ExternalToolResult("files found")),
    )

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "ext__drive__search",
        {"query": "budget"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        result_max_bytes=500,
    )

    assert result == mcp_runtime.ChatActionOutcome(text="files found")
    call.assert_awaited_once_with(proxy, "search", {"query": "budget"}, current, max_bytes=500)


async def test_external_read_only_annotation_overrides_confirmation_fallback(mocker):
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))
    proxy = _external_proxy(require_confirmation=True)
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(proxy, "search"))
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_CONFIRMATION_REQUIRED_TOOLS", [])
    call = mocker.patch.object(
        external_mcp,
        "call_tool",
        mocker.AsyncMock(return_value=external_mcp.ExternalToolResult("files found")),
    )

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "ext__drive__search",
        {"query": "budget"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        external_tool_annotations=ToolAnnotations(read_only_hint=True),
    )

    assert result == mcp_runtime.ChatActionOutcome(text="files found")
    call.assert_awaited_once()


async def test_external_oauth_challenge_is_a_structured_chat_block(mocker):
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))
    proxy = _external_proxy(require_confirmation=False)
    mocker.patch.object(external_mcp, "parse_namespaced_tool_name", return_value=(proxy, "search"))
    mocker.patch.object(
        external_mcp,
        "call_tool",
        mocker.AsyncMock(
            side_effect=external_mcp.ExternalMCPAuthenticationRequired(
                external_mcp.OAuthChallenge("drive", "https://proxy.example/.well-known/mcp")
            )
        ),
    )

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "ext__drive__search",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    assert result.blocked == mcp_runtime.ChatBlockReason.AUTHENTICATION_REQUIRED
    payload = json.loads(result.text)
    assert payload["authentication_required"] is True
    assert payload["resource_metadata"] == "https://proxy.example/.well-known/mcp"


async def test_chat_tool_gate_blocks_call_before_store_lookup(mocker):
    get_enabled_tool = mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool")
    current = _user(frozenset({Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert json.loads(result.content[0].text) == {"error": "Permission denied: chat:tools:call"}
    get_enabled_tool.assert_not_called()


async def test_tool_call_still_requires_underlying_mcp_permission(mocker):
    get_enabled_tool = mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool")
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert json.loads(result.content[0].text) == {"error": "Permission denied: tools:call"}
    get_enabled_tool.assert_not_called()


async def test_user_defined_tool_listing_requires_tools_call_permission(mocker):
    list_enabled_tools = mocker.patch(
        "reporting.services.mcp_runtime.report_store.list_enabled_tools",
        return_value=[_tool()],
    )
    current = _user(frozenset({Permission.TOOLSETS_READ.value}))

    tools = await mcp_runtime.list_tools_for_user(current)

    assert "security__lookup" not in {tool.name for tool in tools}
    list_enabled_tools.assert_not_called()


async def test_chat_tool_call_uses_mcp_acl_and_executes_user_defined_tool(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    run_query = mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed",
        return_value=([{"name": "node-1"}], False),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {"limit": 3},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    # Parameters are positional now, and the row bound travels with the call so
    # the query stops at the source instead of being trimmed after the fact.
    assert run_query.await_args.args[:2] == ("MATCH (n) RETURN n LIMIT $limit", {"limit": 3})
    assert run_query.await_args.kwargs["max_rows"] > 0
    assert json.loads(result.content[0].text) == [{"name": "node-1"}]


async def test_chat_tool_call_surfaces_neo4j_error_without_stacktrace(mocker):
    import neo4j.exceptions

    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    err = neo4j.exceptions.ClientError("Expected parameter(s): cve_id, limit")
    # A server-hydrated Neo4jError exposes the human-readable text via .message
    # (backed by ._message); set it to mirror what the driver returns.
    err._message = "Expected parameter(s): cve_id, limit"
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed",
        side_effect=err,
    )
    log = mocker.patch("reporting.services.mcp_runtime.logger")
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {"limit": 3},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    data = json.loads(result.content[0].text)
    # The database message is surfaced so the caller can see why the tool failed.
    assert "Expected parameter(s): cve_id, limit" in data["error"]
    # Logged concisely (warning), not as a full-traceback ERROR.
    log.warning.assert_called_once()
    log.exception.assert_not_called()


async def test_chat_tool_call_applies_row_limit(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed",
        return_value=([{"name": "node-1"}, {"name": "node-2"}], False),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_rows=1,
    )

    assert json.loads(result.content[0].text) == {
        "results": [{"name": "node-1"}],
        "truncated": True,
        "truncated_reasons": ["row_limit"],
        "returned": 1,
        # The source ran to completion, so this total is real.
        "total_rows": 2,
        "max_rows": 1,
    }


async def test_chat_tool_call_applies_byte_limit(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed",
        return_value=([{"name": "x" * 100}], False),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_bytes=20,
    )

    assert json.loads(result.content[0].text) == {
        "error": "Tool result exceeded chat size limit",
        "truncated": True,
        "truncated_reasons": ["byte_limit"],
        "max_bytes": 20,
    }


async def test_chat_tool_call_byte_limit_sheds_rows(mocker):
    rows = [{"v": "x" * 50} for _ in range(12)]
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch("reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed", return_value=(rows, False))
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_bytes=500,
    )

    data = json.loads(result.content[0].text)
    # Graceful: keep as many whole rows as fit rather than discarding everything.
    assert data["truncated_reasons"] == ["byte_limit"]
    assert data["total_rows"] == 12
    assert 1 <= len(data["results"]) < 12
    assert data["returned"] == len(data["results"])
    assert len(result.content[0].text.encode("utf-8")) <= 500


async def test_chat_safe_tool_listing_includes_create_write_builtins(mocker):
    """Chat lists both: create is safe without confirmation for the private case,
    and clone is listed because a confirmation resolver IS the safety gate."""
    mocker.patch("reporting.services.mcp_runtime.report_store.list_enabled_tools", return_value=[])
    current = _user(
        frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_READ.value,
                Permission.REPORTS_WRITE.value,
            }
        )
    )

    tools = await mcp_runtime.list_tools_for_user(
        current,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    names = {tool.name for tool in tools}
    assert "reports__list" in names
    assert "reports__create" in names
    assert "reports__clone" in names


async def test_chat_safe_tool_call_allows_create_write_builtin_without_confirmation(mocker):
    mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.create_report",
        return_value=_report_list_item(),
    )
    current = _user(
        frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_WRITE.value,
            }
        )
    )

    result = await mcp_runtime.call_tool_for_user(
        current,
        "reports__create",
        {"name": "My Report"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    data = json.loads(result.content[0].text)
    assert "error" not in data


async def test_chat_safe_tool_listing_includes_confirmation_gated_builtins(mocker):
    """Builtins with a confirmation callback are listed in chat (confirmation is the safety gate)."""
    mocker.patch("reporting.services.mcp_runtime.report_store.list_enabled_tools", return_value=[])
    current = _user(
        frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_READ.value,
                Permission.REPORTS_DELETE.value,
            }
        )
    )

    tools = await mcp_runtime.list_tools_for_user(
        current,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    names = {tool.name for tool in tools}
    assert "reports__list" in names
    assert "reports__delete" in names
    assert "reports__create" not in names  # no confirmation callback → still blocked


async def test_chat_safe_confirmation_gated_builtin_triggers_confirmation_not_blocked(mocker):
    """A builtin with a confirmation callback goes through the confirmation flow in chat, not NOT_AVAILABLE."""
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_DELETE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="session-1",
    )

    data = json.loads(outcome.text)
    assert data["confirmation_required"] is True
    assert outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED


async def test_confirmation_gated_builtin_fails_closed_without_confirmation_source(mocker):
    """A confirmation-gated builtin reached with neither a confirmation source nor
    bypass must be refused, not executed ungated. Both real entry points always
    pass a source; this backstops an internal caller (e.g. a subagent) that omits
    the confirmation context."""
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_DELETE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        # No confirmation_source and no bypass_confirmations: the gate cannot run.
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    assert "requires action confirmation" in json.loads(outcome.text)["error"]
    delete_report.assert_not_called()


async def test_conditionally_gated_tool_runs_when_its_resolver_declines(mocker):
    """reports__create is gated only when it publishes.

    Its resolver returns no target for a spaceless create, so the fail-closed
    guard must not refuse it: refusing would deny the safe shape (a private
    draft) that the no-confirmation exception exists for.
    """
    create_report = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.create_report",
        return_value=_report_list_item(),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_WRITE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__create",
        {"name": "Draft"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    assert outcome.blocked is None
    create_report.assert_called_once()


async def test_conditionally_gated_tool_is_refused_when_its_resolver_gates(mocker):
    """Creating into a space publishes, so with no one to approve it, refuse."""
    mocker.patch("reporting.services.report_store.get_space", return_value=object())
    create_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.create_report")
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_WRITE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__create",
        {"name": "Filed", "space_id": "sp1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    assert "requires action confirmation" in json.loads(outcome.text)["error"]
    create_report.assert_not_called()


async def test_creating_into_a_space_asks_for_confirmation(mocker):
    """With a confirmation source, the publishing shape becomes a pending ask."""
    mocker.patch("reporting.services.report_store.get_space", return_value=object())
    create_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.create_report")
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_WRITE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__create",
        {"name": "Filed", "space_id": "sp1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="session-1",
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED
    create_report.assert_not_called()


async def test_cloning_always_asks_for_confirmation(mocker):
    """Clone is gated unconditionally, source placement notwithstanding.

    A clone inherits the source's space, so whether it publishes depends on a
    read the resolver cannot share with the handler.
    """
    mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.get_report_latest",
        return_value=_report_version_in_space(),
    )
    create_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.create_report")
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_WRITE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__clone",
        {"report_id": "r1", "name": "Copy"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="session-1",
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED
    create_report.assert_not_called()


async def test_cloning_a_standalone_report_also_asks_for_confirmation(mocker):
    """No space anywhere in sight, and it is still gated -- that is the point."""
    mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.get_report_latest",
        return_value=_report_version_in_space().model_copy(update={"space_id": None}),
    )
    create_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.create_report")
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_WRITE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__clone",
        {"report_id": "r1", "name": "Copy"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="session-1",
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED
    create_report.assert_not_called()


async def test_pre_approved_confirmation_executes_without_gate(mocker):
    """The post-approval executor path: an already-approved, already-claimed
    confirmation runs the handler directly via confirmation_pre_approved, even with
    no confirmation_source. Without this, the fail-closed guard would block it."""
    delete_report = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.delete_report", return_value=True
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_DELETE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        include_chat_only=True,
        confirmation_pre_approved=True,
    )

    assert outcome.blocked is None
    delete_report.assert_called_once()


async def test_pre_approved_confirmation_requires_authenticated_user():
    """Pre-approved execution still needs an authenticated user to attribute the write."""
    outcome = await mcp_runtime.call_tool_for_chat(
        None,
        "reports__delete",
        {"report_id": "r1"},
        permissions=ALL_PERMISSIONS,
        chat_safe_only=True,
        include_chat_only=True,
        confirmation_pre_approved=True,
    )
    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED


async def test_list_tools_exclude_confirmation_gated_keeps_readonly_and_user_tools(mocker):
    """exclude_confirmation_gated drops only confirmation-gated builtins; read-only
    builtins, the no-confirmation exceptions, and user-defined tools all stay — so
    the sandbox subagent keeps an efficient toolset without any ungated mutators."""
    mocker.patch("reporting.services.mcp_runtime.report_store.list_enabled_tools", return_value=[_tool()])
    current = _user(ALL_PERMISSIONS)

    tools = await mcp_runtime.list_tools_for_user(
        current,
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        exclude_confirmation_gated=True,
    )

    names = {tool.name for tool in tools}
    assert "reports__delete" not in names  # confirmation-gated → excluded
    # Gated on every call, so listing it would only produce refusals.
    assert "reports__clone" not in names
    assert "graph__query" in names  # read-only → kept
    # Conditionally gated: the private-draft shape still runs, and the call-time
    # gate refuses the publishing one.
    assert "reports__create" in names
    assert "security__lookup" in names  # user-defined toolset tool → kept


async def test_unapproved_mutating_builtin_returns_confirmation_without_handler(mocker):
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    data = json.loads(result.content[0].text)
    assert data["confirmation_required"] is True
    assert data["confirmation_id"] == "confirm-1"
    delete_report.assert_not_called()


async def test_repeated_pending_mutating_builtin_reuses_confirmation_without_handler(mocker):
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    # First call: no approved/denied grant. Second call (pending dedup): existing pending found.
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.find_action_confirmation_grant",
        side_effect=[None, _confirmation()],
    )
    create_confirmation = mocker.patch("reporting.services.mcp_runtime.report_store.create_action_confirmation")
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    data = json.loads(result.content[0].text)
    assert data["confirmation_required"] is True
    assert data["confirmation_id"] == "confirm-1"
    delete_report.assert_not_called()
    create_confirmation.assert_not_called()


async def test_approved_mutating_builtin_executes_handler(mocker):
    delete_report = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.delete_report",
        return_value=True,
    )
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.find_action_confirmation_grant",
        return_value=_confirmation("approved"),
    )
    claim_confirmation = mocker.patch(
        "reporting.services.mcp_runtime.report_store.claim_action_confirmation_for_execution",
        return_value=_confirmation("executed"),
    )
    create_confirmation = mocker.patch("reporting.services.mcp_runtime.report_store.create_action_confirmation")
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    assert json.loads(result.content[0].text) == {"report_id": "r1"}
    delete_report.assert_awaited_once_with("r1", user_id="user-1")
    claim_confirmation.assert_awaited_once_with("confirm-1", "user-1")
    create_confirmation.assert_not_called()


async def test_builtin_handler_validation_error_returns_actionable_message(mocker):
    # A malformed argument (here a tools_required entry with an empty tool id)
    # should come back as an actionable validation message the model can fix,
    # not a generic "Failed to execute".
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.find_action_confirmation_grant",
        return_value=_confirmation("approved"),
    )
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.claim_action_confirmation_for_execution",
        return_value=_confirmation("executed"),
    )
    mocker.patch(
        "reporting.services.mcp_builtins.skillsets.report_store.get_skill",
        return_value=_skill(),
    )
    current = _user(frozenset({Permission.SKILLS_WRITE.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "skillsets__update_skill",
        {
            "skillset_id": "security",
            "skill_id": "summarize",
            "name": "Summarize",
            "template": "Summarize {% $topic %}.",
            "parameters": [{"name": "topic", "type": "string", "required": True}],
            "tools_required": ["toolsets__"],
        },
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    payload = json.loads(result.content[0].text)
    assert "Invalid arguments" in payload["error"]
    assert "tools_required" in payload["error"]


async def test_concurrent_claim_race_returns_notice_not_confirmation_required(mocker):
    """When another caller already claimed the approval, the response is a notice (not
    CONFIRMATION_REQUIRED) so the LLM does not retry and trigger a second execution."""
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.find_action_confirmation_grant",
        return_value=_confirmation("approved"),
    )
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.claim_action_confirmation_for_execution",
        return_value=None,
    )
    # Re-fetch shows "executed" — the concurrent caller won and ran the tool.
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.get_action_confirmation",
        return_value=_confirmation("executed"),
    )
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    assert result.blocked is None
    data = json.loads(result.text)
    assert "notice" in data
    assert "confirmation_required" not in data
    delete_report.assert_not_called()


async def test_missing_session_key_fails_closed_for_mutating_builtin(mocker):
    """Omitting confirmation_session_key while providing confirmation_source must block,
    not silently bypass the confirmation gate."""
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    create_confirmation = mocker.patch("reporting.services.mcp_runtime.report_store.create_action_confirmation")
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key=None,
    )

    assert result.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    delete_report.assert_not_called()
    create_confirmation.assert_not_called()


async def test_denied_mutating_builtin_blocks_handler(mocker):
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.find_action_confirmation_grant",
        return_value=_confirmation("denied"),
    )
    current = _user(frozenset({Permission.REPORTS_DELETE.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "reports__delete",
        {"report_id": "r1"},
        confirmation_source="mcp",
        confirmation_session_key="session-1",
    )

    data = json.loads(result.content[0].text)
    assert data["confirmation_required"] is True
    assert data["status"] == "denied"
    delete_report.assert_not_called()


async def test_builtin_call_validates_required_arguments_before_handler(mocker):
    get_skillset = mocker.patch("reporting.services.mcp_builtins.skillsets.report_store.get_skillset")
    current = _user(frozenset({Permission.SKILLS_READ.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "skillsets__list_skills",
        {},
    )

    # Wording follows the SDK's pre-2.0 check, which this replaced.
    assert json.loads(result.content[0].text) == {
        "error": "Input validation error: 'skillset_id' is a required property"
    }
    get_skillset.assert_not_called()


async def test_chat_skill_gate_blocks_listing_before_store_lookup(mocker):
    list_enabled_skills = mocker.patch("reporting.services.mcp_runtime.report_store.list_enabled_skills")
    current = _user(frozenset({Permission.SKILLS_RENDER.value}))

    prompts = await mcp_runtime.list_prompts_for_user(
        current,
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert prompts == []
    list_enabled_skills.assert_not_called()


async def test_chat_skill_listing_includes_triggers_in_description(mocker):
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.list_enabled_skills",
        return_value=[
            _skill().model_copy(
                update={
                    "triggers": [
                        "Investigate a GitHub organization",
                        "Investigate a specific GitHub repository",
                    ]
                }
            )
        ],
    )
    current = _user(frozenset({Permission.CHAT_SKILLS_CALL.value, Permission.SKILLS_RENDER.value}))

    prompts = await mcp_runtime.list_prompts_for_user(
        current,
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert prompts[0].description is not None
    assert "Summarize a topic" in prompts[0].description
    assert "trigger phrases" in prompts[0].description
    assert "Investigate a GitHub organization" in prompts[0].description
    assert "Investigate a specific GitHub repository" in prompts[0].description


async def test_skill_render_still_requires_underlying_mcp_permission(mocker):
    get_enabled_skill = mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_skill")
    current = _user(frozenset({Permission.CHAT_SKILLS_CALL.value}))

    result = await mcp_runtime.get_prompt_for_user(
        current,
        "security__summarize",
        {"topic": "alerts"},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert result.messages[0].content.text == "Permission denied: skills:render"
    get_enabled_skill.assert_not_called()


async def test_chat_skill_render_uses_mcp_acl_and_renders_prompt(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_skill", return_value=_skill())
    current = _user(frozenset({Permission.CHAT_SKILLS_CALL.value, Permission.SKILLS_RENDER.value}))

    result = await mcp_runtime.get_prompt_for_user(
        current,
        "security__summarize",
        {"topic": "alerts"},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert result.messages[0].content.text == "Summarize alerts."


# ---------------------------------------------------------------------------
# Chat-specific outcomes (structured block reasons replace string matching)
# ---------------------------------------------------------------------------


async def test_call_tool_for_chat_flags_permission_denied_with_enum(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool")
    current = _user(frozenset({Permission.TOOLS_CALL.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    assert "chat:tools:call" in outcome.text


async def test_call_tool_for_chat_flags_not_available_for_chat_unsafe_builtin(mocker):
    """A built-in tool that requires a write permission is hidden from chat."""

    from reporting.services.mcp_builtins.base import BuiltinTool

    write_only_tool = BuiltinTool(
        name="reports__delete",
        group="reports",
        description="Delete a report",
        input_schema={"type": "object"},
        required_permissions=["reports:delete"],
        handler=lambda args, current_user: None,  # pragma: no cover — never called
    )
    mocker.patch("reporting.services.mcp_runtime.find_builtin", return_value=write_only_tool)
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, "reports:delete"}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.NOT_AVAILABLE
    assert "not available to chat" in outcome.text


async def test_call_tool_for_chat_returns_none_blocked_on_success(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query_streamed",
        return_value=([{"name": "node-1"}], False),
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "security__lookup",
        {"limit": 3},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert outcome.blocked is None
    assert json.loads(outcome.text) == [{"name": "node-1"}]


async def test_render_prompt_for_chat_flags_permission_denied_with_enum(mocker):
    get_enabled_skill = mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_skill")
    current = _user(frozenset({Permission.CHAT_SKILLS_CALL.value}))

    outcome = await mcp_runtime.render_prompt_for_chat(
        current,
        "security__summarize",
        {"topic": "alerts"},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    assert "Permission denied: skills:render" in outcome.text
    get_enabled_skill.assert_not_called()


async def test_render_prompt_for_chat_returns_none_blocked_on_success(mocker):
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.get_enabled_skill",
        return_value=_skill().model_copy(update={"tools_required": ["security__lookup"]}),
    )
    current = _user(frozenset({Permission.CHAT_SKILLS_CALL.value, Permission.SKILLS_RENDER.value}))

    outcome = await mcp_runtime.render_prompt_for_chat(
        current,
        "security__summarize",
        {"topic": "alerts"},
        gate_permission=Permission.CHAT_SKILLS_CALL,
    )

    assert outcome.blocked is None
    assert "Summarize alerts." in outcome.text
    assert outcome.tools_required == ("security__lookup",)


async def test_bypass_confirmations_executes_with_permission(mocker):
    """With chat:bypass_permissions, bypass mode runs the handler directly and creates no confirmation."""
    delete_report = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.delete_report",
        mocker.AsyncMock(return_value=True),
    )
    create_confirmation = mocker.patch("reporting.services.mcp_runtime.report_store.create_action_confirmation")
    current = _user(
        frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_DELETE.value,
                Permission.CHAT_BYPASS_PERMISSIONS.value,
            }
        )
    )

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        bypass_confirmations=True,
    )

    assert outcome.blocked is None
    assert json.loads(outcome.text) == {"report_id": "r1"}
    delete_report.assert_awaited_once()
    create_confirmation.assert_not_called()


async def test_bypass_confirmations_blocked_without_permission(mocker):
    """Bypass mode without chat:bypass_permissions is denied and the handler never runs."""
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")
    create_confirmation = mocker.patch("reporting.services.mcp_runtime.report_store.create_action_confirmation")
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.REPORTS_DELETE.value}))

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        bypass_confirmations=True,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    assert Permission.CHAT_BYPASS_PERMISSIONS.value in outcome.text
    delete_report.assert_not_called()
    create_confirmation.assert_not_called()


async def test_bypass_confirmations_requires_authenticated_user(mocker):
    delete_report = mocker.patch("reporting.services.mcp_builtins.reports.report_store.delete_report")

    outcome = await mcp_runtime.call_tool_for_chat(
        None,
        "reports__delete",
        {"report_id": "r1"},
        permissions=frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_DELETE.value,
                Permission.CHAT_BYPASS_PERMISSIONS.value,
            }
        ),
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        bypass_confirmations=True,
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.PERMISSION_DENIED
    delete_report.assert_not_called()


async def test_bypass_confirmations_does_not_affect_interactive_path(mocker):
    """Without the bypass flag, the interactive confirmation flow is unchanged."""
    mocker.patch("reporting.services.mcp_runtime.report_store.find_action_confirmation_grant", return_value=None)
    mocker.patch("reporting.services.mcp_runtime.report_store.list_action_confirmations", return_value=[])
    mocker.patch(
        "reporting.services.mcp_runtime.report_store.create_action_confirmation",
        return_value=_confirmation(),
    )
    current = _user(
        frozenset(
            {
                Permission.CHAT_TOOLS_CALL.value,
                Permission.REPORTS_DELETE.value,
                Permission.CHAT_BYPASS_PERMISSIONS.value,
            }
        )
    )

    outcome = await mcp_runtime.call_tool_for_chat(
        current,
        "reports__delete",
        {"report_id": "r1"},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        chat_safe_only=True,
        confirmation_source="chat",
        confirmation_session_key="session-1",
    )

    assert outcome.blocked == mcp_runtime.ChatBlockReason.CONFIRMATION_REQUIRED
    assert json.loads(outcome.text)["confirmation_required"] is True


# --- Row cap on nested payloads ------------------------------------------------


def test_row_cap_applies_to_a_nested_results_payload():
    """The reviewer's reproduction: 50,001 rows under max_rows=50,000 came back whole.

    graph__query returns {"results": [...]}, and only a top-level list counted as
    rows, so the cap never applied to the tools most likely to return thousands.
    The nesting is reached through the tool's declared ``collection_key``, which
    is what graph__query passes.
    """
    payload = {"results": [{"i": i} for i in range(50_001)], "warnings": ["w"]}

    emitted = mcp_runtime._bounded_text_response(payload, max_rows=50_000, max_bytes=None, collection_key="results")
    decoded = json.loads(emitted[0].text)

    assert len(decoded["results"]) == 50_000
    assert decoded["truncated"] is True
    assert decoded["truncated_reasons"] == ["row_limit"]
    # Siblings survive: dropping them would discard validator warnings at
    # exactly the moment the caller is told something was cut.
    assert decoded["warnings"] == ["w"]


def test_row_cap_still_applies_to_a_top_level_list():
    emitted = mcp_runtime._bounded_text_response([{"i": i} for i in range(50)], max_rows=10, max_bytes=None)
    decoded = json.loads(emitted[0].text)

    assert len(decoded["results"]) == 10
    assert decoded["truncated_reasons"] == ["row_limit"]


def test_byte_shedding_keeps_a_nested_payloads_siblings():
    payload = {"results": [{"i": i, "pad": "x" * 200} for i in range(200)], "warnings": ["w"]}

    emitted = mcp_runtime._bounded_text_response(payload, max_rows=None, max_bytes=5_000, collection_key="results")
    decoded = json.loads(emitted[0].text)

    assert decoded["truncated_reasons"] == ["byte_limit"]
    assert decoded["warnings"] == ["w"]
    assert 0 < len(decoded["results"]) < 200
    assert len(emitted[0].text.encode()) <= 5_000


# --- MCP limits apply to every builtin, not just graph__query ------------------


def test_a_non_query_builtin_is_bounded_by_the_mcp_limits(mocker):
    """Regression: only graph__query read the limits, and the final bound used
    the caller's raw arguments -- None for a normal MCP call -- so list
    builtins came back unbounded despite the documented contract."""
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_ROWS", 5)
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_BYTES", 1_000_000)
    payload = {"reports": [{"i": i} for i in range(20)]}

    limits = mcp_runtime._effective_limits(None, None)
    emitted = mcp_runtime._bounded_text_response(
        payload, max_rows=limits.max_rows, max_bytes=limits.max_bytes, collection_key="reports"
    )
    decoded = json.loads(emitted[0].text)

    assert len(decoded["reports"]) == 5
    assert decoded["truncated"] is True


def test_a_builtin_declares_which_field_holds_its_rows():
    from reporting.services.mcp_builtins import find_builtin

    assert find_builtin("reports__list").collection_key == "reports"
    assert find_builtin("toolsets__list_tools").collection_key == "tools"
    # A tool that returns one record declares nothing, so nothing of it is
    # treated as rows.
    assert find_builtin("roles__get").collection_key is None


def test_a_semantic_list_is_never_mistaken_for_rows():
    """Regression: inferring "the single list field" picked permissions on a
    role. Role updates are replace-semantics, so an agent reading a role and
    writing it back would silently delete whatever was trimmed off."""
    from reporting.services.mcp_builtins import find_builtin

    role = {"role_id": "r", "name": "admin", "permissions": [f"p{i}" for i in range(41)]}
    rows, key = mcp_runtime._payload_rows_and_key(role, find_builtin("roles__get").collection_key)

    assert rows is None and key is None
    decoded = json.loads(
        mcp_runtime._bounded_text_response(role, max_rows=5, max_bytes=None, collection_key=None)[0].text
    )
    assert len(decoded["permissions"]) == 41
    assert "truncated" not in decoded


def test_every_builtin_returning_a_collection_declares_it():
    """The declaration lives next to the tool so it cannot drift from the
    handler, but nothing stops a new one being forgotten -- so check."""
    import inspect
    import re

    from reporting.services import mcp_builtins

    missing = []
    for tool in mcp_builtins.list_builtin_tools():
        handler = mcp_builtins.find_builtin(tool.name)
        if handler is None or handler.collection_key:
            continue
        try:
            source = inspect.getsource(handler.handler)
        except (OSError, TypeError):
            continue
        returned = set(re.findall(r'return \{"(\w+)": \[', source))
        if returned:
            missing.append((tool.name, sorted(returned)))
    assert missing == [], f"builtins return a collection without declaring it: {missing}"


def test_a_chat_caller_keeps_its_own_tighter_limits(mocker):
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_ROWS", 50_000)
    limits = mcp_runtime._effective_limits(100, 200_000)
    assert limits.max_rows == 100
    assert limits.max_bytes == 200_000


def test_a_caller_stating_no_limits_gets_the_mcp_contract(mocker):
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_ROWS", 50_000)
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_BYTES", 25_000_000)
    limits = mcp_runtime._effective_limits(None, None)
    # Not unbounded: a caller passing nothing is an MCP client, not a request
    # to be unlimited.
    assert limits.max_rows == 50_000
    assert limits.max_bytes == 25_000_000


# --- Truncation metadata survives a second bound ------------------------------


def test_a_second_truncation_keeps_the_first_reason():
    """Regression: the byte pass overwrote the source's reason, so a client saw
    only byte_limit and never learned the source had stopped early too."""
    payload = {
        "results": [{"pad": "x" * 300} for _ in range(10)],
        "truncated": True,
        "truncated_reasons": ["row_limit"],
        "total_rows_at_least": 10,
    }

    decoded = json.loads(
        mcp_runtime._bounded_text_response(payload, max_rows=None, max_bytes=600, collection_key="results")[0].text
    )

    assert decoded["truncated_reasons"] == ["row_limit", "byte_limit"]
    assert decoded["returned"] == len(decoded["results"])


def test_a_total_is_never_claimed_when_the_source_stopped_early():
    """The real total is unknown and larger; reporting the length of an already
    truncated list as the total tells a client it has seen everything."""
    payload = {
        "results": [{"pad": "x" * 300} for _ in range(10)],
        "truncated": True,
        "truncated_reasons": ["row_limit"],
    }

    decoded = json.loads(
        mcp_runtime._bounded_text_response(payload, max_rows=None, max_bytes=600, collection_key="results")[0].text
    )

    assert "total_rows" not in decoded
    assert decoded["total_rows_at_least"] == 10


def test_a_complete_source_still_reports_a_real_total():
    payload = {"results": [{"pad": "x" * 300} for _ in range(10)]}

    decoded = json.loads(
        mcp_runtime._bounded_text_response(payload, max_rows=None, max_bytes=600, collection_key="results")[0].text
    )

    assert decoded["total_rows"] == 10
    assert "total_rows_at_least" not in decoded
    assert decoded["truncated_reasons"] == ["byte_limit"]


def test_row_then_byte_truncation_in_one_pass_records_both():
    payload = {"results": [{"pad": "x" * 300} for _ in range(50)]}

    decoded = json.loads(
        mcp_runtime._bounded_text_response(payload, max_rows=10, max_bytes=600, collection_key="results")[0].text
    )

    assert decoded["truncated_reasons"] == ["row_limit", "byte_limit"]
    assert decoded["total_rows"] == 50  # the source was complete, so this is real


def test_explicit_zero_limits_mean_unbounded_not_omitted(mocker):
    """The streaming helper defines 0 as unbounded, so a caller disabling a
    dimension must not have the MCP default put back in its place."""
    mocker.patch("reporting.settings.MCP_TOOL_RESULT_MAX_ROWS", 50_000)
    limits = mcp_runtime._effective_limits(0, 0)
    assert limits.max_rows == 0
    assert limits.max_bytes == 0


def test_no_data_bearing_response_exceeds_its_byte_budget():
    """Regression: the sizing search measured a smaller envelope than the one
    emitted, so responses could exceed the budget the search exists to enforce."""
    over = []
    for budget in range(200, 2000, 11):
        for rows in (1, 5, 30):
            for pad in (0, 60, 400):
                payload = {"results": [{"i": i, "p": "x" * pad} for i in range(rows)], "warnings": ["w"]}
                emitted = mcp_runtime._bounded_text_response(
                    payload, max_rows=7, max_bytes=budget, collection_key="results"
                )[0].text
                if len(emitted.encode()) > budget and "error" not in json.loads(emitted):
                    over.append((budget, len(emitted.encode())))
    assert over == []


def test_the_only_response_allowed_to_exceed_the_budget_is_the_empty_one():
    """A fixed message cannot be shortened below its own length."""
    payload = {"results": [{"p": "x" * 400}]}
    emitted = mcp_runtime._bounded_text_response(payload, max_rows=None, max_bytes=100, collection_key="results")[
        0
    ].text
    decoded = json.loads(emitted)
    assert "error" in decoded
    assert decoded["truncated_reasons"] == ["byte_limit"]


def test_a_source_only_truncation_reports_the_full_contract():
    """Regression: the stream marker omitted returned and total_rows_at_least,
    so a source-truncated result did not match its own documentation."""
    from reporting.services.result_limits import ResultLimits, stream_truncation

    fields = stream_truncation("row_limit", [{"i": 0}, {"i": 1}, {"i": 2}], ResultLimits(max_rows=3)).fields()

    assert fields["truncated"] is True
    assert fields["truncated_reasons"] == ["row_limit"]
    assert fields["returned"] == 3
    assert fields["total_rows_at_least"] == 3
    assert fields["max_rows"] == 3
    assert "total_rows" not in fields


def test_a_row_then_byte_cut_keeps_both_limits():
    """The final marker was passed only max_bytes, dropping the row bound that
    had already been applied."""
    payload = {"results": [{"p": "x" * 300} for _ in range(50)]}

    decoded = json.loads(
        mcp_runtime._bounded_text_response(payload, max_rows=10, max_bytes=900, collection_key="results")[0].text
    )

    assert decoded["truncated_reasons"] == ["row_limit", "byte_limit"]
    assert decoded["max_rows"] == 10
    assert decoded["max_bytes"] == 900


def test_sizing_a_large_response_does_not_stall_the_event_loop():
    """Regression: the bound was found by binary search over whole prefixes, so
    a multi-megabyte payload was serialized about seventeen times -- six seconds
    of synchronous CPU inside an async handler, blocking the worker."""
    import time

    from reporting.services.payload_bounds import json_size_bytes, largest_prefix_within_bytes

    rows = [{"i": i, "pad": "x" * 500} for i in range(20_000)]
    envelope = {"results": None, "truncated": True, "truncated_reasons": ["byte_limit"]}

    def wrap(kept):
        return {**envelope, "results": kept, "returned": len(kept)}

    started = time.perf_counter()
    keep = largest_prefix_within_bytes(rows, max_bytes=8_000_000, envelope=wrap, indent=2)
    elapsed = time.perf_counter() - started

    assert json_size_bytes(wrap(rows[:keep]), indent=2) <= 8_000_000  # the bound is a promise
    assert keep > 0
    # Generous, so this fails on a return to whole-prefix search rather than on
    # a slow machine.
    assert elapsed < 2.0


def test_an_undeclared_collection_is_not_trimmed_by_name():
    """No key stated means no rows found -- not a name that looks plausible.

    This is the fix for the guess: a role's ``permissions`` list was treated as
    rows and silently shortened, and role updates are replace-semantics, so a
    get->update round trip wrote the trimmed list back. Returned whole or
    refused whole; never shortened in a place the caller did not name.
    """
    payload = {"permissions": [f"p{i}" for i in range(50)], "name": "editor"}

    emitted = mcp_runtime._bounded_text_response(payload, max_rows=10, max_bytes=None)
    decoded = json.loads(emitted[0].text)

    assert len(decoded["permissions"]) == 50
    assert "truncated" not in decoded


def test_declared_tool_names_reads_the_listing_rather_than_the_store():
    """One store read per turn covers every consumer, so the declaration rides
    on the skill listing the turn already made."""
    from mcp.types import Prompt

    from reporting.services import mcp_runtime as rt

    prompts = [
        Prompt(name="s__a", description="d", _meta={rt.SKILL_TOOLS_META_KEY: ["reports__list", "reports__get"]}),
        Prompt(name="s__b", description="d", _meta={rt.SKILL_TOOLS_META_KEY: ["reports__list", "graph__query"]}),
        Prompt(name="s__c", description="d"),  # a skill declaring nothing
        Prompt(name="s__d", description="d", _meta={rt.SKILL_TOOLS_META_KEY: "not a list"}),
    ]

    assert rt.declared_tool_names(prompts) == frozenset({"reports__list", "reports__get", "graph__query"})
    assert rt.declared_tool_names([]) == frozenset()


# ---------------------------------------------------------------------------
# Argument validation against the tool's advertised JSON Schema
# ---------------------------------------------------------------------------
#
# Until MCP 2.0 the SDK's @server.call_tool() wrapper ran
# jsonschema.validate(arguments, tool.inputSchema) before dispatching, and
# turned any raised exception into an is_error result. The 2.x constructor
# callbacks do neither, so both behaviours now live in the runtime -- and are
# shared with chat, which never had them.


async def _pin(arguments, mocker, **kwargs):
    """Call reports__pin as an MCP caller would, with the store stubbed out."""
    mocker.patch.object(action_confirmations, "ensure_confirmation", mocker.AsyncMock(return_value=None))
    return await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "reports__pin",
        arguments,
        permissions=ALL_PERMISSIONS,
        confirmation_source="mcp",
        confirmation_session_key="session",
        **kwargs,
    )


async def test_wrongly_typed_argument_is_rejected_against_the_schema(mocker):
    """A type the schema forbids must not reach the handler."""
    pin = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.pin_report",
        mocker.AsyncMock(),
    )
    result = await _pin({"report_id": "r1", "pinned": []}, mocker)

    assert "Input validation error" in json.loads(result.content[0].text)["error"]
    pin.assert_not_awaited()


async def test_coercible_argument_cannot_change_what_a_mutation_does(mocker):
    """The string "false" must not become the boolean False and unpin a report.

    Handlers parse arguments with pydantic, which coerces; the advertised schema
    says boolean. Without the schema check this call silently unpinned.
    """
    pin = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.pin_report",
        mocker.AsyncMock(),
    )
    result = await _pin({"report_id": "r1", "pinned": "false"}, mocker)

    assert "Input validation error" in json.loads(result.content[0].text)["error"]
    pin.assert_not_awaited()


async def test_missing_required_argument_is_still_reported(mocker):
    result = await _pin({"report_id": "r1"}, mocker)
    assert "'pinned' is a required property" in json.loads(result.content[0].text)["error"]


async def test_conforming_arguments_still_reach_the_handler(mocker):
    pin = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.pin_report",
        mocker.AsyncMock(return_value={"report_id": "r1", "pinned": True}),
    )
    result = await _pin({"report_id": "r1", "pinned": True}, mocker)

    assert json.loads(result.content[0].text) == {"report_id": "r1", "pinned": True}
    pin.assert_awaited_once()


async def test_chat_gets_the_same_validation_as_mcp(mocker):
    """The check lives in the shared runtime so the two cannot diverge."""
    mocker.patch.object(action_confirmations, "ensure_confirmation", mocker.AsyncMock(return_value=None))
    pin = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.pin_report",
        mocker.AsyncMock(),
    )
    outcome = await mcp_runtime.call_tool_for_chat(
        _user(ALL_PERMISSIONS),
        "reports__pin",
        {"report_id": "r1", "pinned": "false"},
        permissions=ALL_PERMISSIONS,
        confirmation_source="chat",
        confirmation_session_key="session",
    )

    assert "Input validation error" in outcome.text
    pin.assert_not_awaited()


async def test_an_unexpected_failure_becomes_a_result_not_a_raise(mocker):
    """The backstop for everything ahead of the handler's own try/except.

    The confirmation resolvers and the write that records a pending
    confirmation sit outside it, so without this guard a store outage escaped
    the runtime and MCP 2.0 turned it into a JSON-RPC protocol error -- a broken
    server, rather than a failed call the caller can read.
    """
    mocker.patch.object(
        action_confirmations,
        "ensure_confirmation",
        mocker.AsyncMock(side_effect=RuntimeError("confirmation store is down")),
    )
    result = await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "reports__pin",
        {"report_id": "r1", "pinned": True},
        permissions=ALL_PERMISSIONS,
        confirmation_source="mcp",
        confirmation_session_key="session",
    )

    assert json.loads(result.content[0].text)["error"] == "Failed to execute tool 'reports__pin'"


async def test_stored_tool_rejects_a_coercible_string(mocker):
    """A user-defined tool's schema is enforced too, not just a built-in's.

    This path used to be gated by the store's ``validate_tool_arguments``, which
    coerces "5" to 5 to decide it is valid and then throws the coerced value
    away -- so the *string* reached Cypher, where a parameter used in arithmetic
    or a comparison behaves differently. The advertised schema says integer. A
    "not-an-int" case never caught this, because that one fails coercion too.
    """
    run_query = mocker.patch.object(mcp_runtime.reporting_neo4j, "run_query_streamed", mocker.AsyncMock())
    mocker.patch.object(
        report_store,
        "get_enabled_tool",
        mocker.AsyncMock(
            return_value=ToolItem(
                tool_id="t1",
                toolset_id="ts1",
                name="mytool",
                description="",
                cypher="MATCH (n) RETURN n LIMIT $limit",
                parameters=[ToolParamDef(name="limit", type="integer", description="", required=True)],
                enabled=True,
                current_version=1,
                created_at=_NOW,
                updated_at=_NOW,
                created_by="u",
            )
        ),
    )

    result = await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "ts1__t1",
        {"limit": "5"},
        permissions=ALL_PERMISSIONS,
    )

    assert "Input validation error" in json.loads(result.content[0].text)["error"]
    assert result.is_error is True
    run_query.assert_not_awaited()


async def test_a_misconfigured_schema_fails_closed(mocker):
    """An invalid schema is a broken type guard, so the call is refused.

    Letting it through would silently restore the coercion the check exists to
    stop, on a tool that might mutate.
    """
    broken = dataclasses.replace(
        mcp_runtime.find_builtin("reports__pin"),
        # "type" must be a string or list of strings; 5 makes the schema itself
        # invalid, which is what a typo in a tool definition looks like.
        input_schema={"type": "object", "properties": {"pinned": {"type": 5}}},
    )
    mocker.patch.object(mcp_runtime, "find_builtin", return_value=broken)
    pin = mocker.patch(
        "reporting.services.mcp_builtins.reports.report_store.pin_report",
        mocker.AsyncMock(),
    )

    result = await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "reports__pin",
        {"report_id": "r1", "pinned": True},
        permissions=ALL_PERMISSIONS,
        confirmation_source="mcp",
        confirmation_session_key="session",
    )

    assert "not valid JSON Schema" in json.loads(result.content[0].text)["error"]
    assert result.is_error is True
    pin.assert_not_awaited()


async def test_a_tool_that_answered_is_not_flagged_as_an_error(mocker):
    """ "Not found" is an answer. Only an unhonourable call sets is_error."""
    mocker.patch.object(report_store, "get_enabled_tool", mocker.AsyncMock(return_value=None))

    result = await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "ts1__missing",
        {},
        permissions=ALL_PERMISSIONS,
    )

    assert json.loads(result.content[0].text) == {"error": "Tool 'ts1__missing' not found"}
    assert result.is_error is False


def _int_param_tool() -> ToolItem:
    return ToolItem(
        tool_id="t1",
        toolset_id="ts1",
        name="mytool",
        description="",
        cypher="MATCH (n) RETURN n LIMIT $limit",
        parameters=[ToolParamDef(name="limit", type="integer", description="", required=True)],
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u",
    )


async def test_an_integral_float_is_accepted_and_normalized(mocker):
    """A value the advertised schema allows must not be refused.

    JSON Schema counts 2.0 as an integer, so a client validating against the
    schema from tools/list can legitimately send one. Neo4j will not take a
    float where the Cypher uses an integer, so it is normalized rather than
    rejected -- advertising a contract and then refusing what it permits is the
    bug this replaced.
    """
    run_query = mocker.patch.object(
        mcp_runtime.reporting_neo4j, "run_query_streamed", mocker.AsyncMock(return_value=([], None))
    )
    mocker.patch.object(report_store, "get_enabled_tool", mocker.AsyncMock(return_value=_int_param_tool()))

    result = await mcp_runtime.call_tool_for_user(
        _user(ALL_PERMISSIONS),
        "ts1__t1",
        {"limit": 2.0},
        permissions=ALL_PERMISSIONS,
    )

    assert result.is_error is False
    passed = run_query.await_args.args[1]
    assert passed["limit"] == 2
    assert isinstance(passed["limit"], int) and not isinstance(passed["limit"], bool)


async def test_normalizing_does_not_reopen_string_coercion(mocker):
    """Only an integral float is converted; a numeric string stays refused."""
    run_query = mocker.patch.object(mcp_runtime.reporting_neo4j, "run_query_streamed", mocker.AsyncMock())
    mocker.patch.object(report_store, "get_enabled_tool", mocker.AsyncMock(return_value=_int_param_tool()))

    for value in ("2", 2.5, True, "false"):
        result = await mcp_runtime.call_tool_for_user(
            _user(ALL_PERMISSIONS),
            "ts1__t1",
            {"limit": value},
            permissions=ALL_PERMISSIONS,
        )
        assert result.is_error is True, value
        assert "Input validation error" in json.loads(result.content[0].text)["error"], value
    run_query.assert_not_awaited()
