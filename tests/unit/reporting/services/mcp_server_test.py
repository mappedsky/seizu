"""Unit tests for reporting/services/mcp_server.py."""

import contextlib
import json
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.mcp_config import SkillItem, ToolItem, ToolParamDef, ToolsetListItem
from reporting.schema.report_config import User
from reporting.services import mcp_server as mcp_module
from reporting.services.mcp_server import (
    _build_mcp_server,
    _build_oauth_metadata,
    _mcp_permissions,
    _oauth_registration_handler,
)
from tests.unit.reporting.services import mcp_dispatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = "2024-01-01T00:00:00+00:00"


def _toolset(toolset_id: str = "ts1", name: str = "mytoolset", enabled: bool = True):
    return ToolsetListItem(
        toolset_id=toolset_id,
        name=name,
        description="",
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user@example.com",
    )


def _tool(
    tool_id: str = "t1",
    toolset_id: str = "ts1",
    name: str = "mytool",
    enabled: bool = True,
    parameters=None,
    cypher: str = "MATCH (n) RETURN n LIMIT 1",
):
    return ToolItem(
        tool_id=tool_id,
        toolset_id=toolset_id,
        name=name,
        description="A test tool",
        cypher=cypher,
        parameters=parameters or [],
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user@example.com",
    )


def _skill(
    skill_id: str = "summarize",
    skillset_id: str = "prompts",
    name: str = "Summarize",
    enabled: bool = True,
    parameters=None,
    template: str = "Summarize {% $topic %} in {% $count %} bullets.",
):
    return SkillItem(
        skill_id=skill_id,
        skillset_id=skillset_id,
        name=name,
        description="A test skill",
        template=template,
        parameters=parameters or [],
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="user@example.com",
    )


async def _list_tools(server, permissions=ALL_PERMISSIONS):
    """Call the registered list_tools handler."""
    token = _mcp_permissions.set(permissions)
    try:
        result = await mcp_dispatch.list_tools(server)
    finally:
        _mcp_permissions.reset(token)
    return result.tools


async def _call_tool(server, name, arguments=None, permissions=ALL_PERMISSIONS):
    """Call the registered call_tool handler and return the text content list."""
    token = _mcp_permissions.set(permissions)
    try:
        result = await mcp_dispatch.call_tool(server, name, arguments)
    finally:
        _mcp_permissions.reset(token)
    return result.content


async def _list_prompts(server, permissions=ALL_PERMISSIONS):
    """Call the registered list_prompts handler."""
    token = _mcp_permissions.set(permissions)
    try:
        result = await mcp_dispatch.list_prompts(server)
    finally:
        _mcp_permissions.reset(token)
    return result.prompts


async def _get_prompt(server, name, arguments=None, permissions=ALL_PERMISSIONS):
    """Call the registered get_prompt handler."""
    token = _mcp_permissions.set(permissions)
    try:
        return await mcp_dispatch.get_prompt(server, name, arguments)
    finally:
        _mcp_permissions.reset(token)


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


async def test_list_tools_includes_builtin_schema_tool():
    with (
        patch(
            "reporting.services.mcp_server.report_store.list_enabled_tools",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "reporting.services.mcp_server.report_store.list_toolsets",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        server = _build_mcp_server()
        tools = await _list_tools(server)
        names = [t.name for t in tools]
        assert "graph__schema" in names
        assert "graph__query" in names


async def test_list_tools_includes_user_defined_tool():
    ts = _toolset()
    tool = _tool()
    with (
        patch(
            "reporting.services.mcp_server.report_store.list_enabled_tools",
            new_callable=AsyncMock,
            return_value=[tool],
        ),
        patch(
            "reporting.services.mcp_server.report_store.list_toolsets",
            new_callable=AsyncMock,
            return_value=[ts],
        ),
    ):
        server = _build_mcp_server()
        tools = await _list_tools(server)
        names = [t.name for t in tools]
        assert "ts1__t1" in names


async def test_list_tools_with_parameters_builds_schema():
    ts = _toolset()
    tool = _tool(
        parameters=[
            ToolParamDef(
                name="limit",
                type="integer",
                description="Max rows",
                required=True,
            )
        ]
    )
    with (
        patch(
            "reporting.services.mcp_server.report_store.list_enabled_tools",
            new_callable=AsyncMock,
            return_value=[tool],
        ),
        patch(
            "reporting.services.mcp_server.report_store.list_toolsets",
            new_callable=AsyncMock,
            return_value=[ts],
        ),
    ):
        server = _build_mcp_server()
        tools = await _list_tools(server)
        user_tool = next(t for t in tools if t.name == "ts1__t1")
        assert "limit" in user_tool.input_schema["properties"]
        assert user_tool.input_schema["required"] == ["limit"]


async def test_list_tools_store_error_returns_builtins_only():
    with (
        patch(
            "reporting.services.mcp_server.report_store.list_enabled_tools",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
        patch(
            "reporting.services.mcp_server.report_store.list_toolsets",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        server = _build_mcp_server()
        tools = await _list_tools(server)
        names = [t.name for t in tools]
        assert "graph__schema" in names
        assert "graph__query" in names
        # All tool names follow the <group>__<action> convention — no user
        # tools were added because the store raised.
        assert all("__" in n for n in names)
        # Every surfaced tool is from a built-in group.
        from reporting.services.mcp_builtins import list_builtin_tools

        builtin_names = {t.name for t in list_builtin_tools()}
        assert set(names) <= builtin_names


# ---------------------------------------------------------------------------
# call_tool — graph__query
# ---------------------------------------------------------------------------


async def test_call_tool_query_empty_query_string():
    # MCP library validates required params; an empty-string query bypasses that
    # and triggers our own guard inside the handler.
    server = _build_mcp_server()
    result = await _call_tool(server, "graph__query", {"query": "  "})
    data = json.loads(result[0].text)
    assert "error" in data


async def test_call_tool_query_validation_error():
    from reporting.services.query_validator import ValidationResult

    with patch(
        "reporting.services.mcp_builtins.graph.validate_query",
        new_callable=AsyncMock,
        return_value=ValidationResult(errors=["syntax error"], warnings=[]),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "BAD CYPHER"})
        data = json.loads(result[0].text)
        assert "errors" in data
        assert "syntax error" in data["errors"]


async def test_call_tool_query_success():
    from reporting.services.query_validator import ValidationResult

    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=[], warnings=[]),
        ),
        patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            return_value=([{"n": 1}], False),
        ),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH (n) RETURN n"})
        data = json.loads(result[0].text)
        assert "results" in data
        assert data["results"][0]["n"] == 1


async def test_call_tool_query_rejects_large_unindexed_plan_with_explain_result():
    from reporting.services.query_validator import ValidationResult

    plan = {
        "operatorType": "ProduceResults@neo4j",
        "args": {"EstimatedRows": 1},
        "children": [
            {
                "operatorType": "VarLengthExpand(All)@neo4j",
                "args": {"EstimatedRows": 265_000, "Details": "(r)-[*..5]-(n)"},
                "children": [
                    {
                        "operatorType": "NodeByLabelScan@neo4j",
                        "args": {"EstimatedRows": 11, "Details": "r:CodeRepository"},
                        "children": [],
                    }
                ],
            }
        ],
    }
    execute = AsyncMock()
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(plan=plan),
        ),
        patch("reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed", execute),
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_REJECT_UNINDEXED", True),
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS", 100_000),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH p=(r)-[*1..5]-(n) RETURN p"})
        data = json.loads(result[0].text)

    assert data["code"] == "query_plan_rejected"
    assert data["plan"] == plan
    assert data["max_estimated_rows"] == 265_000
    assert data["unindexed_operators"] == [
        {"operator_type": "NodeByLabelScan", "details": "r:CodeRepository", "estimated_rows": 11}
    ]
    execute.assert_not_awaited()


async def test_call_tool_query_allows_small_bounded_scan():
    from reporting.services.query_validator import ValidationResult

    plan = {
        "operatorType": "ProduceResults@neo4j",
        "args": {"EstimatedRows": 10},
        "children": [
            {
                "operatorType": "NodeByLabelScan@neo4j",
                "args": {"EstimatedRows": 10, "Details": "c:CVE"},
                "children": [],
            }
        ],
    }
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(plan=plan),
        ),
        patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            return_value=([{"id": "CVE-1"}], False),
        ) as execute,
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_REJECT_UNINDEXED", True),
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_UNINDEXED_MAX_ESTIMATED_ROWS", 100_000),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH (c:CVE) RETURN c.id LIMIT 10"})
        data = json.loads(result[0].text)

    assert data["results"] == [{"id": "CVE-1"}]
    execute.assert_awaited_once()


async def test_call_tool_query_rejects_neo4j_performance_warning():
    from reporting.services.query_validator import ValidationResult

    plan = {"operatorType": "ProduceResults", "args": {"EstimatedRows": 1}, "children": []}
    execute = AsyncMock()
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(
                warnings=["The query contains a cartesian product"],
                performance_warnings=["The query contains a cartesian product"],
                plan=plan,
            ),
        ),
        patch("reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed", execute),
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_REJECT_UNINDEXED", True),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH (a), (b) RETURN a, b"})
        data = json.loads(result[0].text)

    assert data["code"] == "query_plan_rejected"
    assert data["performance_warnings"] == ["The query contains a cartesian product"]
    assert data["plan"] == plan
    execute.assert_not_awaited()


async def test_call_tool_query_can_allow_a_risky_plan_by_configuration():
    from reporting.services.query_validator import ValidationResult

    plan = {
        "operatorType": "NodeByLabelScan@neo4j",
        "args": {"EstimatedRows": 500_000, "Details": "n:CVE"},
        "children": [],
    }
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(plan=plan),
        ),
        patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            return_value=([{"count": 500_000}], False),
        ) as execute,
        patch("reporting.services.mcp_builtins.graph.settings.MCP_GRAPH_QUERY_REJECT_UNINDEXED", False),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH (n:CVE) RETURN count(n) AS count"})
        data = json.loads(result[0].text)

    assert data["results"] == [{"count": 500_000}]
    execute.assert_awaited_once()


async def test_call_tool_query_execution_error():
    from reporting.services.query_validator import ValidationResult

    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=[], warnings=[]),
        ),
        patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("neo4j down"),
        ),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__query", {"query": "MATCH (n) RETURN n"})
        data = json.loads(result[0].text)
        assert "error" in data


# ---------------------------------------------------------------------------
# call_tool — graph__validate_query
# ---------------------------------------------------------------------------


async def test_call_tool_validate_query_valid():
    from reporting.services.query_validator import ValidationResult

    run_query = AsyncMock()
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=[], warnings=["uses an unindexed scan"]),
        ),
        patch("reporting.services.mcp_builtins.graph.reporting_neo4j.run_query", run_query),
        patch("reporting.services.mcp_builtins.graph.reporting_neo4j.run_query_streamed", run_query),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__validate_query", {"query": "MATCH (n) RETURN n"})
        data = json.loads(result[0].text)
        assert data["valid"] is True
        assert data["errors"] == []
        assert data["warnings"] == ["uses an unindexed scan"]
    # Validation must never execute the query.
    run_query.assert_not_called()


async def test_call_tool_validate_query_invalid():
    from reporting.services.query_validator import ValidationResult

    with patch(
        "reporting.services.mcp_builtins.graph.validate_query",
        new_callable=AsyncMock,
        return_value=ValidationResult(errors=["Write queries are not allowed"], warnings=[]),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__validate_query", {"query": "CREATE (n) RETURN n"})
        data = json.loads(result[0].text)
        assert data["valid"] is False
        assert "Write queries are not allowed" in data["errors"]


async def test_call_tool_validate_query_empty_query_string():
    server = _build_mcp_server()
    result = await _call_tool(server, "graph__validate_query", {"query": "  "})
    data = json.loads(result[0].text)
    assert "error" in data


# ---------------------------------------------------------------------------
# call_tool — graph__explain
# ---------------------------------------------------------------------------


async def test_call_tool_explain_returns_plan():
    from reporting.services.query_validator import ValidationResult

    plan = {"operatorType": "NodeByLabelScan", "identifiers": ["n"], "children": []}
    with (
        patch(
            "reporting.services.mcp_builtins.graph.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=[], warnings=["unindexed scan"], plan=plan),
        ),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__explain", {"query": "MATCH (n) RETURN n"})
        data = json.loads(result[0].text)
        assert data["plan"] == plan
        assert data["warnings"] == ["unindexed scan"]


async def test_call_tool_explain_blocks_invalid_query_before_planning():
    from reporting.services.query_validator import ValidationResult

    with patch(
        "reporting.services.mcp_builtins.graph.validate_query",
        new_callable=AsyncMock,
        return_value=ValidationResult(errors=["Write queries are not allowed"], warnings=[]),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__explain", {"query": "CREATE (n) RETURN n"})
        data = json.loads(result[0].text)
        assert "Write queries are not allowed" in data["errors"]
        assert "plan" not in data


async def test_call_tool_explain_empty_query_string():
    server = _build_mcp_server()
    result = await _call_tool(server, "graph__explain", {"query": "  "})
    data = json.loads(result[0].text)
    assert "error" in data


# ---------------------------------------------------------------------------
# call_tool — graph__schema
# ---------------------------------------------------------------------------


async def test_call_tool_schema_success():
    with patch(
        "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query",
        new_callable=AsyncMock,
        side_effect=[
            [{"label": "Person"}],
            [{"type": "KNOWS"}],
            [{"key": "name"}],
            [
                {
                    "name": "person_name",
                    "type": "RANGE",
                    "entityType": "NODE",
                    "labelsOrTypes": ["Person"],
                    "properties": ["name"],
                    "state": "ONLINE",
                }
            ],
        ],
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__schema", {})
        data = json.loads(result[0].text)
        assert data["labels"] == ["Person"]
        assert data["relationship_types"] == ["KNOWS"]
        assert data["property_keys"] == ["name"]
        assert data["indexes"] == [
            {
                "name": "person_name",
                "type": "RANGE",
                "entity_type": "NODE",
                "labels_or_types": ["Person"],
                "properties": ["name"],
                "state": "ONLINE",
            }
        ]


async def test_call_tool_schema_error():
    with patch(
        "reporting.services.mcp_builtins.graph.reporting_neo4j.run_query",
        new_callable=AsyncMock,
        side_effect=RuntimeError("neo4j down"),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "graph__schema", {})
        data = json.loads(result[0].text)
        assert "error" in data


# ---------------------------------------------------------------------------
# call_tool — user-defined tool
# ---------------------------------------------------------------------------


async def test_call_tool_user_defined_not_found():
    get_enabled_tool = AsyncMock(return_value=None)
    with patch(
        "reporting.services.mcp_server.report_store.get_enabled_tool",
        new=get_enabled_tool,
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "unknown__tool", {})
        data = json.loads(result[0].text)
        assert "error" in data
        assert "not found" in data["error"]
    get_enabled_tool.assert_awaited_once_with("unknown", "tool")


async def test_call_tool_user_defined_argument_validation_error():
    # MCP validates input schema before our handler; wrong type → plain text error
    tool = _tool(
        parameters=[
            ToolParamDef(
                name="limit",
                type="integer",
                required=True,
            )
        ]
    )
    get_enabled_tool = AsyncMock(return_value=tool)
    with patch(
        "reporting.services.mcp_server.report_store.get_enabled_tool",
        new=get_enabled_tool,
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "ts1__t1", {"limit": "not-an-int"})
        assert len(result) == 1
        assert "integer" in result[0].text or "validation" in result[0].text.lower()
    get_enabled_tool.assert_awaited_once_with("ts1", "t1")


async def test_call_tool_user_defined_success():
    tool = _tool()
    get_enabled_tool = AsyncMock(return_value=tool)
    with (
        patch(
            "reporting.services.mcp_server.report_store.get_enabled_tool",
            new=get_enabled_tool,
        ),
        patch(
            "reporting.services.mcp_server.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            return_value=([{"n": "value"}], False),
        ) as run_query,
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "ts1__t1", {})
        data = json.loads(result[0].text)
        assert data[0]["n"] == "value"
    get_enabled_tool.assert_awaited_once_with("ts1", "t1")
    assert run_query.await_args.args[:2] == (tool.cypher, {})


async def test_call_tool_user_defined_coerces_decimal_parameter_defaults():
    tool = _tool(
        parameters=[
            ToolParamDef(name="limit", type="integer", required=False, default=Decimal("10")),
            ToolParamDef(name="threshold", type="float", required=False, default=Decimal("2.5")),
        ],
        cypher="MATCH (n) RETURN n LIMIT $limit",
    )
    get_enabled_tool = AsyncMock(return_value=tool)
    with (
        patch(
            "reporting.services.mcp_server.report_store.get_enabled_tool",
            new=get_enabled_tool,
        ),
        patch(
            "reporting.services.mcp_server.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            return_value=([{"n": "value"}], False),
        ) as run_query,
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "ts1__t1", {})
        data = json.loads(result[0].text)
        assert data[0]["n"] == "value"
    get_enabled_tool.assert_awaited_once_with("ts1", "t1")
    assert run_query.await_args.args[:2] == (tool.cypher, {"limit": 10, "threshold": 2.5})


async def test_call_tool_user_defined_execution_error():
    tool = _tool()
    get_enabled_tool = AsyncMock(return_value=tool)
    with (
        patch(
            "reporting.services.mcp_server.report_store.get_enabled_tool",
            new=get_enabled_tool,
        ),
        patch(
            "reporting.services.mcp_server.reporting_neo4j.run_query_streamed",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db error"),
        ),
    ):
        server = _build_mcp_server()
        result = await _call_tool(server, "ts1__t1", {})
        data = json.loads(result[0].text)
        assert "error" in data
    get_enabled_tool.assert_awaited_once_with("ts1", "t1")


# ---------------------------------------------------------------------------
# prompts — user-defined skills
# ---------------------------------------------------------------------------


async def test_list_prompts_includes_user_defined_skill():
    skill = _skill(
        parameters=[
            ToolParamDef(name="topic", type="string", required=True),
            ToolParamDef(name="count", type="integer", required=False, default=3),
        ]
    )
    with patch(
        "reporting.services.mcp_server.report_store.list_enabled_skills",
        new_callable=AsyncMock,
        return_value=[skill],
    ):
        server = _build_mcp_server()
        prompts = await _list_prompts(server)

    prompt = next(p for p in prompts if p.name == "prompts__summarize")
    assert prompt.title == "Summarize"
    assert [arg.name for arg in prompt.arguments] == ["topic", "count"]
    assert [arg.required for arg in prompt.arguments] == [True, False]


async def test_list_prompts_requires_render_permission():
    with patch(
        "reporting.services.mcp_server.report_store.list_enabled_skills",
        new_callable=AsyncMock,
        return_value=[_skill()],
    ):
        server = _build_mcp_server()
        prompts = await _list_prompts(server, permissions=frozenset())

    assert prompts == []


async def test_get_prompt_renders_skill_template():
    skill = _skill(
        parameters=[
            ToolParamDef(name="topic", type="string", required=True),
            ToolParamDef(name="count", type="integer", required=False, default=3),
        ]
    )
    get_enabled_skill = AsyncMock(return_value=skill)
    with patch(
        "reporting.services.mcp_server.report_store.get_enabled_skill",
        new=get_enabled_skill,
    ):
        server = _build_mcp_server()
        result = await _get_prompt(server, "prompts__summarize", {"topic": "alerts", "count": "2"})

    assert result.description == "A test skill"
    assert result.messages[0].content.text == "Summarize alerts in 2 bullets."
    get_enabled_skill.assert_awaited_once_with("prompts", "summarize")


async def test_get_prompt_reports_render_errors():
    skill = _skill(parameters=[ToolParamDef(name="topic", type="string", required=True)])
    get_enabled_skill = AsyncMock(return_value=skill)
    with patch(
        "reporting.services.mcp_server.report_store.get_enabled_skill",
        new=get_enabled_skill,
    ):
        server = _build_mcp_server()
        result = await _get_prompt(server, "prompts__summarize", {})

    data = json.loads(result.messages[0].content.text)
    assert "Required parameter 'topic' is missing" in data["errors"]
    get_enabled_skill.assert_awaited_once_with("prompts", "summarize")


# ---------------------------------------------------------------------------
# _build_oauth_metadata
# ---------------------------------------------------------------------------


async def test_build_oauth_metadata_returns_none_when_not_configured():
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(mcp_module.settings, "MCP_RESOURCE_URL", ""),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        assert await _build_oauth_metadata() is None


async def test_build_oauth_metadata_uses_mcp_resource_url_as_issuer():
    """RFC 8414: issuer must equal the URL from which the document is served."""
    discovery_doc = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com/o/seizu"),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["issuer"] == "https://seizu.example.com/api/v1/mcp"
        assert result["authorization_endpoint"] == "https://idp.example.com/authorize"
        assert result["token_endpoint"] == "https://idp.example.com/token"
        assert "S256" in result["code_challenge_methods_supported"]


async def test_build_oauth_metadata_mcp_oauth_issuer_overrides_resource_url():
    """MCP_OAUTH_ISSUER overrides issuer for RFC 8414-compliant IdPs."""
    with (
        patch.object(
            mcp_module.settings,
            "MCP_OAUTH_AUTHORIZATION_ENDPOINT",
            "https://idp.example.com/authorize",
        ),
        patch.object(
            mcp_module.settings,
            "MCP_OAUTH_TOKEN_ENDPOINT",
            "https://idp.example.com/token",
        ),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", "https://idp.example.com"),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["issuer"] == "https://idp.example.com"


async def test_build_oauth_metadata_derives_endpoints_from_oidc_authority():
    discovery_doc = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["authorization_endpoint"] == "https://idp.example.com/authorize"
        assert result["token_endpoint"] == "https://idp.example.com/token"


async def test_build_oauth_metadata_rewrites_internal_urls_to_external():
    """Endpoints discovered via an internal authority have their origin rewritten."""
    discovery_doc = {
        "authorization_endpoint": "http://internal-idp:9000/application/o/authorize/",
        "token_endpoint": "http://internal-idp:9000/application/o/token/",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "http://localhost:8080/api/v1/mcp",
        ),
        patch.object(
            mcp_module.settings,
            "OIDC_AUTHORITY",
            "http://localhost:9000/application/o/seizu",
        ),
        patch.object(
            mcp_module.settings,
            "OIDC_INTERNAL_AUTHORITY",
            "http://internal-idp:9000/application/o/seizu",
        ),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["authorization_endpoint"] == "http://localhost:9000/application/o/authorize/"
        assert result["token_endpoint"] == "http://localhost:9000/application/o/token/"


async def test_build_oauth_metadata_returns_none_when_discovery_fails():
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        assert await _build_oauth_metadata() is None


async def test_build_oauth_metadata_includes_jwks_uri_from_discovery():
    discovery_doc = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
        "jwks_uri": "https://idp.example.com/jwks",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["jwks_uri"] == "https://idp.example.com/jwks"


# ---------------------------------------------------------------------------
# _MCPAuthMiddleware
# ---------------------------------------------------------------------------


async def test_auth_middleware_passes_through_when_auth_disabled():
    from reporting.authnz import CurrentUser
    from reporting.schema.report_config import User
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    dev_user = CurrentUser(
        user=User(
            user_id="dev",
            sub="dev@example.com",
            iss="dev",
            email="dev@example.com",
            display_name=None,
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )

    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", False),
        patch.object(mcp_module, "_build_dev_current_user", AsyncMock(return_value=dev_user)),
    ):
        scope = {"type": "http", "path": "/mcp"}
        await middleware(scope, AsyncMock(), AsyncMock())

    inner.assert_called_once()


async def test_auth_middleware_passes_well_known_unauthenticated():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True):
        scope = {
            "type": "http",
            "path": "/.well-known/oauth-authorization-server",
            "headers": [],
        }
        await middleware(scope, AsyncMock(), AsyncMock())

    inner.assert_called_once()


async def test_auth_middleware_returns_401_when_no_token():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})
        sent = []

        async def capture_send(message):
            sent.append(message)

        await middleware(scope, receive, capture_send)

    status_messages = [m for m in sent if m.get("type") == "http.response.start"]
    assert any(m.get("status") == 401 for m in status_messages)


async def test_auth_middleware_passes_protected_resource_unauthenticated():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True):
        scope = {
            "type": "http",
            "path": "/.well-known/oauth-protected-resource",
            "headers": [],
        }
        await middleware(scope, AsyncMock(), AsyncMock())

    inner.assert_called_once()


@pytest.mark.parametrize(
    "path",
    [
        # in-path form (served under MCP prefix)
        "/api/v1/mcp/.well-known/oauth-authorization-server",
        # origin-based form (MCP client derives from server URL origin)
        "/.well-known/oauth-authorization-server",
        # RFC 8414 path-suffix form
        "/.well-known/oauth-authorization-server/api/v1/mcp",
    ],
)
async def test_auth_middleware_passes_all_well_known_auth_server_forms(path: str):
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True):
        scope = {"type": "http", "path": path, "headers": []}
        await middleware(scope, AsyncMock(), AsyncMock())

    inner.assert_called_once()


async def test_auth_middleware_401_includes_resource_metadata_when_oauth_configured():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True),
        patch.object(
            mcp_module.settings,
            "OIDC_AUTHORITY",
            "https://idp.example.com",
        ),
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
    ):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})
        sent = []

        async def capture_send(message):
            sent.append(message)

        await middleware(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 401
    headers = dict(start["headers"])
    www_auth = headers[b"www-authenticate"].decode()
    expected_metadata_url = "https://seizu.example.com/api/v1/mcp/.well-known/oauth-protected-resource"
    assert f'resource_metadata="{expected_metadata_url}"' in www_auth


async def test_auth_middleware_401_plain_bearer_when_oauth_not_configured():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True),
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch.object(mcp_module.settings, "MCP_RESOURCE_URL", ""),
    ):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/mcp",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})
        sent = []

        async def capture_send(message):
            sent.append(message)

        await middleware(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 401
    headers = dict(start["headers"])
    www_auth = headers[b"www-authenticate"].decode()
    assert www_auth == "Bearer"


async def test_auth_middleware_returns_401_on_bad_jwt_claims():
    """Malformed JWT claims (KeyError) must return 401, not 500."""
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    async def _bad_user(_payload):
        raise KeyError("email")

    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True),
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_RESOURCE_URL", ""),
        patch.object(mcp_module.settings, "JWKS_URL", "https://idp.example.com/jwks"),
        patch(
            "reporting.services.mcp_server.validate_bearer_token",
            new=AsyncMock(return_value={"sub": "u1"}),
        ),
        patch(
            "reporting.services.mcp_server._build_current_user_from_jwt",
            side_effect=_bad_user,
        ),
    ):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "scheme": "https",
            "headers": [
                (b"host", b"seizu.example.com"),
                (b"authorization", b"Bearer validtoken"),
            ],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})
        sent = []

        async def capture_send(message):
            sent.append(message)

        await middleware(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 401


async def test_auth_middleware_passes_non_http_scope():
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)

    scope = {"type": "lifespan"}
    await middleware(scope, AsyncMock(), AsyncMock())

    inner.assert_called_once()


async def test_build_dev_current_user_uses_configured_identity():
    from reporting.services.mcp_server import _build_dev_current_user

    user = User(
        user_id="dev-user",
        sub="developer@example.com",
        iss="dev",
        email="developer@example.com",
        display_name=None,
        created_at=_NOW,
        last_login=_NOW,
    )
    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_AUTH_USER_EMAIL", "developer@example.com"),
        patch.object(
            mcp_module.report_store,
            "get_or_create_user",
            new=AsyncMock(return_value=user),
        ) as get_user,
    ):
        current_user = await _build_dev_current_user()

    assert current_user.user == user
    assert current_user.permissions == ALL_PERMISSIONS
    get_user.assert_awaited_once_with(
        sub="developer@example.com",
        iss="dev",
        email="developer@example.com",
        display_name=None,
        preferred_username=None,
    )


async def test_build_current_user_from_jwt_resolves_profile_and_permissions():
    from reporting.services.mcp_server import _build_current_user_from_jwt

    user = User(
        user_id="user-1",
        sub="subject-1",
        iss="https://issuer.example.com",
        email="user@example.com",
        display_name="User One",
        preferred_username="user1",
        created_at=_NOW,
        last_login=_NOW,
    )
    permissions = frozenset({"chat:use"})
    with (
        patch.object(mcp_module.report_store, "get_or_create_user", new=AsyncMock(return_value=user)) as get_user,
        patch(
            "reporting.authnz.permissions.resolve_permissions",
            new=AsyncMock(return_value=permissions),
        ),
    ):
        current_user = await _build_current_user_from_jwt(
            {
                "sub": "subject-1",
                "iss": "https://issuer.example.com",
                "email": "user@example.com",
                "preferred_username": "user1",
                "name": "User One",
                "iat": 1_700_000_000,
                "exp": 1_700_003_600,
            }
        )

    assert current_user.user == user
    assert current_user.permissions == permissions
    assert current_user.jwt_claims["token_iat"] is not None
    assert current_user.jwt_claims["token_exp"] is not None
    get_user.assert_awaited_once_with(
        sub="subject-1",
        iss="https://issuer.example.com",
        email="user@example.com",
        display_name="User One",
        preferred_username="user1",
        role=None,
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"iss": "issuer"}, "sub"),
        ({"sub": "subject"}, "iss"),
        ({"sub": "subject", "iss": "issuer", "email": 123}, "email"),
        ({"sub": "subject", "iss": "issuer", "preferred_username": 123}, "preferred_username"),
    ],
)
async def test_build_current_user_from_jwt_rejects_invalid_identity_claims(payload, message):
    from reporting.services.mcp_server import _build_current_user_from_jwt

    with pytest.raises(ValueError, match=message):
        await _build_current_user_from_jwt(payload)


@pytest.mark.parametrize(
    ("authorization", "token_error"),
    [
        (b"Basic abc", None),
        (b"Bearer invalid", mcp_module.jwt.InvalidTokenError("bad token")),
    ],
)
async def test_auth_middleware_rejects_invalid_authorization(authorization, token_error):
    from reporting.services.mcp_server import _MCPAuthMiddleware

    inner = AsyncMock()
    middleware = _MCPAuthMiddleware(inner)
    validate = AsyncMock(side_effect=token_error) if token_error else AsyncMock()
    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True),
        patch.object(mcp_module, "validate_bearer_token", validate),
    ):
        sent = []

        async def capture_send(message):
            sent.append(message)

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"authorization", authorization)],
                "query_string": b"",
            },
            AsyncMock(return_value={"type": "http.request", "body": b""}),
            capture_send,
        )

    inner.assert_not_called()
    assert next(message for message in sent if message["type"] == "http.response.start")["status"] == 401


@pytest.mark.parametrize(
    ("headers", "expected_session_source"),
    [
        ([(b"authorization", b"Bearer valid"), (b"mcp-session-id", b"session-1")], "session-1"),
        ([(b"authorization", b"Bearer valid")], "user-1"),
    ],
)
async def test_auth_middleware_sets_authenticated_context(headers, expected_session_source):
    from reporting.authnz import CurrentUser
    from reporting.services.action_confirmations import bearer_session_key
    from reporting.services.mcp_server import _MCPAuthMiddleware

    user = User(
        user_id="user-1",
        sub="subject",
        iss="issuer",
        email="user@example.com",
        display_name=None,
        created_at=_NOW,
        last_login=_NOW,
    )
    current_user = CurrentUser(user=user, jwt_claims={}, permissions=frozenset({"chat:use"}))
    captured = {}

    async def inner(scope, receive, send):
        captured["permissions"] = mcp_module._mcp_permissions.get()
        captured["user"] = mcp_module._mcp_current_user.get()
        captured["session"] = mcp_module._mcp_session_key.get()

    middleware = _MCPAuthMiddleware(inner)
    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", True),
        patch.object(mcp_module, "validate_bearer_token", new=AsyncMock(return_value={"sub": "subject"})),
        patch.object(mcp_module, "_build_current_user_from_jwt", new=AsyncMock(return_value=current_user)),
    ):
        await middleware(
            {"type": "http", "method": "POST", "path": "/mcp", "headers": headers},
            AsyncMock(),
            AsyncMock(),
        )

    assert captured == {
        "permissions": frozenset({"chat:use"}),
        "user": current_user,
        "session": bearer_session_key(expected_session_source),
    }


# ---------------------------------------------------------------------------
# _build_protected_resource_metadata
# ---------------------------------------------------------------------------


def test_build_protected_resource_metadata_returns_none_when_not_configured():
    from reporting.services.mcp_server import _build_protected_resource_metadata

    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch.object(mcp_module.settings, "MCP_RESOURCE_URL", ""),
    ):
        assert _build_protected_resource_metadata() is None


def test_build_protected_resource_metadata_returns_none_when_resource_url_missing():
    from reporting.services.mcp_server import _build_protected_resource_metadata

    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch.object(mcp_module.settings, "MCP_RESOURCE_URL", ""),
    ):
        assert _build_protected_resource_metadata() is None


def test_build_protected_resource_metadata_uses_explicit_issuer():
    from reporting.services.mcp_server import _build_protected_resource_metadata

    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
    ):
        result = _build_protected_resource_metadata()
        assert result is not None
        assert result["resource"] == "https://seizu.example.com/api/v1/mcp"
        assert result["authorization_servers"] == ["https://idp.example.com"]


def test_build_protected_resource_metadata_points_to_mcp_server_when_no_explicit_issuer():
    # When MCP_OAUTH_ISSUER is not set, authorization_servers always points to
    # our own MCP server so MCP clients discover our RFC 8414 endpoint rather
    # than an IdP that may not support it (e.g. Authentik).
    from reporting.services.mcp_server import _build_protected_resource_metadata

    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
    ):
        result = _build_protected_resource_metadata()
        assert result is not None
        assert result["authorization_servers"] == ["https://seizu.example.com/api/v1/mcp"]


# ---------------------------------------------------------------------------
# _MCPDispatcher URL form tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        # in-path form
        "/api/v1/mcp/.well-known/oauth-authorization-server",
        # origin-based form
        "/.well-known/oauth-authorization-server",
        # RFC 8414 path-suffix form
        "/.well-known/oauth-authorization-server/api/v1/mcp",
    ],
)
async def test_dispatcher_serves_oauth_metadata_for_all_url_forms(path: str):
    from reporting.services.mcp_server import _MCPDispatcher

    inner = AsyncMock()
    dispatcher = _MCPDispatcher(inner)

    with (
        patch(
            "reporting.services.mcp_server._build_oauth_metadata",
            new=AsyncMock(
                return_value={
                    "issuer": "http://localhost:8080/api/v1/mcp",
                    "authorization_endpoint": "http://idp.example.com/authorize",
                    "token_endpoint": "http://idp.example.com/token",
                }
            ),
        ),
    ):
        sent = []

        async def capture_send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b""})
        await dispatcher(scope, receive, capture_send)

    # Should have sent a response (not forwarded to inner MCP app)
    inner.assert_not_called()
    start_messages = [m for m in sent if m.get("type") == "http.response.start"]
    assert any(m.get("status") == 200 for m in start_messages)


# ---------------------------------------------------------------------------
# _build_oauth_metadata — registration_endpoint
# ---------------------------------------------------------------------------


async def test_build_oauth_metadata_includes_registration_endpoint_from_resource_url():
    """Built-in DCR endpoint is derived from MCP_RESOURCE_URL when OIDC_CLIENT_ID is set."""
    discovery_doc = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_REGISTRATION_ENDPOINT", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_CLIENT_ID", "my-client-id"),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["registration_endpoint"] == (
            "https://seizu.example.com/api/v1/mcp/.well-known/oauth-registration"
        )


async def test_build_oauth_metadata_uses_explicit_registration_endpoint():
    """MCP_OAUTH_REGISTRATION_ENDPOINT overrides the built-in derived endpoint."""
    with (
        patch.object(
            mcp_module.settings,
            "MCP_OAUTH_AUTHORIZATION_ENDPOINT",
            "https://idp.example.com/authorize",
        ),
        patch.object(
            mcp_module.settings,
            "MCP_OAUTH_TOKEN_ENDPOINT",
            "https://idp.example.com/token",
        ),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", "https://idp.example.com"),
        patch.object(
            mcp_module.settings,
            "MCP_OAUTH_REGISTRATION_ENDPOINT",
            "https://idp.example.com/register",
        ),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_CLIENT_ID", "my-client-id"),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert result["registration_endpoint"] == "https://idp.example.com/register"


async def test_build_oauth_metadata_omits_registration_endpoint_when_no_client_id():
    """No registration_endpoint when OIDC_CLIENT_ID is empty and no explicit override."""
    discovery_doc = {
        "authorization_endpoint": "https://idp.example.com/authorize",
        "token_endpoint": "https://idp.example.com/token",
    }
    with (
        patch.object(mcp_module.settings, "MCP_OAUTH_AUTHORIZATION_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_TOKEN_ENDPOINT", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_ISSUER", ""),
        patch.object(mcp_module.settings, "MCP_OAUTH_REGISTRATION_ENDPOINT", ""),
        patch.object(
            mcp_module.settings,
            "MCP_RESOURCE_URL",
            "https://seizu.example.com/api/v1/mcp",
        ),
        patch.object(mcp_module.settings, "OIDC_AUTHORITY", "https://idp.example.com"),
        patch.object(mcp_module.settings, "OIDC_CLIENT_ID", ""),
        patch.object(mcp_module.settings, "OIDC_INTERNAL_AUTHORITY", ""),
        patch.object(mcp_module.settings, "OIDC_SCOPE", "openid email"),
        patch(
            "reporting.services.mcp_server._fetch_oidc_discovery",
            new_callable=AsyncMock,
            return_value=discovery_doc,
        ),
    ):
        result = await _build_oauth_metadata()
        assert result is not None
        assert "registration_endpoint" not in result


# ---------------------------------------------------------------------------
# _oauth_registration_handler
# ---------------------------------------------------------------------------


async def test_registration_handler_returns_client_id_and_echoes_redirect_uris():
    from starlette.requests import Request

    req_body = json.dumps({"redirect_uris": ["http://localhost:3000/auth/callback"]}).encode()
    with patch.object(mcp_module.settings, "OIDC_CLIENT_ID", "test-client-id"):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/.well-known/oauth-registration",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": req_body, "more_body": False})
        sent = []

        async def capture_send(message):
            sent.append(message)

        request = Request(scope, receive)
        response = await _oauth_registration_handler(request)
        await response(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 201
    body_parts = [m["body"] for m in sent if m.get("type") == "http.response.body"]
    body = json.loads(b"".join(body_parts))
    assert body["client_id"] == "test-client-id"
    assert body["redirect_uris"] == ["http://localhost:3000/auth/callback"]
    assert body["token_endpoint_auth_method"] == "none"


async def test_registration_handler_empty_body_returns_empty_redirect_uris():
    from starlette.requests import Request

    with patch.object(mcp_module.settings, "OIDC_CLIENT_ID", "test-client-id"):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/.well-known/oauth-registration",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b"{}", "more_body": False})
        sent = []

        async def capture_send(message):
            sent.append(message)

        request = Request(scope, receive)
        response = await _oauth_registration_handler(request)
        await response(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 201
    body_parts = [m["body"] for m in sent if m.get("type") == "http.response.body"]
    body = json.loads(b"".join(body_parts))
    assert body["redirect_uris"] == []


async def test_registration_handler_returns_400_when_no_client_id():
    from starlette.requests import Request

    with patch.object(mcp_module.settings, "OIDC_CLIENT_ID", ""):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/.well-known/oauth-registration",
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b"{}", "more_body": False})
        sent = []

        async def capture_send(message):
            sent.append(message)

        request = Request(scope, receive)
        response = await _oauth_registration_handler(request)
        await response(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 400


async def test_registration_handler_returns_405_for_get():
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/.well-known/oauth-registration",
        "headers": [],
        "query_string": b"",
    }
    receive = AsyncMock(return_value={"type": "http.request", "body": b""})
    sent = []

    async def capture_send(message):
        sent.append(message)

    request = Request(scope, receive)
    response = await _oauth_registration_handler(request)
    await response(scope, receive, capture_send)

    start = next(m for m in sent if m.get("type") == "http.response.start")
    assert start["status"] == 405


# ---------------------------------------------------------------------------
# _MCPDispatcher — registration endpoint routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/mcp/.well-known/oauth-registration",
        "/.well-known/oauth-registration",
    ],
)
async def test_dispatcher_routes_registration_endpoint(path: str):
    from reporting.services.mcp_server import _MCPDispatcher

    inner = AsyncMock()
    dispatcher = _MCPDispatcher(inner)

    with patch.object(mcp_module.settings, "OIDC_CLIENT_ID", "test-client"):
        sent = []

        async def capture_send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
        }
        receive = AsyncMock(return_value={"type": "http.request", "body": b"{}", "more_body": False})
        await dispatcher(scope, receive, capture_send)

    inner.assert_not_called()
    start_messages = [m for m in sent if m.get("type") == "http.response.start"]
    assert any(m.get("status") == 201 for m in start_messages)


# ---------------------------------------------------------------------------
# Streamable HTTP transport — both protocol eras over the real ASGI app
# ---------------------------------------------------------------------------

# The 2026-07-28 revision drops the initialize handshake: every request carries
# its own protocol version and client capabilities in ``params._meta``, plus
# ``MCP-Method`` (and ``MCP-Name`` for named calls) as headers the server checks
# against the body. A 2025-era client sends none of that. Both must reach the
# same handlers.
_MODERN_VERSION = "2026-07-28"
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}


@contextlib.asynccontextmanager
async def _mcp_http_client():
    """Serve ``get_mcp_app()`` over ASGI with auth disabled, as dev mode does."""
    from reporting.services.mcp_server import get_mcp_app

    session_manager, asgi_app = get_mcp_app()
    dev_user = User(
        user_id="u1",
        sub="testuser",
        iss="dev",
        email="testuser",
        display_name=None,
        created_at=_NOW,
        last_login=_NOW,
    )
    with (
        patch.object(mcp_module.settings, "DEVELOPMENT_ONLY_REQUIRE_AUTH", False),
        patch.object(mcp_module.report_store, "get_or_create_user", new_callable=AsyncMock, return_value=dev_user),
    ):
        async with session_manager.run():
            transport = httpx.ASGITransport(app=asgi_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client


async def test_streamable_http_serves_legacy_client():
    """A 2025-era client still gets a handshake and a tool listing."""
    async with _mcp_http_client() as client:
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        init = await client.post(
            "/api/v1/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert init.json()["result"]["protocolVersion"] == "2025-06-18"

        listed = await client.post(
            "/api/v1/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )

    tools = listed.json()["result"]["tools"]
    assert "graph__schema" in {tool["name"] for tool in tools}
    # The wire form stays camelCase even though the SDK's Python attribute is
    # now ``input_schema``.
    assert all("inputSchema" in tool for tool in tools)


async def test_streamable_http_serves_2026_client_without_handshake():
    """A 2026-07-28 client lists and calls tools with no initialize at all."""
    async with _mcp_http_client() as client:
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": _MODERN_VERSION}
        listed = await client.post(
            "/api/v1/mcp",
            headers={**headers, "MCP-Method": "tools/list"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _MODERN_META}},
        )

        with patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.fetch_graph_schema",
            new_callable=AsyncMock,
            return_value={"labels": ["CVE"], "relationship_types": [], "property_keys": [], "indexes": []},
        ):
            called = await client.post(
                "/api/v1/mcp",
                headers={**headers, "MCP-Method": "tools/call", "MCP-Name": "graph__schema"},
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "graph__schema", "arguments": {}, "_meta": _MODERN_META},
                },
            )

    assert "graph__schema" in {tool["name"] for tool in listed.json()["result"]["tools"]}
    result = called.json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["labels"] == ["CVE"]


# ---------------------------------------------------------------------------
# Invalid arguments over the wire, in both protocol eras
# ---------------------------------------------------------------------------
#
# MCP 1.x validated arguments against the advertised inputSchema inside the
# SDK's call_tool wrapper. The 2.x callbacks do not, so these assert the
# replacement in the runtime is actually reached over the transport -- and that
# a rejection arrives as a readable tool result rather than a JSON-RPC error.


async def test_invalid_arguments_are_rejected_for_a_legacy_client():
    async with _mcp_http_client() as client:
        headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
        with patch(
            "reporting.services.mcp_builtins.reports.report_store.pin_report",
            new_callable=AsyncMock,
        ) as pin:
            resp = await client.post(
                "/api/v1/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    # "false" is a string; the schema says boolean.
                    "params": {"name": "reports__pin", "arguments": {"report_id": "r1", "pinned": "false"}},
                },
            )

    body = resp.json()
    assert "error" not in body, "a refused call must be a result, not a JSON-RPC protocol error"
    # ...but still flagged as a failed call, the way the 1.x SDK reported it.
    assert body["result"]["isError"] is True
    assert "Input validation error" in json.loads(body["result"]["content"][0]["text"])["error"]
    pin.assert_not_awaited()


async def test_invalid_arguments_are_rejected_for_a_2026_client():
    async with _mcp_http_client() as client:
        headers = {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _MODERN_VERSION,
            "MCP-Method": "tools/call",
            "MCP-Name": "reports__pin",
        }
        with patch(
            "reporting.services.mcp_builtins.reports.report_store.pin_report",
            new_callable=AsyncMock,
        ) as pin:
            resp = await client.post(
                "/api/v1/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "reports__pin",
                        "arguments": {"report_id": "r1", "pinned": []},
                        "_meta": _MODERN_META,
                    },
                },
            )

    body = resp.json()
    assert "error" not in body, "a refused call must be a result, not a JSON-RPC protocol error"
    # ...but still flagged as a failed call, the way the 1.x SDK reported it.
    assert body["result"]["isError"] is True
    assert "Input validation error" in json.loads(body["result"]["content"][0]["text"])["error"]
    pin.assert_not_awaited()


async def test_a_successful_call_is_not_flagged_as_an_error():
    """is_error must stay false for a call that actually ran."""
    async with _mcp_http_client() as client:
        with patch(
            "reporting.services.mcp_builtins.graph.reporting_neo4j.fetch_graph_schema",
            new_callable=AsyncMock,
            return_value={"labels": ["CVE"], "relationship_types": [], "property_keys": [], "indexes": []},
        ):
            resp = await client.post(
                "/api/v1/mcp",
                headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "graph__schema", "arguments": {}},
                },
            )

    assert resp.json()["result"]["isError"] is False
