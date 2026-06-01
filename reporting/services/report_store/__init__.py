import logging
from datetime import datetime
from typing import Any

from reporting.schema.chat import ChatSessionItem
from reporting.schema.confirmations import ActionConfirmation, ConfirmationDecision, ConfirmationSource
from reporting.schema.mcp_config import (
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
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
from reporting.services.report_store.base import ReportStore

logger = logging.getLogger(__name__)

_store: ReportStore | None = None


def get_store() -> ReportStore:
    """Return the configured report store singleton.

    The backend is selected by the REPORT_STORE_BACKEND setting (default: ``dynamodb``).
    """
    global _store
    if _store is None:
        from reporting import settings

        backend = settings.REPORT_STORE_BACKEND
        if backend == "dynamodb":
            from reporting.services.report_store.dynamodb import DynamoDBReportStore

            _store = DynamoDBReportStore()
        elif backend == "sqlmodel":
            from reporting.services.report_store.sql import SQLModelReportStore

            _store = SQLModelReportStore()
        else:
            raise ValueError(f"Unknown report store backend: {backend!r}")
    return _store


# ---------------------------------------------------------------------------
# Module-level convenience functions — delegate to the configured store so
# callers can use ``report_store.list_reports()`` without calling get_store().
# ---------------------------------------------------------------------------


async def initialize() -> None:
    await get_store().initialize()


async def list_reports(user_id: str | None = None) -> list[ReportListItem]:
    return await get_store().list_reports(user_id=user_id)


async def get_report_metadata(report_id: str, user_id: str | None = None) -> ReportListItem | None:
    return await get_store().get_report_metadata(report_id, user_id=user_id)


async def get_report_latest(report_id: str, user_id: str | None = None) -> ReportVersion | None:
    return await get_store().get_report_latest(report_id, user_id=user_id)


async def get_report_version(report_id: str, version: int, user_id: str | None = None) -> ReportVersion | None:
    return await get_store().get_report_version(report_id, version, user_id=user_id)


async def list_report_versions(report_id: str, user_id: str | None = None) -> list[ReportVersion]:
    return await get_store().list_report_versions(report_id, user_id=user_id)


async def create_report(
    name: str,
    created_by: str,
    access: ReportAccess | None = None,
) -> ReportListItem:
    return await get_store().create_report(name=name, created_by=created_by, access=access)


async def save_report_version(
    report_id: str,
    config: dict[str, Any],
    created_by: str,
    comment: str | None = None,
    user_id: str | None = None,
) -> ReportVersion | None:
    return await get_store().save_report_version(
        report_id=report_id,
        config=config,
        created_by=created_by,
        comment=comment,
        user_id=user_id,
    )


async def update_report_visibility(
    report_id: str,
    updated_by: str,
    access: ReportAccess | None = None,
) -> ReportListItem | None:
    return await get_store().update_report_visibility(
        report_id=report_id,
        updated_by=updated_by,
        access=access,
    )


async def delete_report(report_id: str, user_id: str | None = None) -> bool:
    return await get_store().delete_report(report_id, user_id=user_id)


async def pin_report(
    report_id: str,
    pinned: bool,
    updated_by: str,
    user_id: str | None = None,
) -> bool:
    return await get_store().pin_report(
        report_id,
        pinned,
        updated_by=updated_by,
        user_id=user_id,
    )


async def get_dashboard_report_id() -> str | None:
    return await get_store().get_dashboard_report_id()


async def set_dashboard_report(report_id: str) -> bool:
    return await get_store().set_dashboard_report(report_id)


async def get_dashboard_report() -> ReportVersion | None:
    return await get_store().get_dashboard_report()


async def get_or_create_user(
    sub: str,
    iss: str,
    email: str | None = None,
    display_name: str | None = None,
    preferred_username: str | None = None,
) -> User:
    return await get_store().get_or_create_user(
        sub=sub,
        iss=iss,
        email=email,
        display_name=display_name,
        preferred_username=preferred_username,
    )


async def update_user_profile(
    user_id: str,
    email: str | None = None,
    display_name: str | None = None,
    preferred_username: str | None = None,
    token_iat: datetime | None = None,
) -> User:
    return await get_store().update_user_profile(
        user_id=user_id,
        email=email,
        display_name=display_name,
        preferred_username=preferred_username,
        token_iat=token_iat,
    )


async def get_user(user_id: str) -> User | None:
    return await get_store().get_user(user_id)


async def archive_user(user_id: str) -> bool:
    return await get_store().archive_user(user_id)


async def list_scheduled_queries() -> list[ScheduledQueryItem]:
    return await get_store().list_scheduled_queries()


async def get_scheduled_query(sq_id: str) -> ScheduledQueryItem | None:
    return await get_store().get_scheduled_query(sq_id)


async def create_scheduled_query(
    name: str,
    cypher: str,
    params: list[dict[str, Any]],
    frequency: int | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    actions: list[dict[str, Any]],
    created_by: str,
) -> ScheduledQueryItem:
    return await get_store().create_scheduled_query(
        name=name,
        cypher=cypher,
        params=params,
        frequency=frequency,
        watch_scans=watch_scans,
        enabled=enabled,
        actions=actions,
        created_by=created_by,
    )


async def update_scheduled_query(
    sq_id: str,
    name: str,
    cypher: str,
    params: list[dict[str, Any]],
    frequency: int | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    actions: list[dict[str, Any]],
    updated_by: str,
    comment: str | None = None,
) -> ScheduledQueryItem | None:
    return await get_store().update_scheduled_query(
        sq_id=sq_id,
        name=name,
        cypher=cypher,
        params=params,
        frequency=frequency,
        watch_scans=watch_scans,
        enabled=enabled,
        actions=actions,
        updated_by=updated_by,
        comment=comment,
    )


async def acquire_scheduled_query_lock(sq_id: str, expected_last_scheduled_at: str | None) -> bool:
    return await get_store().acquire_scheduled_query_lock(
        sq_id=sq_id, expected_last_scheduled_at=expected_last_scheduled_at
    )


async def record_scheduled_query_result(sq_id: str, status: str, error: str | None = None) -> None:
    await get_store().record_scheduled_query_result(sq_id=sq_id, status=status, error=error)


async def delete_scheduled_query(sq_id: str) -> bool:
    return await get_store().delete_scheduled_query(sq_id)


async def list_scheduled_query_versions(sq_id: str) -> list[ScheduledQueryVersion]:
    return await get_store().list_scheduled_query_versions(sq_id)


async def get_scheduled_query_version(sq_id: str, version: int) -> ScheduledQueryVersion | None:
    return await get_store().get_scheduled_query_version(sq_id, version)


# ---------------------------------------------------------------------------
# Toolset convenience functions
# ---------------------------------------------------------------------------


async def list_toolsets() -> list[ToolsetListItem]:
    return await get_store().list_toolsets()


async def get_toolset(toolset_id: str) -> ToolsetListItem | None:
    return await get_store().get_toolset(toolset_id)


async def create_toolset(
    toolset_id: str,
    name: str,
    description: str,
    enabled: bool,
    created_by: str,
) -> ToolsetListItem:
    return await get_store().create_toolset(
        toolset_id=toolset_id,
        name=name,
        description=description,
        enabled=enabled,
        created_by=created_by,
    )


async def update_toolset(
    toolset_id: str,
    name: str,
    description: str,
    enabled: bool,
    updated_by: str,
    comment: str | None = None,
) -> ToolsetListItem | None:
    return await get_store().update_toolset(
        toolset_id=toolset_id,
        name=name,
        description=description,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )


async def delete_toolset(toolset_id: str) -> bool:
    return await get_store().delete_toolset(toolset_id)


async def list_toolset_versions(toolset_id: str) -> list[ToolsetVersion]:
    return await get_store().list_toolset_versions(toolset_id)


async def get_toolset_version(toolset_id: str, version: int) -> ToolsetVersion | None:
    return await get_store().get_toolset_version(toolset_id, version)


# ---------------------------------------------------------------------------
# Tool convenience functions
# ---------------------------------------------------------------------------


async def list_tools(toolset_id: str) -> list[ToolItem]:
    return await get_store().list_tools(toolset_id)


async def get_tool(tool_id: str) -> ToolItem | None:
    return await get_store().get_tool(tool_id)


async def create_tool(
    toolset_id: str,
    tool_id: str,
    name: str,
    description: str,
    cypher: str,
    parameters: list[dict[str, Any]],
    enabled: bool,
    created_by: str,
) -> ToolItem | None:
    return await get_store().create_tool(
        toolset_id=toolset_id,
        tool_id=tool_id,
        name=name,
        description=description,
        cypher=cypher,
        parameters=parameters,
        enabled=enabled,
        created_by=created_by,
    )


async def update_tool(
    tool_id: str,
    name: str,
    description: str,
    cypher: str,
    parameters: list[dict[str, Any]],
    enabled: bool,
    updated_by: str,
    comment: str | None = None,
) -> ToolItem | None:
    return await get_store().update_tool(
        tool_id=tool_id,
        name=name,
        description=description,
        cypher=cypher,
        parameters=parameters,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )


async def delete_tool(tool_id: str) -> bool:
    return await get_store().delete_tool(tool_id)


async def list_tool_versions(tool_id: str) -> list[ToolVersion]:
    return await get_store().list_tool_versions(tool_id)


async def get_tool_version(tool_id: str, version: int) -> ToolVersion | None:
    return await get_store().get_tool_version(tool_id, version)


async def list_enabled_tools() -> list[ToolItem]:
    return await get_store().list_enabled_tools()


async def get_enabled_tool(toolset_id: str, tool_id: str) -> ToolItem | None:
    return await get_store().get_enabled_tool(toolset_id, tool_id)


# ---------------------------------------------------------------------------
# Skillset convenience functions
# ---------------------------------------------------------------------------


async def list_skillsets() -> list[SkillsetListItem]:
    return await get_store().list_skillsets()


async def get_skillset(skillset_id: str) -> SkillsetListItem | None:
    return await get_store().get_skillset(skillset_id)


async def create_skillset(
    skillset_id: str,
    name: str,
    description: str,
    enabled: bool,
    created_by: str,
) -> SkillsetListItem:
    return await get_store().create_skillset(
        skillset_id=skillset_id,
        name=name,
        description=description,
        enabled=enabled,
        created_by=created_by,
    )


async def update_skillset(
    skillset_id: str,
    name: str,
    description: str,
    enabled: bool,
    updated_by: str,
    comment: str | None = None,
) -> SkillsetListItem | None:
    return await get_store().update_skillset(
        skillset_id=skillset_id,
        name=name,
        description=description,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )


async def delete_skillset(skillset_id: str) -> bool:
    return await get_store().delete_skillset(skillset_id)


async def list_skillset_versions(skillset_id: str) -> list[SkillsetVersion]:
    return await get_store().list_skillset_versions(skillset_id)


async def get_skillset_version(skillset_id: str, version: int) -> SkillsetVersion | None:
    return await get_store().get_skillset_version(skillset_id, version)


async def list_skills(skillset_id: str) -> list[SkillItem]:
    return await get_store().list_skills(skillset_id)


async def get_skill(skill_id: str) -> SkillItem | None:
    return await get_store().get_skill(skill_id)


async def create_skill(
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
    return await get_store().create_skill(
        skillset_id=skillset_id,
        skill_id=skill_id,
        name=name,
        description=description,
        template=template,
        parameters=parameters,
        triggers=triggers,
        tools_required=tools_required,
        enabled=enabled,
        created_by=created_by,
    )


async def update_skill(
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
    return await get_store().update_skill(
        skill_id=skill_id,
        name=name,
        description=description,
        template=template,
        parameters=parameters,
        triggers=triggers,
        tools_required=tools_required,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )


async def delete_skill(skill_id: str) -> bool:
    return await get_store().delete_skill(skill_id)


async def list_skill_versions(skill_id: str) -> list[SkillVersion]:
    return await get_store().list_skill_versions(skill_id)


async def get_skill_version(skill_id: str, version: int) -> SkillVersion | None:
    return await get_store().get_skill_version(skill_id, version)


async def list_enabled_skills() -> list[SkillItem]:
    return await get_store().list_enabled_skills()


async def get_enabled_skill(skillset_id: str, skill_id: str) -> SkillItem | None:
    return await get_store().get_enabled_skill(skillset_id, skill_id)


# ---------------------------------------------------------------------------
# Query history convenience functions
# ---------------------------------------------------------------------------


async def save_query_history(user_id: str, query: str) -> QueryHistoryItem:
    return await get_store().save_query_history(user_id=user_id, query=query)


async def list_query_history(user_id: str, page: int, per_page: int) -> tuple[list[QueryHistoryItem], int]:
    return await get_store().list_query_history(user_id=user_id, page=page, per_page=per_page)


async def get_query_history_item(user_id: str, history_id: str) -> QueryHistoryItem | None:
    return await get_store().get_query_history_item(user_id=user_id, history_id=history_id)


# ---------------------------------------------------------------------------
# Role convenience functions
# ---------------------------------------------------------------------------


async def list_roles() -> list[RoleItem]:
    return await get_store().list_roles()


async def get_role(role_id: str) -> RoleItem | None:
    return await get_store().get_role(role_id)


async def get_role_by_name(name: str) -> RoleItem | None:
    return await get_store().get_role_by_name(name)


async def create_role(
    name: str,
    description: str,
    permissions: list[str],
    created_by: str,
) -> RoleItem:
    return await get_store().create_role(
        name=name,
        description=description,
        permissions=permissions,
        created_by=created_by,
    )


async def update_role(
    role_id: str,
    name: str,
    description: str,
    permissions: list[str],
    updated_by: str,
    comment: str | None = None,
) -> RoleItem | None:
    return await get_store().update_role(
        role_id=role_id,
        name=name,
        description=description,
        permissions=permissions,
        updated_by=updated_by,
        comment=comment,
    )


async def delete_role(role_id: str) -> bool:
    return await get_store().delete_role(role_id)


async def list_role_versions(role_id: str) -> list[RoleVersion]:
    return await get_store().list_role_versions(role_id)


async def get_role_version(role_id: str, version: int) -> RoleVersion | None:
    return await get_store().get_role_version(role_id, version)


# ---------------------------------------------------------------------------
# Chat session convenience functions
# ---------------------------------------------------------------------------


async def list_chat_sessions(user_id: str, limit: int) -> list[ChatSessionItem]:
    return await get_store().list_chat_sessions(user_id, limit=limit)


async def get_chat_session(user_id: str, thread_id: str) -> ChatSessionItem | None:
    return await get_store().get_chat_session(user_id, thread_id)


async def create_chat_session(user_id: str, title: str) -> ChatSessionItem:
    return await get_store().create_chat_session(user_id, title)


async def touch_chat_session(user_id: str, thread_id: str) -> ChatSessionItem | None:
    return await get_store().touch_chat_session(user_id, thread_id)


async def update_chat_session_title(user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
    return await get_store().update_chat_session_title(user_id, thread_id, title)


async def delete_chat_session(user_id: str, thread_id: str) -> bool:
    return await get_store().delete_chat_session(user_id, thread_id)


# ---------------------------------------------------------------------------
# Action confirmation convenience functions
# ---------------------------------------------------------------------------


async def create_action_confirmation(confirmation: ActionConfirmation) -> ActionConfirmation:
    return await get_store().create_action_confirmation(confirmation)


async def get_action_confirmation(
    confirmation_id: str,
    user_id: str | None = None,
) -> ActionConfirmation | None:
    return await get_store().get_action_confirmation(confirmation_id, user_id=user_id)


async def list_action_confirmations(
    user_id: str,
    source: ConfirmationSource,
    session_key: str,
    status: str | None = None,
) -> list[ActionConfirmation]:
    return await get_store().list_action_confirmations(
        user_id=user_id,
        source=source,
        session_key=session_key,
        status=status,
    )


async def list_batch_action_confirmations(user_id: str, batch_id: str) -> list[ActionConfirmation]:
    return await get_store().list_batch_action_confirmations(user_id=user_id, batch_id=batch_id)


async def decide_action_confirmation(
    confirmation_id: str,
    user_id: str,
    decision: ConfirmationDecision,
) -> ActionConfirmation | None:
    return await get_store().decide_action_confirmation(
        confirmation_id=confirmation_id,
        user_id=user_id,
        decision=decision,
    )


async def claim_action_confirmation_for_execution(
    confirmation_id: str,
    user_id: str,
) -> ActionConfirmation | None:
    return await get_store().claim_action_confirmation_for_execution(
        confirmation_id=confirmation_id,
        user_id=user_id,
    )


async def find_action_confirmation_grant(
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
    return await get_store().find_action_confirmation_grant(
        user_id=user_id,
        source=source,
        session_key=session_key,
        tool_name=tool_name,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        arguments_hash=arguments_hash,
        statuses=statuses,
    )
