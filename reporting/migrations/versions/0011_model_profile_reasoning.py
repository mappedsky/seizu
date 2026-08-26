"""Persist session reasoning selection and profile locking."""

import sqlalchemy as sa
from alembic import op

revision = "0011_model_profile_reasoning"
down_revision = "0010_model_profiles"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "chat_sessions" in tables:
        columns = _columns("chat_sessions")
        if "model_reasoning_effort" not in columns:
            op.add_column("chat_sessions", sa.Column("model_reasoning_effort", sa.String(), nullable=True))
        if "model_profile_locked" not in columns:
            op.add_column(
                "chat_sessions",
                sa.Column("model_profile_locked", sa.Boolean(), nullable=False, server_default=sa.false()),
            )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "chat_sessions" not in tables:
        return
    columns = _columns("chat_sessions")
    if "model_profile_locked" in columns:
        op.drop_column("chat_sessions", "model_profile_locked")
    if "model_reasoning_effort" in columns:
        op.drop_column("chat_sessions", "model_reasoning_effort")
