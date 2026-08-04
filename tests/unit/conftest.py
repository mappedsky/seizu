"""Shared fixtures for the backend unit suite."""

import os
import socket

import pytest

# litellm downloads its model-cost map on first use unless told to read the copy
# it ships with. That fetch was one of the recorded network attempts, landing on
# whichever test imported litellm first -- so it moved around as the suite
# changed. Set before any test module imports litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from reporting.services import reporting_neo4j  # noqa: E402


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


class NetworkAccessAttempted(BaseException):
    """Raised when a unit test opens a socket. Deliberately not an ``Exception``.

    This is the whole reason the guard can fail hard. Every leak found so far
    sat inside an ``except Exception`` handler -- the MCP tool listing, the
    query-history write, the LLM token count -- so an ``AssertionError`` was
    caught by the code under test, the connection was merely refused, and the
    test still passed. Only recording it made it visible.

    Inheriting from ``BaseException`` walks straight out through those handlers,
    the way ``KeyboardInterrupt`` does, and fails the test that leaked.
    """


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Fail a unit test that opens a network connection.

    Three times in recent work a rename severed a mock and a "unit" test went on
    talking to the real thing -- an LLM in one case, Neo4j in another -- while
    still passing, because the leaking call sat inside an ``except Exception``.
    Each was noticed only when something unrelated broke.

    A socket guard catches all of it at once: bolt, asyncpg, aiobotocore, httpx,
    the sandbox provider. Blocking every socket was measured against the whole
    suite and changed nothing, so this needs no exemptions -- a test that trips
    it is leaking, not legitimately networked. Mark a test ``allow_network`` if
    it ever genuinely needs one.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    real_connect = socket.socket.connect

    def _blocked(self, address, *args, **kwargs):
        # Unix sockets are local IPC, not the network this guards against.
        if self.family == getattr(socket, "AF_UNIX", object()):
            return real_connect(self, address, *args, **kwargs)
        raise NetworkAccessAttempted(
            f"unit test opened a network connection to {address!r}. "
            "Something it should mock is reaching a real dependency -- often a patch "
            "whose target moved. Mock it, or mark the test allow_network."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
