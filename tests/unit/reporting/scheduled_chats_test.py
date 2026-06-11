from reporting import scheduled_chats
from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError
from reporting.schema.chat import ScheduledChatItem
from reporting.schema.report_config import User
from reporting.services.headless_chat import HeadlessChatResult

_NOW = "2024-01-01T00:00:00+00:00"
_PAST = "2000-01-01T00:00:00+00:00"
_FUTURE = "2099-01-01T00:00:00+00:00"


def _item(**overrides) -> ScheduledChatItem:
    defaults = {
        "scheduled_chat_id": "sc-1",
        "name": "Daily digest",
        "prompt": "Summarize new findings",
        "frequency": 60,
        "watch_scans": [],
        "enabled": True,
        "created_at": _NOW,
        "updated_at": _NOW,
        "created_by": "user-1",
        "last_scheduled_at": _PAST,
    }
    defaults.update(overrides)
    return ScheduledChatItem(**defaults)


def _current_user() -> CurrentUser:
    return CurrentUser(
        user=User(
            user_id="user-1",
            sub="sub",
            iss="iss",
            email="user@example.com",
            created_at=_NOW,
            last_login=_NOW,
            role="seizu-editor",
        ),
        jwt_claims={},
        permissions=frozenset({"chat:use", "chat:bypass_permissions"}),
    )


def _patch_run(mocker):
    mocker.patch(
        "reporting.scheduled_chats.resolve_stored_user",
        mocker.AsyncMock(return_value=_current_user()),
    )
    run_chat = mocker.patch(
        "reporting.scheduled_chats.headless_chat.run_headless_chat",
        mocker.AsyncMock(return_value=HeadlessChatResult(thread_id="12345", summary="done")),
    )
    lock = mocker.patch(
        "reporting.scheduled_chats.report_store.acquire_scheduled_chat_lock",
        mocker.AsyncMock(return_value=True),
    )
    record = mocker.patch(
        "reporting.scheduled_chats.report_store.record_scheduled_chat_result",
        mocker.AsyncMock(),
    )
    return run_chat, lock, record


async def test_run_scheduled_chat_success(mocker):
    run_chat, lock, record = _patch_run(mocker)

    await scheduled_chats.run_scheduled_chat(_item())

    lock.assert_awaited_once_with("sc-1", _PAST)
    kwargs = run_chat.await_args.kwargs
    assert kwargs["prompt"] == "Summarize new findings"
    assert "Daily digest" in kwargs["title"]
    record.assert_awaited_once_with("sc-1", "success")


async def test_disabled_schedule_skipped(mocker):
    run_chat, lock, _record = _patch_run(mocker)

    await scheduled_chats.run_scheduled_chat(_item(enabled=False))

    lock.assert_not_called()
    run_chat.assert_not_called()


async def test_not_due_schedule_skipped(mocker):
    run_chat, lock, _record = _patch_run(mocker)

    await scheduled_chats.run_scheduled_chat(_item(last_scheduled_at=_FUTURE))

    lock.assert_not_called()
    run_chat.assert_not_called()


async def test_lock_contention_skips_run(mocker):
    run_chat, lock, _record = _patch_run(mocker)
    lock.return_value = False

    await scheduled_chats.run_scheduled_chat(_item())

    run_chat.assert_not_called()


async def test_watch_scan_trigger(mocker):
    run_chat, _lock, record = _patch_run(mocker)
    check = mocker.patch(
        "reporting.scheduled_chats.check_watch_scan_triggered",
        mocker.AsyncMock(return_value=True),
    )

    await scheduled_chats.run_scheduled_chat(
        _item(frequency=None, watch_scans=[{"grouptype": "CVEMetadata"}], last_scheduled_at=_FUTURE)
    )

    check.assert_awaited_once()
    run_chat.assert_awaited_once()
    record.assert_awaited_once_with("sc-1", "success")


async def test_identity_failure_records_failure(mocker):
    run_chat, _lock, record = _patch_run(mocker)
    mocker.patch(
        "reporting.scheduled_chats.resolve_stored_user",
        mocker.AsyncMock(side_effect=HeadlessIdentityError("archived")),
    )

    await scheduled_chats.run_scheduled_chat(_item())

    run_chat.assert_not_called()
    record.assert_awaited_once_with("sc-1", "failure", error="archived")


async def test_run_error_records_failure(mocker):
    run_chat, _lock, record = _patch_run(mocker)
    run_chat.side_effect = RuntimeError("boom")

    await scheduled_chats.run_scheduled_chat(_item())

    record.assert_awaited_once_with("sc-1", "failure", error="boom")
