"""Add versioned, admin-managed chat model profiles."""

import sqlalchemy as sa
from alembic import op

revision = "0010_model_profiles"
down_revision = "0009_agent_plugins"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("SELECT pg_advisory_xact_lock(2750010)"))
    tables = _tables()
    inspector = sa.inspect(op.get_bind())
    if "chat_sessions" in tables:
        columns = {column["name"] for column in inspector.get_columns("chat_sessions")}
        if "model_profile_id" not in columns:
            op.add_column("chat_sessions", sa.Column("model_profile_id", sa.String(), nullable=True))
    if "scheduled_chats" in tables:
        columns = {column["name"] for column in inspector.get_columns("scheduled_chats")}
        if "model_profile_id" not in columns:
            op.add_column("scheduled_chats", sa.Column("model_profile_id", sa.String(), nullable=True))
            op.create_index("ix_scheduled_chats_model_profile_id", "scheduled_chats", ["model_profile_id"])
    if "scheduled_chat_versions" in tables:
        columns = {column["name"] for column in inspector.get_columns("scheduled_chat_versions")}
        if "model_profile_id" not in columns:
            op.add_column("scheduled_chat_versions", sa.Column("model_profile_id", sa.String(), nullable=True))
    if "model_profiles" not in tables:
        op.create_table(
            "model_profiles",
            sa.Column("profile_id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False, unique=True),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("current_version", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
        )
        op.create_index(
            "uq_model_profiles_default",
            "model_profiles",
            ["is_default"],
            unique=True,
            postgresql_where=sa.text("is_default = true"),
            sqlite_where=sa.text("is_default = 1"),
        )
    if "model_profile_versions" not in tables:
        op.create_table(
            "model_profile_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("comment", sa.String(), nullable=True),
            sa.UniqueConstraint("profile_id", "version"),
        )
        op.create_index("ix_model_profile_versions_profile_id", "model_profile_versions", ["profile_id"])


def downgrade() -> None:
    tables = _tables()
    inspector = sa.inspect(op.get_bind())
    if "scheduled_chat_versions" in tables:
        columns = {column["name"] for column in inspector.get_columns("scheduled_chat_versions")}
        if "model_profile_id" in columns:
            op.drop_column("scheduled_chat_versions", "model_profile_id")
    if "scheduled_chats" in tables:
        columns = {column["name"] for column in inspector.get_columns("scheduled_chats")}
        if "model_profile_id" in columns:
            op.drop_index("ix_scheduled_chats_model_profile_id", table_name="scheduled_chats")
            op.drop_column("scheduled_chats", "model_profile_id")
    if "model_profile_versions" in tables:
        op.drop_table("model_profile_versions")
    if "model_profiles" in tables:
        op.drop_table("model_profiles")
    if "chat_sessions" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_sessions")}
        if "model_profile_id" in columns:
            op.drop_column("chat_sessions", "model_profile_id")
