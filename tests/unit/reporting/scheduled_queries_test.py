from unittest.mock import AsyncMock

from reporting import scheduled_queries
from reporting.schema.report_config import ScheduledQueryItem
from reporting.schema.reporting_config import ScheduledQueryAction


def _make_sq_item(
    sq_id: str,
    name: str,
    enabled: bool = True,
    frequency: int = 5,
    last_scheduled_at: str | None = None,
) -> ScheduledQueryItem:
    return ScheduledQueryItem(
        scheduled_query_id=sq_id,
        name=name,
        cypher="TEST",
        enabled=enabled,
        frequency=frequency,
        params=[],
        watch_scans=[],
        actions=[{"action_type": "sqs", "action_config": {"sqs_queue": "test"}}],
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
        created_by="seed-script",
        last_scheduled_at=last_scheduled_at,
    )


async def test_schedule_query_disabled(mocker):
    """Disabled queries are skipped without checking triggers."""
    item = _make_sq_item("test_query", "test name", enabled=False)
    trigger_mock = mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(),
    )
    await scheduled_queries.schedule_query(item)
    assert trigger_mock.call_count == 0


async def test_schedule_query_not_triggered(mocker):
    """If no trigger fires, the query is not run."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=False),
    )
    run_mock = mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(),
    )
    await scheduled_queries.schedule_query(item)
    assert run_mock.call_count == 0


async def test_schedule_query_lock_not_acquired(mocker):
    """If the trigger fires but the lock CAS fails, the query is not run."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.acquire_scheduled_query_lock",
        new=AsyncMock(return_value=False),
    )
    run_mock = mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(),
    )
    await scheduled_queries.schedule_query(item)
    assert run_mock.call_count == 0


async def test_schedule_query_runs_and_handles_results(mocker):
    """When the lock is acquired, the query is run and actions are handled."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.acquire_scheduled_query_lock",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(
            return_value=[
                {"details": {"test": "test"}},
                {"details": {"test2": "test2"}},
            ]
        ),
    )
    handle_results_mock = mocker.patch(
        "reporting.scheduled_queries._handle_results",
        new=AsyncMock(),
    )
    record_result_mock = mocker.patch(
        "reporting.scheduled_queries.report_store.record_scheduled_query_result",
        new=AsyncMock(),
    )
    await scheduled_queries.schedule_query(item)
    assert handle_results_mock.call_count == 1
    record_result_mock.assert_called_once_with(item.scheduled_query_id, "success")


async def test_schedule_query_failure_records_error(mocker):
    """When query execution raises, the failure is recorded."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.acquire_scheduled_query_lock",
        new=AsyncMock(return_value=True),
    )
    exc = Exception("test error")
    mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(side_effect=exc),
    )
    record_result_mock = mocker.patch(
        "reporting.scheduled_queries.report_store.record_scheduled_query_result",
        new=AsyncMock(),
    )
    await scheduled_queries.schedule_query(item)
    record_result_mock.assert_called_once_with(item.scheduled_query_id, "failure", error="test error")


async def test___handle_results_no_action_type(mocker):
    """Missing action_type is handled gracefully."""
    mod_mock = mocker.patch("reporting.scheduled_query_modules.get_module")
    sq_id = "test_query"
    await scheduled_queries._handle_results(sq_id, mocker.MagicMock(), [])
    assert mod_mock.call_count == 0


async def test___handle_results_unknown_action(mocker):
    """Action with unknown action_type (not in module names) is skipped."""
    mod_mock = mocker.patch("reporting.scheduled_query_modules.get_module")
    sq_id = "test_query"
    action = ScheduledQueryAction(
        action_type="sqs",
        action_config={"sqs_queue": "test"},
    )
    results = [{"details": {"hello": "world"}}]
    mocker.patch("reporting.scheduled_query_modules.get_module_names", return_value=["slack"])
    await scheduled_queries._handle_results(sq_id, action, results)
    assert mod_mock.call_count == 0


async def test___handle_results_known_action(mocker):
    """Action with known action_type calls the module."""
    mod_mock = mocker.patch("reporting.scheduled_query_modules.get_module")
    sq_id = "test_query"
    action = ScheduledQueryAction(
        action_type="sqs",
        action_config={"sqs_queue": "test"},
    )
    results = [{"details": {"hello": "world"}}, {"details": {"foo": "bar"}}]
    mocker.patch("reporting.scheduled_query_modules.get_module_names", return_value=["sqs"])
    await scheduled_queries._handle_results(sq_id, action, results)
    assert mod_mock.call_count == 1


async def test___handle_results_known_async_action(mocker):
    """Async action handlers run on the scheduled-query worker event loop."""
    module = mocker.MagicMock()
    module.handle_results = AsyncMock()
    mocker.patch("reporting.scheduled_query_modules.get_module", return_value=module)
    mocker.patch("reporting.scheduled_query_modules.get_module_names", return_value=["temporal"])
    action = ScheduledQueryAction(
        action_type="temporal",
        action_config={"workflow": "cve_repo_report"},
    )
    results = [{"details": {"repo": "org/app"}}]

    await scheduled_queries._handle_results("sq-1", action, results)

    module.handle_results.assert_awaited_once_with("sq-1", action, results)


async def test__schedule_queries(mocker):
    item1 = _make_sq_item("sq1", "test name")
    item2 = _make_sq_item("sq2", "test name 2")
    mocker.patch(
        "reporting.scheduled_queries.report_store.initialize",
        new=AsyncMock(),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=[item1, item2]),
    )
    load_modules_mock = mocker.patch("reporting.scheduled_query_modules.load_modules")
    bootstrap_mock = mocker.patch("reporting.scheduled_queries._bootstrap")
    sq_mock = mocker.patch(
        "reporting.scheduled_queries.schedule_query",
        new=AsyncMock(),
    )

    # Control the shutdown event: set it after the sleep call so only one
    # loop iteration runs.
    sleep_call_count = 0

    async def fake_sleep(_delay: float) -> None:
        nonlocal sleep_call_count
        sleep_call_count += 1
        scheduled_queries._shutdown_event.set()

    mocker.patch("asyncio.sleep", new=fake_sleep)

    scheduled_queries._shutdown_event.clear()
    await scheduled_queries._schedule_queries()

    assert load_modules_mock.call_count == 1
    assert bootstrap_mock.call_count == 1
    assert sq_mock.call_count == 2
    assert sleep_call_count == 1


async def test__schedule_queries_calls_with_item(mocker):
    """_schedule_queries passes the full ScheduledQueryItem to schedule_query."""
    item = _make_sq_item("sq1", "test name")
    mocker.patch(
        "reporting.scheduled_queries.report_store.initialize",
        new=AsyncMock(),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.list_scheduled_queries",
        new=AsyncMock(return_value=[item]),
    )
    mocker.patch("reporting.scheduled_query_modules.load_modules")
    mocker.patch("reporting.scheduled_queries._bootstrap")
    sq_mock = mocker.patch(
        "reporting.scheduled_queries.schedule_query",
        new=AsyncMock(),
    )

    async def fake_sleep(_delay: float) -> None:
        scheduled_queries._shutdown_event.set()

    mocker.patch("asyncio.sleep", new=fake_sleep)

    scheduled_queries._shutdown_event.clear()
    await scheduled_queries._schedule_queries()

    sq_mock.assert_called_once_with(item)


async def test__frequency_triggered_no_previous_run():
    """A query with no last_scheduled_at is always triggered."""
    assert scheduled_queries._frequency_triggered(None, 60) is True


async def test__frequency_triggered_not_yet_due():
    """A query scheduled very recently is not triggered."""
    future = "2099-01-01T00:00:00+00:00"
    assert scheduled_queries._frequency_triggered(future, 60) is False


async def test__frequency_triggered_overdue():
    """A query whose window has elapsed is triggered."""
    past = "2000-01-01T00:00:00+00:00"
    assert scheduled_queries._frequency_triggered(past, 60) is True


async def test__is_triggered_frequency(mocker):
    """_is_triggered returns True when frequency condition fires."""
    item = _make_sq_item("sq1", "test", last_scheduled_at=None)
    sq = scheduled_queries._item_to_scheduled_query(item)
    result = await scheduled_queries._is_triggered(item, sq)
    assert result is True


async def test__is_triggered_watch_scan_fires(mocker):
    """_is_triggered returns True when watch scan condition fires."""
    item = _make_sq_item("sq1", "test", frequency=0)
    # Give it a watch_scan trigger instead
    item.watch_scans = [{"grouptype": ".*", "syncedtype": ".*", "groupid": ".*"}]
    item.frequency = None
    sq = scheduled_queries._item_to_scheduled_query(item)
    mocker.patch(
        "reporting.scheduled_queries.check_watch_scan_triggered",
        new=AsyncMock(return_value=True),
    )
    result = await scheduled_queries._is_triggered(item, sq)
    assert result is True


async def test__is_triggered_no_trigger(mocker):
    """_is_triggered returns False when neither condition fires."""
    item = _make_sq_item("sq1", "test", last_scheduled_at="2099-01-01T00:00:00+00:00")
    sq = scheduled_queries._item_to_scheduled_query(item)
    result = await scheduled_queries._is_triggered(item, sq)
    assert result is False


async def test_schedule_query_record_result_failure_on_success(mocker):
    """If record_scheduled_query_result raises after a successful run, it is caught."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.acquire_scheduled_query_lock",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(return_value=[]),
    )
    mocker.patch(
        "reporting.scheduled_queries._handle_results",
        new=AsyncMock(),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.record_scheduled_query_result",
        new=AsyncMock(side_effect=Exception("store error")),
    )
    # Should not raise
    await scheduled_queries.schedule_query(item)


async def test_schedule_query_record_result_failure_on_failure(mocker):
    """If record_scheduled_query_result raises after a failed run, it is caught."""
    item = _make_sq_item("test_query", "test name")
    mocker.patch(
        "reporting.scheduled_queries._is_triggered",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.acquire_scheduled_query_lock",
        new=AsyncMock(return_value=True),
    )
    mocker.patch(
        "reporting.scheduled_queries.run_query_with_retry",
        new=AsyncMock(side_effect=Exception("query failed")),
    )
    mocker.patch(
        "reporting.scheduled_queries.report_store.record_scheduled_query_result",
        new=AsyncMock(side_effect=Exception("store error")),
    )
    # Should not raise
    await scheduled_queries.schedule_query(item)


def test_main_disabled(mocker):
    """main() exits immediately when ENABLE_SCHEDULED_QUERIES is False."""
    mocker.patch("reporting.scheduled_queries.settings.ENABLE_SCHEDULED_QUERIES", False)
    run_mock = mocker.patch("asyncio.run")
    scheduled_queries.main()
    assert run_mock.call_count == 0


def test_main_enabled(mocker):
    """main() calls asyncio.run when ENABLE_SCHEDULED_QUERIES is True."""
    mocker.patch("reporting.scheduled_queries.settings.ENABLE_SCHEDULED_QUERIES", True)
    run_mock = mocker.patch("asyncio.run")
    scheduled_queries.main()
    assert run_mock.call_count == 1


def test__bootstrap_registers_sigterm(mocker):
    """_bootstrap delegates worker signal registration."""
    signal_mock = mocker.patch("reporting.scheduled_queries.install_shutdown_handlers")
    scheduled_queries._bootstrap()
    assert signal_mock.call_count == 1
