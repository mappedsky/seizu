"""Bind a chat turn's idempotency key to the request it was minted for.

The key alone says "this is a repeat"; it does not say a repeat *of what*. A
retry carrying the same key resolves to the turn already admitted, and that
turn's body is what the producer executes -- so a repeat with a different
message, confirmation id, continuation flag or bypass setting would silently
change the work of a turn that may already be running. The fingerprint stored
here is compared on admission, and a mismatch is refused rather than resolved.

Nullable, and compared only when both sides have one: turns admitted before this
revision carry no fingerprint, and must keep resolving normally rather than
being refused.

Inspector-guarded, like every revision here: on a fresh database the baseline
runs ``SQLModel.metadata.create_all``, which has already added this column by
the time this revision runs (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_chat_turn_request_hash"
down_revision = "0007_chat_turn_events"
branch_labels = None
depends_on = None

_TURNS = "chat_turns"
_COLUMN = "request_hash"


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    if table not in inspector.get_table_names():
        return False
    return any(existing["name"] == column for existing in inspector.get_columns(table))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _TURNS in inspector.get_table_names() and not _has_column(inspector, _TURNS, _COLUMN):
        op.add_column(_TURNS, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if _has_column(inspector, _TURNS, _COLUMN):
        op.drop_column(_TURNS, _COLUMN)
