"""Store Seizu's encrypted dynamic client registrations with gateway authorization servers.

Inspector-guarded: on a fresh database the baseline revision's
``SQLModel.metadata.create_all`` already creates the table (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_oauth_clients"
down_revision = "0017_delegated_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "oauth_clients" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "oauth_clients",
            sa.Column("issuer", sa.String(), primary_key=True),
            sa.Column("client_id", sa.String(), primary_key=True),
            sa.Column("redirect_uri", sa.String(), nullable=False),
            sa.Column("key_id", sa.String(), nullable=False),
            sa.Column("ciphertext", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
        )


def downgrade() -> None:
    if "oauth_clients" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("oauth_clients")
