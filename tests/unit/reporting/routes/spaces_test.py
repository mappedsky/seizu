from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ReportListItem, User
from reporting.schema.space_config import SpaceDeleteResult, SpaceListItem, SubspaceItem

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="user@example.com",
    display_name="Test User",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_FAKE_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
    permissions=ALL_PERMISSIONS,
)

_UNPRIVILEGED_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
    permissions=frozenset(),
)


def _make_app(current_user: CurrentUser = _FAKE_CURRENT_USER):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def _space(space_id="sp1", name="Cloud", overview_report_id=None):
    return SpaceListItem(
        space_id=space_id,
        name=name,
        description="desc",
        overview_report_id=overview_report_id,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
    )


def _subspace(subspace_id="ss1", space_id="sp1", name="Network"):
    return SubspaceItem(
        subspace_id=subspace_id,
        space_id=space_id,
        name=name,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
    )


def _report(report_id="r1", space_id="sp1", subspace_id=None):
    return ReportListItem(
        report_id=report_id,
        name=f"Report {report_id}",
        current_version=1,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        created_by="test-user-id",
        updated_by="test-user-id",
        access={"scope": "public"},
        space_id=space_id,
        subspace_id=subspace_id,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/spaces
# ---------------------------------------------------------------------------


async def test_list_spaces(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.list_spaces",
        new=AsyncMock(return_value=[_space()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces")
    assert ret.status_code == 200
    assert [s["space_id"] for s in ret.json()["spaces"]] == ["sp1"]


async def test_list_spaces_empty(mocker):
    mocker.patch("reporting.routes.spaces.report_store.list_spaces", new=AsyncMock(return_value=[]))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces")
    assert ret.status_code == 200
    assert ret.json()["spaces"] == []


# ---------------------------------------------------------------------------
# POST /api/v1/spaces
# ---------------------------------------------------------------------------


async def test_create_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.list_spaces", new=AsyncMock(return_value=[]))
    mock_create = mocker.patch(
        "reporting.routes.spaces.report_store.create_space",
        new=AsyncMock(return_value=_space()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/spaces", json={"name": "Cloud", "description": "desc"})
    assert ret.status_code == 201
    assert ret.json()["space_id"] == "sp1"
    # A new space has no overview until one is set.
    assert ret.json()["overview_report_id"] is None
    mock_create.assert_called_once_with(name="Cloud", description="desc", created_by="test-user-id")


async def test_create_space_rejects_duplicate_name(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.list_spaces",
        new=AsyncMock(return_value=[_space(name="Cloud")]),
    )
    mock_create = mocker.patch(
        "reporting.routes.spaces.report_store.create_space",
        new=AsyncMock(return_value=_space()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Trimmed and case-insensitive.
        ret = await client.post("/api/v1/spaces", json={"name": "  cloud  "})
    assert ret.status_code == 409
    mock_create.assert_not_called()


async def test_create_space_rejects_blank_name(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/spaces", json={"name": "   "})
    # Malformed body, so 422 rather than the semantic 400s.
    assert ret.status_code == 422


# ---------------------------------------------------------------------------
# GET / PUT /api/v1/spaces/<space_id>
# ---------------------------------------------------------------------------


async def test_get_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1")
    assert ret.status_code == 200
    assert ret.json()["name"] == "Cloud"


async def test_get_space_not_found(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/missing")
    assert ret.status_code == 404


async def test_update_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.list_spaces", new=AsyncMock(return_value=[]))
    mocker.patch(
        "reporting.routes.spaces.report_store.update_space",
        new=AsyncMock(return_value=_space(name="Cloud Security")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1", json={"name": "Cloud Security", "description": "d"})
    assert ret.status_code == 200
    assert ret.json()["name"] == "Cloud Security"


async def test_update_space_allows_keeping_its_own_name(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.list_spaces",
        new=AsyncMock(return_value=[_space(space_id="sp1", name="Cloud")]),
    )
    mocker.patch(
        "reporting.routes.spaces.report_store.update_space",
        new=AsyncMock(return_value=_space(name="Cloud")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1", json={"name": "Cloud", "description": "new"})
    assert ret.status_code == 200


async def test_update_space_rejects_another_spaces_name(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.list_spaces",
        new=AsyncMock(return_value=[_space(space_id="sp2", name="Taken")]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1", json={"name": "Taken"})
    assert ret.status_code == 409


async def test_update_space_not_found(mocker):
    mocker.patch("reporting.routes.spaces.report_store.list_spaces", new=AsyncMock(return_value=[]))
    mocker.patch("reporting.routes.spaces.report_store.update_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/missing", json={"name": "Cloud"})
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/v1/spaces/<space_id>
# ---------------------------------------------------------------------------


async def test_delete_space(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.delete_space",
        new=AsyncMock(return_value=SpaceDeleteResult.DELETED),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/sp1")
    assert ret.status_code == 200
    assert ret.json()["space_id"] == "sp1"


async def test_delete_space_not_found(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.delete_space",
        new=AsyncMock(return_value=SpaceDeleteResult.NOT_FOUND),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/missing")
    assert ret.status_code == 404


async def test_delete_non_empty_space_is_rejected(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.delete_space",
        new=AsyncMock(return_value=SpaceDeleteResult.NOT_EMPTY),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/sp1")
    assert ret.status_code == 409
    # The detail has to reach the client — it is what the delete dialog shows.
    assert "Move every report out of the space" in ret.json()["error"]
    # Sub-spaces are not mentioned: they no longer block the delete.
    assert "sub-space" not in ret.json()["error"]


# ---------------------------------------------------------------------------
# GET /api/v1/spaces/<space_id>/tree
# ---------------------------------------------------------------------------


async def test_get_space_tree(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_subspaces",
        new=AsyncMock(return_value=[_subspace()]),
    )
    mock_reports = mocker.patch(
        "reporting.routes.spaces.report_store.list_space_reports",
        new=AsyncMock(
            return_value=[
                _report("r1"),
                _report("r2"),
                _report("r3", subspace_id="ss1"),
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1/tree")
    assert ret.status_code == 200
    body = ret.json()
    assert body["space"]["space_id"] == "sp1"
    assert [s["subspace_id"] for s in body["subspaces"]] == ["ss1"]
    assert [r["report_id"] for r in body["reports"]] == ["r1", "r2", "r3"]
    # Reports are scoped to the caller's visibility, not the whole space.
    mock_reports.assert_called_once_with("sp1", user_id="test-user-id")


async def test_get_space_tree_normalises_dangling_subspace_ids(mocker):
    """A report left behind by a deleted sub-space reads as ungrouped."""
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_subspaces",
        new=AsyncMock(return_value=[_subspace("ss1")]),
    )
    mocker.patch(
        "reporting.routes.spaces.report_store.list_space_reports",
        new=AsyncMock(
            return_value=[
                _report("r1", subspace_id="ss1"),
                _report("r2", subspace_id="deleted-ss"),
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1/tree")
    reports = {r["report_id"]: r["subspace_id"] for r in ret.json()["reports"]}
    assert reports == {"r1": "ss1", "r2": None}


async def test_get_space_tree_not_found(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/missing/tree")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# Sub-spaces
# ---------------------------------------------------------------------------


async def test_list_subspaces(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_subspaces",
        new=AsyncMock(return_value=[_subspace()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1/subspaces")
    assert ret.status_code == 200
    assert [s["subspace_id"] for s in ret.json()["subspaces"]] == ["ss1"]


async def test_list_subspaces_unknown_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/missing/subspaces")
    assert ret.status_code == 404


async def test_create_subspace(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch("reporting.routes.spaces.report_store.list_subspaces", new=AsyncMock(return_value=[]))
    mock_create = mocker.patch(
        "reporting.routes.spaces.report_store.create_subspace",
        new=AsyncMock(return_value=_subspace()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/spaces/sp1/subspaces", json={"name": "Network"})
    assert ret.status_code == 201
    assert ret.json()["subspace_id"] == "ss1"
    mock_create.assert_called_once_with(space_id="sp1", name="Network", created_by="test-user-id")


async def test_create_subspace_unknown_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/spaces/missing/subspaces", json={"name": "Network"})
    assert ret.status_code == 404


async def test_create_subspace_rejects_duplicate_name_within_the_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_subspaces",
        new=AsyncMock(return_value=[_subspace(name="Network")]),
    )
    mock_create = mocker.patch(
        "reporting.routes.spaces.report_store.create_subspace",
        new=AsyncMock(return_value=_subspace()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/spaces/sp1/subspaces", json={"name": "network"})
    assert ret.status_code == 409
    mock_create.assert_not_called()


async def test_update_subspace(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.get_subspace",
        new=AsyncMock(return_value=_subspace()),
    )
    mocker.patch("reporting.routes.spaces.report_store.list_subspaces", new=AsyncMock(return_value=[]))
    mocker.patch(
        "reporting.routes.spaces.report_store.update_subspace",
        new=AsyncMock(return_value=_subspace(name="Networking")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/subspaces/ss1", json={"name": "Networking"})
    assert ret.status_code == 200
    assert ret.json()["name"] == "Networking"


async def test_update_subspace_rejects_wrong_parent(mocker):
    """Sub-space IDs are globally unique, so the parent link is re-checked."""
    mocker.patch(
        "reporting.routes.spaces.report_store.get_subspace",
        new=AsyncMock(return_value=_subspace("ss1", space_id="sp2")),
    )
    mock_update = mocker.patch(
        "reporting.routes.spaces.report_store.update_subspace",
        new=AsyncMock(return_value=_subspace()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/subspaces/ss1", json={"name": "Networking"})
    assert ret.status_code == 404
    mock_update.assert_not_called()


async def test_update_subspace_not_found(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_subspace", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/subspaces/missing", json={"name": "x"})
    assert ret.status_code == 404


async def test_delete_subspace(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.get_subspace",
        new=AsyncMock(return_value=_subspace()),
    )
    mocker.patch("reporting.routes.spaces.report_store.delete_subspace", new=AsyncMock(return_value=True))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/sp1/subspaces/ss1")
    assert ret.status_code == 200
    assert ret.json()["subspace_id"] == "ss1"


async def test_delete_subspace_rejects_wrong_parent(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.get_subspace",
        new=AsyncMock(return_value=_subspace("ss1", space_id="sp2")),
    )
    mock_delete = mocker.patch(
        "reporting.routes.spaces.report_store.delete_subspace",
        new=AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/sp1/subspaces/ss1")
    assert ret.status_code == 404
    mock_delete.assert_not_called()


async def test_delete_subspace_not_found(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_subspace", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.delete("/api/v1/spaces/sp1/subspaces/missing")
    assert ret.status_code == 404


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


async def test_every_space_endpoint_requires_a_permission():
    app = _make_app(_UNPRIVILEGED_CURRENT_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [
            await client.get("/api/v1/spaces"),
            await client.post("/api/v1/spaces", json={"name": "Cloud"}),
            await client.get("/api/v1/spaces/sp1"),
            await client.put("/api/v1/spaces/sp1", json={"name": "Cloud"}),
            await client.delete("/api/v1/spaces/sp1"),
            await client.get("/api/v1/spaces/sp1/tree"),
            await client.get("/api/v1/spaces/sp1/subspaces"),
            await client.post("/api/v1/spaces/sp1/subspaces", json={"name": "Net"}),
            await client.put("/api/v1/spaces/sp1/subspaces/ss1", json={"name": "Net"}),
            await client.delete("/api/v1/spaces/sp1/subspaces/ss1"),
        ]
    assert [r.status_code for r in responses] == [403] * len(responses)


# ---------------------------------------------------------------------------
# PUT /api/v1/spaces/<space_id>/overview
# ---------------------------------------------------------------------------


async def test_set_space_overview(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.services.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report("r1", space_id="sp1")),
    )
    mock_set = mocker.patch(
        "reporting.routes.spaces.report_store.set_space_overview",
        new=AsyncMock(return_value=_space(overview_report_id="r1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": "r1"})
    assert ret.status_code == 200
    assert ret.json()["overview_report_id"] == "r1"
    mock_set.assert_called_once_with(space_id="sp1", report_id="r1", updated_by="test-user-id")


async def test_clear_space_overview(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mock_set = mocker.patch(
        "reporting.routes.spaces.report_store.set_space_overview",
        new=AsyncMock(return_value=_space()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": None})
    assert ret.status_code == 200
    assert mock_set.call_args.kwargs["report_id"] is None


async def test_set_space_overview_rejects_a_report_from_another_space(mocker):
    """The overview has to be a report filed in this space."""
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.services.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report("r9", space_id="other")),
    )
    mock_set = mocker.patch(
        "reporting.routes.spaces.report_store.set_space_overview",
        new=AsyncMock(return_value=_space()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": "r9"})
    assert ret.status_code == 400
    assert "filed in that space" in ret.json()["error"]
    mock_set.assert_not_called()


async def test_set_space_overview_rejects_an_unknown_report(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch("reporting.services.report_store.get_report_metadata", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": "missing"})
    assert ret.status_code == 400
    assert "Report not found" in ret.json()["error"]


async def test_set_space_overview_unknown_space(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=None))
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/missing/overview", json={"report_id": None})
    assert ret.status_code == 404


async def test_tree_blanks_an_overview_pointer_that_no_longer_resolves(mocker):
    """The target may have been deleted, moved out, or be invisible here.

    Resolving lazily is what lets the overview be an ordinary report with no
    protections on it.
    """
    mocker.patch(
        "reporting.routes.spaces.report_store.get_space",
        new=AsyncMock(return_value=_space(overview_report_id="gone")),
    )
    mocker.patch("reporting.routes.spaces.report_store.list_subspaces", new=AsyncMock(return_value=[]))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_space_reports",
        new=AsyncMock(return_value=[_report("r1")]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1/tree")
    assert ret.status_code == 200
    assert ret.json()["space"]["overview_report_id"] is None


async def test_tree_keeps_an_overview_pointer_that_resolves(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.get_space",
        new=AsyncMock(return_value=_space(overview_report_id="r1")),
    )
    mocker.patch("reporting.routes.spaces.report_store.list_subspaces", new=AsyncMock(return_value=[]))
    mocker.patch(
        "reporting.routes.spaces.report_store.list_space_reports",
        new=AsyncMock(return_value=[_report("r1")]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1/tree")
    assert ret.json()["space"]["overview_report_id"] == "r1"


async def test_set_space_overview_requires_spaces_write():
    app = _make_app(_UNPRIVILEGED_CURRENT_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": None})
    assert ret.status_code == 403


# ---------------------------------------------------------------------------
# The overview pointer is only exposed by the tree
# ---------------------------------------------------------------------------


async def test_list_spaces_omits_the_overview_pointer(mocker):
    """Only the tree can resolve the pointer, so the list must not carry it.

    An unresolved pointer would disclose the ID and existence of a report the
    caller was never shown.
    """
    mocker.patch(
        "reporting.routes.spaces.report_store.list_spaces",
        new=AsyncMock(return_value=[_space(overview_report_id="r1")]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces")
    assert ret.status_code == 200
    assert ret.json()["spaces"][0]["overview_report_id"] is None


async def test_get_space_omits_the_overview_pointer(mocker):
    mocker.patch(
        "reporting.routes.spaces.report_store.get_space",
        new=AsyncMock(return_value=_space(overview_report_id="r1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/spaces/sp1")
    assert ret.status_code == 200
    assert ret.json()["overview_report_id"] is None


async def test_update_space_omits_the_overview_pointer(mocker):
    mocker.patch("reporting.routes.spaces.report_store.list_spaces", new=AsyncMock(return_value=[]))
    mocker.patch(
        "reporting.routes.spaces.report_store.update_space",
        new=AsyncMock(return_value=_space(name="Renamed", overview_report_id="r1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1", json={"name": "Renamed"})
    assert ret.status_code == 200
    assert ret.json()["overview_report_id"] is None


async def test_set_space_overview_looks_the_report_up_as_the_caller(mocker):
    """Nominating a report must not confirm the existence of an unseen one."""
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mock_meta = mocker.patch(
        "reporting.services.report_store.get_report_metadata",
        new=AsyncMock(return_value=_report("r1", space_id="sp1")),
    )
    mocker.patch(
        "reporting.routes.spaces.report_store.set_space_overview",
        new=AsyncMock(return_value=_space(overview_report_id="r1")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": "r1"})
    assert ret.status_code == 200
    assert mock_meta.call_args.kwargs["user_id"] == "test-user-id"


async def test_set_space_overview_rejects_a_report_the_caller_cannot_see(mocker):
    mocker.patch("reporting.routes.spaces.report_store.get_space", new=AsyncMock(return_value=_space()))
    mocker.patch(
        "reporting.services.report_store.get_report_metadata",
        new=AsyncMock(return_value=None),
    )
    mock_set = mocker.patch(
        "reporting.routes.spaces.report_store.set_space_overview",
        new=AsyncMock(return_value=_space()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.put("/api/v1/spaces/sp1/overview", json={"report_id": "hidden"})
    assert ret.status_code == 400
    assert "Report not found" in ret.json()["error"]
    mock_set.assert_not_called()
