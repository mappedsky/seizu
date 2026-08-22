"""Tests for the SQLModel report store backend.

Uses an in-memory async SQLite database (aiosqlite + StaticPool) so all
sessions within a test share the same underlying connection.
"""

import asyncio
import hashlib
import itertools
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from reporting import settings
from reporting.schema.chat import CHAT_TURN_MAX_BATCH_BYTES, ChatTurnCommand
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.mcp_config import SkillItem, SkillsetListItem, SkillsetVersion, SkillVersion
from reporting.schema.plugins import PluginFile
from reporting.schema.report_config import ReportAccess, ReportListItem, ReportVersion, User
from reporting.schema.space_config import SpaceConflictError, SpaceDeleteResult, SubspaceItem
from reporting.services.plugin_packages import legacy_skillset_package
from reporting.services.report_store import sql as sql_module
from reporting.services.report_store.base import PluginRevisionConflict
from reporting.services.report_store.sql import SQLModelReportStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


#: Space members must be public, so fixtures that file a report publish it.
_PUBLIC = ReportAccess(scope="public")


async def _plant_private_member(store, space_id: str, created_by: str) -> str:
    """Put a private report inside a space, behind the store's back.

    Every store method now refuses this combination, so the only way to produce
    one is to write the row directly -- which is exactly the state a database
    predating the rule can be in. The checks that must not depend on the
    invariant holding (emptiness, list filtering) are tested against it.
    """
    report = await store.create_report(name="Private", created_by=created_by, access=_PUBLIC, space_id=space_id)
    async with sql_module.AsyncSession(sql_module._get_engine()) as session:
        record = await session.get(sql_module.ReportRecord, report.report_id)
        record.access = {"scope": "private"}
        session.add(record)
        await session.commit()
    return report.report_id


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


async def test_initialize_migrates_legacy_scheduled_query_tables(mocker):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE scheduled_queries ("
                "scheduled_query_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, "
                "cypher VARCHAR NOT NULL, params JSON NOT NULL, frequency INTEGER, "
                "watch_scans JSON NOT NULL, enabled BOOLEAN NOT NULL, actions JSON NOT NULL, "
                "current_version INTEGER NOT NULL, created_at VARCHAR NOT NULL, "
                "updated_at VARCHAR NOT NULL, created_by VARCHAR NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "CREATE TABLE scheduled_query_versions ("
                "scheduled_query_id VARCHAR NOT NULL, version INTEGER NOT NULL, "
                "cypher VARCHAR NOT NULL, params JSON NOT NULL, "
                "frequency INTEGER, watch_scans JSON NOT NULL, enabled BOOLEAN NOT NULL, "
                "actions JSON NOT NULL, created_at VARCHAR NOT NULL, created_by VARCHAR NOT NULL, "
                "PRIMARY KEY (scheduled_query_id, version))"
            )
        )
    mocker.patch("reporting.services.report_store.sql._get_engine", return_value=engine)

    await SQLModelReportStore().initialize()

    async with engine.connect() as conn:
        query_columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(scheduled_queries)"))}
        version_columns = {row[1] for row in await conn.execute(text("PRAGMA table_info(scheduled_query_versions)"))}
        table_names = await conn.run_sync(lambda c: set(c.dialect.get_table_names(c)))
    assert {"inputs", "activities", "stages", "schedule_sync_status"} <= query_columns
    assert {"name", "inputs", "activities", "stages"} <= version_columns
    assert {"reports", "users", "scheduled_chats", "action_confirmations"} <= table_names
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


def test_get_engine_rejects_non_postgres_url(mocker):
    mocker.patch("reporting.settings.SQL_DATABASE_URL", "sqlite:///seizu.db")
    mocker.patch("reporting.settings.SQL_DATABASE_USER", "")
    mocker.patch("reporting.settings.SQL_DATABASE_PASSWORD", "")

    with pytest.raises(ValueError, match="SQL_DATABASE_URL must be a PostgreSQL URL"):
        sql_module._get_engine()


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


async def _backdate_session(store, thread_id: str, updated_at: str) -> None:
    """Age a session directly. The store has no API for it -- ``touch`` only ever
    moves ``updated_at`` forward -- and the reaper's whole question is which
    sessions are old."""
    async with AsyncSession(sql_module._get_engine()) as session:
        result = await session.execute(
            select(sql_module.ChatSessionRecord).where(sql_module.ChatSessionRecord.thread_id == thread_id)
        )
        record = result.scalar_one()
        record.updated_at = updated_at
        session.add(record)
        await session.commit()


async def test_list_idle_chat_sessions_selects_only_the_stale_ones(store, mocker):
    """The reaper's one cross-user read (SBX-011). Anything it returns is about
    to be deleted, so "recently updated" must never appear in it."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=["old", "fresh"],
    )
    await store.create_chat_session("user-1", title="Old")
    await store.create_chat_session("user-2", title="Fresh")
    await _backdate_session(store, "old", "2020-01-01T00:00:00+00:00")

    idle = await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10)

    assert [(i.user_id, i.thread_id) for i in idle] == [("user-1", "old")]


async def test_list_idle_chat_sessions_ignores_headless_sessions(store, mocker):
    """Scheduled run sessions belong to a schedule's history, are bounded by it,
    and never leave a suspended sandbox behind."""
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="run-1",
    )
    await store.create_chat_session("user-1", title="Run", origin="scheduled", scheduled_chat_id="sc-1")
    await _backdate_session(store, "run-1", "2020-01-01T00:00:00+00:00")

    assert await store.list_idle_chat_sessions("2021-01-01T00:00:00+00:00", limit=10) == []


async def test_claiming_a_session_that_moved_reports_failure(store, mocker):
    """A conflict means keep, not retry: the conditional UPDATE is the only
    thing standing between a sweep and a conversation its owner just returned
    to."""
    mocker.patch("reporting.services.report_store.sql.generate_report_id", return_value="t1")
    created = await store.create_chat_session("user-1", title="Test")

    assert await store.claim_chat_session_for_retirement("user-1", "t1", "1999-01-01T00:00:00+00:00") is False
    assert await store.claim_chat_session_for_retirement("user-1", "t1", created.updated_at) is True


async def test_a_claimed_session_refuses_further_use(store, mocker):
    """Its checkpoint and sandbox are going away, so a turn must not start
    against it -- and it must not be renamed into looking alive either."""
    mocker.patch("reporting.services.report_store.sql.generate_report_id", return_value="t1")
    created = await store.create_chat_session("user-1", title="Test")
    assert await store.claim_chat_session_for_retirement("user-1", "t1", created.updated_at) is True

    assert await store.touch_chat_session("user-1", "t1") is None
    assert await store.update_chat_session_title("user-1", "t1", "new") is None
    assert await store.complete_chat_session_run("user-1", "t1", "success", []) is None


async def test_a_claimed_session_is_left_untouched_by_concurrent_writes(store, mocker):
    """Not just refused -- unmodified. The guard is evaluated by the database as
    part of the write, so there is no window where a claim lands between a read
    and its update and the update commits anyway."""
    mocker.patch("reporting.services.report_store.sql.generate_report_id", return_value="t1")
    created = await store.create_chat_session("user-1", title="Test")
    assert await store.claim_chat_session_for_retirement("user-1", "t1", created.updated_at) is True

    assert await store.touch_chat_session("user-1", "t1") is None
    assert await store.update_chat_session_title("user-1", "t1", "renamed") is None

    unchanged = await store.get_chat_session("user-1", "t1")
    assert unchanged is not None
    assert (unchanged.updated_at, unchanged.title) == (created.updated_at, "Test")


async def test_a_claim_can_be_retried_after_a_failed_sweep(store, mocker):
    """A pass that died between claiming and finishing has to be resumable, or
    the session is stuck claimed and its transcript is never collected."""
    mocker.patch("reporting.services.report_store.sql.generate_report_id", return_value="t1")
    created = await store.create_chat_session("user-1", title="Test")

    assert await store.claim_chat_session_for_retirement("user-1", "t1", created.updated_at) is True
    assert await store.claim_chat_session_for_retirement("user-1", "t1", created.updated_at) is True


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


async def test_workflow_chat_session_is_hidden_and_starts_running(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="workflow-1",
    )

    created = await store.create_chat_session(
        "user-1",
        title="Workflow",
        origin="workflow",
    )

    assert created.origin == "workflow"
    assert created.run_status == "running"
    assert await store.list_chat_sessions("user-1", limit=10) == []


async def test_delete_scheduled_chat_removes_associated_sessions(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        side_effect=["sc-1", "thread-1"],
    )
    await store.create_scheduled_chat(
        name="Digest",
        prompt="Summarize",
        schedule={"type": "hourly", "interval_hours": 1},
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )
    await store.create_chat_session(
        "user-1",
        title="Run",
        origin="scheduled",
        scheduled_chat_id="sc-1",
    )

    assert await store.delete_scheduled_chat("sc-1") is True
    assert await store.get_chat_session("user-1", "thread-1") is None


async def test_partial_scheduled_chat_result_clears_stale_errors(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sc-1",
    )
    await store.create_scheduled_chat(
        name="Digest",
        prompt="Summarize",
        schedule={"type": "hourly", "interval_hours": 1},
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )
    await store.record_scheduled_chat_result("sc-1", "failure", error="boom")
    await store.record_scheduled_chat_result("sc-1", "partial")

    item = await store.get_scheduled_chat("sc-1")
    assert item is not None
    assert item.last_run_status == "partial"
    assert item.last_errors == []


# ---------------------------------------------------------------------------
# Chat turn admission and event log
# ---------------------------------------------------------------------------


async def _ensure_session(user_id: str = "user-1", thread_id: str = "1001") -> None:
    """Give a thread a session row.

    Admission moves the session's timestamp in the same commit as the turn, so
    a turn cannot exist without a session to admit it -- which is the point.
    Written directly because ``create_chat_session`` mints its own thread id.
    """
    async with AsyncSession(sql_module._get_engine()) as session:
        existing = (
            (
                await session.execute(
                    select(sql_module.ChatSessionRecord).where(
                        sql_module.ChatSessionRecord.user_id == user_id,
                        sql_module.ChatSessionRecord.thread_id == thread_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return
        session.add(
            sql_module.ChatSessionRecord(
                user_id=user_id,
                thread_id=thread_id,
                title="",
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )
        await session.commit()


_TURN_KEYS = itertools.count()


def _turn_command(message: str = "hello") -> ChatTurnCommand:
    return ChatTurnCommand(
        message=message,
        permission_cap=[],
        timeout_seconds=settings.CHAT_TURN_TIMEOUT_SECONDS,
    )


async def _admit(store, user_id: str = "user-1", thread_id: str = "1001", key: str | None = None):
    await _ensure_session(user_id, thread_id)
    key = key or f"ik_test_{next(_TURN_KEYS)}"
    return await store.admit_chat_turn(user_id, thread_id, "msg_1", "text_1", key, _turn_command())


async def _open_turn(store, user_id: str = "user-1", thread_id: str = "1001"):
    admission = await _admit(store, user_id, thread_id)
    assert admission.turn is not None, admission.outcome
    return admission.turn


async def _expire_turn(store, turn_id: str, expires_at: str) -> None:
    """Age a turn directly; the store only ever pushes expiry forward."""
    async with AsyncSession(sql_module._get_engine()) as session:
        record = await session.get(sql_module.ChatTurnRecord, turn_id)
        record.expires_at = expires_at
        session.add(record)
        await session.commit()


# --- admission -------------------------------------------------------------


async def test_a_running_turns_lease_outlasts_the_turn_itself(store):
    """Admission retires a turn whose lease has lapsed, so a lease shorter than
    the turn's own timeout lets one send retire a turn that is merely slow --
    and the thread ends up with two producers writing one conversation. The
    replay retention window is far shorter than a turn may legitimately run, so
    it is the wrong clock for this."""
    turn = await _open_turn(store)

    latest_possible_finish = datetime.now(tz=UTC) + timedelta(seconds=settings.CHAT_TURN_TIMEOUT_SECONDS)
    assert turn.expires_at > latest_possible_finish.isoformat()


async def test_admission_reports_what_it_did(store):
    """An outcome, not an exception the caller has to interpret. Admission used
    to raise one of four errors inferred from whichever constraint rejected the
    write, so one collision could mean any of them."""
    admission = await _admit(store)

    assert admission.outcome == "created"
    assert admission.turn is not None


async def test_a_repeat_of_a_request_resolves_to_the_turn_it_made(store):
    """How a lost response is fixed: ask again. It is never read as anything
    else -- and in particular never as a cancellation."""
    first = await _admit(store, key="ik_repeated1")
    second = await _admit(store, key="ik_repeated1")

    assert first.outcome == "created"
    assert second.outcome == "existing"
    assert second.turn is not None and first.turn is not None
    assert second.turn.turn_id == first.turn.turn_id


async def test_a_different_key_on_a_busy_thread_is_busy(store):
    await _admit(store, key="ik_thefirst1")

    admission = await _admit(store, key="ik_thesecond")

    assert admission.outcome == "busy"
    assert admission.turn is None


async def test_admission_to_a_missing_session_is_retired(store):
    admission = await store.admit_chat_turn("user-1", "9999", "msg_1", "text_1", "ik_missing1", _turn_command())

    assert admission.outcome == "retired"


async def test_admission_to_a_session_being_retired_is_refused(store):
    """The claim is what closes a conversation to new turns; admitting one
    afterwards is the race the single write exists to remove."""
    await _ensure_session()
    async with AsyncSession(sql_module._get_engine()) as session:
        record = (
            (
                await session.execute(
                    select(sql_module.ChatSessionRecord).where(sql_module.ChatSessionRecord.thread_id == "1001")
                )
            )
            .scalars()
            .first()
        )
        record.retiring_at = "2024-01-01T00:00:00+00:00"
        session.add(record)
        await session.commit()

    assert (await _admit(store)).outcome == "retired"


async def test_admission_moves_the_session_timestamp(store):
    """The same write, so a delete cannot read the fresh timestamp, claim the
    session and cascade in between."""
    await _ensure_session()

    await _admit(store)

    session_item = await store.get_chat_session("user-1", "1001")
    assert session_item is not None
    assert session_item.updated_at > "2024-01-01T00:00:00+00:00"


async def test_a_new_turn_takes_over_from_an_expired_lease(store):
    """An expired lease means the producer is gone, so it must not keep holding
    the thread."""
    stale = await _open_turn(store)
    await _expire_turn(store, stale.turn_id, "2020-01-01T00:00:00+00:00")

    fresh = await _admit(store)

    assert fresh.outcome == "created"
    assert fresh.turn is not None and fresh.turn.turn_id != stale.turn_id
    assert (await store.get_chat_turn(stale.turn_id)).status == "failed"


async def test_admission_does_not_retire_a_live_lease(store):
    """Retiring a turn whose producer is alive would put a second producer on
    the thread. The retirement and the insert are one transaction, and the
    retirement only matches a lease that has already lapsed."""
    live = await _open_turn(store)

    assert (await _admit(store)).outcome == "busy"
    assert (await store.get_chat_turn(live.turn_id)).status == "running"


async def test_only_a_running_turn_can_be_finished(store):
    """First writer wins. Two writers reach here for one turn -- the turn
    closing itself, and the workflow's fallback closing it after the activity
    timed out -- and a turn that timed out *spuriously* is still alive. Without
    this the late one replaces the recorded outcome, including the `last_seq` a
    reader uses to know it has seen the whole answer."""
    turn = await _open_turn(store)

    won = await store.finish_chat_turn(turn.turn_id, "completed", 7)
    lost = await store.finish_chat_turn(turn.turn_id, "failed", 0)

    assert won is not None and won.status == "completed"
    # The loser is handed what is recorded, not what it asked for, so it can
    # tell the difference.
    assert lost is not None
    assert lost.status == "completed"
    assert lost.last_seq == 7
    stored = await store.get_chat_turn(turn.turn_id)
    assert stored is not None and (stored.status, stored.last_seq) == ("completed", 7)


async def test_finishing_a_missing_turn_is_still_none(store):
    """`None` keeps meaning "no such turn", so a caller can tell a turn that is
    gone from one another writer already closed."""
    assert await store.finish_chat_turn("turn-nope", "completed", 1) is None


async def test_two_concurrent_finishes_cannot_both_win(store, tmp_path):
    """The database settles this, not a read above the write.

    Its own file-backed engine for the reason given below: under StaticPool the
    two sessions share one connection, so a broken read-then-write passes with
    both callers reporting success.
    """

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/finish.db")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    try:
        with patch("reporting.services.report_store.sql._get_engine", return_value=engine):
            concurrent = SQLModelReportStore()
            async with AsyncSession(engine) as session:
                session.add(
                    sql_module.ChatSessionRecord(
                        user_id="user-1",
                        thread_id="1001",
                        title="",
                        created_at="2024-01-01T00:00:00+00:00",
                        updated_at="2024-01-01T00:00:00+00:00",
                    )
                )
                await session.commit()
            admission = await concurrent.admit_chat_turn(
                "user-1", "1001", "msg_1", "text_1", "ik_finish1", _turn_command()
            )
            assert admission.turn is not None
            turn_id = admission.turn.turn_id

            results = await asyncio.gather(
                concurrent.finish_chat_turn(turn_id, "completed", 5),
                concurrent.finish_chat_turn(turn_id, "failed", 0),
            )

            # Both see the same recorded outcome, whichever of them wrote it.
            statuses = {r.status for r in results if r is not None}
            assert len(statuses) == 1
            async with AsyncSession(engine) as session:
                row = await session.get(sql_module.ChatTurnRecord, turn_id)
            assert row is not None
            assert (row.status, row.last_seq) in {("completed", 5), ("failed", 0)}
    finally:
        await engine.dispose()


async def test_two_concurrent_admissions_cannot_both_win(store, tmp_path):
    """The database is the authority, not a read above the insert.

    Uses its own file-backed engine: the shared ``store`` fixture's StaticPool
    hands every session the same connection, so two "concurrent" sessions
    interleave inside one transaction and the race cannot occur -- both callers
    appear to succeed even though only one row lands.
    """

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/turns.db")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    try:
        with patch("reporting.services.report_store.sql._get_engine", return_value=engine):
            concurrent = SQLModelReportStore()
            async with AsyncSession(engine) as session:
                session.add(
                    sql_module.ChatSessionRecord(
                        user_id="user-1",
                        thread_id="1001",
                        title="",
                        created_at="2024-01-01T00:00:00+00:00",
                        updated_at="2024-01-01T00:00:00+00:00",
                    )
                )
                await session.commit()
            results = await asyncio.gather(
                concurrent.admit_chat_turn("user-1", "1001", "msg_1", "text_1", "ik_concurrent1", _turn_command()),
                concurrent.admit_chat_turn("user-1", "1001", "msg_1", "text_1", "ik_concurrent2", _turn_command()),
            )

            assert sorted(r.outcome for r in results) == ["busy", "created"]
            async with AsyncSession(engine) as session:
                rows = (await session.execute(select(sql_module.ChatTurnRecord))).scalars().all()
            assert len(rows) == 1
    finally:
        await engine.dispose()


# --- cancellation ----------------------------------------------------------


async def test_cancel_flags_the_running_turn(store):
    turn = await _open_turn(store)

    flagged = await store.request_chat_turn_cancel(turn.turn_id, "user-1")

    assert flagged is not None and flagged.cancel_requested is True
    assert (await store.get_chat_turn(turn.turn_id)).cancel_requested is True


async def test_cancel_is_scoped_to_the_owner(store):
    turn = await _open_turn(store)

    assert await store.request_chat_turn_cancel(turn.turn_id, "someone-else") is None


async def test_cancel_of_a_finished_turn_reports_nothing(store):
    turn = await _open_turn(store)
    await store.finish_chat_turn(turn.turn_id, "completed", 1)

    assert await store.request_chat_turn_cancel(turn.turn_id, "user-1") is None


async def test_cancel_creates_nothing(store):
    """It only ever marks a turn that exists. A client cannot name a turn before
    admission gives it one, so there is nothing here to create or park."""
    await _ensure_session()

    assert await store.request_chat_turn_cancel("no-such-turn", "user-1") is None

    async with AsyncSession(sql_module._get_engine()) as session:
        rows = (await session.execute(select(sql_module.ChatTurnRecord))).scalars().all()
    assert rows == []


# --- the event log ---------------------------------------------------------


async def test_chat_turn_round_trips_a_batch(store):
    turn = await _open_turn(store)
    assert await store.append_chat_turn_events(turn.turn_id, '[{"type":"start"}]') == 1

    page = await store.read_chat_turn_events(turn.turn_id, 0, limit=10)

    assert page is not None
    assert [(b.seq, b.parts_json) for b in page.batches] == [(1, '[{"type":"start"}]')]
    assert page.turn.status == "running"


async def test_chat_turn_events_are_returned_verbatim(store):
    """The stored text is what the live stream sent. Re-encoding it -- which a
    JSON column would do -- makes a replay differ from the original."""
    turn = await _open_turn(store)
    parts = '[{"type":"data-seizu-detail","id":"d1","data":{"body":null,"ratio":0.5}}]'
    await store.append_chat_turn_events(turn.turn_id, parts)

    page = await store.read_chat_turn_events(turn.turn_id, 0, limit=10)

    assert page is not None
    assert page.batches[0].parts_json == parts


async def test_chat_turn_appends_allocate_a_dense_sequence(store):
    """A turn has several producers once its plan steps run as their own
    activities (AGT-018), so none of them may choose the number. Allocating it
    here is what keeps the log gap-free across all of them."""
    turn = await _open_turn(store)

    allocated = [await store.append_chat_turn_events(turn.turn_id, f'["{n}"]') for n in range(3)]

    assert allocated == [1, 2, 3]
    page = await store.read_chat_turn_events(turn.turn_id, 0, limit=10)
    assert page is not None
    assert [b.seq for b in page.batches] == [1, 2, 3]


async def test_a_lost_allocation_race_retries_rather_than_leaving_a_gap(store):
    """Serialization is the database's job -- the turn row is locked, and on
    PostgreSQL that settles it. This pins the belt-and-braces half: if another
    producer takes the number anyway, the append re-reads the maximum instead of
    failing or skipping ahead, because the reader truncates at the first gap.

    (The race itself cannot be reproduced on the SQLite test seam, whose single
    shared connection has no isolation between sessions to lose.)
    """
    turn = await _open_turn(store)
    async with AsyncSession(sql_module._get_engine()) as session:
        session.add(
            sql_module.ChatTurnEventRecord(
                turn_id=turn.turn_id, seq=1, parts_json='["taken"]', created_at="2024-01-01T00:00:00+00:00"
            )
        )
        await session.commit()

    assert await store.append_chat_turn_events(turn.turn_id, '["mine"]') == 2

    page = await store.read_chat_turn_events(turn.turn_id, 0, limit=10)
    assert page is not None
    assert [(b.seq, b.parts_json) for b in page.batches] == [(1, '["taken"]'), (2, '["mine"]')]


async def test_chat_turn_append_reports_a_deleted_turn(store):
    """Nothing to append to, and writing anyway leaves rows no reader reaches."""
    turn = await _open_turn(store)
    await store.delete_chat_turn(turn.turn_id)

    assert await store.append_chat_turn_events(turn.turn_id, '["one"]') is None


async def test_chat_turn_read_truncates_at_the_first_gap(store):
    """A reader advances its cursor to the last batch it received. Handing it
    seq 3 while 2 is missing would move the cursor past 2 forever."""
    turn = await _open_turn(store)
    await store.append_chat_turn_events(turn.turn_id, '["one"]')
    async with AsyncSession(sql_module._get_engine()) as session:
        session.add(
            sql_module.ChatTurnEventRecord(
                turn_id=turn.turn_id, seq=3, parts_json='["three"]', created_at="2024-01-01T00:00:00+00:00"
            )
        )
        await session.commit()

    page = await store.read_chat_turn_events(turn.turn_id, 0, limit=10)

    assert page is not None
    assert [b.seq for b in page.batches] == [1]


async def test_chat_turn_read_resumes_from_a_cursor(store):
    turn = await _open_turn(store)
    for seq in (1, 2, 3):
        await store.append_chat_turn_events(turn.turn_id, f'["{seq}"]')

    page = await store.read_chat_turn_events(turn.turn_id, 1, limit=10)

    assert page is not None
    assert [b.seq for b in page.batches] == [2, 3]


async def test_chat_turn_rejects_an_oversized_batch(store):
    turn = await _open_turn(store)
    with pytest.raises(ValueError):
        await store.append_chat_turn_events(turn.turn_id, "x" * (CHAT_TURN_MAX_BATCH_BYTES + 1))


async def test_chat_turn_payload_round_trips_and_is_deleted_with_the_turn(store):
    """A distributed step's oversized result is stored here rather than carried
    through Temporal history, and is collected with the turn it belongs to."""
    turn = await _open_turn(store)
    await store.put_chat_turn_payload(turn.turn_id, "step_1", '{"output": "big"}')

    assert await store.get_chat_turn_payload(turn.turn_id, "step_1") == '{"output": "big"}'

    await store.delete_chat_turn(turn.turn_id)
    assert await store.get_chat_turn_payload(turn.turn_id, "step_1") is None


async def test_finish_chat_turn_records_the_final_sequence(store):
    """last_seq is what lets a reader tell "finished" from "finished, and you
    have seen all of it"."""
    turn = await _open_turn(store)
    finished = await store.finish_chat_turn(turn.turn_id, "completed", 7)

    assert finished is not None
    assert (finished.status, finished.last_seq) == ("completed", 7)


async def test_get_active_chat_turn_ignores_finished_turns(store):
    turn = await _open_turn(store)
    assert await store.get_active_chat_turn("user-1", "1001") is not None
    await store.finish_chat_turn(turn.turn_id, "completed", 1)
    assert await store.get_active_chat_turn("user-1", "1001") is None


async def test_an_expired_running_turn_is_not_offered_for_reconnect(store):
    """Its producer is gone, so there is nothing to reattach to."""
    turn = await _open_turn(store)
    await _expire_turn(store, turn.turn_id, "2020-01-01T00:00:00+00:00")

    assert await store.get_active_chat_turn("user-1", "1001") is None


async def test_get_chat_turn_is_scoped_to_its_owner(store):
    turn = await _open_turn(store)
    assert await store.get_chat_turn(turn.turn_id, user_id="user-1") is not None
    assert await store.get_chat_turn(turn.turn_id, user_id="someone-else") is None


async def test_delete_chat_turn_removes_its_batches(store):
    turn = await _open_turn(store)
    await store.append_chat_turn_events(turn.turn_id, '["one"]')

    assert await store.delete_chat_turn(turn.turn_id) is True

    assert await store.read_chat_turn_events(turn.turn_id, 0, limit=10) is None


async def test_deleting_a_turn_collects_batches_whose_header_is_gone(store):
    """A producer that kept writing after its conversation was deleted leaves
    headerless batches; gating the batch delete on the header meant the only
    rows worth collecting were the ones skipped."""
    turn = await _open_turn(store)
    await store.append_chat_turn_events(turn.turn_id, '["one"]')
    async with AsyncSession(sql_module._get_engine()) as session:
        await session.execute(
            sql_module.delete(sql_module.ChatTurnRecord).where(
                sql_module.col(sql_module.ChatTurnRecord.turn_id) == turn.turn_id
            )
        )
        await session.commit()

    assert await store.delete_chat_turn(turn.turn_id) is True

    async with AsyncSession(sql_module._get_engine()) as session:
        remaining = (
            (
                await session.execute(
                    select(sql_module.ChatTurnEventRecord).where(sql_module.ChatTurnEventRecord.turn_id == turn.turn_id)
                )
            )
            .scalars()
            .all()
        )
    assert remaining == []


async def test_deleting_a_session_deletes_its_turn_logs(store):
    """A turn log is only reachable through its session, so one that outlived
    its session is a row nothing will ever look for again."""
    turn = await _open_turn(store)
    await store.append_chat_turn_events(turn.turn_id, '["one"]')

    assert await store.delete_chat_session("user-1", "1001") is True

    assert await store.get_chat_turn(turn.turn_id) is None


async def test_list_expired_chat_turns_selects_only_expired(store):
    live = await _open_turn(store)
    stale = await _open_turn(store, "user-2", "2002")
    await _expire_turn(store, stale.turn_id, "2020-01-01T00:00:00+00:00")

    expired = await store.list_expired_chat_turns("2021-01-01T00:00:00+00:00", limit=10)

    assert expired == [stale.turn_id]
    assert live.turn_id not in expired


# ---------------------------------------------------------------------------
# Scheduled chat CRUD
# ---------------------------------------------------------------------------


async def test_get_scheduled_chat_returns_none_for_unknown(store):
    result = await store.get_scheduled_chat("does-not-exist")
    assert result is None


async def test_update_scheduled_chat_bumps_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sc-u",
    )
    await store.create_scheduled_chat(
        name="Original",
        prompt="Prompt",
        schedule={"type": "hourly", "interval_hours": 1},
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )

    updated = await store.update_scheduled_chat(
        "sc-u",
        name="Renamed",
        prompt="New prompt",
        schedule=None,
        watch_scans=[],
        enabled=False,
        updated_by="user-2",
        comment="v2",
    )

    assert updated is not None
    assert updated.name == "Renamed"
    assert updated.current_version == 2
    assert updated.enabled is False


async def test_update_nonexistent_scheduled_chat_returns_none(store):
    result = await store.update_scheduled_chat(
        "no-such-id",
        name="X",
        prompt="Y",
        schedule=None,
        watch_scans=[],
        enabled=True,
        updated_by="user-1",
    )
    assert result is None


async def test_list_scheduled_chat_versions_returns_in_desc_order(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sc-v",
    )
    await store.create_scheduled_chat(
        name="V",
        prompt="P",
        schedule=None,
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )
    await store.update_scheduled_chat(
        "sc-v",
        name="V2",
        prompt="P2",
        schedule=None,
        watch_scans=[],
        enabled=True,
        updated_by="user-1",
    )

    versions = await store.list_scheduled_chat_versions("sc-v")

    assert [v.version for v in versions] == [2, 1]


async def test_get_scheduled_chat_version_returns_correct_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sc-gv",
    )
    await store.create_scheduled_chat(
        name="GV",
        prompt="P",
        schedule=None,
        watch_scans=[],
        enabled=True,
        created_by="user-1",
    )

    v = await store.get_scheduled_chat_version("sc-gv", 1)
    assert v is not None
    assert v.version == 1
    assert v.name == "GV"

    missing = await store.get_scheduled_chat_version("sc-gv", 99)
    assert missing is None


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
    assert result[0].current_version == 1


# ---------------------------------------------------------------------------
# get_report_latest
# ---------------------------------------------------------------------------


async def test_get_report_latest_not_found(store):
    assert await store.get_report_latest("missing") is None


async def test_get_report_latest_returns_initial_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r1", created_by="user@example.com")
    result = await store.get_report_latest("rid1")
    assert result is not None
    assert result.version == 1
    assert result.config == {"name": "r1", "rows": [], "schema_version": 1}
    assert result.comment == "Initial version"


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
    assert result.version == 2
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
    assert result.version == 3
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
    v3 = await store.get_report_version("rid1", 3)
    assert v1.version == 1
    assert v1.name == "r"
    assert v1.config == {"name": "r", "rows": [], "schema_version": 1}
    assert v2.version == 2
    assert v2.config == {"name": "r", "v": 1}
    assert v3.version == 3
    assert v3.config == {"name": "r", "v": 2}


# ---------------------------------------------------------------------------
# list_report_versions
# ---------------------------------------------------------------------------


async def test_list_report_versions_empty(store):
    assert await store.list_report_versions("missing") == []


async def test_list_report_versions_contains_initial_version(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="rid1",
    )
    await store.create_report(name="r", created_by="u@x.com")
    versions = await store.list_report_versions("rid1")
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].config == {"name": "r", "rows": [], "schema_version": 1}


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
    assert len(versions) == 4
    assert versions[0].version == 4
    assert versions[1].version == 3
    assert versions[2].version == 2
    assert versions[3].version == 1


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
    assert result.current_version == 1


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
    assert result.version == 2
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
    assert result[0].current_version == 2


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
    assert latest.version == 3


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
    assert report.version == 2


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
    schedule=None,
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


async def test_scheduled_query_runtime_state_methods(store, mocker):
    mocker.patch(
        "reporting.services.report_store.sql.generate_report_id",
        return_value="sq1",
    )
    await store.create_scheduled_query(**_SQ_KWARGS)

    requested_at = await store.request_scheduled_query_run("sq1")
    assert requested_at is not None
    assert await store.request_scheduled_query_run("missing") is None

    await store.record_scheduled_query_result("sq1", "failure", "boom")
    failed = await store.get_scheduled_query("sq1")
    assert failed is not None
    assert failed.last_run_status == "failure"
    assert failed.last_errors[0]["error"] == "boom"
    await store.record_scheduled_query_result("sq1", "success")
    succeeded = await store.get_scheduled_query("sq1")
    assert succeeded is not None
    assert succeeded.last_errors == []
    await store.record_scheduled_query_result("missing", "success")

    await store.set_workflow_schedule_sync_status(
        "sq1",
        "error",
        error="offline",
        synced_at="2026-01-01T00:00:00+00:00",
    )
    synced = await store.get_scheduled_query("sq1")
    assert synced is not None
    assert synced.schedule_sync_status == "error"
    assert synced.schedule_sync_error == "offline"
    await store.set_workflow_schedule_sync_status("missing", "synced")


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
        schedule=None,
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
        schedule=None,
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
        schedule=None,
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
    assert versions[0].name == "Updated"
    assert versions[1].name == "Test Query"


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


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------


async def test_list_spaces_empty(store):
    assert await store.list_spaces() == []


async def test_get_space_and_list_spaces(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    fetched = await store.get_space(space.space_id)
    assert fetched is not None
    assert fetched.space_id == space.space_id
    assert [s.space_id for s in await store.list_spaces()] == [space.space_id]


async def test_get_space_not_found(store):
    assert await store.get_space("missing") is None


async def test_update_space(store):
    space = await store.create_space(name="Cloud", description="old", created_by="u1")
    updated = await store.update_space(
        space_id=space.space_id,
        name="Cloud Security",
        description="new",
        updated_by="u2",
    )
    assert updated is not None
    assert updated.name == "Cloud Security"
    assert updated.description == "new"
    assert updated.updated_by == "u2"
    assert updated.created_by == "u1"
    # Renaming a space does not rename its overview report.
    assert updated.overview_report_id == space.overview_report_id


async def test_update_space_not_found(store):
    assert await store.update_space(space_id="missing", name="x", description="", updated_by="u1") is None


async def test_delete_space_not_found(store):
    assert await store.delete_space("missing") == SpaceDeleteResult.NOT_FOUND


async def test_delete_space_blocked_by_member_report(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    report = await store.create_report(name="Member", created_by="u1", access=_PUBLIC)
    await store.update_report_space(
        report_id=report.report_id,
        space_id=space.space_id,
        subspace_id=None,
        updated_by="u1",
    )
    assert await store.delete_space(space.space_id) == SpaceDeleteResult.NOT_EMPTY
    assert await store.get_space(space.space_id) is not None


async def test_delete_space_emptiness_ignores_report_visibility(store):
    """A private report belonging to another user still blocks the delete.

    Evaluating emptiness through the caller's visibility filter would let this
    space read as empty and be deleted, orphaning user B's report.
    """
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    other_id = await _plant_private_member(store, space.space_id, created_by="u2")
    # u1 cannot see the report at all...
    visible_to_u1 = await store.list_space_reports(space.space_id, user_id="u1")
    assert all(item.report_id != other_id for item in visible_to_u1)
    # ...but it still keeps the space non-empty.
    assert await store.delete_space(space.space_id) == SpaceDeleteResult.NOT_EMPTY


async def test_delete_space_removes_its_subspaces(store):
    """Sub-spaces are grouping labels, so they go with the space rather than blocking it."""
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    sub = await store.create_subspace(space_id=space.space_id, name="Network", created_by="u1")

    assert await store.delete_space(space.space_id) == SpaceDeleteResult.DELETED
    assert await store.get_space(space.space_id) is None
    assert await store.get_subspace(sub.subspace_id) is None
    assert await store.list_subspaces(space.space_id) == []


async def test_list_space_reports_filters_by_space_and_visibility(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    other_space = await store.create_space(name="Other", description="", created_by="u1")

    mine = await store.create_report(name="Mine", created_by="u1", access=_PUBLIC)
    await store.update_report_space(
        report_id=mine.report_id, space_id=space.space_id, subspace_id=None, updated_by="u1"
    )
    theirs_id = await _plant_private_member(store, space.space_id, created_by="u2")

    visible = await store.list_space_reports(space.space_id, user_id="u1")
    ids = {item.report_id for item in visible}
    assert ids == {mine.report_id}
    assert theirs_id not in ids
    assert await store.list_space_reports(other_space.space_id, user_id="u1") == []

    unfiltered = await store.list_space_reports(space.space_id)
    assert theirs_id in {item.report_id for item in unfiltered}


# ---------------------------------------------------------------------------
# Sub-spaces
# ---------------------------------------------------------------------------


async def test_create_subspace_requires_existing_space(store):
    assert await store.create_subspace(space_id="missing", name="Network", created_by="u1") is None


async def test_subspace_crud(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    sub = await store.create_subspace(space_id=space.space_id, name="Network", created_by="u1")
    assert isinstance(sub, SubspaceItem)
    assert sub.space_id == space.space_id
    assert sub.name == "Network"

    assert (await store.get_subspace(sub.subspace_id)).name == "Network"
    assert [s.subspace_id for s in await store.list_subspaces(space.space_id)] == [sub.subspace_id]

    updated = await store.update_subspace(subspace_id=sub.subspace_id, name="Networking", updated_by="u2")
    assert updated is not None
    assert updated.name == "Networking"
    assert updated.updated_by == "u2"
    assert updated.space_id == space.space_id

    assert await store.delete_subspace(sub.subspace_id) is True
    assert await store.get_subspace(sub.subspace_id) is None
    assert await store.list_subspaces(space.space_id) == []


async def test_update_and_delete_subspace_not_found(store):
    assert await store.update_subspace(subspace_id="missing", name="x", updated_by="u1") is None
    assert await store.delete_subspace("missing") is False


async def test_delete_subspace_leaves_member_reports_in_place(store):
    """Reports keep a dangling subspace_id rather than triggering a fan-out write.

    An unresolvable subspace_id reads as ungrouped at the API boundary.
    """
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    sub = await store.create_subspace(space_id=space.space_id, name="Network", created_by="u1")
    report = await store.create_report(name="Member", created_by="u1", access=_PUBLIC)
    await store.update_report_space(
        report_id=report.report_id,
        space_id=space.space_id,
        subspace_id=sub.subspace_id,
        updated_by="u1",
    )

    assert await store.delete_subspace(sub.subspace_id) is True
    still_there = await store.get_report_metadata(report.report_id)
    assert still_there is not None
    assert still_there.space_id == space.space_id
    assert still_there.subspace_id == sub.subspace_id


# ---------------------------------------------------------------------------
# Report space membership
# ---------------------------------------------------------------------------


async def test_create_report_defaults_to_no_space(store):
    report = await store.create_report(name="Loose", created_by="u1")
    assert report.space_id is None
    assert report.subspace_id is None


async def test_create_report_with_space_membership(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    sub = await store.create_subspace(space_id=space.space_id, name="Network", created_by="u1")
    report = await store.create_report(
        name="Member",
        created_by="u1",
        access=_PUBLIC,
        space_id=space.space_id,
        subspace_id=sub.subspace_id,
    )
    assert report.space_id == space.space_id
    assert report.subspace_id == sub.subspace_id
    stored = await store.get_report_metadata(report.report_id)
    assert stored.space_id == space.space_id
    assert stored.subspace_id == sub.subspace_id


async def test_update_report_space_replaces_membership(store):
    space_a = await store.create_space(name="A", description="", created_by="u1")
    space_b = await store.create_space(name="B", description="", created_by="u1")
    sub_a = await store.create_subspace(space_id=space_a.space_id, name="Sub", created_by="u1")
    report = await store.create_report(
        name="Member",
        created_by="u1",
        access=_PUBLIC,
        space_id=space_a.space_id,
        subspace_id=sub_a.subspace_id,
    )

    # Replace semantics: moving to another space with no sub-space clears it.
    moved = await store.update_report_space(
        report_id=report.report_id,
        space_id=space_b.space_id,
        subspace_id=None,
        updated_by="u2",
    )
    assert moved is not None
    assert moved.space_id == space_b.space_id
    assert moved.subspace_id is None
    assert moved.updated_by == "u2"

    cleared = await store.update_report_space(
        report_id=report.report_id, space_id=None, subspace_id=None, updated_by="u2"
    )
    assert cleared.space_id is None


async def test_create_report_refuses_a_private_report_in_a_space(store):
    """The store owns the invariant, not just its callers."""
    space = await store.create_space(name="Cloud", description="", created_by="u1")

    with pytest.raises(SpaceConflictError):
        await store.create_report(
            name="Draft",
            created_by="u1",
            access=ReportAccess(scope="private"),
            space_id=space.space_id,
        )


async def test_create_report_defaults_are_refused_in_a_space(store):
    """New reports default to private, so a bare create into a space is refused."""
    space = await store.create_space(name="Cloud", description="", created_by="u1")

    with pytest.raises(SpaceConflictError):
        await store.create_report(name="Draft", created_by="u1", space_id=space.space_id)


async def test_update_report_space_refuses_to_file_a_private_report(store):
    """Enforced in the store, so a race cannot slip a draft into a space."""
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    draft = await store.create_report(name="Draft", created_by="u1")

    with pytest.raises(SpaceConflictError):
        await store.update_report_space(
            report_id=draft.report_id,
            space_id=space.space_id,
            subspace_id=None,
            updated_by="u1",
        )

    assert (await store.get_report_metadata(draft.report_id)).space_id is None


async def test_update_report_visibility_refuses_to_privatise_a_member(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    member = await store.create_report(name="Member", created_by="u1", access=_PUBLIC, space_id=space.space_id)

    with pytest.raises(SpaceConflictError):
        await store.update_report_visibility(
            report_id=member.report_id,
            updated_by="u1",
            access=ReportAccess(scope="private"),
        )

    assert (await store.get_report_metadata(member.report_id)).access.scope == "public"


async def test_update_report_visibility_allows_privatising_a_loose_report(store):
    report = await store.create_report(name="Loose", created_by="u1", access=_PUBLIC)

    updated = await store.update_report_visibility(
        report_id=report.report_id,
        updated_by="u1",
        access=ReportAccess(scope="private"),
    )
    assert updated.access.scope == "private"


async def test_update_report_space_respects_visibility(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    private = await store.create_report(name="Theirs", created_by="u2", access=ReportAccess(scope="private"))
    assert (
        await store.update_report_space(
            report_id=private.report_id,
            space_id=space.space_id,
            subspace_id=None,
            updated_by="u1",
            user_id="u1",
        )
        is None
    )


async def test_update_report_space_not_found(store):
    assert (
        await store.update_report_space(report_id="missing", space_id=None, subspace_id=None, updated_by="u1") is None
    )


async def test_save_report_version_preserves_space_membership(store):
    """Saving a version must never unfile a report."""
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    sub = await store.create_subspace(space_id=space.space_id, name="Network", created_by="u1")
    report = await store.create_report(
        name="Member", created_by="u1", access=_PUBLIC, space_id=space.space_id, subspace_id=sub.subspace_id
    )

    saved = await store.save_report_version(
        report_id=report.report_id,
        config={"name": "Member", "rows": [], "schema_version": 1},
        created_by="u1",
    )
    assert saved is not None
    assert saved.space_id == space.space_id
    assert saved.subspace_id == sub.subspace_id

    stored = await store.get_report_metadata(report.report_id)
    assert stored.space_id == space.space_id
    assert stored.subspace_id == sub.subspace_id
    assert (await store.list_reports())[0].space_id is not None


async def test_visibility_and_pin_changes_preserve_space_membership(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    report = await store.create_report(name="Member", created_by="u1", access=_PUBLIC, space_id=space.space_id)

    await store.update_report_visibility(
        report_id=report.report_id, updated_by="u1", access=ReportAccess(scope="public")
    )
    assert (await store.get_report_metadata(report.report_id)).space_id == space.space_id

    assert await store.pin_report(report.report_id, True, updated_by="u1") is True
    assert (await store.get_report_metadata(report.report_id)).space_id == space.space_id


# ---------------------------------------------------------------------------
# Store-level overview-report guards
#
# A backstop, so the invariant holds for any caller rather than only the
# transports that remember to pre-check.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The overview pointer
# ---------------------------------------------------------------------------


async def test_create_space_starts_with_no_overview(store):
    """A space no longer auto-creates a report; the overview is set later."""
    space = await store.create_space(name="Cloud Security", description="AWS", created_by="u1")

    assert space.overview_report_id is None
    assert await store.list_space_reports(space.space_id) == []


async def test_set_and_clear_the_overview_pointer(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    report = await store.create_report(name="Landing", created_by="u1", access=_PUBLIC, space_id=space.space_id)

    updated = await store.set_space_overview(space_id=space.space_id, report_id=report.report_id, updated_by="u2")
    assert updated.overview_report_id == report.report_id
    assert updated.updated_by == "u2"
    assert (await store.get_space(space.space_id)).overview_report_id == report.report_id

    cleared = await store.set_space_overview(space_id=space.space_id, report_id=None, updated_by="u2")
    assert cleared.overview_report_id is None


async def test_set_space_overview_not_found(store):
    assert await store.set_space_overview(space_id="missing", report_id=None, updated_by="u1") is None


async def test_renaming_a_space_keeps_its_overview_pointer(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    report = await store.create_report(name="Landing", created_by="u1", access=_PUBLIC, space_id=space.space_id)
    await store.set_space_overview(space_id=space.space_id, report_id=report.report_id, updated_by="u1")

    renamed = await store.update_space(space_id=space.space_id, name="Cloud Security", description="d", updated_by="u1")
    assert renamed.overview_report_id == report.report_id


async def test_the_pinned_report_is_an_ordinary_report(store):
    """No protections from being pinned: it can be moved out and deleted.

    Its visibility is governed by the public-space-member rule like any other
    member's, so unpublishing goes through leaving the space first.
    """
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    other = await store.create_space(name="Other", description="", created_by="u1")
    report = await store.create_report(name="Landing", created_by="u1", access=_PUBLIC, space_id=space.space_id)
    await store.set_space_overview(space_id=space.space_id, report_id=report.report_id, updated_by="u1")

    moved = await store.update_report_space(
        report_id=report.report_id, space_id=other.space_id, subspace_id=None, updated_by="u1"
    )
    assert moved.space_id == other.space_id

    unfiled = await store.update_report_space(
        report_id=report.report_id, space_id=None, subspace_id=None, updated_by="u1"
    )
    assert unfiled.space_id is None
    privatized = await store.update_report_visibility(
        report_id=report.report_id, updated_by="u1", access=ReportAccess(scope="private")
    )
    assert privatized.access.scope == "private"

    assert await store.delete_report(report.report_id) is True
    # The pointer is left dangling on purpose; the API resolves it lazily.
    assert (await store.get_space(space.space_id)).overview_report_id == report.report_id


async def test_delete_space_never_deletes_a_report(store):
    space = await store.create_space(name="Cloud", description="", created_by="u1")
    report = await store.create_report(name="Member", created_by="u1", access=_PUBLIC, space_id=space.space_id)
    await store.set_space_overview(space_id=space.space_id, report_id=report.report_id, updated_by="u1")

    # The pinned report still blocks the delete — it is a member report.
    assert await store.delete_space(space.space_id) == SpaceDeleteResult.NOT_EMPTY

    await store.update_report_space(report_id=report.report_id, space_id=None, subspace_id=None, updated_by="u1")
    assert await store.delete_space(space.space_id) == SpaceDeleteResult.DELETED
    # The report survives; only the space is gone.
    assert await store.get_report_metadata(report.report_id) is not None


async def test_plugin_publish_is_versioned_and_content_addressed(store):
    skillset = SkillsetListItem(
        skillset_id="incident_response",
        name="Incident response",
        current_version=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        created_by="u1",
    )
    skill = SkillItem(
        skill_id="review_alert",
        skillset_id=skillset.skillset_id,
        name="Review alert",
        description="Review one alert",
        template="Review it.",
        current_version=1,
        created_at=skillset.created_at,
        updated_at=skillset.updated_at,
        created_by="u1",
    )
    parsed = legacy_skillset_package(skillset, [skill])
    indexed_skills = [parsed.skills[0].model_copy(update={"has_scripts": True})]
    first = await store.publish_plugin(
        parsed.plugin_id,
        parsed.manifest,
        parsed.files,
        indexed_skills,
        [],
        parsed.package_digest,
        "u1",
    )
    second = await store.publish_plugin(
        parsed.plugin_id,
        parsed.manifest,
        parsed.files,
        indexed_skills,
        [],
        parsed.package_digest,
        "u1",
    )
    assert first.current_revision == 1
    assert second.current_revision == 2
    assert len(await store.list_plugin_versions(parsed.plugin_id)) == 2
    assert len(await store.list_plugin_files(parsed.plugin_id, 1)) == len(parsed.files)
    stored_skill = await store.get_enabled_plugin_skill(parsed.plugin_id, "review_alert")
    assert stored_skill.portable_name == "review-alert"
    assert stored_skill.has_scripts is True
    assert (await store.set_plugin_enabled(parsed.plugin_id, False, "u2")).enabled is False
    assert await store.get_enabled_plugin_skill(parsed.plugin_id, "review_alert") is None
    await store.set_plugin_enabled(parsed.plugin_id, True, "u2")

    with pytest.raises(PluginRevisionConflict):
        await store.publish_plugin(
            parsed.plugin_id,
            parsed.manifest,
            parsed.files,
            indexed_skills,
            [],
            parsed.package_digest,
            "u1",
            expected_revision=1,
        )

    async with AsyncSession(sql_module._get_engine()) as session:
        blobs = (await session.execute(select(sql_module.PluginBlobRecord))).scalars().all()
    assert len(blobs) == len(parsed.files)

    manifest_file = next(item for item in parsed.files if item.path == "plugin.json")
    digest = hashlib.sha256(manifest_file.content).hexdigest()
    retained = await store.read_plugin_blob(parsed.plugin_id, digest)
    assert retained.content == manifest_file.content
    assert await store.read_plugin_blob(parsed.plugin_id, "0" * 64) is None
    assert await store.read_plugin_blob("other_plugin", digest) is None


async def test_legacy_plugin_projection_takes_postgres_startup_lock(mocker):
    store = SQLModelReportStore()
    connection = mocker.AsyncMock()
    transaction = mocker.AsyncMock()
    transaction.__aenter__.return_value = connection
    engine = mocker.MagicMock()
    engine.dialect.name = "postgresql"
    engine.begin.return_value = transaction
    mocker.patch.object(sql_module, "_get_engine", return_value=engine)
    mocker.patch.object(store, "list_skillsets", new=mocker.AsyncMock(return_value=[object()]))
    migrate = mocker.patch.object(store, "_migrate_legacy_skillsets_unlocked", new=mocker.AsyncMock())

    await store._migrate_legacy_skillsets()  # noqa: SLF001

    lock_sql = str(connection.execute.await_args.args[0])
    assert "pg_advisory_xact_lock" in lock_sql
    assert "seizu-plugin-legacy-migration" in lock_sql
    migrate.assert_awaited_once_with()


async def test_legacy_plugin_projection_skips_the_lock_with_no_legacy_skillsets(mocker):
    """Every worker runs startup on every boot; the settled case costs one query."""
    store = SQLModelReportStore()
    engine = mocker.MagicMock()
    engine.dialect.name = "postgresql"
    mocker.patch.object(sql_module, "_get_engine", return_value=engine)
    mocker.patch.object(store, "list_skillsets", new=mocker.AsyncMock(return_value=[]))
    migrate = mocker.patch.object(store, "_migrate_legacy_skillsets_unlocked", new=mocker.AsyncMock())

    await store._migrate_legacy_skillsets()  # noqa: SLF001

    engine.begin.assert_not_called()
    migrate.assert_not_awaited()


async def test_deleting_a_plugin_collects_its_orphaned_blobs(store):
    """Content-addressed blobs are shared, so they are collected, not cascaded."""
    from reporting.services.plugin_packages import parse_package

    def package(plugin_id: str, body: str) -> object:
        manifest = {
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": plugin_id.replace("_", "-"),
            "extensions": {"com.mappedsky.seizu": {"skillsetId": plugin_id}},
        }
        return parse_package(
            [
                PluginFile(path="plugin.json", content=json.dumps(manifest, sort_keys=True).encode()),
                PluginFile(
                    path="skills/review/SKILL.md",
                    content=f"---\nname: review\ndescription: Review it\n---\n{body}".encode(),
                ),
                PluginFile(path="shared.txt", content=b"shared between both plugins"),
            ]
        )

    for plugin_id, body in (("alpha_tools", "Alpha."), ("beta_tools", "Beta.")):
        parsed = package(plugin_id, body)
        await store.publish_plugin(parsed.plugin_id, parsed.manifest, parsed.files, [], [], parsed.package_digest, "u1")

    async def blob_count() -> int:
        async with AsyncSession(sql_module._get_engine()) as session:
            return len((await session.execute(select(sql_module.PluginBlobRecord))).scalars().all())

    # Five distinct blobs: two manifests, two SKILL.md bodies, one shared file.
    assert await blob_count() == 5
    assert await store.delete_plugin("alpha_tools") is True
    # Its own two blobs go; the file both plugins share stays.
    assert await blob_count() == 3
    assert await store.read_plugin_blob("beta_tools", hashlib.sha256(b"shared between both plugins").hexdigest())
    assert await store.delete_plugin("beta_tools") is True
    assert await blob_count() == 0
