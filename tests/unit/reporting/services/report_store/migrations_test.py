"""Tests for the Alembic schema migrations run at SQL report-store startup.

The riskiest case is a *fresh* database: the baseline revision calls
``SQLModel.metadata.create_all``, so every later revision runs against a schema
that already has the tables and columns it wants to add. Unguarded DDL there
fails on every new install.
"""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from reporting.services.report_store.migrations import run_schema_migrations


async def _inspect(engine, fn):
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync_conn: fn(sa.inspect(sync_conn)))


async def test_migrations_run_on_a_fresh_database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    try:
        await run_schema_migrations(engine)

        tables = await _inspect(engine, lambda i: set(i.get_table_names()))
        assert {"spaces", "subspaces", "reports"} <= tables

        report_columns = await _inspect(engine, lambda i: {c["name"] for c in i.get_columns("reports")})
        assert {"space_id", "subspace_id"} <= report_columns
    finally:
        await engine.dispose()


async def test_migrations_are_idempotent(tmp_path):
    """Re-running against an already-migrated database is a no-op."""
    db = tmp_path / "twice.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    try:
        await run_schema_migrations(engine)
        await run_schema_migrations(engine)

        report_columns = await _inspect(engine, lambda i: [c["name"] for c in i.get_columns("reports")])
        assert report_columns.count("space_id") == 1
    finally:
        await engine.dispose()


async def test_spaces_migration_adds_columns_to_a_pre_existing_reports_table(tmp_path):
    """The upgrade path: an older database that predates the space columns.

    Because ``reports`` already exists, the baseline's ``create_all`` leaves it
    alone, so 0005 has to add the three columns explicitly.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "CREATE TABLE reports ("
                    "report_id VARCHAR PRIMARY KEY, name VARCHAR, current_version INTEGER,"
                    " created_at VARCHAR, updated_at VARCHAR, created_by VARCHAR,"
                    " updated_by VARCHAR, access JSON, pinned BOOLEAN)"
                )
            )
            await conn.execute(
                sa.text("INSERT INTO reports VALUES ('r1', 'Existing', 1, 'now', 'now', 'u1', 'u1', '{}', 0)")
            )

        await run_schema_migrations(engine)

        tables = await _inspect(engine, lambda i: set(i.get_table_names()))
        assert {"spaces", "subspaces"} <= tables
        report_columns = await _inspect(engine, lambda i: {c["name"] for c in i.get_columns("reports")})
        assert {"space_id", "subspace_id"} <= report_columns

        # The pre-existing row survives and defaults to "not in a space".
        async with engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT space_id, subspace_id FROM reports"))).one()
        assert row.space_id is None
        assert row.subspace_id is None
    finally:
        await engine.dispose()


async def test_retirement_migration_adds_the_column_and_index_to_an_existing_table(tmp_path):
    """The upgrade path for 0006: ``chat_sessions`` already exists, so the
    baseline's ``create_all`` leaves it alone and the revision has to add both
    the claim column and the index the reaper's sweep depends on."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-upgrade.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "CREATE TABLE chat_sessions ("
                    "id INTEGER PRIMARY KEY, user_id VARCHAR, thread_id VARCHAR, title VARCHAR,"
                    " created_at VARCHAR, updated_at VARCHAR, origin VARCHAR,"
                    " scheduled_chat_id VARCHAR, run_status VARCHAR, run_errors JSON)"
                )
            )
            await conn.execute(
                sa.text(
                    "INSERT INTO chat_sessions VALUES "
                    "(1, 'u1', 't1', 'Old', 'then', 'then', 'interactive', NULL, NULL, '[]')"
                )
            )

        await run_schema_migrations(engine)

        columns = await _inspect(engine, lambda i: {c["name"] for c in i.get_columns("chat_sessions")})
        assert "retiring_at" in columns
        indexes = await _inspect(engine, lambda i: {x["name"] for x in i.get_indexes("chat_sessions")})
        assert "ix_chat_sessions_origin_updated_at" in indexes

        # The pre-existing session survives, unclaimed.
        async with engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT thread_id, retiring_at FROM chat_sessions"))).one()
        assert (row.thread_id, row.retiring_at) == ("t1", None)
    finally:
        await engine.dispose()


async def test_a_fresh_database_gets_the_reaper_index_from_the_model(tmp_path):
    """0006 returns early on a fresh database, so the index has to come from the
    model's table args -- otherwise new installs sweep without one."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh-chat.db'}")
    try:
        await run_schema_migrations(engine)

        indexes = await _inspect(engine, lambda i: {x["name"] for x in i.get_indexes("chat_sessions")})
        assert "ix_chat_sessions_origin_updated_at" in indexes
    finally:
        await engine.dispose()


async def test_chat_turn_migration_creates_the_current_schema_after_0006(tmp_path):
    """The upgrade path from master must not rely on baseline ``create_all``.

    Chat-turn migrations developed on this branch were squashed before merge,
    so 0007 has to create the final schema directly rather than relying on an
    intermediate request-hash or client-token shape.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat-turn-upgrade.db'}")
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            await conn.execute(sa.text("INSERT INTO alembic_version VALUES ('0006_chat_session_retirement')"))

        await run_schema_migrations(engine)

        tables = await _inspect(engine, lambda i: set(i.get_table_names()))
        assert {"chat_turns", "chat_turn_events"} <= tables

        turn_columns = await _inspect(engine, lambda i: {c["name"]: c for c in i.get_columns("chat_turns")})
        assert {"idempotency_key", "command"} <= turn_columns.keys()
        assert turn_columns["idempotency_key"]["nullable"] is False
        assert turn_columns["command"]["nullable"] is False
        assert "request_hash" not in turn_columns
        assert "client_token" not in turn_columns

        indexes = await _inspect(engine, lambda i: {x["name"] for x in i.get_indexes("chat_turns")})
        assert {
            "ix_chat_turns_thread_status",
            "ix_chat_turns_expires_at",
            "uq_chat_turns_one_running",
            "uq_chat_turns_idempotency_key",
        } <= indexes
    finally:
        await engine.dispose()
