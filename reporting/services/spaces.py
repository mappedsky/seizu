"""Cross-entity validation for report space membership.

Lives outside the route so the REST layer and any future MCP builtin share one
implementation of the rules rather than drifting apart.
"""

from reporting.services import report_store


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
