"""Store each user's encrypted delegated grants for external MCP gateways.

Inspector-guarded: on a fresh database the baseline revision's
``SQLModel.metadata.create_all`` already creates the table (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_delegated_tokens"
down_revision = "0016_query_history_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "delegated_tokens" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "delegated_tokens",
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("proxy_name", sa.String(), primary_key=True),
            sa.Column("resource", sa.String(), primary_key=True),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("revision", sa.String(), nullable=False),
            sa.Column("refresh_claimant", sa.String(), nullable=True),
            sa.Column("refresh_claim_expires_at", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
        )


def downgrade() -> None:
    if "delegated_tokens" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("delegated_tokens")
