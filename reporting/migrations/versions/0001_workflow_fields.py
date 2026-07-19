"""Baseline the report store and add configurable-workflow fields."""

import sqlalchemy as sa
from alembic import op
from sqlmodel import SQLModel

revision = "0001_workflow_fields"
down_revision = None
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_missing(table: str, additions: dict[str, sa.Column]) -> None:
    current = _columns(table)
    for name, column in additions.items():
        if current and name not in current:
            op.add_column(table, column)


def upgrade() -> None:
    bind = op.get_bind()
    application_tables = set(sa.inspect(bind).get_table_names()) - {"alembic_version"}
    if not application_tables:
        # This is the one-time baseline for a fresh report-store database.
        # Subsequent schema changes must be explicit Alembic revisions.
        SQLModel.metadata.create_all(bind=bind)
        return

    # Bring pre-Alembic databases to the baseline before adding workflow fields.
    # ``create_all`` is deliberately inside the baseline migration: it creates
    # tables absent from a partially initialized legacy database but never
    # mutates tables that already exist. Alembic remains the sole startup
    # schema owner; later revisions must continue to use explicit operations.
    SQLModel.metadata.create_all(bind=bind)
    _add_missing(
        "users",
        {
            "preferred_username": sa.Column("preferred_username", sa.String(), nullable=True),
            "role": sa.Column("role", sa.String(), nullable=True),
        },
    )
    if bind.dialect.name == "postgresql" and "email" in _columns("users"):
        op.alter_column("users", "email", existing_type=sa.String(), nullable=True)
    _add_missing(
        "scheduled_chats",
        {
            "schedule": sa.Column("schedule", sa.JSON(), nullable=True),
            "current_version": sa.Column("current_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
            "updated_by": sa.Column("updated_by", sa.String(), nullable=True),
            "run_requested_at": sa.Column("run_requested_at", sa.String(), nullable=True),
        },
    )
    _add_missing(
        "chat_sessions",
        {
            "origin": sa.Column("origin", sa.String(), nullable=False, server_default=sa.text("'interactive'")),
            "scheduled_chat_id": sa.Column("scheduled_chat_id", sa.String(), nullable=True),
            "run_status": sa.Column("run_status", sa.String(), nullable=True),
            "run_errors": sa.Column("run_errors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        },
    )
    _add_missing(
        "action_confirmations",
        {
            "batch_id": sa.Column("batch_id", sa.String(), nullable=True),
            "arguments_hash": sa.Column("arguments_hash", sa.String(), nullable=False, server_default=sa.text("''")),
        },
    )
    _add_missing(
        "scheduled_queries",
        {
            "schedule": sa.Column("schedule", sa.JSON(), nullable=True),
            "run_requested_at": sa.Column("run_requested_at", sa.String(), nullable=True),
            "inputs": sa.Column("inputs", sa.JSON(), nullable=True),
            "activities": sa.Column("activities", sa.JSON(), nullable=True),
            "schedule_sync_status": sa.Column(
                "schedule_sync_status",
                sa.String(),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            "schedule_sync_error": sa.Column("schedule_sync_error", sa.String(), nullable=True),
            "schedule_synced_at": sa.Column("schedule_synced_at", sa.String(), nullable=True),
        },
    )
    _add_missing(
        "scheduled_query_versions",
        {
            "schedule": sa.Column("schedule", sa.JSON(), nullable=True),
            "inputs": sa.Column("inputs", sa.JSON(), nullable=True),
            "activities": sa.Column("activities", sa.JSON(), nullable=True),
        },
    )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_action_conf_user_status_list"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_action_conf_pending_dedup "
            "ON action_confirmations "
            "(user_id, source, session_key, tool_name, action, resource_type, resource_id, arguments_hash) "
            "WHERE status = 'pending'"
        )
    )


def downgrade() -> None:
    for table, names in (
        (
            "scheduled_queries",
            (
                "schedule_synced_at",
                "schedule_sync_error",
                "schedule_sync_status",
                "activities",
                "inputs",
            ),
        ),
        ("scheduled_query_versions", ("activities", "inputs")),
    ):
        current = _columns(table)
        for name in names:
            if name in current:
                op.drop_column(table, name)
