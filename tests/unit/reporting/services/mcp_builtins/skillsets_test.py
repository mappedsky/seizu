"""Tests for the ``skillsets__*`` MCP built-in group."""

import json
from unittest.mock import AsyncMock, patch

from mcp import types as mcp_types

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.mcp_config import SkillItem, SkillsetListItem, SkillsetVersion, SkillVersion, ToolParamDef
from reporting.schema.report_config import User
from reporting.services.mcp_server import _build_mcp_server, _mcp_current_user, _mcp_permissions, _mcp_session_key

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


async def _call(server, name, arguments):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    perm_tok = _mcp_permissions.set(ALL_PERMISSIONS)
    user_tok = _mcp_current_user.set(_current_user())
    session_tok = _mcp_session_key.set("test-session")
    try:
        with patch(
            "reporting.services.mcp_runtime.action_confirmations.ensure_confirmation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await handler(req)
    finally:
        _mcp_permissions.reset(perm_tok)
        _mcp_current_user.reset(user_tok)
        _mcp_session_key.reset(session_tok)
    return json.loads(result.root.content[0].text)


def _skillset(enabled: bool = True) -> SkillsetListItem:
    return SkillsetListItem(
        skillset_id="ss1",
        name="Skillset",
        description="desc",
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _skillset_version() -> SkillsetVersion:
    return SkillsetVersion(
        skillset_id="ss1",
        name="Skillset",
        description="desc",
        enabled=True,
        version=1,
        created_at=_NOW,
        created_by="u1",
    )


def _skill(skillset_id: str = "ss1", skill_id: str = "sk1", enabled: bool = True) -> SkillItem:
    return SkillItem(
        skill_id=skill_id,
        skillset_id=skillset_id,
        name="Skill",
        description="desc",
        template="Hello {% $topic %} {% $count %}",
        parameters=[
            ToolParamDef(name="topic", type="string", required=True),
            ToolParamDef(name="count", type="integer", required=False, default=3),
        ],
        triggers=["say hello"],
        tools_required=["toolset__tool"],
        enabled=enabled,
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _skill_version() -> SkillVersion:
    return SkillVersion(
        skill_id="sk1",
        skillset_id="ss1",
        name="Skill",
        description="desc",
        template="Hello {% $topic %}",
        parameters=[ToolParamDef(name="topic", type="string", required=True)],
        triggers=[],
        tools_required=[],
        enabled=True,
        version=1,
        created_at=_NOW,
        created_by="u1",
    )


async def test_skillsets_list_returns_items():
    with patch(
        "reporting.services.mcp_builtins.skillsets.report_store.list_skillsets",
        new_callable=AsyncMock,
        return_value=[_skillset()],
    ):
        data = await _call(_build_mcp_server(), "skillsets__list", {})
    assert data["skillsets"][0]["skillset_id"] == "ss1"


async def test_skillsets_crud_and_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset",
            new_callable=AsyncMock,
            side_effect=[_skillset(), None, _skillset()],
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.create_skillset",
            new_callable=AsyncMock,
            return_value=_skillset(),
        ) as create,
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.update_skillset",
            new_callable=AsyncMock,
            return_value=_skillset(enabled=False),
        ) as update,
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.delete_skillset",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.list_skillset_versions",
            new_callable=AsyncMock,
            return_value=[_skillset_version()],
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset_version",
            new_callable=AsyncMock,
            return_value=_skillset_version(),
        ),
    ):
        server = _build_mcp_server()
        assert (await _call(server, "skillsets__get", {"skillset_id": "ss1"}))["skillset_id"] == "ss1"
        assert (
            await _call(
                server,
                "skillsets__create",
                {"skillset_id": "ss1", "name": "Skillset", "description": "desc", "enabled": True},
            )
        )["skillset_id"] == "ss1"
        assert (
            await _call(
                server,
                "skillsets__update",
                {"skillset_id": "ss1", "name": "Skillset", "description": "desc", "enabled": False},
            )
        )["enabled"] is False
        assert (await _call(server, "skillsets__delete", {"skillset_id": "ss1"})) == {"skillset_id": "ss1"}
        assert len((await _call(server, "skillsets__list_versions", {"skillset_id": "ss1"}))["versions"]) == 1
        assert (await _call(server, "skillsets__get_version", {"skillset_id": "ss1", "version": 1}))["version"] == 1

    create.assert_awaited_once()
    update.assert_awaited_once()


async def test_skillsets_error_paths():
    with (
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.update_skillset",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.delete_skillset",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset_version",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        server = _build_mcp_server()
        assert await _call(server, "skillsets__get", {"skillset_id": "missing"}) == {"error": "Skillset not found"}
        assert await _call(server, "skillsets__update", {"skillset_id": "missing", "name": "n"}) == {
            "error": "Skillset not found"
        }
        assert await _call(server, "skillsets__delete", {"skillset_id": "missing"}) == {"error": "Skillset not found"}
        assert await _call(server, "skillsets__list_versions", {"skillset_id": "missing"}) == {
            "error": "Skillset not found"
        }
        assert await _call(server, "skillsets__get_version", {"skillset_id": "missing", "version": 99}) == {
            "error": "Skillset version not found"
        }


async def test_skills_crud_render_and_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skill",
            new_callable=AsyncMock,
            side_effect=[_skill(), None, _skill(), _skill(), _skill(), _skill()],
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset",
            new_callable=AsyncMock,
            return_value=_skillset(),
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.list_skills",
            new_callable=AsyncMock,
            return_value=[_skill()],
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.create_skill",
            new_callable=AsyncMock,
            return_value=_skill(),
        ) as create,
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.update_skill",
            new_callable=AsyncMock,
            return_value=_skill(enabled=False),
        ) as update,
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.delete_skill",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.list_skill_versions",
            new_callable=AsyncMock,
            return_value=[_skill_version()],
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skill_version",
            new_callable=AsyncMock,
            return_value=_skill_version(),
        ),
    ):
        server = _build_mcp_server()
        assert len((await _call(server, "skillsets__list_skills", {"skillset_id": "ss1"}))["skills"]) == 1
        assert (await _call(server, "skillsets__get_skill", {"skillset_id": "ss1", "skill_id": "sk1"}))[
            "skill_id"
        ] == "sk1"
        create_args = {
            "skillset_id": "ss1",
            "skill_id": "sk1",
            "name": "Skill",
            "description": "desc",
            "template": "Hello {% $topic %}",
            "parameters": [{"name": "topic", "type": "string", "required": True}],
            "triggers": [],
            "tools_required": [],
            "enabled": True,
        }
        assert (await _call(server, "skillsets__create_skill", create_args))["skill_id"] == "sk1"
        update_args = {**create_args, "comment": "update"}
        assert (await _call(server, "skillsets__update_skill", update_args))["enabled"] is False
        assert (
            await _call(
                server,
                "skillsets__render_skill",
                {"skillset_id": "ss1", "skill_id": "sk1", "arguments": {"topic": "world"}},
            )
        )["text"] == "Hello world 3"
        skill_versions = await _call(
            server,
            "skillsets__list_skill_versions",
            {"skillset_id": "ss1", "skill_id": "sk1"},
        )
        assert len(skill_versions["versions"]) == 1
        assert (
            await _call(
                server,
                "skillsets__get_skill_version",
                {"skillset_id": "ss1", "skill_id": "sk1", "version": 1},
            )
        )["version"] == 1
        assert await _call(server, "skillsets__delete_skill", {"skillset_id": "ss1", "skill_id": "sk1"}) == {
            "skill_id": "sk1"
        }

    create.assert_awaited_once()
    update.assert_awaited_once()


async def test_skills_error_paths():
    with (
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skill",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skillset",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.create_skill",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.delete_skill",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "reporting.services.mcp_builtins.skillsets.report_store.get_skill_version",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        server = _build_mcp_server()
        assert await _call(server, "skillsets__list_skills", {"skillset_id": "missing"}) == {
            "error": "Skillset not found"
        }
        assert await _call(server, "skillsets__get_skill", {"skillset_id": "ss1", "skill_id": "missing"}) == {
            "error": "Skill not found"
        }
        missing_update = {
            "skillset_id": "ss1",
            "skill_id": "missing",
            "name": "Missing",
            "description": "",
            "template": "x",
            "parameters": [],
            "triggers": [],
            "tools_required": [],
            "enabled": True,
        }
        assert await _call(server, "skillsets__update_skill", missing_update) == {"error": "Skill not found"}
        assert await _call(server, "skillsets__delete_skill", {"skillset_id": "ss1", "skill_id": "missing"}) == {
            "error": "Skill not found"
        }
        assert await _call(server, "skillsets__render_skill", {"skillset_id": "ss1", "skill_id": "missing"}) == {
            "error": "Skill not found"
        }
        assert await _call(
            server,
            "skillsets__get_skill_version",
            {"skillset_id": "ss1", "skill_id": "missing", "version": 99},
        ) == {"error": "Skill version not found"}
