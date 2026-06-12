"""Tests for the SQLModel report store backend.

Uses an in-memory async SQLite database (aiosqlite + StaticPool) so all
sessions within a test share the same underlying connection.
"""

from datetime import UTC
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.mcp_config import SkillItem, SkillsetListItem, SkillsetVersion, SkillVersion
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion, User
from reporting.services.report_store import sql as sql_module
from reporting.services.report_store.sql import SQLModelReportStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_snowflake_gen():
    """Reset the module-level snowflake generator between tests."""
    original = sql_module._snowflake_gen
    sql_module._snowflake_gen = None
    yield
    sql_module._snowflake_gen = original


@pytest.fixture(autouse=True)
def reset_engine():
    original = sql_module._engine
    sql_module._engine = None
    yield
    sql_module._engine = original


@pytest.fixture()
async def test_engine():
    """In-memory async SQLite engine shared across all sessions in a test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
async def store(test_engine):
    with patch("reporting.services.report_store.sql._get_engine", return_value=test_engine):
        yield SQLModelReportStore()


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


async def test_initialize_creates_tables(mocker):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    mocker.patch("reporting.services.report_store.sql._get_engine", return_value=engine)
    s = SQLModelReportStore()
    await s.initialize()
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda c: c.dialect.get_table_names(c))
    assert "report_versions" in table_names
    assert "dashboard_pointer" in table_names
    assert "reports" in table_names
    assert "users" in table_names
    assert "skillsets" in table_names
    assert "skills" in table_names
    await engine.dispose()


def test_get_engine_uses_command_timeout_for_postgres(mocker):
    engine_mock = object()
    create_engine = mocker.patch(
        "reporting.services.report_store.sql.create_async_engine",
        return_value=engine_mock,
    )
    mocker.patch("reporting.settings.SQL_DATABASE_URL", "postgresql://localhost:5432/seizu")
    mocker.patch("reporting.settings.SQL_DATABASE_USER", "user")
    mocker.patch("reporting.settings.SQL_DATABASE_PASSWORD", "p@ssword")
    mocker.patch("reporting.settings.SQL_STATEMENT_TIMEOUT", 31)

    result = sql_module._get_engine()

    assert result is engine_mock
    url = create_engine.call_args.args[0]
    assert url.render_as_string(hide_password=False) == "postgresql+asyncpg://user:p%40ssword@localhost:5432/seizu"
    assert create_engine.call_args.kwargs["connect_args"] == {"command_timeout": 31}


# ---------------------------------------------------------------------------
# list_reports
# ---------------------------------------------------------------------------


def _action_confirmation(
    confirmation_id: str,
    status: str,
    created_at: str,
    *,
    session_key: str = "session-1",
    expires_at: str = "2099-01-01T00:30:00+00:00",
) -> ActionConfirmation:
    return ActionConfirmation.model_validate(
        {
            "confirmation_id": confirmation_id,
            "user_id": "user-1",
            "source": "mcp",
            "session_key": session_key,
            "tool_name": "reports__delete",
            "action": "delete",
            "resource_type": "report",
            "resource_id": "report-1",
            "arguments": {"report_id": "report-1"},
            "arguments_hash": "hash-1",
            "status": status,
            "created_at": created_at,
            "expires_at": expires_at,
        }
    )


async def test_list_reports_empty(store):
    assert await store.list_reports() == []


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------


async def test_chat_session_list_empty(store):
    assert await store.list_chat_sessions("user-1", limit=10) == []


async def test_chat_session_create_and_get(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="thread-abc",
    )
    item = await store.create_chat_session("user-1", title="My session")
    assert item.thread_id == "thread-abc"
    assert item.title == "My session"

    fetched = await store.get_chat_session("user-1", "thread-abc")
    assert fetched is not None
    assert fetched.title == "My session"


async def test_chat_session_get_not_found(store):
    assert await store.get_chat_session("user-1", "no-such-thread") is None


async def test_chat_session_list_returns_sessions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=["t1", "t2"],
    )
    await store.create_chat_session("user-1", title="First")
    await store.create_chat_session("user-1", title="Second")
    sessions = await store.list_chat_sessions("user-1", limit=10)
    assert len(sessions) == 2
    assert {s.title for s in sessions} == {"First", "Second"}


async def test_chat_session_touch_updates_timestamp(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="t1",
    )
    await store.create_chat_session("user-1", title="Test")
    result = await store.touch_chat_session("user-1", "t1")
    assert result is not None
    assert result.thread_id == "t1"


async def test_chat_session_touch_missing_returns_none(store):
    result = await store.touch_chat_session("user-1", "no-such")
    assert result is None


async def test_scheduled_chat_session_records_run_status_and_errors(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="t1",
    )
    created = await store.create_chat_session(
        "user-1",
        title="Scheduled",
        origin="scheduled",
        scheduled_chat_id="sc-1",
    )
    assert created.run_status == "running"

    result = await store.complete_chat_session_run(
        "user-1",
        "t1",
        "partial",
        ["Planner fallback"],
    )

    assert result is not None
    assert result.run_status == "partial"
    assert result.run_errors == ["Planner fallback"]


async def test_chat_session_update_title(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="t1",
    )
    await store.create_chat_session("user-1", title="Old")
    result = await store.update_chat_session_title("user-1", "t1", "New")
    assert result is not None
    assert result.title == "New"


async def test_chat_session_update_title_missing_returns_none(store):
    result = await store.update_chat_session_title("user-1", "no-such", "New")
    assert result is None


async def test_chat_session_delete(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="t1",
    )
    await store.create_chat_session("user-1", title="To delete")
    assert await store.delete_chat_session("user-1", "t1") is True
    assert await store.get_chat_session("user-1", "t1") is None


async def test_chat_session_delete_missing_returns_false(store):
    assert await store.delete_chat_session("user-1", "no-such") is False


# ---------------------------------------------------------------------------
# Action confirmation — additional coverage
# ---------------------------------------------------------------------------


async def test_get_action_confirmation_not_found(store):
    result = await store.get_action_confirmation("no-such", user_id="user-1")
    assert result is None


async def test_list_batch_action_confirmations_empty(store):
    result = await store.list_batch_action_confirmations("user-1", "batch-1")
    assert result == []


async def test_list_batch_action_confirmations_returns_items(store):
    conf = _action_confirmation(
        confirmation_id="c1",
        status="pending",
        created_at="2024-01-01T00:00:00+00:00",
    )
    conf2 = conf.model_copy(update={"confirmation_id": "c2", "batch_id": "batch-1"})
    conf_with_batch = conf.model_copy(update={"batch_id": "batch-1"})
    await store.create_action_confirmation(conf_with_batch)
    await store.create_action_confirmation(conf2)
    result = await store.list_batch_action_confirmations("user-1", "batch-1")
    assert any(r.batch_id == "batch-1" for r in result)


async def test_decide_action_confirmation_approves(store):
    conf = _action_confirmation(
        confirmation_id="c1",
        status="pending",
        created_at="2024-01-01T00:00:00+00:00",
    )
    await store.create_action_confirmation(conf)
    decided = await store.decide_action_confirmation(
        confirmation_id="c1",
        user_id="user-1",
        decision="approved",
    )
    assert decided is not None
    assert decided.status == "approved"


async def test_decide_action_confirmation_not_found(store):
    result = await store.decide_action_confirmation(
        confirmation_id="no-such",
        user_id="user-1",
        decision="approved",
    )
    assert result is None


async def test_claim_action_confirmation_for_execution(store):
    conf = _action_confirmation(
        confirmation_id="c1",
        status="approved",
        created_at="2024-01-01T00:00:00+00:00",
    )
    await store.create_action_confirmation(conf)
    # Force status to approved (bypassing decide) by updating directly
    await store.decide_action_confirmation(
        confirmation_id="c1",
        user_id="user-1",
        decision="approved",
    )
    claimed = await store.claim_action_confirmation_for_execution("c1", "user-1")
    assert claimed is not None
    assert claimed.status == "executed"


async def test_claim_action_confirmation_for_execution_not_found(store):
    result = await store.claim_action_confirmation_for_execution("no-such", "user-1")
    assert result is None


async def test_find_action_confirmation_grant_returns_none_when_missing(store):
    result = await store.find_action_confirmation_grant(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        action="delete",
        resource_type="report",
        resource_id="report-1",
        arguments_hash="hash-1",
    )
    assert result is None


async def test_find_action_confirmation_grant_returns_approved(store):
    conf = _action_confirmation(
        confirmation_id="c1",
        status="pending",
        created_at="2024-01-01T00:00:00+00:00",
    )
    await store.create_action_confirmation(conf)
    await store.decide_action_confirmation(
        confirmation_id="c1",
        user_id="user-1",
        decision="approved",
    )
    result = await store.find_action_confirmation_grant(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        tool_name="reports__delete",
        action="delete",
        resource_type="report",
        resource_id="report-1",
        arguments_hash="hash-1",
    )
    assert result is not None
    assert result.status == "approved"


async def test_action_confirmation_session_status_list_returns_pending(store):
    await store.create_action_confirmation(
        _action_confirmation(
            confirmation_id="pending-session",
            status="pending",
            created_at="2024-01-01T00:00:00+00:00",
        )
    )

    result = await store.list_action_confirmations(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        status="pending",
    )

    assert [item.confirmation_id for item in result] == ["pending-session"]


async def test_create_action_confirmation_replaces_expired_pending_dedup(store):
    await store.create_action_confirmation(
        _action_confirmation(
            confirmation_id="expired-pending",
            status="pending",
            created_at="2020-01-01T00:00:00+00:00",
            expires_at="2020-01-01T00:30:00+00:00",
        )
    )
    replacement = _action_confirmation(
        confirmation_id="replacement-pending",
        status="pending",
        created_at="2024-01-01T00:00:00+00:00",
    )

    result = await store.create_action_confirmation(replacement)

    assert result.confirmation_id == "replacement-pending"
    pending = await store.list_action_confirmations(
        user_id="user-1",
        source="mcp",
        session_key="session-1",
        status="pending",
    )
    assert [item.confirmation_id for item in pending] == ["replacement-pending"]
    expired = await store.get_action_confirmation("expired-pending", user_id="user-1")
    assert expired is not None
    assert expired.status == "expired"


async def test_list_reports_returns_created_reports(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="My Report", created_by="user@example.com")
    result = await store.list_reports()
    assert len(result) == 1
    assert isinstance(result[0], ReportListItem)
    assert result[0].report_id == "rid1"
    assert result[0].name == "My Report"
    assert result[0].current_version == 0


# ---------------------------------------------------------------------------
# get_report_latest
# ---------------------------------------------------------------------------


async def test_get_report_latest_not_found(store):
    assert await store.get_report_latest("missing") is None


async def test_get_report_latest_not_found_for_empty_report(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r1", created_by="user@example.com")
    assert await store.get_report_latest("rid1") is None


async def test_get_report_latest_returns_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r1", created_by="user@example.com")
    await store.save_report_version(
        report_id="rid1",
        config={"rows": [{"name": "r1"}]},
        created_by="user@example.com",
        comment="v1",
    )
    result = await store.get_report_latest("rid1")
    assert isinstance(result, ReportVersion)
    assert result.report_id == "rid1"
    assert result.name == "r1"
    assert result.version == 1
    assert result.config == {"name": "r1", "rows": [{"name": "r1"}]}
    assert result.created_by == "user@example.com"
    assert result.comment == "v1"


async def test_get_report_latest_returns_newest_after_update(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.save_report_version(report_id="rid1", config={"v": 1}, created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 2}, created_by="u@x.com")
    result = await store.get_report_latest("rid1")
    assert result.version == 2
    assert result.config == {"name": "r", "v": 2}


# ---------------------------------------------------------------------------
# get_report_version
# ---------------------------------------------------------------------------


async def test_get_report_version_not_found(store):
    assert await store.get_report_version("missing", 1) is None


async def test_get_report_version_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 1}, created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 2}, created_by="u@x.com")

    v1 = await store.get_report_version("rid1", 1)
    v2 = await store.get_report_version("rid1", 2)
    assert v1.version == 1
    assert v1.name == "r"
    assert v1.config == {"name": "r", "v": 1}
    assert v2.version == 2
    assert v2.config == {"name": "r", "v": 2}


# ---------------------------------------------------------------------------
# list_report_versions
# ---------------------------------------------------------------------------


async def test_list_report_versions_empty(store):
    assert await store.list_report_versions("missing") == []


async def test_list_report_versions_empty_for_report_with_no_versions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    assert await store.list_report_versions("rid1") == []


async def test_list_report_versions_newest_first(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 1}, created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 2}, created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 3}, created_by="u@x.com")

    versions = await store.list_report_versions("rid1")
    assert len(versions) == 3
    assert versions[0].version == 3
    assert versions[1].version == 2
    assert versions[2].version == 1


# ---------------------------------------------------------------------------
# create_report
# ---------------------------------------------------------------------------


async def test_create_report_returns_list_item(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="snowflake42",
    )
    result = await store.create_report(
        name="My Report",
        created_by="creator@example.com",
    )
    assert isinstance(result, ReportListItem)
    assert result.report_id == "snowflake42"
    assert result.name == "My Report"
    assert result.current_version == 0


# ---------------------------------------------------------------------------
# save_report_version
# ---------------------------------------------------------------------------


async def test_save_report_version_returns_none_for_missing_report(store):
    result = await store.save_report_version(report_id="nonexistent", config={}, created_by="u@x.com")
    assert result is None


async def test_save_report_version_increments_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    result = await store.save_report_version(
        report_id="rid1",
        config={"v": 2},
        created_by="editor@example.com",
        comment="update",
    )
    assert result.version == 1
    assert result.name == "r"
    assert result.config == {"name": "r", "v": 2}
    assert result.comment == "update"


async def test_save_report_version_does_not_change_name_without_config_name(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="Original Name", created_by="u@x.com")
    await store.save_report_version(
        report_id="rid1",
        config={"rows": []},
        created_by="u@x.com",
    )
    result = await store.list_reports()
    assert result[0].name == "Original Name"
    assert result[0].current_version == 1


async def test_save_report_version_updates_name_from_config(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="Original Name", created_by="u@x.com")
    version = await store.save_report_version(
        report_id="rid1",
        config={"name": "Renamed Report", "rows": []},
        created_by="u@x.com",
    )

    reports = await store.list_reports()
    latest = await store.get_report_latest("rid1")

    assert version.name == "Renamed Report"
    assert reports[0].name == "Renamed Report"
    assert latest.name == "Renamed Report"
    assert latest.config["name"] == "Renamed Report"


async def test_save_report_version_ignores_blank_config_name(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="Original Name", created_by="u@x.com")
    version = await store.save_report_version(
        report_id="rid1",
        config={"name": "   ", "rows": []},
        created_by="u@x.com",
    )

    reports = await store.list_reports()

    assert version.name == "Original Name"
    assert reports[0].name == "Original Name"


async def test_save_report_version_latest_reflects_new_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 1}, created_by="u@x.com")
    await store.save_report_version(report_id="rid1", config={"v": 2}, created_by="u@x.com")

    latest = await store.get_report_latest("rid1")
    assert latest.version == 2


# ---------------------------------------------------------------------------
# get/set dashboard
# ---------------------------------------------------------------------------


async def test_get_dashboard_report_id_none_when_not_set(store):
    assert await store.get_dashboard_report_id() is None


async def test_get_dashboard_report_none_when_not_set(store):
    assert await store.get_dashboard_report() is None


async def test_set_dashboard_report_false_for_missing_report(store):
    assert await store.set_dashboard_report("nonexistent") is False


async def test_set_dashboard_report_succeeds_for_empty_report(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="My Report", created_by="u@x.com", access=ReportAccess(scope="public"))
    ok = await store.set_dashboard_report("rid1")
    assert ok is True
    assert await store.get_dashboard_report_id() == "rid1"


async def test_set_and_get_dashboard_report(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="My Report", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.save_report_version(report_id="rid1", config={"rows": []}, created_by="u@x.com")
    ok = await store.set_dashboard_report("rid1")
    assert ok is True
    assert await store.get_dashboard_report_id() == "rid1"

    report = await store.get_dashboard_report()
    assert isinstance(report, ReportVersion)
    assert report.report_id == "rid1"
    assert report.version == 1


async def test_set_dashboard_report_can_be_changed(store, mocker):
    ids = iter(["rid1", "rid2"])
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=lambda: next(ids),
    )
    await store.create_report(name="r1", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.create_report(name="r2", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.set_dashboard_report("rid1")
    await store.set_dashboard_report("rid2")
    assert await store.get_dashboard_report_id() == "rid2"


# ---------------------------------------------------------------------------
# delete_report
# ---------------------------------------------------------------------------


async def test_delete_report_returns_false_for_missing_report(store):
    assert await store.delete_report("nonexistent") is False


async def test_delete_report_removes_report(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.save_report_version(report_id="rid1", config={"v": 1}, created_by="u@x.com")
    assert await store.delete_report("rid1") is True
    assert await store.list_reports() == []
    assert await store.list_report_versions("rid1") == []


async def test_delete_report_clears_dashboard_pointer(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.set_dashboard_report("rid1")
    assert await store.get_dashboard_report_id() == "rid1"
    await store.delete_report("rid1")
    assert await store.get_dashboard_report_id() is None


async def test_delete_report_does_not_clear_other_dashboard_pointer(store, mocker):
    ids = iter(["rid1", "rid2"])
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=lambda: next(ids),
    )
    await store.create_report(name="r1", created_by="u@x.com")
    await store.create_report(name="r2", created_by="u@x.com", access=ReportAccess(scope="public"))
    await store.set_dashboard_report("rid2")
    await store.delete_report("rid1")
    assert await store.get_dashboard_report_id() == "rid2"


# ---------------------------------------------------------------------------
# get_or_create_user
# ---------------------------------------------------------------------------


async def test_get_or_create_user_creates_new_user(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    user = await store.get_or_create_user(
        sub="sub123",
        iss="https://idp.example.com",
        email="alice@example.com",
        display_name="Alice",
    )
    assert isinstance(user, User)
    assert user.user_id == "uid1"
    assert user.sub == "sub123"
    assert user.iss == "https://idp.example.com"
    assert user.email == "alice@example.com"
    assert user.display_name == "Alice"
    assert user.archived_at is None


async def test_get_or_create_user_returns_existing_user(store, mocker):
    ids = iter(["uid1", "uid2"])
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=lambda: next(ids),
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    # Second call with same (iss, sub) must not create a new user
    user = await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    assert user.user_id == "uid1"


async def test_get_or_create_user_returns_existing_without_update(store, mocker):
    """Subsequent calls with a changed email must not update the stored record."""
    ids = iter(["uid1", "uid2"])
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=lambda: next(ids),
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="old@example.com")
    user = await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="new@example.com")
    assert user.email == "old@example.com"


async def test_get_or_create_user_returns_existing_after_unique_race(store, mocker):
    """A concurrent first login can win the insert race after the initial lookup."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid2",
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")

    original_execute = sql_module.AsyncSession.execute
    first_lookup_done = False

    async def execute_with_stale_first_lookup(self, statement, *args, **kwargs):
        nonlocal first_lookup_done
        result = await original_execute(self, statement, *args, **kwargs)
        if not first_lookup_done:
            first_lookup_done = True
            return mocker.Mock(scalars=lambda: mocker.Mock(first=lambda: None))
        return result

    mocker.patch.object(sql_module.AsyncSession, "execute", execute_with_stale_first_lookup)
    mocker.patch.object(
        sql_module.AsyncSession,
        "commit",
        side_effect=IntegrityError("duplicate key", {}, Exception("duplicate key")),
    )

    user = await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")

    assert user.user_id == "uid2"


# ---------------------------------------------------------------------------
# update_user_profile
# ---------------------------------------------------------------------------


async def test_update_user_profile_updates_email_when_changed(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="old@example.com")
    user = await store.update_user_profile(user_id="uid1", email="new@example.com")
    assert user.email == "new@example.com"


async def test_update_user_profile_no_write_when_nothing_changed(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    user = await store.update_user_profile(user_id="uid1", email="alice@example.com")
    assert user.email == "alice@example.com"


async def test_update_user_profile_updates_last_login_when_iat_is_newer(store, mocker):
    from datetime import datetime

    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    # Use future dates so both are guaranteed newer than the creation-time `now`
    first_iat = datetime(2030, 1, 1, tzinfo=UTC)
    second_iat = datetime(2030, 6, 1, tzinfo=UTC)
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    await store.update_user_profile(user_id="uid1", email="alice@example.com", token_iat=first_iat)
    user = await store.update_user_profile(user_id="uid1", email="alice@example.com", token_iat=second_iat)
    assert user.last_login == second_iat.isoformat()


async def test_update_user_profile_does_not_update_last_login_when_iat_is_older(store, mocker):
    from datetime import datetime

    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    # Use future dates so both are newer than creation-time `now`
    newer_iat = datetime(2030, 6, 1, tzinfo=UTC)
    older_iat = datetime(2030, 1, 1, tzinfo=UTC)
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    await store.update_user_profile(user_id="uid1", email="alice@example.com", token_iat=newer_iat)
    user = await store.update_user_profile(user_id="uid1", email="alice@example.com", token_iat=older_iat)
    assert user.last_login == newer_iat.isoformat()


async def test_get_or_create_user_different_sub_creates_separate_users(store, mocker):
    ids = iter(["uid1", "uid2"])
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=lambda: next(ids),
    )
    u1 = await store.get_or_create_user(sub="sub-alice", iss="https://idp.example.com", email="shared@example.com")
    u2 = await store.get_or_create_user(sub="sub-bob", iss="https://idp.example.com", email="shared@example.com")
    assert u1.user_id != u2.user_id


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------


async def test_get_user_not_found(store):
    assert await store.get_user("nonexistent") is None


async def test_get_user_returns_created_user(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    user = await store.get_user("uid1")
    assert isinstance(user, User)
    assert user.user_id == "uid1"
    assert user.email == "alice@example.com"


# ---------------------------------------------------------------------------
# archive_user
# ---------------------------------------------------------------------------


async def test_archive_user_returns_false_for_missing(store):
    assert await store.archive_user("nonexistent") is False


async def test_archive_user_sets_archived_at(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="uid1",
    )
    await store.get_or_create_user(sub="sub123", iss="https://idp.example.com", email="alice@example.com")
    assert await store.archive_user("uid1") is True
    user = await store.get_user("uid1")
    assert user.archived_at is not None


# ---------------------------------------------------------------------------
# Scheduled queries
# ---------------------------------------------------------------------------

_SQ_KWARGS = dict(
    name="Test Query",
    cypher="MATCH (n) RETURN n",
    params=[],
    frequency=60,
    watch_scans=[],
    enabled=True,
    actions=[{"action_type": "log", "action_config": {}}],
    created_by="user@example.com",
)


async def test_list_scheduled_queries_empty(store):
    assert await store.list_scheduled_queries() == []


async def test_create_scheduled_query(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    result = await store.create_scheduled_query(**_SQ_KWARGS)
    assert result.scheduled_query_id == "sq1"
    assert result.name == "Test Query"
    assert result.current_version == 1
    assert result.created_by == "user@example.com"
    assert result.updated_by == "user@example.com"


async def test_list_scheduled_queries_returns_created(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    items = await store.list_scheduled_queries()
    assert len(items) == 1
    assert items[0].scheduled_query_id == "sq1"


async def test_get_scheduled_query_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    item = await store.get_scheduled_query("sq1")
    assert item is not None
    assert item.name == "Test Query"
    assert item.current_version == 1


async def test_get_scheduled_query_not_found(store):
    assert await store.get_scheduled_query("nonexistent") is None


async def test_update_scheduled_query_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    result = await store.update_scheduled_query(
        sq_id="sq1",
        name="Updated Query",
        cypher="MATCH (n) RETURN n LIMIT 1",
        params=[],
        frequency=120,
        watch_scans=[],
        enabled=False,
        actions=[],
        updated_by="editor@example.com",
        comment="Updated for testing",
    )
    assert result is not None
    assert result.name == "Updated Query"
    assert result.current_version == 2
    assert result.updated_by == "editor@example.com"
    assert result.created_by == "user@example.com"


async def test_update_scheduled_query_not_found(store):
    result = await store.update_scheduled_query(
        sq_id="nonexistent",
        name="X",
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=60,
        watch_scans=[],
        enabled=True,
        actions=[],
        updated_by="u@x.com",
    )
    assert result is None


async def test_list_scheduled_query_versions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    await store.update_scheduled_query(
        sq_id="sq1",
        name="Updated",
        cypher="MATCH (n) RETURN n LIMIT 1",
        params=[],
        frequency=60,
        watch_scans=[],
        enabled=True,
        actions=[],
        updated_by="u@x.com",
        comment="v2",
    )
    versions = await store.list_scheduled_query_versions("sq1")
    assert len(versions) == 2
    assert versions[0].version == 2  # descending order
    assert versions[1].version == 1


async def test_list_scheduled_query_versions_not_found(store):
    assert await store.list_scheduled_query_versions("nonexistent") == []


async def test_get_scheduled_query_version_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    v = await store.get_scheduled_query_version("sq1", 1)
    assert v is not None
    assert v.version == 1
    assert v.name == "Test Query"


async def test_get_scheduled_query_version_not_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    assert await store.get_scheduled_query_version("sq1", 99) is None


async def test_get_scheduled_query_version_sq_not_found(store):
    assert await store.get_scheduled_query_version("nonexistent", 1) is None


async def test_delete_scheduled_query_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    assert await store.delete_scheduled_query("sq1") is True
    assert await store.get_scheduled_query("sq1") is None
    assert await store.list_scheduled_query_versions("sq1") == []


async def test_delete_scheduled_query_not_found(store):
    assert await store.delete_scheduled_query("nonexistent") is False


async def test_acquire_scheduled_query_lock_no_previous(store, mocker):
    """First-ever lock acquisition (last_scheduled_at is None) succeeds."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    acquired = await store.acquire_scheduled_query_lock("sq1", None)
    assert acquired is True
    item = await store.get_scheduled_query("sq1")
    assert item is not None
    assert item.last_scheduled_at is not None


async def test_acquire_scheduled_query_lock_with_expected(store, mocker):
    """CAS succeeds when expected value matches stored value."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    # Acquire once to set a known value
    await store.acquire_scheduled_query_lock("sq1", None)
    item = await store.get_scheduled_query("sq1")
    assert item is not None
    prev = item.last_scheduled_at
    # CAS with correct expected value
    acquired = await store.acquire_scheduled_query_lock("sq1", prev)
    assert acquired is True


async def test_acquire_scheduled_query_lock_race(store, mocker):
    """CAS fails when expected value no longer matches (another worker won)."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)
    # Acquire once to set a value
    await store.acquire_scheduled_query_lock("sq1", None)
    # A second worker uses the old (None) expected value — should fail
    acquired = await store.acquire_scheduled_query_lock("sq1", None)
    assert acquired is False


# ===========================================================================
# Toolsets
# ===========================================================================

_TS_KWARGS = {
    "toolset_id": "ts1",
    "name": "My Toolset",
    "description": "A test toolset",
    "enabled": True,
    "created_by": "user@example.com",
}


# ---------------------------------------------------------------------------
# create_toolset / list_toolsets
# ---------------------------------------------------------------------------


async def test_create_toolset_and_list(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    ts = await store.create_toolset(**_TS_KWARGS)
    assert ts.toolset_id == "ts1"
    assert ts.name == "My Toolset"
    assert ts.enabled is True
    assert ts.current_version == 1
    assert ts.created_by == "user@example.com"

    items = await store.list_toolsets()
    assert len(items) == 1
    assert items[0].toolset_id == "ts1"


async def test_list_toolsets_empty(store):
    assert await store.list_toolsets() == []


# ---------------------------------------------------------------------------
# get_toolset
# ---------------------------------------------------------------------------


async def test_get_toolset_not_found(store):
    assert await store.get_toolset("missing") is None


async def test_get_toolset_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    await store.create_toolset(**_TS_KWARGS)
    ts = await store.get_toolset("ts1")
    assert ts is not None
    assert ts.toolset_id == "ts1"
    assert ts.name == "My Toolset"


# ---------------------------------------------------------------------------
# update_toolset
# ---------------------------------------------------------------------------


async def test_update_toolset_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    await store.create_toolset(**_TS_KWARGS)
    updated = await store.update_toolset(
        toolset_id="ts1",
        name="Updated Toolset",
        description="New description",
        enabled=False,
        updated_by="user@example.com",
        comment="Updated",
    )
    assert updated is not None
    assert updated.name == "Updated Toolset"
    assert updated.enabled is False
    assert updated.current_version == 2
    assert updated.updated_by == "user@example.com"


async def test_update_toolset_not_found(store):
    result = await store.update_toolset(
        toolset_id="missing",
        name="X",
        description="",
        enabled=True,
        updated_by="u",
        comment=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# list_toolset_versions / get_toolset_version
# ---------------------------------------------------------------------------


async def test_list_toolset_versions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    await store.create_toolset(**_TS_KWARGS)
    await store.update_toolset(
        toolset_id="ts1",
        name="v2 Name",
        description="",
        enabled=True,
        updated_by="u",
        comment="second",
    )
    versions = await store.list_toolset_versions("ts1")
    assert len(versions) == 2
    nums = {v.version for v in versions}
    assert nums == {1, 2}


async def test_get_toolset_version_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    await store.create_toolset(**_TS_KWARGS)
    v = await store.get_toolset_version("ts1", 1)
    assert v is not None
    assert v.version == 1
    assert v.name == "My Toolset"


async def test_get_toolset_version_not_found(store):
    assert await store.get_toolset_version("missing", 1) is None


# ---------------------------------------------------------------------------
# delete_toolset
# ---------------------------------------------------------------------------


async def test_delete_toolset_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="ts1",
    )
    await store.create_toolset(**_TS_KWARGS)
    assert await store.delete_toolset("ts1") is True
    assert await store.get_toolset("ts1") is None
    assert await store.list_toolset_versions("ts1") == []


async def test_delete_toolset_not_found(store):
    assert await store.delete_toolset("nonexistent") is False


# ===========================================================================
# Tools
# ===========================================================================

_TOOL_KWARGS = {
    "tool_id": "tool1",
    "name": "My Tool",
    "description": "A test tool",
    "cypher": "MATCH (n) RETURN n",
    "parameters": [],
    "enabled": True,
    "created_by": "user@example.com",
}


async def _make_toolset(store, mocker, ts_id: str = "ts1") -> None:
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value=ts_id,
    )
    await store.create_toolset(**{**_TS_KWARGS, "toolset_id": ts_id})


# ---------------------------------------------------------------------------
# create_tool / list_tools
# ---------------------------------------------------------------------------


async def test_create_tool_and_list(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    tool = await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    assert tool is not None
    assert tool.tool_id == "tool1"
    assert tool.toolset_id == "ts1"
    assert tool.name == "My Tool"
    assert tool.current_version == 1
    assert tool.created_by == "user@example.com"

    tools = await store.list_tools("ts1")
    assert len(tools) == 1
    assert tools[0].tool_id == "tool1"


async def test_create_tool_toolset_not_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    result = await store.create_tool(toolset_id="missing", **_TOOL_KWARGS)
    assert result is None


async def test_list_tools_empty(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    assert await store.list_tools("ts1") == []


# ---------------------------------------------------------------------------
# get_tool
# ---------------------------------------------------------------------------


async def test_get_tool_not_found(store):
    assert await store.get_tool("missing") is None


async def test_get_tool_found(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    tool = await store.get_tool("tool1")
    assert tool is not None
    assert tool.tool_id == "tool1"
    assert tool.toolset_id == "ts1"


# ---------------------------------------------------------------------------
# update_tool
# ---------------------------------------------------------------------------


async def test_update_tool_success(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    updated = await store.update_tool(
        tool_id="tool1",
        name="Updated Tool",
        description="New desc",
        cypher="MATCH (n) RETURN n LIMIT 10",
        parameters=[
            {
                "name": "limit",
                "type": "integer",
                "description": "",
                "required": False,
                "default": 10,
            }
        ],
        enabled=False,
        updated_by="user@example.com",
        comment="Updated",
    )
    assert updated is not None
    assert updated.name == "Updated Tool"
    assert updated.enabled is False
    assert updated.current_version == 2
    assert len(updated.parameters) == 1
    assert updated.parameters[0].name == "limit"


async def test_update_tool_not_found(store):
    result = await store.update_tool(
        tool_id="missing",
        name="X",
        description="",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        updated_by="u",
        comment=None,
    )
    assert result is None


# ---------------------------------------------------------------------------
# list_tool_versions / get_tool_version
# ---------------------------------------------------------------------------


async def test_list_tool_versions(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    await store.update_tool(
        tool_id="tool1",
        name="v2",
        description="",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=True,
        updated_by="u",
        comment="second",
    )
    versions = await store.list_tool_versions("tool1")
    assert len(versions) == 2
    nums = {v.version for v in versions}
    assert nums == {1, 2}


async def test_get_tool_version_found(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    v = await store.get_tool_version("tool1", 1)
    assert v is not None
    assert v.version == 1
    assert v.name == "My Tool"
    assert v.toolset_id == "ts1"


async def test_get_tool_version_not_found(store):
    assert await store.get_tool_version("missing", 1) is None


# ---------------------------------------------------------------------------
# delete_tool
# ---------------------------------------------------------------------------


async def test_delete_tool_success(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    assert await store.delete_tool("tool1") is True
    assert await store.get_tool("tool1") is None
    assert await store.list_tool_versions("tool1") == []


async def test_delete_tool_not_found(store):
    assert await store.delete_tool("nonexistent") is False


# ---------------------------------------------------------------------------
# delete_toolset cascades to tools
# ---------------------------------------------------------------------------


async def test_delete_toolset_cascades_to_tools(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    assert await store.delete_toolset("ts1") is True
    assert await store.get_tool("tool1") is None
    assert await store.list_tools("ts1") == []


# ---------------------------------------------------------------------------
# list_enabled_tools
# ---------------------------------------------------------------------------


async def test_list_enabled_tools_empty(store):
    assert await store.list_enabled_tools() == []


async def test_list_enabled_tools_returns_tools_in_enabled_toolsets(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    tools = await store.list_enabled_tools()
    assert len(tools) == 1
    assert tools[0].tool_id == "tool1"


async def test_list_enabled_tools_excludes_disabled_toolset(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(toolset_id="ts1", **_TOOL_KWARGS)
    # Disable the toolset
    await store.update_toolset(
        toolset_id="ts1",
        name="My Toolset",
        description="",
        enabled=False,
        updated_by="u",
        comment=None,
    )
    assert await store.list_enabled_tools() == []


async def test_list_enabled_tools_excludes_disabled_tool(store, mocker):
    await _make_toolset(store, mocker, "ts1")
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="tool1",
    )
    await store.create_tool(
        toolset_id="ts1",
        tool_id="tool1",
        name="Disabled Tool",
        description="",
        cypher="MATCH (n) RETURN n",
        parameters=[],
        enabled=False,
        created_by="u",
    )
    assert await store.list_enabled_tools() == []


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

_ROLE_KWARGS = dict(
    name="Custom Role",
    description="A test role",
    permissions=["reports:read", "query:execute"],
    created_by="uid1",
)


async def test_create_role_and_list(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    role = await store.create_role(**_ROLE_KWARGS)
    assert role.role_id == "r1"
    assert role.name == "Custom Role"
    assert role.current_version == 1
    assert role.created_by == "uid1"
    assert "reports:read" in role.permissions

    items = await store.list_roles()
    assert len(items) == 1
    assert items[0].role_id == "r1"


async def test_list_roles_empty(store):
    assert await store.list_roles() == []


async def test_get_role_not_found(store):
    assert await store.get_role("missing") is None


async def test_get_role_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    role = await store.get_role("r1")
    assert role is not None
    assert role.role_id == "r1"


async def test_get_role_by_name_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    role = await store.get_role_by_name("Custom Role")
    assert role is not None
    assert role.name == "Custom Role"


async def test_get_role_by_name_not_found(store):
    assert await store.get_role_by_name("nonexistent") is None


async def test_update_role_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    updated = await store.update_role(
        role_id="r1",
        name="Updated Role",
        description="new desc",
        permissions=["reports:read", "reports:write"],
        updated_by="uid2",
        comment="second version",
    )
    assert updated is not None
    assert updated.name == "Updated Role"
    assert updated.current_version == 2
    assert updated.updated_by == "uid2"
    assert "reports:write" in updated.permissions


async def test_update_role_not_found(store):
    result = await store.update_role(
        role_id="missing",
        name="X",
        description="",
        permissions=[],
        updated_by="u",
    )
    assert result is None


async def test_delete_role_success(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    assert await store.delete_role("r1") is True
    assert await store.get_role("r1") is None


async def test_delete_role_not_found(store):
    assert await store.delete_role("missing") is False


async def test_list_role_versions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    await store.update_role(
        role_id="r1",
        name="Updated Role",
        description="",
        permissions=[],
        updated_by="uid1",
        comment="v2",
    )
    versions = await store.list_role_versions("r1")
    assert len(versions) == 2
    assert versions[0].version == 2
    assert versions[1].version == 1


async def test_get_role_version_found(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="r1",
    )
    await store.create_role(**_ROLE_KWARGS)
    v = await store.get_role_version("r1", 1)
    assert v is not None
    assert v.version == 1
    assert v.role_id == "r1"


async def test_get_role_version_not_found(store):
    assert await store.get_role_version("missing", 1) is None


# ---------------------------------------------------------------------------
# Skillsets and skills
# ---------------------------------------------------------------------------


async def test_skillset_and_skill_crud(store):
    assert await store.list_skillsets() == []
    assert await store.get_skillset("ss1") is None
    assert (
        await store.update_skillset(
            skillset_id="missing",
            name="Missing",
            description="",
            enabled=True,
            updated_by="u2",
        )
        is None
    )
    assert await store.delete_skillset("missing") is False
    assert await store.list_skillset_versions("missing") == []
    assert await store.get_skillset_version("missing", 1) is None

    skillset = await store.create_skillset(
        skillset_id="ss1",
        name="Skillset",
        description="desc",
        enabled=True,
        created_by="u1",
    )
    assert isinstance(skillset, SkillsetListItem)
    assert skillset.current_version == 1
    assert (await store.list_skillsets())[0].skillset_id == "ss1"
    assert (await store.get_skillset("ss1")).name == "Skillset"

    updated_skillset = await store.update_skillset(
        skillset_id="ss1",
        name="Updated",
        description="new",
        enabled=False,
        updated_by="u2",
        comment="v2",
    )
    assert updated_skillset is not None
    assert updated_skillset.current_version == 2
    versions = await store.list_skillset_versions("ss1")
    assert len(versions) == 2
    assert isinstance(versions[0], SkillsetVersion)
    assert versions[0].version == 2
    assert (await store.get_skillset_version("ss1", 1)).version == 1

    assert (
        await store.create_skill(
            skillset_id="missing",
            skill_id="sk_missing",
            name="Missing",
            description="",
            template="x",
            parameters=[],
            triggers=[],
            tools_required=[],
            enabled=True,
            created_by="u1",
        )
        is None
    )
    assert await store.list_skills("ss1") == []
    assert await store.get_skill("sk1") is None
    assert (
        await store.update_skill(
            skill_id="missing",
            name="Missing",
            description="",
            template="x",
            parameters=[],
            triggers=[],
            tools_required=[],
            enabled=True,
            updated_by="u2",
        )
        is None
    )
    assert await store.delete_skill("missing") is False
    assert await store.list_skill_versions("missing") == []
    assert await store.get_skill_version("missing", 1) is None
    assert await store.list_enabled_skills() == []
    assert await store.get_enabled_skill("ss1", "sk1") is None

    await store.update_skillset(
        skillset_id="ss1",
        name="Updated",
        description="new",
        enabled=True,
        updated_by="u2",
    )
    skill = await store.create_skill(
        skillset_id="ss1",
        skill_id="sk1",
        name="Skill",
        description="desc",
        template="Hello {{topic}}",
        parameters=[{"name": "topic", "type": "string", "required": True}],
        triggers=["say hello"],
        tools_required=["toolset__tool"],
        enabled=True,
        created_by="u1",
    )
    assert isinstance(skill, SkillItem)
    assert skill.parameters[0].name == "topic"
    assert skill.triggers == ["say hello"]
    assert (await store.list_skills("ss1"))[0].skill_id == "sk1"
    assert (await store.get_skill("sk1")).tools_required == ["toolset__tool"]
    assert (await store.list_enabled_skills())[0].skill_id == "sk1"
    assert (await store.get_enabled_skill("ss1", "sk1")).skill_id == "sk1"

    updated_skill = await store.update_skill(
        skill_id="sk1",
        name="Skill v2",
        description="new",
        template="Hello {{topic}} again",
        parameters=[{"name": "topic", "type": "string", "required": True}],
        triggers=[],
        tools_required=[],
        enabled=False,
        updated_by="u2",
        comment="v2",
    )
    assert updated_skill is not None
    assert updated_skill.current_version == 2
    assert await store.list_enabled_skills() == []
    assert await store.get_enabled_skill("ss1", "sk1") is None
    skill_versions = await store.list_skill_versions("sk1")
    assert len(skill_versions) == 2
    assert isinstance(skill_versions[0], SkillVersion)
    assert skill_versions[0].version == 2
    assert (await store.get_skill_version("sk1", 1)).version == 1
    assert await store.delete_skill("sk1") is True
    assert await store.get_skill("sk1") is None
    assert await store.delete_skillset("ss1") is True
    assert await store.get_skillset("ss1") is None
