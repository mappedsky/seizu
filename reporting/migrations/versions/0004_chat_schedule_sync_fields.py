"""Add Temporal Schedule reconciliation state to scheduled chats.

Scheduled chats moved from a polling worker to Temporal Schedules; these are
the same reconciliation fields scheduled queries already carry.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_chat_schedule_sync_fields"
down_revision = "0003_workflow_version_name"
branch_labels = None
depends_on = None

_COLUMNS = {
    "schedule_sync_status": sa.Column(
        "schedule_sync_status",
        sa.String(),
        nullable=False,
        server_default=sa.text("'pending'"),
    ),
    "schedule_sync_error": sa.Column("schedule_sync_error", sa.String(), nullable=True),
    "schedule_synced_at": sa.Column("schedule_synced_at", sa.String(), nullable=True),
}


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    current = _columns("scheduled_chats")
    if not current:
        # Fresh databases get the table from the baseline's create_all.
        return
    for name, column in _COLUMNS.items():
        if name not in current:
            op.add_column("scheduled_chats", column)


def downgrade() -> None:
    current = _columns("scheduled_chats")
    for name in _COLUMNS:
        if name in current:
            op.drop_column("scheduled_chats", name)
