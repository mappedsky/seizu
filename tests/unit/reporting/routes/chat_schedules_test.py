from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.chat import ScheduledChatItem
from reporting.schema.report_config import User

_NOW = "2024-01-01T00:00:00+00:00"

_FAKE_USER = User(
    user_id="test-user-id",
    sub="sub",
    iss="iss",
    email="user@example.com",
    created_at=_NOW,
    last_login=_NOW,
)


def _schedule(created_by: str = "test-user-id") -> ScheduledChatItem:
    return ScheduledChatItem(
        scheduled_chat_id="sc-1",
        name="Daily digest",
        prompt="Summarize new findings",
        schedule={"type": "daily", "days_of_week": [0], "hour": 9},
        watch_scans=[],
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
        created_by=created_by,
    )


def _current_user(permissions: frozenset[str] = ALL_PERMISSIONS) -> CurrentUser:
    return CurrentUser(user=_FAKE_USER, jwt_claims={}, permissions=permissions)


def _make_app(current: CurrentUser | None = None):
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: current or _current_user()
    return app


async def test_list_scheduled_chats_scoped_to_user(mocker):
    list_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chats",
        mocker.AsyncMock(return_value=[_schedule()]),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules")

    assert response.status_code == 200
    assert response.json()["schedules"][0]["scheduled_chat_id"] == "sc-1"
    list_mock.assert_awaited_once_with(user_id="test-user-id")


async def test_requires_chat_schedule_permission(mocker):
    app = _make_app(_current_user(frozenset({"chat:use"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules")
    assert response.status_code == 403


async def test_create_scheduled_chat(mocker):
    create_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.create_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/schedules",
            json={
                "name": "Daily digest",
                "prompt": "Summarize new findings",
                "schedule": {"type": "daily", "days_of_week": [0, 2], "hour": 9},
            },
        )

    assert response.status_code == 201
    create_mock.assert_awaited_once_with(
        name="Daily digest",
        prompt="Summarize new findings",
        schedule={
            "type": "daily",
            "interval_hours": None,
            "days_of_week": [0, 2],
            "hour": 9,
            "days_of_month": [],
        },
        watch_scans=[],
        enabled=True,
        created_by="test-user-id",
    )


async def test_create_requires_a_trigger(mocker):
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chat/schedules",
            json={"name": "No trigger", "prompt": "Hi"},
        )
    assert response.status_code == 422


async def test_get_hides_other_users_schedules(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule(created_by="someone-else")),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1")
    assert response.status_code == 404


async def test_update_scheduled_chat(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    update_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.update_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/chat/schedules/sc-1",
            json={
                "name": "Daily digest",
                "prompt": "Summarize",
                "watch_scans": [{"grouptype": "CVEMetadata"}],
                "enabled": False,
            },
        )

    assert response.status_code == 200
    update_mock.assert_awaited_once_with(
        sc_id="sc-1",
        name="Daily digest",
        prompt="Summarize",
        schedule=None,
        watch_scans=[{"grouptype": "CVEMetadata"}],
        enabled=False,
    )


async def test_delete_scheduled_chat(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    delete_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.delete_scheduled_chat",
        mocker.AsyncMock(return_value=True),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete("/api/v1/chat/schedules/sc-1")

    assert response.status_code == 204
    delete_mock.assert_awaited_once_with("sc-1")


async def test_routes_absent_when_feature_disabled(mocker):
    mocker.patch("reporting.settings.CHAT_SCHEDULES_ENABLED", False)
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules")
    # The SPA catch-all serves GETs for unregistered API paths in this app;
    # the POST route definitely doesn't exist.
    assert response.status_code in (404, 200)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/schedules", json={})
    assert response.status_code in (404, 405)
