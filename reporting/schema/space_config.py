"""Pydantic models for spaces and sub-spaces.

Spaces group reports; sub-spaces group reports within a space. Unlike
toolsets/skillsets these records carry no version history and no access scope:
they are flat and globally visible, and the reports listed inside a space are
still filtered by the report's own ``access`` (see ``_report_visible_to_user``
in the store backends).
"""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from reporting.schema.report_config import ReportListItem


def _strip_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("Name must not be blank")
    return stripped


class SpaceListItem(BaseModel):
    """A space record.

    ``overview_report_id`` optionally points at one of the space's member
    reports, which the detail page renders as the space's landing page. It is
    just a pointer: the report stays an ordinary report, and the pointer is
    resolved lazily, so a target that has been deleted, moved out, or is
    invisible to the caller simply reads as "no overview set".

    Two endpoints populate it: the tree, which can resolve it against the
    caller's visible reports, and ``PUT /spaces/<id>/overview``, which echoes
    back the pointer the caller just set (and was authorised to see). Every
    other space response blanks it, because without a report list there is
    nothing to resolve it against and an unresolved pointer would disclose a
    report ID the caller was never shown.
    """

    space_id: str
    name: str
    description: str = ""
    overview_report_id: str | None = None
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class SubspaceItem(BaseModel):
    """A sub-space: a grouping label for reports within a single space.

    Sub-spaces have no detail page and no content of their own. Deleting one
    leaves its member reports with a ``subspace_id`` that no longer resolves,
    which renders as "ungrouped" -- see ``reporting/routes/spaces.py``.
    """

    subspace_id: str
    space_id: str
    name: str
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class SpaceConflictError(Exception):
    """A write would leave a private report inside a space.

    Callers map this to HTTP 409 (or an ``error`` payload for MCP): the request
    is well formed and names real records, but the report's visibility and its
    space membership cannot both be what it asks for.

    Defined here rather than in ``reporting.services.spaces`` because the store
    backends raise it too -- they enforce the rule atomically, and importing the
    service layer from a store would be a cycle.
    """


#: Messages for the two directions of the public-space-member rule. Shared so
#: the up-front validation and the store's atomic enforcement cannot drift.
FILING_PRIVATE_REPORT_DETAIL = "Publish the report before filing it into a space"
PRIVATISING_SPACE_MEMBER_DETAIL = "Remove the report from its space before making it private"


class SpaceDeleteResult(StrEnum):
    """Outcome of a space or sub-space delete.

    Deliberately not the ``bool`` the other delete methods return: the store
    distinguishes "no such space" (404) from "space still has content" (409),
    and that emptiness rule has to live in the store so both backends share it
    and so it is never evaluated through a per-user visibility filter.
    """

    DELETED = "deleted"
    NOT_FOUND = "not_found"
    NOT_EMPTY = "not_empty"


class CreateSpaceRequest(BaseModel):
    """Request body for POST /api/v1/spaces."""

    name: str = Field(min_length=1)
    description: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _strip_name(v)


class UpdateSpaceRequest(BaseModel):
    """Request body for PUT /api/v1/spaces/<id>."""

    name: str = Field(min_length=1)
    description: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _strip_name(v)


class CreateSubspaceRequest(BaseModel):
    """Request body for POST /api/v1/spaces/<id>/subspaces."""

    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _strip_name(v)


class UpdateSubspaceRequest(BaseModel):
    """Request body for PUT /api/v1/spaces/<id>/subspaces/<id>."""

    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _strip_name(v)


class SetReportSpaceRequest(BaseModel):
    """Request body for PUT /api/v1/reports/<id>/space.

    Replace semantics: both fields describe the desired final state, so an
    omitted ``subspace_id`` and an explicit ``null`` mean the same thing --
    clear it. That is what makes "moving to another space clears the
    sub-space" fall out without special-casing, and it sidesteps pydantic's
    inability to distinguish omitted from null without inspecting
    ``model_fields_set``.
    """

    space_id: str | None = None
    subspace_id: str | None = None


class SpaceListResponse(BaseModel):
    spaces: list[SpaceListItem]


class SubspaceListResponse(BaseModel):
    subspaces: list[SubspaceItem]


class SetSpaceOverviewRequest(BaseModel):
    """Request body for PUT /api/v1/spaces/<id>/overview.

    ``report_id`` must name a report filed in this space; null clears it.
    """

    report_id: str | None = None


class SpaceIdResponse(BaseModel):
    space_id: str


class SubspaceIdResponse(BaseModel):
    subspace_id: str


class SpaceTreeResponse(BaseModel):
    """Everything the space detail page needs in one round trip.

    ``reports`` is filtered by the caller's report visibility. Dangling
    references are normalised away before the response is built, so clients
    never see one: a ``subspace_id`` that does not resolve against
    ``subspaces``, and a ``space.overview_report_id`` that is not among
    ``reports``, both come back as ``None``.
    """

    space: SpaceListItem
    subspaces: list[SubspaceItem]
    reports: list[ReportListItem]
