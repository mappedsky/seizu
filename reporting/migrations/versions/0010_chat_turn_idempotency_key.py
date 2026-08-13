"""Rename the published chat-turn client token to its current name.

Early versions of revision 0007 created ``client_token`` and its unique index.
That revision was later edited in place to call the same value
``idempotency_key``, so databases which had already applied 0007 retained the
old physical name. Preserve both upgrade paths with an inspector-guarded rename
instead of relying on the current contents of an already-published revision
(STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_chat_turn_idempotency_key"
down_revision = "0009_chat_turn_command"
branch_labels = None
depends_on = None

_TURNS = "chat_turns"
_EVENTS = "chat_turn_events"
_LEGACY_COLUMN = "client_token"
_CURRENT_COLUMN = "idempotency_key"
_LEGACY_INDEX = "uq_chat_turns_client_token"
_CURRENT_INDEX = "uq_chat_turns_idempotency_key"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns() -> dict[str, dict[str, object]]:
    inspector = sa.inspect(op.get_bind())
    if _TURNS not in inspector.get_table_names():
        return {}
    return {column["name"]: column for column in inspector.get_columns(_TURNS)}


def _indexes() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if _TURNS not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(_TURNS)}


def _unique_constraints() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if _TURNS not in inspector.get_table_names():
        return set()
    return {
        constraint["name"] for constraint in inspector.get_unique_constraints(_TURNS) if constraint["name"] is not None
    }


def _drop_uniqueness(name: str) -> None:
    if name in _unique_constraints():
        op.drop_constraint(name, _TURNS, type_="unique")
    elif name in _indexes():
        op.drop_index(name, table_name=_TURNS)


def _has_uniqueness(name: str) -> bool:
    return name in _unique_constraints() or name in _indexes()


def _clear_ephemeral_turns(tables: set[str]) -> None:
    if _EVENTS in tables:
        op.execute(sa.text(f"DELETE FROM {_EVENTS}"))
    op.execute(sa.text(f"DELETE FROM {_TURNS}"))


def upgrade() -> None:
    tables = _tables()
    if _TURNS not in tables:
        return

    columns = _columns()
    has_legacy = _LEGACY_COLUMN in columns
    has_current = _CURRENT_COLUMN in columns
    current_is_nullable = bool(columns.get(_CURRENT_COLUMN, {}).get("nullable", False))

    if (not has_legacy and not has_current) or current_is_nullable:
        # This is a compatibility path for an unexpected intermediate schema.
        # Turn/event rows are ephemeral and cannot be assigned a safe key.
        _clear_ephemeral_turns(tables)

    with op.batch_alter_table(_TURNS) as batch_op:
        if has_legacy and not has_current:
            batch_op.alter_column(
                _LEGACY_COLUMN,
                new_column_name=_CURRENT_COLUMN,
                existing_type=sa.String(),
                nullable=False,
            )
        elif not has_current:
            batch_op.add_column(sa.Column(_CURRENT_COLUMN, sa.String(), nullable=False))
        elif current_is_nullable:
            batch_op.alter_column(_CURRENT_COLUMN, existing_type=sa.String(), nullable=False)
        if has_legacy and has_current:
            batch_op.drop_column(_LEGACY_COLUMN)

    _drop_uniqueness(_LEGACY_INDEX)
    if not _has_uniqueness(_CURRENT_INDEX):
        op.create_index(
            _CURRENT_INDEX,
            _TURNS,
            ["user_id", "thread_id", _CURRENT_COLUMN],
            unique=True,
        )


def downgrade() -> None:
    if _TURNS not in _tables():
        return

    columns = _columns()
    has_legacy = _LEGACY_COLUMN in columns
    has_current = _CURRENT_COLUMN in columns
    with op.batch_alter_table(_TURNS) as batch_op:
        if has_current and not has_legacy:
            batch_op.alter_column(
                _CURRENT_COLUMN,
                new_column_name=_LEGACY_COLUMN,
                existing_type=sa.String(),
                nullable=True,
            )

    _drop_uniqueness(_CURRENT_INDEX)
    if not _has_uniqueness(_LEGACY_INDEX):
        op.create_index(
            _LEGACY_INDEX,
            _TURNS,
            ["user_id", "thread_id", _LEGACY_COLUMN],
            unique=True,
        )
