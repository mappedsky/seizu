"""Built-in ``reports__*`` tools — CRUD + pin/dashboard for reports."""

from typing import Any

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import Permission
from reporting.schema.confirmations import ActionConfirmationTarget
from reporting.schema.report_config import (
    CloneReportRequest,
    CreateReportRequest,
    CreateVersionRequest,
    PinReportRequest,
    UpdateReportVisibilityRequest,
)
from reporting.services import report_store
from reporting.services.mcp_builtins.base import BuiltinGroup, BuiltinTool, model_input_schema
from reporting.services.spaces import (
    SPACE_MEMBER_ACCESS,
    SpaceConflictError,
    SpaceValidationError,
    reject_privatising_space_member,
    resolve_clone_space,
    resolve_report_space,
)

GROUP = "reports"


def _require_user(current_user: CurrentUser | None) -> CurrentUser:
    if current_user is None:
        raise RuntimeError("No current user on the request context")
    return current_user


def _report_id_prop() -> dict[str, Any]:
    return {"report_id": {"type": "string", "description": "The report ID."}}


async def _list(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    reports = await report_store.list_reports(user_id=user.user.user_id)
    return {"reports": [r.model_dump() for r in reports]}


async def _get(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    report = await report_store.get_report_latest(args["report_id"], user_id=user.user.user_id)
    if not report:
        return {"error": "Report not found"}
    return report.model_dump()


async def _get_dashboard(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    report = await report_store.get_dashboard_report()
    if not report:
        return {"error": "No dashboard report configured"}
    return report.model_dump()


async def _create(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    body = CreateReportRequest.model_validate(args)
    try:
        space_id, subspace_id = await resolve_report_space(body.space_id, body.subspace_id)
    except SpaceValidationError as exc:
        return {"error": str(exc)}
    try:
        item = await report_store.create_report(
            name=body.name,
            created_by=user.user.user_id,
            # Space members are public; standalone reports stay drafts.
            access=SPACE_MEMBER_ACCESS if space_id is not None else None,
            space_id=space_id,
            subspace_id=subspace_id,
        )
    # Unreachable while the access above is chosen from space_id; kept so the
    # store's invariant surfaces as an error rather than a traceback.
    except SpaceConflictError as exc:
        return {"error": str(exc)}
    return item.model_dump()


async def _create_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    report_id = args["report_id"]
    body = CreateVersionRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    version = await report_store.save_report_version(
        report_id=report_id,
        config=body.config,
        created_by=user.user.user_id,
        comment=body.comment,
        user_id=user.user.user_id,
    )
    if not version:
        return {"error": "Report not found"}
    return version.model_dump()


async def _delete(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    report_id = args["report_id"]
    ok = await report_store.delete_report(report_id, user_id=user.user.user_id)
    if not ok:
        return {"error": "Report not found"}
    return {"report_id": report_id}


async def _pin(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    report_id = args["report_id"]
    body = PinReportRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    ok = await report_store.pin_report(
        report_id,
        body.pinned,
        updated_by=user.user.user_id,
        user_id=user.user.user_id,
    )
    if not ok:
        return {"error": "Report not found"}
    return {"report_id": report_id, "pinned": body.pinned}


async def _set_dashboard(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    meta = await report_store.get_report_metadata(args["report_id"], user_id=user.user.user_id)
    if not meta:
        return {"error": "Report not found"}
    if meta.access.scope != "public":
        return {"error": "Dashboard report must be public"}
    ok = await report_store.set_dashboard_report(args["report_id"])
    if not ok:
        return {"error": "Report not found"}
    return {"report_id": args["report_id"]}


async def _list_versions(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    versions = await report_store.list_report_versions(args["report_id"], user_id=user.user.user_id)
    if not versions:
        return {"error": "Report not found"}
    return {"versions": [v.model_dump() for v in versions]}


async def _get_version(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    version = await report_store.get_report_version(
        args["report_id"],
        int(args["version"]),
        user_id=user.user.user_id,
    )
    if not version:
        return {"error": "Version not found"}
    return version.model_dump()


async def _clone(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    source = await report_store.get_report_latest(args["report_id"], user_id=user.user.user_id)
    if not source:
        return {"error": "Report not found"}
    body = CloneReportRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    try:
        space_id, subspace_id = await resolve_clone_space(
            source.space_id, source.subspace_id, body.space_id, body.subspace_id
        )
    except SpaceValidationError as exc:
        return {"error": str(exc)}
    try:
        new_item = await report_store.create_report(
            name=body.name,
            created_by=user.user.user_id,
            access=SPACE_MEMBER_ACCESS if space_id is not None else None,
            space_id=space_id,
            subspace_id=subspace_id,
        )
    except SpaceConflictError as exc:
        return {"error": str(exc)}
    cloned_config = {**source.config, "name": body.name}
    await report_store.save_report_version(
        report_id=new_item.report_id,
        config=cloned_config,
        created_by=user.user.user_id,
        comment=f"Cloned from {source.name}",
        user_id=user.user.user_id,
    )
    return new_item.model_dump()


async def _update_visibility(args: dict[str, Any], current_user: CurrentUser | None) -> dict[str, Any]:
    user = _require_user(current_user)
    report_id = args["report_id"]
    body = UpdateReportVisibilityRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    meta = await report_store.get_report_metadata(report_id, user_id=user.user.user_id)
    if not meta:
        return {"error": "Report not found"}
    if body.access is not None and meta.created_by != user.user.user_id:
        return {"error": "Only the report owner can update report access"}
    try:
        reject_privatising_space_member(meta.space_id, body.access)
    except SpaceConflictError as exc:
        return {"error": str(exc)}
    if body.access is not None and body.access.scope == "private":
        dashboard_report_id = await report_store.get_dashboard_report_id()
        if meta.pinned or dashboard_report_id == report_id:
            return {"error": "Report must be unpinned and removed from the dashboard before it can be made private"}
    try:
        updated = await report_store.update_report_visibility(
            report_id=report_id,
            updated_by=user.user.user_id,
            access=body.access,
        )
    # The store re-checks the rule above atomically, so a report filed into a
    # space concurrently lands here instead.
    except SpaceConflictError as exc:
        return {"error": str(exc)}
    if not updated:
        return {"error": "Report not found"}
    return updated.model_dump()


async def _confirm_report_version(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    report_id = str(args["report_id"])
    return ActionConfirmationTarget(action="update", resource_type="report", resource_id=report_id)


async def _confirm_report_create(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    """Gate a create only when the new report would be public.

    Creating a report is normally exempt from confirmation because the result is
    a private draft nobody else can see. Filing into a space publishes it
    (``SPACE_MEMBER_ACCESS``), which is a visible change and needs approval.
    """
    if args.get("space_id") is None:
        return None
    return ActionConfirmationTarget(
        action="create public",
        resource_type="report",
        resource_id=str(args.get("name", "")),
    )


async def _confirm_report_clone(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    """Gate every clone, unconditionally.

    A clone with no requested space inherits the source's, so whether it
    publishes depends on the source's placement — and the resolver's read of that
    placement is not the handler's. If the source were filed into a space between
    the two reads, a conditional gate would wave through a clone that publishes a
    private report's contents. Confirming every clone closes that window; there
    is no cheap way to make the two reads one, and clones are rare enough that
    the extra approval costs little.
    """
    return ActionConfirmationTarget(
        action="clone",
        resource_type="report",
        resource_id=str(args["report_id"]),
    )


async def _confirm_report_visibility(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    report_id = str(args["report_id"])
    body = UpdateReportVisibilityRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    if body.access is None:
        return None
    action = "publish" if body.access.scope == "public" else "unpublish"
    return ActionConfirmationTarget(action=action, resource_type="report", resource_id=report_id)


async def _confirm_report_delete(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(action="delete", resource_type="report", resource_id=str(args["report_id"]))


async def _confirm_report_pin(
    args: dict[str, Any], current_user: CurrentUser | None
) -> ActionConfirmationTarget | None:
    body = PinReportRequest.model_validate({k: v for k, v in args.items() if k != "report_id"})
    return ActionConfirmationTarget(
        action="pin" if body.pinned else "unpin",
        resource_type="report",
        resource_id=str(args["report_id"]),
    )


async def _confirm_report_dashboard(
    args: dict[str, Any],
    current_user: CurrentUser | None,
) -> ActionConfirmationTarget | None:
    return ActionConfirmationTarget(action="set_dashboard", resource_type="report", resource_id=str(args["report_id"]))


GROUP_DEF = BuiltinGroup(
    name=GROUP,
    tools=[
        BuiltinTool(
            name="reports__list",
            group=GROUP,
            description="List all reports (metadata only).",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.REPORTS_READ.value],
            handler=_list,
        ),
        BuiltinTool(
            name="reports__get",
            group=GROUP,
            description="Return the latest version of a report.",
            input_schema={
                "type": "object",
                "properties": _report_id_prop(),
                "required": ["report_id"],
            },
            required_permissions=[Permission.REPORTS_READ.value],
            handler=_get,
        ),
        BuiltinTool(
            name="reports__get_dashboard",
            group=GROUP,
            description="Return the latest version of the default dashboard report.",
            input_schema={"type": "object", "properties": {}},
            required_permissions=[Permission.REPORTS_READ.value],
            handler=_get_dashboard,
        ),
        BuiltinTool(
            name="reports__create",
            group=GROUP,
            description="Create a new report.",
            input_schema=model_input_schema(CreateReportRequest),
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_create,
            requires_user=True,
            # Confirmation exception: creates a new private report and does not
            # mutate existing resources. Conditional gate on top, because
            # creating into a space publishes -- see _confirm_report_create.
            chat_safe_without_confirmation=True,
            confirmation=_confirm_report_create,
        ),
        BuiltinTool(
            name="reports__create_version",
            group=GROUP,
            description="Save a new version of an existing report.",
            input_schema=model_input_schema(
                CreateVersionRequest,
                extra_properties=_report_id_prop(),
                extra_required=["report_id"],
            ),
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_create_version,
            requires_user=True,
            confirmation=_confirm_report_version,
        ),
        BuiltinTool(
            name="reports__update_visibility",
            group=GROUP,
            description="Update report visibility without creating a version.",
            input_schema=model_input_schema(
                UpdateReportVisibilityRequest,
                extra_properties=_report_id_prop(),
                extra_required=["report_id"],
            ),
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_update_visibility,
            requires_user=True,
            confirmation=_confirm_report_visibility,
        ),
        BuiltinTool(
            name="reports__delete",
            group=GROUP,
            description="Delete a report and all its versions.",
            input_schema={
                "type": "object",
                "properties": _report_id_prop(),
                "required": ["report_id"],
            },
            required_permissions=[Permission.REPORTS_DELETE.value],
            handler=_delete,
            confirmation=_confirm_report_delete,
        ),
        BuiltinTool(
            name="reports__pin",
            group=GROUP,
            description="Pin or unpin a report on the dashboard nav.",
            input_schema=model_input_schema(
                PinReportRequest,
                extra_properties=_report_id_prop(),
                extra_required=["report_id"],
            ),
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_pin,
            confirmation=_confirm_report_pin,
        ),
        BuiltinTool(
            name="reports__set_dashboard",
            group=GROUP,
            description="Set the given report as the default dashboard.",
            input_schema={
                "type": "object",
                "properties": _report_id_prop(),
                "required": ["report_id"],
            },
            required_permissions=[Permission.REPORTS_SET_DASHBOARD.value],
            handler=_set_dashboard,
            confirmation=_confirm_report_dashboard,
        ),
        BuiltinTool(
            name="reports__list_versions",
            group=GROUP,
            description="List all versions of a report.",
            input_schema={
                "type": "object",
                "properties": _report_id_prop(),
                "required": ["report_id"],
            },
            required_permissions=[Permission.REPORTS_READ.value],
            handler=_list_versions,
        ),
        BuiltinTool(
            name="reports__get_version",
            group=GROUP,
            description="Return a specific version of a report.",
            input_schema={
                "type": "object",
                "properties": {
                    **_report_id_prop(),
                    "version": {
                        "type": "integer",
                        "description": "The version number.",
                    },
                },
                "required": ["report_id", "version"],
            },
            required_permissions=[Permission.REPORTS_READ.value],
            handler=_get_version,
        ),
        BuiltinTool(
            name="reports__clone",
            group=GROUP,
            description="Clone a report into a new report with the given name.",
            input_schema=model_input_schema(
                CloneReportRequest,
                extra_properties=_report_id_prop(),
                extra_required=["report_id"],
            ),
            required_permissions=[Permission.REPORTS_WRITE.value],
            handler=_clone,
            requires_user=True,
            # Always confirmation-gated: a clone that lands in a space publishes
            # the source's contents, and the resolver cannot rule that out
            # race-free -- see _confirm_report_clone. The flag stays so the tool
            # is still listed for callers that exclude gated tools; the gate
            # itself refuses when there is nobody to approve.
            chat_safe_without_confirmation=True,
            confirmation=_confirm_report_clone,
        ),
    ],
)
