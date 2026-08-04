import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal, cast

import neo4j.exceptions
from neo4j import AsyncGraphDatabase, AsyncTransaction, Driver, GraphDatabase, Query, Record

from reporting import settings
from reporting.schema.reporting_config import ScheduledQueryWatchScan
from reporting.services.payload_bounds import json_size_bytes

logger = logging.getLogger(__name__)

_ASYNC_CLIENT_CACHE: neo4j.AsyncDriver | None = None
_SYNC_CLIENT_CACHE: Driver | None = None
# (monotonic timestamp, schema) for the process-wide graph schema cache.
_SCHEMA_CACHE: tuple[float, dict[str, Any]] | None = None
_SCHEMA_CACHE_LOCK = asyncio.Lock()


def _get_async_neo4j_client() -> neo4j.AsyncDriver:
    global _ASYNC_CLIENT_CACHE
    if _ASYNC_CLIENT_CACHE is None:
        neo4j_auth = None
        if settings.NEO4J_USER or settings.NEO4J_PASSWORD:
            neo4j_auth = (settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        _ASYNC_CLIENT_CACHE = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=neo4j_auth,
            max_connection_lifetime=settings.NEO4J_MAX_CONNECTION_LIFETIME,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            notifications_min_severity=cast(
                Literal["OFF", "WARNING", "INFORMATION"],
                settings.NEO4J_NOTIFICATIONS_MIN_SEVERITY,
            ),
        )
    return _ASYNC_CLIENT_CACHE


def _get_sync_neo4j_client() -> Driver:
    """Return a synchronous Neo4j driver — used only by CyVer validators."""
    global _SYNC_CLIENT_CACHE
    if _SYNC_CLIENT_CACHE is None:
        neo4j_auth = None
        if settings.NEO4J_USER or settings.NEO4J_PASSWORD:
            neo4j_auth = (settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        _SYNC_CLIENT_CACHE = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=neo4j_auth,
            max_connection_lifetime=settings.NEO4J_MAX_CONNECTION_LIFETIME,
            connection_timeout=settings.NEO4J_CONNECTION_TIMEOUT,
            notifications_min_severity=cast(
                Literal["OFF", "WARNING", "INFORMATION"],
                settings.NEO4J_NOTIFICATIONS_MIN_SEVERITY,
            ),
        )
    return _SYNC_CLIENT_CACHE


async def run_query(cypher: str, parameters: dict = None) -> list[Record]:
    results = []
    driver = _get_async_neo4j_client()
    async with driver.session() as session:
        # The transaction timeout must travel as part of the Query object — the
        # driver's session.run() has no `timeout` kwarg, so passing one was
        # silently sent as a query parameter (`$timeout`) and NOT enforced,
        # leaving heavy/unindexed queries to run unbounded. Wrapped here, the
        # server terminates the transaction after NEO4J_QUERY_TIMEOUT seconds.
        query = Query(cypher, timeout=settings.NEO4J_QUERY_TIMEOUT)
        query_results = await session.run(query, parameters=parameters)
        async for result in query_results:
            results.append(result)
    return results


async def explain_query(cypher: str) -> dict[str, Any]:
    """Return Neo4j's query plan for ``EXPLAIN <cypher>`` without executing it.

    EXPLAIN produces the plan only — it never runs the query (unlike PROFILE).
    Callers must validate *cypher* first (so disallowed constructs are rejected
    before it reaches the planner) and pass a bare query: the validator rejects a
    leading EXPLAIN/PROFILE, so prefixing EXPLAIN here is always plan-only.
    """
    driver = _get_async_neo4j_client()
    async with driver.session() as session:
        query = Query(f"EXPLAIN {cypher}", timeout=settings.NEO4J_QUERY_TIMEOUT)
        result = await session.run(query)
        summary = await result.consume()
    return summary.plan or {}


_LABELS_QUERY = "CALL db.labels() YIELD label RETURN label ORDER BY label"
_RELS_QUERY = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS type ORDER BY type"
_PROPS_QUERY = "CALL db.propertyKeys() YIELD propertyKey RETURN propertyKey AS key ORDER BY key"
_INDEXES_QUERY = "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state ORDER BY name"


async def _fetch_indexes() -> list[dict[str, Any]]:
    try:
        results = await run_query(_INDEXES_QUERY)
    except neo4j.exceptions.Neo4jError:
        # SHOW INDEXES needs catalog privileges (and a recent Neo4j); degrade to
        # an empty list rather than failing the whole schema fetch.
        logger.warning("SHOW INDEXES failed; returning schema without indexes", exc_info=True)
        return []
    return [
        {
            "name": str(record["name"]),
            "type": str(record["type"]),
            "entity_type": str(record["entityType"]),
            "labels_or_types": [str(value) for value in (record["labelsOrTypes"] or [])],
            "properties": [str(value) for value in (record["properties"] or [])],
            "state": str(record["state"]),
        }
        for record in results
    ]


async def _fetch_graph_schema_uncached() -> dict[str, Any]:
    labels = await run_query(_LABELS_QUERY)
    rels = await run_query(_RELS_QUERY)
    props = await run_query(_PROPS_QUERY)
    indexes = await _fetch_indexes()
    return {
        "labels": [str(record["label"]) for record in labels],
        "relationship_types": [str(record["type"]) for record in rels],
        "property_keys": [str(record["key"]) for record in props],
        "indexes": indexes,
    }


def reset_graph_schema_cache() -> None:
    """Drop the cached schema. For tests and for callers that just changed it."""
    global _SCHEMA_CACHE
    _SCHEMA_CACHE = None


async def fetch_graph_schema() -> dict[str, Any]:
    """Introspect the graph: node labels, relationship types, property keys, indexes.

    Runs privileged catalog queries (incl. SHOW INDEXES) directly — the user query
    validator intentionally blocks these for ad-hoc user queries, so they are only
    reachable through this server-side path. Shared by the schema route and the
    graph__schema built-in tool.

    Cached process-wide for ``GRAPH_SCHEMA_CACHE_TTL_SECONDS``. Four catalog
    queries per call is cheap once and expensive in a loop: an agent exploring
    the graph re-introspects constantly (one observed chat step called this 73
    times, ~292 round trips, because each sandbox delegation starts a fresh
    subagent that knows nothing). The TTL, not invalidation, is what bounds
    staleness after a sync adds a label.

    **Why one cache is safe to share across users and sessions.** The result is
    graph-wide, not per-caller: this takes no user or database argument, and
    every query runs as the single service account in ``NEO4J_USER`` — so the
    schema returned is already identical for every caller, and the cache holds
    nothing one user could not have fetched itself. Authorization happens
    strictly above this: the ``/api/v1/graph/schema`` route and the
    ``graph__schema`` built-in each require ``query:execute`` before calling
    here (the sandbox subagent reaches it only through the built-in, with its
    delegating user's permissions), so a caller without the permission is
    refused whether or not a value is cached. The content is label,
    relationship-type, property-key and index *names* — no rows, no user data.

    That reasoning depends on the schema being user-independent. Introducing
    per-user Neo4j credentials, database-per-tenant routing, or label-level
    access control would each make it false, and the cache would then need the
    scoping dimension in its key rather than a single global slot.
    """
    ttl = max(0, settings.GRAPH_SCHEMA_CACHE_TTL_SECONDS)
    if not ttl:
        return await _fetch_graph_schema_uncached()

    global _SCHEMA_CACHE
    cached = _SCHEMA_CACHE
    now = time.monotonic()
    if cached is not None and now - cached[0] < ttl:
        return cached[1]

    # Single-flight: a cold cache under concurrent workers would otherwise fire
    # one introspection per caller, which is the stampede this exists to stop.
    async with _SCHEMA_CACHE_LOCK:
        cached = _SCHEMA_CACHE
        now = time.monotonic()
        if cached is not None and now - cached[0] < ttl:
            return cached[1]
        schema = await _fetch_graph_schema_uncached()
        _SCHEMA_CACHE = (time.monotonic(), schema)
        return schema


async def run_query_with_retry(cypher: str, parameters: dict = None) -> list[Record]:
    attempt = 1
    while True:
        try:
            return await run_query(cypher, parameters=parameters)
        except neo4j.exceptions.ServiceUnavailable:
            logger.debug("Unable to connect to neo4j, retrying...")
            if attempt >= 5:
                raise
        attempt = attempt + 1


async def run_query_bounded_with_retry(
    cypher: str,
    parameters: dict | None,
    *,
    max_rows: int,
) -> tuple[list[Record], bool]:
    """Stream at most ``max_rows`` records plus one truncation sentinel.

    Unlike :func:`run_query_with_retry`, this protects worker memory from a
    large result set. Closing the session after the sentinel discards the
    remaining records instead of materializing them in Python.
    """

    attempt = 1
    while True:
        try:
            records: list[Record] = []
            driver = _get_async_neo4j_client()
            async with driver.session() as session:
                result = await session.run(
                    Query(cypher, timeout=settings.NEO4J_QUERY_TIMEOUT),
                    parameters=parameters,
                )
                async for record in result:
                    records.append(record)
                    if len(records) > max_rows:
                        break
            return records[:max_rows], len(records) > max_rows
        except neo4j.exceptions.ServiceUnavailable:
            logger.debug("Unable to connect to neo4j, retrying...")
            if attempt >= 5:
                raise
            attempt += 1


async def run_query_streamed(
    cypher: str,
    parameters: dict | None,
    *,
    max_rows: int | None,
    max_bytes: int | None,
    serialize: Callable[[Record], Any],
) -> tuple[list[Any], bool]:
    """Stream, serializing each record, and stop at whichever bound comes first.

    Serializing inside the loop is what makes the byte bound real. Counting rows
    alone does not bound memory -- a hundred records of large maps, or a single
    ``collect()`` aggregate, blow any budget -- and applying a byte cap after
    materializing the result does not bound it either, because by then the whole
    thing is already in the process.

    A record that would not fit on its own is dropped rather than retained, so
    one enormous row cannot sit in memory alongside everything already read.
    That leaves the driver's own per-record allocation as the floor, which is
    not something this layer can do anything about.

    Returns the rows kept and whether anything was left behind.
    """
    attempt = 1
    while True:
        try:
            rows: list[Any] = []
            used = 0
            truncated = False
            driver = _get_async_neo4j_client()
            async with driver.session() as session:
                result = await session.run(
                    Query(cypher, timeout=settings.NEO4J_QUERY_TIMEOUT),
                    parameters=parameters,
                )
                async for record in result:
                    if max_rows is not None and max_rows > 0 and len(rows) >= max_rows:
                        truncated = True
                        break
                    row = serialize(record)
                    if max_bytes is not None and max_bytes > 0:
                        size = json_size_bytes(row)
                        if used + size > max_bytes:
                            # Closing the session here discards the rest rather
                            # than reading it into memory to throw away.
                            truncated = True
                            break
                        used += size
                    rows.append(row)
            return rows, truncated
        except neo4j.exceptions.ServiceUnavailable:
            logger.debug("Unable to connect to neo4j, retrying...")
            if attempt >= 5:
                raise
            attempt += 1


async def run_tx(tx: AsyncTransaction, cypher: str, parameters: dict = None) -> list[Record]:
    results = []
    # tx.run() takes no transaction timeout — for an explicit transaction the
    # timeout is fixed when it is begun (session.begin_transaction(timeout=...)),
    # so a caller wanting a bound must set it there. Passing `timeout=` here would
    # only add a stray query parameter, never an enforced timeout.
    query_results = await tx.run(cypher, parameters=parameters)
    async for result in query_results:
        results.append(result)
    return results


async def run_tx_with_retry(tx: AsyncTransaction, cypher: str, parameters: dict = None) -> list[Record]:
    attempt = 1
    while True:
        try:
            return await run_tx(tx, cypher, parameters=parameters)
        except neo4j.exceptions.ServiceUnavailable:
            logger.debug("Unable to connect to neo4j, retrying...")
            if attempt >= 5:
                raise
        attempt = attempt + 1


async def _scan_time(scan_type: ScheduledQueryWatchScan) -> int:
    query = """
    MATCH (s:SyncMetadata)
    WHERE s.grouptype =~ ($grouptype)
          AND s.syncedtype =~ ($syncedtype)
          AND toString(s.groupid) =~ ($groupid)
    RETURN max(s.lastupdated) AS maxlastupdated
    """
    results = await run_query_with_retry(
        query,
        {
            "grouptype": scan_type.grouptype,
            "syncedtype": scan_type.syncedtype,
            "groupid": scan_type.groupid,
        },
    )
    maxlastupdated = 0
    for result in results:
        if result["maxlastupdated"] is not None:
            maxlastupdated = result["maxlastupdated"]
    return maxlastupdated


async def check_watch_scan_triggered(
    last_scheduled_at: str | None,
    watch_scans: list[ScheduledQueryWatchScan],
) -> bool:
    """Return True if any watched SyncMetadata node was updated after last_scheduled_at.

    Converts *last_scheduled_at* (ISO string or None) to Unix seconds for
    comparison with Neo4j's ``lastupdated`` field, preserving the same unit
    semantics as the previous Neo4j-based locking implementation.
    """
    if last_scheduled_at is None:
        scheduled_unix = 0
    else:
        scheduled_unix = int(datetime.fromisoformat(last_scheduled_at).timestamp())

    for scan_type in watch_scans:
        scan_time = await _scan_time(scan_type)
        logger.debug(f"scan_type: {scan_type}, scan_time: {scan_time}, scheduled: {scheduled_unix}")
        if scan_time > scheduled_unix:
            return True
    return False
