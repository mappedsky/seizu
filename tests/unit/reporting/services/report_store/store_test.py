"""Tests for the report_store __init__ module (factory and delegators)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting.services import report_store
from reporting.services.report_store.dynamodb import DynamoDBReportStore
from reporting.services.report_store.sql import SQLModelReportStore


@pytest.fixture(autouse=True)
def reset_store():
    """Reset the module-level store singleton between tests."""
    original = report_store._store
    report_store._store = None
    yield
    report_store._store = original


# ---------------------------------------------------------------------------
# get_store factory
# ---------------------------------------------------------------------------


def test_get_store_dynamodb_default(mocker):
    mocker.patch("reporting.settings.REPORT_STORE_BACKEND", "dynamodb")
    store = report_store.get_store()
    assert isinstance(store, DynamoDBReportStore)


def test_get_store_sqlmodel(mocker):
    mocker.patch("reporting.settings.REPORT_STORE_BACKEND", "sqlmodel")
    store = report_store.get_store()
    assert isinstance(store, SQLModelReportStore)


def test_get_store_unknown_backend_raises(mocker):
    mocker.patch("reporting.settings.REPORT_STORE_BACKEND", "unknown")
    with pytest.raises(ValueError, match="Unknown report store backend"):
        report_store.get_store()


def test_get_store_returns_singleton(mocker):
    mocker.patch("reporting.settings.REPORT_STORE_BACKEND", "dynamodb")
    s1 = report_store.get_store()
    s2 = report_store.get_store()
    assert s1 is s2


# ---------------------------------------------------------------------------
# Module-level delegators
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_store():
    store = MagicMock()
    # Make the facade targets async so the delegator tests can assert calls.
    async_methods = {
        "initialize": None,
        "list_reports": [],
        "get_report_metadata": None,
        "get_report_latest": None,
        "get_report_version": None,
        "list_report_versions": [],
        "create_report": None,
        "save_report_version": None,
        "update_report_visibility": None,
        "delete_report": True,
        "pin_report": True,
        "get_dashboard_report_id": None,
        "set_dashboard_report": True,
        "get_dashboard_report": None,
        "get_or_create_user": None,
        "update_user_profile": None,
        "get_user": None,
        "archive_user": True,
        "list_scheduled_queries": [],
        "get_scheduled_query": None,
        "create_scheduled_query": None,
        "update_scheduled_query": None,
        "acquire_scheduled_query_lock": True,
        "record_scheduled_query_result": None,
        "delete_scheduled_query": True,
        "list_scheduled_chats": [],
        "get_scheduled_chat": None,
        "create_scheduled_chat": None,
        "update_scheduled_chat": None,
        "delete_scheduled_chat": True,
        "acquire_scheduled_chat_lock": True,
        "record_scheduled_chat_result": None,
        "list_scheduled_query_versions": [],
        "get_scheduled_query_version": None,
        "list_toolsets": [],
        "get_toolset": None,
        "create_toolset": None,
        "update_toolset": None,
        "delete_toolset": True,
        "list_toolset_versions": [],
        "get_toolset_version": None,
        "list_tools": [],
        "get_tool": None,
        "create_tool": None,
        "update_tool": None,
        "delete_tool": True,
        "list_tool_versions": [],
        "get_tool_version": None,
        "list_enabled_tools": [],
        "get_enabled_tool": None,
        "list_skillsets": [],
        "get_skillset": None,
        "create_skillset": None,
        "update_skillset": None,
        "delete_skillset": True,
        "list_skillset_versions": [],
        "get_skillset_version": None,
        "list_skills": [],
        "get_skill": None,
        "create_skill": None,
        "update_skill": None,
        "delete_skill": True,
        "list_skill_versions": [],
        "get_skill_version": None,
        "list_enabled_skills": [],
        "get_enabled_skill": None,
        "save_query_history": None,
        "list_query_history": ([], 0),
        "list_roles": [],
        "get_role": None,
        "get_role_by_name": None,
        "create_role": None,
        "update_role": None,
        "delete_role": True,
        "list_role_versions": [],
        "get_role_version": None,
    }
    for name, return_value in async_methods.items():
        setattr(store, name, AsyncMock(return_value=return_value))
    with patch("reporting.services.report_store.get_store", return_value=store):
        yield store


async def test_initialize_delegates(mock_store):
    await report_store.initialize()
    mock_store.initialize.assert_called_once()


async def test_list_reports_delegates(mock_store):
    mock_store.list_reports.return_value = []
    result = await report_store.list_reports()
    mock_store.list_reports.assert_called_once()
    assert result == []


async def test_get_report_latest_delegates(mock_store):
    mock_store.get_report_latest.return_value = None
    await report_store.get_report_latest("rid1")
    mock_store.get_report_latest.assert_called_once_with("rid1", user_id=None)


async def test_get_report_version_delegates(mock_store):
    mock_store.get_report_version.return_value = None
    await report_store.get_report_version("rid1", 2)
    mock_store.get_report_version.assert_called_once_with("rid1", 2, user_id=None)


async def test_list_report_versions_delegates(mock_store):
    mock_store.list_report_versions.return_value = []
    await report_store.list_report_versions("rid1")
    mock_store.list_report_versions.assert_called_once_with("rid1", user_id=None)


async def test_create_report_delegates(mock_store):
    await report_store.create_report(name="My Report", created_by="u@x.com")
    mock_store.create_report.assert_called_once_with(name="My Report", created_by="u@x.com", access=None)


async def test_save_report_version_delegates(mock_store):
    await report_store.save_report_version(report_id="rid1", config={}, created_by="u@x.com", comment="v2")
    mock_store.save_report_version.assert_called_once_with(
        report_id="rid1", config={}, created_by="u@x.com", comment="v2", user_id=None
    )


async def test_get_dashboard_report_id_delegates(mock_store):
    mock_store.get_dashboard_report_id.return_value = None
    await report_store.get_dashboard_report_id()
    mock_store.get_dashboard_report_id.assert_called_once()


async def test_set_dashboard_report_delegates(mock_store):
    mock_store.set_dashboard_report.return_value = True
    await report_store.set_dashboard_report("rid1")
    mock_store.set_dashboard_report.assert_called_once_with("rid1")


async def test_get_dashboard_report_delegates(mock_store):
    mock_store.get_dashboard_report.return_value = None
    await report_store.get_dashboard_report()
    mock_store.get_dashboard_report.assert_called_once()


async def test_facade_delegates_remaining_methods(mock_store):
    await report_store.get_report_metadata("rid1")
    mock_store.get_report_metadata.assert_awaited_once_with("rid1", user_id=None)

    await report_store.delete_report("rid1")
    mock_store.delete_report.assert_awaited_once_with("rid1", user_id=None)

    await report_store.pin_report("rid1", True, updated_by="u@x.com")
    mock_store.pin_report.assert_awaited_once_with("rid1", True, updated_by="u@x.com", user_id=None)

    await report_store.update_report_visibility("rid1", updated_by="u@x.com")
    mock_store.update_report_visibility.assert_awaited_once_with(
        report_id="rid1",
        updated_by="u@x.com",
        access=None,
    )

    await report_store.get_or_create_user(sub="s", iss="i", email="e@example.com")
    mock_store.get_or_create_user.assert_awaited_once_with(
        sub="s",
        iss="i",
        email="e@example.com",
        display_name=None,
        preferred_username=None,
        role=None,
    )

    await report_store.update_user_profile(user_id="u1", email="e@example.com")
    mock_store.update_user_profile.assert_awaited_once_with(
        user_id="u1",
        email="e@example.com",
        display_name=None,
        preferred_username=None,
        token_iat=None,
    )

    await report_store.get_user("u1")
    mock_store.get_user.assert_awaited_once_with("u1")

    await report_store.archive_user("u1")
    mock_store.archive_user.assert_awaited_once_with("u1")

    await report_store.list_scheduled_queries()
    mock_store.list_scheduled_queries.assert_awaited_once_with()
    await report_store.get_scheduled_query("sq1")
    mock_store.get_scheduled_query.assert_awaited_once_with("sq1")
    await report_store.create_scheduled_query(
        name="n",
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=None,
        watch_scans=[],
        enabled=True,
        actions=[],
        created_by="u1",
    )
    mock_store.create_scheduled_query.assert_awaited_once_with(
        name="n",
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=None,
        watch_scans=[],
        enabled=True,
        actions=[],
        created_by="u1",
    )

    await report_store.acquire_scheduled_query_lock("sq1", None)
    mock_store.acquire_scheduled_query_lock.assert_awaited_once_with(
        sq_id="sq1",
        expected_last_scheduled_at=None,
    )
    await report_store.record_scheduled_query_result("sq1", "ok")
    mock_store.record_scheduled_query_result.assert_awaited_once_with(sq_id="sq1", status="ok", error=None)
    await report_store.delete_scheduled_query("sq1")
    mock_store.delete_scheduled_query.assert_awaited_once_with("sq1")
    await report_store.list_scheduled_query_versions("sq1")
    mock_store.list_scheduled_query_versions.assert_awaited_once_with("sq1")
    await report_store.get_scheduled_query_version("sq1", 2)
    mock_store.get_scheduled_query_version.assert_awaited_once_with("sq1", 2)

    await report_store.list_toolsets()
    mock_store.list_toolsets.assert_awaited_once_with()
    await report_store.get_toolset("ts1")
    mock_store.get_toolset.assert_awaited_once_with("ts1")
    await report_store.create_toolset("ts1", "n", "d", True, "u1")
    mock_store.create_toolset.assert_awaited_once_with(
        toolset_id="ts1",
        name="n",
        description="d",
        enabled=True,
        created_by="u1",
    )
    await report_store.update_toolset("ts1", "n2", "d2", False, "u2")
    mock_store.update_toolset.assert_awaited_once_with(
        toolset_id="ts1",
        name="n2",
        description="d2",
        enabled=False,
        updated_by="u2",
        comment=None,
    )
    await report_store.delete_toolset("ts1")
    mock_store.delete_toolset.assert_awaited_once_with("ts1")
    await report_store.list_toolset_versions("ts1")
    mock_store.list_toolset_versions.assert_awaited_once_with("ts1")
    await report_store.get_toolset_version("ts1", 1)
    mock_store.get_toolset_version.assert_awaited_once_with("ts1", 1)

    await report_store.list_tools("ts1")
    mock_store.list_tools.assert_awaited_once_with("ts1")
    await report_store.get_tool("t1")
    mock_store.get_tool.assert_awaited_once_with("t1")
    await report_store.create_tool("ts1", "t1", "n", "d", "MATCH (n) RETURN n", [], True, "u1")
    mock_store.create_tool.assert_awaited_once_with(
        toolset_id="ts1",
        tool_id="t1",
        name="n",
        description="d",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        created_by="u1",
    )
    await report_store.update_tool("t1", "n2", "d2", "MATCH (n) RETURN n", [], False, "u2")
    mock_store.update_tool.assert_awaited_once_with(
        tool_id="t1",
        name="n2",
        description="d2",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=False,
        updated_by="u2",
        comment=None,
    )
    await report_store.delete_tool("t1")
    mock_store.delete_tool.assert_awaited_once_with("t1")
    await report_store.list_tool_versions("t1")
    mock_store.list_tool_versions.assert_awaited_once_with("t1")
    await report_store.get_tool_version("t1", 1)
    mock_store.get_tool_version.assert_awaited_once_with("t1", 1)
    await report_store.list_enabled_tools()
    mock_store.list_enabled_tools.assert_awaited_once_with()
    await report_store.get_enabled_tool("ts1", "t1")
    mock_store.get_enabled_tool.assert_awaited_once_with("ts1", "t1")

    await report_store.list_skillsets()
    mock_store.list_skillsets.assert_awaited_once_with()
    await report_store.get_skillset("ss1")
    mock_store.get_skillset.assert_awaited_once_with("ss1")
    await report_store.create_skillset("ss1", "n", "d", True, "u1")
    mock_store.create_skillset.assert_awaited_once_with(
        skillset_id="ss1",
        name="n",
        description="d",
        enabled=True,
        created_by="u1",
    )
    await report_store.update_skillset("ss1", "n2", "d2", False, "u2")
    mock_store.update_skillset.assert_awaited_once_with(
        skillset_id="ss1",
        name="n2",
        description="d2",
        enabled=False,
        updated_by="u2",
        comment=None,
    )
    await report_store.delete_skillset("ss1")
    mock_store.delete_skillset.assert_awaited_once_with("ss1")
    await report_store.list_skillset_versions("ss1")
    mock_store.list_skillset_versions.assert_awaited_once_with("ss1")
    await report_store.get_skillset_version("ss1", 1)
    mock_store.get_skillset_version.assert_awaited_once_with("ss1", 1)
    await report_store.list_skills("ss1")
    mock_store.list_skills.assert_awaited_once_with("ss1")
    await report_store.get_skill("sk1")
    mock_store.get_skill.assert_awaited_once_with("sk1")
    await report_store.create_skill("ss1", "sk1", "n", "d", "template", [], [], [], True, "u1")
    mock_store.create_skill.assert_awaited_once_with(
        skillset_id="ss1",
        skill_id="sk1",
        name="n",
        description="d",
        template="template",
        parameters=[],
        triggers=[],
        tools_required=[],
        enabled=True,
        created_by="u1",
    )
    await report_store.update_skill("sk1", "n2", "d2", "template2", [], [], [], False, "u2")
    mock_store.update_skill.assert_awaited_once_with(
        skill_id="sk1",
        name="n2",
        description="d2",
        template="template2",
        parameters=[],
        triggers=[],
        tools_required=[],
        enabled=False,
        updated_by="u2",
        comment=None,
    )
    await report_store.delete_skill("sk1")
    mock_store.delete_skill.assert_awaited_once_with("sk1")
    await report_store.list_skill_versions("sk1")
    mock_store.list_skill_versions.assert_awaited_once_with("sk1")
    await report_store.get_skill_version("sk1", 1)
    mock_store.get_skill_version.assert_awaited_once_with("sk1", 1)
    await report_store.list_enabled_skills()
    mock_store.list_enabled_skills.assert_awaited_once_with()
    await report_store.get_enabled_skill("ss1", "sk1")
    mock_store.get_enabled_skill.assert_awaited_once_with("ss1", "sk1")

    await report_store.save_query_history("u1", "RETURN 1")
    mock_store.save_query_history.assert_awaited_once_with(user_id="u1", query="RETURN 1")
    await report_store.list_query_history("u1", 1, 10)
    mock_store.list_query_history.assert_awaited_once_with(user_id="u1", page=1, per_page=10)

    await report_store.list_roles()
    mock_store.list_roles.assert_awaited_once_with()
    await report_store.get_role("r1")
    mock_store.get_role.assert_awaited_once_with("r1")
    await report_store.get_role_by_name("viewer")
    mock_store.get_role_by_name.assert_awaited_once_with("viewer")
    await report_store.create_role("n", "d", [], "u1")
    mock_store.create_role.assert_awaited_once_with(
        name="n",
        description="d",
        permissions=[],
        created_by="u1",
    )
    await report_store.update_role("r1", "n2", "d2", [], "u2")
    mock_store.update_role.assert_awaited_once_with(
        role_id="r1",
        name="n2",
        description="d2",
        permissions=[],
        updated_by="u2",
        comment=None,
    )
    await report_store.delete_role("r1")
    mock_store.delete_role.assert_awaited_once_with("r1")
    await report_store.list_role_versions("r1")
    mock_store.list_role_versions.assert_awaited_once_with("r1")
    await report_store.get_role_version("r1", 1)
    mock_store.get_role_version.assert_awaited_once_with("r1", 1)


async def test_scheduled_chat_facade_delegates(mock_store):
    await report_store.list_scheduled_chats(user_id="u1")
    mock_store.list_scheduled_chats.assert_awaited_once_with(user_id="u1")

    await report_store.get_scheduled_chat("sc1")
    mock_store.get_scheduled_chat.assert_awaited_once_with("sc1")

    await report_store.create_scheduled_chat(
        name="Digest",
        prompt="Summarize",
        schedule={"type": "hourly", "interval_hours": 4},
        watch_scans=[],
        enabled=True,
        created_by="u1",
    )
    mock_store.create_scheduled_chat.assert_awaited_once_with(
        name="Digest",
        prompt="Summarize",
        schedule={"type": "hourly", "interval_hours": 4},
        watch_scans=[],
        enabled=True,
        created_by="u1",
    )

    await report_store.update_scheduled_chat(
        sc_id="sc1",
        name="Digest",
        prompt="Summarize",
        schedule=None,
        watch_scans=[{"grouptype": "CVEMetadata"}],
        enabled=False,
    )
    mock_store.update_scheduled_chat.assert_awaited_once_with(
        sc_id="sc1",
        name="Digest",
        prompt="Summarize",
        schedule=None,
        watch_scans=[{"grouptype": "CVEMetadata"}],
        enabled=False,
    )

    await report_store.delete_scheduled_chat("sc1")
    mock_store.delete_scheduled_chat.assert_awaited_once_with("sc1")

    await report_store.acquire_scheduled_chat_lock("sc1", None)
    mock_store.acquire_scheduled_chat_lock.assert_awaited_once_with("sc1", None)

    await report_store.record_scheduled_chat_result("sc1", "failure", error="boom")
    mock_store.record_scheduled_chat_result.assert_awaited_once_with("sc1", "failure", error="boom")
