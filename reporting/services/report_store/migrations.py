"""Programmatic Alembic runner used during SQL report-store startup."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


def _upgrade(connection: Connection, config: Config) -> None:
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


async def run_schema_migrations(engine: AsyncEngine) -> None:
    root = Path(__file__).resolve().parents[2]
    # The engine is the authoritative connection target. Reading settings
    # again breaks callers that supply an engine directly (including tests)
    # and risks migrating a different database than the one initialized.
    url = engine.url
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    async with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            # Gunicorn workers may start concurrently. Serialize the one-time
            # upgrade in PostgreSQL so two processes cannot race to add the
            # same column or stamp the Alembic version table.
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext('seizu-report-store-schema-migrations'))")
            )
        await connection.run_sync(_upgrade, config)
