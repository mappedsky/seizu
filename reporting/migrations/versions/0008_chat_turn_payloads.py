"""Add out-of-band storage for oversized chat turn payloads.

A plan step that runs as its own Temporal activity has to hand its result back
to the turn that scheduled it, and that result carries every tool call the step
made together with what each returned -- bounded per call by
``CHAT_TOOL_RESULT_MAX_BYTES``, so megabytes for a step that made dozens.
Returning that through Temporal copies it into workflow history twice. The
reference travels through history instead and the body lives here, keyed within
the turn so the existing expiry sweep collects it (AGT-018).

Every operation is inspector-guarded: on a fresh database the baseline revision
runs ``SQLModel.metadata.create_all``, which has already created this table by
the time this revision runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_chat_turn_payloads"
down_revision = "0007_chat_turn_events"
branch_labels = None
depends_on = None

_PAYLOADS = "chat_turn_payloads"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if _PAYLOADS not in _tables():
        op.create_table(
            _PAYLOADS,
            # Composite key rather than a surrogate id: a payload is named by the
            # turn it belongs to and an id the producer chose, and re-writing the
            # same name is the same payload.
            sa.Column("turn_id", sa.String(), primary_key=True),
            sa.Column("payload_id", sa.String(), primary_key=True),
            # Text for the same reason as the event log: opaque to the store,
            # never queried into.
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
        )


def downgrade() -> None:
    if _PAYLOADS in _tables():
        op.drop_table(_PAYLOADS)
