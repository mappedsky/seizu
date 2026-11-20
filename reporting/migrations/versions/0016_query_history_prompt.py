"""Record the plain-text question a generated console query came from.

Inspector-guarded: on a fresh database the baseline revision's
``SQLModel.metadata.create_all`` already creates the column.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_query_history_prompt"
down_revision = "0015_subspace_position"
branch_labels = None
depends_on = None


def _query_history_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "query_history" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("query_history")}


def upgrade() -> None:
    columns = _query_history_columns()
    if columns and "prompt" not in columns:
        # Nullable with no default: every existing entry was written by hand.
        op.add_column("query_history", sa.Column("prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    if "prompt" in _query_history_columns():
        op.drop_column("query_history", "prompt")
