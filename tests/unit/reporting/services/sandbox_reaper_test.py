"""The sweep that reclaims suspended sandboxes nobody comes back for (SBX-011).

Every test drives the provider-agnostic layer: ``list_paused_sandboxes`` and
``kill_sandbox`` are patched, so nothing here needs E2B.
"""

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from reporting.services import sandbox_reaper
from reporting.services.sandbox_backend import SandboxSnapshot

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "SANDBOX_API_KEY": "k",
        "SANDBOX_DOMAIN": "",
        "SANDBOX_REAP_ENABLED": True,
        "SANDBOX_REAP_IDLE_SECONDS": 86_400,
        "SANDBOX_REAP_UNTAGGED": False,
    }
    values.update(overrides)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(patch(f"reporting.settings.{name}", value))
    return stack


def _snapshot(sandbox_id: str, *, age_hours: float = 48.0, **overrides: Any) -> SandboxSnapshot:
    fields: dict[str, Any] = {
        "managed": True,
        "purpose": "chat-session",
        "started_at": NOW - timedelta(hours=age_hours),
        "end_at": None,
    }
    fields.update(overrides)
    return SandboxSnapshot(sandbox_id=sandbox_id, **fields)


def _patch_provider(snapshots: list[SandboxSnapshot], kill: Any = None) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("reporting.services.sandbox_reaper.list_paused_sandboxes", AsyncMock(return_value=snapshots))
    )
    stack.enter_context(patch("reporting.services.sandbox_reaper.kill_sandbox", kill or AsyncMock()))
    return stack


async def test_a_sandbox_idle_past_the_threshold_is_destroyed() -> None:
    """The whole point: an abandoned thread's sandbox stops costing storage."""
    kill = AsyncMock()
    with _settings(), _patch_provider([_snapshot("sbx-old", age_hours=48)], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_awaited_once_with("sbx-old")
    assert (summary.listed, summary.reaped, summary.failed) == (1, 1, 0)


async def test_a_recently_used_sandbox_is_left_alone() -> None:
    """A conversation between turns must find its sandbox where it left it."""
    kill = AsyncMock()
    with _settings(), _patch_provider([_snapshot("sbx-fresh", age_hours=1)], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_not_awaited()
    assert summary.reaped == 0


async def test_a_future_expiry_does_not_make_a_sandbox_immortal() -> None:
    """``end_at`` can carry the expiry the sandbox *would* have had while
    running, which says nothing about when it was last used. Taking the latest
    timestamp at face value would leave such a sandbox permanently unreapable."""
    kill = AsyncMock()
    stale = _snapshot("sbx-old", age_hours=48, end_at=NOW + timedelta(hours=1))
    with _settings(), _patch_provider([stale], kill):
        await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_awaited_once_with("sbx-old")


async def test_the_most_recent_timestamp_wins() -> None:
    """Idle time is measured from whichever timestamp the provider refreshed.

    Which of them a resume advances is the provider's business, so a sandbox is
    only reaped when *every* datable signal is old.
    """
    kill = AsyncMock()
    resumed = _snapshot("sbx-resumed", age_hours=48, end_at=NOW - timedelta(hours=2))
    with _settings(), _patch_provider([resumed], kill):
        await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_not_awaited()


async def test_an_undatable_sandbox_is_never_reaped() -> None:
    """No timestamp means no evidence of abandonment, not licence to kill."""
    kill = AsyncMock()
    undatable = _snapshot("sbx-unknown", started_at=None, end_at=None)
    with _settings(), _patch_provider([undatable], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_not_awaited()
    assert summary.reaped == 0


async def test_sandboxes_seizu_did_not_create_are_skipped() -> None:
    """The listing is account-wide. Killing another deployment's sandbox -- or a
    person's own -- is worse than leaking one of ours."""
    kill = AsyncMock()
    foreign = _snapshot("sbx-foreign", managed=False, purpose="")
    with _settings(), _patch_provider([foreign, _snapshot("sbx-ours")], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_awaited_once_with("sbx-ours")
    assert summary.skipped_foreign == 1


async def test_untagged_sandboxes_are_reaped_when_the_credentials_are_ours_alone() -> None:
    """How sandboxes created before tagging existed get cleaned up."""
    kill = AsyncMock()
    foreign = _snapshot("sbx-untagged", managed=False, purpose="")
    with _settings(SANDBOX_REAP_UNTAGGED=True), _patch_provider([foreign], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_awaited_once_with("sbx-untagged")
    assert summary.skipped_foreign == 0


async def test_one_failed_kill_does_not_stop_the_sweep() -> None:
    """A pass that gave up on the first error would leave the rest of the
    account uncollected until the next interval, every interval."""
    kill = AsyncMock(side_effect=[RuntimeError("provider down"), None])
    with _settings(), _patch_provider([_snapshot("sbx-a"), _snapshot("sbx-b")], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    assert (summary.reaped, summary.failed) == (1, 1)


async def test_a_listing_failure_is_not_raised_at_the_loop() -> None:
    """The caller is a periodic loop; the next pass sees the same sandboxes."""
    with _settings(), ExitStack() as stack:
        stack.enter_context(
            patch(
                "reporting.services.sandbox_reaper.list_paused_sandboxes",
                AsyncMock(side_effect=RuntimeError("provider down")),
            )
        )
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    assert summary == sandbox_reaper.ReapSummary()


async def test_reaping_off_lists_nothing() -> None:
    """Disabled means no provider calls at all, not calls whose result is dropped."""
    listing = AsyncMock(return_value=[])
    with _settings(SANDBOX_REAP_ENABLED=False):
        with patch("reporting.services.sandbox_reaper.list_paused_sandboxes", listing):
            await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    listing.assert_not_awaited()


async def test_reaping_survives_the_sandbox_feature_being_turned_off() -> None:
    """Turning delegation off is when leftovers most need collecting, so the
    sweep is gated on credentials rather than on SANDBOX_ENABLED."""
    kill = AsyncMock()
    with _settings(), patch("reporting.settings.SANDBOX_ENABLED", False):
        with _patch_provider([_snapshot("sbx-old")], kill):
            await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    kill.assert_awaited_once_with("sbx-old")


async def test_no_credentials_means_no_sweep() -> None:
    """Nothing to talk to the provider with; every pass would just error."""
    listing = AsyncMock(return_value=[])
    with _settings(SANDBOX_API_KEY="", SANDBOX_DOMAIN=""):
        with patch("reporting.services.sandbox_reaper.list_paused_sandboxes", listing):
            await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)

    listing.assert_not_awaited()


async def test_a_zero_threshold_disables_reaping_rather_than_reaping_everything() -> None:
    """Read as "immediately", it would destroy the sandbox the turn running
    right now is about to resume."""
    kill = AsyncMock()
    with _settings(SANDBOX_REAP_IDLE_SECONDS=0), _patch_provider([_snapshot("sbx-old")], kill):
        summary = await sandbox_reaper.reap_abandoned_sandboxes(now=NOW)
        assert not sandbox_reaper.reaping_configured()

    kill.assert_not_awaited()
    assert summary == sandbox_reaper.ReapSummary()
