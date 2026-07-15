"""Per-module advisory lock in Neo4j, serializing overlapping sync runs.

Cartography warns that concurrent jobs for the same resource type race on
their update tags and can delete each other's freshly loaded data. Every
run_cartography_module activity therefore acquires a ``SeizuSyncLock`` node
keyed by module name before starting the subprocess, waiting (bounded) when
an overlapping run of the same module — from any stage, schedule, or tick,
on any sync worker replica — holds it. Neo4j is the resource being protected,
so it is also the natural place for the lock.

Locks carry an expiry so a crashed worker can never wedge a module: a lock
past ``expires_at`` is stolen by the next acquirer. The TTL must therefore
comfortably exceed the subprocess timeout (callers pass timeout + margin).
"""

import asyncio
import logging
import time
from types import TracebackType
from typing import Any

import neo4j

logger = logging.getLogger(__name__)

_POLL_SECONDS = 5.0

_CONSTRAINT_QUERY = "CREATE CONSTRAINT seizu_sync_lock_key IF NOT EXISTS FOR (l:SeizuSyncLock) REQUIRE l.key IS UNIQUE"

# Acquire when the lock is free, expired, or already ours (re-entrant for
# activity retries that share an owner id). Returns a row only on success.
_ACQUIRE_QUERY = """
MERGE (l:SeizuSyncLock {key: $key})
WITH l, timestamp() AS now
WHERE l.owner IS NULL OR l.expires_at IS NULL OR l.expires_at < now OR l.owner = $owner
SET l.owner = $owner, l.expires_at = now + $ttl_ms
RETURN l.owner AS owner
"""

_RELEASE_QUERY = """
MATCH (l:SeizuSyncLock {key: $key, owner: $owner})
DELETE l
"""


class LockTimeoutError(Exception):
    """The lock stayed held by another run for the whole wait budget."""


class SyncLock:
    """One acquire/release cycle against Neo4j; use as an async context manager."""

    def __init__(
        self,
        uri: str,
        key: str,
        owner: str,
        ttl_seconds: int,
        wait_timeout_seconds: int,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        self._uri = uri
        self._key = key
        self._owner = owner
        self._ttl_seconds = ttl_seconds
        self._wait_timeout_seconds = wait_timeout_seconds
        self._auth = (user, password) if user and password else None
        self._driver: neo4j.AsyncDriver | None = None

    async def _execute(self, query: str, **params: Any) -> list[Any]:
        assert self._driver is not None
        result = await self._driver.execute_query(query, **params)
        return list(result.records)

    async def __aenter__(self) -> "SyncLock":
        self._driver = neo4j.AsyncGraphDatabase.driver(self._uri, auth=self._auth)
        try:
            await self._execute(_CONSTRAINT_QUERY)
            deadline = time.monotonic() + self._wait_timeout_seconds
            while True:
                records = await self._execute(
                    _ACQUIRE_QUERY,
                    key=self._key,
                    owner=self._owner,
                    ttl_ms=self._ttl_seconds * 1000,
                )
                if records:
                    logger.info(
                        "Acquired sync lock",
                        extra={"lock_key": self._key, "owner": self._owner},
                    )
                    return self
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"sync lock '{self._key}' still held by another run after {self._wait_timeout_seconds}s"
                    )
                await asyncio.sleep(_POLL_SECONDS)
        except BaseException:
            await self._close()
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self._execute(_RELEASE_QUERY, key=self._key, owner=self._owner)
        except Exception:
            # Failing to release is survivable: the expiry reclaims the lock.
            logger.warning("Failed to release sync lock", extra={"lock_key": self._key}, exc_info=True)
        finally:
            await self._close()

    async def _close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None
