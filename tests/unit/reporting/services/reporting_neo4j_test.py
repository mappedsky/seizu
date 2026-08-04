import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import neo4j.exceptions
import pytest

from reporting.schema.reporting_config import ScheduledQueryWatchScan
from reporting.services import reporting_neo4j


@pytest.fixture(autouse=True)
def _clear_client_cache():
    reporting_neo4j._ASYNC_CLIENT_CACHE = None
    reporting_neo4j._SYNC_CLIENT_CACHE = None
    reporting_neo4j.reset_graph_schema_cache()
    yield
    reporting_neo4j._ASYNC_CLIENT_CACHE = None
    reporting_neo4j._SYNC_CLIENT_CACHE = None
    reporting_neo4j.reset_graph_schema_cache()


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


# --- Graph schema cache --------------------------------------------------------


async def test_fetch_graph_schema_is_cached_within_the_ttl(mocker):
    uncached = mocker.patch(
        "reporting.services.reporting_neo4j._fetch_graph_schema_uncached",
        new=AsyncMock(return_value={"labels": ["CVE"]}),
    )
    mocker.patch("reporting.settings.GRAPH_SCHEMA_CACHE_TTL_SECONDS", 300)

    first = await reporting_neo4j.fetch_graph_schema()
    second = await reporting_neo4j.fetch_graph_schema()

    assert first == second == {"labels": ["CVE"]}
    # The point of the cache: an agent that re-introspects pays once.
    assert uncached.await_count == 1


async def test_fetch_graph_schema_refetches_after_the_ttl(mocker):
    uncached = mocker.patch(
        "reporting.services.reporting_neo4j._fetch_graph_schema_uncached",
        new=AsyncMock(side_effect=[{"labels": ["old"]}, {"labels": ["new"]}]),
    )
    mocker.patch("reporting.settings.GRAPH_SCHEMA_CACHE_TTL_SECONDS", 300)
    clock = [0.0]
    mocker.patch("reporting.services.reporting_neo4j.time.monotonic", lambda: clock[0])

    assert await reporting_neo4j.fetch_graph_schema() == {"labels": ["old"]}
    clock[0] = 301.0
    # A sync that adds a label must become visible without a restart.
    assert await reporting_neo4j.fetch_graph_schema() == {"labels": ["new"]}
    assert uncached.await_count == 2


async def test_fetch_graph_schema_ttl_zero_disables_caching(mocker):
    uncached = mocker.patch(
        "reporting.services.reporting_neo4j._fetch_graph_schema_uncached",
        new=AsyncMock(return_value={"labels": []}),
    )
    mocker.patch("reporting.settings.GRAPH_SCHEMA_CACHE_TTL_SECONDS", 0)

    await reporting_neo4j.fetch_graph_schema()
    await reporting_neo4j.fetch_graph_schema()

    assert uncached.await_count == 2


async def test_fetch_graph_schema_concurrent_callers_introspect_once(mocker):
    started = 0

    async def _slow() -> dict:
        nonlocal started
        started += 1
        await asyncio.sleep(0)
        return {"labels": ["CVE"]}

    mocker.patch("reporting.services.reporting_neo4j._fetch_graph_schema_uncached", new=_slow)
    mocker.patch("reporting.settings.GRAPH_SCHEMA_CACHE_TTL_SECONDS", 300)

    results = await asyncio.gather(*(reporting_neo4j.fetch_graph_schema() for _ in range(8)))

    assert all(result == {"labels": ["CVE"]} for result in results)
    # Parallel workers hitting a cold cache must not stampede the database.
    assert started == 1


# --- run_query_streamed: the limiter that closes the memory path ---------------


class _FakeRecord(dict):
    """Enough of a Record for the serializer used by callers."""


def _streaming_session(records, *, fail_first: bool = False):
    """A driver whose session yields records one at a time."""
    state = {"attempts": 0}

    class _Result:
        def __aiter__(self):
            async def gen():
                for record in records:
                    yield record

            return gen()

    class _Session:
        async def run(self, *_a, **_kw):
            state["attempts"] += 1
            if fail_first and state["attempts"] == 1:
                raise neo4j.exceptions.ServiceUnavailable("flapping")
            return _Result()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    driver = MagicMock()
    driver.session = lambda: _Session()
    return driver, state


async def test_streamed_stops_at_the_row_limit(mocker):
    rows = [_FakeRecord(i=i) for i in range(50)]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=10, max_bytes=None, serialize=dict
    )

    assert len(kept) == 10
    assert reason == "row_limit"


async def test_streamed_reports_no_reason_when_the_result_fits_exactly(mocker):
    rows = [_FakeRecord(i=i) for i in range(10)]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=10, max_bytes=None, serialize=dict
    )

    # Exactly at the bound is complete, not truncated.
    assert len(kept) == 10
    assert reason == ""


async def test_streamed_stops_at_the_byte_limit(mocker):
    rows = [_FakeRecord(pad="x" * 100) for _ in range(50)]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=None, max_bytes=500, serialize=dict
    )

    assert reason == "byte_limit"
    assert 0 < len(kept) < 50


async def test_streamed_drops_a_first_row_that_cannot_fit(mocker):
    """A single oversized record is not retained; the driver's own allocation
    is the floor this layer cannot do anything about."""
    rows = [_FakeRecord(pad="x" * 10_000), _FakeRecord(pad="y")]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=None, max_bytes=100, serialize=dict
    )

    assert kept == []
    assert reason == "byte_limit"


async def test_streamed_is_unbounded_when_limits_are_none_or_zero(mocker):
    rows = [_FakeRecord(i=i) for i in range(25)]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=0, max_bytes=0, serialize=dict
    )

    assert len(kept) == 25
    assert reason == ""


async def test_streamed_retries_after_service_unavailable(mocker):
    rows = [_FakeRecord(i=i) for i in range(3)]
    driver, state = _streaming_session(rows, fail_first=True)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, reason = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=None, max_bytes=None, serialize=dict
    )

    # Retried, and the partial read from the failed attempt is not carried over.
    assert state["attempts"] == 2
    assert len(kept) == 3
    assert reason == ""


async def test_streamed_byte_accounting_is_a_source_bound_not_a_response_bound(mocker):
    """The bound protects this process; the emitted response is bounded exactly
    downstream, where the envelope and indentation are known."""
    rows = [_FakeRecord() for _ in range(5)]
    driver, _ = _streaming_session(rows)
    mocker.patch("reporting.services.reporting_neo4j._get_async_neo4j_client", return_value=driver)

    kept, _ = await reporting_neo4j.run_query_streamed(
        "MATCH (n) RETURN n", None, max_rows=None, max_bytes=10, serialize=dict
    )

    # Row bodies alone fit the budget; the serialized list around them does not,
    # which is why the caller re-bounds the assembled payload.
    assert len(json.dumps(kept, indent=2).encode()) > 10
