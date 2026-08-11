"""Add the chat turn event log: turn headers and their append-only batches.

A turn's stream parts are written by whichever process runs the turn and read
back by whichever process is serving the client's SSE connection, so they need
a store both can reach. The records are ephemeral -- they exist only for as long
as a dropped connection might come back -- which is why they carry ``expires_at``
and are swept rather than kept.

Every operation is inspector-guarded. That is load-bearing rather than
defensive: on a fresh database the baseline revision runs
``SQLModel.metadata.create_all``, which has already created both tables and both
indexes by the time this revision runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_chat_turn_events"
down_revision = "0006_chat_session_retirement"
branch_labels = None
depends_on = None

_TURNS = "chat_turns"
_EVENTS = "chat_turn_events"
_TURN_INDEXES = {
    "ix_chat_turns_thread_status": ["user_id", "thread_id", "status"],
    "ix_chat_turns_expires_at": ["expires_at"],
}


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _indexes(table: str) -> set[str]:
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    existing = _tables()
    if _TURNS not in existing:
        op.create_table(
            _TURNS,
            sa.Column("turn_id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("thread_id", sa.String(), nullable=False),
            sa.Column("message_id", sa.String(), nullable=False),
            sa.Column("text_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("last_seq", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("expires_at", sa.String(), nullable=False),
        )
    turn_indexes = _indexes(_TURNS)
    for name, columns in _TURN_INDEXES.items():
        if name not in turn_indexes:
            op.create_index(name, _TURNS, columns)

    if _EVENTS not in existing:
        op.create_table(
            _EVENTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("turn_id", sa.String(), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            # Text, not JSON: a replay has to reproduce the exact bytes the live
            # stream sent, and a JSON column renormalises them.
            sa.Column("parts_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.UniqueConstraint("turn_id", "seq", name="uq_chat_turn_events_turn_seq"),
        )


def downgrade() -> None:
    existing = _tables()
    if _EVENTS in existing:
        op.drop_table(_EVENTS)
    if _TURNS in existing:
        turn_indexes = _indexes(_TURNS)
        for name in _TURN_INDEXES:
            if name in turn_indexes:
                op.drop_index(name, table_name=_TURNS)
        op.drop_table(_TURNS)
