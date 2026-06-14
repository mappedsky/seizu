from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import neo4j.exceptions
from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import QueryHistoryItem, User
from reporting.services.query_validator import ValidationResult
from reporting.services.report_query_tokens import issue_report_query_token

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub123",
    iss="https://idp.example.com",
    email="test@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)

_FAKE_CURRENT_USER = CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=ALL_PERMISSIONS)


def _report_current_user():
    return CurrentUser(
        user=_FAKE_USER,
        jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
        permissions=ALL_PERMISSIONS,
    )


def _issue_report_token(
    mocker,
    *,
    current_user: CurrentUser,
    report_id: str,
    report_version: int,
    path: str,
    query: str,
    allowed_param_names: list[str],
    static_params: dict[str, object],
) -> str:
    mocker.patch("reporting.settings.REPORT_QUERY_SIGNING_SECRET", "test-secret")
    return issue_report_query_token(
        current_user=current_user,
        report_id=report_id,
        report_version=report_version,
        path=path,
        query=query,
        allowed_param_names=allowed_param_names,
        static_params=static_params,
    )


def _mock_validate(mocker, errors=None, warnings=None):
    result = ValidationResult(
        errors=errors if errors is not None else [],
        warnings=warnings if warnings is not None else [],
    )
    mocker.patch(
        "reporting.routes.query.validate_query",
        new=AsyncMock(return_value=result),
    )
    return result


def _make_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_CURRENT_USER
    return app


async def test_query_success(mocker):
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice"), ("count", 42)]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n) RETURN n.name AS name, count(n) AS count"},
        )
    assert ret.status_code == 200
    assert ret.json()["results"] == [{"name": "Alice", "count": 42}]
    assert ret.json()["errors"] == []
    assert ret.json()["warnings"] == []


async def test_query_success_with_warnings(mocker):
    _mock_validate(mocker, warnings=["Unknown label: Foo"])
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice")]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n:Foo) RETURN n.name AS name"},
        )
    assert ret.status_code == 200
    assert ret.json()["results"] == [{"name": "Alice"}]
    assert ret.json()["warnings"] == ["Unknown label: Foo"]
    assert ret.json()["errors"] == []


async def test_query_with_params(mocker):
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice")]
    mock_run_query = mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={
                "query": "MATCH (n) WHERE n.name = $name RETURN n.name AS name",
                "params": {"name": "Alice"},
            },
        )
    assert ret.status_code == 200
    mock_run_query.assert_called_once_with(
        "MATCH (n) WHERE n.name = $name RETURN n.name AS name",
        parameters={"name": "Alice"},
    )


async def test_query_passes_params_to_validator(mocker):
    """validate_query must receive the request params so it can run EXPLAIN with them."""
    mock_validate = mocker.patch(
        "reporting.routes.query.validate_query",
        new=AsyncMock(return_value=ValidationResult()),
    )
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice")]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/query/adhoc",
            json={
                "query": "MATCH (n) WHERE n.name = $name RETURN n.name AS name",
                "params": {"name": "Alice"},
            },
        )
    mock_validate.assert_called_once_with(
        "MATCH (n) WHERE n.name = $name RETURN n.name AS name",
        params={"name": "Alice"},
    )


async def test_query_no_json_body(mocker):

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            content=b"not json",
            headers={"Content-Type": "text/plain"},
        )
    assert ret.status_code == 422


async def test_query_missing_query_field(mocker):

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"params": {"name": "Alice"}},
        )
    assert ret.status_code == 422


async def test_query_validation_errors_return_400_with_errors_and_warnings(mocker):
    _mock_validate(mocker, errors=["Write queries are not allowed"])

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "CREATE (n) RETURN n"},
        )
    assert ret.status_code == 400
    assert ret.json()["errors"] == ["Write queries are not allowed"]
    assert "warnings" in ret.json()


async def test_query_validation_errors_do_not_execute_query(mocker):
    _mock_validate(mocker, errors=["Write queries are not allowed"])
    mock_run = mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/query/adhoc", json={"query": "CREATE (n) RETURN n"})

    mock_run.assert_not_called()


async def test_query_execution_failure(mocker):
    _mock_validate(mocker)
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=Exception("Connection refused")),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n) RETURN n"},
        )
    assert ret.status_code == 500
    assert "Query execution failed" in ret.json()["error"]


async def test_query_client_execution_error_returns_query_errors(mocker):
    _mock_validate(mocker, warnings=["Planner warning"])
    client_error = neo4j.exceptions.Neo4jError._hydrate_neo4j(
        code="Neo.ClientError.Statement.SyntaxError",
        message="Invalid call signature for datetime()",
    )
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=client_error),
    )
    save_history = mocker.patch(
        "reporting.routes.query.report_store.save_query_history",
        new=AsyncMock(),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "RETURN datetime(1781321046)"},
        )

    assert ret.status_code == 400
    assert ret.json() == {
        "errors": ["Invalid call signature for datetime()"],
        "warnings": ["Planner warning"],
    }
    save_history.assert_not_awaited()


async def test_query_neo4j_database_error_remains_server_error(mocker):
    _mock_validate(mocker)
    database_error = neo4j.exceptions.Neo4jError._hydrate_neo4j(
        code="Neo.DatabaseError.General.UnknownError",
        message="Database unavailable",
    )
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=database_error),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n) RETURN n"},
        )

    assert ret.status_code == 500
    assert ret.json()["error"] == "Query execution failed"


async def test_query_serialize_node(mocker):
    _mock_validate(mocker)

    from neo4j.graph import Node

    mock_node = MagicMock(spec=Node)
    mock_node.id = 123
    mock_node.labels = frozenset(["Person"])
    mock_node.items.return_value = [("name", "Alice")]

    mock_record = MagicMock()
    mock_record.items.return_value = [("n", mock_node)]

    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n:Person) RETURN n LIMIT 1"},
        )
    assert ret.status_code == 200
    result = ret.json()["results"][0]["n"]
    assert result["id"] == 123
    assert result["labels"] == ["Person"]
    assert result["properties"]["name"] == "Alice"


async def test_query_serialize_relationship(mocker):
    _mock_validate(mocker)

    from neo4j.graph import Node, Relationship

    mock_start = MagicMock(spec=Node)
    mock_start.id = 1
    mock_end = MagicMock(spec=Node)
    mock_end.id = 2

    mock_rel = MagicMock(spec=Relationship)
    mock_rel.id = 456
    mock_rel.type = "KNOWS"
    mock_rel.start_node = mock_start
    mock_rel.end_node = mock_end
    mock_rel.items.return_value = [("since", 2020)]

    mock_record = MagicMock()
    mock_record.items.return_value = [("r", mock_rel)]

    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH ()-[r:KNOWS]->() RETURN r LIMIT 1"},
        )
    assert ret.status_code == 200
    result = ret.json()["results"][0]["r"]
    assert result["id"] == 456
    assert result["type"] == "KNOWS"
    assert result["start_node_id"] == 1
    assert result["end_node_id"] == 2
    assert result["properties"]["since"] == 2020


async def test_query_serialize_path(mocker):
    _mock_validate(mocker)

    from neo4j.graph import Node, Path, Relationship

    mock_node1 = MagicMock(spec=Node)
    mock_node1.id = 1
    mock_node1.labels = frozenset(["Person"])
    mock_node1.items.return_value = [("name", "Alice")]

    mock_node2 = MagicMock(spec=Node)
    mock_node2.id = 2
    mock_node2.labels = frozenset(["Person"])
    mock_node2.items.return_value = [("name", "Bob")]

    mock_rel = MagicMock(spec=Relationship)
    mock_rel.id = 10
    mock_rel.type = "KNOWS"
    mock_rel.start_node = mock_node1
    mock_rel.end_node = mock_node2
    mock_rel.items.return_value = []

    mock_path = MagicMock(spec=Path)
    mock_path.nodes = [mock_node1, mock_node2]
    mock_path.relationships = [mock_rel]

    mock_record = MagicMock()
    mock_record.items.return_value = [("p", mock_path)]

    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH p=shortestPath((a)-[*]-(b)) RETURN p LIMIT 1"},
        )
    assert ret.status_code == 200
    result = ret.json()["results"][0]["p"]
    assert len(result["nodes"]) == 2
    assert len(result["relationships"]) == 1
    assert result["nodes"][0]["properties"]["name"] == "Alice"


async def test_query_saves_history_for_adhoc_requests(mocker):
    """Ad hoc queries always save history."""
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("n", 1)]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )
    fake_history_item = QueryHistoryItem(
        history_id="hist-1",
        user_id="test-user-id",
        query="MATCH (n) RETURN n LIMIT 1",
        executed_at="2024-01-01T00:00:00+00:00",
    )
    mock_save = mocker.patch(
        "reporting.routes.query.report_store.save_query_history",
        new=AsyncMock(return_value=fake_history_item),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/adhoc",
            json={"query": "MATCH (n) RETURN n LIMIT 1"},
        )

    mock_save.assert_awaited_once_with(
        user_id="test-user-id",
        query="MATCH (n) RETURN n LIMIT 1",
    )
    assert ret.json()["history_id"] == "hist-1"


async def test_report_query_does_not_save_history(mocker):
    """Report queries do not write console history."""
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("n", 1)]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )
    mock_save = mocker.patch(
        "reporting.routes.query.report_store.save_query_history",
        new=AsyncMock(),
    )

    current_user = _report_current_user()
    report_token = _issue_report_token(
        mocker,
        current_user=current_user,
        report_id="rid1",
        report_version=2,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n LIMIT 1",
        allowed_param_names=[],
        static_params={},
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/query/report",
            json={"token": report_token},
        )

    mock_save.assert_not_called()


async def test_report_query_success(mocker):
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice")]
    mock_run_query = mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )

    current_user = _report_current_user()
    report_token = _issue_report_token(
        mocker,
        current_user=current_user,
        report_id="rid1",
        report_version=2,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n.name AS name",
        allowed_param_names=["severity", "limit"],
        static_params={"limit": 10},
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/report",
            json={"token": report_token, "params": {"severity": "HIGH", "limit": 10}},
        )

    assert ret.status_code == 200
    assert ret.json()["results"] == [{"name": "Alice"}]
    mock_run_query.assert_called_once_with(
        "MATCH (n) RETURN n.name AS name",
        parameters={"limit": 10, "severity": "HIGH"},
    )


async def test_report_query_rejects_token_for_other_user(mocker):
    _mock_validate(mocker)
    other_user = CurrentUser(
        user=User(
            user_id="other-user",
            sub="other",
            iss="https://idp.example.com",
            email="other@example.com",
            created_at="2024-01-01T00:00:00+00:00",
            last_login="2024-01-01T00:00:00+00:00",
        ),
        jwt_claims={"token_exp": datetime.now(tz=UTC) + timedelta(minutes=10)},
        permissions=ALL_PERMISSIONS,
    )
    token = _issue_report_token(
        mocker,
        current_user=_report_current_user(),
        report_id="rid1",
        report_version=1,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n",
        allowed_param_names=[],
        static_params={},
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: other_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/query/report", json={"token": token})

    assert ret.status_code == 403
    assert ret.json()["error"] == "Forbidden"


async def test_report_query_rejects_tampered_token(mocker):
    _mock_validate(mocker)
    token = _issue_report_token(
        mocker,
        current_user=_report_current_user(),
        report_id="rid1",
        report_version=1,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n",
        allowed_param_names=[],
        static_params={},
    )
    encoded_payload, encoded_sig = token.split(".", 1)
    tampered_payload = ("A" if encoded_payload[0] != "A" else "B") + encoded_payload[1:]
    tampered = f"{tampered_payload}.{encoded_sig}"

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _report_current_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/query/report", json={"token": tampered})

    assert ret.status_code == 400
    assert ret.json()["error"] == "Invalid report query token"


async def test_report_query_rejects_expired_token(mocker):
    _mock_validate(mocker)
    expired_user = CurrentUser(
        user=_FAKE_USER,
        jwt_claims={"token_exp": datetime.now(tz=UTC) - timedelta(minutes=1)},
        permissions=ALL_PERMISSIONS,
    )
    token = _issue_report_token(
        mocker,
        current_user=expired_user,
        report_id="rid1",
        report_version=1,
        path="rows.0.panels.0.cypher",
        query="MATCH (n) RETURN n",
        allowed_param_names=[],
        static_params={},
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _report_current_user()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post("/api/v1/query/report", json={"token": token})

    assert ret.status_code == 400
    body = ret.json()
    assert body["error"] == "Report query token has expired"
    assert body["code"] == "token_expired"


async def test_history_query_success(mocker):
    """Re-executing by history ID returns results."""
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("name", "Alice")]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )
    fake_history_item = QueryHistoryItem(
        history_id="hist-abc",
        user_id="test-user-id",
        query="MATCH (n) RETURN n.name AS name",
        executed_at="2024-01-01T00:00:00+00:00",
    )
    mocker.patch(
        "reporting.routes.query.report_store.get_query_history_item",
        new=AsyncMock(return_value=fake_history_item),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/history",
            json={"history_id": "hist-abc"},
        )

    assert ret.status_code == 200
    assert ret.json()["results"] == [{"name": "Alice"}]
    assert ret.json()["errors"] == []


async def test_history_query_not_found(mocker):
    """Returns 404 when the history item does not exist."""
    mocker.patch(
        "reporting.routes.query.report_store.get_query_history_item",
        new=AsyncMock(return_value=None),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/history",
            json={"history_id": "nonexistent"},
        )

    assert ret.status_code == 404
    assert ret.json()["error"] == "Not found"


async def test_history_query_scoped_to_current_user(mocker):
    """get_query_history_item is called with the current user's user_id."""
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("n", 1)]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )
    fake_history_item = QueryHistoryItem(
        history_id="hist-xyz",
        user_id="test-user-id",
        query="MATCH (n) RETURN n LIMIT 1",
        executed_at="2024-01-01T00:00:00+00:00",
    )
    mock_get = mocker.patch(
        "reporting.routes.query.report_store.get_query_history_item",
        new=AsyncMock(return_value=fake_history_item),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/query/history",
            json={"history_id": "hist-xyz"},
        )

    mock_get.assert_awaited_once_with(
        user_id="test-user-id",
        history_id="hist-xyz",
    )


async def test_history_query_does_not_save_history(mocker):
    """Re-executing by history ID must not write a new history entry."""
    _mock_validate(mocker)
    mock_record = MagicMock()
    mock_record.items.return_value = [("n", 1)]
    mocker.patch(
        "reporting.routes.query.reporting_neo4j.run_query",
        new=AsyncMock(return_value=[mock_record]),
    )
    fake_history_item = QueryHistoryItem(
        history_id="hist-nosave",
        user_id="test-user-id",
        query="MATCH (n) RETURN n LIMIT 1",
        executed_at="2024-01-01T00:00:00+00:00",
    )
    mocker.patch(
        "reporting.routes.query.report_store.get_query_history_item",
        new=AsyncMock(return_value=fake_history_item),
    )
    mock_save = mocker.patch(
        "reporting.routes.query.report_store.save_query_history",
        new=AsyncMock(),
    )

    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/history",
            json={"history_id": "hist-nosave"},
        )

    assert ret.status_code == 200
    assert ret.json()["history_id"] is None
    mock_save.assert_not_called()


async def test_history_query_requires_permission(mocker):
    """Unprivileged users receive 403."""
    from reporting.authnz.permissions import Permission

    unprivileged = CurrentUser(
        user=_FAKE_USER,
        jwt_claims={},
        permissions=frozenset(p for p in ALL_PERMISSIONS if p != Permission.QUERY_EXECUTE),
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: unprivileged
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ret = await client.post(
            "/api/v1/query/history",
            json={"history_id": "any"},
        )

    assert ret.status_code == 403
