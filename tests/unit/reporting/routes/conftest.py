"""Fixtures for the route unit tests."""

import pytest

from reporting.schema.report_config import QueryHistoryItem
from reporting.services import report_store


@pytest.fixture(autouse=True)
def _stub_save_query_history(mocker):
    """Keep the ad-hoc query route's history write away from a real store.

    ``/query/adhoc`` records history on every successful run, inside an
    ``except Exception`` that logs and leaves ``history_id`` null. So a test
    asserting only on results passed either way, while quietly opening a
    connection to whatever backend was configured.

    A real ``QueryHistoryItem`` rather than a bare mock, so the route's response
    serializes the way it does in production. Tests that assert on a specific
    ``history_id`` patch this themselves and their patch wins.
    """
    mocker.patch.object(
        report_store,
        "save_query_history",
        return_value=QueryHistoryItem(
            history_id="stub-history-id",
            user_id="user-1",
            query="stubbed",
            executed_at="2024-01-01T00:00:00+00:00",
        ),
    )
