"""Persist bounded external MCP elicitation recovery links."""

import sqlalchemy as sa
from alembic import op

revision = "0013_external_mcp_elicitation"
down_revision = "0012_external_mcp_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("external_mcp_connections")}
    if "elicitations_json" not in columns:
        op.add_column("external_mcp_connections", sa.Column("elicitations_json", sa.String(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("external_mcp_connections")}
    if "elicitations_json" in columns:
        op.drop_column("external_mcp_connections", "elicitations_json")
