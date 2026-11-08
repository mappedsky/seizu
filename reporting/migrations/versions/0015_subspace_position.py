"""Add a display position to sub-spaces.

Inspector-guarded: on a fresh database the baseline revision's
``SQLModel.metadata.create_all`` already creates the column.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_subspace_position"
down_revision = "0014_chat_elicitations"
branch_labels = None
depends_on = None


def _subspace_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "subspaces" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("subspaces")}


def upgrade() -> None:
    columns = _subspace_columns()
    if columns and "position" not in columns:
        # Every existing row starts at 0, so list order falls back to creation
        # order -- what these spaces showed before positions existed.
        op.add_column(
            "subspaces",
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if "position" in _subspace_columns():
        op.drop_column("subspaces", "position")
