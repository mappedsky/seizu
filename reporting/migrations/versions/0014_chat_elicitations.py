"""Persist owner-scoped chat elicitation continuations."""

import sqlalchemy as sa
from alembic import op

revision = "0014_chat_elicitations"
down_revision = "0013_external_mcp_elicitation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from reporting.services.report_store.sql import ChatElicitationRecord

    if "chat_elicitations" not in sa.inspect(op.get_bind()).get_table_names():
        ChatElicitationRecord.__table__.create(op.get_bind())


def downgrade() -> None:
    if "chat_elicitations" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("chat_elicitations")
