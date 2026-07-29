import logging

from fastapi import APIRouter, Depends, HTTPException

from reporting.authnz import CurrentUser, require_permission
from reporting.authnz.permissions import Permission
from reporting.schema.report_config import ReportListItem
from reporting.schema.space_config import (
    CreateSpaceRequest,
    CreateSubspaceRequest,
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


async def _get_subspace_or_404(space_id: str, subspace_id: str) -> SubspaceItem:
    """Fetch a sub-space, rejecting one that belongs to a different space.

    Sub-space IDs are globally unique, so every nested handler re-checks the
    parent link the same way the tool handlers do.
    """
    subspace = await report_store.get_subspace(subspace_id)
    if not subspace or subspace.space_id != space_id:
        raise HTTPException(status_code=404, detail="Sub-space not found")
    return subspace


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
    return SpaceListResponse(spaces=await report_store.list_spaces())


@router.post("/api/v1/spaces", response_model=SpaceListItem, status_code=201)
async def create_space(
    body: CreateSpaceRequest,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_WRITE)),
) -> SpaceListItem:
    """Create a space along with its overview report."""
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
    """Return a space by ID."""
    return await _get_space_or_404(space_id)


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
    return updated


@router.delete("/api/v1/spaces/{space_id}", response_model=SpaceIdResponse)
async def delete_space(
    space_id: str,
    current: CurrentUser = Depends(require_permission(Permission.SPACES_DELETE)),
) -> SpaceIdResponse:
    """Delete a space with no member reports, and its overview report and sub-spaces."""
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
        space=space,
        subspaces=subspaces,
        reports=_with_resolved_subspace(reports, subspaces),
    )


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
