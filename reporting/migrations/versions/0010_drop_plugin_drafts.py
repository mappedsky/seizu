"""Drop the server-side plugin draft tables.

Plugin edits are staged in the client and submitted as one complete package, so
there is no server-side draft to hold. Inspector-guarded because the baseline
creates current SQLModel metadata on a fresh database (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_drop_plugin_drafts"
down_revision = "0009_agent_plugins"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    for table in ("plugin_draft_files", "plugin_drafts"):
        if table in tables:
            op.drop_table(table)


def downgrade() -> None:
    tables = _tables()
    if "plugin_drafts" not in tables:
        op.create_table(
            "plugin_drafts",
            sa.Column("plugin_id", sa.String(), primary_key=True),
            sa.Column("base_revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=False),
        )
    if "plugin_draft_files" not in tables:
        op.create_table(
            "plugin_draft_files",
            sa.Column("plugin_id", sa.String(), primary_key=True),
            sa.Column("path", sa.String(), primary_key=True),
            sa.Column("blob_sha256", sa.String(), nullable=False),
            sa.Column("media_type", sa.String(), nullable=False),
            sa.Column("executable", sa.Boolean(), nullable=False),
        )
        op.create_index("ix_plugin_draft_files_blob_sha256", "plugin_draft_files", ["blob_sha256"])
