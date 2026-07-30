"""Cross-entity validation for spaces.

Lives outside the route layer so REST and MCP share one implementation — both
create and clone reports, and validation written twice drifts.

These are validations, not invariants: they reject a bad request up front but
nothing enforces them afterwards. Membership and the overview pointer are both
allowed to go stale, and are resolved lazily at the API boundary instead, which
is what lets every report in a space stay an ordinary report.
"""

from reporting.services import report_store

__all__ = [
    "SpaceValidationError",
    "resolve_clone_space",
    "resolve_overview_report",
    "resolve_report_space",
]


class SpaceValidationError(Exception):
    """A requested (space, sub-space) pairing is invalid.

    Callers map this to HTTP 400. It is deliberately not a pydantic validator:
    pydantic failures surface as 422, and these are semantic rather than
    structural problems with the request.
    """


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


async def resolve_overview_report(space_id: str, report_id: str | None) -> str | None:
    """Validate a report proposed as a space's overview, and return it.

    The target must be filed in this space; ``None`` clears the pointer. This is
    a validation rather than an invariant: nothing stops the report being moved
    out or deleted later, and the pointer is resolved lazily so it simply reads
    as "no overview" if that happens.
    """
    if report_id is None:
        return None
    report = await report_store.get_report_metadata(report_id)
    if report is None:
        raise SpaceValidationError("Report not found")
    if report.space_id != space_id:
        raise SpaceValidationError("A space overview must be a report filed in that space")
    return report_id


async def resolve_clone_space(
    source_space_id: str | None,
    source_subspace_id: str | None,
    requested_space_id: str | None,
    requested_subspace_id: str | None,
) -> tuple[str | None, str | None]:
    """Pick the space a clone lands in.

    With no requested space the clone inherits the source's placement — cloning
    inside a space is the common case.
    """
    if requested_space_id is None:
        return source_space_id, source_subspace_id
    return await resolve_report_space(requested_space_id, requested_subspace_id)
