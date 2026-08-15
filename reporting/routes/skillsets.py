import logging
from typing import Any

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
    render_skill_prompt,
    validate_skill_template,
)
from reporting.services import external_mcp, report_store
from reporting.services.mcp_builtins import find_builtin

router = APIRouter()
logger = logging.getLogger(__name__)


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
        raise HTTPException(status_code=404, detail="Skillset not found")
    return item


@router.delete("/api/v1/skillsets/{skillset_id}", response_model=SkillsetIdResponse)
async def delete_skillset(
    skillset_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLSETS_DELETE)),
) -> SkillsetIdResponse:
    ok = await report_store.delete_skillset(skillset_id)
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
    if await report_store.get_skill(body.skill_id):
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
    skill = await report_store.get_skill(skill_id)
    if not skill or skill.skillset_id != skillset_id:
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
    existing = await report_store.get_skill(skill_id)
    if not existing or existing.skillset_id != skillset_id:
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
    existing = await report_store.get_skill(skill_id)
    if not existing or existing.skillset_id != skillset_id:
        raise HTTPException(status_code=404, detail="Skill not found")
    ok = await report_store.delete_skill(skill_id)
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
    skill = await report_store.get_skill(skill_id)
    if not skill or skill.skillset_id != skillset_id:
        raise HTTPException(status_code=404, detail="Skill not found")
    skillset = await report_store.get_skillset(skillset_id)
    if not skillset:
        raise HTTPException(status_code=404, detail="Skillset not found")
    if not skillset.enabled:
        raise HTTPException(status_code=400, detail="Skillset is disabled")
    if not skill.enabled:
        raise HTTPException(status_code=400, detail="Skill is disabled")
    rendered, errors = render_skill_prompt(
        skill.parameters,
        skill.template,
        body.arguments,
        skill.triggers,
        skill.tools_required,
    )
    if errors or rendered is None:
        return JSONResponse(content={"errors": errors}, status_code=400)
    return RenderSkillResponse(text=rendered)


@router.get(
    "/api/v1/skillsets/{skillset_id}/skills/{skill_id}/versions",
    response_model=SkillVersionListResponse,
)
async def list_skill_versions(
    skillset_id: str,
    skill_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SKILLS_READ)),
) -> SkillVersionListResponse:
    skill = await report_store.get_skill(skill_id)
    if not skill or skill.skillset_id != skillset_id:
        raise HTTPException(status_code=404, detail="Skill not found")
    return SkillVersionListResponse(versions=await report_store.list_skill_versions(skill_id))


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
    v = await report_store.get_skill_version(skill_id, version)
    if not v or v.skillset_id != skillset_id:
        raise HTTPException(status_code=404, detail="Skill version not found")
    return v
