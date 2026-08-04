"""Shared fixtures for the backend unit suite."""

import socket

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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch, request):
    """Fail a unit test that opens a network connection.

    Three times in recent work a rename severed a mock and a "unit" test went on
    talking to the real thing -- an LLM in one case, Neo4j in another -- while
    still passing, because the leaking call sat inside an ``except Exception``.
    Each was noticed only when something unrelated broke.

    A socket guard catches all of it at once: bolt, asyncpg, aiobotocore, httpx,
    the sandbox provider. Blocking every socket was measured against the whole
    suite and changed nothing (2567 passed either way), so this needs no
    exemptions -- a test that trips it is leaking, not legitimately networked.
    Mark a test ``allow_network`` if it ever genuinely needs one.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    real_connect = socket.socket.connect

    def _blocked(self, address, *args, **kwargs):
        # Unix sockets are local IPC, not the network this guards against.
        if self.family == getattr(socket, "AF_UNIX", object()):
            return real_connect(self, address, *args, **kwargs)
        _ATTEMPTED_CONNECTIONS.setdefault(request.node.nodeid, set()).add(str(address))
        raise AssertionError(
            f"unit test opened a network connection to {address!r}. "
            "Something it should mock is reaching a real dependency -- often a patch "
            "whose target moved. Mock it, or mark the test allow_network."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


# Blocking alone is not enough to surface a leak. The calls that leak sit inside
# ``except Exception`` handlers, and AssertionError is an Exception -- so the
# connection is refused, the handler swallows it, and the test still passes.
# Recording them makes the leak visible without failing a suite that has a
# backlog of them; the goal is to turn this into a hard failure once that
# backlog is cleared.
_ATTEMPTED_CONNECTIONS: dict[str, set[str]] = {}


def pytest_terminal_summary(terminalreporter):
    if not _ATTEMPTED_CONNECTIONS:
        return
    terminalreporter.write_sep("=", "unit tests that reached for the network")
    for nodeid, addresses in sorted(_ATTEMPTED_CONNECTIONS.items()):
        terminalreporter.write_line(f"  {nodeid} -> {', '.join(sorted(addresses))}")
    terminalreporter.write_line(
        f"{len(_ATTEMPTED_CONNECTIONS)} test(s) attempted a real connection. Each is a mock that is not "
        "covering what the code actually calls; the connection was refused, so the test result is unaffected."
    )
