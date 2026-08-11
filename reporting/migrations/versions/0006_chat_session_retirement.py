"""Add the session-retirement claim and the index the reaper sweeps on.

``retiring_at`` is the reaper's claim (SBX-011): set by a conditional update, it
closes the session to every other writer until its checkpoint and sandbox are
gone. The index is what keeps the sweep's "which sessions are idle" query from
scanning the whole table -- it filters on ``origin`` and orders by
``updated_at``, so the two belong in one composite index in that order.
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_chat_session_retirement"
down_revision = "0005_spaces"
branch_labels = None
depends_on = None

_TABLE = "chat_sessions"
_COLUMN = sa.Column("retiring_at", sa.String(), nullable=True)
_INDEX = "ix_chat_sessions_origin_updated_at"


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _columns(table: str) -> set[str]:
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    current = _columns(_TABLE)
    if not current:
        # Fresh databases get the table, column and index from the baseline's
        # create_all; there is nothing here to add.
        return
    if _COLUMN.name not in current:
        op.add_column(_TABLE, _COLUMN)
    if _INDEX not in _indexes(_TABLE):
        op.create_index(_INDEX, _TABLE, ["origin", "updated_at"])


def downgrade() -> None:
    if _INDEX in _indexes(_TABLE):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _COLUMN.name in _columns(_TABLE):
        op.drop_column(_TABLE, _COLUMN.name)
