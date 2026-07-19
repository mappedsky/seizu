from unittest.mock import AsyncMock, MagicMock

import neo4j.exceptions
import pytest

from reporting.schema.reporting_config import ScheduledQueryWatchScan
from reporting.services import reporting_neo4j


@pytest.fixture(autouse=True)
def _clear_client_cache():
    reporting_neo4j._ASYNC_CLIENT_CACHE = None
    reporting_neo4j._SYNC_CLIENT_CACHE = None
    yield
    reporting_neo4j._ASYNC_CLIENT_CACHE = None
    reporting_neo4j._SYNC_CLIENT_CACHE = None


def test__get_neo4j_client(mocker):
    db_mock = mocker.MagicMock
    driver_ctor = mocker.patch(
        "reporting.services.reporting_neo4j.AsyncGraphDatabase.driver",
        return_value=db_mock,
    )
    assert reporting_neo4j._get_async_neo4j_client() == db_mock
    driver_ctor.assert_called_once_with(
        reporting_neo4j.settings.NEO4J_URI,
        auth=None,
        max_connection_lifetime=reporting_neo4j.settings.NEO4J_MAX_CONNECTION_LIFETIME,
        connection_timeout=reporting_neo4j.settings.NEO4J_CONNECTION_TIMEOUT,
        notifications_min_severity=reporting_neo4j.settings.NEO4J_NOTIFICATIONS_MIN_SEVERITY,
    )


def test__get_neo4j_client_with_cache(mocker):
    db_mock = mocker.MagicMock
    mocker.patch.object(reporting_neo4j, "_ASYNC_CLIENT_CACHE", db_mock)
    assert reporting_neo4j._get_async_neo4j_client() == db_mock


def test__get_sync_neo4j_client(mocker):
    db_mock = mocker.MagicMock
    driver_ctor = mocker.patch(
        "reporting.services.reporting_neo4j.GraphDatabase.driver",
        return_value=db_mock,
    )
    assert reporting_neo4j._get_sync_neo4j_client() == db_mock
    driver_ctor.assert_called_once_with(
        reporting_neo4j.settings.NEO4J_URI,
        auth=None,
        max_connection_lifetime=reporting_neo4j.settings.NEO4J_MAX_CONNECTION_LIFETIME,
        connection_timeout=reporting_neo4j.settings.NEO4J_CONNECTION_TIMEOUT,
        notifications_min_severity=reporting_neo4j.settings.NEO4J_NOTIFICATIONS_MIN_SEVERITY,
    )


async def test_run_query(mocker):
    mock_record = MagicMock()
    driver_mock = MagicMock()

    async def _records():
        yield mock_record

    session_mock = AsyncMock()
    session_mock.run = AsyncMock(return_value=_records())
    driver_mock.session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    driver_mock.session.return_value.__aexit__ = AsyncMock(return_value=False)

    mocker.patch(
        "reporting.services.reporting_neo4j._get_async_neo4j_client",
        return_value=driver_mock,
    )
    result = await reporting_neo4j.run_query("MATCH (n) RETURN n")
    assert result == [mock_record]
    session_mock.run.assert_awaited_once()
    args, kwargs = session_mock.run.await_args
    # The query is wrapped in a Query carrying a server-enforced transaction
    # timeout (driver session.run has no `timeout` kwarg).
    query_arg = args[0]
    assert isinstance(query_arg, reporting_neo4j.Query)
    assert query_arg.text == "MATCH (n) RETURN n"
    assert query_arg.timeout == reporting_neo4j.settings.NEO4J_QUERY_TIMEOUT
    assert kwargs == {"parameters": None}


async def test_explain_query_prefixes_explain_and_returns_plan(mocker):
    plan = {"operatorType": "ProduceResults", "children": []}
    summary = MagicMock()
    summary.plan = plan
    result = AsyncMock()
    result.consume = AsyncMock(return_value=summary)

    session_mock = AsyncMock()
    session_mock.run = AsyncMock(return_value=result)
    driver_mock = MagicMock()
    driver_mock.session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    driver_mock.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch(
        "reporting.services.reporting_neo4j._get_async_neo4j_client",
        return_value=driver_mock,
    )

    out = await reporting_neo4j.explain_query("MATCH (n) RETURN n")
    assert out == plan
    # EXPLAIN is prefixed (plan-only) and carries the transaction timeout.
    query_arg = session_mock.run.await_args.args[0]
    assert isinstance(query_arg, reporting_neo4j.Query)
    assert query_arg.text == "EXPLAIN MATCH (n) RETURN n"
    assert query_arg.timeout == reporting_neo4j.settings.NEO4J_QUERY_TIMEOUT


async def test_run_query_with_single_retry_failure(mocker):
    run_query_mock = mocker.patch(
        "reporting.services.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=[neo4j.exceptions.ServiceUnavailable(), ["test-result"]]),
    )
    result = await reporting_neo4j.run_query_with_retry("test", {})
    assert result == ["test-result"]
    assert run_query_mock.call_count == 2


async def test_run_query_with_raise(mocker):
    run_query_mock = mocker.patch(
        "reporting.services.reporting_neo4j.run_query",
        new=AsyncMock(side_effect=neo4j.exceptions.ServiceUnavailable()),
    )
    with pytest.raises(neo4j.exceptions.ServiceUnavailable):
        await reporting_neo4j.run_query_with_retry("test", {})
    assert run_query_mock.call_count >= 2


async def test_run_query_bounded_stops_after_truncation_sentinel(mocker):
    records = [MagicMock(name=f"record-{index}") for index in range(5)]
    yielded = 0

    async def _records():
        nonlocal yielded
        for record in records:
            yielded += 1
            yield record

    session_mock = AsyncMock()
    session_mock.run = AsyncMock(return_value=_records())
    driver_mock = MagicMock()
    driver_mock.session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    driver_mock.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch(
        "reporting.services.reporting_neo4j._get_async_neo4j_client",
        return_value=driver_mock,
    )

    result, truncated = await reporting_neo4j.run_query_bounded_with_retry(
        "MATCH (n) RETURN n",
        {},
        max_rows=2,
    )

    assert result == records[:2]
    assert truncated is True
    assert yielded == 3


async def test_run_tx(mocker):
    mock_record = MagicMock()
    tx_mock = AsyncMock()

    async def _records():
        yield mock_record

    tx_mock.run = AsyncMock(return_value=_records())
    result = await reporting_neo4j.run_tx(tx_mock, "MATCH (n) RETURN n")
    assert result == [mock_record]
    # No bogus timeout kwarg — an explicit transaction's timeout is set at begin.
    tx_mock.run.assert_awaited_once_with("MATCH (n) RETURN n", parameters=None)


async def test_run_tx_with_single_retry_failure(mocker):
    run_tx_mock = mocker.patch(
        "reporting.services.reporting_neo4j.run_tx",
        new=AsyncMock(side_effect=[neo4j.exceptions.ServiceUnavailable(), ["test-result"]]),
    )
    tx_mock = AsyncMock()
    result = await reporting_neo4j.run_tx_with_retry(tx_mock, "test")
    assert result == ["test-result"]
    assert run_tx_mock.call_count == 2


async def test_run_tx_with_raise(mocker):
    run_tx_mock = mocker.patch(
        "reporting.services.reporting_neo4j.run_tx",
        new=AsyncMock(side_effect=neo4j.exceptions.ServiceUnavailable()),
    )
    tx_mock = AsyncMock()
    with pytest.raises(neo4j.exceptions.ServiceUnavailable):
        await reporting_neo4j.run_tx_with_retry(tx_mock, "test")
    assert run_tx_mock.call_count >= 2


async def test__scan_time(mocker):
    mocker.patch(
        "reporting.services.reporting_neo4j.run_query_with_retry",
        new=AsyncMock(return_value=[{"maxlastupdated": 1}]),
    )
    assert await reporting_neo4j._scan_time(ScheduledQueryWatchScan(grouptype="test")) == 1


async def test__scan_time_no_results(mocker):
    mocker.patch(
        "reporting.services.reporting_neo4j.run_query_with_retry",
        new=AsyncMock(return_value=[{"maxlastupdated": None}]),
    )
    assert await reporting_neo4j._scan_time(ScheduledQueryWatchScan(grouptype="test")) == 0


async def test_check_watch_scan_triggered_true(mocker):
    mocker.patch(
        "reporting.services.reporting_neo4j._scan_time",
        new=AsyncMock(return_value=10),
    )
    # last_scheduled_at = epoch → unix seconds = 0 → 10 > 0
    result = await reporting_neo4j.check_watch_scan_triggered(
        "1970-01-01T00:00:00+00:00", [ScheduledQueryWatchScan(grouptype="test")]
    )
    assert result is True


async def test_check_watch_scan_triggered_false(mocker):
    mocker.patch(
        "reporting.services.reporting_neo4j._scan_time",
        new=AsyncMock(return_value=10),
    )
    # last_scheduled_at far in the future → unix seconds >> 10
    result = await reporting_neo4j.check_watch_scan_triggered(
        "2099-01-01T00:00:00+00:00", [ScheduledQueryWatchScan(grouptype="test")]
    )
    assert result is False


async def test_check_watch_scan_triggered_none_last_scheduled(mocker):
    mocker.patch(
        "reporting.services.reporting_neo4j._scan_time",
        new=AsyncMock(return_value=1),
    )
    # None → scheduled_unix = 0, any non-zero scan_time triggers
    result = await reporting_neo4j.check_watch_scan_triggered(None, [ScheduledQueryWatchScan(grouptype="test")])
    assert result is True
