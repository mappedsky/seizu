"""Tests for the ``scheduled_queries__*`` MCP built-in group."""

import json
from unittest.mock import AsyncMock, patch

from mcp import types as mcp_types

from reporting.authnz import CurrentUser
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.report_config import ScheduledQueryItem, ScheduledQueryVersion, User
from reporting.services.mcp_server import _build_mcp_server, _mcp_current_user, _mcp_permissions, _mcp_session_key
from reporting.services.query_validator import ValidationResult

_NOW = "2024-01-01T00:00:00+00:00"


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="u1",
            sub="u1",
            iss="dev",
            email="u1@example.com",
            display_name="u1",
            created_at=_NOW,
            last_login=_NOW,
        ),
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )


async def _call(server, name, arguments):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    req = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    perm_tok = _mcp_permissions.set(ALL_PERMISSIONS)
    user_tok = _mcp_current_user.set(_current_user())
    session_tok = _mcp_session_key.set("test-session")
    try:
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
    return result.root.content


def _sq_item() -> ScheduledQueryItem:
    return ScheduledQueryItem(
        scheduled_query_id="sq1",
        name="my-sq",
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=60,
        watch_scans=[],
        enabled=True,
        actions=[],
        current_version=1,
        created_at=_NOW,
        updated_at=_NOW,
        created_by="u1",
    )


def _sq_version() -> ScheduledQueryVersion:
    return ScheduledQueryVersion(
        scheduled_query_id="sq1",
        name="my-sq",
        version=1,
        cypher="MATCH (n) RETURN n",
        params=[],
        frequency=60,
        watch_scans=[],
        enabled=True,
        actions=[],
        created_at=_NOW,
        created_by="u1",
    )


def _valid_create_args():
    return {
        "name": "my-sq",
        "cypher": "MATCH (n) RETURN n",
        "params": [],
        "frequency": 60,
        "watch_scans": [],
        "enabled": True,
        "actions": [],
    }


async def test_scheduled_queries_list_returns_items():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.list_scheduled_queries",
        new_callable=AsyncMock,
        return_value=[_sq_item()],
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__list", {})
        data = json.loads(result[0].text)

    assert len(data["scheduled_queries"]) == 1
    assert data["scheduled_queries"][0]["scheduled_query_id"] == "sq1"


async def test_scheduled_queries_get_returns_item():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
        new_callable=AsyncMock,
        return_value=_sq_item(),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__get", {"scheduled_query_id": "sq1"})
        data = json.loads(result[0].text)

    assert data["scheduled_query_id"] == "sq1"


async def test_scheduled_queries_get_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__get", {"scheduled_query_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Scheduled query not found"}


async def test_scheduled_queries_create_success():
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.create_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ) as mock_create,
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__create", _valid_create_args())
        data = json.loads(result[0].text)

    assert data["scheduled_query_id"] == "sq1"
    mock_create.assert_awaited_once()
    assert mock_create.await_args.kwargs["created_by"] == "u1"


async def test_scheduled_queries_create_rejects_bad_action_config():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
        return_value="bad action",
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__create", _valid_create_args())
        data = json.loads(result[0].text)

    assert data == {"error": "bad action"}


async def test_scheduled_queries_create_rejects_invalid_cypher():
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=["syntax"], warnings=["warn"]),
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__create", _valid_create_args())
        data = json.loads(result[0].text)

    assert data["errors"] == ["syntax"]
    assert data["warnings"] == ["warn"]


async def test_scheduled_queries_update_success():
    args = {"scheduled_query_id": "sq1", **_valid_create_args(), "comment": "why"}
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.update_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ) as mock_update,
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__update", args)
        data = json.loads(result[0].text)

    assert data["scheduled_query_id"] == "sq1"
    mock_update.assert_awaited_once()
    assert mock_update.await_args.kwargs["sq_id"] == "sq1"
    assert mock_update.await_args.kwargs["updated_by"] == "u1"
    assert mock_update.await_args.kwargs["comment"] == "why"


async def test_scheduled_queries_update_rejects_bad_action_config():
    args = {"scheduled_query_id": "sq1", **_valid_create_args()}
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
            return_value="bad action",
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__update", args)
        data = json.loads(result[0].text)

    assert data == {"error": "bad action"}


async def test_scheduled_queries_update_rejects_invalid_cypher():
    args = {"scheduled_query_id": "sq1", **_valid_create_args()}
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_action_configs",
            return_value=None,
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.validate_query",
            new_callable=AsyncMock,
            return_value=ValidationResult(errors=["nope"]),
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__update", args)
        data = json.loads(result[0].text)

    assert data["errors"] == ["nope"]


async def test_scheduled_queries_update_returns_error_when_missing():
    args = {"scheduled_query_id": "nope", **_valid_create_args()}
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__update", args)
        data = json.loads(result[0].text)

    assert data == {"error": "Scheduled query not found"}


async def test_scheduled_queries_delete_success():
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.workflow_schedules.delete_schedule",
            new_callable=AsyncMock,
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.delete_scheduled_query",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__delete", {"scheduled_query_id": "sq1"})
        data = json.loads(result[0].text)

    assert data == {"scheduled_query_id": "sq1"}


async def test_scheduled_queries_delete_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(server, "scheduled_queries__delete", {"scheduled_query_id": "nope"})
        data = json.loads(result[0].text)

    assert data == {"error": "Scheduled query not found"}


async def test_scheduled_queries_list_versions_returns_versions():
    with (
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
            new_callable=AsyncMock,
            return_value=_sq_item(),
        ),
        patch(
            "reporting.services.mcp_builtins.scheduled_queries.report_store.list_scheduled_query_versions",
            new_callable=AsyncMock,
            return_value=[_sq_version()],
        ),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "scheduled_queries__list_versions",
            {"scheduled_query_id": "sq1"},
        )
        data = json.loads(result[0].text)

    assert len(data["versions"]) == 1
    assert data["versions"][0]["version"] == 1


async def test_scheduled_queries_list_versions_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "scheduled_queries__list_versions",
            {"scheduled_query_id": "nope"},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Scheduled query not found"}


async def test_scheduled_queries_get_version_returns_version():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query_version",
        new_callable=AsyncMock,
        return_value=_sq_version(),
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "scheduled_queries__get_version",
            {"scheduled_query_id": "sq1", "version": 1},
        )
        data = json.loads(result[0].text)

    assert data["version"] == 1


async def test_scheduled_queries_get_version_returns_error_when_missing():
    with patch(
        "reporting.services.mcp_builtins.scheduled_queries.report_store.get_scheduled_query_version",
        new_callable=AsyncMock,
        return_value=None,
    ):
        server = _build_mcp_server()
        result = await _call(
            server,
            "scheduled_queries__get_version",
            {"scheduled_query_id": "sq1", "version": 99},
        )
        data = json.loads(result[0].text)

    assert data == {"error": "Scheduled query version not found"}
