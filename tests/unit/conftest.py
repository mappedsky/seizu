"""Shared fixtures for the backend unit suite."""

import pytest

from reporting.services import reporting_neo4j


@pytest.fixture(autouse=True)
def _reset_graph_schema_cache():
    """Keep the process-wide graph schema cache from leaking between tests.

    ``fetch_graph_schema`` caches for ``GRAPH_SCHEMA_CACHE_TTL_SECONDS``, and the
    whole suite shares one process. Without this, the first test to fetch a
    schema serves its value to every later test that mocks a *different* one —
    and the failure surfaces in the innocent test, not the one that populated
    the cache.
    """
    reporting_neo4j.reset_graph_schema_cache()
    yield
    reporting_neo4j.reset_graph_schema_cache()
