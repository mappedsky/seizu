"""Cross-entity rules for report space membership.

Lives outside the route layer so every transport shares one implementation.
REST and MCP both mutate reports, and rules enforced in only one of them are
rules an authorized caller can walk around.
"""

from reporting.schema.report_config import ReportAccess, ReportListItem
from reporting.services import report_store
from reporting.services.report_store.base import (
    OVERVIEW_IMMOBILE,
    OVERVIEW_MUST_BE_PUBLIC,
    OVERVIEW_UNDELETABLE,
    ProtectedReportError,
)

__all__ = [
    "ProtectedReportError",
    "SpaceValidationError",
    "ensure_report_deletable",
    "ensure_report_movable",
    "ensure_visibility_change_allowed",
    "resolve_report_space",
    "resolve_clone_space",
]


class SpaceValidationError(Exception):
    """A requested (space, sub-space) pairing is invalid.

    Callers map this to HTTP 400. It is deliberately not a pydantic validator:
    pydantic failures surface as 422, and these are semantic rather than
    structural problems with the request.
    """


def ensure_report_movable(report: ReportListItem) -> None:
    """Reject moving a space's overview report. Callers map this to HTTP 409."""
    if report.space_overview:
        raise ProtectedReportError(OVERVIEW_IMMOBILE)


def ensure_report_deletable(report: ReportListItem) -> None:
    """Reject deleting a space's overview report. Callers map this to HTTP 409."""
    if report.space_overview:
        raise ProtectedReportError(OVERVIEW_UNDELETABLE)


def ensure_visibility_change_allowed(report: ReportListItem, access: ReportAccess | None) -> None:
    """Reject making a space's overview report private.

    Spaces are globally visible, so a private overview would leave every
    non-owner looking at an empty space.
    """
    if access is not None and access.scope == "private" and report.space_overview:
        raise ProtectedReportError(OVERVIEW_MUST_BE_PUBLIC)


async def resolve_report_space(
    space_id: str | None,
    subspace_id: str | None,
) -> tuple[str | None, str | None]:
    """Validate a requested (space, sub-space) pair and return it normalised.

    Rules:
    - A sub-space cannot be set without a space.
    - Both must exist.
    - The sub-space must belong to the given space.
    """
    if space_id is None:
        if subspace_id is not None:
            raise SpaceValidationError("A sub-space cannot be set without a space")
        return None, None

    if await report_store.get_space(space_id) is None:
        raise SpaceValidationError("Space not found")

    if subspace_id is None:
        return space_id, None

    subspace = await report_store.get_subspace(subspace_id)
    if subspace is None:
        raise SpaceValidationError("Sub-space not found")
    if subspace.space_id != space_id:
        raise SpaceValidationError("Sub-space does not belong to the given space")
    return space_id, subspace_id


async def resolve_clone_space(
    source_space_id: str | None,
    source_subspace_id: str | None,
    requested_space_id: str | None,
    requested_subspace_id: str | None,
) -> tuple[str | None, str | None]:
    """Pick the space a clone lands in.

    With no requested space the clone inherits the source's placement — cloning
    inside a space is the common case. The overview flag is never inherited;
    only ``create_space`` sets it.
    """
    if requested_space_id is None:
        return source_space_id, source_subspace_id
    return await resolve_report_space(requested_space_id, requested_subspace_id)
