import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from snowflake import SnowflakeGenerator
from sqlalchemy import (
    JSON,
    Column,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    and_,
    delete,
    func,
    null,
    nullslast,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlmodel import Field, SQLModel, col, select

from reporting import settings
from reporting.schema.chat import (
    ChatSessionItem,
    ChatTurnAdmission,
    ChatTurnCommand,
    ChatTurnEventBatch,
    ChatTurnEventPage,
    ChatTurnItem,
    IdleChatSession,
    ScheduledChatItem,
    ScheduledChatVersion,
)
from reporting.schema.confirmations import ActionConfirmation, ConfirmationDecision, ConfirmationSource
from reporting.schema.mcp_config import (
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
    ToolParamDef,
    ToolsetListItem,
    ToolsetVersion,
    ToolVersion,
)
from reporting.schema.model_profiles import ModelProfileConfig, ModelProfileItem, ModelProfileVersion
from reporting.schema.plugins import PluginFile, PluginFileInfo, PluginListItem, PluginSkillItem, PluginVersion
from reporting.schema.rbac import RoleItem, RoleVersion
from reporting.schema.report_config import (
    QueryHistoryItem,
    ReportAccess,
    ReportListItem,
    ReportVersion,
    ScheduledQueryItem,
    ScheduledQueryVersion,
    User,
)
from reporting.schema.space_config import (
    FILING_PRIVATE_REPORT_DETAIL,
    PRIVATISING_SPACE_MEMBER_DETAIL,
    SpaceConflictError,
    SpaceDeleteResult,
    SpaceListItem,
    SubspaceItem,
)
from reporting.services.report_store.base import (
    PluginRevisionConflict,
    ReportStore,
    chat_turn_lease_expiry,
    initial_report_config,
    require_public_space_member,
    resolve_chat_turn_for_key,
    validate_chat_turn_batch,
    validate_chat_turn_seq,
)
from reporting.utils.sql import build_database_url

logger = logging.getLogger(__name__)


_engine: AsyncEngine | None = None
_snowflake_gen: SnowflakeGenerator | None = None

# How many times an event-log append re-reads the highest sequence after losing
# the race for it. The turn row is locked first, so a loss needs two producers to
# get past that lock concurrently -- a backend without row locking, or a lock
# released by a rollback elsewhere. A handful of retries covers that without
# turning a genuinely stuck allocation into an unbounded loop.
_CHAT_TURN_SEQ_ALLOCATION_ATTEMPTS = 8


# ---------------------------------------------------------------------------
# SQLModel table definitions
# ---------------------------------------------------------------------------


class ReportVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "report_versions"
    __table_args__ = (UniqueConstraint("report_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    report_id: str = Field(index=True)
    version: int
    config: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    created_at: str
    created_by: str
    comment: str | None = None


class DashboardPointerRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "dashboard_pointer"
    id: int = Field(default=1, primary_key=True)
    report_id: str
    updated_at: str


class ReportRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "reports"
    report_id: str = Field(primary_key=True)
    name: str
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str
    access: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    pinned: bool = False
    space_id: str | None = Field(default=None, index=True)
    subspace_id: str | None = Field(default=None, index=True)


class SpaceRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "spaces"
    space_id: str = Field(primary_key=True)
    name: str
    description: str = ""
    overview_report_id: str | None = None
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class SubspaceRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "subspaces"
    subspace_id: str = Field(primary_key=True)
    # Plain index rather than a foreign key, matching ToolRecord.toolset_id —
    # this module declares no FKs and cascades by hand.
    space_id: str = Field(index=True)
    name: str
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class UserRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("iss", "sub"),)
    user_id: str = Field(primary_key=True)
    sub: str
    iss: str
    email: str | None = None
    display_name: str | None = None
    preferred_username: str | None = None
    created_at: str
    last_login: str
    archived_at: str | None = None
    role: str | None = None


class ScheduledQueryRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_queries"
    scheduled_query_id: str = Field(primary_key=True)
    name: str
    cypher: str
    params: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    frequency: int | None = None
    schedule: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    watch_scans: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    stages: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    inputs: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    activities: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    last_scheduled_at: str | None = None
    run_requested_at: str | None = None
    schedule_sync_status: str = "pending"
    schedule_sync_error: str | None = None
    schedule_synced_at: str | None = None


class ScheduledQueryVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_query_versions"
    __table_args__ = (UniqueConstraint("scheduled_query_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    scheduled_query_id: str = Field(index=True)
    version: int
    name: str | None = None
    cypher: str
    params: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    frequency: int | None = None
    schedule: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    watch_scans: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    actions: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    stages: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    inputs: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    activities: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: str
    created_by: str
    comment: str | None = None


class ToolsetRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "toolsets"
    toolset_id: str = Field(primary_key=True)
    name: str
    description: str = ""
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class ToolsetVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "toolset_versions"
    __table_args__ = (UniqueConstraint("toolset_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    toolset_id: str = Field(index=True)
    version: int
    name: str
    description: str = ""
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class ToolRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "tools"
    tool_id: str = Field(primary_key=True)
    toolset_id: str = Field(index=True)
    name: str
    description: str = ""
    cypher: str
    parameters: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class ToolVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "tool_versions"
    __table_args__ = (UniqueConstraint("tool_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    tool_id: str = Field(index=True)
    toolset_id: str
    version: int
    name: str
    description: str = ""
    cypher: str
    parameters: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class SkillsetRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "skillsets"
    skillset_id: str = Field(primary_key=True)
    name: str
    description: str = ""
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class SkillsetVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "skillset_versions"
    __table_args__ = (UniqueConstraint("skillset_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    skillset_id: str = Field(index=True)
    version: int
    name: str
    description: str = ""
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class SkillRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "skills"
    skill_id: str = Field(primary_key=True)
    skillset_id: str = Field(index=True)
    name: str
    description: str = ""
    template: str
    parameters: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    triggers: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    tools_required: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class SkillVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("skill_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    skill_id: str = Field(index=True)
    skillset_id: str
    version: int
    name: str
    description: str = ""
    template: str
    parameters: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    triggers: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    tools_required: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class PluginRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "plugins"
    plugin_id: str = Field(primary_key=True)
    name: str
    package_version: str | None = None
    description: str = ""
    manifest: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    diagnostics: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    current_revision: int = 0
    package_digest: str = ""
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class PluginVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "plugin_versions"
    __table_args__ = (UniqueConstraint("plugin_id", "revision"),)
    id: int | None = Field(default=None, primary_key=True)
    plugin_id: str = Field(index=True)
    revision: int
    manifest: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    diagnostics: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    package_digest: str
    created_at: str
    created_by: str
    comment: str | None = None


class PluginBlobRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "plugin_blobs"
    sha256: str = Field(primary_key=True)
    content: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    size: int


class PluginFileRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "plugin_files"
    plugin_id: str = Field(primary_key=True)
    revision: int = Field(primary_key=True)
    path: str = Field(primary_key=True)
    blob_sha256: str = Field(index=True)
    media_type: str
    executable: bool = False


class PluginSkillRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "plugin_skills"
    plugin_id: str = Field(primary_key=True)
    skill_id: str = Field(primary_key=True)
    portable_name: str
    title: str
    description: str = ""
    template: str
    parameters: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    triggers: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    allowed_tools: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    source_path: str
    aliases: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    mcp_servers: dict[str, dict[str, Any]] = Field(default={}, sa_column=Column(JSON, nullable=False))
    revision: int
    package_digest: str
    has_scripts: bool = False


class QueryHistoryRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "query_history"
    id: int | None = Field(default=None, primary_key=True)
    history_id: str = Field(unique=True)
    user_id: str = Field(index=True)
    query: str
    executed_at: str


class ChatSessionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "chat_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id"),
        # The reaper's sweep filters on origin and orders by updated_at across
        # every user, which is a full scan without this. Same order as the
        # query; migration 0006 adds it to existing databases.
        Index("ix_chat_sessions_origin_updated_at", "origin", "updated_at"),
    )
    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    thread_id: str
    title: str = ""
    created_at: str
    updated_at: str
    origin: str = "interactive"
    scheduled_chat_id: str | None = Field(default=None, index=True)
    run_status: str | None = None
    run_errors: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    model_profile_id: str | None = None
    # Set by the reaper's claim (SBX-011); a claimed session is closed to every
    # other writer until its checkpoint and sandbox are gone.
    retiring_at: str | None = None


class ChatTurnRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "chat_turns"
    __table_args__ = (
        # get_active_chat_turn, keyed the way it is queried.
        Index("ix_chat_turns_thread_status", "user_id", "thread_id", "status"),
        # The expiry sweep spans every user, which is a full scan without this.
        Index("ix_chat_turns_expires_at", "expires_at"),
        # One running turn per thread, enforced by the *database*. A read
        # followed by an insert does not do this: under read-committed two
        # requests can both see no running turn and both commit, leaving two
        # producers interleaving state on one LangGraph thread. Partial so
        # finished turns, of which a thread has many, do not collide.
        # One turn per idempotency key: a repeat of an admission request
        # resolves to the immutable command it already admitted.
        UniqueConstraint("user_id", "thread_id", "idempotency_key", name="uq_chat_turns_idempotency_key"),
        Index(
            "uq_chat_turns_one_running",
            "user_id",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
    )
    turn_id: str = Field(primary_key=True)
    user_id: str
    thread_id: str
    message_id: str
    text_id: str
    idempotency_key: str
    command: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = "running"
    # None until the turn finishes; see ChatTurnItem for why a reader needs it.
    last_seq: int | None = None
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    expires_at: str


class ChatTurnEventRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "chat_turn_events"
    # (turn_id, seq) is both the reader's index and the append's idempotency
    # key, so turn_id needs no index of its own.
    __table_args__ = (UniqueConstraint("turn_id", "seq", name="uq_chat_turn_events_turn_seq"),)
    id: int | None = Field(default=None, primary_key=True)
    turn_id: str
    seq: int
    # Text rather than JSON: the value has to come back byte-identical to what
    # the live stream sent, and a JSON column round-trips through Python and
    # renormalises it. Nothing ever queries into it.
    parts_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: str


class ChatTurnPayloadRecord(SQLModel, table=True):  # type: ignore
    """A turn payload too large to travel through Temporal history (AGT-018).

    Keyed within the turn so it is collected by the same sweep, and holds opaque
    text for the same reason as the event log: nothing queries into it.
    """

    __tablename__ = "chat_turn_payloads"
    turn_id: str = Field(primary_key=True)
    payload_id: str = Field(primary_key=True)
    body: str = Field(sa_column=Column(Text, nullable=False))
    created_at: str


class ScheduledChatRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_chats"
    scheduled_chat_id: str = Field(primary_key=True)
    name: str
    prompt: str
    model_profile_id: str | None = Field(default=None, index=True)
    schedule: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    watch_scans: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str = Field(index=True)
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    last_scheduled_at: str | None = None
    run_requested_at: str | None = None
    schedule_sync_status: str = "pending"
    schedule_sync_error: str | None = None
    schedule_synced_at: str | None = None


class ScheduledChatVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_chat_versions"
    __table_args__ = (UniqueConstraint("scheduled_chat_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    scheduled_chat_id: str = Field(index=True)
    version: int
    name: str
    prompt: str
    model_profile_id: str | None = None
    schedule: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    watch_scans: list[dict[str, Any]] = Field(default=[], sa_column=Column(JSON, nullable=False))
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class ActionConfirmationRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "action_confirmations"
    __table_args__ = (
        # Covers find_action_confirmation_grant (all 5 matching fields + status filter).
        Index(
            "ix_action_conf_grant_lookup",
            "user_id",
            "source",
            "session_key",
            "tool_name",
            "action",
            "resource_type",
            "resource_id",
            "arguments_hash",
            "status",
        ),
        # Covers list_action_confirmations (session + status filter).
        Index(
            "ix_action_conf_session_list",
            "user_id",
            "source",
            "session_key",
            "status",
        ),
        # Prevents duplicate pending confirmations from concurrent ensure_confirmation
        # calls with identical arguments (the dedup sentinel for SQL backends).
        Index(
            "ix_action_conf_pending_dedup",
            "user_id",
            "source",
            "session_key",
            "tool_name",
            "action",
            "resource_type",
            "resource_id",
            "arguments_hash",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )
    confirmation_id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    source: str = Field(index=True)
    session_key: str = Field(index=True)
    tool_name: str
    action: str
    resource_type: str
    resource_id: str
    arguments: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    arguments_hash: str = Field(default="", index=True)
    status: str = Field(index=True)
    batch_id: str | None = Field(default=None, index=True)
    created_at: str
    expires_at: str = Field(index=True)
    decided_at: str | None = None
    decided_by: str | None = None


class RoleRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "roles"
    role_id: str = Field(primary_key=True)
    name: str = Field(unique=True)
    description: str = ""
    permissions: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class RoleVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "role_versions"
    __table_args__ = (UniqueConstraint("role_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    role_id: str = Field(index=True)
    version: int
    name: str
    description: str = ""
    permissions: list[str] = Field(default=[], sa_column=Column(JSON, nullable=False))
    created_at: str
    created_by: str
    comment: str | None = None


class ModelProfileRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "model_profiles"
    __table_args__ = (
        Index(
            "uq_model_profiles_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default = true"),
            sqlite_where=text("is_default = 1"),
        ),
    )
    profile_id: str = Field(primary_key=True)
    name: str = Field(unique=True)
    description: str = ""
    enabled: bool = True
    is_default: bool = False
    config: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    current_version: int = 1
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class ModelProfileVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "model_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    profile_id: str = Field(index=True)
    version: int
    name: str
    description: str = ""
    enabled: bool
    is_default: bool
    config: dict[str, Any] = Field(default={}, sa_column=Column(JSON, nullable=False))
    created_at: str
    created_by: str
    comment: str | None = None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _get_snowflake_gen() -> SnowflakeGenerator:
    global _snowflake_gen
    if _snowflake_gen is None:
        _snowflake_gen = SnowflakeGenerator(settings.SNOWFLAKE_MACHINE_ID)
    return _snowflake_gen


def generate_report_id() -> str:
    return str(next(_get_snowflake_gen()))


async def _locked_report(session: AsyncSession, report_id: str) -> ReportRecord | None:
    """Fetch a report for update, holding its row until the session commits.

    Used where a check on the row decides the write -- the public-space-member
    rule reads ``access`` to allow a space move and ``space_id`` to allow an
    unpublish, and without the lock two concurrent requests can both pass and
    leave a private report inside a space. ``FOR UPDATE`` is a no-op on SQLite,
    whose writes are serialised anyway.
    """
    stmt = select(ReportRecord).where(ReportRecord.report_id == report_id).with_for_update()
    result = await session.execute(stmt)
    return result.scalars().first()


def _report_visible_to_user(report: ReportRecord, user_id: str | None) -> bool:
    if user_id is None:
        return True
    return report.access["scope"] == "public" or report.created_by == user_id


def _report_list_item_from_record(report: ReportRecord) -> ReportListItem:
    return ReportListItem(
        report_id=report.report_id,
        name=report.name,
        current_version=report.current_version,
        created_at=report.created_at,
        updated_at=report.updated_at,
        created_by=report.created_by,
        updated_by=report.updated_by,
        access=report.access,
        pinned=report.pinned,
        space_id=report.space_id,
        subspace_id=report.subspace_id,
    )


def _report_version_from_records(report: ReportRecord, version: ReportVersionRecord) -> ReportVersion:
    return ReportVersion(
        report_id=version.report_id,
        name=report.name,
        version=version.version,
        config=version.config,
        created_at=version.created_at,
        created_by=version.created_by,
        comment=version.comment,
        report_created_by=report.created_by,
        report_updated_by=report.updated_by,
        access=report.access,
        # Denormalised from the parent record — space membership is
        # unversioned, so restoring an old version cannot relocate a report.
        space_id=report.space_id,
        subspace_id=report.subspace_id,
    )


def _new_report_records(
    *,
    report_id: str,
    name: str,
    created_by: str,
    now: str,
    access: ReportAccess,
    space_id: str | None = None,
    subspace_id: str | None = None,
) -> tuple[ReportRecord, ReportVersionRecord]:
    """Build the rows a brand-new report is made of."""
    return (
        ReportRecord(
            report_id=report_id,
            name=name,
            current_version=1,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
            access=access.model_dump(),
            space_id=space_id,
            subspace_id=subspace_id,
        ),
        ReportVersionRecord(
            report_id=report_id,
            version=1,
            config=initial_report_config(name),
            created_at=now,
            created_by=created_by,
            comment="Initial version",
        ),
    )


def _space_from_record(record: SpaceRecord) -> SpaceListItem:
    return SpaceListItem(
        space_id=record.space_id,
        name=record.name,
        description=record.description,
        overview_report_id=record.overview_report_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        created_by=record.created_by,
        updated_by=record.updated_by,
    )


def _subspace_from_record(record: SubspaceRecord) -> SubspaceItem:
    return SubspaceItem(
        subspace_id=record.subspace_id,
        space_id=record.space_id,
        name=record.name,
        created_at=record.created_at,
        updated_at=record.updated_at,
        created_by=record.created_by,
        updated_by=record.updated_by,
    )


def _chat_turn_from_record(record: ChatTurnRecord) -> ChatTurnItem:
    return ChatTurnItem.model_validate(
        {
            "turn_id": record.turn_id,
            "thread_id": record.thread_id,
            "user_id": record.user_id,
            "message_id": record.message_id,
            "text_id": record.text_id,
            "idempotency_key": record.idempotency_key,
            "command": record.command,
            "status": record.status,
            "last_seq": record.last_seq,
            "cancel_requested": record.cancel_requested,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "expires_at": record.expires_at,
        }
    )


def _action_confirmation_from_record(record: ActionConfirmationRecord) -> ActionConfirmation:
    return ActionConfirmation.model_validate(
        {
            "confirmation_id": record.confirmation_id,
            "user_id": record.user_id,
            "source": record.source,
            "session_key": record.session_key,
            "tool_name": record.tool_name,
            "action": record.action,
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
            "arguments": record.arguments,
            "arguments_hash": record.arguments_hash,
            "status": record.status,
            "batch_id": record.batch_id,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "decided_at": record.decided_at,
            "decided_by": record.decided_by,
        }
    )


def _model_profile_from_record(record: ModelProfileRecord) -> ModelProfileItem:
    config = ModelProfileConfig.model_validate(record.config)
    return ModelProfileItem(
        profile_id=record.profile_id,
        name=record.name,
        description=record.description,
        enabled=record.enabled,
        is_default=record.is_default,
        current_version=record.current_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
        created_by=record.created_by,
        updated_by=record.updated_by,
        **config.model_dump(),
    )


def _model_profile_version_from_record(record: ModelProfileVersionRecord) -> ModelProfileVersion:
    config = ModelProfileConfig.model_validate(record.config)
    return ModelProfileVersion(
        profile_id=record.profile_id,
        version=record.version,
        name=record.name,
        description=record.description,
        enabled=record.enabled,
        is_default=record.is_default,
        created_at=record.created_at,
        created_by=record.created_by,
        comment=record.comment,
        **config.model_dump(),
    )


def _user_from_record(record: UserRecord) -> User:
    return User(
        user_id=record.user_id,
        sub=record.sub,
        iss=record.iss,
        email=record.email,
        display_name=record.display_name,
        preferred_username=record.preferred_username,
        created_at=record.created_at,
        last_login=record.last_login,
        archived_at=record.archived_at,
        role=record.role,
    )


def _chat_session_from_sql_record(record: "ChatSessionRecord") -> ChatSessionItem:
    return ChatSessionItem(
        thread_id=record.thread_id,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
        origin=record.origin if record.origin in ("interactive", "scheduled", "workflow") else "interactive",
        scheduled_chat_id=record.scheduled_chat_id,
        run_status=record.run_status,
        run_errors=record.run_errors or [],
        model_profile_id=record.model_profile_id,
    )


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        if not settings.SQL_DATABASE_URL.strip():
            raise ValueError("SQL_DATABASE_URL is required and must be a PostgreSQL URL")
        url = build_database_url(
            settings.SQL_DATABASE_URL,
            user=settings.SQL_DATABASE_USER,
            password=settings.SQL_DATABASE_PASSWORD,
        )
        if url.get_backend_name() != "postgresql":
            raise ValueError("SQL_DATABASE_URL must be a PostgreSQL URL")
        url = url.set(drivername="postgresql+asyncpg")
        connect_args = {"command_timeout": settings.SQL_STATEMENT_TIMEOUT}
        _engine = create_async_engine(url, connect_args=connect_args)
    return _engine


# ---------------------------------------------------------------------------
# SQL backend implementation
# ---------------------------------------------------------------------------


class SQLModelReportStore(ReportStore):
    """PostgreSQL implementation configured via ``SQL_DATABASE_*``."""

    def generate_id(self) -> str:
        return generate_report_id()

    async def initialize(self) -> None:
        """Bring the report-store schema to the Alembic head revision."""
        from reporting.services.report_store.migrations import run_schema_migrations

        await run_schema_migrations(_get_engine())
        await self._migrate_legacy_skillsets()
        logger.info("SQL report store tables initialised")

    async def _migrate_legacy_skillsets(self) -> None:
        """Serialize and create canonical packages for skillsets predating plugins."""
        # Checked before the lock: every worker runs startup on every boot, and
        # once a deployment holds no legacy skillsets there is nothing to
        # serialize -- so the common path is one query, not a lock plus a scan.
        if not await self.list_skillsets():
            return
        engine = _get_engine()
        if engine.dialect.name != "postgresql":
            await self._migrate_legacy_skillsets_unlocked()
            return
        async with engine.begin() as connection:
            # Every web worker runs startup. Keep the projection single-writer
            # so its check-and-create sequence cannot race another worker.
            await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('seizu-plugin-legacy-migration'))"))
            await self._migrate_legacy_skillsets_unlocked()

    async def _migrate_legacy_skillsets_unlocked(self) -> None:
        """Create missing canonical packages while the startup lock is held."""
        from reporting.services.plugin_packages import legacy_skillset_package

        for skillset in await self.list_skillsets():
            existing = await self.get_plugin(skillset.skillset_id)
            if existing is not None:
                continue
            parsed = legacy_skillset_package(skillset, await self.list_skills(skillset.skillset_id))
            if not parsed.valid:
                logger.error("Could not migrate legacy skillset %s to a plugin", skillset.skillset_id)
                continue
            await self.publish_plugin(
                parsed.plugin_id,
                parsed.manifest,
                parsed.files,
                parsed.skills,
                [item.model_dump() for item in parsed.diagnostics],
                parsed.package_digest,
                skillset.updated_by or skillset.created_by,
                "Migrated from legacy skillset",
            )

    async def list_reports(self, user_id: str | None = None) -> list[ReportListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(ReportRecord))
            rows = result.scalars().all()
            return [_report_list_item_from_record(r) for r in rows if _report_visible_to_user(r, user_id)]

    async def get_report_metadata(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportListItem | None:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return None
            return _report_list_item_from_record(report)

    async def get_report_latest(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return None
            stmt = (
                select(ReportVersionRecord)
                .where(ReportVersionRecord.report_id == report_id)
                .order_by(col(ReportVersionRecord.version).desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return _report_version_from_records(report, row)

    async def get_report_version(
        self,
        report_id: str,
        version: int,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return None
            stmt = (
                select(ReportVersionRecord)
                .where(ReportVersionRecord.report_id == report_id)
                .where(ReportVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return _report_version_from_records(report, row)

    async def list_report_versions(
        self,
        report_id: str,
        user_id: str | None = None,
    ) -> list[ReportVersion]:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return []
            stmt = (
                select(ReportVersionRecord)
                .where(ReportVersionRecord.report_id == report_id)
                .order_by(col(ReportVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_report_version_from_records(report, r) for r in rows]

    async def create_report(
        self,
        name: str,
        created_by: str,
        access: ReportAccess | None = None,
        space_id: str | None = None,
        subspace_id: str | None = None,
    ) -> ReportListItem:
        """Create a report and its initial renderable version atomically."""
        report_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        report_access = access or ReportAccess(scope="private")
        require_public_space_member(report_access, space_id)

        async with AsyncSession(_get_engine()) as session:
            for record in _new_report_records(
                report_id=report_id,
                name=name,
                created_by=created_by,
                now=now,
                access=report_access,
                space_id=space_id,
                subspace_id=subspace_id,
            ):
                session.add(record)
            await session.commit()

        return ReportListItem(
            report_id=report_id,
            name=name,
            current_version=1,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
            access=report_access,
            space_id=space_id,
            subspace_id=subspace_id,
        )

    async def save_report_version(
        self,
        report_id: str,
        config: dict[str, Any],
        created_by: str,
        comment: str | None = None,
        user_id: str | None = None,
    ) -> ReportVersion | None:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return None

            version = report.current_version + 1
            config_name = config.get("name")
            if isinstance(config_name, str) and config_name.strip():
                report_name = config_name.strip()
            else:
                report_name = report.name
            stored_config = {**config, "name": report_name}
            report_created_by = report.created_by
            report_access = report.access
            # Space membership is left untouched by a version save; captured
            # here only so it can be echoed back on the response.
            report_space_id = report.space_id
            report_subspace_id = report.subspace_id
            now = datetime.now(tz=UTC).isoformat()

            session.add(
                ReportVersionRecord(
                    report_id=report_id,
                    version=version,
                    config=stored_config,
                    created_at=now,
                    created_by=created_by,
                    comment=comment,
                )
            )
            report.current_version = version
            report.name = report_name
            report.updated_at = now
            report.updated_by = created_by
            session.add(report)

            await session.commit()

        return ReportVersion(
            report_id=report_id,
            name=report_name,
            version=version,
            config=stored_config,
            created_at=now,
            created_by=created_by,
            comment=comment,
            report_created_by=report_created_by,
            report_updated_by=created_by,
            access=report_access,
            space_id=report_space_id,
            subspace_id=report_subspace_id,
        )

    async def update_report_visibility(
        self,
        report_id: str,
        updated_by: str,
        access: ReportAccess | None = None,
    ) -> ReportListItem | None:
        async with AsyncSession(_get_engine()) as session:
            report = await _locked_report(session, report_id)
            if not report:
                return None
            # Held under the row lock: privatising must not race a concurrent
            # filing into a space.
            if access is not None and access.scope != "public" and report.space_id is not None:
                raise SpaceConflictError(PRIVATISING_SPACE_MEMBER_DETAIL)
            report.updated_at = datetime.now(tz=UTC).isoformat()
            report.updated_by = updated_by
            if access is not None:
                report.access = access.model_dump()
            session.add(report)
            await session.commit()
            await session.refresh(report)
            return _report_list_item_from_record(report)

    async def update_report_space(
        self,
        report_id: str,
        space_id: str | None,
        subspace_id: str | None,
        updated_by: str,
        user_id: str | None = None,
    ) -> ReportListItem | None:
        async with AsyncSession(_get_engine()) as session:
            report = await _locked_report(session, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return None
            # Held under the row lock: filing must not race a concurrent
            # unpublish.
            if space_id is not None and (report.access or {}).get("scope") != "public":
                raise SpaceConflictError(FILING_PRIVATE_REPORT_DETAIL)
            report.space_id = space_id
            report.subspace_id = subspace_id
            report.updated_at = datetime.now(tz=UTC).isoformat()
            report.updated_by = updated_by
            session.add(report)
            await session.commit()
            await session.refresh(report)
            return _report_list_item_from_record(report)

    async def delete_report(self, report_id: str, user_id: str | None = None) -> bool:
        """Delete a report and all its versions."""
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return False

            pointer = await session.get(DashboardPointerRecord, 1)
            if pointer and pointer.report_id == report_id:
                await session.delete(pointer)

            stmt = select(ReportVersionRecord).where(ReportVersionRecord.report_id == report_id)
            result = await session.execute(stmt)
            for version_record in result.scalars().all():
                await session.delete(version_record)

            await session.delete(report)
            await session.commit()
        return True

    async def pin_report(
        self,
        report_id: str,
        pinned: bool,
        updated_by: str,
        user_id: str | None = None,
    ) -> bool:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report or not _report_visible_to_user(report, user_id):
                return False
            report.pinned = pinned
            report.updated_at = datetime.now(tz=UTC).isoformat()
            report.updated_by = updated_by
            await session.commit()
        return True

    async def get_dashboard_report_id(self) -> str | None:
        async with AsyncSession(_get_engine()) as session:
            row = await session.get(DashboardPointerRecord, 1)
            if not row:
                return None
            return row.report_id

    async def set_dashboard_report(self, report_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            exists = await session.get(ReportRecord, report_id)
            if not exists:
                return False
            if exists.access["scope"] != "public":
                return False
            now = datetime.now(tz=UTC).isoformat()
            existing = await session.get(DashboardPointerRecord, 1)
            if existing:
                existing.report_id = report_id
                existing.updated_at = now
                session.add(existing)
            else:
                session.add(DashboardPointerRecord(id=1, report_id=report_id, updated_at=now))
            await session.commit()
        return True

    async def get_dashboard_report(self) -> ReportVersion | None:
        report_id = await self.get_dashboard_report_id()
        if not report_id:
            return None
        report = await self.get_report_latest(report_id)
        if report and report.access.scope == "public":
            return report
        return None

    async def list_scheduled_queries(self) -> list[ScheduledQueryItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(ScheduledQueryRecord))
            rows = result.scalars().all()
            return [
                ScheduledQueryItem(
                    scheduled_query_id=r.scheduled_query_id,
                    name=r.name,
                    cypher=r.cypher,
                    params=r.params or [],
                    frequency=r.frequency,
                    schedule=r.schedule,
                    watch_scans=r.watch_scans or [],
                    enabled=r.enabled,
                    actions=r.actions or [],
                    stages=r.stages,
                    inputs=r.inputs,
                    activities=r.activities,
                    current_version=r.current_version,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    created_by=r.created_by,
                    updated_by=r.updated_by,
                    last_run_status=r.last_run_status,
                    last_run_at=r.last_run_at,
                    last_errors=r.last_errors or [],
                    last_scheduled_at=r.last_scheduled_at,
                    run_requested_at=r.run_requested_at,
                    schedule_sync_status=r.schedule_sync_status,
                    schedule_sync_error=r.schedule_sync_error,
                    schedule_synced_at=r.schedule_synced_at,
                )
                for r in rows
            ]

    async def get_scheduled_query(self, sq_id: str) -> ScheduledQueryItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, sq_id)
            if not record:
                return None
            return ScheduledQueryItem(
                scheduled_query_id=record.scheduled_query_id,
                name=record.name,
                cypher=record.cypher,
                params=record.params or [],
                frequency=record.frequency,
                schedule=record.schedule,
                watch_scans=record.watch_scans or [],
                enabled=record.enabled,
                actions=record.actions or [],
                stages=record.stages,
                inputs=record.inputs,
                activities=record.activities,
                current_version=record.current_version,
                created_at=record.created_at,
                updated_at=record.updated_at,
                created_by=record.created_by,
                updated_by=record.updated_by,
                last_run_status=record.last_run_status,
                last_run_at=record.last_run_at,
                last_errors=record.last_errors or [],
                last_scheduled_at=record.last_scheduled_at,
                run_requested_at=record.run_requested_at,
                schedule_sync_status=record.schedule_sync_status,
                schedule_sync_error=record.schedule_sync_error,
                schedule_synced_at=record.schedule_synced_at,
            )

    async def create_scheduled_query(
        self,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        created_by: str,
        stages: list[dict[str, Any]] | None = None,
        inputs: dict[str, Any] | None = None,
        activities: list[dict[str, Any]] | None = None,
    ) -> ScheduledQueryItem:
        sq_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            record = ScheduledQueryRecord(
                scheduled_query_id=sq_id,
                name=name,
                cypher=cypher,
                params=params,
                frequency=frequency,
                schedule=schedule,
                watch_scans=watch_scans,
                enabled=enabled,
                actions=actions,
                stages=stages,
                inputs=inputs,
                activities=activities,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                ScheduledQueryVersionRecord(
                    scheduled_query_id=sq_id,
                    version=version,
                    name=name,
                    cypher=cypher,
                    params=params,
                    frequency=frequency,
                    schedule=schedule,
                    watch_scans=watch_scans,
                    enabled=enabled,
                    actions=actions,
                    stages=stages,
                    inputs=inputs,
                    activities=activities,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
        return ScheduledQueryItem(
            scheduled_query_id=sq_id,
            name=name,
            cypher=cypher,
            params=params,
            frequency=frequency,
            schedule=schedule,
            watch_scans=watch_scans,
            enabled=enabled,
            actions=actions,
            stages=stages,
            inputs=inputs,
            activities=activities,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
            last_run_status=None,
            last_run_at=None,
            last_errors=[],
            last_scheduled_at=None,
        )

    async def update_scheduled_query(
        self,
        sq_id: str,
        name: str,
        cypher: str,
        params: list[dict[str, Any]],
        frequency: int | None,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        actions: list[dict[str, Any]],
        updated_by: str,
        comment: str | None = None,
        stages: list[dict[str, Any]] | None = None,
        inputs: dict[str, Any] | None = None,
        activities: list[dict[str, Any]] | None = None,
    ) -> ScheduledQueryItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, sq_id)
            if not record:
                return None
            original_created_at = record.created_at
            original_created_by = record.created_by
            orig_last_run_status = record.last_run_status
            orig_last_run_at = record.last_run_at
            orig_last_errors = list(record.last_errors or [])
            orig_last_scheduled_at = record.last_scheduled_at
            orig_run_requested_at = record.run_requested_at
            version = record.current_version + 1
            record.name = name
            record.cypher = cypher
            record.params = params
            record.frequency = frequency
            record.schedule = schedule
            record.watch_scans = watch_scans
            record.enabled = enabled
            record.actions = actions
            record.stages = stages
            record.inputs = inputs
            record.activities = activities
            record.schedule_sync_status = "pending"
            record.schedule_sync_error = None
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                ScheduledQueryVersionRecord(
                    scheduled_query_id=sq_id,
                    version=version,
                    name=name,
                    cypher=cypher,
                    params=params,
                    frequency=frequency,
                    schedule=schedule,
                    watch_scans=watch_scans,
                    enabled=enabled,
                    actions=actions,
                    stages=stages,
                    inputs=inputs,
                    activities=activities,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
        return ScheduledQueryItem(
            scheduled_query_id=sq_id,
            name=name,
            cypher=cypher,
            params=params,
            frequency=frequency,
            schedule=schedule,
            watch_scans=watch_scans,
            enabled=enabled,
            actions=actions,
            stages=stages,
            inputs=inputs,
            activities=activities,
            current_version=version,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=updated_by,
            last_run_status=orig_last_run_status,
            last_run_at=orig_last_run_at,
            last_errors=orig_last_errors,
            last_scheduled_at=orig_last_scheduled_at,
            run_requested_at=orig_run_requested_at,
            schedule_sync_status="pending",
        )

    async def list_scheduled_query_versions(self, sq_id: str) -> list[ScheduledQueryVersion]:
        async with AsyncSession(_get_engine()) as session:
            sq = await session.get(ScheduledQueryRecord, sq_id)
            if not sq:
                return []
            stmt = (
                select(ScheduledQueryVersionRecord)
                .where(ScheduledQueryVersionRecord.scheduled_query_id == sq_id)
                .order_by(col(ScheduledQueryVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                ScheduledQueryVersion(
                    scheduled_query_id=r.scheduled_query_id,
                    name=r.name or sq.name,
                    version=r.version,
                    cypher=r.cypher,
                    params=r.params or [],
                    frequency=r.frequency,
                    schedule=r.schedule,
                    watch_scans=r.watch_scans or [],
                    enabled=r.enabled,
                    actions=r.actions or [],
                    stages=r.stages,
                    inputs=r.inputs,
                    activities=r.activities,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in rows
            ]

    async def get_scheduled_query_version(self, sq_id: str, version: int) -> ScheduledQueryVersion | None:
        async with AsyncSession(_get_engine()) as session:
            sq = await session.get(ScheduledQueryRecord, sq_id)
            if not sq:
                return None
            stmt = (
                select(ScheduledQueryVersionRecord)
                .where(ScheduledQueryVersionRecord.scheduled_query_id == sq_id)
                .where(ScheduledQueryVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return ScheduledQueryVersion(
                scheduled_query_id=row.scheduled_query_id,
                name=row.name or sq.name,
                version=row.version,
                cypher=row.cypher,
                params=row.params or [],
                frequency=row.frequency,
                schedule=row.schedule,
                watch_scans=row.watch_scans or [],
                enabled=row.enabled,
                actions=row.actions or [],
                stages=row.stages,
                inputs=row.inputs,
                activities=row.activities,
                created_at=row.created_at,
                created_by=row.created_by,
                comment=row.comment,
            )

    async def acquire_scheduled_query_lock(self, sq_id: str, expected_last_scheduled_at: str | None) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            if expected_last_scheduled_at is None:
                condition = and_(
                    ScheduledQueryRecord.scheduled_query_id == sq_id,
                    ScheduledQueryRecord.last_scheduled_at == null(),
                )
            else:
                condition = and_(
                    ScheduledQueryRecord.scheduled_query_id == sq_id,
                    ScheduledQueryRecord.last_scheduled_at == expected_last_scheduled_at,
                )
            stmt = update(ScheduledQueryRecord).where(condition).values(last_scheduled_at=now)
            result = await session.execute(stmt)
            await session.commit()
        return result.rowcount == 1

    async def record_scheduled_query_result(self, sq_id: str, status: str, error: str | None = None) -> None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, sq_id)
            if not record:
                return
            record.last_run_status = status
            record.last_run_at = now
            if status == "failure" and error:
                errors = list(record.last_errors or [])
                errors.insert(0, {"timestamp": now, "error": error})
                record.last_errors = errors[:5]
            elif status == "success":
                record.last_errors = []
            session.add(record)
            await session.commit()

    async def request_scheduled_query_run(self, sq_id: str) -> str | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, sq_id)
            if not record:
                return None
            record.run_requested_at = now
            session.add(record)
            await session.commit()
        return now

    async def set_workflow_schedule_sync_status(
        self,
        workflow_id: str,
        status: str,
        *,
        error: str | None = None,
        synced_at: str | None = None,
    ) -> None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, workflow_id)
            if record is None:
                return
            record.schedule_sync_status = status
            record.schedule_sync_error = error
            record.schedule_synced_at = synced_at
            session.add(record)
            await session.commit()

    async def set_chat_schedule_sync_status(
        self,
        sc_id: str,
        status: str,
        *,
        error: str | None = None,
        synced_at: str | None = None,
    ) -> None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if record is None:
                return
            record.schedule_sync_status = status
            record.schedule_sync_error = error
            record.schedule_synced_at = synced_at
            session.add(record)
            await session.commit()

    async def delete_scheduled_query(self, sq_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledQueryRecord, sq_id)
            if not record:
                return False
            stmt = select(ScheduledQueryVersionRecord).where(ScheduledQueryVersionRecord.scheduled_query_id == sq_id)
            result = await session.execute(stmt)
            for ver in result.scalars().all():
                await session.delete(ver)
            await session.delete(record)
            await session.commit()
        return True

    def _scheduled_chat_from_record(self, record: ScheduledChatRecord) -> ScheduledChatItem:
        return ScheduledChatItem(
            scheduled_chat_id=record.scheduled_chat_id,
            name=record.name,
            prompt=record.prompt,
            model_profile_id=record.model_profile_id,
            schedule=record.schedule,
            watch_scans=record.watch_scans or [],
            enabled=record.enabled,
            current_version=record.current_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
            last_run_status=record.last_run_status,
            last_run_at=record.last_run_at,
            last_errors=record.last_errors or [],
            last_scheduled_at=record.last_scheduled_at,
            run_requested_at=record.run_requested_at,
        )

    def _scheduled_chat_version_from_record(self, record: ScheduledChatVersionRecord) -> ScheduledChatVersion:
        return ScheduledChatVersion(
            scheduled_chat_id=record.scheduled_chat_id,
            version=record.version,
            name=record.name,
            prompt=record.prompt,
            model_profile_id=record.model_profile_id,
            schedule=record.schedule,
            watch_scans=record.watch_scans or [],
            enabled=record.enabled,
            created_at=record.created_at,
            created_by=record.created_by,
            comment=record.comment,
        )

    async def list_scheduled_chats(self, user_id: str | None = None) -> list[ScheduledChatItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ScheduledChatRecord)
            if user_id is not None:
                stmt = stmt.where(ScheduledChatRecord.created_by == user_id)
            result = await session.execute(stmt)
            return [self._scheduled_chat_from_record(r) for r in result.scalars().all()]

    async def get_scheduled_chat(self, sc_id: str) -> ScheduledChatItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return None
            return self._scheduled_chat_from_record(record)

    async def create_scheduled_chat(
        self,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
        model_profile_id: str | None = None,
    ) -> ScheduledChatItem:
        sc_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            record = ScheduledChatRecord(
                scheduled_chat_id=sc_id,
                name=name,
                prompt=prompt,
                model_profile_id=model_profile_id,
                schedule=schedule,
                watch_scans=watch_scans,
                enabled=enabled,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                ScheduledChatVersionRecord(
                    scheduled_chat_id=sc_id,
                    version=version,
                    name=name,
                    prompt=prompt,
                    model_profile_id=model_profile_id,
                    schedule=schedule,
                    watch_scans=watch_scans,
                    enabled=enabled,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
            await session.refresh(record)
            return self._scheduled_chat_from_record(record)

    async def update_scheduled_chat(
        self,
        sc_id: str,
        name: str,
        prompt: str,
        schedule: dict[str, Any] | None,
        watch_scans: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
        model_profile_id: str | None = None,
    ) -> ScheduledChatItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return None
            version = record.current_version + 1
            record.name = name
            record.prompt = prompt
            record.model_profile_id = model_profile_id
            record.schedule = schedule
            record.watch_scans = watch_scans
            record.enabled = enabled
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                ScheduledChatVersionRecord(
                    scheduled_chat_id=sc_id,
                    version=version,
                    name=name,
                    prompt=prompt,
                    model_profile_id=model_profile_id,
                    schedule=schedule,
                    watch_scans=watch_scans,
                    enabled=enabled,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
            await session.refresh(record)
            return self._scheduled_chat_from_record(record)

    async def list_scheduled_chat_versions(self, sc_id: str) -> list[ScheduledChatVersion]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ScheduledChatVersionRecord)
                .where(ScheduledChatVersionRecord.scheduled_chat_id == sc_id)
                .order_by(col(ScheduledChatVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            return [self._scheduled_chat_version_from_record(r) for r in result.scalars().all()]

    async def get_scheduled_chat_version(self, sc_id: str, version: int) -> ScheduledChatVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ScheduledChatVersionRecord).where(
                ScheduledChatVersionRecord.scheduled_chat_id == sc_id,
                ScheduledChatVersionRecord.version == version,
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record is None:
                return None
            return self._scheduled_chat_version_from_record(record)

    async def delete_scheduled_chat(self, sc_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return False
            await session.execute(delete(ChatSessionRecord).where(col(ChatSessionRecord.scheduled_chat_id) == sc_id))
            stmt = select(ScheduledChatVersionRecord).where(ScheduledChatVersionRecord.scheduled_chat_id == sc_id)
            result = await session.execute(stmt)
            for ver in result.scalars().all():
                await session.delete(ver)
            await session.delete(record)
            await session.commit()
        return True

    async def acquire_scheduled_chat_lock(self, sc_id: str, expected_last_scheduled_at: str | None) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            if expected_last_scheduled_at is None:
                condition = and_(
                    ScheduledChatRecord.scheduled_chat_id == sc_id,
                    ScheduledChatRecord.last_scheduled_at == null(),
                )
            else:
                condition = and_(
                    ScheduledChatRecord.scheduled_chat_id == sc_id,
                    ScheduledChatRecord.last_scheduled_at == expected_last_scheduled_at,
                )
            stmt = update(ScheduledChatRecord).where(condition).values(last_scheduled_at=now)
            result = await session.execute(stmt)
            await session.commit()
        return result.rowcount == 1

    async def record_scheduled_chat_result(self, sc_id: str, status: str, error: str | None = None) -> None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return
            record.last_run_status = status
            record.last_run_at = now
            if status == "failure" and error:
                errors = list(record.last_errors or [])
                errors.insert(0, {"timestamp": now, "error": error})
                record.last_errors = errors[:5]
            elif status in {"success", "partial", "budget_exhausted"}:
                record.last_errors = []
            session.add(record)
            await session.commit()

    async def request_scheduled_chat_run(self, sc_id: str) -> str | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return None
            record.run_requested_at = now
            session.add(record)
            await session.commit()
        return now

    async def get_or_create_user(
        self,
        sub: str,
        iss: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        role: str | None = None,
    ) -> User:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = select(UserRecord).where(UserRecord.iss == iss).where(UserRecord.sub == sub)
            result = await session.execute(stmt)
            record = result.scalars().first()
            if not record:
                user_id = generate_report_id()
                record = UserRecord(
                    user_id=user_id,
                    sub=sub,
                    iss=iss,
                    email=email,
                    display_name=display_name,
                    preferred_username=preferred_username,
                    created_at=now,
                    last_login=now,
                    archived_at=None,
                    role=role,
                )
                session.add(record)
                try:
                    await session.commit()
                    await session.refresh(record)
                except IntegrityError:
                    await session.rollback()
                    result = await session.execute(stmt)
                    record = result.scalars().first()
                    if not record:
                        raise
            if record.role != role:
                record.role = role
                session.add(record)
                await session.commit()
                await session.refresh(record)
        return _user_from_record(record)

    async def update_user_profile(
        self,
        user_id: str,
        email: str | None = None,
        display_name: str | None = None,
        preferred_username: str | None = None,
        token_iat: datetime | None = None,
    ) -> User:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(UserRecord, user_id)
            if not record:
                raise ValueError(f"User {user_id!r} not found")
            changed = False
            if email is not None and record.email != email:
                record.email = email
                changed = True
            if display_name is not None and record.display_name != display_name:
                record.display_name = display_name
                changed = True
            if preferred_username is not None and record.preferred_username != preferred_username:
                record.preferred_username = preferred_username
                changed = True
            if token_iat is not None:
                stored = datetime.fromisoformat(record.last_login)
                if token_iat > stored:
                    record.last_login = token_iat.isoformat()
                    changed = True
            if changed:
                session.add(record)
                await session.commit()
                await session.refresh(record)
        return _user_from_record(record)

    async def get_user(self, user_id: str) -> User | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(UserRecord, user_id)
            if not record:
                return None
            return _user_from_record(record)

    async def archive_user(self, user_id: str) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(UserRecord, user_id)
            if not record:
                return False
            record.archived_at = now
            session.add(record)
            await session.commit()
        return True

    # ------------------------------------------------------------------
    # Spaces
    # ------------------------------------------------------------------

    async def list_spaces(self) -> list[SpaceListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(SpaceRecord))
            return [_space_from_record(r) for r in result.scalars().all()]

    async def get_space(self, space_id: str) -> SpaceListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SpaceRecord, space_id)
            return _space_from_record(record) if record else None

    async def create_space(
        self,
        name: str,
        description: str,
        created_by: str,
    ) -> SpaceListItem:
        space_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()

        # No overview report: the overview is a pointer the user sets later.
        async with AsyncSession(_get_engine()) as session:
            session.add(
                SpaceRecord(
                    space_id=space_id,
                    name=name,
                    description=description,
                    overview_report_id=None,
                    created_at=now,
                    updated_at=now,
                    created_by=created_by,
                    updated_by=created_by,
                )
            )
            await session.commit()

        return SpaceListItem(
            space_id=space_id,
            name=name,
            description=description,
            overview_report_id=None,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_space(
        self,
        space_id: str,
        name: str,
        description: str,
        updated_by: str,
    ) -> SpaceListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SpaceRecord, space_id)
            if not record:
                return None
            record.name = name
            record.description = description
            record.updated_at = datetime.now(tz=UTC).isoformat()
            record.updated_by = updated_by
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _space_from_record(record)

    async def delete_space(self, space_id: str) -> SpaceDeleteResult:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SpaceRecord, space_id)
            if not record:
                return SpaceDeleteResult.NOT_FOUND

            # Unfiltered on purpose: a member report the caller cannot see
            # still keeps the space non-empty, so deleting cannot orphan it.
            members = await session.execute(select(ReportRecord).where(ReportRecord.space_id == space_id))
            if members.scalars().first() is not None:
                return SpaceDeleteResult.NOT_EMPTY

            # Sub-spaces go with the space. They are only grouping labels, and
            # with no member reports left there is nothing referencing them.
            subspaces = await session.execute(select(SubspaceRecord).where(SubspaceRecord.space_id == space_id))
            for subspace in subspaces.scalars().all():
                await session.delete(subspace)

            await session.delete(record)
            await session.commit()
        return SpaceDeleteResult.DELETED

    async def set_space_overview(
        self,
        space_id: str,
        report_id: str | None,
        updated_by: str,
    ) -> SpaceListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SpaceRecord, space_id)
            if not record:
                return None
            record.overview_report_id = report_id
            record.updated_at = datetime.now(tz=UTC).isoformat()
            record.updated_by = updated_by
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _space_from_record(record)

    async def list_space_reports(
        self,
        space_id: str,
        user_id: str | None = None,
    ) -> list[ReportListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(ReportRecord).where(ReportRecord.space_id == space_id))
            return [
                _report_list_item_from_record(r) for r in result.scalars().all() if _report_visible_to_user(r, user_id)
            ]

    # ------------------------------------------------------------------
    # Sub-spaces (nested under spaces)
    # ------------------------------------------------------------------

    async def list_subspaces(self, space_id: str) -> list[SubspaceItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(SubspaceRecord).where(SubspaceRecord.space_id == space_id))
            return [_subspace_from_record(r) for r in result.scalars().all()]

    async def get_subspace(self, subspace_id: str) -> SubspaceItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SubspaceRecord, subspace_id)
            return _subspace_from_record(record) if record else None

    async def create_subspace(
        self,
        space_id: str,
        name: str,
        created_by: str,
    ) -> SubspaceItem | None:
        async with AsyncSession(_get_engine()) as session:
            if await session.get(SpaceRecord, space_id) is None:
                return None
            now = datetime.now(tz=UTC).isoformat()
            record = SubspaceRecord(
                subspace_id=generate_report_id(),
                space_id=space_id,
                name=name,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _subspace_from_record(record)

    async def update_subspace(
        self,
        subspace_id: str,
        name: str,
        updated_by: str,
    ) -> SubspaceItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SubspaceRecord, subspace_id)
            if not record:
                return None
            record.name = name
            record.updated_at = datetime.now(tz=UTC).isoformat()
            record.updated_by = updated_by
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _subspace_from_record(record)

    async def delete_subspace(self, subspace_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SubspaceRecord, subspace_id)
            if not record:
                return False
            # Member reports are deliberately left alone: an unresolvable
            # subspace_id reads as ungrouped, which beats an unbounded
            # non-transactional fan-out write over every member report.
            await session.delete(record)
            await session.commit()
        return True

    # ------------------------------------------------------------------
    # Toolsets
    # ------------------------------------------------------------------

    def _toolset_item_from_record(self, record: ToolsetRecord) -> ToolsetListItem:
        return ToolsetListItem(
            toolset_id=record.toolset_id,
            name=record.name,
            description=record.description or "",
            enabled=record.enabled,
            current_version=record.current_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
        )

    async def list_toolsets(self) -> list[ToolsetListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(ToolsetRecord))
            rows = result.scalars().all()
            return [self._toolset_item_from_record(r) for r in rows]

    async def get_toolset(self, toolset_id: str) -> ToolsetListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolsetRecord, toolset_id)
            if not record:
                return None
            return self._toolset_item_from_record(record)

    async def create_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> ToolsetListItem:
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            record = ToolsetRecord(
                toolset_id=toolset_id,
                name=name,
                description=description,
                enabled=enabled,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                ToolsetVersionRecord(
                    toolset_id=toolset_id,
                    version=version,
                    name=name,
                    description=description,
                    enabled=enabled,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
        return ToolsetListItem(
            toolset_id=toolset_id,
            name=name,
            description=description,
            enabled=enabled,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_toolset(
        self,
        toolset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolsetListItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolsetRecord, toolset_id)
            if not record:
                return None
            original_created_at = record.created_at
            original_created_by = record.created_by
            version = record.current_version + 1
            record.name = name
            record.description = description
            record.enabled = enabled
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                ToolsetVersionRecord(
                    toolset_id=toolset_id,
                    version=version,
                    name=name,
                    description=description,
                    enabled=enabled,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
        return ToolsetListItem(
            toolset_id=toolset_id,
            name=name,
            description=description,
            enabled=enabled,
            current_version=version,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=updated_by,
        )

    async def delete_toolset(self, toolset_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolsetRecord, toolset_id)
            if not record:
                return False

            # Delete all tools and their versions
            tools_stmt = select(ToolRecord).where(ToolRecord.toolset_id == toolset_id)
            tools_result = await session.execute(tools_stmt)
            for tool_record in tools_result.scalars().all():
                versions_stmt = select(ToolVersionRecord).where(ToolVersionRecord.tool_id == tool_record.tool_id)
                versions_result = await session.execute(versions_stmt)
                for ver in versions_result.scalars().all():
                    await session.delete(ver)
                await session.delete(tool_record)

            # Delete all toolset versions
            ts_versions_stmt = select(ToolsetVersionRecord).where(ToolsetVersionRecord.toolset_id == toolset_id)
            ts_versions_result = await session.execute(ts_versions_stmt)
            for ver in ts_versions_result.scalars().all():
                await session.delete(ver)

            await session.delete(record)
            await session.commit()
        return True

    async def list_toolset_versions(self, toolset_id: str) -> list[ToolsetVersion]:
        async with AsyncSession(_get_engine()) as session:
            ts = await session.get(ToolsetRecord, toolset_id)
            if not ts:
                return []
            stmt = (
                select(ToolsetVersionRecord)
                .where(ToolsetVersionRecord.toolset_id == toolset_id)
                .order_by(col(ToolsetVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                ToolsetVersion(
                    toolset_id=r.toolset_id,
                    name=r.name,
                    description=r.description or "",
                    enabled=r.enabled,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in rows
            ]

    async def get_toolset_version(self, toolset_id: str, version: int) -> ToolsetVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ToolsetVersionRecord)
                .where(ToolsetVersionRecord.toolset_id == toolset_id)
                .where(ToolsetVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return ToolsetVersion(
                toolset_id=row.toolset_id,
                name=row.name,
                description=row.description or "",
                enabled=row.enabled,
                version=row.version,
                created_at=row.created_at,
                created_by=row.created_by,
                comment=row.comment,
            )

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _tool_item_from_record(self, record: ToolRecord) -> ToolItem:
        return ToolItem(
            tool_id=record.tool_id,
            toolset_id=record.toolset_id,
            name=record.name,
            description=record.description or "",
            cypher=record.cypher,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (record.parameters or [])],
            enabled=record.enabled,
            current_version=record.current_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
        )

    async def list_tools(self, toolset_id: str) -> list[ToolItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ToolRecord).where(ToolRecord.toolset_id == toolset_id)
            result = await session.execute(stmt)
            return [self._tool_item_from_record(r) for r in result.scalars().all()]

    async def get_tool(self, tool_id: str) -> ToolItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolRecord, tool_id)
            if not record:
                return None
            return self._tool_item_from_record(record)

    async def create_tool(
        self,
        toolset_id: str,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        created_by: str,
    ) -> ToolItem | None:
        async with AsyncSession(_get_engine()) as session:
            ts = await session.get(ToolsetRecord, toolset_id)
            if not ts:
                return None
            now = datetime.now(tz=UTC).isoformat()
            version = 1
            record = ToolRecord(
                tool_id=tool_id,
                toolset_id=toolset_id,
                name=name,
                description=description,
                cypher=cypher,
                parameters=parameters,
                enabled=enabled,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                ToolVersionRecord(
                    tool_id=tool_id,
                    toolset_id=toolset_id,
                    version=version,
                    name=name,
                    description=description,
                    cypher=cypher,
                    parameters=parameters,
                    enabled=enabled,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
        return ToolItem(
            tool_id=tool_id,
            toolset_id=toolset_id,
            name=name,
            description=description,
            cypher=cypher,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in parameters],
            enabled=enabled,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_tool(
        self,
        tool_id: str,
        name: str,
        description: str,
        cypher: str,
        parameters: list[dict[str, Any]],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> ToolItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolRecord, tool_id)
            if not record:
                return None
            toolset_id = record.toolset_id
            original_created_at = record.created_at
            original_created_by = record.created_by
            version = record.current_version + 1
            record.name = name
            record.description = description
            record.cypher = cypher
            record.parameters = parameters
            record.enabled = enabled
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                ToolVersionRecord(
                    tool_id=tool_id,
                    toolset_id=toolset_id,
                    version=version,
                    name=name,
                    description=description,
                    cypher=cypher,
                    parameters=parameters,
                    enabled=enabled,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
        return ToolItem(
            tool_id=tool_id,
            toolset_id=toolset_id,
            name=name,
            description=description,
            cypher=cypher,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in parameters],
            enabled=enabled,
            current_version=version,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=updated_by,
        )

    async def delete_tool(self, tool_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ToolRecord, tool_id)
            if not record:
                return False
            stmt = select(ToolVersionRecord).where(ToolVersionRecord.tool_id == tool_id)
            result = await session.execute(stmt)
            for ver in result.scalars().all():
                await session.delete(ver)
            await session.delete(record)
            await session.commit()
        return True

    async def list_tool_versions(self, tool_id: str) -> list[ToolVersion]:
        async with AsyncSession(_get_engine()) as session:
            tool = await session.get(ToolRecord, tool_id)
            if not tool:
                return []
            stmt = (
                select(ToolVersionRecord)
                .where(ToolVersionRecord.tool_id == tool_id)
                .order_by(col(ToolVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                ToolVersion(
                    tool_id=r.tool_id,
                    toolset_id=r.toolset_id,
                    name=r.name,
                    description=r.description or "",
                    cypher=r.cypher,
                    parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (r.parameters or [])],
                    enabled=r.enabled,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in rows
            ]

    async def get_tool_version(self, tool_id: str, version: int) -> ToolVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ToolVersionRecord)
                .where(ToolVersionRecord.tool_id == tool_id)
                .where(ToolVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return ToolVersion(
                tool_id=row.tool_id,
                toolset_id=row.toolset_id,
                name=row.name,
                description=row.description or "",
                cypher=row.cypher,
                parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (row.parameters or [])],
                enabled=row.enabled,
                version=row.version,
                created_at=row.created_at,
                created_by=row.created_by,
                comment=row.comment,
            )

    async def list_enabled_tools(self) -> list[ToolItem]:
        from sqlmodel import col

        async with AsyncSession(_get_engine()) as session:
            ts_stmt = select(ToolsetRecord).where(
                col(ToolsetRecord.enabled) == True  # noqa: E712
            )
            ts_result = await session.execute(ts_stmt)
            enabled_toolset_ids = [r.toolset_id for r in ts_result.scalars().all()]
            if not enabled_toolset_ids:
                return []
            tool_stmt = (
                select(ToolRecord)
                .where(col(ToolRecord.toolset_id).in_(enabled_toolset_ids))
                .where(col(ToolRecord.enabled) == True)  # noqa: E712
            )
            tool_result = await session.execute(tool_stmt)
            return [self._tool_item_from_record(r) for r in tool_result.scalars().all()]

    async def get_enabled_tool(self, toolset_id: str, tool_id: str) -> ToolItem | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ToolRecord)
                .join(ToolsetRecord, ToolRecord.toolset_id == ToolsetRecord.toolset_id)
                .where(ToolRecord.tool_id == tool_id)
                .where(ToolRecord.toolset_id == toolset_id)
                .where(ToolRecord.enabled == True)  # noqa: E712
                .where(ToolsetRecord.enabled == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            return self._tool_item_from_record(record) if record else None

    # ------------------------------------------------------------------
    # Skillsets
    # ------------------------------------------------------------------

    def _skillset_item_from_record(self, record: SkillsetRecord) -> SkillsetListItem:
        return SkillsetListItem(
            skillset_id=record.skillset_id,
            name=record.name,
            description=record.description or "",
            enabled=record.enabled,
            current_version=record.current_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
        )

    async def list_skillsets(self) -> list[SkillsetListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(SkillsetRecord))
            rows = result.scalars().all()
            return [self._skillset_item_from_record(r) for r in rows]

    async def get_skillset(self, skillset_id: str) -> SkillsetListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillsetRecord, skillset_id)
            if not record:
                return None
            return self._skillset_item_from_record(record)

    async def create_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        created_by: str,
    ) -> SkillsetListItem:
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            record = SkillsetRecord(
                skillset_id=skillset_id,
                name=name,
                description=description,
                enabled=enabled,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                SkillsetVersionRecord(
                    skillset_id=skillset_id,
                    version=version,
                    name=name,
                    description=description,
                    enabled=enabled,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
        return SkillsetListItem(
            skillset_id=skillset_id,
            name=name,
            description=description,
            enabled=enabled,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_skillset(
        self,
        skillset_id: str,
        name: str,
        description: str,
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillsetListItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillsetRecord, skillset_id)
            if not record:
                return None
            original_created_at = record.created_at
            original_created_by = record.created_by
            version = record.current_version + 1
            record.name = name
            record.description = description
            record.enabled = enabled
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                SkillsetVersionRecord(
                    skillset_id=skillset_id,
                    version=version,
                    name=name,
                    description=description,
                    enabled=enabled,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
        return SkillsetListItem(
            skillset_id=skillset_id,
            name=name,
            description=description,
            enabled=enabled,
            current_version=version,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=updated_by,
        )

    async def delete_skillset(self, skillset_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillsetRecord, skillset_id)
            if not record:
                return False

            skills_stmt = select(SkillRecord).where(SkillRecord.skillset_id == skillset_id)
            skills_result = await session.execute(skills_stmt)
            for skill_record in skills_result.scalars().all():
                versions_stmt = select(SkillVersionRecord).where(SkillVersionRecord.skill_id == skill_record.skill_id)
                versions_result = await session.execute(versions_stmt)
                for ver in versions_result.scalars().all():
                    await session.delete(ver)
                await session.delete(skill_record)

            ss_versions_stmt = select(SkillsetVersionRecord).where(SkillsetVersionRecord.skillset_id == skillset_id)
            ss_versions_result = await session.execute(ss_versions_stmt)
            for ver in ss_versions_result.scalars().all():
                await session.delete(ver)

            await session.delete(record)
            await session.commit()
        return True

    async def list_skillset_versions(self, skillset_id: str) -> list[SkillsetVersion]:
        async with AsyncSession(_get_engine()) as session:
            ss = await session.get(SkillsetRecord, skillset_id)
            if not ss:
                return []
            stmt = (
                select(SkillsetVersionRecord)
                .where(SkillsetVersionRecord.skillset_id == skillset_id)
                .order_by(col(SkillsetVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                SkillsetVersion(
                    skillset_id=r.skillset_id,
                    name=r.name,
                    description=r.description or "",
                    enabled=r.enabled,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in rows
            ]

    async def get_skillset_version(self, skillset_id: str, version: int) -> SkillsetVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(SkillsetVersionRecord)
                .where(SkillsetVersionRecord.skillset_id == skillset_id)
                .where(SkillsetVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return SkillsetVersion(
                skillset_id=row.skillset_id,
                name=row.name,
                description=row.description or "",
                enabled=row.enabled,
                version=row.version,
                created_at=row.created_at,
                created_by=row.created_by,
                comment=row.comment,
            )

    # ------------------------------------------------------------------
    # Skills
    # ------------------------------------------------------------------

    def _skill_item_from_record(self, record: SkillRecord) -> SkillItem:
        return SkillItem(
            skill_id=record.skill_id,
            skillset_id=record.skillset_id,
            name=record.name,
            description=record.description or "",
            template=record.template,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (record.parameters or [])],
            triggers=record.triggers or [],
            tools_required=record.tools_required or [],
            enabled=record.enabled,
            current_version=record.current_version,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
        )

    async def list_skills(self, skillset_id: str) -> list[SkillItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(SkillRecord).where(SkillRecord.skillset_id == skillset_id)
            result = await session.execute(stmt)
            return [self._skill_item_from_record(r) for r in result.scalars().all()]

    async def get_skill(self, skill_id: str) -> SkillItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillRecord, skill_id)
            if not record:
                return None
            return self._skill_item_from_record(record)

    async def create_skill(
        self,
        skillset_id: str,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        created_by: str,
    ) -> SkillItem | None:
        async with AsyncSession(_get_engine()) as session:
            ss = await session.get(SkillsetRecord, skillset_id)
            if not ss:
                return None
            now = datetime.now(tz=UTC).isoformat()
            version = 1
            record = SkillRecord(
                skill_id=skill_id,
                skillset_id=skillset_id,
                name=name,
                description=description,
                template=template,
                parameters=parameters,
                triggers=triggers,
                tools_required=tools_required,
                enabled=enabled,
                current_version=version,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                SkillVersionRecord(
                    skill_id=skill_id,
                    skillset_id=skillset_id,
                    version=version,
                    name=name,
                    description=description,
                    template=template,
                    parameters=parameters,
                    triggers=triggers,
                    tools_required=tools_required,
                    enabled=enabled,
                    created_at=now,
                    created_by=created_by,
                    comment=None,
                )
            )
            await session.commit()
        return SkillItem(
            skill_id=skill_id,
            skillset_id=skillset_id,
            name=name,
            description=description,
            template=template,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in parameters],
            triggers=triggers,
            tools_required=tools_required,
            enabled=enabled,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        template: str,
        parameters: list[dict[str, Any]],
        triggers: list[str],
        tools_required: list[str],
        enabled: bool,
        updated_by: str,
        comment: str | None = None,
    ) -> SkillItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillRecord, skill_id)
            if not record:
                return None
            skillset_id = record.skillset_id
            original_created_at = record.created_at
            original_created_by = record.created_by
            version = record.current_version + 1
            record.name = name
            record.description = description
            record.template = template
            record.parameters = parameters
            record.triggers = triggers
            record.tools_required = tools_required
            record.enabled = enabled
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                SkillVersionRecord(
                    skill_id=skill_id,
                    skillset_id=skillset_id,
                    version=version,
                    name=name,
                    description=description,
                    template=template,
                    parameters=parameters,
                    triggers=triggers,
                    tools_required=tools_required,
                    enabled=enabled,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
        return SkillItem(
            skill_id=skill_id,
            skillset_id=skillset_id,
            name=name,
            description=description,
            template=template,
            parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in parameters],
            triggers=triggers,
            tools_required=tools_required,
            enabled=enabled,
            current_version=version,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=updated_by,
        )

    async def delete_skill(self, skill_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(SkillRecord, skill_id)
            if not record:
                return False
            stmt = select(SkillVersionRecord).where(SkillVersionRecord.skill_id == skill_id)
            result = await session.execute(stmt)
            for ver in result.scalars().all():
                await session.delete(ver)
            await session.delete(record)
            await session.commit()
        return True

    async def list_skill_versions(self, skill_id: str) -> list[SkillVersion]:
        async with AsyncSession(_get_engine()) as session:
            skill = await session.get(SkillRecord, skill_id)
            if not skill:
                return []
            stmt = (
                select(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == skill_id)
                .order_by(col(SkillVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                SkillVersion(
                    skill_id=r.skill_id,
                    skillset_id=r.skillset_id,
                    name=r.name,
                    description=r.description or "",
                    template=r.template,
                    parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (r.parameters or [])],
                    triggers=r.triggers or [],
                    tools_required=r.tools_required or [],
                    enabled=r.enabled,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in rows
            ]

    async def get_skill_version(self, skill_id: str, version: int) -> SkillVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(SkillVersionRecord)
                .where(SkillVersionRecord.skill_id == skill_id)
                .where(SkillVersionRecord.version == version)
            )
            result = await session.execute(stmt)
            row = result.scalars().first()
            if not row:
                return None
            return SkillVersion(
                skill_id=row.skill_id,
                skillset_id=row.skillset_id,
                name=row.name,
                description=row.description or "",
                template=row.template,
                parameters=[ToolParamDef(**p) if isinstance(p, dict) else p for p in (row.parameters or [])],
                triggers=row.triggers or [],
                tools_required=row.tools_required or [],
                enabled=row.enabled,
                version=row.version,
                created_at=row.created_at,
                created_by=row.created_by,
                comment=row.comment,
            )

    async def list_enabled_skills(self) -> list[SkillItem]:
        from sqlmodel import col

        async with AsyncSession(_get_engine()) as session:
            ss_stmt = select(SkillsetRecord).where(
                col(SkillsetRecord.enabled) == True  # noqa: E712
            )
            ss_result = await session.execute(ss_stmt)
            enabled_skillset_ids = [r.skillset_id for r in ss_result.scalars().all()]
            if not enabled_skillset_ids:
                return []
            skill_stmt = (
                select(SkillRecord)
                .where(col(SkillRecord.skillset_id).in_(enabled_skillset_ids))
                .where(col(SkillRecord.enabled) == True)  # noqa: E712
            )
            skill_result = await session.execute(skill_stmt)
            return [self._skill_item_from_record(r) for r in skill_result.scalars().all()]

    async def get_enabled_skill(self, skillset_id: str, skill_id: str) -> SkillItem | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(SkillRecord)
                .join(SkillsetRecord, SkillRecord.skillset_id == SkillsetRecord.skillset_id)
                .where(SkillRecord.skill_id == skill_id)
                .where(SkillRecord.skillset_id == skillset_id)
                .where(SkillRecord.enabled == True)  # noqa: E712
                .where(SkillsetRecord.enabled == True)  # noqa: E712
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            return self._skill_item_from_record(record) if record else None

    # ------------------------------------------------------------------
    # Agent plugins
    # ------------------------------------------------------------------

    @staticmethod
    def _plugin_item(record: PluginRecord) -> PluginListItem:
        return PluginListItem(
            plugin_id=record.plugin_id,
            name=record.name,
            package_version=record.package_version,
            description=record.description or "",
            enabled=record.enabled,
            current_revision=record.current_revision,
            package_digest=record.package_digest,
            created_at=record.created_at,
            updated_at=record.updated_at,
            created_by=record.created_by,
            updated_by=record.updated_by,
            diagnostics=record.diagnostics or [],
        )

    @staticmethod
    def _plugin_skill(record: PluginSkillRecord) -> PluginSkillItem:
        return PluginSkillItem(
            plugin_id=record.plugin_id,
            skill_id=record.skill_id,
            portable_name=record.portable_name,
            title=record.title,
            description=record.description or "",
            template=record.template,
            parameters=record.parameters or [],
            triggers=record.triggers or [],
            allowed_tools=record.allowed_tools or [],
            enabled=record.enabled,
            source_path=record.source_path,
            aliases=record.aliases or [],
            mcp_servers=record.mcp_servers or {},
            revision=record.revision,
            package_digest=record.package_digest,
            has_scripts=record.has_scripts,
        )

    @staticmethod
    def _file_info(record: PluginFileRecord, size: int) -> PluginFileInfo:
        return PluginFileInfo(
            path=record.path,
            media_type=record.media_type,
            size=size,
            sha256=record.blob_sha256,
            executable=record.executable,
            etag=f'"{record.blob_sha256}"',
        )

    async def list_plugins(self) -> list[PluginListItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(PluginRecord).order_by(col(PluginRecord.plugin_id)))
            return [self._plugin_item(record) for record in result.scalars().all()]

    async def get_plugin(self, plugin_id: str) -> PluginListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(PluginRecord, plugin_id)
            return self._plugin_item(record) if record else None

    async def publish_plugin(
        self,
        plugin_id: str,
        manifest: dict[str, Any],
        files: list[PluginFile],
        skills: list[PluginSkillItem],
        diagnostics: list[dict[str, Any]],
        package_digest: str,
        created_by: str,
        comment: str | None = None,
        expected_revision: int | None = None,
    ) -> PluginListItem:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = (
                (
                    await session.execute(
                        select(PluginRecord).where(PluginRecord.plugin_id == plugin_id).with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            if expected_revision is not None and (record is None or record.current_revision != expected_revision):
                raise PluginRevisionConflict(plugin_id)
            if record:
                revision = record.current_revision + 1
                original_created_at = record.created_at
                original_created_by = record.created_by
                enabled = record.enabled
                record.name = manifest["name"]
                record.package_version = manifest.get("version")
                record.description = manifest.get("description", "")
                record.manifest = manifest
                record.diagnostics = diagnostics
                record.current_revision = revision
                record.package_digest = package_digest
                record.updated_at = now
                record.updated_by = created_by
            else:
                revision = 1
                original_created_at = now
                original_created_by = created_by
                enabled = True
                record = PluginRecord(
                    plugin_id=plugin_id,
                    name=manifest["name"],
                    package_version=manifest.get("version"),
                    description=manifest.get("description", ""),
                    manifest=manifest,
                    diagnostics=diagnostics,
                    enabled=enabled,
                    current_revision=revision,
                    package_digest=package_digest,
                    created_at=now,
                    updated_at=now,
                    created_by=created_by,
                    updated_by=created_by,
                )
            session.add(record)
            session.add(
                PluginVersionRecord(
                    plugin_id=plugin_id,
                    revision=revision,
                    manifest=manifest,
                    diagnostics=diagnostics,
                    package_digest=package_digest,
                    created_at=now,
                    created_by=created_by,
                    comment=comment,
                )
            )
            for file in files:
                sha256 = hashlib.sha256(file.content).hexdigest()
                if await session.get(PluginBlobRecord, sha256) is None:
                    session.add(PluginBlobRecord(sha256=sha256, content=file.content, size=len(file.content)))
                session.add(
                    PluginFileRecord(
                        plugin_id=plugin_id,
                        revision=revision,
                        path=file.path,
                        blob_sha256=sha256,
                        media_type=file.media_type,
                        executable=file.executable,
                    )
                )
            old_skills = (
                (await session.execute(select(PluginSkillRecord).where(PluginSkillRecord.plugin_id == plugin_id)))
                .scalars()
                .all()
            )
            # Whether a skill is on is the operator's, not the package's, so a
            # republish carries it forward rather than resetting it. A skill
            # this revision introduces starts on (AGT-041).
            previously_enabled = {record.skill_id: record.enabled for record in old_skills}
            for old_skill in old_skills:
                await session.delete(old_skill)
            for skill in skills:
                session.add(
                    PluginSkillRecord(
                        plugin_id=plugin_id,
                        skill_id=skill.skill_id,
                        portable_name=skill.portable_name,
                        title=skill.title,
                        description=skill.description,
                        template=skill.template,
                        parameters=[item.model_dump() for item in skill.parameters],
                        triggers=skill.triggers,
                        allowed_tools=skill.allowed_tools,
                        source_path=skill.source_path,
                        aliases=skill.aliases,
                        mcp_servers=skill.mcp_servers,
                        enabled=previously_enabled.get(skill.skill_id, True),
                        revision=revision,
                        package_digest=package_digest,
                        has_scripts=skill.has_scripts,
                    )
                )
            try:
                await session.commit()
            except IntegrityError as exc:
                # Two first publishes of the same plugin race past the row lock,
                # which cannot lock a row that does not exist yet. The unique
                # constraint decides; the loser is a conflict, not a 500.
                await session.rollback()
                raise PluginRevisionConflict(plugin_id) from exc
        return PluginListItem(
            plugin_id=plugin_id,
            name=manifest["name"],
            package_version=manifest.get("version"),
            description=manifest.get("description", ""),
            enabled=enabled,
            current_revision=revision,
            package_digest=package_digest,
            created_at=original_created_at,
            updated_at=now,
            created_by=original_created_by,
            updated_by=created_by,
            diagnostics=diagnostics,
        )

    async def set_plugin_enabled(self, plugin_id: str, enabled: bool, updated_by: str) -> PluginListItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(PluginRecord, plugin_id)
            if not record:
                return None
            record.enabled = enabled
            record.updated_at = datetime.now(tz=UTC).isoformat()
            record.updated_by = updated_by
            session.add(record)
            result = self._plugin_item(record)
            await session.commit()
            return result

    async def delete_plugin(self, plugin_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(PluginRecord, plugin_id)
            if not record:
                return False
            models: tuple[Any, ...] = (
                PluginSkillRecord,
                PluginFileRecord,
                PluginVersionRecord,
            )
            referenced = set(
                (
                    await session.execute(
                        select(PluginFileRecord.blob_sha256).where(PluginFileRecord.plugin_id == plugin_id)
                    )
                )
                .scalars()
                .all()
            )
            for model in models:
                result = await session.execute(select(model).where(model.plugin_id == plugin_id))
                for item in result.scalars().all():
                    await session.delete(item)
            await session.delete(record)
            await session.flush()
            # Blobs are content-addressed and shared between revisions and
            # plugins, so they are collected here rather than cascaded: without
            # this every deleted package's content stayed in the database.
            for sha256 in referenced:
                still_used = (
                    await session.execute(
                        select(PluginFileRecord.plugin_id).where(PluginFileRecord.blob_sha256 == sha256).limit(1)
                    )
                ).first()
                if still_used is None:
                    blob = await session.get(PluginBlobRecord, sha256)
                    if blob is not None:
                        await session.delete(blob)
            await session.commit()
            return True

    async def list_plugin_versions(self, plugin_id: str) -> list[PluginVersion]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(
                select(PluginVersionRecord)
                .where(PluginVersionRecord.plugin_id == plugin_id)
                .order_by(col(PluginVersionRecord.revision).desc())
            )
            return [
                PluginVersion(
                    plugin_id=row.plugin_id,
                    revision=row.revision,
                    manifest=row.manifest,
                    package_digest=row.package_digest,
                    created_at=row.created_at,
                    created_by=row.created_by,
                    comment=row.comment,
                    diagnostics=row.diagnostics or [],
                )
                for row in result.scalars().all()
            ]

    async def _plugin_revision(self, session: AsyncSession, plugin_id: str, revision: int | None) -> int | None:
        if revision is not None:
            return revision
        plugin = await session.get(PluginRecord, plugin_id)
        return plugin.current_revision if plugin else None

    async def list_plugin_files(self, plugin_id: str, revision: int | None = None) -> list[PluginFileInfo]:
        async with AsyncSession(_get_engine()) as session:
            resolved = await self._plugin_revision(session, plugin_id, revision)
            if resolved is None:
                return []
            result = await session.execute(
                select(PluginFileRecord, PluginBlobRecord.size)
                .join(PluginBlobRecord, PluginFileRecord.blob_sha256 == PluginBlobRecord.sha256)
                .where(PluginFileRecord.plugin_id == plugin_id)
                .where(PluginFileRecord.revision == resolved)
                .order_by(col(PluginFileRecord.path))
            )
            return [self._file_info(record, size) for record, size in result.all()]

    async def read_plugin_files(
        self, plugin_id: str, revision: int | None = None, paths: list[str] | None = None
    ) -> list[PluginFile]:
        """Read a revision's files in one statement.

        ``paths`` selects a subset; omitting it reads the whole revision. One
        query, because the callers that need many files -- download, restore,
        version projection -- were issuing one per path.
        """
        if paths is not None and not paths:
            return []
        async with AsyncSession(_get_engine()) as session:
            resolved = await self._plugin_revision(session, plugin_id, revision)
            if resolved is None:
                return []
            statement = (
                select(PluginFileRecord, PluginBlobRecord)
                .join(PluginBlobRecord, PluginFileRecord.blob_sha256 == PluginBlobRecord.sha256)
                .where(PluginFileRecord.plugin_id == plugin_id)
                .where(PluginFileRecord.revision == resolved)
                .order_by(col(PluginFileRecord.path))
            )
            if paths is not None:
                statement = statement.where(col(PluginFileRecord.path).in_(paths))
            return [
                PluginFile(
                    path=record.path,
                    content=blob.content,
                    media_type=record.media_type,
                    executable=record.executable,
                )
                for record, blob in (await session.execute(statement)).all()
            ]

    async def read_plugin_file(self, plugin_id: str, path: str, revision: int | None = None) -> PluginFile | None:
        async with AsyncSession(_get_engine()) as session:
            resolved = await self._plugin_revision(session, plugin_id, revision)
            if resolved is None:
                return None
            statement = (
                select(PluginFileRecord, PluginBlobRecord)
                .join(PluginBlobRecord, PluginFileRecord.blob_sha256 == PluginBlobRecord.sha256)
                .where(PluginFileRecord.plugin_id == plugin_id)
                .where(PluginFileRecord.revision == resolved)
                .where(PluginFileRecord.path == path)
            )
            result = (await session.execute(statement)).first()
            if not result:
                return None
            record, blob = result
            return PluginFile(
                path=path, content=blob.content, media_type=record.media_type, executable=record.executable
            )

    async def list_enabled_plugin_skills(self) -> list[PluginSkillItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(
                select(PluginSkillRecord)
                .join(PluginRecord, PluginSkillRecord.plugin_id == PluginRecord.plugin_id)
                .where(PluginSkillRecord.enabled == True)  # noqa: E712
                .where(PluginRecord.enabled == True)  # noqa: E712
            )
            return [self._plugin_skill(record) for record in result.scalars().all()]

    async def list_plugin_skills(self, plugin_id: str) -> list[PluginSkillItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(
                select(PluginSkillRecord)
                .where(PluginSkillRecord.plugin_id == plugin_id)
                .order_by(col(PluginSkillRecord.skill_id))
            )
            return [self._plugin_skill(record) for record in result.scalars().all()]

    async def get_plugin_skill(self, plugin_id: str, skill_id: str) -> PluginSkillItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(PluginSkillRecord, (plugin_id, skill_id))
            return self._plugin_skill(record) if record else None

    async def get_enabled_plugin_skill(self, plugin_id: str, skill_id: str) -> PluginSkillItem | None:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(
                select(PluginSkillRecord)
                .join(PluginRecord, PluginSkillRecord.plugin_id == PluginRecord.plugin_id)
                .where(PluginSkillRecord.plugin_id == plugin_id)
                .where(PluginSkillRecord.skill_id == skill_id)
                .where(PluginSkillRecord.enabled == True)  # noqa: E712
                .where(PluginRecord.enabled == True)  # noqa: E712
            )
            record = result.scalars().first()
            return self._plugin_skill(record) if record else None

    async def set_plugin_skill_enabled(self, plugin_id: str, skill_id: str, enabled: bool) -> PluginSkillItem | None:
        """Turn one indexed skill on or off without republishing the package."""
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(PluginSkillRecord, (plugin_id, skill_id))
            if not record:
                return None
            record.enabled = enabled
            session.add(record)
            result = self._plugin_skill(record)
            await session.commit()
            return result

    async def read_plugin_blob(self, plugin_id: str, sha256: str) -> PluginFile | None:
        """Read a blob this plugin already stores, by digest.

        Scoped to the plugin: a staged package may only retain content that is
        already part of one of its own revisions, never an arbitrary blob whose
        digest the caller happens to know.
        """
        async with AsyncSession(_get_engine()) as session:
            result = (
                await session.execute(
                    select(PluginFileRecord, PluginBlobRecord)
                    .join(PluginBlobRecord, PluginFileRecord.blob_sha256 == PluginBlobRecord.sha256)
                    .where(PluginFileRecord.plugin_id == plugin_id)
                    .where(PluginFileRecord.blob_sha256 == sha256)
                    .limit(1)
                )
            ).first()
            if not result:
                return None
            record, blob = result
            return PluginFile(
                path=record.path, content=blob.content, media_type=record.media_type, executable=record.executable
            )

    # ------------------------------------------------------------------
    # Query history
    # ------------------------------------------------------------------

    async def save_query_history(self, user_id: str, query: str) -> QueryHistoryItem:
        """Append a query execution to the user's history."""
        history_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        record = QueryHistoryRecord(
            history_id=history_id,
            user_id=user_id,
            query=query,
            executed_at=now,
        )
        async with AsyncSession(_get_engine()) as session:
            session.add(record)
            await session.commit()
        return QueryHistoryItem(
            history_id=history_id,
            user_id=user_id,
            query=query,
            executed_at=now,
        )

    async def list_query_history(self, user_id: str, page: int, per_page: int) -> tuple[list[QueryHistoryItem], int]:
        """Return a paginated page of query history (newest first) and the total count."""
        async with AsyncSession(_get_engine()) as session:
            count_stmt = (
                select(func.count()).select_from(QueryHistoryRecord).where(col(QueryHistoryRecord.user_id) == user_id)
            )
            count_result = await session.execute(count_stmt)
            total = count_result.scalar() or 0

            offset = (page - 1) * per_page
            page_stmt = (
                select(QueryHistoryRecord)
                .where(col(QueryHistoryRecord.user_id) == user_id)
                .order_by(col(QueryHistoryRecord.id).desc())
                .offset(offset)
                .limit(per_page)
            )
            page_result = await session.execute(page_stmt)
            rows = page_result.scalars().all()
            return [
                QueryHistoryItem(
                    history_id=r.history_id,
                    user_id=r.user_id,
                    query=r.query,
                    executed_at=r.executed_at,
                )
                for r in rows
            ], total

    async def get_query_history_item(self, user_id: str, history_id: str) -> QueryHistoryItem | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(QueryHistoryRecord).where(
                col(QueryHistoryRecord.user_id) == user_id,
                col(QueryHistoryRecord.history_id) == history_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return QueryHistoryItem(
                history_id=row.history_id,
                user_id=row.user_id,
                query=row.query,
                executed_at=row.executed_at,
            )

    # ------------------------------------------------------------------
    # Roles (user-defined, versioned)
    # ------------------------------------------------------------------

    async def list_roles(self) -> list[RoleItem]:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(select(RoleRecord))
            rows = result.scalars().all()
            return [
                RoleItem(
                    role_id=r.role_id,
                    name=r.name,
                    description=r.description,
                    permissions=r.permissions,
                    current_version=r.current_version,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    created_by=r.created_by,
                    updated_by=r.updated_by,
                )
                for r in rows
            ]

    async def get_role(self, role_id: str) -> RoleItem | None:
        async with AsyncSession(_get_engine()) as session:
            r = await session.get(RoleRecord, role_id)
            if not r:
                return None
            return RoleItem(
                role_id=r.role_id,
                name=r.name,
                description=r.description,
                permissions=r.permissions,
                current_version=r.current_version,
                created_at=r.created_at,
                updated_at=r.updated_at,
                created_by=r.created_by,
                updated_by=r.updated_by,
            )

    async def get_role_by_name(self, name: str) -> RoleItem | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(RoleRecord).where(col(RoleRecord.name) == name)
            result = await session.execute(stmt)
            r = result.scalars().first()
            if not r:
                return None
            return RoleItem(
                role_id=r.role_id,
                name=r.name,
                description=r.description,
                permissions=r.permissions,
                current_version=r.current_version,
                created_at=r.created_at,
                updated_at=r.updated_at,
                created_by=r.created_by,
                updated_by=r.updated_by,
            )

    async def create_role(
        self,
        name: str,
        description: str,
        permissions: list[str],
        created_by: str,
    ) -> RoleItem:
        role_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            session.add(
                RoleRecord(
                    role_id=role_id,
                    name=name,
                    description=description,
                    permissions=permissions,
                    current_version=version,
                    created_at=now,
                    updated_at=now,
                    created_by=created_by,
                    updated_by=created_by,
                )
            )
            session.add(
                RoleVersionRecord(
                    role_id=role_id,
                    version=version,
                    name=name,
                    description=description,
                    permissions=permissions,
                    created_at=now,
                    created_by=created_by,
                )
            )
            await session.commit()
        return RoleItem(
            role_id=role_id,
            name=name,
            description=description,
            permissions=permissions,
            current_version=version,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            updated_by=created_by,
        )

    async def update_role(
        self,
        role_id: str,
        name: str,
        description: str,
        permissions: list[str],
        updated_by: str,
        comment: str | None = None,
    ) -> RoleItem | None:
        async with AsyncSession(_get_engine()) as session:
            r = await session.get(RoleRecord, role_id)
            if not r:
                return None
            now = datetime.now(tz=UTC).isoformat()
            version = r.current_version + 1
            r.name = name
            r.description = description
            r.permissions = permissions
            r.current_version = version
            r.updated_at = now
            r.updated_by = updated_by
            session.add(r)
            session.add(
                RoleVersionRecord(
                    role_id=role_id,
                    version=version,
                    name=name,
                    description=description,
                    permissions=permissions,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            await session.commit()
            await session.refresh(r)
            return RoleItem(
                role_id=r.role_id,
                name=r.name,
                description=r.description,
                permissions=r.permissions,
                current_version=r.current_version,
                created_at=r.created_at,
                updated_at=r.updated_at,
                created_by=r.created_by,
                updated_by=r.updated_by,
            )

    async def delete_role(self, role_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            r = await session.get(RoleRecord, role_id)
            if not r:
                return False
            stmt = select(RoleVersionRecord).where(col(RoleVersionRecord.role_id) == role_id)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                await session.delete(row)
            await session.delete(r)
            await session.commit()
            return True

    async def list_role_versions(self, role_id: str) -> list[RoleVersion]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(RoleVersionRecord)
                .where(col(RoleVersionRecord.role_id) == role_id)
                .order_by(col(RoleVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            return [
                RoleVersion(
                    role_id=r.role_id,
                    name=r.name,
                    description=r.description,
                    permissions=r.permissions,
                    version=r.version,
                    created_at=r.created_at,
                    created_by=r.created_by,
                    comment=r.comment,
                )
                for r in result.scalars().all()
            ]

    async def get_role_version(self, role_id: str, version: int) -> RoleVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(RoleVersionRecord)
                .where(col(RoleVersionRecord.role_id) == role_id)
                .where(col(RoleVersionRecord.version) == version)
            )
            result = await session.execute(stmt)
            r = result.scalars().first()
            if not r:
                return None
            return RoleVersion(
                role_id=r.role_id,
                name=r.name,
                description=r.description,
                permissions=r.permissions,
                version=r.version,
                created_at=r.created_at,
                created_by=r.created_by,
                comment=r.comment,
            )

    # ------------------------------------------------------------------
    # Model profiles
    # ------------------------------------------------------------------

    async def list_model_profiles(self, *, enabled_only: bool = False) -> list[ModelProfileItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ModelProfileRecord)
            if enabled_only:
                stmt = stmt.where(col(ModelProfileRecord.enabled).is_(True))
            stmt = stmt.order_by(col(ModelProfileRecord.is_default).desc(), col(ModelProfileRecord.name))
            result = await session.execute(stmt)
            return [_model_profile_from_record(row) for row in result.scalars().all()]

    async def get_model_profile(self, profile_id: str) -> ModelProfileItem | None:
        async with AsyncSession(_get_engine()) as session:
            row = await session.get(ModelProfileRecord, profile_id)
            return _model_profile_from_record(row) if row else None

    @staticmethod
    def _model_profile_config(data: dict[str, Any]) -> dict[str, Any]:
        return ModelProfileConfig.model_validate(
            {
                "primary": data["primary"],
                "economy": data["economy"],
                "stage_overrides": data.get("stage_overrides") or {},
                "run_cost_budget_usd": data["run_cost_budget_usd"],
            }
        ).model_dump(mode="json")

    async def create_model_profile(self, data: dict[str, Any], created_by: str) -> ModelProfileItem:
        profile_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        config = self._model_profile_config(data)
        async with AsyncSession(_get_engine()) as session:
            existing = (await session.execute(select(ModelProfileRecord).with_for_update())).scalars().all()
            enabled = bool(data.get("enabled", True))
            is_default = bool(data.get("is_default", False))
            if is_default and not enabled:
                raise ValueError("the default model profile must be enabled")
            if enabled and not any(row.enabled for row in existing):
                is_default = True
            if is_default:
                for row in existing:
                    if row.is_default:
                        row.is_default = False
                        row.current_version += 1
                        row.updated_at = now
                        row.updated_by = created_by
                        session.add(row)
                        session.add(
                            ModelProfileVersionRecord(
                                profile_id=row.profile_id,
                                version=row.current_version,
                                name=row.name,
                                description=row.description,
                                enabled=row.enabled,
                                is_default=False,
                                config=row.config,
                                created_at=now,
                                created_by=created_by,
                                comment=f"Default changed to {data['name']}",
                            )
                        )
            record = ModelProfileRecord(
                profile_id=profile_id,
                name=str(data["name"]),
                description=str(data.get("description") or ""),
                enabled=enabled,
                is_default=is_default,
                config=config,
                current_version=1,
                created_at=now,
                updated_at=now,
                created_by=created_by,
                updated_by=created_by,
            )
            session.add(record)
            session.add(
                ModelProfileVersionRecord(
                    profile_id=profile_id,
                    version=1,
                    name=record.name,
                    description=record.description,
                    enabled=record.enabled,
                    is_default=record.is_default,
                    config=config,
                    created_at=now,
                    created_by=created_by,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("a model profile with that name already exists") from exc
            await session.refresh(record)
            return _model_profile_from_record(record)

    async def update_model_profile(
        self,
        profile_id: str,
        data: dict[str, Any],
        updated_by: str,
        comment: str | None = None,
    ) -> ModelProfileItem | None:
        config = self._model_profile_config(data)
        async with AsyncSession(_get_engine()) as session:
            rows = (await session.execute(select(ModelProfileRecord).with_for_update())).scalars().all()
            record = next((row for row in rows if row.profile_id == profile_id), None)
            if record is None:
                return None
            now = datetime.now(tz=UTC).isoformat()
            enabled = bool(data.get("enabled", True))
            is_default = bool(data.get("is_default", False))
            if is_default and not enabled:
                raise ValueError("the default model profile must be enabled")
            other_enabled = [row for row in rows if row.profile_id != profile_id and row.enabled]
            if record.is_default and not is_default and (enabled or other_enabled):
                raise ValueError("select another default profile before changing this default")
            if enabled and not any(row.enabled for row in rows if row.profile_id != profile_id):
                is_default = True
            if is_default:
                for row in rows:
                    if row.profile_id != profile_id and row.is_default:
                        row.is_default = False
                        row.current_version += 1
                        row.updated_at = now
                        row.updated_by = updated_by
                        session.add(row)
                        session.add(
                            ModelProfileVersionRecord(
                                profile_id=row.profile_id,
                                version=row.current_version,
                                name=row.name,
                                description=row.description,
                                enabled=row.enabled,
                                is_default=False,
                                config=row.config,
                                created_at=now,
                                created_by=updated_by,
                                comment=f"Default changed to {data['name']}",
                            )
                        )
            version = record.current_version + 1
            record.name = str(data["name"])
            record.description = str(data.get("description") or "")
            record.enabled = enabled
            record.is_default = is_default
            record.config = config
            record.current_version = version
            record.updated_at = now
            record.updated_by = updated_by
            session.add(record)
            session.add(
                ModelProfileVersionRecord(
                    profile_id=profile_id,
                    version=version,
                    name=record.name,
                    description=record.description,
                    enabled=enabled,
                    is_default=is_default,
                    config=config,
                    created_at=now,
                    created_by=updated_by,
                    comment=comment,
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("a model profile with that name already exists") from exc
            await session.refresh(record)
            return _model_profile_from_record(record)

    async def delete_model_profile(self, profile_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            rows = (await session.execute(select(ModelProfileRecord).with_for_update())).scalars().all()
            record = next((row for row in rows if row.profile_id == profile_id), None)
            if record is None:
                return False
            if record.is_default and any(row.enabled for row in rows if row.profile_id != profile_id):
                raise ValueError("select another default profile before deleting this default")
            await session.execute(
                delete(ModelProfileVersionRecord).where(col(ModelProfileVersionRecord.profile_id) == profile_id)
            )
            await session.delete(record)
            await session.commit()
            return True

    async def list_model_profile_versions(self, profile_id: str) -> list[ModelProfileVersion]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ModelProfileVersionRecord)
                .where(col(ModelProfileVersionRecord.profile_id) == profile_id)
                .order_by(col(ModelProfileVersionRecord.version).desc())
            )
            result = await session.execute(stmt)
            return [_model_profile_version_from_record(row) for row in result.scalars().all()]

    async def get_model_profile_version(self, profile_id: str, version: int) -> ModelProfileVersion | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ModelProfileVersionRecord).where(
                col(ModelProfileVersionRecord.profile_id) == profile_id,
                col(ModelProfileVersionRecord.version) == version,
            )
            row = (await session.execute(stmt)).scalars().first()
            return _model_profile_version_from_record(row) if row else None

    # ------------------------------------------------------------------
    # Chat sessions
    # ------------------------------------------------------------------

    async def list_chat_sessions(self, user_id: str, limit: int) -> list[ChatSessionItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == user_id,
                    col(ChatSessionRecord.origin) == "interactive",
                )
                .order_by(col(ChatSessionRecord.updated_at).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [_chat_session_from_sql_record(r) for r in result.scalars().all()]

    async def claim_chat_session_for_retirement(
        self,
        user_id: str,
        thread_id: str,
        expected_updated_at: str,
    ) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            # A conditional UPDATE, not read-then-write: the row must not have
            # moved since the sweep listed it, and the database is the only
            # thing that can decide that without a race.
            stmt = (
                update(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == user_id,
                    col(ChatSessionRecord.thread_id) == thread_id,
                    col(ChatSessionRecord.updated_at) == expected_updated_at,
                )
                .values(retiring_at=now)
            )
            result = await session.execute(stmt)
            await session.commit()
            return bool(result.rowcount)

    async def list_idle_chat_sessions(self, idle_before: str, limit: int) -> list[IdleChatSession]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.origin) == "interactive",
                    col(ChatSessionRecord.updated_at) < idle_before,
                )
                # Oldest first: a sweep bounded by `limit` should collect the
                # sessions that have been idle longest, not an arbitrary page.
                .order_by(col(ChatSessionRecord.updated_at).asc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [
                IdleChatSession(user_id=r.user_id, thread_id=r.thread_id, updated_at=r.updated_at)
                for r in result.scalars().all()
            ]

    async def get_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _chat_session_from_sql_record(row)

    async def create_chat_session(
        self,
        user_id: str,
        title: str,
        origin: str = "interactive",
        scheduled_chat_id: str | None = None,
        model_profile_id: str | None = None,
    ) -> ChatSessionItem:
        thread_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = ChatSessionRecord(
                user_id=user_id,
                thread_id=thread_id,
                title=title,
                created_at=now,
                updated_at=now,
                origin=origin,
                scheduled_chat_id=scheduled_chat_id,
                run_status="running" if origin != "interactive" else None,
                run_errors=[],
                model_profile_id=model_profile_id,
            )
            session.add(record)
            await session.commit()
            return ChatSessionItem(
                thread_id=thread_id,
                title=title,
                created_at=now,
                updated_at=now,
                origin=origin if origin in ("interactive", "scheduled", "workflow") else "interactive",
                scheduled_chat_id=scheduled_chat_id,
                run_status="running" if origin != "interactive" else None,
                run_errors=[],
                model_profile_id=model_profile_id,
            )

    async def list_scheduled_chat_sessions(
        self,
        user_id: str,
        scheduled_chat_id: str,
        limit: int,
    ) -> list[ChatSessionItem]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == user_id,
                    col(ChatSessionRecord.scheduled_chat_id) == scheduled_chat_id,
                )
                .order_by(col(ChatSessionRecord.updated_at).desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [_chat_session_from_sql_record(r) for r in result.scalars().all()]

    async def touch_chat_session(self, user_id: str, thread_id: str) -> ChatSessionItem | None:
        return await self._update_unretired_chat_session(user_id, thread_id, {})

    async def _update_unretired_chat_session(
        self,
        user_id: str,
        thread_id: str,
        values: dict[str, Any],
    ) -> ChatSessionItem | None:
        """Update a session and stamp its activity, unless it is being retired.

        One conditional statement, never read-then-write. The reaper's claim can
        land between a SELECT and its UPDATE without moving ``updated_at``, so a
        guard evaluated in Python commits anyway, and a turn proceeds against a
        session whose checkpoint and sandbox are already being deleted. The
        database has to evaluate ``retiring_at IS NULL`` as part of the write.

        Returns None when the row is missing or claimed -- indistinguishable on
        purpose, since both mean the conversation is gone as far as callers are
        concerned.
        """
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                update(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == user_id,
                    col(ChatSessionRecord.thread_id) == thread_id,
                    col(ChatSessionRecord.retiring_at).is_(None),
                )
                .values(updated_at=now, **values)
                .returning(ChatSessionRecord)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            item = _chat_session_from_sql_record(row) if row else None
            await session.commit()
            return item

    async def complete_chat_session_run(
        self,
        user_id: str,
        thread_id: str,
        status: str,
        errors: list[str],
    ) -> ChatSessionItem | None:
        return await self._update_unretired_chat_session(
            user_id,
            thread_id,
            {"run_status": status, "run_errors": list(errors)},
        )

    async def update_chat_session_title(self, user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        return await self._update_unretired_chat_session(user_id, thread_id, {"title": title})

    async def update_chat_session_model_profile(
        self, user_id: str, thread_id: str, model_profile_id: str | None
    ) -> ChatSessionItem | None:
        return await self._update_unretired_chat_session(user_id, thread_id, {"model_profile_id": model_profile_id})

    async def delete_chat_session(self, user_id: str, thread_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            stmt = delete(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            if result.rowcount > 0:
                # A turn log is only reachable through its session; deleting one
                # without the other leaves rows nothing will ever look for.
                turn_ids = (
                    (
                        await session.execute(
                            select(col(ChatTurnRecord.turn_id)).where(
                                col(ChatTurnRecord.user_id) == user_id,
                                col(ChatTurnRecord.thread_id) == thread_id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if turn_ids:
                    await session.execute(
                        delete(ChatTurnEventRecord).where(col(ChatTurnEventRecord.turn_id).in_(turn_ids))
                    )
                    await session.execute(delete(ChatTurnRecord).where(col(ChatTurnRecord.turn_id).in_(turn_ids)))
            await session.commit()
            return result.rowcount > 0

    # ------------------------------------------------------------------
    # Chat turn event log
    # ------------------------------------------------------------------
    async def admit_chat_turn(
        self,
        user_id: str,
        thread_id: str,
        message_id: str,
        text_id: str,
        idempotency_key: str,
        command: ChatTurnCommand,
    ) -> ChatTurnAdmission:
        now = datetime.now(tz=UTC)
        now_iso = now.isoformat()
        async with AsyncSession(_get_engine()) as session:
            # A repeat resolves to the immutable command already admitted.
            already = (
                (
                    await session.execute(
                        select(ChatTurnRecord).where(
                            col(ChatTurnRecord.user_id) == user_id,
                            col(ChatTurnRecord.thread_id) == thread_id,
                            col(ChatTurnRecord.idempotency_key) == idempotency_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if already is not None:
                return resolve_chat_turn_for_key(_chat_turn_from_record(already))

            # Admission and creation commit together, so a delete cannot slip
            # between them: the session is closed to new turns the moment it is
            # claimed, and this update is what observes that.
            admitted = await session.execute(
                update(ChatSessionRecord)
                .where(
                    col(ChatSessionRecord.user_id) == user_id,
                    col(ChatSessionRecord.thread_id) == thread_id,
                    col(ChatSessionRecord.retiring_at).is_(None),
                )
                .values(updated_at=now_iso)
            )
            if admitted.rowcount == 0:
                await session.rollback()
                return ChatTurnAdmission(outcome="retired")

            # Retire a turn whose lease has lapsed, in the same transaction as
            # the insert. Its producer is gone, so it must not keep holding the
            # thread -- and doing it here means a turn that is still alive
            # cannot be retired by us at all.
            await session.execute(
                update(ChatTurnRecord)
                .where(
                    col(ChatTurnRecord.user_id) == user_id,
                    col(ChatTurnRecord.thread_id) == thread_id,
                    col(ChatTurnRecord.status) == "running",
                    col(ChatTurnRecord.expires_at) <= now_iso,
                )
                .values(status="failed", updated_at=now_iso)
            )

            values = {
                "turn_id": generate_report_id(),
                "user_id": user_id,
                "thread_id": thread_id,
                "message_id": message_id,
                "text_id": text_id,
                "idempotency_key": idempotency_key,
                "command": command.model_dump(mode="json"),
                "status": "running",
                "created_at": now_iso,
                "updated_at": now_iso,
                "expires_at": chat_turn_lease_expiry(now),
            }
            session.add(ChatTurnRecord(**values))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            else:
                # Built from the values we wrote: commit expires the ORM
                # object's attributes, so reading them back is a second round
                # trip after the row is already durable.
                return ChatTurnAdmission(outcome="created", turn=ChatTurnItem.model_validate(values))

            # The insert lost. Only two things can have taken it, and the
            # idempotency key was already checked above, so this is the thread
            # being held -- either by a turn that is running or by one admitted
            # concurrently under the same key.
            concurrent = (
                (
                    await session.execute(
                        select(ChatTurnRecord).where(
                            col(ChatTurnRecord.user_id) == user_id,
                            col(ChatTurnRecord.thread_id) == thread_id,
                            col(ChatTurnRecord.idempotency_key) == idempotency_key,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if concurrent is not None:
                return resolve_chat_turn_for_key(_chat_turn_from_record(concurrent))
            return ChatTurnAdmission(outcome="busy")

    async def get_active_chat_turn(self, user_id: str, thread_id: str) -> ChatTurnItem | None:
        now_iso = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = (
                (
                    await session.execute(
                        select(ChatTurnRecord)
                        .where(
                            col(ChatTurnRecord.user_id) == user_id,
                            col(ChatTurnRecord.thread_id) == thread_id,
                            col(ChatTurnRecord.status) == "running",
                            # An expired running turn has no producer left to finish
                            # it, so there is nothing to reconnect to.
                            col(ChatTurnRecord.expires_at) > now_iso,
                        )
                        .order_by(col(ChatTurnRecord.created_at).desc())
                    )
                )
                .scalars()
                .first()
            )
            return _chat_turn_from_record(record) if record else None

    async def get_chat_turn(self, turn_id: str, user_id: str | None = None) -> ChatTurnItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ChatTurnRecord, turn_id)
            if record is None or (user_id is not None and record.user_id != user_id):
                return None
            return _chat_turn_from_record(record)

    async def append_chat_turn_events(self, turn_id: str, parts_json: str) -> int | None:
        validate_chat_turn_batch(parts_json)
        for _ in range(_CHAT_TURN_SEQ_ALLOCATION_ATTEMPTS):
            async with AsyncSession(_get_engine()) as session:
                # The turn row is the allocation lock. Taking it means two
                # producers -- the coordinating turn and a distributed plan step,
                # or two steps of one batch -- serialize here rather than both
                # reading the same MAX and picking the same number. On SQLite the
                # dialect renders no FOR UPDATE and the write lock does the same
                # job, which is why the retry below is kept as well.
                turn = (
                    await session.execute(
                        select(ChatTurnRecord).where(col(ChatTurnRecord.turn_id) == turn_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if turn is None:
                    # Deleted underneath the producer. Writing anyway leaves rows
                    # no reader can reach and no sweep collects by header.
                    return None
                highest = (
                    await session.execute(
                        select(func.max(col(ChatTurnEventRecord.seq))).where(
                            col(ChatTurnEventRecord.turn_id) == turn_id
                        )
                    )
                ).scalar()
                seq = int(highest or 0) + 1
                validate_chat_turn_seq(seq)
                session.add(
                    ChatTurnEventRecord(
                        turn_id=turn_id,
                        seq=seq,
                        parts_json=parts_json,
                        created_at=datetime.now(tz=UTC).isoformat(),
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    # Another producer took this number between the read and the
                    # commit. Nothing has been written, so re-reading the maximum
                    # is the whole recovery.
                    await session.rollback()
                    continue
                return seq
        raise RuntimeError(f"could not allocate a chat turn event sequence for {turn_id}")

    async def put_chat_turn_payload(self, turn_id: str, payload_id: str, body: str) -> None:
        async with AsyncSession(_get_engine()) as session:
            existing = await session.get(ChatTurnPayloadRecord, (turn_id, payload_id))
            if existing is not None:
                existing.body = body
                session.add(existing)
            else:
                session.add(
                    ChatTurnPayloadRecord(
                        turn_id=turn_id,
                        payload_id=payload_id,
                        body=body,
                        created_at=datetime.now(tz=UTC).isoformat(),
                    )
                )
            await session.commit()

    async def get_chat_turn_payload(self, turn_id: str, payload_id: str) -> str | None:
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ChatTurnPayloadRecord, (turn_id, payload_id))
            return record.body if record is not None else None

    async def read_chat_turn_events(self, turn_id: str, after_seq: int, limit: int) -> ChatTurnEventPage | None:
        async with AsyncSession(_get_engine()) as session:
            # Events before status, so a status read cannot overtake the final
            # batches and end the stream mid-answer.
            rows = (
                (
                    await session.execute(
                        select(ChatTurnEventRecord)
                        .where(
                            col(ChatTurnEventRecord.turn_id) == turn_id,
                            col(ChatTurnEventRecord.seq) > after_seq,
                        )
                        .order_by(col(ChatTurnEventRecord.seq))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            record = await session.get(ChatTurnRecord, turn_id)
            if record is None:
                return None
            batches: list[ChatTurnEventBatch] = []
            expected = after_seq + 1
            for row in rows:
                # Truncate at the first gap rather than skipping it: a reader
                # that advanced past a missing batch would never come back for it.
                if row.seq != expected:
                    break
                batches.append(ChatTurnEventBatch(seq=row.seq, parts_json=row.parts_json))
                expected += 1
            return ChatTurnEventPage(turn=_chat_turn_from_record(record), batches=batches)

    async def request_chat_turn_cancel(self, turn_id: str, user_id: str) -> ChatTurnItem | None:
        async with AsyncSession(_get_engine()) as session:
            record = (
                (
                    await session.execute(
                        select(ChatTurnRecord).where(
                            col(ChatTurnRecord.turn_id) == turn_id,
                            col(ChatTurnRecord.user_id) == user_id,
                            col(ChatTurnRecord.status) == "running",
                        )
                    )
                )
                .scalars()
                .first()
            )
            if record is None:
                return None
            record.cancel_requested = True
            record.updated_at = datetime.now(tz=UTC).isoformat()
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _chat_turn_from_record(record)

    async def finish_chat_turn(
        self,
        turn_id: str,
        status: Literal["completed", "failed", "canceled", "expired"],
        last_seq: int,
    ) -> ChatTurnItem | None:
        now = datetime.now(tz=UTC)
        async with AsyncSession(_get_engine()) as session:
            # Conditional in the statement, not a read followed by a write: two
            # writers can race here (the turn closing itself, and the workflow's
            # fallback closing it after a timeout), and a read-then-write lets
            # both observe `running` and both commit.
            result = await session.execute(
                update(ChatTurnRecord)
                .where(
                    col(ChatTurnRecord.turn_id) == turn_id,
                    col(ChatTurnRecord.status) == "running",
                )
                .values(
                    status=status,
                    last_seq=last_seq,
                    updated_at=now.isoformat(),
                    expires_at=(now + timedelta(seconds=settings.CHAT_TURN_RETENTION_SECONDS)).isoformat(),
                )
            )
            await session.commit()
            record = await session.get(ChatTurnRecord, turn_id)
            if record is None:
                return None
            if result.rowcount == 0:
                # Already terminal. The caller is handed what is actually
                # recorded rather than what it tried to write, so a late writer
                # can tell it lost instead of believing it won.
                logger.info(
                    "Chat turn was already finished",
                    extra={"turn_id": turn_id, "recorded": record.status, "attempted": status},
                )
            return _chat_turn_from_record(record)

    async def delete_chat_turn(self, turn_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            result = await session.execute(delete(ChatTurnRecord).where(col(ChatTurnRecord.turn_id) == turn_id))
            # Batches are deleted whether or not the header was still there.
            # A producer that kept writing after its conversation was deleted is
            # exactly the case that leaves headerless batches, and gating this
            # on the header would skip the only rows worth collecting.
            events = await session.execute(
                delete(ChatTurnEventRecord).where(col(ChatTurnEventRecord.turn_id) == turn_id)
            )
            payloads = await session.execute(
                delete(ChatTurnPayloadRecord).where(col(ChatTurnPayloadRecord.turn_id) == turn_id)
            )
            await session.commit()
            return result.rowcount > 0 or events.rowcount > 0 or payloads.rowcount > 0

    async def list_expired_chat_turns(self, expired_before: str, limit: int) -> list[str]:
        async with AsyncSession(_get_engine()) as session:
            rows = (
                (
                    await session.execute(
                        select(ChatTurnRecord)
                        .where(col(ChatTurnRecord.expires_at) <= expired_before)
                        .order_by(col(ChatTurnRecord.expires_at))
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [row.turn_id for row in rows]

    # ------------------------------------------------------------------
    # Action confirmations
    # ------------------------------------------------------------------

    async def create_action_confirmation(self, confirmation: ActionConfirmation) -> ActionConfirmation:
        for attempt in range(2):
            async with AsyncSession(_get_engine()) as session:
                record = ActionConfirmationRecord(**confirmation.model_dump())
                session.add(record)
                try:
                    await session.commit()
                    return confirmation
                except IntegrityError:
                    await session.rollback()
            # Concurrent create race: the dedup index rejected our insert.
            # Re-fetch the existing pending confirmation that beat us to it.
            existing = await self.find_action_confirmation_grant(
                user_id=confirmation.user_id,
                source=confirmation.source,
                session_key=confirmation.session_key,
                tool_name=confirmation.tool_name,
                action=confirmation.action,
                resource_type=confirmation.resource_type,
                resource_id=confirmation.resource_id,
                arguments_hash=confirmation.arguments_hash,
                statuses=("pending",),
            )
            if existing is not None:
                return existing
            if attempt == 0:
                await self._expire_matching_pending_action_confirmation(confirmation)
                continue
            break
        return confirmation

    async def _expire_matching_pending_action_confirmation(self, confirmation: ActionConfirmation) -> bool:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                update(ActionConfirmationRecord)
                .where(
                    col(ActionConfirmationRecord.user_id) == confirmation.user_id,
                    col(ActionConfirmationRecord.source) == confirmation.source,
                    col(ActionConfirmationRecord.session_key) == confirmation.session_key,
                    col(ActionConfirmationRecord.tool_name) == confirmation.tool_name,
                    col(ActionConfirmationRecord.action) == confirmation.action,
                    col(ActionConfirmationRecord.resource_type) == confirmation.resource_type,
                    col(ActionConfirmationRecord.resource_id) == confirmation.resource_id,
                    col(ActionConfirmationRecord.arguments_hash) == confirmation.arguments_hash,
                    col(ActionConfirmationRecord.status) == "pending",
                    col(ActionConfirmationRecord.expires_at) <= now,
                )
                .values(status="expired", decided_at=now)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def get_action_confirmation(
        self,
        confirmation_id: str,
        user_id: str | None = None,
    ) -> ActionConfirmation | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ActionConfirmationRecord).where(
                col(ActionConfirmationRecord.confirmation_id) == confirmation_id
            )
            if user_id is not None:
                stmt = stmt.where(col(ActionConfirmationRecord.user_id) == user_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _action_confirmation_from_record(row) if row else None

    async def list_action_confirmations(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        status: str | None = None,
    ) -> list[ActionConfirmation]:
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ActionConfirmationRecord).where(col(ActionConfirmationRecord.user_id) == user_id)
            if source is not None:
                stmt = stmt.where(col(ActionConfirmationRecord.source) == source)
            if session_key is not None:
                stmt = stmt.where(col(ActionConfirmationRecord.session_key) == session_key)
            if status is not None:
                stmt = stmt.where(col(ActionConfirmationRecord.status) == status)
            stmt = stmt.order_by(col(ActionConfirmationRecord.created_at).desc()).limit(500)
            result = await session.execute(stmt)
            return [_action_confirmation_from_record(row) for row in result.scalars().all()]

    async def list_batch_action_confirmations(self, user_id: str, batch_id: str) -> list[ActionConfirmation]:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ActionConfirmationRecord)
                .where(
                    col(ActionConfirmationRecord.user_id) == user_id,
                    col(ActionConfirmationRecord.batch_id) == batch_id,
                )
                .order_by(col(ActionConfirmationRecord.created_at).asc())
                .limit(500)
            )
            result = await session.execute(stmt)
            return [_action_confirmation_from_record(row) for row in result.scalars().all()]

    async def decide_action_confirmation(
        self,
        confirmation_id: str,
        user_id: str,
        decision: ConfirmationDecision,
    ) -> ActionConfirmation | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                update(ActionConfirmationRecord)
                .where(
                    col(ActionConfirmationRecord.confirmation_id) == confirmation_id,
                    col(ActionConfirmationRecord.user_id) == user_id,
                    col(ActionConfirmationRecord.status) == "pending",
                    col(ActionConfirmationRecord.expires_at) > now,
                )
                .values(status=decision, decided_at=now, decided_by=user_id)
                .returning(ActionConfirmationRecord)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            confirmation = _action_confirmation_from_record(row) if row else None
            await session.commit()
            return confirmation

    async def claim_action_confirmation_for_execution(
        self,
        confirmation_id: str,
        user_id: str,
    ) -> ActionConfirmation | None:
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                update(ActionConfirmationRecord)
                .where(
                    col(ActionConfirmationRecord.confirmation_id) == confirmation_id,
                    col(ActionConfirmationRecord.user_id) == user_id,
                    col(ActionConfirmationRecord.status) == "approved",
                    col(ActionConfirmationRecord.expires_at) > datetime.now(tz=UTC).isoformat(),
                )
                .values(status="executed")
                .returning(ActionConfirmationRecord)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            confirmation = _action_confirmation_from_record(row) if row else None
            await session.commit()
            return confirmation

    async def find_action_confirmation_grant(
        self,
        user_id: str,
        source: ConfirmationSource,
        session_key: str,
        tool_name: str,
        action: str,
        resource_type: str,
        resource_id: str,
        arguments_hash: str,
        statuses: tuple[str, ...] = ("approved", "denied"),
    ) -> ActionConfirmation | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = (
                select(ActionConfirmationRecord)
                .where(
                    col(ActionConfirmationRecord.user_id) == user_id,
                    col(ActionConfirmationRecord.source) == source,
                    col(ActionConfirmationRecord.session_key) == session_key,
                    col(ActionConfirmationRecord.tool_name) == tool_name,
                    col(ActionConfirmationRecord.action) == action,
                    col(ActionConfirmationRecord.resource_type) == resource_type,
                    col(ActionConfirmationRecord.resource_id) == resource_id,
                    col(ActionConfirmationRecord.arguments_hash) == arguments_hash,
                    col(ActionConfirmationRecord.status).in_(list(statuses)),
                    col(ActionConfirmationRecord.expires_at) > now,
                )
                .order_by(nullslast(col(ActionConfirmationRecord.decided_at).desc()))
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return _action_confirmation_from_record(row) if row else None
