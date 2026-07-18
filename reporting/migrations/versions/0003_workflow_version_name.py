"""Store workflow names in each SQL version snapshot."""

import sqlalchemy as sa
from alembic import op

revision = "0003_workflow_version_name"
down_revision = "0002_workflow_stages"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "name" not in _columns("scheduled_query_versions"):
        op.add_column(
            "scheduled_query_versions",
            sa.Column("name", sa.String(), nullable=True),
        )
    op.execute(
        sa.text(
            "UPDATE scheduled_query_versions "
            "SET name = ("
            "SELECT scheduled_queries.name FROM scheduled_queries "
            "WHERE scheduled_queries.scheduled_query_id = scheduled_query_versions.scheduled_query_id"
            ") WHERE name IS NULL"
        )
    )


def downgrade() -> None:
    if "name" in _columns("scheduled_query_versions"):
        op.drop_column("scheduled_query_versions", "name")
