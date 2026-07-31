"""Add spaces and sub-spaces, and report space-membership columns.

Every operation is guarded by an inspector check. That is load-bearing rather
than defensive: on a fresh database the baseline revision calls
``SQLModel.metadata.create_all``, which already creates both tables and both
``reports`` columns before this revision runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_spaces"
down_revision = "0004_chat_schedule_sync_fields"
branch_labels = None
depends_on = None

_REPORT_COLUMNS = {
    "space_id": sa.Column("space_id", sa.String(), nullable=True),
    "subspace_id": sa.Column("subspace_id", sa.String(), nullable=True),
}

_REPORT_INDEXES = {
    "ix_reports_space_id": ["space_id"],
    "ix_reports_subspace_id": ["subspace_id"],
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    existing = _tables()

    if "spaces" not in existing:
        op.create_table(
            "spaces",
            sa.Column("space_id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=False, server_default=""),
            sa.Column("overview_report_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
        )

    if "subspaces" not in existing:
        op.create_table(
            "subspaces",
            sa.Column("subspace_id", sa.String(), primary_key=True),
            sa.Column("space_id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
        )
    if "ix_subspaces_space_id" not in _indexes("subspaces"):
        op.create_index("ix_subspaces_space_id", "subspaces", ["space_id"])

    current = _columns("reports")
    if not current:
        # Table absent entirely — nothing to bring forward.
        return
    for name, column in _REPORT_COLUMNS.items():
        if name not in current:
            op.add_column("reports", column)
    report_indexes = _indexes("reports")
    for name, columns in _REPORT_INDEXES.items():
        if name not in report_indexes:
            op.create_index(name, "reports", columns)


def downgrade() -> None:
    report_indexes = _indexes("reports")
    for name in _REPORT_INDEXES:
        if name in report_indexes:
            op.drop_index(name, table_name="reports")
    current = _columns("reports")
    for name in _REPORT_COLUMNS:
        if name in current:
            op.drop_column("reports", name)

    existing = _tables()
    if "subspaces" in existing:
        if "ix_subspaces_space_id" in _indexes("subspaces"):
            op.drop_index("ix_subspaces_space_id", table_name="subspaces")
        op.drop_table("subspaces")
    if "spaces" in existing:
        op.drop_table("spaces")
