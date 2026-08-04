"""Fixtures for the service-layer unit tests."""

import pytest

from reporting.services import report_store


@pytest.fixture(autouse=True)
def _stub_list_enabled_tools(mocker):
    """Keep MCP tool listing away from a real report store.

    ``list_tools_for_user`` loads user-defined tools on every listing, and the
    MCP SDK lists tools on the way to dispatching a call -- so every test that
    calls *any* tool reaches the store, whether or not it cares about tools.
    That was 239 of the 247 recorded network attempts, all through this one
    line. The store call sits inside ``except Exception``, so the failure was
    swallowed and the listing quietly degraded to builtins-only.

    Returning an empty list is the same state those tests were already getting,
    minus the connection attempt. A test that cares about user-defined tools
    patches this itself and its patch wins, which is why the four listing tests
    never appeared in the leak set.
    """
    mocker.patch.object(report_store, "list_enabled_tools", return_value=[])
