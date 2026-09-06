"""PostgreSQL contracts that SQLite cannot represent (STO-004, STO-005)."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from reporting import settings
from reporting.schema.chat import ChatTurnCommand
from reporting.services.report_store import sql as sql_module
from reporting.services.report_store.migrations import run_schema_migrations
from reporting.services.report_store.sql import SQLModelReportStore


def _postgres_url():
    configured = os.environ.get("TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("TEST_POSTGRES_URL is required for PostgreSQL contract tests")
    return make_url(configured).set(drivername="postgresql+asyncpg")


@asynccontextmanager
async def _isolated_engine() -> AsyncIterator[AsyncEngine]:
    url = _postgres_url()
    schema = f"test_{uuid4().hex}"
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_migrations_are_fresh_idempotent_and_create_postgres_indexes() -> None:
    async with _isolated_engine() as engine:
        await run_schema_migrations(engine)
        await run_schema_migrations(engine)

        async with engine.connect() as connection:
            tables, indexes = await connection.run_sync(
                lambda sync: (
                    set(inspect(sync).get_table_names()),
                    {index["name"] for index in inspect(sync).get_indexes("chat_turns")},
                )
            )

        assert {"reports", "chat_turns", "plugin_blobs", "model_profiles"} <= tables
        assert "uq_chat_turns_one_running" in indexes


async def test_postgres_serializes_concurrent_turn_admission(mocker) -> None:
    async with _isolated_engine() as engine:
        await run_schema_migrations(engine)
        mocker.patch.object(sql_module, "_get_engine", return_value=engine, autospec=True)
        store = SQLModelReportStore()
        chat = await store.create_chat_session("user-1", "Contract")
        command = ChatTurnCommand(
            message="hello", permission_cap=[], timeout_seconds=settings.CHAT_TURN_TIMEOUT_SECONDS
        )

        first, second = await asyncio.gather(
            store.admit_chat_turn("user-1", chat.thread_id, "message-1", "text-1", "contract-key-1", command),
            store.admit_chat_turn("user-1", chat.thread_id, "message-2", "text-2", "contract-key-2", command),
        )

        assert {first.outcome, second.outcome} == {"created", "busy"}


async def test_postgres_resolves_concurrent_idempotent_admission(mocker) -> None:
    async with _isolated_engine() as engine:
        await run_schema_migrations(engine)
        mocker.patch.object(sql_module, "_get_engine", return_value=engine, autospec=True)
        store = SQLModelReportStore()
        chat = await store.create_chat_session("user-1", "Contract")
        command = ChatTurnCommand(
            message="hello", permission_cap=[], timeout_seconds=settings.CHAT_TURN_TIMEOUT_SECONDS
        )

        first, second = await asyncio.gather(
            store.admit_chat_turn("user-1", chat.thread_id, "message-1", "text-1", "contract-key-3", command),
            store.admit_chat_turn("user-1", chat.thread_id, "message-1", "text-1", "contract-key-3", command),
        )

        assert {first.outcome, second.outcome} == {"created", "existing"}
        assert first.turn is not None and second.turn is not None
        assert first.turn.turn_id == second.turn.turn_id
