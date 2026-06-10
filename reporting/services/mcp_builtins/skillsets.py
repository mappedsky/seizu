"""Built-in ``skillsets__*`` tools — manage user-defined MCP skills."""

from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.mcp_config import (
    CreateSkillRequest,
    CreateSkillsetRequest,
    RenderSkillRequest,
    UpdateSkillRequest,
    UpdateSkillsetRequest,
    render_skill_template,
    validate_skill_template,
)
from reporting.services import report_store
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema

GROUP = "skillsets"


def _require_user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _skillset_id_prop() -> dict[str, Any]:
    return {"skillset_id": {"type": "string"}}


def _skill_id_prop() -> dict[str, Any]:
    return {"skill_id": {"type": "string"}}


def _target(action: str, resource_type: str, resource_id: Any) -> ActionConfirmationTarget:
    return ActionConfirmationTarget(action=action, resource_type=resource_type, resource_id=str(resource_id))


async def _confirm_skillset_create(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("create", "skillset", args.get("skillset_id", "new"))


async def _confirm_skillset_update(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("update", "skillset", args["skillset_id"])


async def _confirm_skillset_delete(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return _target("delete", "skillset", args["skillset_id"])


async def _confirm_skill_create(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("create", "skill", args.get("skill_id", "new"))


async def _confirm_skill_update(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("update", "skill", args["skill_id"])


async def _confirm_skill_delete(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return _target("delete", "skill", args["skill_id"])


async def _list_skillsets(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    items = await report_store.list_skillsets()
    return {"skillsets": [i.model_dump() for i in items]}


async def _get_skillset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    item = await report_store.get_skillset(args["skillset_id"])
    if not item:
        return {"error": "Skillset not found"}
    return item.model_dump()


async def _create_skillset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    body = CreateSkillsetRequest.model_validate(args)
    if await report_store.get_skillset(body.skillset_id):
        return {"error": "Skillset already exists"}
    item = await report_store.create_skillset(
        skillset_id=body.skillset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        created_by=user.user.user_id,
    )
    return item.model_dump()


async def _update_skillset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    skillset_id = args["skillset_id"]
    body = UpdateSkillsetRequest.model_validate({k: v for k, v in args.items() if k != "skillset_id"})
    item = await report_store.update_skillset(
        skillset_id=skillset_id,
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        updated_by=user.user.user_id,
        comment=body.comment,
    )
    if not item:
        return {"error": "Skillset not found"}
    return item.model_dump()


async def _delete_skillset(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    ok = await report_store.delete_skillset(args["skillset_id"])
    if not ok:
        return {"error": "Skillset not found"}
    return {"skillset_id": args["skillset_id"]}


async def _list_skillset_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skillset_id = args["skillset_id"]
    item = await report_store.get_skillset(skillset_id)
    if not item:
        return {"error": "Skillset not found"}
    versions = await report_store.list_skillset_versions(skillset_id)
    return {"versions": [v.model_dump() for v in versions]}


async def _get_skillset_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    v = await report_store.get_skillset_version(args["skillset_id"], int(args["version"]))
    if not v:
        return {"error": "Skillset version not found"}
    return v.model_dump()


async def _list_skills(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skillset_id = args["skillset_id"]
    ss = await report_store.get_skillset(skillset_id)
    if not ss:
        return {"error": "Skillset not found"}
    skills = await report_store.list_skills(skillset_id)
    return {"skills": [s.model_dump() for s in skills]}


async def _get_skill(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skill = await report_store.get_skill(args["skill_id"])
    if not skill or skill.skillset_id != args["skillset_id"]:
        return {"error": "Skill not found"}
    return skill.model_dump()


async def _create_skill(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    skillset_id = args["skillset_id"]
    body = CreateSkillRequest.model_validate({k: v for k, v in args.items() if k != "skillset_id"})
    if await report_store.get_skill(body.skill_id):
        return {"error": "Skill already exists"}
    errors = validate_skill_template(body.parameters, body.template)
    if errors:
        return {"errors": errors}
    skill = await report_store.create_skill(
        skillset_id=skillset_id,
        skill_id=body.skill_id,
        name=body.name,
        description=body.description,
        template=body.template,
        parameters=[p.model_dump() for p in body.parameters],
        triggers=body.triggers,
        tools_required=body.tools_required,
        enabled=body.enabled,
        created_by=user.user.user_id,
    )
    if not skill:
        return {"error": "Skillset not found"}
    return skill.model_dump()


async def _update_skill(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    skillset_id = args["skillset_id"]
    skill_id = args["skill_id"]
    existing = await report_store.get_skill(skill_id)
    if not existing or existing.skillset_id != skillset_id:
        return {"error": "Skill not found"}
    body = UpdateSkillRequest.model_validate({k: v for k, v in args.items() if k not in ("skillset_id", "skill_id")})
    errors = validate_skill_template(body.parameters, body.template)
    if errors:
        return {"errors": errors}
    skill = await report_store.update_skill(
        skill_id=skill_id,
        name=body.name,
        description=body.description,
        template=body.template,
        parameters=[p.model_dump() for p in body.parameters],
        triggers=body.triggers,
        tools_required=body.tools_required,
        enabled=body.enabled,
        updated_by=user.user.user_id,
        comment=body.comment,
    )
    if not skill:
        return {"error": "Skill not found"}
    return skill.model_dump()


async def _delete_skill(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skill_id = args["skill_id"]
    existing = await report_store.get_skill(skill_id)
    if not existing or existing.skillset_id != args["skillset_id"]:
        return {"error": "Skill not found"}
    ok = await report_store.delete_skill(skill_id)
    if not ok:
        return {"error": "Skill not found"}
    return {"skill_id": skill_id}


async def _render_skill(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skill_id = args["skill_id"]
    skill = await report_store.get_skill(skill_id)
    if not skill or skill.skillset_id != args["skillset_id"]:
        return {"error": "Skill not found"}
    body = RenderSkillRequest.model_validate({"arguments": args.get("arguments", {})})
    rendered, errors = render_skill_template(skill.parameters, skill.template, body.arguments)
    if errors or rendered is None:
        return {"errors": errors}
    return {"text": rendered}


async def _list_skill_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    skill_id = args["skill_id"]
    skill = await report_store.get_skill(skill_id)
    if not skill or skill.skillset_id != args["skillset_id"]:
        return {"error": "Skill not found"}
    versions = await report_store.list_skill_versions(skill_id)
    return {"versions": [v.model_dump() for v in versions]}


async def _get_skill_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    v = await report_store.get_skill_version(args["skill_id"], int(args["version"]))
    if not v or v.skillset_id != args["skillset_id"]:
        return {"error": "Skill version not found"}
    return v.model_dump()


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="skillsets__list",
            group=GROUP,
            description="List all skillsets.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.SKILLSETS_READ.value],
            handler=_list_skillsets,
        ),
        BuiltinTool(
            name="skillsets__get",
            group=GROUP,
            description="Return a skillset by ID.",
            input_schema={"type": "object", "properties": _skillset_id_prop(), "required": ["skillset_id"]},
            required_permissions=[Permission.SKILLSETS_READ.value],
            handler=_get_skillset,
        ),
        BuiltinTool(
            name="skillsets__create",
            group=GROUP,
            description="Create a new skillset.",
            input_schema=model_input_schema(CreateSkillsetRequest),
            required_permissions=[Permission.SKILLSETS_WRITE.value],
            handler=_create_skillset,
            requires_user=True,
            confirmation=_confirm_skillset_create,
        ),
        BuiltinTool(
            name="skillsets__update",
            group=GROUP,
            description="Update a skillset.",
            input_schema=model_input_schema(
                UpdateSkillsetRequest,
                extra_properties=_skillset_id_prop(),
                extra_required=["skillset_id"],
            ),
            required_permissions=[Permission.SKILLSETS_WRITE.value],
            handler=_update_skillset,
            requires_user=True,
            confirmation=_confirm_skillset_update,
        ),
        BuiltinTool(
            name="skillsets__delete",
            group=GROUP,
            description="Delete a skillset and all its skills.",
            input_schema={"type": "object", "properties": _skillset_id_prop(), "required": ["skillset_id"]},
            required_permissions=[Permission.SKILLSETS_DELETE.value],
            handler=_delete_skillset,
            confirmation=_confirm_skillset_delete,
        ),
        BuiltinTool(
            name="skillsets__list_versions",
            group=GROUP,
            description="List all versions of a skillset.",
            input_schema={"type": "object", "properties": _skillset_id_prop(), "required": ["skillset_id"]},
            required_permissions=[Permission.SKILLSETS_READ.value],
            handler=_list_skillset_versions,
        ),
        BuiltinTool(
            name="skillsets__get_version",
            group=GROUP,
            description="Return a specific version of a skillset.",
            input_schema={
                "type": "object",
                "properties": {**_skillset_id_prop(), "version": {"type": "integer"}},
                "required": ["skillset_id", "version"],
            },
            required_permissions=[Permission.SKILLSETS_READ.value],
            handler=_get_skillset_version,
        ),
        BuiltinTool(
            name="skillsets__list_skills",
            group=GROUP,
            description="List all skills in a skillset.",
            input_schema={"type": "object", "properties": _skillset_id_prop(), "required": ["skillset_id"]},
            required_permissions=[Permission.SKILLS_READ.value],
            handler=_list_skills,
        ),
        BuiltinTool(
            name="skillsets__get_skill",
            group=GROUP,
            description="Return a skill by ID.",
            input_schema={
                "type": "object",
                "properties": {**_skillset_id_prop(), **_skill_id_prop()},
                "required": ["skillset_id", "skill_id"],
            },
            required_permissions=[Permission.SKILLS_READ.value],
            handler=_get_skill,
        ),
        BuiltinTool(
            name="skillsets__create_skill",
            group=GROUP,
            description="Create a new skill within a skillset.",
            input_schema=model_input_schema(
                CreateSkillRequest,
                extra_properties=_skillset_id_prop(),
                extra_required=["skillset_id"],
            ),
            required_permissions=[Permission.SKILLS_WRITE.value],
            handler=_create_skill,
            requires_user=True,
            confirmation=_confirm_skill_create,
        ),
        BuiltinTool(
            name="skillsets__update_skill",
            group=GROUP,
            description="Update a skill.",
            input_schema=model_input_schema(
                UpdateSkillRequest,
                extra_properties={**_skillset_id_prop(), **_skill_id_prop()},
                extra_required=["skillset_id", "skill_id"],
            ),
            required_permissions=[Permission.SKILLS_WRITE.value],
            handler=_update_skill,
            requires_user=True,
            confirmation=_confirm_skill_update,
        ),
        BuiltinTool(
            name="skillsets__delete_skill",
            group=GROUP,
            description="Delete a skill.",
            input_schema={
                "type": "object",
                "properties": {**_skillset_id_prop(), **_skill_id_prop()},
                "required": ["skillset_id", "skill_id"],
            },
            required_permissions=[Permission.SKILLS_DELETE.value],
            handler=_delete_skill,
            confirmation=_confirm_skill_delete,
        ),
        BuiltinTool(
            name="skillsets__render_skill",
            group=GROUP,
            description="Render a skill prompt template.",
            input_schema={
                "type": "object",
                "properties": {
                    **_skillset_id_prop(),
                    **_skill_id_prop(),
                    "arguments": {"type": "object"},
                },
                "required": ["skillset_id", "skill_id"],
            },
            required_permissions=[Permission.SKILLS_RENDER.value],
            handler=_render_skill,
        ),
        BuiltinTool(
            name="skillsets__list_skill_versions",
            group=GROUP,
            description="List all versions of a skill.",
            input_schema={
                "type": "object",
                "properties": {**_skillset_id_prop(), **_skill_id_prop()},
                "required": ["skillset_id", "skill_id"],
            },
            required_permissions=[Permission.SKILLS_READ.value],
            handler=_list_skill_versions,
        ),
        BuiltinTool(
            name="skillsets__get_skill_version",
            group=GROUP,
            description="Return a specific version of a skill.",
            input_schema={
                "type": "object",
                "properties": {
                    **_skillset_id_prop(),
                    **_skill_id_prop(),
                    "version": {"type": "integer"},
                },
                "required": ["skillset_id", "skill_id", "version"],
            },
            required_permissions=[Permission.SKILLS_READ.value],
            handler=_get_skill_version,
        ),
    ],
)
