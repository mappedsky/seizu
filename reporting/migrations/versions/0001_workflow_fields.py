"""Baseline the report store and add configurable-workflow fields."""

import sqlalchemy as sa
from alembic import op

revision = "0001_workflow_fields"
down_revision = None
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    current = _columns("scheduled_queries")
    additions = {
        "inputs": sa.Column("inputs", sa.JSON(), nullable=True),
        "activities": sa.Column("activities", sa.JSON(), nullable=True),
        "schedule_sync_status": sa.Column(
            "schedule_sync_status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
        "schedule_sync_error": sa.Column("schedule_sync_error", sa.String(), nullable=True),
        "schedule_synced_at": sa.Column("schedule_synced_at", sa.String(), nullable=True),
    }
    for name, column in additions.items():
        if current and name not in current:
            op.add_column("scheduled_queries", column)

    versions = _columns("scheduled_query_versions")
    for name in ("inputs", "activities"):
        if versions and name not in versions:
            op.add_column(
                "scheduled_query_versions",
                sa.Column(name, sa.JSON(), nullable=True),
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
