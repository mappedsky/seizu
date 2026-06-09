from unittest.mock import AsyncMock

import neo4j.exceptions
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS, Permission
from reporting.schema.report_config import User

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="test@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_FAKE_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)
_UNPRIVILEGED_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=frozenset())
_NO_EXECUTE_CURRENT_USER = CurrentUser(
    user=_FAKE_USER,
    jwt_claims={},
    permissions=ALL_PERMISSIONS - {Permission.QUERY_EXECUTE},
)


def _make_app(current_user: CurrentUser = _FAKE_CURRENT_USER) -> object:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    return app


def _make_record(data: dict) -> object:
    mock = AsyncMock()
    mock.__getitem__ = lambda self, k: data[k]
    mock.items.return_value = list(data.items())
    return mock


async def test_get_graph_schema_returns_labels_rels_props_and_indexes(mocker):
    label_rec = _make_record({"label": "Person"})
    rel_rec = _make_record({"type": "KNOWS"})
    prop_rec = _make_record({"key": "name"})
    index_rec = _make_record(
        {
            "name": "person_name",
            "type": "RANGE",
            "entityType": "NODE",
            "labelsOrTypes": ["Person"],
            "properties": ["name"],
            "state": "ONLINE",
        }
    )

    mock_run = mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=[[label_rec], [rel_rec], [prop_rec], [index_rec]]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 200
    body = ret.json()
    assert body["labels"] == ["Person"]
    assert body["relationship_types"] == ["KNOWS"]
    assert body["property_keys"] == ["name"]
    assert body["indexes"] == [
        {
            "name": "person_name",
            "type": "RANGE",
            "entity_type": "NODE",
            "labels_or_types": ["Person"],
            "properties": ["name"],
            "state": "ONLINE",
        }
    ]
    assert mock_run.call_count == 4


async def test_get_graph_schema_empty_database(mocker):
    mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=[[], [], [], []]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 200
    body = ret.json()
    assert body["labels"] == []
    assert body["relationship_types"] == []
    assert body["property_keys"] == []
    assert body["indexes"] == []


async def test_get_graph_schema_degrades_when_show_indexes_unavailable(mocker):
    # SHOW INDEXES needs catalog privileges; if it fails the rest of the schema
    # must still return, with an empty index list.
    label_rec = _make_record({"label": "Person"})
    rel_rec = _make_record({"type": "KNOWS"})
    prop_rec = _make_record({"key": "name"})
    mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=[[label_rec], [rel_rec], [prop_rec], neo4j.exceptions.ClientError("no permission")]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 200
    body = ret.json()
    assert body["labels"] == ["Person"]
    assert body["indexes"] == []


async def test_get_graph_schema_requires_query_execute_permission():
    app = _make_app(current_user=_UNPRIVILEGED_CURRENT_USER)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")
    assert ret.status_code == 403


async def test_get_graph_schema_returns_500_on_neo4j_error(mocker):
    mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=neo4j.exceptions.ServiceUnavailable("down")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 500
    assert "error" in ret.json()


async def test_get_graph_schema_returns_500_on_generic_error(mocker):
    mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=RuntimeError("unexpected")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 500
    assert "error" in ret.json()


async def test_get_graph_schema_does_not_save_history(mocker):
    mocker.patch(
        "reporting.routes.graph.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=[[], [], [], []]),
    )
    mock_save = mocker.patch(
        "reporting.services.report_store.save_query_history",
        new=AsyncMock(),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.get("/api/v1/graph/schema")

    assert ret.status_code == 200
    mock_save.assert_not_called()
