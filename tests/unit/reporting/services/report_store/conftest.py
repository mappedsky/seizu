"""Fixtures for the report-store unit tests."""

import pytest


@pytest.fixture(autouse=True)
def _stub_list_enabled_tools():
    """Undo the parent stub: these are the tests *of* ``list_enabled_tools``.

    ``tests/unit/reporting/services/conftest.py`` stubs it so MCP tests stop
    reaching a real store. Three files here exercise the real implementation
    across both backends, and the parent stub would replace the thing under
    test with an empty list -- the assertions would still pass, against nothing.

    Overriding by name is how pytest scopes a parent autouse fixture: the
    closest conftest wins. Deleting this file silently weakens the store suite
    rather than breaking it, so it stays even though its body is empty.
    """
    return None
