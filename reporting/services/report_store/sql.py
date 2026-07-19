import logging
from datetime import UTC, datetime
from typing import Any

from snowflake import SnowflakeGenerator
from sqlalchemy import JSON, Column, Index, UniqueConstraint, and_, delete, null, nullslast, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlmodel import Field, SQLModel, col, select

from reporting import settings
from reporting.schema.chat import ChatSessionItem, ScheduledChatItem, ScheduledChatVersion
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
from reporting.services.report_store.base import ReportStore, initial_report_config
from reporting.utils.sql import build_database_url

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_snowflake_gen: SnowflakeGenerator | None = None


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


class QueryHistoryRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "query_history"
    id: int | None = Field(default=None, primary_key=True)
    history_id: str = Field(unique=True)
    user_id: str = Field(index=True)
    query: str
    executed_at: str


class ChatSessionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "chat_sessions"
    __table_args__ = (UniqueConstraint("user_id", "thread_id"),)
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


class ScheduledChatRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_chats"
    scheduled_chat_id: str = Field(primary_key=True)
    name: str
    prompt: str
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


class ScheduledChatVersionRecord(SQLModel, table=True):  # type: ignore
    __tablename__ = "scheduled_chat_versions"
    __table_args__ = (UniqueConstraint("scheduled_chat_id", "version"),)
    id: int | None = Field(default=None, primary_key=True)
    scheduled_chat_id: str = Field(index=True)
    version: int
    name: str
    prompt: str
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
    )


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = build_database_url(
            settings.SQL_DATABASE_URL,
            user=settings.SQL_DATABASE_USER,
            password=settings.SQL_DATABASE_PASSWORD,
        )
        connect_args: dict[str, Any] = {}
        if url.get_backend_name() == "postgresql":
            url = url.set(drivername="postgresql+asyncpg")
            connect_args["command_timeout"] = settings.SQL_STATEMENT_TIMEOUT
        elif url.get_backend_name() == "sqlite":
            # sqlite+aiosqlite for async sqlite (not commonly used in prod)
            url = url.set(drivername="sqlite+aiosqlite")
        _engine = create_async_engine(url, connect_args=connect_args)
    return _engine


# ---------------------------------------------------------------------------
# SQL backend implementation
# ---------------------------------------------------------------------------


class SQLModelReportStore(ReportStore):
    """ReportStore implementation backed by any SQLAlchemy-compatible database.

    Configured via the ``SQL_DATABASE_*`` settings.
    """

    def generate_id(self) -> str:
        return generate_report_id()

    async def initialize(self) -> None:
        """Bring the report-store schema to the Alembic head revision."""
        from reporting.services.report_store.migrations import run_schema_migrations

        await run_schema_migrations(_get_engine())
        logger.info("SQL report store tables initialised")

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
    ) -> ReportListItem:
        """Create a report and its initial renderable version atomically."""
        report_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        report_access = access or ReportAccess(scope="private")
        config = initial_report_config(name)

        async with AsyncSession(_get_engine()) as session:
            session.add(
                ReportRecord(
                    report_id=report_id,
                    name=name,
                    current_version=1,
                    created_at=now,
                    updated_at=now,
                    created_by=created_by,
                    updated_by=created_by,
                    access=report_access.model_dump(),
                )
            )
            session.add(
                ReportVersionRecord(
                    report_id=report_id,
                    version=1,
                    config=config,
                    created_at=now,
                    created_by=created_by,
                    comment="Initial version",
                )
            )
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
        )

    async def update_report_visibility(
        self,
        report_id: str,
        updated_by: str,
        access: ReportAccess | None = None,
    ) -> ReportListItem | None:
        async with AsyncSession(_get_engine()) as session:
            report = await session.get(ReportRecord, report_id)
            if not report:
                return None
            report.updated_at = datetime.now(tz=UTC).isoformat()
            report.updated_by = updated_by
            if access is not None:
                report.access = access.model_dump()
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
    ) -> ScheduledChatItem:
        sc_id = generate_report_id()
        now = datetime.now(tz=UTC).isoformat()
        version = 1
        async with AsyncSession(_get_engine()) as session:
            record = ScheduledChatRecord(
                scheduled_chat_id=sc_id,
                name=name,
                prompt=prompt,
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
    ) -> ScheduledChatItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            record = await session.get(ScheduledChatRecord, sc_id)
            if not record:
                return None
            version = record.current_version + 1
            record.name = name
            record.prompt = prompt
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
        from sqlalchemy import func

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
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.updated_at = now
            session.add(row)
            item = _chat_session_from_sql_record(row)
            await session.commit()
            return item

    async def complete_chat_session_run(
        self,
        user_id: str,
        thread_id: str,
        status: str,
        errors: list[str],
    ) -> ChatSessionItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.updated_at = now
            row.run_status = status
            row.run_errors = list(errors)
            session.add(row)
            item = _chat_session_from_sql_record(row)
            await session.commit()
            return item

    async def update_chat_session_title(self, user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
        now = datetime.now(tz=UTC).isoformat()
        async with AsyncSession(_get_engine()) as session:
            stmt = select(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.title = title
            row.updated_at = now
            session.add(row)
            item = _chat_session_from_sql_record(row)
            await session.commit()
            return item

    async def delete_chat_session(self, user_id: str, thread_id: str) -> bool:
        async with AsyncSession(_get_engine()) as session:
            stmt = delete(ChatSessionRecord).where(
                col(ChatSessionRecord.user_id) == user_id,
                col(ChatSessionRecord.thread_id) == thread_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

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
