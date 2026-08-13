"""Persist the immutable command admitted for each chat turn.

The command supersedes the request fingerprint: retries resolve directly to
the original work instead of comparing a second copy of the request. Existing
turn rows cannot be reconstructed because they contain only a fingerprint, so
this migration clears the ephemeral turn/event log before changing the shape.
A restarted producer cannot still be running after the application is down for
the migration, and clients may safely retry with their idempotency key.

Inspector-guarded because the baseline creates the current SQLModel schema on a
fresh database before Alembic visits this revision (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_chat_turn_command"
down_revision = "0008_chat_turn_request_hash"
branch_labels = None
depends_on = None

_TURNS = "chat_turns"
_EVENTS = "chat_turn_events"
_COMMAND = "command"
_IDEMPOTENCY_KEY = "idempotency_key"
_REQUEST_HASH = "request_hash"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if _TURNS not in tables:
        return

    columns = _columns(_TURNS)
    needs_command = _COMMAND not in columns
    needs_required_key = bool(columns.get(_IDEMPOTENCY_KEY, {}).get("nullable", False))

    if needs_command or needs_required_key:
        # Turn logs are explicitly ephemeral. Old rows have no executable
        # command to backfill, and retaining one would make the current model
        # fail while deserializing it.
        if _EVENTS in tables:
            op.execute(sa.text(f"DELETE FROM {_EVENTS}"))
        op.execute(sa.text(f"DELETE FROM {_TURNS}"))

    with op.batch_alter_table(_TURNS) as batch_op:
        if needs_command:
            batch_op.add_column(sa.Column(_COMMAND, sa.JSON(), nullable=False))
        if needs_required_key:
            batch_op.alter_column(_IDEMPOTENCY_KEY, existing_type=sa.String(), nullable=False)
        if _REQUEST_HASH in columns:
            batch_op.drop_column(_REQUEST_HASH)


def downgrade() -> None:
    if _TURNS not in _tables():
        return

    columns = _columns(_TURNS)
    with op.batch_alter_table(_TURNS) as batch_op:
        if _REQUEST_HASH not in columns:
            batch_op.add_column(sa.Column(_REQUEST_HASH, sa.String(), nullable=True))
        if _IDEMPOTENCY_KEY in columns and not columns[_IDEMPOTENCY_KEY].get("nullable", True):
            batch_op.alter_column(_IDEMPOTENCY_KEY, existing_type=sa.String(), nullable=True)
        if _COMMAND in columns:
            batch_op.drop_column(_COMMAND)
