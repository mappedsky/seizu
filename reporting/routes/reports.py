import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import (
    CloneReportRequest,
    CreateReportRequest,
    CreateVersionRequest,
    PinReportRequest,
    ReportIdResponse,
    ReportListItem,
    ReportListResponse,
    ReportVersion,
    ReportVersionListResponse,
    UpdateReportVisibilityRequest,
)
from reporting.schema.space_config import SetReportSpaceRequest
from reporting.services import report_store
from reporting.services.report_query_tokens import build_report_query_capabilities
from reporting.services.spaces import SpaceValidationError, resolve_report_space

logger = logging.getLogger(__name__)
router = APIRouter()

# The overview report is an artefact of its space: it is created with the
# space, deleted with it, and is the space detail page's main view. Letting it
# move, disappear, or go private would leave the space pointing at something
# that is gone or invisible.
_OVERVIEW_IMMOBILE = "The space overview report cannot be moved to another space"
_OVERVIEW_UNDELETABLE = "The space overview report cannot be deleted; delete the space instead"
_OVERVIEW_MUST_BE_PUBLIC = "The space overview report cannot be made private"


async def _resolve_space_or_400(
    space_id: str | None,
    subspace_id: str | None,
) -> tuple[str | None, str | None]:
    try:
        return await resolve_report_space(space_id, subspace_id)
    except SpaceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _with_query_capabilities(
    report: ReportVersion,
    current: CurrentUser,
    include_query_capabilities: bool,
) -> ReportVersion:
    if not include_query_capabilities:
        return report.model_copy(update={"query_capabilities": None})
    return report.model_copy(
        update={
            "query_capabilities": build_report_query_capabilities(report, current),
        }
    )


@router.get("/api/v1/reports", response_model=ReportListResponse)
async def list_reports(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=1, le=500),
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_READ)),
) -> ReportListResponse:
    """List all reports."""
    reports = await report_store.list_reports(user_id=current.user.user_id)
    start = (page - 1) * per_page
    end = start + per_page
    return ReportListResponse(
        reports=reports[start:end],
        total=len(reports),
        page=page,
        per_page=per_page,
    )


@router.get("/api/v1/reports/dashboard", response_model=ReportVersion, response_model_exclude_none=True)
async def get_dashboard_report(
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_READ)),
    include_query_capabilities: bool = False,
) -> ReportVersion:
    """Return the latest version of the dashboard report."""
    report = await report_store.get_dashboard_report()
    if not report:
        raise HTTPException(status_code=404, detail="No dashboard report configured")
    return _with_query_capabilities(report, current, include_query_capabilities)


@router.put("/api/v1/reports/{report_id}/pin", response_model=ReportIdResponse)
async def pin_report(
    report_id: str,
    body: PinReportRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
) -> ReportIdResponse:
    """Pin or unpin a report."""
    ok = await report_store.pin_report(
        report_id,
        body.pinned,
        updated_by=current.user.user_id,
        user_id=current.user.user_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportIdResponse(report_id=report_id)


@router.put("/api/v1/reports/{report_id}/dashboard", response_model=ReportIdResponse)
async def set_dashboard_report(
    report_id: str,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_SET_DASHBOARD)),
) -> ReportIdResponse:
    """Set a report as the default dashboard."""
    meta = await report_store.get_report_metadata(report_id, user_id=current.user.user_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Report not found")
    if meta.access.scope != "public":
        raise HTTPException(status_code=400, detail="Dashboard report must be public")
    ok = await report_store.set_dashboard_report(report_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportIdResponse(report_id=report_id)


@router.get("/api/v1/reports/{report_id}", response_model=ReportVersion, response_model_exclude_none=True)
async def get_report(
    report_id: str,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_READ)),
    include_query_capabilities: bool = False,
) -> ReportVersion:
    """Return the latest version of a report."""
    report = await report_store.get_report_latest(report_id, user_id=current.user.user_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _with_query_capabilities(report, current, include_query_capabilities)


@router.get("/api/v1/reports/{report_id}/versions", response_model=ReportVersionListResponse)
async def list_versions(
    report_id: str,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_READ)),
) -> ReportVersionListResponse:
    """List all versions of a report."""
    versions = await report_store.list_report_versions(report_id, user_id=current.user.user_id)
    if not versions:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportVersionListResponse(versions=versions)


@router.get(
    "/api/v1/reports/{report_id}/versions/{version_num}",
    response_model=ReportVersion,
    response_model_exclude_none=True,
)
async def get_version(
    report_id: str,
    version_num: int,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_READ)),
    include_query_capabilities: bool = False,
) -> ReportVersion:
    """Return a specific version of a report."""
    version = await report_store.get_report_version(report_id, version_num, user_id=current.user.user_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return _with_query_capabilities(version, current, include_query_capabilities)


@router.delete("/api/v1/reports/{report_id}", response_model=ReportIdResponse)
async def delete_report(
    report_id: str,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_DELETE)),
) -> ReportIdResponse:
    """Delete a report and all its versions."""
    meta = await report_store.get_report_metadata(report_id, user_id=current.user.user_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Report not found")
    if meta.space_overview:
        raise HTTPException(status_code=409, detail=_OVERVIEW_UNDELETABLE)
    ok = await report_store.delete_report(report_id, user_id=current.user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportIdResponse(report_id=report_id)


@router.post("/api/v1/reports", response_model=ReportListItem, status_code=201)
async def create_report(
    body: CreateReportRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
) -> Any:
    """Create a new report."""
    space_id, subspace_id = await _resolve_space_or_400(body.space_id, body.subspace_id)
    return await report_store.create_report(
        name=body.name,
        created_by=current.user.user_id,
        space_id=space_id,
        subspace_id=subspace_id,
    )


@router.put("/api/v1/reports/{report_id}/visibility", response_model=ReportListItem)
async def update_report_visibility(
    report_id: str,
    body: UpdateReportVisibilityRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
) -> ReportListItem:
    """Update report visibility without creating a report version."""
    meta = await report_store.get_report_metadata(report_id, user_id=current.user.user_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Report not found")
    if body.access is not None and meta.created_by != current.user.user_id:
        raise HTTPException(status_code=403, detail="Only the report owner can update report access")
    if body.access is not None and body.access.scope == "private":
        if meta.space_overview:
            raise HTTPException(status_code=409, detail=_OVERVIEW_MUST_BE_PUBLIC)
        dashboard_report_id = await report_store.get_dashboard_report_id()
        if meta.pinned or dashboard_report_id == report_id:
            raise HTTPException(
                status_code=400,
                detail="Report must be unpinned and removed from the dashboard before it can be made private",
            )
    updated = await report_store.update_report_visibility(
        report_id=report_id,
        updated_by=current.user.user_id,
        access=body.access,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Report not found")
    return updated


@router.put("/api/v1/reports/{report_id}/space", response_model=ReportListItem)
async def update_report_space(
    report_id: str,
    body: SetReportSpaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
) -> ReportListItem:
    """Set a report's space membership without creating a report version.

    Requires only ``reports:write``: filing a report is a report edit, and
    spaces are globally visible so there is nothing to leak by letting any
    report author file into any space.
    """
    meta = await report_store.get_report_metadata(report_id, user_id=current.user.user_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Report not found")
    if meta.space_overview:
        raise HTTPException(status_code=409, detail=_OVERVIEW_IMMOBILE)
    space_id, subspace_id = await _resolve_space_or_400(body.space_id, body.subspace_id)
    updated = await report_store.update_report_space(
        report_id=report_id,
        space_id=space_id,
        subspace_id=subspace_id,
        updated_by=current.user.user_id,
        user_id=current.user.user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Report not found")
    return updated


@router.post(
    "/api/v1/reports/{report_id}/versions",
    response_model=ReportVersion,
    response_model_exclude_none=True,
    status_code=201,
)
async def create_version(
    report_id: str,
    body: CreateVersionRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
    include_query_capabilities: bool = False,
) -> Any:
    """Save a new version of a report."""
    version = await report_store.save_report_version(
        report_id=report_id,
        config=body.config,
        created_by=current.user.user_id,
        comment=body.comment,
        user_id=current.user.user_id,
    )
    if not version:
        raise HTTPException(status_code=404, detail="Report not found")
    return _with_query_capabilities(version, current, include_query_capabilities)


@router.post("/api/v1/reports/{report_id}/clone", response_model=ReportListItem, status_code=201)
async def clone_report(
    report_id: str,
    body: CloneReportRequest,
    current: CurrentUser = Depends(require_permission(Permission.REPORTS_WRITE)),
) -> Any:
    """Clone a report into a new report with the given name."""
    source = await report_store.get_report_latest(report_id, user_id=current.user.user_id)
    if not source:
        raise HTTPException(status_code=404, detail="Report not found")
    # Inherit the source's space unless the caller names another one; a clone
    # is never an overview report, whatever the source was.
    if body.space_id is None:
        space_id, subspace_id = source.space_id, source.subspace_id
    else:
        space_id, subspace_id = await _resolve_space_or_400(body.space_id, body.subspace_id)
    new_item = await report_store.create_report(
        name=body.name,
        created_by=current.user.user_id,
        space_id=space_id,
        subspace_id=subspace_id,
    )
    cloned_config = {**source.config, "name": body.name}
    await report_store.save_report_version(
        report_id=new_item.report_id,
        config=cloned_config,
        created_by=current.user.user_id,
        comment=f"Cloned from {source.name}",
        user_id=current.user.user_id,
    )
    return new_item
