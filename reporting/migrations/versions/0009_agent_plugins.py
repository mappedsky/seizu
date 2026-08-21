"""Add content-addressed Agent Plugins packages and drafts.

Every operation is inspector-guarded because the baseline creates the current
SQLModel metadata on a fresh database (STO-004).
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_agent_plugins"
down_revision = "0008_chat_turn_payloads"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "plugins" not in tables:
        op.create_table(
            "plugins",
            sa.Column("plugin_id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("package_version", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("diagnostics", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("current_revision", sa.Integer(), nullable=False),
            sa.Column("package_digest", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("updated_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("updated_by", sa.String(), nullable=True),
        )
    if "plugin_versions" not in tables:
        op.create_table(
            "plugin_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("plugin_id", sa.String(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("diagnostics", sa.JSON(), nullable=False),
            sa.Column("package_digest", sa.String(), nullable=False),
            sa.Column("created_at", sa.String(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=False),
            sa.Column("comment", sa.String(), nullable=True),
            sa.UniqueConstraint("plugin_id", "revision"),
        )
        op.create_index("ix_plugin_versions_plugin_id", "plugin_versions", ["plugin_id"])
    if "plugin_blobs" not in tables:
        op.create_table(
            "plugin_blobs",
            sa.Column("sha256", sa.String(), primary_key=True),
            sa.Column("content", sa.LargeBinary(), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
        )
    if "plugin_files" not in tables:
        op.create_table(
            "plugin_files",
            sa.Column("plugin_id", sa.String(), primary_key=True),
            sa.Column("revision", sa.Integer(), primary_key=True),
            sa.Column("path", sa.String(), primary_key=True),
            sa.Column("blob_sha256", sa.String(), nullable=False),
            sa.Column("media_type", sa.String(), nullable=False),
            sa.Column("executable", sa.Boolean(), nullable=False),
        )
        op.create_index("ix_plugin_files_blob_sha256", "plugin_files", ["blob_sha256"])
    if "plugin_skills" not in tables:
        op.create_table(
            "plugin_skills",
            sa.Column("plugin_id", sa.String(), primary_key=True),
            sa.Column("skill_id", sa.String(), primary_key=True),
            sa.Column("portable_name", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.String(), nullable=False),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("parameters", sa.JSON(), nullable=False),
            sa.Column("triggers", sa.JSON(), nullable=False),
            sa.Column("allowed_tools", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("source_path", sa.String(), nullable=False),
            sa.Column("aliases", sa.JSON(), nullable=False),
            sa.Column("mcp_servers", sa.JSON(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("package_digest", sa.String(), nullable=False),
            sa.Column("has_scripts", sa.Boolean(), nullable=False),
        )
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


def downgrade() -> None:
    tables = _tables()
    for table in (
        "plugin_draft_files",
        "plugin_drafts",
        "plugin_skills",
        "plugin_files",
        "plugin_versions",
        "plugins",
        "plugin_blobs",
    ):
        if table in tables:
            op.drop_table(table)
