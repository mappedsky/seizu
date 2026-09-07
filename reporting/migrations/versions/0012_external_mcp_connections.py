"""Persist per-user external gateway connection observations."""

import sqlalchemy as sa
from alembic import op

revision = "0012_external_mcp_connections"
down_revision = "0011_model_profile_reasoning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "external_mcp_connections" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "external_mcp_connections",
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("proxy_name", sa.String(), primary_key=True),
            sa.Column("fingerprint", sa.String(), primary_key=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("observed_at", sa.String(), nullable=False),
            sa.Column("error_code", sa.String(), nullable=True),
        )


def downgrade() -> None:
    if "external_mcp_connections" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("external_mcp_connections")
