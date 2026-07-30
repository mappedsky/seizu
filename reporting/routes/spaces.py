import logging

from fastapi import APIRouter, Depends, HTTPException

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import ReportListItem
from reporting.schema.space_config import (
    CreateSpaceRequest,
    CreateSubspaceRequest,
    SetSpaceOverviewRequest,
    SpaceDeleteResult,
    SpaceIdResponse,
    SpaceListItem,
    SpaceListResponse,
    SpaceTreeResponse,
    SubspaceIdResponse,
    SubspaceItem,
    SubspaceListResponse,
    UpdateSpaceRequest,
    UpdateSubspaceRequest,
)
from reporting.services import report_store
from reporting.services.spaces import SpaceValidationError, resolve_overview_report

logger = logging.getLogger(__name__)
router = APIRouter()


def _normalised_name(name: str) -> str:
    return name.strip().casefold()


async def _reject_duplicate_space_name(name: str, *, exclude_space_id: str | None = None) -> None:
    """Best-effort duplicate-name guard, matching the toolset create check.

    Not backed by a unique constraint: a functional lower(name) index is
    awkward cross-dialect and DynamoDB cannot enforce one at all, so adding it
    to only the SQL backend would make the two behave differently under a race.
    """
    target = _normalised_name(name)
    for space in await report_store.list_spaces():
        if space.space_id != exclude_space_id and _normalised_name(space.name) == target:
            raise HTTPException(status_code=409, detail="A space with that name already exists")


async def _reject_duplicate_subspace_name(
    space_id: str,
    name: str,
    *,
    exclude_subspace_id: str | None = None,
) -> None:
    target = _normalised_name(name)
    for subspace in await report_store.list_subspaces(space_id):
        if subspace.subspace_id != exclude_subspace_id and _normalised_name(subspace.name) == target:
            raise HTTPException(
                status_code=409,
                detail="A sub-space with that name already exists in this space",
            )


async def _get_space_or_404(space_id: str) -> SpaceListItem:
    space = await report_store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return space


def _without_overview(space: SpaceListItem) -> SpaceListItem:
    """Drop the overview pointer from a response that carries no report list.

    Only the tree endpoint can say whether the pointer still resolves for this
    caller, and a pointer at a report they cannot see would otherwise disclose
    that report's ID and existence. Space members are public
    (``SPACE_MEMBER_ACCESS``), so the pointer is normally harmless -- but it can
    still go stale, and nothing outside the tree consumes it.
    """
    if space.overview_report_id is None:
        return space
    return space.model_copy(update={"overview_report_id": None})


async def _get_subspace_or_404(space_id: str, subspace_id: str) -> SubspaceItem:
    """Fetch a sub-space, rejecting one that belongs to a different space.

    Sub-space IDs are globally unique, so every nested handler re-checks the
    parent link the same way the tool handlers do.
    """
    subspace = await report_store.get_subspace(subspace_id)
    if not subspace or subspace.space_id != space_id:
        raise HTTPException(status_code=404, detail="Sub-space not found")
    return subspace


def _with_resolved_overview(space: SpaceListItem, reports: list[ReportListItem]) -> SpaceListItem:
    """Blank out an overview pointer that no longer resolves.

    The target may have been deleted, moved out of the space, or be invisible to
    this caller. Resolving lazily is what lets the overview be an ordinary
    report with no protections on it.
    """
    if space.overview_report_id is None:
        return space
    if any(report.report_id == space.overview_report_id for report in reports):
        return space
    return space.model_copy(update={"overview_report_id": None})


def _with_resolved_subspace(
    reports: list[ReportListItem],
    subspaces: list[SubspaceItem],
) -> list[ReportListItem]:
    """Blank out any ``subspace_id`` that no longer resolves.

    Deleting a sub-space leaves its member reports pointing at it; rather than
    fanning out a write over every member, the reference is resolved lazily
    here so clients only ever see "grouped" or "ungrouped".
    """
    known = {subspace.subspace_id for subspace in subspaces}
    return [
        report if report.subspace_id in known else report.model_copy(update={"subspace_id": None}) for report in reports
    ]


# ---------------------------------------------------------------------------
# Spaces
# ---------------------------------------------------------------------------


@router.get("/api/v1/spaces", response_model=SpaceListResponse)
async def list_spaces(
    current: CurrentUser = Depends(require_permission(Permission.SPACES_READ)),
) -> SpaceListResponse:
    """List all spaces."""
    spaces = await report_store.list_spaces()
    return SpaceListResponse(spaces=[_without_overview(space) for space in spaces])


@router.post("/api/v1/spaces", response_model=SpaceListItem, status_code=201)
async def create_space(
    body: CreateSpaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SpaceListItem:
    """Create an empty space.

    No report is created with it: the overview is a pointer the user sets later
    at one of the space's own reports.
    """
    await _reject_duplicate_space_name(body.name)
    return await report_store.create_space(
        name=body.name,
        description=body.description,
        created_by=current.user.user_id,
    )


@router.get("/api/v1/spaces/{space_id}", response_model=SpaceListItem)
async def get_space(
    space_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_READ)),
) -> SpaceListItem:
    """Return a space by ID, without its overview pointer.

    The pointer needs the caller's visible report list to resolve, which only
    the tree endpoint has.
    """
    return _without_overview(await _get_space_or_404(space_id))


@router.put("/api/v1/spaces/{space_id}", response_model=SpaceListItem)
async def update_space(
    space_id: str,
    body: UpdateSpaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SpaceListItem:
    """Rename a space."""
    await _reject_duplicate_space_name(body.name, exclude_space_id=space_id)
    updated = await report_store.update_space(
        space_id=space_id,
        name=body.name,
        description=body.description,
        updated_by=current.user.user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Space not found")
    return _without_overview(updated)


@router.delete("/api/v1/spaces/{space_id}", response_model=SpaceIdResponse)
async def delete_space(
    space_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_DELETE)),
) -> SpaceIdResponse:
    """Delete a space that holds no reports, along with its sub-spaces."""
    result = await report_store.delete_space(space_id)
    if result is SpaceDeleteResult.NOT_FOUND:
        raise HTTPException(status_code=404, detail="Space not found")
    if result is SpaceDeleteResult.NOT_EMPTY:
        raise HTTPException(
            status_code=409,
            detail="Move every report out of the space before deleting it",
        )
    return SpaceIdResponse(space_id=space_id)


@router.get("/api/v1/spaces/{space_id}/tree", response_model=SpaceTreeResponse)
async def get_space_tree(
    space_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_READ)),
) -> SpaceTreeResponse:
    """Return a space with its sub-spaces and visible reports.

    One endpoint rather than three so the detail page has a single loading
    state instead of flashing an empty sidebar.
    """
    space = await _get_space_or_404(space_id)
    subspaces = await report_store.list_subspaces(space_id)
    reports = await report_store.list_space_reports(space_id, user_id=current.user.user_id)
    return SpaceTreeResponse(
        space=_with_resolved_overview(space, reports),
        subspaces=subspaces,
        reports=_with_resolved_subspace(reports, subspaces),
    )


@router.put("/api/v1/spaces/{space_id}/overview", response_model=SpaceListItem)
async def set_space_overview(
    space_id: str,
    body: SetSpaceOverviewRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SpaceListItem:
    """Point the space at one of its reports as the landing page, or clear it.

    Unlike the other non-tree space responses this one carries
    ``overview_report_id``: it echoes what the caller just set, on a report they
    supplied and were authorised to see.
    """
    await _get_space_or_404(space_id)
    try:
        report_id = await resolve_overview_report(space_id, body.report_id, user_id=current.user.user_id)
    except SpaceValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = await report_store.set_space_overview(
        space_id=space_id,
        report_id=report_id,
        updated_by=current.user.user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Space not found")
    return updated


# ---------------------------------------------------------------------------
# Sub-spaces (nested under spaces)
# ---------------------------------------------------------------------------


@router.get("/api/v1/spaces/{space_id}/subspaces", response_model=SubspaceListResponse)
async def list_subspaces(
    space_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_READ)),
) -> SubspaceListResponse:
    """List the sub-spaces of a space."""
    await _get_space_or_404(space_id)
    return SubspaceListResponse(subspaces=await report_store.list_subspaces(space_id))


@router.post(
    "/api/v1/spaces/{space_id}/subspaces",
    response_model=SubspaceItem,
    status_code=201,
)
async def create_subspace(
    space_id: str,
    body: CreateSubspaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SubspaceItem:
    """Create a sub-space within a space."""
    await _get_space_or_404(space_id)
    await _reject_duplicate_subspace_name(space_id, body.name)
    created = await report_store.create_subspace(
        space_id=space_id,
        name=body.name,
        created_by=current.user.user_id,
    )
    if not created:
        raise HTTPException(status_code=404, detail="Space not found")
    return created


@router.put(
    "/api/v1/spaces/{space_id}/subspaces/{subspace_id}",
    response_model=SubspaceItem,
)
async def update_subspace(
    space_id: str,
    subspace_id: str,
    body: UpdateSubspaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SubspaceItem:
    """Rename a sub-space."""
    await _get_subspace_or_404(space_id, subspace_id)
    await _reject_duplicate_subspace_name(space_id, body.name, exclude_subspace_id=subspace_id)
    updated = await report_store.update_subspace(
        subspace_id=subspace_id,
        name=body.name,
        updated_by=current.user.user_id,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Sub-space not found")
    return updated


@router.delete(
    "/api/v1/spaces/{space_id}/subspaces/{subspace_id}",
    response_model=SubspaceIdResponse,
)
async def delete_subspace(
    space_id: str,
    subspace_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_DELETE)),
) -> SubspaceIdResponse:
    """Delete a sub-space; its reports fall back to ungrouped."""
    await _get_subspace_or_404(space_id, subspace_id)
    ok = await report_store.delete_subspace(subspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sub-space not found")
    return SubspaceIdResponse(subspace_id=subspace_id)
