from httpx import ASGITransport, AsyncClient

from reporting.app import create_app
from reporting.authnz import CurrentUser, get_current_user
from reporting.authnz.permissions import ALL_PERMISSIONS
from reporting.schema.chat import ChatSessionItem, ScheduledChatItem, ScheduledChatVersion
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
            "interval_minutes": None,
            "interval_hours": None,
            "days_of_week": [0, 2],
            "hour": 9,
            "minute": 0,
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
    # Without chat:schedule:read_all, other users' schedules are invisible.
    app = _make_app(_current_user(frozenset({"chat:schedule"})))
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
        updated_by="test-user-id",
        comment=None,
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


async def test_list_versions(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chat_versions",
        mocker.AsyncMock(
            return_value=[
                ScheduledChatVersion(
                    scheduled_chat_id="sc-1",
                    version=1,
                    name="Daily digest",
                    prompt="Summarize new findings",
                    schedule={"type": "daily", "days_of_week": [0], "hour": 9},
                    created_at=_NOW,
                    created_by="test-user-id",
                )
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1/versions")

    assert response.status_code == 200
    assert response.json()["versions"][0]["version"] == 1


async def test_get_version_not_found(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat_version",
        mocker.AsyncMock(return_value=None),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1/versions/9")
    assert response.status_code == 404


async def test_update_records_comment_and_author(mocker):
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
                "comment": "tuned prompt",
            },
        )

    assert response.status_code == 200
    kwargs = update_mock.await_args.kwargs
    assert kwargs["updated_by"] == "test-user-id"
    assert kwargs["comment"] == "tuned prompt"


async def test_list_run_sessions(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    sessions_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chat_sessions",
        mocker.AsyncMock(
            return_value=[
                ChatSessionItem(
                    thread_id="12345",
                    title="Daily digest – 2026-06-11",
                    created_at=_NOW,
                    updated_at=_NOW,
                    origin="scheduled",
                    scheduled_chat_id="sc-1",
                    run_status="partial",
                    run_errors=["Planner fallback"],
                )
            ]
        ),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1/sessions")

    assert response.status_code == 200
    assert response.json()["sessions"][0]["thread_id"] == "12345"
    assert response.json()["sessions"][0]["run_status"] == "partial"
    assert response.json()["sessions"][0]["run_errors"] == ["Planner fallback"]
    sessions_mock.assert_awaited_once_with("test-user-id", "sc-1", 50)


def _admin_user() -> CurrentUser:
    return _current_user(frozenset({"chat:use", "chat:schedule", "chat:schedule:read_all"}))


async def test_list_all_requires_read_all_permission(mocker):
    app = _make_app(_current_user(frozenset({"chat:schedule"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules?all=true")
    assert response.status_code == 403
    assert "chat:schedule:read_all" in response.text


async def test_list_all_with_read_all_lists_everyone(mocker):
    list_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chats",
        mocker.AsyncMock(return_value=[_schedule(), _schedule(created_by="someone-else")]),
    )
    app = _make_app(_admin_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules?all=true")

    assert response.status_code == 200
    assert len(response.json()["schedules"]) == 2
    list_mock.assert_awaited_once_with(user_id=None)


async def test_list_filtered_by_user_with_read_all(mocker):
    list_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chats",
        mocker.AsyncMock(return_value=[_schedule(created_by="someone-else")]),
    )
    app = _make_app(_admin_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules?user_id=someone-else")

    assert response.status_code == 200
    list_mock.assert_awaited_once_with(user_id="someone-else")


async def test_list_filtered_by_other_user_without_read_all_forbidden(mocker):
    app = _make_app(_current_user(frozenset({"chat:schedule"})))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules?user_id=someone-else")
    assert response.status_code == 403


async def test_read_all_can_view_other_users_schedule_but_not_mutate(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule(created_by="someone-else")),
    )
    app = _make_app(_admin_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_response = await client.get("/api/v1/chat/schedules/sc-1")
        delete_response = await client.delete("/api/v1/chat/schedules/sc-1")

    assert get_response.status_code == 200
    # Mutations stay owner-only even with read_all.
    assert delete_response.status_code == 404


async def test_run_sessions_use_owner_partition_for_read_all(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule(created_by="someone-else")),
    )
    sessions_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.list_scheduled_chat_sessions",
        mocker.AsyncMock(return_value=[]),
    )
    app = _make_app(_admin_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1/sessions")

    assert response.status_code == 200
    sessions_mock.assert_awaited_once_with("someone-else", "sc-1", 50)


async def test_run_session_history_resolves_owner_thread(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule(created_by="someone-else")),
    )
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_chat_session",
        mocker.AsyncMock(
            return_value=ChatSessionItem(
                thread_id="12345",
                title="run",
                created_at=_NOW,
                updated_at=_NOW,
                origin="scheduled",
                scheduled_chat_id="sc-1",
            )
        ),
    )
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_user",
        mocker.AsyncMock(
            return_value=User(
                user_id="someone-else",
                sub="s",
                iss="i",
                created_at=_NOW,
                last_login=_NOW,
            )
        ),
    )
    load_mock = mocker.patch(
        "reporting.routes.chat_schedules.load_thread_messages",
        mocker.AsyncMock(return_value=[]),
    )
    app = _make_app(_admin_user())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/chat/schedules/sc-1/sessions/12345/history")

    assert response.status_code == 200
    owner_arg = load_mock.await_args.args[0]
    assert owner_arg.user.user_id == "someone-else"


async def test_run_scheduled_chat(mocker):
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule()),
    )
    run_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.request_scheduled_chat_run",
        mocker.AsyncMock(return_value="2026-01-01T00:00:00+00:00"),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/schedules/sc-1/run")

    assert response.status_code == 202
    assert response.json() == {
        "scheduled_chat_id": "sc-1",
        "run_requested_at": "2026-01-01T00:00:00+00:00",
    }
    run_mock.assert_awaited_once_with("sc-1")


async def test_run_scheduled_chat_owner_only(mocker):
    """Another user's schedule is hidden (404) from run requests, even for read_all holders."""
    mocker.patch(
        "reporting.routes.chat_schedules.report_store.get_scheduled_chat",
        mocker.AsyncMock(return_value=_schedule(created_by="someone-else")),
    )
    run_mock = mocker.patch(
        "reporting.routes.chat_schedules.report_store.request_scheduled_chat_run",
        mocker.AsyncMock(),
    )
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/schedules/sc-1/run")

    assert response.status_code == 404
    run_mock.assert_not_called()
