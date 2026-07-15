import pytest

from cartography_sync import sync_lock
from cartography_sync.sync_lock import LockTimeoutError, SyncLock


class _FakeDriver:
    """Scripted execute_query: acquire attempts pop results off a queue."""

    def __init__(self, acquire_outcomes: list[bool]):
        self.acquire_outcomes = acquire_outcomes
        self.queries: list[tuple[str, dict]] = []
        self.closed = False

    async def execute_query(self, query, **params):
        self.queries.append((query, params))

        class _Result:
            records: list = []

        result = _Result()
        if "MERGE" in query:
            result.records = [{"owner": params["owner"]}] if self.acquire_outcomes.pop(0) else []
        return result

    async def close(self):
        self.closed = True


def _lock(monkeypatch, outcomes: list[bool], wait_timeout: int = 0) -> tuple[SyncLock, _FakeDriver]:
    driver = _FakeDriver(outcomes)
    monkeypatch.setattr(sync_lock.neo4j.AsyncGraphDatabase, "driver", lambda uri, auth: driver)
    lock = SyncLock(
        uri="bolt://neo4j:7687",
        key="cartography-module:aws",
        owner="wf-1:1",
        ttl_seconds=60,
        wait_timeout_seconds=wait_timeout,
    )
    return lock, driver


async def test_acquire_release_cycle(monkeypatch):
    lock, driver = _lock(monkeypatch, outcomes=[True])
    async with lock:
        pass
    constraint, acquire, release = driver.queries
    assert "CREATE CONSTRAINT" in constraint[0]
    assert acquire[1] == {"key": "cartography-module:aws", "owner": "wf-1:1", "ttl_ms": 60_000}
    assert "DELETE l" in release[0]
    assert release[1] == {"key": "cartography-module:aws", "owner": "wf-1:1"}
    assert driver.closed


async def test_contended_lock_retries_until_acquired(monkeypatch):
    monkeypatch.setattr(sync_lock, "_POLL_SECONDS", 0.01)
    lock, driver = _lock(monkeypatch, outcomes=[False, False, True], wait_timeout=5)
    async with lock:
        pass
    acquire_attempts = [q for q, _ in driver.queries if "MERGE" in q]
    assert len(acquire_attempts) == 3


async def test_wait_budget_exhausted_raises_and_closes(monkeypatch):
    monkeypatch.setattr(sync_lock, "_POLL_SECONDS", 0.01)
    lock, driver = _lock(monkeypatch, outcomes=[False], wait_timeout=0)
    with pytest.raises(LockTimeoutError, match="still held"):
        async with lock:
            pass
    # No release attempted (never acquired), and the driver was closed.
    assert not any("DELETE" in q for q, _ in driver.queries)
    assert driver.closed


async def test_release_failure_is_survivable(monkeypatch):
    lock, driver = _lock(monkeypatch, outcomes=[True])

    original = driver.execute_query

    async def _failing(query, **params):
        if "DELETE" in query:
            raise RuntimeError("connection lost")
        return await original(query, **params)

    driver.execute_query = _failing
    async with lock:  # must not raise on exit; expiry reclaims the lock
        pass
    assert driver.closed
