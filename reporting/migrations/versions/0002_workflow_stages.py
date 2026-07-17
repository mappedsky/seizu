"""Add staged configurable-workflow definitions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_workflow_stages"
down_revision = "0001_workflow_fields"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    for table in ("scheduled_queries", "scheduled_query_versions"):
        columns = _columns(table)
        if columns and "stages" not in columns:
            op.add_column(table, sa.Column("stages", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("scheduled_query_versions", "scheduled_queries"):
        if "stages" in _columns(table):
            op.drop_column(table, "stages")
