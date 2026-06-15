import json

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.mcp_config import SkillItem, ToolItem, ToolParamDef
from reporting.schema.report_config import ReportAccess, ReportListItem, User
from reporting.services import action_confirmations, mcp_runtime

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


async def test_chat_tool_gate_blocks_call_before_store_lookup(mocker):
    get_enabled_tool = mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool")
    current = _user(frozenset({Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    assert json.loads(result[0].text) == {"error": "Permission denied: chat:tools:call"}
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

    assert json.loads(result[0].text) == {"error": "Permission denied: tools:call"}
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
        "reporting.services.mcp_runtime.reporting_neo4j.run_query",
        return_value=[{"name": "node-1"}],
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {"limit": 3},
        gate_permission=Permission.CHAT_TOOLS_CALL,
    )

    run_query.assert_awaited_once_with("MATCH (n) RETURN n LIMIT $limit", parameters={"limit": 3})
    assert json.loads(result[0].text) == [{"name": "node-1"}]


async def test_chat_tool_call_surfaces_neo4j_error_without_stacktrace(mocker):
    import neo4j.exceptions

    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    err = neo4j.exceptions.ClientError("Expected parameter(s): cve_id, limit")
    # A server-hydrated Neo4jError exposes the human-readable text via .message
    # (backed by ._message); set it to mirror what the driver returns.
    err._message = "Expected parameter(s): cve_id, limit"
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query",
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

    data = json.loads(result[0].text)
    # The database message is surfaced so the caller can see why the tool failed.
    assert "Expected parameter(s): cve_id, limit" in data["error"]
    # Logged concisely (warning), not as a full-traceback ERROR.
    log.warning.assert_called_once()
    log.exception.assert_not_called()


async def test_chat_tool_call_applies_row_limit(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query",
        return_value=[{"name": "node-1"}, {"name": "node-2"}],
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_rows=1,
    )

    assert json.loads(result[0].text) == {
        "results": [{"name": "node-1"}],
        "truncated": True,
        "truncated_reason": "row_limit",
        "max_rows": 1,
    }


async def test_chat_tool_call_applies_byte_limit(mocker):
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch(
        "reporting.services.mcp_runtime.reporting_neo4j.run_query",
        return_value=[{"name": "x" * 100}],
    )
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_bytes=20,
    )

    assert json.loads(result[0].text) == {
        "error": "Tool result exceeded chat size limit",
        "truncated": True,
        "truncated_reason": "byte_limit",
        "max_bytes": 20,
    }


async def test_chat_tool_call_byte_limit_sheds_rows(mocker):
    rows = [{"v": "x" * 50} for _ in range(12)]
    mocker.patch("reporting.services.mcp_runtime.report_store.get_enabled_tool", return_value=_tool())
    mocker.patch("reporting.services.mcp_runtime.reporting_neo4j.run_query", return_value=rows)
    current = _user(frozenset({Permission.CHAT_TOOLS_CALL.value, Permission.TOOLS_CALL.value}))

    result = await mcp_runtime.call_tool_for_user(
        current,
        "security__lookup",
        {},
        gate_permission=Permission.CHAT_TOOLS_CALL,
        result_max_bytes=500,
    )

    data = json.loads(result[0].text)
    # Graceful: keep as many whole rows as fit rather than discarding everything.
    assert data["truncated_reason"] == "byte_limit"
    assert data["total_rows"] == 12
    assert 1 <= len(data["results"]) < 12
    assert data["returned"] == len(data["results"])
    assert len(result[0].text.encode("utf-8")) <= 500


async def test_chat_safe_tool_listing_includes_create_write_builtins(mocker):
    """reports__create/clone are always private so they are safe without confirmation."""
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

    data = json.loads(result[0].text)
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

    data = json.loads(result[0].text)
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

    data = json.loads(result[0].text)
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

    assert json.loads(result[0].text) == {"report_id": "r1"}
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

    payload = json.loads(result[0].text)
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

    data = json.loads(result[0].text)
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

    assert json.loads(result[0].text) == {"error": "Missing required argument(s): skillset_id"}
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
        "reporting.services.mcp_runtime.reporting_neo4j.run_query",
        return_value=[{"name": "node-1"}],
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
