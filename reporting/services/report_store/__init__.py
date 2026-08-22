import logging
import re
from datetime import datetime
from typing import Any, Literal

from reporting.schema.chat import (
    ChatSessionItem,
    ChatTurnAdmission,
    ChatTurnCommand,
    ChatTurnEventPage,
    ChatTurnItem,
    IdleChatSession,
    ScheduledChatItem,
    ScheduledChatVersion,
)
from reporting.schema.confirmations import ActionConfirmation, ConfirmationDecision, ConfirmationSource
from reporting.schema.mcp_config import (
    EXTERNAL_MCP_TOOL_NAME_RE,
    MCP_TOOL_NAME_RE,
    SkillItem,
    SkillsetListItem,
    SkillsetVersion,
    SkillVersion,
    ToolItem,
    ToolsetListItem,
    ToolsetVersion,
    ToolVersion,
)
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
from reporting.schema.space_config import SpaceDeleteResult, SpaceListItem, SubspaceItem
from reporting.services.report_store.base import ReportStore

logger = logging.getLogger(__name__)

_store: ReportStore | None = None
_initialized = False


def get_store() -> ReportStore:
    """Return the PostgreSQL report store singleton."""
    global _store
    if _store is None:
        from reporting.services.report_store.sql import SQLModelReportStore

        _store = SQLModelReportStore()
    return _store


# ---------------------------------------------------------------------------
# Module-level convenience functions — delegate to the configured store so
# callers can use ``report_store.list_reports()`` without calling get_store().
# ---------------------------------------------------------------------------


async def initialize() -> None:
    global _initialized
    await get_store().initialize()
    _initialized = True


def is_initialized() -> bool:
    """Whether the application store completed its startup migrations."""
    return _initialized


def generate_id() -> str:
    """Return a new Snowflake ID from the configured store backend."""
    return get_store().generate_id()


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
    space_id: str | None = None,
    subspace_id: str | None = None,
) -> ReportListItem:
    return await get_store().create_report(
        name=name,
        created_by=created_by,
        access=access,
        space_id=space_id,
        subspace_id=subspace_id,
    )


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


async def update_report_space(
    report_id: str,
    space_id: str | None,
    subspace_id: str | None,
    updated_by: str,
    user_id: str | None = None,
) -> ReportListItem | None:
    return await get_store().update_report_space(
        report_id=report_id,
        space_id=space_id,
        subspace_id=subspace_id,
        updated_by=updated_by,
        user_id=user_id,
    )


async def delete_report(report_id: str, user_id: str | None = None) -> bool:
    return await get_store().delete_report(report_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------


async def list_spaces() -> list[SpaceListItem]:
    return await get_store().list_spaces()


async def get_space(space_id: str) -> SpaceListItem | None:
    return await get_store().get_space(space_id)


async def create_space(name: str, description: str, created_by: str) -> SpaceListItem:
    return await get_store().create_space(name=name, description=description, created_by=created_by)


async def update_space(
    space_id: str,
    name: str,
    description: str,
    updated_by: str,
) -> SpaceListItem | None:
    return await get_store().update_space(
        space_id=space_id,
        name=name,
        description=description,
        updated_by=updated_by,
    )


async def delete_space(space_id: str) -> SpaceDeleteResult:
    return await get_store().delete_space(space_id)


async def set_space_overview(
    space_id: str,
    report_id: str | None,
    updated_by: str,
) -> SpaceListItem | None:
    return await get_store().set_space_overview(
        space_id=space_id,
        report_id=report_id,
        updated_by=updated_by,
    )


async def list_space_reports(space_id: str, user_id: str | None = None) -> list[ReportListItem]:
    return await get_store().list_space_reports(space_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Sub-spaces
# ---------------------------------------------------------------------------


async def list_subspaces(space_id: str) -> list[SubspaceItem]:
    return await get_store().list_subspaces(space_id)


async def get_subspace(subspace_id: str) -> SubspaceItem | None:
    return await get_store().get_subspace(subspace_id)


async def create_subspace(space_id: str, name: str, created_by: str) -> SubspaceItem | None:
    return await get_store().create_subspace(space_id=space_id, name=name, created_by=created_by)


async def update_subspace(subspace_id: str, name: str, updated_by: str) -> SubspaceItem | None:
    return await get_store().update_subspace(subspace_id=subspace_id, name=name, updated_by=updated_by)


async def delete_subspace(subspace_id: str) -> bool:
    return await get_store().delete_subspace(subspace_id)


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
    role: str | None = None,
) -> User:
    return await get_store().get_or_create_user(
        sub=sub,
        iss=iss,
        email=email,
        display_name=display_name,
        preferred_username=preferred_username,
        role=role,
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
    schedule: dict[str, Any] | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    actions: list[dict[str, Any]],
    created_by: str,
    stages: list[dict[str, Any]] | None = None,
    inputs: dict[str, Any] | None = None,
    activities: list[dict[str, Any]] | None = None,
) -> ScheduledQueryItem:
    kwargs: dict[str, Any] = {}
    if stages is not None:
        kwargs["stages"] = stages
    if inputs is not None:
        kwargs["inputs"] = inputs
    if activities is not None:
        kwargs["activities"] = activities
    return await get_store().create_scheduled_query(
        name=name,
        cypher=cypher,
        params=params,
        frequency=frequency,
        schedule=schedule,
        watch_scans=watch_scans,
        enabled=enabled,
        actions=actions,
        created_by=created_by,
        **kwargs,
    )


async def update_scheduled_query(
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
    kwargs: dict[str, Any] = {}
    if stages is not None:
        kwargs["stages"] = stages
    if inputs is not None:
        kwargs["inputs"] = inputs
    if activities is not None:
        kwargs["activities"] = activities
    return await get_store().update_scheduled_query(
        sq_id=sq_id,
        name=name,
        cypher=cypher,
        params=params,
        frequency=frequency,
        schedule=schedule,
        watch_scans=watch_scans,
        enabled=enabled,
        actions=actions,
        updated_by=updated_by,
        comment=comment,
        **kwargs,
    )


async def set_workflow_schedule_sync_status(
    workflow_id: str,
    status: str,
    *,
    error: str | None = None,
    synced_at: str | None = None,
) -> None:
    await get_store().set_workflow_schedule_sync_status(
        workflow_id,
        status,
        error=error,
        synced_at=synced_at,
    )


async def set_chat_schedule_sync_status(
    sc_id: str,
    status: str,
    *,
    error: str | None = None,
    synced_at: str | None = None,
) -> None:
    await get_store().set_chat_schedule_sync_status(
        sc_id,
        status,
        error=error,
        synced_at=synced_at,
    )


async def acquire_scheduled_query_lock(sq_id: str, expected_last_scheduled_at: str | None) -> bool:
    return await get_store().acquire_scheduled_query_lock(
        sq_id=sq_id, expected_last_scheduled_at=expected_last_scheduled_at
    )


async def record_scheduled_query_result(sq_id: str, status: str, error: str | None = None) -> None:
    await get_store().record_scheduled_query_result(sq_id=sq_id, status=status, error=error)


async def request_scheduled_query_run(sq_id: str) -> str | None:
    return await get_store().request_scheduled_query_run(sq_id)


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
    store = get_store()
    legacy = await store.list_skillsets()
    known = {item.skillset_id for item in legacy}
    projected = [_plugin_skillset(item) for item in await store.list_plugins() if item.plugin_id not in known]
    return [*legacy, *projected]


async def get_skillset(skillset_id: str) -> SkillsetListItem | None:
    store = get_store()
    legacy = await store.get_skillset(skillset_id)
    if legacy:
        return legacy
    plugin = await store.get_plugin(skillset_id)
    return _plugin_skillset(plugin) if plugin else None


def _plugin_skillset(plugin: PluginListItem) -> SkillsetListItem:
    return SkillsetListItem(
        skillset_id=plugin.plugin_id,
        name=plugin.name,
        description=plugin.description,
        enabled=plugin.enabled,
        current_version=plugin.current_revision,
        created_at=plugin.created_at,
        updated_at=plugin.updated_at,
        created_by=plugin.created_by,
        updated_by=plugin.updated_by,
    )


async def create_skillset(
    skillset_id: str,
    name: str,
    description: str,
    enabled: bool,
    created_by: str,
) -> SkillsetListItem:
    item = await get_store().create_skillset(
        skillset_id=skillset_id,
        name=name,
        description=description,
        enabled=enabled,
        created_by=created_by,
    )
    await _sync_legacy_skillset(skillset_id, created_by, "Legacy skillset create")
    return item


async def update_skillset(
    skillset_id: str,
    name: str,
    description: str,
    enabled: bool,
    updated_by: str,
    comment: str | None = None,
) -> SkillsetListItem | None:
    item = await get_store().update_skillset(
        skillset_id=skillset_id,
        name=name,
        description=description,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )
    if item:
        await _sync_legacy_skillset(skillset_id, updated_by, comment or "Legacy skillset update")
    return item


async def delete_skillset(skillset_id: str) -> bool:
    store = get_store()
    deleted = await store.delete_skillset(skillset_id)
    if deleted:
        plugin = await store.get_plugin(skillset_id)
        if plugin and await _is_projection_owned_plugin(store, plugin):
            await store.delete_plugin(skillset_id)
    return deleted


async def list_skillset_versions(skillset_id: str) -> list[SkillsetVersion]:
    store = get_store()
    legacy = await store.list_skillset_versions(skillset_id)
    if legacy:
        return legacy
    plugin = await store.get_plugin(skillset_id)
    if not plugin:
        return []
    return [
        SkillsetVersion(
            skillset_id=skillset_id,
            name=version.manifest.get("name", plugin.name),
            description=version.manifest.get("description", ""),
            enabled=plugin.enabled,
            version=version.revision,
            created_at=version.created_at,
            created_by=version.created_by,
            comment=version.comment,
        )
        for version in await store.list_plugin_versions(skillset_id)
    ]


async def get_skillset_version(skillset_id: str, version: int) -> SkillsetVersion | None:
    store = get_store()
    legacy = await store.get_skillset_version(skillset_id, version)
    if legacy:
        return legacy
    return next(
        (item for item in await list_skillset_versions(skillset_id) if item.version == version),
        None,
    )


async def list_skills(skillset_id: str) -> list[SkillItem]:
    store = get_store()
    legacy = await store.list_skills(skillset_id)
    if legacy or await store.get_skillset(skillset_id):
        return legacy
    plugin = await store.get_plugin(skillset_id)
    if not plugin:
        return []
    return [_plugin_skill(item, plugin) for item in await store.list_plugin_skills(skillset_id)]


def _plugin_skill(skill: PluginSkillItem, plugin: PluginListItem) -> SkillItem:
    direct_tools = [
        name
        for name in skill.allowed_tools
        if MCP_TOOL_NAME_RE.fullmatch(name) or EXTERNAL_MCP_TOOL_NAME_RE.fullmatch(name)
    ]
    return SkillItem(
        skill_id=skill.skill_id,
        skillset_id=skill.plugin_id,
        name=skill.title,
        description=skill.description,
        template=skill.template,
        parameters=skill.parameters,
        triggers=skill.triggers,
        tools_required=direct_tools,
        enabled=skill.enabled,
        current_version=skill.revision,
        created_at=plugin.created_at,
        updated_at=plugin.updated_at,
        created_by=plugin.created_by,
        updated_by=plugin.updated_by,
        effective_enabled=skill.enabled and plugin.enabled,
        disabled_reason=("plugin_disabled" if not plugin.enabled else "skill_disabled" if not skill.enabled else None),
    )


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
    item = await get_store().create_skill(
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
    if item:
        await _sync_legacy_skillset(skillset_id, created_by, "Legacy skill create")
    return item


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
    item = await get_store().update_skill(
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
    if item:
        await _sync_legacy_skillset(item.skillset_id, updated_by, comment or "Legacy skill update")
    return item


async def delete_skill(skill_id: str) -> bool:
    existing = await get_store().get_skill(skill_id)
    deleted = await get_store().delete_skill(skill_id)
    if deleted and existing:
        await _sync_legacy_skillset(
            existing.skillset_id, existing.updated_by or existing.created_by, "Legacy skill delete"
        )
    return deleted


async def _sync_legacy_skillset(skillset_id: str, user_id: str, comment: str) -> None:
    """Publish the package projection after a legacy compatibility write."""
    from reporting.services.plugin_packages import legacy_skillset_package

    store = get_store()
    existing = await store.get_plugin(skillset_id)
    if existing and not await _is_projection_owned_plugin(store, existing):
        return
    skillset = await store.get_skillset(skillset_id)
    if not skillset:
        return
    parsed = legacy_skillset_package(skillset, await store.list_skills(skillset_id))
    if not parsed.valid:
        logger.error("Legacy skillset %s no longer forms a valid plugin package", skillset_id)
        return
    if existing and existing.package_digest == parsed.package_digest:
        return
    await store.publish_plugin(
        parsed.plugin_id,
        parsed.manifest,
        parsed.files,
        parsed.skills,
        [diagnostic.model_dump() for diagnostic in parsed.diagnostics],
        parsed.package_digest,
        user_id,
        comment,
    )


async def _is_projection_owned_plugin(store: ReportStore, plugin: PluginListItem) -> bool:
    """Return whether the current package revision belongs to the legacy projection."""
    from reporting.services.plugin_packages import is_legacy_skillset_projection

    versions = await store.list_plugin_versions(plugin.plugin_id)
    current = next((version for version in versions if version.revision == plugin.current_revision), None)
    return bool(current and is_legacy_skillset_projection(current.manifest))


# A package's skills are determined by plugin.json, mcp.json and the SKILL.md
# files; supporting files only set `has_scripts`, which the legacy skill
# projection does not carry. Reading just these keeps a version listing from
# pulling every asset of every revision through the database.
_SKILL_DEFINING_FILES = re.compile(r"^(plugin\.json|mcp\.json|skills/[^/]+/SKILL\.md)$")


async def _plugin_skill_version(
    skillset_id: str,
    skill_id: str,
    version: PluginVersion,
) -> SkillVersion | None:
    from reporting.services.plugin_packages import parse_package

    store = get_store()
    paths = [
        info.path
        for info in await store.list_plugin_files(skillset_id, version.revision)
        if _SKILL_DEFINING_FILES.fullmatch(info.path)
    ]
    parsed = parse_package(await store.read_plugin_files(skillset_id, version.revision, paths))
    skill = next((item for item in parsed.skills if item.skill_id == skill_id), None)
    if not skill:
        return None
    direct_tools = [
        name
        for name in skill.allowed_tools
        if MCP_TOOL_NAME_RE.fullmatch(name) or EXTERNAL_MCP_TOOL_NAME_RE.fullmatch(name)
    ]
    return SkillVersion(
        skill_id=skill.skill_id,
        skillset_id=skill.plugin_id,
        name=skill.title,
        description=skill.description,
        template=skill.template,
        parameters=skill.parameters,
        triggers=skill.triggers,
        tools_required=direct_tools,
        enabled=skill.enabled,
        version=version.revision,
        created_at=version.created_at,
        created_by=version.created_by,
        comment=version.comment,
    )


async def list_skill_versions(skill_id: str, skillset_id: str | None = None) -> list[SkillVersion]:
    store = get_store()
    if skillset_id is None:
        return await store.list_skill_versions(skill_id)
    legacy_skill = await store.get_skill(skill_id)
    if legacy_skill and legacy_skill.skillset_id == skillset_id:
        return await store.list_skill_versions(skill_id)
    results: list[SkillVersion] = []
    if await store.get_plugin_skill(skillset_id, skill_id) is None:
        return results
    for version in await store.list_plugin_versions(skillset_id):
        projected = await _plugin_skill_version(skillset_id, skill_id, version)
        if projected:
            results.append(projected)
    return sorted(results, key=lambda item: item.version, reverse=True)


async def get_skill_version(
    skill_id: str,
    version: int,
    skillset_id: str | None = None,
) -> SkillVersion | None:
    if skillset_id is None:
        return await get_store().get_skill_version(skill_id, version)
    return next(
        (item for item in await list_skill_versions(skill_id, skillset_id) if item.version == version),
        None,
    )


async def list_enabled_skills() -> list[SkillItem]:
    return await get_store().list_enabled_skills()


async def get_enabled_skill(skillset_id: str, skill_id: str) -> SkillItem | None:
    return await get_store().get_enabled_skill(skillset_id, skill_id)


# ---------------------------------------------------------------------------
# Agent plugin convenience functions
# ---------------------------------------------------------------------------


async def list_plugins() -> list[PluginListItem]:
    return await get_store().list_plugins()


async def get_plugin(plugin_id: str) -> PluginListItem | None:
    return await get_store().get_plugin(plugin_id)


async def publish_plugin(
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
    return await get_store().publish_plugin(
        plugin_id,
        manifest,
        files,
        skills,
        diagnostics,
        package_digest,
        created_by,
        comment,
        expected_revision,
    )


async def set_plugin_enabled(plugin_id: str, enabled: bool, updated_by: str) -> PluginListItem | None:
    return await get_store().set_plugin_enabled(plugin_id, enabled, updated_by)


async def delete_plugin(plugin_id: str) -> bool:
    return await get_store().delete_plugin(plugin_id)


async def list_plugin_versions(plugin_id: str) -> list[PluginVersion]:
    return await get_store().list_plugin_versions(plugin_id)


async def list_plugin_files(plugin_id: str, revision: int | None = None) -> list[PluginFileInfo]:
    return await get_store().list_plugin_files(plugin_id, revision)


async def read_plugin_file(plugin_id: str, path: str, revision: int | None = None) -> PluginFile | None:
    return await get_store().read_plugin_file(plugin_id, path, revision)


async def read_plugin_files(
    plugin_id: str, revision: int | None = None, paths: list[str] | None = None
) -> list[PluginFile]:
    return await get_store().read_plugin_files(plugin_id, revision, paths)


async def list_enabled_plugin_skills() -> list[PluginSkillItem]:
    return await get_store().list_enabled_plugin_skills()


async def list_plugin_skills(plugin_id: str) -> list[PluginSkillItem]:
    return await get_store().list_plugin_skills(plugin_id)


async def get_plugin_skill(plugin_id: str, skill_id: str) -> PluginSkillItem | None:
    return await get_store().get_plugin_skill(plugin_id, skill_id)


async def get_enabled_plugin_skill(plugin_id: str, skill_id: str) -> PluginSkillItem | None:
    return await get_store().get_enabled_plugin_skill(plugin_id, skill_id)


async def read_plugin_blob(plugin_id: str, sha256: str) -> PluginFile | None:
    return await get_store().read_plugin_blob(plugin_id, sha256)


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


async def list_idle_chat_sessions(idle_before: str, limit: int) -> list[IdleChatSession]:
    return await get_store().list_idle_chat_sessions(idle_before, limit)


async def claim_chat_session_for_retirement(user_id: str, thread_id: str, expected_updated_at: str) -> bool:
    return await get_store().claim_chat_session_for_retirement(user_id, thread_id, expected_updated_at)


async def create_chat_session(
    user_id: str,
    title: str,
    origin: str = "interactive",
    scheduled_chat_id: str | None = None,
) -> ChatSessionItem:
    return await get_store().create_chat_session(
        user_id,
        title,
        origin=origin,
        scheduled_chat_id=scheduled_chat_id,
    )


async def list_scheduled_chat_sessions(user_id: str, scheduled_chat_id: str, limit: int) -> list[ChatSessionItem]:
    return await get_store().list_scheduled_chat_sessions(user_id, scheduled_chat_id, limit)


async def touch_chat_session(user_id: str, thread_id: str) -> ChatSessionItem | None:
    return await get_store().touch_chat_session(user_id, thread_id)


async def complete_chat_session_run(
    user_id: str,
    thread_id: str,
    status: str,
    errors: list[str],
) -> ChatSessionItem | None:
    return await get_store().complete_chat_session_run(user_id, thread_id, status, errors)


async def update_chat_session_title(user_id: str, thread_id: str, title: str) -> ChatSessionItem | None:
    return await get_store().update_chat_session_title(user_id, thread_id, title)


async def delete_chat_session(user_id: str, thread_id: str) -> bool:
    return await get_store().delete_chat_session(user_id, thread_id)


# ---------------------------------------------------------------------------
# Chat turn event log convenience functions
# ---------------------------------------------------------------------------


async def admit_chat_turn(
    user_id: str,
    thread_id: str,
    message_id: str,
    text_id: str,
    idempotency_key: str,
    command: ChatTurnCommand,
) -> ChatTurnAdmission:
    return await get_store().admit_chat_turn(user_id, thread_id, message_id, text_id, idempotency_key, command)


async def get_active_chat_turn(user_id: str, thread_id: str) -> ChatTurnItem | None:
    return await get_store().get_active_chat_turn(user_id, thread_id)


async def get_chat_turn(turn_id: str, user_id: str | None = None) -> ChatTurnItem | None:
    return await get_store().get_chat_turn(turn_id, user_id=user_id)


async def append_chat_turn_events(turn_id: str, parts_json: str) -> int | None:
    return await get_store().append_chat_turn_events(turn_id, parts_json)


async def put_chat_turn_payload(turn_id: str, payload_id: str, body: str) -> None:
    return await get_store().put_chat_turn_payload(turn_id, payload_id, body)


async def get_chat_turn_payload(turn_id: str, payload_id: str) -> str | None:
    return await get_store().get_chat_turn_payload(turn_id, payload_id)


async def read_chat_turn_events(turn_id: str, after_seq: int, limit: int) -> ChatTurnEventPage | None:
    return await get_store().read_chat_turn_events(turn_id, after_seq, limit)


async def request_chat_turn_cancel(turn_id: str, user_id: str) -> ChatTurnItem | None:
    return await get_store().request_chat_turn_cancel(turn_id, user_id)


async def finish_chat_turn(
    turn_id: str,
    status: Literal["completed", "failed", "canceled", "expired"],
    last_seq: int,
) -> ChatTurnItem | None:
    return await get_store().finish_chat_turn(turn_id, status, last_seq)


async def delete_chat_turn(turn_id: str) -> bool:
    return await get_store().delete_chat_turn(turn_id)


async def list_expired_chat_turns(expired_before: str, limit: int) -> list[str]:
    return await get_store().list_expired_chat_turns(expired_before, limit)


# ---------------------------------------------------------------------------
# Scheduled chat convenience functions
# ---------------------------------------------------------------------------


async def list_scheduled_chats(user_id: str | None = None) -> list[ScheduledChatItem]:
    return await get_store().list_scheduled_chats(user_id=user_id)


async def get_scheduled_chat(sc_id: str) -> ScheduledChatItem | None:
    return await get_store().get_scheduled_chat(sc_id)


async def create_scheduled_chat(
    name: str,
    prompt: str,
    schedule: dict[str, Any] | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    created_by: str,
) -> ScheduledChatItem:
    return await get_store().create_scheduled_chat(
        name=name,
        prompt=prompt,
        schedule=schedule,
        watch_scans=watch_scans,
        enabled=enabled,
        created_by=created_by,
    )


async def update_scheduled_chat(
    sc_id: str,
    name: str,
    prompt: str,
    schedule: dict[str, Any] | None,
    watch_scans: list[dict[str, Any]],
    enabled: bool,
    updated_by: str,
    comment: str | None = None,
) -> ScheduledChatItem | None:
    return await get_store().update_scheduled_chat(
        sc_id=sc_id,
        name=name,
        prompt=prompt,
        schedule=schedule,
        watch_scans=watch_scans,
        enabled=enabled,
        updated_by=updated_by,
        comment=comment,
    )


async def list_scheduled_chat_versions(sc_id: str) -> list[ScheduledChatVersion]:
    return await get_store().list_scheduled_chat_versions(sc_id)


async def get_scheduled_chat_version(sc_id: str, version: int) -> ScheduledChatVersion | None:
    return await get_store().get_scheduled_chat_version(sc_id, version)


async def delete_scheduled_chat(sc_id: str) -> bool:
    return await get_store().delete_scheduled_chat(sc_id)


async def acquire_scheduled_chat_lock(sc_id: str, expected_last_scheduled_at: str | None) -> bool:
    return await get_store().acquire_scheduled_chat_lock(sc_id, expected_last_scheduled_at)


async def record_scheduled_chat_result(sc_id: str, status: str, error: str | None = None) -> None:
    await get_store().record_scheduled_chat_result(sc_id, status, error=error)


async def request_scheduled_chat_run(sc_id: str) -> str | None:
    return await get_store().request_scheduled_chat_run(sc_id)


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
