from contextlib import asynccontextmanager

import pytest

from reporting import scheduled_chats
from reporting.authnz import CurrentUser
from reporting.authnz.headless import HeadlessIdentityError
from reporting.schema.chat import CreateScheduledChatRequest, ScheduledChatItem
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
        "schedule": {"type": "hourly", "interval_hours": 1},
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
    assert kwargs["origin"] == "scheduled"
    record.assert_awaited_once_with("sc-1", "success")


async def test_run_scheduled_chat_records_partial_budgeted_result(mocker):
    run_chat, _lock, record = _patch_run(mocker)
    run_chat.return_value = HeadlessChatResult(
        thread_id="12345",
        summary="completed required steps",
        status="partial",
        budget={"total_tokens": 120_000},
    )

    await scheduled_chats.run_scheduled_chat(_item())

    record.assert_awaited_once_with("sc-1", "partial")


def test_scheduled_chat_requires_exactly_one_trigger():
    common = {"name": "Digest", "prompt": "Summarize"}

    with pytest.raises(ValueError, match="exactly one"):
        CreateScheduledChatRequest(**common)
    with pytest.raises(ValueError, match="exactly one"):
        CreateScheduledChatRequest(
            **common,
            schedule={"type": "hourly", "interval_hours": 1},
            watch_scans=[{"grouptype": "GitHub"}],
        )

    assert (
        CreateScheduledChatRequest(
            **common,
            schedule={"type": "hourly", "interval_hours": 1},
        ).schedule
        is not None
    )


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
        _item(schedule=None, watch_scans=[{"grouptype": "CVEMetadata"}], last_scheduled_at=_FUTURE)
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


async def test_run_requested_runs_even_when_disabled(mocker):
    """A pending "run now" request runs a disabled, not-due schedule."""
    run_chat, lock, record = _patch_run(mocker)

    await scheduled_chats.run_scheduled_chat(_item(enabled=False, last_scheduled_at=_PAST, run_requested_at=_NOW))

    lock.assert_awaited_once_with("sc-1", _PAST)
    run_chat.assert_awaited_once()
    record.assert_awaited_once_with("sc-1", "success")


async def test_stale_run_request_ignored(mocker):
    """A run request older than the last claimed run does not re-trigger."""
    run_chat, lock, _record = _patch_run(mocker)

    await scheduled_chats.run_scheduled_chat(_item(enabled=False, last_scheduled_at=_NOW, run_requested_at=_PAST))

    lock.assert_not_called()
    run_chat.assert_not_called()


# ---------------------------------------------------------------------------
# schedule_due
# ---------------------------------------------------------------------------

from datetime import datetime  # noqa: E402

from reporting.scheduled_chats import schedule_due  # noqa: E402
from reporting.schema.chat import ChatScheduleSpec  # noqa: E402

_CREATED = "2026-01-01T00:00:00+00:00"


def _now(value: str) -> datetime:
    return datetime.fromisoformat(value)


def test_hourly_due_when_never_run():
    spec = ChatScheduleSpec(type="hourly", interval_hours=4)
    assert schedule_due(spec, None, _CREATED, now=_now("2026-01-01T00:05:00+00:00")) is True


def test_hourly_respects_interval():
    spec = ChatScheduleSpec(type="hourly", interval_hours=4)
    last = "2026-01-02T10:00:00+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-02T13:59:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-02T14:00:00+00:00")) is True


def test_daily_runs_on_selected_day_at_hour():
    # 2026-01-05 is a Monday (weekday 0).
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    last = "2026-01-01T09:30:00+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-05T08:59:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-05T09:00:00+00:00")) is True


def test_daily_does_not_rerun_after_claim():
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    # Claimed at Monday 09:00:20; next due is the following Monday.
    last = "2026-01-05T09:00:20+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-05T15:00:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-12T09:00:00+00:00")) is True


def test_daily_new_schedule_waits_for_next_occurrence():
    # Created Tuesday; Mondays at 09:00 must not fire until next Monday.
    spec = ChatScheduleSpec(type="daily", days_of_week=[0], hour=9)
    created = "2026-01-06T12:00:00+00:00"  # Tuesday
    assert schedule_due(spec, None, created, now=_now("2026-01-07T12:00:00+00:00")) is False
    assert schedule_due(spec, None, created, now=_now("2026-01-12T09:00:00+00:00")) is True


def test_monthly_runs_on_selected_days():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[1, 15])
    last = "2026-01-01T00:00:10+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-14T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-01-15T00:00:00+00:00")) is True


def test_monthly_day_31_clamps_to_last_day_of_month():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[31])
    # April has 30 days; the run lands on the 30th.
    last = "2026-03-31T00:00:10+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-04-29T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-04-30T00:00:00+00:00")) is True


def test_monthly_day_29_clamps_in_february():
    spec = ChatScheduleSpec(type="monthly", days_of_month=[29])
    # 2026 is not a leap year; February runs on the 28th.
    last = "2026-01-29T00:00:10+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-02-27T23:00:00+00:00")) is False
    assert schedule_due(spec, last, _CREATED, now=_now("2026-02-28T00:00:00+00:00")) is True


def test_monthly_clamped_day_runs_once_not_twice():
    # Days {30, 31} in a 30-day month collapse to a single occurrence on the 30th.
    spec = ChatScheduleSpec(type="monthly", days_of_month=[30, 31])
    last = "2026-04-30T00:00:10+00:00"
    assert schedule_due(spec, last, _CREATED, now=_now("2026-04-30T23:59:00+00:00")) is False


def test_schedule_spec_validation():
    import pytest

    with pytest.raises(ValueError):
        ChatScheduleSpec(type="hourly")
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="daily", days_of_week=[])
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="daily", days_of_week=[7], hour=9)
    with pytest.raises(ValueError):
        ChatScheduleSpec(type="monthly", days_of_month=[0])
    assert ChatScheduleSpec(type="monthly", days_of_month=[29, 31]).days_of_month == [29, 31]


# ---------------------------------------------------------------------------
# _bootstrap / _run_worker / main
# ---------------------------------------------------------------------------


def test_bootstrap_installs_shutdown_handlers(mocker):
    installed = mocker.patch("reporting.scheduled_chats.install_shutdown_handlers")
    from reporting.scheduled_chats import _bootstrap

    _bootstrap()

    installed.assert_called_once()


def test_monthly_year_rollback():
    # January: day 31 hasn't happened yet → look back to Dec 31 of previous year.
    spec = ChatScheduleSpec(type="monthly", days_of_month=[31])
    # last run was Dec 31 — the Dec occurrence is already claimed, so not due.
    assert schedule_due(spec, "2025-12-31T00:00:10+00:00", _CREATED, now=_now("2026-01-15T00:00:00+00:00")) is False
    # no prior run (None) → floor=created_at which is before Dec 31 → due.
    assert schedule_due(spec, None, "2025-12-01T00:00:00+00:00", now=_now("2026-01-15T00:00:00+00:00")) is True


async def test_run_scheduled_chat_records_failure_status(mocker):
    run_chat, _lock, record = _patch_run(mocker)
    run_chat.return_value = HeadlessChatResult(thread_id="t1", summary="err", status="failed")

    await scheduled_chats.run_scheduled_chat(_item())

    record.assert_awaited_once_with("sc-1", "failure", error="Headless run ended with status: failed")


async def test_record_failure_handles_store_exception(mocker):
    mocker.patch(
        "reporting.scheduled_chats.report_store.record_scheduled_chat_result",
        mocker.AsyncMock(side_effect=RuntimeError("store down")),
    )
    # Should not raise — exception is caught and logged.
    await scheduled_chats._record_failure("sc-1", "something went wrong")


async def test_run_worker_polls_and_exits_on_shutdown(mocker):
    @asynccontextmanager
    async def _noop():
        yield

    import reporting.scheduled_chats as sc

    mocker.patch("reporting.scheduled_chats._bootstrap")
    mocker.patch("reporting.scheduled_chats.chat_worker_resources", _noop)
    run_chat = mocker.patch("reporting.scheduled_chats.run_scheduled_chat", mocker.AsyncMock())
    mocker.patch(
        "reporting.scheduled_chats.report_store.list_scheduled_chats",
        mocker.AsyncMock(return_value=[_item()]),
    )

    async def _sleep_and_set(*args, **kwargs):
        sc._shutdown_event.set()

    mocker.patch("reporting.scheduled_chats.asyncio.sleep", _sleep_and_set)

    sc._shutdown_event.clear()
    try:
        from reporting.scheduled_chats import _run_worker

        await _run_worker()
    finally:
        sc._shutdown_event.clear()

    run_chat.assert_awaited_once()


def test_main_runs_when_enabled(mocker):
    mocker.patch("reporting.scheduled_chats.settings.CHAT_ENABLED", True)
    mocker.patch("reporting.scheduled_chats.settings.CHAT_SCHEDULES_ENABLED", True)
    mock_run = mocker.patch("reporting.scheduled_chats.asyncio.run")

    from reporting.scheduled_chats import main

    main()

    assert mock_run.called


def test_main_exits_when_disabled(mocker):
    mocker.patch("reporting.scheduled_chats.settings.CHAT_ENABLED", False)
    mock_run = mocker.patch("reporting.scheduled_chats.asyncio.run")

    from reporting.scheduled_chats import main

    main()

    mock_run.assert_not_called()
