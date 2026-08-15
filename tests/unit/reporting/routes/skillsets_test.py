from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.external_mcp import ExternalMCPProxy
from reporting.schema.mcp_config import (
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
    ToolParamDef,
)
from reporting.schema.report_config import User
from reporting.services import external_mcp

_NOW = "2024-01-01T00:00:00+00:00"
_SKILLSET_ID = "incident_response"
_SKILL_ID = "summarize_findings"

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="user@example.com",
    display_name="Test User",
    created_at=_NOW,
    last_login=_NOW,
)
_FAKE_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)


def _make_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_CURRENT_USER
    return app


def _skillset_item(enabled: bool = True) -> SkillsetListItem:
    return SkillsetListItem(
        skillset_id=_SKILLSET_ID,
        name="Incident Response",
        description="IR prompt templates",
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="test-user-id",
    )


def _skill_item(enabled: bool = True) -> SkillItem:
    return SkillItem(
        skill_id=_SKILL_ID,
        skillset_id=_SKILLSET_ID,
        name="Summarize Findings",
        description="Summarize graph findings",
        template="Write {% $count %} findings about {% $topic %}. Concise={% $concise %}",
        parameters=[
            ToolParamDef(name="topic", type="string", required=True),
            ToolParamDef(name="count", type="integer", required=False, default=3),
            ToolParamDef(name="concise", type="boolean", required=False, default=True),
        ],
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="test-user-id",
    )


def _skillset_version(version: int = 1) -> SkillsetVersion:
    return SkillsetVersion(
        skillset_id=_SKILLSET_ID,
        name="Incident Response",
        description="IR prompt templates",
        enabled=True,
        version=version,
        created_at=_NOW,
        created_by="test-user-id",
        comment=None,
    )


def _skill_version(version: int = 1) -> SkillVersion:
    return SkillVersion(
        skill_id=_SKILL_ID,
        skillset_id=_SKILLSET_ID,
        name="Summarize Findings",
        description="Summarize graph findings",
        template="Write {% $topic %}",
        parameters=[ToolParamDef(name="topic", type="string", required=True)],
        triggers=[],
        tools_required=[],
        enabled=True,
        version=version,
        created_at=_NOW,
        created_by="test-user-id",
        comment=None,
    )


def _tool_item() -> ToolItem:
    return ToolItem(
        tool_id="lookup_alerts",
        toolset_id="graph_tools",
        name="Lookup Alerts",
        description="Lookup alert context",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="test-user-id",
    )


async def test_list_get_update_delete_skillsets_and_versions(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.list_skillsets",
        new=AsyncMock(return_value=[_skillset_item()]),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    update_skillset = mocker.patch(
        "reporting.routes.skillsets.report_store.update_skillset",
        new=AsyncMock(return_value=_skillset_item(enabled=False)),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.delete_skillset",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.list_skillset_versions",
        new=AsyncMock(return_value=[_skillset_version()]),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset_version",
        new=AsyncMock(return_value=_skillset_version()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/skillsets")).json()["skillsets"][0]["skillset_id"] == _SKILLSET_ID
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}")).json()["skillset_id"] == _SKILLSET_ID
        ret = await client.put(
            f"/api/v1/skillsets/{_SKILLSET_ID}",
            json={"name": "Incident Response", "description": "new", "enabled": False, "comment": "v2"},
        )
        assert ret.status_code == 200
        assert ret.json()["enabled"] is False
        assert (await client.delete(f"/api/v1/skillsets/{_SKILLSET_ID}")).json()["skillset_id"] == _SKILLSET_ID
        versions = await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/versions")
        assert versions.json()["versions"][0]["version"] == 1
        version = await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/versions/1")
        assert version.json()["version"] == 1

    update_skillset.assert_awaited_once()


async def test_skillset_not_found_paths(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.update_skillset",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.delete_skillset",
        new=AsyncMock(return_value=False),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}")).status_code == 404
        assert (
            await client.put(
                f"/api/v1/skillsets/{_SKILLSET_ID}",
                json={"name": "Missing", "description": "", "enabled": True},
            )
        ).status_code == 404
        assert (await client.delete(f"/api/v1/skillsets/{_SKILLSET_ID}")).status_code == 404
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/versions")).status_code == 404
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/versions/9")).status_code == 404


async def test_create_skillset_success(mocker):
    get_skillset = mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=None),
    )
    create_skillset = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    app = _make_app()
    body = {
        "skillset_id": _SKILLSET_ID,
        "name": "Incident Response",
        "description": "IR prompt templates",
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/skillsets", json=body)

    assert ret.status_code == 201
    assert ret.json()["skillset_id"] == _SKILLSET_ID
    get_skillset.assert_awaited_once_with(_SKILLSET_ID)
    create_skillset.assert_awaited_once_with(
        skillset_id=_SKILLSET_ID,
        name="Incident Response",
        description="IR prompt templates",
        enabled=True,
        created_by="test-user-id",
    )


async def test_create_skillset_rejects_invalid_id():
    app = _make_app()
    body = {
        "skillset_id": "Incident-Response",
        "name": "Incident Response",
        "description": "",
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/skillsets", json=body)

    assert ret.status_code == 422


async def test_create_skill_rejects_unknown_template_placeholder(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $missing_param %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 400
    assert "missing_param" in ret.text
    create_skill.assert_not_called()


async def test_create_skill_accepts_structured_metadata_and_valid_tool_refs(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_tool",
        new=AsyncMock(return_value=_tool_item()),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(
            return_value=_skill_item().model_copy(
                update={
                    "triggers": ["summarize alerts"],
                    "tools_required": ["graph_tools__lookup_alerts"],
                }
            )
        ),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "triggers": ["summarize alerts"],
        "tools_required": ["graph_tools__lookup_alerts"],
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 201
    assert ret.json()["triggers"] == ["summarize alerts"]
    assert ret.json()["tools_required"] == ["graph_tools__lookup_alerts"]
    create_skill.assert_awaited_once()
    assert create_skill.await_args.kwargs["triggers"] == ["summarize alerts"]
    assert create_skill.await_args.kwargs["tools_required"] == ["graph_tools__lookup_alerts"]


async def test_create_skill_drops_missing_required_tool_refs(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(return_value=_skill_item().model_copy(update={"tools_required": []})),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "tools_required": ["graph_tools__missing"],
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 201
    assert ret.json()["tools_required"] == []
    create_skill.assert_awaited_once()
    assert create_skill.await_args.kwargs["tools_required"] == []


async def test_create_skill_keeps_builtin_tool_refs_even_when_catalog_lookup_missing(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(return_value=_skill_item().model_copy(update={"tools_required": ["graph__query"]})),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "tools_required": ["graph__query"],
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 201
    assert ret.json()["tools_required"] == ["graph__query"]
    create_skill.assert_awaited_once()
    assert create_skill.await_args.kwargs["tools_required"] == ["graph__query"]


async def test_create_skill_keeps_external_tool_refs_for_configured_proxies(mocker):
    mocker.patch.object(
        external_mcp.settings,
        "MCP_EXTERNAL_PROXIES",
        [
            ExternalMCPProxy(
                name="github",
                url="https://proxy.example/mcp/github",
                transport="streamable_http",
                auth_mode="bearer",
                token_env="MCP_EXTERNAL_PROXY_TOKEN",
            )
        ],
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    # An external tool has no stored catalog row; it is discovered per user.
    get_tool = mocker.patch(
        "reporting.routes.skillsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    tools = ["ext__github__get_file_contents", "ext__github__search_code"]
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(return_value=_skill_item().model_copy(update={"tools_required": tools})),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "tools_required": tools,
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 201
    assert ret.json()["tools_required"] == tools
    create_skill.assert_awaited_once()
    assert create_skill.await_args.kwargs["tools_required"] == tools
    get_tool.assert_not_awaited()


async def test_create_skill_drops_external_tool_refs_for_unknown_proxies(mocker):
    mocker.patch.object(external_mcp.settings, "MCP_EXTERNAL_PROXIES", [])
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_tool",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    create_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.create_skill",
        new=AsyncMock(return_value=_skill_item().model_copy(update={"tools_required": []})),
    )
    app = _make_app()
    body = {
        "skill_id": _SKILL_ID,
        "name": "Summarize Findings",
        "description": "",
        "template": "Summarize {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "tools_required": ["ext__nowhere__get_file_contents"],
        "enabled": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(f"/api/v1/skillsets/{_SKILLSET_ID}/skills", json=body)

    assert ret.status_code == 201
    create_skill.assert_awaited_once()
    assert create_skill.await_args.kwargs["tools_required"] == []


async def test_list_skills_marks_parent_disabled_skills_effectively_disabled(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item(enabled=False)),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.list_skills",
        new=AsyncMock(return_value=[_skill_item()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills")

    assert ret.status_code == 200
    item = ret.json()["skills"][0]
    assert item["enabled"] is True
    assert item["effective_enabled"] is False
    assert item["disabled_reason"] == "skillset_disabled"


async def test_get_update_delete_skills_and_versions(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=_skill_item()),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    update_skill = mocker.patch(
        "reporting.routes.skillsets.report_store.update_skill",
        new=AsyncMock(return_value=_skill_item(enabled=False)),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.delete_skill",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.list_skill_versions",
        new=AsyncMock(return_value=[_skill_version()]),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill_version",
        new=AsyncMock(return_value=_skill_version()),
    )
    app = _make_app()
    update_body = {
        "name": "Summarize Findings",
        "description": "new",
        "template": "Write {% $topic %}",
        "parameters": [{"name": "topic", "type": "string", "required": True}],
        "triggers": [],
        "tools_required": [],
        "enabled": False,
        "comment": "v2",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}")).json()[
            "skill_id"
        ] == _SKILL_ID
        ret = await client.put(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}", json=update_body)
        assert ret.status_code == 200
        assert ret.json()["enabled"] is False
        assert (await client.delete(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}")).json()[
            "skill_id"
        ] == _SKILL_ID
        versions = await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/versions")
        assert versions.json()["versions"][0]["version"] == 1
        version = await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/versions/1")
        assert version.json()["version"] == 1

    update_skill.assert_awaited_once()


async def test_skill_not_found_paths(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=None),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill_version",
        new=AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}")).status_code == 404
        assert (
            await client.put(
                f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}",
                json={
                    "name": "Missing",
                    "description": "",
                    "template": "x",
                    "parameters": [],
                    "enabled": True,
                },
            )
        ).status_code == 404
        assert (await client.delete(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}")).status_code == 404
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/versions")).status_code == 404
        assert (await client.get(f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/versions/9")).status_code == 404


async def test_render_skill_applies_defaults_and_coerces_argument_types(mocker):
    skill = _skill_item().model_copy(
        update={
            "triggers": ["summarize alerts"],
            "tools_required": ["graph_tools__lookup_alerts"],
        }
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=skill),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    app = _make_app()
    body = {"arguments": {"topic": "graph alerts", "count": "5", "concise": "false"}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/render",
            json=body,
        )

    assert ret.status_code == 200
    assert ret.json() == {
        "text": (
            "---\n"
            'triggers:\n  - "summarize alerts"\n'
            'tools_required:\n  - "graph_tools__lookup_alerts"\n'
            "---\n"
            "Write 5 findings about graph alerts. Concise=False"
        )
    }


async def test_render_skill_rejects_missing_required_argument(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=_skill_item()),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/render",
            json={"arguments": {}},
        )

    assert ret.status_code == 400
    assert "Required parameter 'topic' is missing" in ret.text


async def test_render_skill_rejects_disabled_skillset(mocker):
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skill",
        new=AsyncMock(return_value=_skill_item()),
    )
    mocker.patch(
        "reporting.routes.skillsets.report_store.get_skillset",
        new=AsyncMock(return_value=_skillset_item(enabled=False)),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            f"/api/v1/skillsets/{_SKILLSET_ID}/skills/{_SKILL_ID}/render",
            json={"arguments": {"topic": "alerts"}},
        )

    assert ret.status_code == 400
    assert "skillset" in ret.text.lower()
    assert "disabled" in ret.text.lower()
