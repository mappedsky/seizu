import json
import logging
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.mcp_config import (
    CreateSkillRequest,
    CreateSkillsetRequest,
    RenderSkillRequest,
    RenderSkillResponse,
    SkillIdResponse,
    SkillItem,
    SkillListResponse,
    SkillsetIdResponse,
    SkillsetListItem,
    SkillsetListResponse,
    SkillsetVersion,
    SkillsetVersionListResponse,
    SkillVersion,
    SkillVersionListResponse,
    UpdateSkillRequest,
    UpdateSkillsetRequest,
    render_skill_parts,
    validate_skill_template,
)
from reporting.schema.plugins import PluginFile
from reporting.services import external_mcp, plugin_packages, report_store
from reporting.services.mcp_builtins import find_builtin

router = APIRouter()
logger = logging.getLogger(__name__)


async def _find_skill(skillset_id: str, skill_id: str) -> SkillItem | None:
    skill = await report_store.get_skill(skill_id)
    if skill and skill.skillset_id == skillset_id:
        return skill
    return next((item for item in await report_store.list_skills(skillset_id) if item.skill_id == skill_id), None)


async def _plugin_files(plugin_id: str) -> list[PluginFile] | None:
    plugin = await report_store.get_plugin(plugin_id)
    if not plugin:
        return None
    return await report_store.read_plugin_files(plugin_id, plugin.current_revision)


async def _publish_legacy_edit(
    plugin_id: str,
    files: list[PluginFile],
    current: CurrentUser,
    comment: str | None,
) -> bool:
    parsed = plugin_packages.parse_package(files)
    if not parsed.valid or parsed.plugin_id != plugin_id:
        return False
    await report_store.publish_plugin(
        parsed.plugin_id,
        parsed.manifest,
        parsed.files,
        parsed.skills,
        [diagnostic.model_dump() for diagnostic in parsed.diagnostics],
        parsed.package_digest,
        current.user.user_id,
        comment,
    )
    return True


def _legacy_skill_markdown(
    portable_name: str,
    description: str,
    template: str,
    tools_required: list[str],
) -> bytes:
    metadata: dict[str, Any] = {"name": portable_name, "description": description}
    if tools_required:
        metadata["allowed-tools"] = " ".join(tools_required)
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{frontmatter}\n---\n{template}".encode()


async def _edit_plugin_skill(
    plugin_id: str,
    skill_id: str,
    body: CreateSkillRequest | UpdateSkillRequest | None,
    current: CurrentUser,
    *,
    delete: bool = False,
) -> SkillItem | None:
    files = await _plugin_files(plugin_id)
    plugin = await report_store.get_plugin(plugin_id)
    if files is None or plugin is None:
        return None
    indexed = await report_store.get_plugin_skill(plugin_id, skill_id)
    portable_name = indexed.portable_name if indexed else skill_id.replace("_", "-")
    by_path = {file.path: file for file in files}
    manifest_file = by_path.get("plugin.json")
    if manifest_file is None:
        return None
    manifest = json.loads(manifest_file.content)
    extension = manifest["extensions"][plugin_packages.EXTENSION_NAMESPACE]
    extension_skills = extension.setdefault("skills", {})
    if delete:
        if indexed is None:
            return None
        extension_skills.pop(portable_name, None)
        by_path = {path: file for path, file in by_path.items() if not path.startswith(f"{indexed.source_path}/")}
    elif body is not None:
        extension_skills[portable_name] = {
            "skillId": skill_id,
            "title": body.name,
            "enabled": body.enabled,
            "triggers": body.triggers,
            "parameters": [parameter.model_dump() for parameter in body.parameters],
            "aliases": [f"{plugin_id}__{skill_id}"],
        }
        path = f"skills/{portable_name}/SKILL.md"
        by_path[path] = PluginFile(
            path=path,
            content=_legacy_skill_markdown(
                portable_name,
                body.description or body.name,
                body.template,
                body.tools_required,
            ),
            media_type="text/markdown",
        )
    by_path["plugin.json"] = PluginFile(
        path="plugin.json",
        content=json.dumps(manifest, indent=2, sort_keys=True).encode(),
        media_type="application/json",
    )
    comment = getattr(body, "comment", None) if body is not None else "Legacy skill delete"
    if not await _publish_legacy_edit(plugin_id, list(by_path.values()), current, comment):
        return None
    skills = await report_store.list_skills(plugin_id)
    return next((skill for skill in skills if skill.skill_id == skill_id), None) if not delete else None


def _with_effective_skill_state(skill: SkillItem, skillset: SkillsetListItem) -> SkillItem:
    """Return a skill response with parent-disabled state folded in."""
    effective_enabled = skill.enabled and skillset.enabled
    disabled_reason = None
    if not skillset.enabled:
        disabled_reason = "skillset_disabled"
    elif not skill.enabled:
        disabled_reason = "skill_disabled"
    return skill.model_copy(
        update={
            "effective_enabled": effective_enabled,
            "disabled_reason": disabled_reason,
        }
    )


async def _check_tools_required(tools_required: list[str]) -> tuple[list[str], list[str]]:
    """Check tool references, returning (valid_refs, dropped_refs).

    Missing tools are dropped rather than blocking the save so that deleting a
    tool does not permanently prevent editing skills that referenced it.

    ``ext__<proxy>__<tool>`` references are checked against the configured
    proxies only: the remote tool list is discovered per user at call time, so
    the server has no user-independent inventory to validate against here.
    """
    valid: list[str] = []
    dropped: list[str] = []
    for tool_ref in tools_required:
        if find_builtin(tool_ref) is not None:
            valid.append(tool_ref)
            continue
        if tool_ref.startswith(f"{external_mcp.NAMESPACE_PREFIX}__"):
            if external_mcp.parse_namespaced_tool_name(tool_ref) is not None:
                valid.append(tool_ref)
            else:
                dropped.append(tool_ref)
            continue
        if "__" not in tool_ref:
            dropped.append(tool_ref)
            continue
        toolset_id, tool_id = tool_ref.split("__", 1)
        tool = await report_store.get_tool(tool_id)
        if tool and tool.toolset_id == toolset_id:
            valid.append(tool_ref)
        else:
            dropped.append(tool_ref)
    return valid, dropped


@router.get("/api/v1/skillsets", response_model=SkillsetListResponse)
async def list_skillsets(
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_READ)),
) -> SkillsetListResponse:
    return SkillsetListResponse(skillsets=await report_store.list_skillsets())


@router.post("/api/v1/skillsets", response_model=SkillsetListItem, status_code=201)
async def create_skillset(
    body: CreateSkillsetRequest,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_WRITE)),
) -> SkillsetListItem:
    if await report_store.get_skillset(body.skillset_id):
        raise HTTPException(status_code=409, detail="Skillset already exists")
    return await report_store.create_skillset(
        skillset_id=body.skillset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        created_by=current.user.user_id,
    )


@router.get("/api/v1/skillsets/{skillset_id}", response_model=SkillsetListItem)
async def get_skillset(
    skillset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_READ)),
) -> SkillsetListItem:
    item = await report_store.get_skillset(skillset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return item


@router.put("/api/v1/skillsets/{skillset_id}", response_model=SkillsetListItem)
async def update_skillset(
    skillset_id: str,
    body: UpdateSkillsetRequest,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_WRITE)),
) -> SkillsetListItem:
    item = await report_store.update_skillset(
        skillset_id=skillset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not item:
        files = await _plugin_files(skillset_id)
        if files is not None:
            by_path = {file.path: file for file in files}
            manifest_file = by_path.get("plugin.json")
            if manifest_file is not None:
                manifest = json.loads(manifest_file.content)
                manifest["description"] = body.description
                by_path["plugin.json"] = PluginFile(
                    path="plugin.json",
                    content=json.dumps(manifest, indent=2, sort_keys=True).encode(),
                    media_type="application/json",
                )
                if await _publish_legacy_edit(skillset_id, list(by_path.values()), current, body.comment):
                    enabled_plugin = await report_store.set_plugin_enabled(
                        skillset_id, body.enabled, current.user.user_id
                    )
                    item = await report_store.get_skillset(skillset_id) if enabled_plugin else None
    if not item:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return item


@router.delete("/api/v1/skillsets/{skillset_id}", response_model=SkillsetIdResponse)
async def delete_skillset(
    skillset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_DELETE)),
) -> SkillsetIdResponse:
    ok = await report_store.delete_skillset(skillset_id)
    if not ok:
        ok = await report_store.delete_plugin(skillset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return SkillsetIdResponse(skillset_id=skillset_id)


@router.get(
    "/api/v1/skillsets/{skillset_id}/versions",
    response_model=SkillsetVersionListResponse,
)
async def list_skillset_versions(
    skillset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_READ)),
) -> SkillsetVersionListResponse:
    item = await report_store.get_skillset(skillset_id)
    if not item:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return SkillsetVersionListResponse(versions=await report_store.list_skillset_versions(skillset_id))


@router.get(
    "/api/v1/skillsets/{skillset_id}/versions/{version}",
    response_model=SkillsetVersion,
)
async def get_skillset_version(
    skillset_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_READ)),
) -> SkillsetVersion:
    v = await report_store.get_skillset_version(skillset_id, version)
    if not v:
        raise HTTPException(status_code=404, detail="Skillset version not found")
    return v


@router.get(
    "/api/v1/skillsets/{skillset_id}/skills",
    response_model=SkillListResponse,
)
async def list_skills(
    skillset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_READ)),
) -> SkillListResponse:
    ss = await report_store.get_skillset(skillset_id)
    if not ss:
        raise HTTPException(status_code=404, detail="Skillset not found")
    skills = await report_store.list_skills(skillset_id)
    return SkillListResponse(skills=[_with_effective_skill_state(skill, ss) for skill in skills])


@router.post(
    "/api/v1/skillsets/{skillset_id}/skills",
    response_model=SkillItem,
    status_code=201,
)
async def create_skill(
    skillset_id: str,
    body: CreateSkillRequest,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_WRITE)),
) -> Any:
    if await report_store.get_skill(body.skill_id) or await report_store.get_plugin_skill(skillset_id, body.skill_id):
        raise HTTPException(status_code=409, detail="Skill already exists")
    errors = validate_skill_template(body.parameters, body.template)
    if errors:
        return JSONResponse(content={"errors": errors}, status_code=400)
    valid_tools, dropped_tools = await _check_tools_required(body.tools_required)
    if dropped_tools:
        logger.warning("Dropping missing tool references for new skill %s: %s", body.skill_id, dropped_tools)
    skill = await report_store.create_skill(
        skillset_id=skillset_id,
        skill_id=body.skill_id,
        name=body.name,
        description=body.description,
        template=body.template,
        parameters=[p.model_dump() for p in body.parameters],
        triggers=body.triggers,
        tools_required=valid_tools,
        enabled=body.enabled,
        created_by=current.user.user_id,
    )
    if not skill:
        skill = await _edit_plugin_skill(
            skillset_id,
            body.skill_id,
            body.model_copy(update={"tools_required": valid_tools}),
            current,
        )
    if not skill:
        raise HTTPException(status_code=404, detail="Skillset not found")
    skillset = await report_store.get_skillset(skillset_id)
    if not skillset:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return _with_effective_skill_state(skill, skillset)


@router.get(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}",
    response_model=SkillItem,
)
async def get_skill(
    skillset_id: str,
    skill_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_READ)),
) -> SkillItem:
    skill = await _find_skill(skillset_id, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skillset = await report_store.get_skillset(skillset_id)
    if not skillset:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return _with_effective_skill_state(skill, skillset)


@router.put(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}",
    response_model=SkillItem,
)
async def update_skill(
    skillset_id: str,
    skill_id: str,
    body: UpdateSkillRequest,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_WRITE)),
) -> Any:
    existing = await _find_skill(skillset_id, skill_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill not found")
    errors = validate_skill_template(body.parameters, body.template)
    if errors:
        return JSONResponse(content={"errors": errors}, status_code=400)
    valid_tools, dropped_tools = await _check_tools_required(body.tools_required)
    if dropped_tools:
        logger.warning("Dropping missing tool references for skill %s: %s", skill_id, dropped_tools)
    skill = await report_store.update_skill(
        skill_id=skill_id,
        name=body.name,
        description=body.description,
        template=body.template,
        parameters=[p.model_dump() for p in body.parameters],
        triggers=body.triggers,
        tools_required=valid_tools,
        enabled=body.enabled,
        updated_by=current.user.user_id,
        comment=body.comment,
    )
    if not skill:
        skill = await _edit_plugin_skill(
            skillset_id,
            skill_id,
            body.model_copy(update={"tools_required": valid_tools}),
            current,
        )
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skillset = await report_store.get_skillset(skillset_id)
    if not skillset:
        raise HTTPException(status_code=404, detail="Skillset not found")
    return _with_effective_skill_state(skill, skillset)


@router.delete(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}",
    response_model=SkillIdResponse,
)
async def delete_skill(
    skillset_id: str,
    skill_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_DELETE)),
) -> SkillIdResponse:
    existing = await _find_skill(skillset_id, skill_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Skill not found")
    ok = await report_store.delete_skill(skill_id)
    if not ok:
        plugin_skill = await report_store.get_plugin_skill(skillset_id, skill_id)
        if plugin_skill:
            await _edit_plugin_skill(skillset_id, skill_id, None, current, delete=True)
            ok = await report_store.get_plugin_skill(skillset_id, skill_id) is None
    if not ok:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillIdResponse(skill_id=skill_id)


@router.post(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}/render",
    response_model=RenderSkillResponse,
)
async def render_skill(
    skillset_id: str,
    skill_id: str,
    body: RenderSkillRequest,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_RENDER)),
) -> Any:
    skill = await _find_skill(skillset_id, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    skillset = await report_store.get_skillset(skillset_id)
    if not skillset:
        raise HTTPException(status_code=404, detail="Skillset not found")
    if not skillset.enabled:
        raise HTTPException(status_code=400, detail="Skillset is disabled")
    if not skill.enabled:
        raise HTTPException(status_code=400, detail="Skill is disabled")
    prompt, errors = render_skill_parts(
        skill.parameters,
        skill.template,
        body.arguments,
        skill.triggers,
        skill.tools_required,
    )
    if errors or prompt is None:
        return JSONResponse(content={"errors": errors}, status_code=400)
    # The body alone, as this endpoint has always returned. A legacy skill
    # substitutes its values inline, so an inputs block here would repeat them
    # and change a response its callers already parse (AGT-039).
    return RenderSkillResponse(text=prompt.body)


@router.get(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}/versions",
    response_model=SkillVersionListResponse,
)
async def list_skill_versions(
    skillset_id: str,
    skill_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_READ)),
) -> SkillVersionListResponse:
    skill = await _find_skill(skillset_id, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillVersionListResponse(versions=await report_store.list_skill_versions(skill_id, skillset_id))


@router.get(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}/versions/{version}",
    response_model=SkillVersion,
)
async def get_skill_version(
    skillset_id: str,
    skill_id: str,
    version: int,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_READ)),
) -> SkillVersion:
    v = await report_store.get_skill_version(skill_id, version, skillset_id)
    if not v or v.skillset_id != skillset_id:
        raise HTTPException(status_code=404, detail="Skill version not found")
    return v
