"""The Temporal Schedule that makes the sweep singleton (SBX-011)."""

from contextlib import ExitStack
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from temporalio.client import ScheduleAlreadyRunningError, ScheduleOverlapPolicy

from reporting.services import session_reaper_schedule
from reporting.temporal_workflows.session_reap import SCHEDULE_ID, WORKFLOW_ID, WORKFLOW_TYPE


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "CHAT_ENABLED": True,
        "CHAT_SESSION_REAP_ENABLED": True,
        "CHAT_SESSION_REAP_IDLE_SECONDS": 2_592_000,
        "CHAT_SESSION_REAP_INTERVAL_SECONDS": 3_600,
        "TEMPORAL_TASK_QUEUE": "seizu-workflows",
    }
    values.update(overrides)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(patch(f"reporting.settings.{name}", value))
    return stack


def _client() -> MagicMock:
    client = MagicMock()
    client.create_schedule = AsyncMock()
    handle = MagicMock()
    handle.update = AsyncMock()
    handle.delete = AsyncMock()
    client.get_schedule_handle = MagicMock(return_value=handle)
    return client


async def test_the_schedule_has_one_fixed_id_and_skips_overlap() -> None:
    """Fixed ids are what make this singleton across worker replicas; SKIP is
    what keeps a slow sweep from stacking a second one behind it."""
    client = _client()
    with _settings():
        await session_reaper_schedule.reconcile(client)

    schedule_id, schedule = client.create_schedule.await_args.args
    assert schedule_id == SCHEDULE_ID
    assert schedule.action.id == WORKFLOW_ID
    assert schedule.action.workflow == WORKFLOW_TYPE
    assert schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
    assert schedule.spec.intervals[0].every == timedelta(seconds=3_600)


async def test_a_replica_that_finds_the_schedule_updates_it() -> None:
    """Every replica after the first lands here, and settings may have changed
    since the schedule was created."""
    client = _client()
    client.create_schedule = AsyncMock(side_effect=ScheduleAlreadyRunningError())
    with _settings():
        await session_reaper_schedule.reconcile(client)

    client.get_schedule_handle.return_value.update.assert_awaited_once()


async def test_turning_reaping_off_removes_the_schedule() -> None:
    """Left in place it would keep firing sweeps that immediately return."""
    client = _client()
    with _settings(CHAT_SESSION_REAP_ENABLED=False):
        await session_reaper_schedule.reconcile(client)

    client.create_schedule.assert_not_awaited()
    client.get_schedule_handle.return_value.delete.assert_awaited_once()


async def test_a_missing_schedule_is_not_an_error_when_disabled() -> None:
    """The ordinary case: reaping was never on, so there is nothing to delete."""
    client = _client()
    client.get_schedule_handle.return_value.delete = AsyncMock(side_effect=RuntimeError("not found"))
    with _settings(CHAT_SESSION_REAP_ENABLED=False):
        await session_reaper_schedule.reconcile(client)


async def test_an_unreachable_temporal_does_not_stop_the_worker() -> None:
    """Reconciling is startup work; a worker that cannot do it must still serve
    workflows, and the next restart reconciles again."""
    with _settings(), patch("reporting.services.schedule_reconciler.get_client", AsyncMock(side_effect=OSError())):
        await session_reaper_schedule.reconcile()
