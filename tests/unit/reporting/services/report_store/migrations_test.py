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
        assert {"space_id", "subspace_id", "space_overview"} <= report_columns
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
        assert {"space_id", "subspace_id", "space_overview"} <= report_columns

        # The pre-existing row survives and defaults to "not in a space".
        async with engine.connect() as conn:
            row = (await conn.execute(sa.text("SELECT space_id, subspace_id, space_overview FROM reports"))).one()
        assert row.space_id is None
        assert row.subspace_id is None
        assert not row.space_overview
    finally:
        await engine.dispose()
