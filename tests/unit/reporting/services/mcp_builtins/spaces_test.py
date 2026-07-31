"""Tests for the ``spaces__*`` MCP built-in group."""

import json
from unittest.mock import AsyncMock, patch

from mcp import types as mcp_types

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.report_config import ReportListItem, User
from reporting.schema.space_config import (
    PRIVATISING_SPACE_MEMBER_DETAIL,
    SpaceDeleteResult,
    SpaceListItem,
    SubspaceItem,
)
from reporting.services.mcp_server import _build_mcp_server, _mcp_current_user, _mcp_permissions, _mcp_session_key

_NOW = "2024-01-01T00:00:00+00:00"


def _space(space_id: str = "s1", name: str = "Security", overview: str | None = None) -> SpaceListItem:
    return SpaceListItem(
        space_id=space_id,
        name=name,
        description="d",
        overview_report_id=overview,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
        updated_by=None,
    )


def _subspace(subspace_id: str = "ss1", space_id: str = "s1", name: str = "Vulns") -> SubspaceItem:
    return SubspaceItem(
        subspace_id=subspace_id,
        space_id=space_id,
        name=name,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
        updated_by=None,
    )


def _report(report_id: str = "r1", **kwargs) -> ReportListItem:
    return ReportListItem(
        report_id=report_id,
        name="n",
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
        updated_by="u1",
        access={"scope": "public"},
        **kwargs,
    )


def _current_user(user_id: str = "u1") -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id=user_id,
            sub=user_id,
            iss="dev",
            email=f"{user_id}@example.com",
            display_name=user_id,
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


async def _call(server, name, arguments, permissions=None):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    perm_tok = _mcp_permissions.set(permissions or ALL_PERMISSIONS)
    user_tok = _mcp_current_user.set(_current_user())
    session_tok = _mcp_session_key.set("test-session")
    try:
        # Every mutating tool in this group is confirmation-gated; the flow
        # itself is covered in mcp_runtime_test.py, so bypass it here.
        with patch(
            "reporting.services.mcp_runtime.action_confirmations.ensure_confirmation",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await handler(req)
    finally:
        _mcp_permissions.reset(perm_tok)
        _mcp_current_user.reset(user_tok)
        _mcp_session_key.reset(session_tok)
    return json.loads(result.root.content[0].text)


def _patch(name: str, **kwargs):
    return patch(f"reporting.services.mcp_builtins.spaces.report_store.{name}", new_callable=AsyncMock, **kwargs)


def _patch_service(name: str, **kwargs):
    return patch(f"reporting.services.spaces.report_store.{name}", new_callable=AsyncMock, **kwargs)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_list_blanks_the_overview_pointer():
    # Without a report list there is nothing to resolve the pointer against, so
    # returning it could disclose a report ID the caller was never shown.
    with _patch("list_spaces", return_value=[_space(overview="r1")]):
        data = await _call(_build_mcp_server(), "spaces__list", {})

    assert data["spaces"][0]["space_id"] == "s1"
    assert data["spaces"][0]["overview_report_id"] is None


async def test_get_blanks_the_overview_pointer():
    with _patch("get_space", return_value=_space(overview="r1")):
        data = await _call(_build_mcp_server(), "spaces__get", {"space_id": "s1"})

    assert data["overview_report_id"] is None


async def test_get_missing_space_returns_error():
    with _patch("get_space", return_value=None):
        data = await _call(_build_mcp_server(), "spaces__get", {"space_id": "nope"})

    assert data == {"error": "Space not found"}


async def test_tree_resolves_overview_and_subspace_references():
    """A tree scopes reports to the caller and drops references that dangle."""
    reports = [_report("r1", space_id="s1", subspace_id="ss1"), _report("r2", space_id="s1", subspace_id="gone")]
    with (
        _patch("get_space", return_value=_space(overview="deleted-report")),
        _patch("list_subspaces", return_value=[_subspace()]),
        _patch("list_space_reports", return_value=reports) as mock_list,
    ):
        data = await _call(_build_mcp_server(), "spaces__get_tree", {"space_id": "s1"})

    mock_list.assert_awaited_once_with("s1", user_id="u1")
    # Overview points at a report that is not in the space's visible list.
    assert data["space"]["overview_report_id"] is None
    assert data["reports"][0]["subspace_id"] == "ss1"
    # Sub-space was deleted out from under its member: reads as ungrouped.
    assert data["reports"][1]["subspace_id"] is None


# ---------------------------------------------------------------------------
# Space writes
# ---------------------------------------------------------------------------


async def test_create_space_records_the_current_user():
    with (
        _patch_service("list_spaces", return_value=[]),
        _patch("create_space", return_value=_space()) as mock_create,
    ):
        data = await _call(_build_mcp_server(), "spaces__create", {"name": "Security", "description": "d"})

    assert data["space_id"] == "s1"
    mock_create.assert_awaited_once_with(name="Security", description="d", created_by="u1")


async def test_create_space_rejects_a_duplicate_name():
    with (
        _patch_service("list_spaces", return_value=[_space(name="Security")]),
        _patch("create_space") as mock_create,
    ):
        # Stripped by the request model, so the whitespace still collides.
        data = await _call(_build_mcp_server(), "spaces__create", {"name": "  Security  "})

    assert "already exists" in data["error"]
    mock_create.assert_not_awaited()


async def test_create_space_allows_a_name_differing_only_by_case():
    """Exact matching, matching the REST route — see find_duplicate_space_name."""
    with (
        _patch_service("list_spaces", return_value=[_space(name="Security")]),
        _patch("create_space", return_value=_space(space_id="s2")) as mock_create,
    ):
        data = await _call(_build_mcp_server(), "spaces__create", {"name": "security"})

    assert data["space_id"] == "s2"
    mock_create.assert_awaited_once()


async def test_update_space_ignores_its_own_name():
    with (
        _patch_service("list_spaces", return_value=[_space(space_id="s1", name="Security")]),
        _patch("update_space", return_value=_space()) as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__update",
            {"space_id": "s1", "name": "Security", "description": "new"},
        )

    assert data["space_id"] == "s1"
    mock_update.assert_awaited_once_with(space_id="s1", name="Security", description="new", updated_by="u1")


async def test_delete_space_reports_a_non_empty_space():
    with _patch("delete_space", return_value=SpaceDeleteResult.NOT_EMPTY):
        data = await _call(_build_mcp_server(), "spaces__delete", {"space_id": "s1"})

    assert "Move every report out" in data["error"]


async def test_delete_space_reports_a_missing_space():
    with _patch("delete_space", return_value=SpaceDeleteResult.NOT_FOUND):
        data = await _call(_build_mcp_server(), "spaces__delete", {"space_id": "s1"})

    assert data == {"error": "Space not found"}


async def test_delete_space_returns_the_id():
    with _patch("delete_space", return_value=SpaceDeleteResult.DELETED):
        data = await _call(_build_mcp_server(), "spaces__delete", {"space_id": "s1"})

    assert data == {"space_id": "s1"}


async def test_set_overview_rejects_a_report_outside_the_space():
    with (
        _patch("get_space", return_value=_space()),
        _patch_service("get_report_metadata", return_value=_report("r9", space_id="other")),
        _patch("set_space_overview") as mock_set,
    ):
        data = await _call(_build_mcp_server(), "spaces__set_overview", {"space_id": "s1", "report_id": "r9"})

    assert "must be a report filed in that space" in data["error"]
    mock_set.assert_not_awaited()


async def test_set_overview_keeps_the_pointer_it_just_set():
    """The one response that carries the pointer: the caller supplied it."""
    with (
        _patch("get_space", return_value=_space()),
        _patch_service("get_report_metadata", return_value=_report("r1", space_id="s1")),
        _patch("set_space_overview", return_value=_space(overview="r1")),
    ):
        data = await _call(_build_mcp_server(), "spaces__set_overview", {"space_id": "s1", "report_id": "r1"})

    assert data["overview_report_id"] == "r1"


async def test_set_overview_clears_the_pointer_with_a_null_report():
    with (
        _patch("get_space", return_value=_space()),
        _patch("set_space_overview", return_value=_space()) as mock_set,
    ):
        data = await _call(_build_mcp_server(), "spaces__set_overview", {"space_id": "s1", "report_id": None})

    assert data["overview_report_id"] is None
    mock_set.assert_awaited_once_with(space_id="s1", report_id=None, updated_by="u1")


# ---------------------------------------------------------------------------
# Sub-spaces
# ---------------------------------------------------------------------------


async def test_create_subspace_requires_the_space_to_exist():
    with _patch("get_space", return_value=None), _patch("create_subspace") as mock_create:
        data = await _call(_build_mcp_server(), "spaces__create_subspace", {"space_id": "s1", "name": "Vulns"})

    assert data == {"error": "Space not found"}
    mock_create.assert_not_awaited()


async def test_create_subspace_rejects_a_duplicate_name_within_the_space():
    with (
        _patch("get_space", return_value=_space()),
        _patch_service("list_subspaces", return_value=[_subspace(name="Vulns")]),
        _patch("create_subspace") as mock_create,
    ):
        data = await _call(_build_mcp_server(), "spaces__create_subspace", {"space_id": "s1", "name": "  Vulns  "})

    assert "already exists" in data["error"]
    mock_create.assert_not_awaited()


async def test_update_subspace_rejects_one_from_another_space():
    # Sub-space IDs are globally unique, so the parent link has to be re-checked.
    with (
        _patch("get_subspace", return_value=_subspace(space_id="other")),
        _patch("update_subspace") as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__update_subspace",
            {"space_id": "s1", "subspace_id": "ss1", "name": "Renamed"},
        )

    assert data == {"error": "Sub-space not found"}
    mock_update.assert_not_awaited()


async def test_delete_subspace_rejects_one_from_another_space():
    with (
        _patch("get_subspace", return_value=_subspace(space_id="other")),
        _patch("delete_subspace") as mock_delete,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__delete_subspace",
            {"space_id": "s1", "subspace_id": "ss1"},
        )

    assert data == {"error": "Sub-space not found"}
    mock_delete.assert_not_awaited()


async def test_delete_subspace_returns_the_id():
    with (
        _patch("get_subspace", return_value=_subspace()),
        _patch("delete_subspace", return_value=True),
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__delete_subspace",
            {"space_id": "s1", "subspace_id": "ss1"},
        )

    assert data == {"subspace_id": "ss1"}


# ---------------------------------------------------------------------------
# Report membership
# ---------------------------------------------------------------------------


async def test_set_report_space_files_a_report():
    with (
        _patch_service("get_space", return_value=_space()),
        _patch_service("get_subspace", return_value=_subspace()),
        _patch("get_report_metadata", return_value=_report("r1")),
        _patch("update_report_space", return_value=_report("r1", space_id="s1", subspace_id="ss1")) as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "space_id": "s1", "subspace_id": "ss1"},
        )

    assert data["space_id"] == "s1"
    mock_update.assert_awaited_once_with(
        report_id="r1", space_id="s1", subspace_id="ss1", updated_by="u1", user_id="u1"
    )


async def test_set_report_space_rejects_a_subspace_from_another_space():
    with (
        _patch_service("get_space", return_value=_space()),
        _patch_service("get_subspace", return_value=_subspace(space_id="other")),
        _patch("update_report_space") as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "space_id": "s1", "subspace_id": "ss1"},
        )

    assert data == {"error": "Sub-space does not belong to the given space"}
    mock_update.assert_not_awaited()


async def test_set_report_space_rejects_a_subspace_without_a_space():
    with _patch("update_report_space") as mock_update:
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "subspace_id": "ss1"},
        )

    assert data == {"error": "A sub-space cannot be set without a space"}
    mock_update.assert_not_awaited()


async def test_set_report_space_refuses_to_file_a_draft():
    """A report filed in a space is public — SPACE_MEMBER_ACCESS."""
    draft = _report("r1")
    draft = draft.model_copy(update={"access": draft.access.model_copy(update={"scope": "private"})})
    with (
        _patch_service("get_space", return_value=_space()),
        _patch("get_report_metadata", return_value=draft),
        _patch("update_report_space") as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "space_id": "s1"},
        )

    assert "Publish the report" in data["error"]
    mock_update.assert_not_awaited()


async def test_set_report_space_surfaces_a_store_side_conflict():
    """The store re-checks the rule atomically; that raise must not 500."""
    from reporting.schema.space_config import SpaceConflictError

    with (
        _patch_service("get_space", return_value=_space()),
        _patch("get_report_metadata", return_value=_report("r1")),
        _patch("update_report_space", side_effect=SpaceConflictError(PRIVATISING_SPACE_MEMBER_DETAIL)),
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "space_id": "s1"},
        )

    assert data == {"error": PRIVATISING_SPACE_MEMBER_DETAIL}


async def test_unfiling_a_report_skips_the_access_read():
    """Removing a report from a space is allowed whatever its visibility."""
    with (
        _patch("get_report_metadata") as mock_meta,
        _patch("update_report_space", return_value=_report("r1")) as mock_update,
    ):
        data = await _call(
            _build_mcp_server(),
            "spaces__set_report_space",
            {"report_id": "r1", "space_id": None},
        )

    assert data["report_id"] == "r1"
    mock_meta.assert_not_awaited()
    mock_update.assert_awaited_once_with(report_id="r1", space_id=None, subspace_id=None, updated_by="u1", user_id="u1")


async def test_set_report_space_needs_reports_write_not_spaces_write():
    """Matches the REST route: filing a report is a report edit."""
    from reporting.services.mcp_builtins import find_builtin

    tool = find_builtin("spaces__set_report_space")
    assert tool is not None
    assert tool.required_permissions == [Permission.REPORTS_WRITE.value]


async def test_every_mutating_space_tool_is_confirmation_gated():
    """Fail-closed check: a space is a shared, globally visible container."""
    from reporting.services.mcp_builtins import list_builtin_tools

    read_only = {"spaces__list", "spaces__get", "spaces__get_tree", "spaces__list_subspaces"}
    for tool in list_builtin_tools():
        if tool.group != "spaces" or tool.name in read_only:
            continue
        assert tool.confirmation is not None, f"{tool.name} is not confirmation-gated"
        assert not tool.chat_safe_without_confirmation, f"{tool.name} claims a no-confirmation exception"
