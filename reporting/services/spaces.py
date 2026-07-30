"""Cross-entity validation for spaces.

Lives outside the route layer so REST and MCP share one implementation — both
create and clone reports, and validation written twice drifts.

Two kinds of check live here, and they are enforced differently:

- The (space, sub-space) pairing and the overview target are *validations only*.
  Nothing keeps them true afterwards: membership and the pointer are both allowed
  to go stale and are resolved lazily at the API boundary, which is what lets
  every report in a space stay an ordinary report.
- The public-space-member rule (``SPACE_MEMBER_ACCESS`` and the two ``reject_``
  helpers) *is* an invariant. The checks here exist for a clear error message;
  the store backends re-check it atomically on every write that could break it,
  because a check up here alone loses a concurrent unpublish/file race.
"""

from reporting.schema.report_config import ReportAccess
from reporting.schema.space_config import (
    FILING_PRIVATE_REPORT_DETAIL,
    PRIVATISING_SPACE_MEMBER_DETAIL,
    SpaceConflictError,
)
from reporting.services import report_store

__all__ = [
    "SPACE_MEMBER_ACCESS",
    "SpaceConflictError",
    "SpaceValidationError",
    "reject_filing_private_report",
    "reject_privatising_space_member",
    "resolve_clone_space",
    "resolve_overview_report",
    "resolve_report_space",
]

#: Reports filed in a space are public. A space is a shared container, so a
#: private member would be invisible to everyone but its owner while still
#: blocking the space's deletion -- and its ID would leak through the space's
#: overview pointer. Creating a report into a space therefore publishes it.
SPACE_MEMBER_ACCESS = ReportAccess(scope="public")


class SpaceValidationError(Exception):
    """A requested (space, sub-space) pairing is invalid.

    Callers map this to HTTP 400. It is deliberately not a pydantic validator:
    pydantic failures surface as 422, and these are semantic rather than
    structural problems with the request.
    """


def reject_filing_private_report(space_id: str | None, access: ReportAccess) -> None:
    """Refuse to file a private report into a space.

    Checked on the way in, for an actionable error; the store backends enforce
    the same rule atomically at the write, which is what makes it hold under
    concurrent requests. Keeping a space free of private members is what lets
    the space's overview pointer be resolved against member reports alone.
    """
    if space_id is not None and access.scope != "public":
        raise SpaceConflictError(FILING_PRIVATE_REPORT_DETAIL)


def reject_privatising_space_member(space_id: str | None, access: ReportAccess | None) -> None:
    """Refuse to make a report private while it is filed in a space.

    The other half of the same invariant: without it, filing a public report and
    then unpublishing it would reintroduce a private space member.
    """
    if space_id is not None and access is not None and access.scope != "public":
        raise SpaceConflictError(PRIVATISING_SPACE_MEMBER_DETAIL)


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


async def resolve_overview_report(
    space_id: str,
    report_id: str | None,
    *,
    user_id: str | None = None,
) -> str | None:
    """Validate a report proposed as a space's overview, and return it.

    The target must be filed in this space; ``None`` clears the pointer. This is
    a validation rather than an invariant: nothing stops the report being moved
    out or deleted later, and the pointer is resolved lazily so it simply reads
    as "no overview" if that happens.

    ``user_id`` scopes the lookup to reports the caller can see, so nominating a
    report cannot confirm the existence of one they were never shown. Space
    members are public by ``SPACE_MEMBER_ACCESS``, so this only ever rejects a
    report the caller had no business naming.
    """
    if report_id is None:
        return None
    report = await report_store.get_report_metadata(report_id, user_id=user_id)
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
