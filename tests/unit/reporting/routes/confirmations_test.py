from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.confirmations import ActionConfirmation
from reporting.schema.report_config import User

_USER = User(
    user_id="user-1",
    sub="sub",
    iss="iss",
    email="user@example.com",
    created_at="2024-01-01T00:00:00+00:00",
    last_login="2024-01-01T00:00:00+00:00",
)


_CONFIRMATION_ID = "a" * 32
_BATCH_ID = "b" * 32


def _confirmation(expires_at: str = "2099-01-01T00:30:00+00:00") -> ActionConfirmation:
    return ActionConfirmation.model_validate(
        {
            "confirmation_id": _CONFIRMATION_ID,
            "user_id": "user-1",
            "source": "chat",
            "session_key": "123",
            "tool_name": "toolsets__update_tool",
            "action": "update",
            "resource_type": "tool",
            "resource_id": "tool-1",
            "arguments": {"name": "Lookup", "cypher": "MATCH (n) RETURN n"},
            "arguments_hash": "hash-1",
            "status": "pending",
            "batch_id": _BATCH_ID,
            "created_at": "2024-01-01T00:00:00+00:00",
            "expires_at": expires_at,
        }
    )


def _make_app():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user=_USER,
        jwt_claims={},
        permissions=ALL_PERMISSIONS,
    )
    return app


async def test_get_confirmation_returns_public_shape(mocker):
    mocker.patch("reporting.routes.confirmations.report_store.get_action_confirmation", return_value=_confirmation())
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/confirmations/{_CONFIRMATION_ID}")

    assert response.status_code == 200
    body = response.json()["confirmation"]
    assert body["arguments"] == {"name": "Lookup", "cypher": "MATCH (n) RETURN n"}
    assert body["thread_id"] == "123"
    assert "arguments_hash" not in body
    assert "session_key" not in body
    assert "user_id" not in body


async def test_list_confirmations_requires_thread_id(mocker):
    list_confirmations = mocker.patch("reporting.routes.confirmations.report_store.list_action_confirmations")
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/confirmations")

    assert response.status_code == 422
    list_confirmations.assert_not_called()


async def test_list_confirmations_uses_chat_thread_session(mocker):
    list_confirmations = mocker.patch(
        "reporting.routes.confirmations.report_store.list_action_confirmations",
        return_value=[_confirmation()],
    )
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/confirmations?thread_id=123")

    assert response.status_code == 200
    assert len(response.json()["confirmations"]) == 1
    list_confirmations.assert_awaited_once_with(
        user_id="user-1",
        source="chat",
        session_key="123",
        status="pending",
    )


async def test_batch_confirmation_lookup_uses_batch_store_method(mocker):
    list_batch = mocker.patch(
        "reporting.routes.confirmations.report_store.list_batch_action_confirmations",
        return_value=[_confirmation()],
    )
    list_all = mocker.patch("reporting.routes.confirmations.report_store.list_action_confirmations")
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/confirmations/batch/{_BATCH_ID}")

    assert response.status_code == 200
    assert len(response.json()["confirmations"]) == 1
    list_batch.assert_awaited_once_with(user_id="user-1", batch_id=_BATCH_ID)
    list_all.assert_not_called()


async def test_batch_confirmation_filters_expired_pending_only(mocker):
    """Expired items are only filtered when status=pending; executed items are always shown."""
    mocker.patch(
        "reporting.routes.confirmations.report_store.list_batch_action_confirmations",
        return_value=[
            _confirmation(expires_at="2099-01-01T00:30:00+00:00"),  # pending, not expired — kept
            _confirmation(expires_at="2000-01-01T00:00:00+00:00"),  # pending, expired — filtered
            ActionConfirmation.model_validate(
                {
                    **_confirmation().model_dump(),
                    "status": "executed",
                    "expires_at": "2000-01-01T00:00:00+00:00",
                }
            ),  # executed, expired — kept (audit trail)
        ],
    )
    app = _make_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/confirmations/batch/{_BATCH_ID}")

    assert response.status_code == 200
    assert len(response.json()["confirmations"]) == 2
