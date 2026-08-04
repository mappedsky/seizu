"""Built-in ``spaces__*`` tools — manage spaces, sub-spaces, and membership.

Every handler here is the MCP counterpart of a route in
``reporting/routes/spaces.py``, and both go through
``reporting.services.spaces`` for the cross-entity rules so the two cannot
drift. Spaces carry no version history, so this group has no ``*_versions``
tools — unlike toolsets, reports, and roles.

Filing a report into a space lives here rather than in the ``reports`` group:
it is the space feature, and an operator who disables the group should lose the
whole of it. Its permission still matches the REST route (``reports:write``
alone), because filing a report is a report edit and spaces are globally
visible.
"""

from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.space_config import (
    CreateSpaceRequest,
    CreateSubspaceRequest,
    SetReportSpaceRequest,
    SetSpaceOverviewRequest,
    SpaceConflictError,
    SpaceDeleteResult,
    SpaceListItem,
    SubspaceItem,
    UpdateSpaceRequest,
    UpdateSubspaceRequest,
)
from reporting.services import report_store
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema
from reporting.services.spaces import (
    DUPLICATE_SPACE_NAME_DETAIL,
    DUPLICATE_SUBSPACE_NAME_DETAIL,
    SpaceValidationError,
    find_duplicate_space_name,
    find_duplicate_subspace_name,
    reject_filing_private_report,
    resolve_overview_report,
    resolve_report_space,
    with_resolved_overview,
    with_resolved_subspace,
    without_overview,
)

GROUP = "spaces"


def _require_user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _space_id_prop() -> dict[str, Any]:
    return {"space_id": {"type": "string", "description": "The space ID."}}


def _subspace_id_prop() -> dict[str, Any]:
    return {"subspace_id": {"type": "string", "description": "The sub-space ID."}}


def _report_id_prop() -> dict[str, Any]:
    return {"report_id": {"type": "string", "description": "The report ID."}}


def _target(action: str, resource_type: str, resource_id: Any) -> ActionConfirmationTarget:
    return ActionConfirmationTarget(action=action, resource_type=resource_type, resource_id=str(resource_id))


async def _get_space(space_id: str) -> SpaceListItem | None:
    return await report_store.get_space(space_id)


async def _get_subspace_of(space_id: str, subspace_id: str) -> SubspaceItem | None:
    """Fetch a sub-space, rejecting one that belongs to a different space.

    Sub-space IDs are globally unique, so every handler re-checks the parent
    link the same way the routes do.
    """
    subspace = await report_store.get_subspace(subspace_id)
    if not subspace or subspace.space_id != space_id:
        return None
    return subspace


# ---------------------------------------------------------------------------
# Space handlers
# ---------------------------------------------------------------------------


async def _list_spaces(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    spaces = await report_store.list_spaces()
    return {"spaces": [without_overview(space).model_dump() for space in spaces]}


async def _get_space_tool(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    space = await _get_space(args["space_id"])
    if not space:
        return {"error": "Space not found"}
    # No report list here to resolve the pointer against — see without_overview.
    return without_overview(space).model_dump()


async def _get_tree(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    space_id = args["space_id"]
    space = await _get_space(space_id)
    if not space:
        return {"error": "Space not found"}
    subspaces = await report_store.list_subspaces(space_id)
    reports = await report_store.list_space_reports(space_id, user_id=user.user.user_id)
    return {
        "space": with_resolved_overview(space, reports).model_dump(),
        "subspaces": [s.model_dump() for s in subspaces],
        "reports": [r.model_dump() for r in with_resolved_subspace(reports, subspaces)],
    }


async def _create_space(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    body = CreateSpaceRequest.model_validate(args)
    if await find_duplicate_space_name(body.name):
        return {"error": DUPLICATE_SPACE_NAME_DETAIL}
    space = await report_store.create_space(
        name=body.name,
        description=body.description,
        created_by=user.user.user_id,
    )
    return space.model_dump()


async def _update_space(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    space_id = args["space_id"]
    body = UpdateSpaceRequest.model_validate({k: v for k, v in args.items() if k != "space_id"})
    if await find_duplicate_space_name(body.name, exclude_space_id=space_id):
        return {"error": DUPLICATE_SPACE_NAME_DETAIL}
    updated = await report_store.update_space(
        space_id=space_id,
        name=body.name,
        description=body.description,
        updated_by=user.user.user_id,
    )
    if not updated:
        return {"error": "Space not found"}
    return without_overview(updated).model_dump()


async def _delete_space(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    space_id = args["space_id"]
    result = await report_store.delete_space(space_id)
    if result is SpaceDeleteResult.NOT_FOUND:
        return {"error": "Space not found"}
    if result is SpaceDeleteResult.NOT_EMPTY:
        return {"error": "Move every report out of the space before deleting it"}
    return {"space_id": space_id}


async def _set_overview(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    space_id = args["space_id"]
    body = SetSpaceOverviewRequest.model_validate({k: v for k, v in args.items() if k != "space_id"})
    if not await _get_space(space_id):
        return {"error": "Space not found"}
    try:
        report_id = await resolve_overview_report(space_id, body.report_id, user_id=user.user.user_id)
    except SpaceValidationError as exc:
        return {"error": str(exc)}
    updated = await report_store.set_space_overview(
        space_id=space_id,
        report_id=report_id,
        updated_by=user.user.user_id,
    )
    if not updated:
        return {"error": "Space not found"}
    # Echoes back a pointer the caller supplied and was authorised to see, so
    # unlike the other space responses this one keeps it.
    return updated.model_dump()


# ---------------------------------------------------------------------------
# Sub-space handlers
# ---------------------------------------------------------------------------


async def _list_subspaces(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    space_id = args["space_id"]
    if not await _get_space(space_id):
        return {"error": "Space not found"}
    subspaces = await report_store.list_subspaces(space_id)
    return {"subspaces": [s.model_dump() for s in subspaces]}


async def _create_subspace(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    space_id = args["space_id"]
    body = CreateSubspaceRequest.model_validate({k: v for k, v in args.items() if k != "space_id"})
    if not await _get_space(space_id):
        return {"error": "Space not found"}
    if await find_duplicate_subspace_name(space_id, body.name):
        return {"error": DUPLICATE_SUBSPACE_NAME_DETAIL}
    created = await report_store.create_subspace(
        space_id=space_id,
        name=body.name,
        created_by=user.user.user_id,
    )
    if not created:
        return {"error": "Space not found"}
    return created.model_dump()


async def _update_subspace(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    space_id = args["space_id"]
    subspace_id = args["subspace_id"]
    body = UpdateSubspaceRequest.model_validate({k: v for k, v in args.items() if k not in ("space_id", "subspace_id")})
    if not await _get_subspace_of(space_id, subspace_id):
        return {"error": "Sub-space not found"}
    if await find_duplicate_subspace_name(space_id, body.name, exclude_subspace_id=subspace_id):
        return {"error": DUPLICATE_SUBSPACE_NAME_DETAIL}
    updated = await report_store.update_subspace(
        subspace_id=subspace_id,
        name=body.name,
        updated_by=user.user.user_id,
    )
    if not updated:
        return {"error": "Sub-space not found"}
    return updated.model_dump()


async def _delete_subspace(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    space_id = args["space_id"]
    subspace_id = args["subspace_id"]
    if not await _get_subspace_of(space_id, subspace_id):
        return {"error": "Sub-space not found"}
    ok = await report_store.delete_subspace(subspace_id)
    if not ok:
        return {"error": "Sub-space not found"}
    return {"subspace_id": subspace_id}


# ---------------------------------------------------------------------------
# Report membership
# ---------------------------------------------------------------------------


async def _set_report_space(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    """Move a report between spaces, or out of one.

    Replace semantics, matching the route: an omitted ``subspace_id`` clears it,
    which is what makes "moving to another space drops the sub-space" fall out.
    """
    user = _require_user(current_user)
    report_id = args["report_id"]
    body = SetReportSpaceRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    try:
        space_id, subspace_id = await resolve_report_space(body.space_id, body.subspace_id)
    except SpaceValidationError as exc:
        return {"error": str(exc)}
    try:
        if space_id is not None:
            # Only filing needs the report's access: removing one from its space
            # is always allowed, whatever its visibility.
            meta = await report_store.get_report_metadata(report_id, user_id=user.user.user_id)
            if not meta:
                return {"error": "Report not found"}
            reject_filing_private_report(space_id, meta.access)
        # The store re-checks the same rule atomically, so the raise can come
        # from either place.
        updated = await report_store.update_report_space(
            report_id=report_id,
            space_id=space_id,
            subspace_id=subspace_id,
            updated_by=user.user.user_id,
            user_id=user.user.user_id,
        )
    except SpaceConflictError as exc:
        return {"error": str(exc)}
    if not updated:
        return {"error": "Report not found"}
    return updated.model_dump()


# ---------------------------------------------------------------------------
# Confirmation resolvers
#
# Every mutating tool in this group is gated. A space is a shared, globally
# visible container: creating, renaming, or deleting one is visible to every
# user, and filing a report into one publishes it. None of them get the
# "creates a private draft" exception ``reports__create`` relies on.
# ---------------------------------------------------------------------------


async def _confirm_space_create(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("create", "space", args.get("name", ""))


async def _confirm_space_update(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("update", "space", args["space_id"])


async def _confirm_space_delete(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("delete", "space", args["space_id"])


async def _confirm_space_overview(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("set_overview", "space", args["space_id"])


async def _confirm_subspace_create(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("create", "subspace", args.get("name", ""))


async def _confirm_subspace_update(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("update", "subspace", args["subspace_id"])


async def _confirm_subspace_delete(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    return _target("delete", "subspace", args["subspace_id"])


async def _confirm_report_space(args: dict[str, Any], current_user: CurrentUser | None) -> ActionConfirmationTarget:
    """Gate every move, in both directions.

    Filing publishes a report into a shared container; unfiling takes it out of
    one other people are reading. Both are visible changes, so neither gets the
    conditional treatment ``reports__create`` has.
    """
    action = "file into space" if args.get("space_id") is not None else "remove from space"
    return _target(action, "report", args["report_id"])


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="spaces__list",
            group=GROUP,
            description="List all spaces.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.SPACES_READ.value],
            handler=_list_spaces,
            collection_key="spaces",
        ),
        BuiltinTool(
            name="spaces__get",
            group=GROUP,
            description="Return a space by ID.",
            input_schema={
                "type": "object",
                "properties": _space_id_prop(),
                "required": ["space_id"],
            },
            required_permissions=[Permission.SPACES_READ.value],
            handler=_get_space_tool,
        ),
        BuiltinTool(
            name="spaces__get_tree",
            group=GROUP,
            description=(
                "Return a space with its sub-spaces and the reports filed in it that are visible to the caller."
            ),
            input_schema={
                "type": "object",
                "properties": _space_id_prop(),
                "required": ["space_id"],
            },
            required_permissions=[Permission.SPACES_READ.value],
            handler=_get_tree,
            requires_user=True,
        ),
        BuiltinTool(
            name="spaces__create",
            group=GROUP,
            description="Create an empty space.",
            input_schema=model_input_schema(CreateSpaceRequest),
            required_permissions=[Permission.SPACES_WRITE.value],
            handler=_create_space,
            requires_user=True,
            confirmation=_confirm_space_create,
        ),
        BuiltinTool(
            name="spaces__update",
            group=GROUP,
            description="Rename a space or change its description.",
            input_schema=model_input_schema(
                UpdateSpaceRequest,
                extra_properties=_space_id_prop(),
                extra_required=["space_id"],
            ),
            required_permissions=[Permission.SPACES_WRITE.value],
            handler=_update_space,
            requires_user=True,
            confirmation=_confirm_space_update,
        ),
        BuiltinTool(
            name="spaces__delete",
            group=GROUP,
            description=(
                "Delete a space that holds no reports, along with its sub-spaces. No report is ever deleted with it."
            ),
            input_schema={
                "type": "object",
                "properties": _space_id_prop(),
                "required": ["space_id"],
            },
            required_permissions=[Permission.SPACES_DELETE.value],
            handler=_delete_space,
            confirmation=_confirm_space_delete,
        ),
        BuiltinTool(
            name="spaces__set_overview",
            group=GROUP,
            description=(
                "Point a space at one of its own reports as the landing page. Pass a null report_id to clear it."
            ),
            input_schema=model_input_schema(
                SetSpaceOverviewRequest,
                extra_properties=_space_id_prop(),
                extra_required=["space_id"],
            ),
            required_permissions=[Permission.SPACES_WRITE.value],
            handler=_set_overview,
            requires_user=True,
            confirmation=_confirm_space_overview,
        ),
        BuiltinTool(
            name="spaces__list_subspaces",
            group=GROUP,
            description="List the sub-spaces of a space.",
            input_schema={
                "type": "object",
                "properties": _space_id_prop(),
                "required": ["space_id"],
            },
            required_permissions=[Permission.SPACES_READ.value],
            handler=_list_subspaces,
            collection_key="subspaces",
        ),
        BuiltinTool(
            name="spaces__create_subspace",
            group=GROUP,
            description="Create a sub-space within a space.",
            input_schema=model_input_schema(
                CreateSubspaceRequest,
                extra_properties=_space_id_prop(),
                extra_required=["space_id"],
            ),
            required_permissions=[Permission.SPACES_WRITE.value],
            handler=_create_subspace,
            requires_user=True,
            confirmation=_confirm_subspace_create,
        ),
        BuiltinTool(
            name="spaces__update_subspace",
            group=GROUP,
            description="Rename a sub-space.",
            input_schema=model_input_schema(
                UpdateSubspaceRequest,
                extra_properties={**_space_id_prop(), **_subspace_id_prop()},
                extra_required=["space_id", "subspace_id"],
            ),
            required_permissions=[Permission.SPACES_WRITE.value],
            handler=_update_subspace,
            requires_user=True,
            confirmation=_confirm_subspace_update,
        ),
        BuiltinTool(
            name="spaces__delete_subspace",
            group=GROUP,
            description="Delete a sub-space; the reports in it fall back to ungrouped.",
            input_schema={
                "type": "object",
                "properties": {**_space_id_prop(), **_subspace_id_prop()},
                "required": ["space_id", "subspace_id"],
            },
            required_permissions=[Permission.SPACES_DELETE.value],
            handler=_delete_subspace,
            confirmation=_confirm_subspace_delete,
        ),
        BuiltinTool(
            name="spaces__set_report_space",
            group=GROUP,
            description=(
                "File a report into a space and optionally a sub-space, or remove it from its space by passing "
                "a null space_id. A report filed in a space is public, so a draft must be published first. "
                "Omitting subspace_id clears the sub-space."
            ),
            input_schema=model_input_schema(
                SetReportSpaceRequest,
                extra_properties=_report_id_prop(),
                extra_required=["report_id"],
            ),
            # Matches the REST route: filing a report is a report edit, and
            # spaces are globally visible so there is nothing to leak by letting
            # any report author file into any space.
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_set_report_space,
            requires_user=True,
            confirmation=_confirm_report_space,
        ),
    ],
)
