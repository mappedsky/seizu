"""Tests for reporting.services.spaces."""

from unittest.mock import AsyncMock, patch

import pytest

from reporting.schema.report_config import ReportAccess
from reporting.schema.space_config import SpaceListItem, SubspaceItem
from reporting.services.spaces import (
    SpaceConflictError,
    SpaceValidationError,
    reject_filing_private_report,
    reject_privatising_space_member,
    resolve_report_space,
)


def _space(space_id: str = "sp1") -> SpaceListItem:
    return SpaceListItem(
        space_id=space_id,
        name="Cloud",
        description="",
        overview_report_id="r1",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="u1",
        updated_by="u1",
    )


def _subspace(subspace_id: str = "ss1", space_id: str = "sp1") -> SubspaceItem:
    return SubspaceItem(
        subspace_id=subspace_id,
        space_id=space_id,
        name="Network",
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="u1",
        updated_by="u1",
    )


async def test_no_space_and_no_subspace_is_valid():
    assert await resolve_report_space(None, None) == (None, None)


async def test_subspace_without_space_is_rejected():
    with pytest.raises(SpaceValidationError, match="cannot be set without a space"):
        await resolve_report_space(None, "ss1")


async def test_unknown_space_is_rejected():
    with patch("reporting.services.report_store.get_space", AsyncMock(return_value=None)):
        with pytest.raises(SpaceValidationError, match="Space not found"):
            await resolve_report_space("sp1", None)


async def test_space_without_subspace_is_valid():
    with patch("reporting.services.report_store.get_space", AsyncMock(return_value=_space())):
        assert await resolve_report_space("sp1", None) == ("sp1", None)


async def test_unknown_subspace_is_rejected():
    with (
        patch("reporting.services.report_store.get_space", AsyncMock(return_value=_space())),
        patch("reporting.services.report_store.get_subspace", AsyncMock(return_value=None)),
    ):
        with pytest.raises(SpaceValidationError, match="Sub-space not found"):
            await resolve_report_space("sp1", "ss1")


async def test_subspace_from_another_space_is_rejected():
    with (
        patch("reporting.services.report_store.get_space", AsyncMock(return_value=_space("sp1"))),
        patch(
            "reporting.services.report_store.get_subspace",
            AsyncMock(return_value=_subspace("ss1", space_id="sp2")),
        ),
    ):
        with pytest.raises(SpaceValidationError, match="does not belong to the given space"):
            await resolve_report_space("sp1", "ss1")


async def test_matching_space_and_subspace_is_valid():
    with (
        patch("reporting.services.report_store.get_space", AsyncMock(return_value=_space())),
        patch("reporting.services.report_store.get_subspace", AsyncMock(return_value=_subspace())),
    ):
        assert await resolve_report_space("sp1", "ss1") == ("sp1", "ss1")


# ---------------------------------------------------------------------------
# The public-space-member invariant
# ---------------------------------------------------------------------------


def test_filing_a_private_report_is_a_conflict():
    with pytest.raises(SpaceConflictError, match="Publish the report"):
        reject_filing_private_report("sp1", ReportAccess(scope="private"))


def test_filing_a_public_report_is_allowed():
    reject_filing_private_report("sp1", ReportAccess(scope="public"))


def test_unfiling_a_private_report_is_allowed():
    """No space means no invariant, so a draft can always leave one."""
    reject_filing_private_report(None, ReportAccess(scope="private"))


def test_privatising_a_space_member_is_a_conflict():
    with pytest.raises(SpaceConflictError, match="Remove the report from its space"):
        reject_privatising_space_member("sp1", ReportAccess(scope="private"))


def test_privatising_a_report_outside_a_space_is_allowed():
    reject_privatising_space_member(None, ReportAccess(scope="private"))


def test_publishing_a_space_member_is_allowed():
    reject_privatising_space_member("sp1", ReportAccess(scope="public"))


def test_visibility_request_without_access_is_allowed():
    """A visibility PUT that carries no access is not a privatisation."""
    reject_privatising_space_member("sp1", None)
