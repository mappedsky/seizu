"""Retiring idle sessions and the sandboxes they hold (SBX-011).

Everything below drives the provider-agnostic layer and the store, so no E2B
package is involved.
"""

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from reporting.schema.chat import ChatSessionItem, IdleChatSession
from reporting.services import session_reaper
from reporting.services.sandbox_backend import SandboxSnapshot

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
IDLE_SECONDS = 2_592_000  # 30 days


def _settings(**overrides: Any) -> ExitStack:
    values: dict[str, Any] = {
        "CHAT_ENABLED": True,
        "CHAT_SESSION_REAP_ENABLED": True,
        "CHAT_SESSION_REAP_IDLE_SECONDS": IDLE_SECONDS,
        "SANDBOX_REAP_UNTAGGED": False,
        "SANDBOX_SESSION_TIMEOUT_SECONDS": 1_800,
        "SEIZU_DEPLOYMENT_ID": "prod",
    }
    values.update(overrides)
    stack = ExitStack()
    for name, value in values.items():
        stack.enter_context(patch(f"reporting.settings.{name}", value))
    return stack


def _age(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def _session(thread_id: str, *, idle_hours: float) -> ChatSessionItem:
    return ChatSessionItem(
        thread_id=thread_id,
        title="",
        created_at=_age(idle_hours + 1),
        updated_at=_age(idle_hours),
    )


def _snapshot(sandbox_id: str, *, thread: str = "", age_hours: float = 48.0, **overrides: Any) -> SandboxSnapshot:
    fields: dict[str, Any] = {
        "owner": "prod",
        "purpose": "chat-session",
        "thread": thread,
        "started_at": NOW - timedelta(hours=age_hours),
        "end_at": None,
    }
    fields.update(overrides)
    return SandboxSnapshot(sandbox_id=sandbox_id, **fields)


def _patch_store(
    *,
    idle: list[IdleChatSession] | None = None,
    sessions: dict[tuple[str, str], ChatSessionItem] | None = None,
    delete_session: Any = None,
    delete_state: Any = None,
) -> ExitStack:
    known = sessions or {}
    stack = ExitStack()
    stack.enter_context(
        patch(
            "reporting.services.report_store.list_idle_chat_sessions",
            AsyncMock(return_value=idle or []),
        )
    )
    stack.enter_context(
        patch(
            "reporting.services.report_store.get_chat_session",
            AsyncMock(side_effect=lambda user_id, thread_id: known.get((user_id, thread_id))),
        )
    )
    stack.enter_context(
        patch("reporting.services.report_store.delete_chat_session", delete_session or AsyncMock(return_value=True))
    )
    stack.enter_context(patch("reporting.services.chat_graph.delete_thread_state", delete_state or AsyncMock()))
    return stack


def _patch_provider(snapshots: list[SandboxSnapshot] | None = None, kill: Any = None, paused: bool = True) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(
        patch("reporting.services.session_reaper.list_paused_sandboxes", AsyncMock(return_value=snapshots or []))
    )
    stack.enter_context(patch("reporting.services.session_reaper.sandbox_is_paused", AsyncMock(return_value=paused)))
    stack.enter_context(patch("reporting.services.session_reaper.kill_sandbox", kill or AsyncMock()))
    return stack


# --------------------------------------------------------------------------
# Idle sessions
# --------------------------------------------------------------------------


async def test_an_idle_session_is_deleted_with_its_sandbox() -> None:
    """The point: the thread and the sandbox it holds go together, through the
    same path a user's own delete takes."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    delete_state = AsyncMock()
    delete_session = AsyncMock(return_value=True)
    with (
        _settings(),
        _patch_store(
            idle=idle,
            sessions={("u1", "t1"): _session("t1", idle_hours=24 * 40)},
            delete_session=delete_session,
            delete_state=delete_state,
        ),
        _patch_provider(),
    ):
        summary = await session_reaper.reap(now=NOW)

    delete_session.assert_awaited_once_with("u1", "t1")
    delete_state.assert_awaited_once_with("u1", "t1")
    assert summary.sessions_reaped == 1


async def test_a_session_replied_to_during_the_sweep_is_spared() -> None:
    """The listing is a snapshot. Without the re-read, a sweep working through a
    long backlog would delete a conversation that came back to life while it ran."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    delete_state = AsyncMock()
    with (
        _settings(),
        _patch_store(
            idle=idle,
            # Re-read shows a session touched a minute ago.
            sessions={("u1", "t1"): _session("t1", idle_hours=0.02)},
            delete_state=delete_state,
        ),
        _patch_provider(),
    ):
        summary = await session_reaper.reap(now=NOW)

    delete_state.assert_not_awaited()
    assert (summary.sessions_reaped, summary.sessions_kept) == (0, 1)


async def test_one_failed_deletion_does_not_stop_the_sweep() -> None:
    idle = [
        IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40)),
        IdleChatSession(user_id="u1", thread_id="t2", updated_at=_age(24 * 41)),
    ]
    with (
        _settings(),
        _patch_store(
            idle=idle,
            sessions={
                ("u1", "t1"): _session("t1", idle_hours=24 * 40),
                ("u1", "t2"): _session("t2", idle_hours=24 * 41),
            },
            delete_state=AsyncMock(side_effect=[RuntimeError("checkpoint store down"), None]),
        ),
        _patch_provider(),
    ):
        summary = await session_reaper.reap(now=NOW)

    assert (summary.sessions_reaped, summary.failed) == (1, 1)


async def test_a_session_that_vanished_before_deletion_is_not_an_error() -> None:
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    with _settings(), _patch_store(idle=idle, sessions={}), _patch_provider():
        summary = await session_reaper.reap(now=NOW)

    assert (summary.sessions_reaped, summary.failed) == (0, 0)


# --------------------------------------------------------------------------
# Orphan sandboxes
# --------------------------------------------------------------------------


async def test_a_sandbox_whose_session_is_gone_is_destroyed() -> None:
    """The case no checkpoint walk can find: the session record is already gone,
    so nothing but the provider's listing knows the sandbox exists."""
    kill = AsyncMock()
    snapshot = _snapshot("sbx-orphan", thread="user:u1:thread:t9")
    with _settings(), _patch_store(sessions={}), _patch_provider([snapshot], kill):
        summary = await session_reaper.reap(now=NOW)

    kill.assert_awaited_once_with("sbx-orphan")
    assert summary.orphans_reaped == 1


async def test_a_sandbox_with_a_live_session_is_left_alone() -> None:
    """Even an ancient one: the sandbox belongs to the thread for as long as the
    thread exists, and reaping it alone would empty a conversation's disk."""
    kill = AsyncMock()
    snapshot = _snapshot("sbx-live", thread="user:u1:thread:t1", age_hours=24 * 90)
    with (
        _settings(),
        _patch_store(sessions={("u1", "t1"): _session("t1", idle_hours=1)}),
        _patch_provider([snapshot], kill),
    ):
        summary = await session_reaper.reap(now=NOW)

    kill.assert_not_awaited()
    assert summary.orphans_reaped == 0


async def test_a_freshly_created_sandbox_is_not_mistaken_for_an_orphan() -> None:
    """A session record is written by a different call than the sandbox, so a
    sandbox seconds old can genuinely have no session yet."""
    kill = AsyncMock()
    with _settings(), _patch_store(sessions={}), _patch_provider([_snapshot("sbx-new", age_hours=0.1)], kill):
        await session_reaper.reap(now=NOW)

    kill.assert_not_awaited()


async def test_a_sandbox_resumed_since_the_listing_is_not_killed() -> None:
    """The listing is minutes old; a sandbox someone resumed in between is a
    live delegation, not reclaimable storage."""
    kill = AsyncMock()
    with (
        _settings(),
        _patch_store(sessions={}),
        _patch_provider([_snapshot("sbx-resumed", thread="user:u1:thread:t9")], kill, paused=False),
    ):
        await session_reaper.reap(now=NOW)

    kill.assert_not_awaited()


async def test_another_deployments_sandbox_is_never_touched() -> None:
    """Shared credentials: the tag names the deployment, and only a match is an
    ownership claim. "Some Seizu made it" is not one."""
    kill = AsyncMock()
    sibling = _snapshot("sbx-staging", owner="staging")
    with _settings(), _patch_store(sessions={}), _patch_provider([sibling], kill):
        summary = await session_reaper.reap(now=NOW)

    kill.assert_not_awaited()
    assert summary.orphans_reaped == 0


async def test_untagged_sandboxes_are_reaped_when_the_credentials_are_ours_alone() -> None:
    """How sandboxes created before tagging existed get cleaned up."""
    kill = AsyncMock()
    legacy = _snapshot("sbx-legacy", owner="")
    with _settings(SANDBOX_REAP_UNTAGGED=True), _patch_store(sessions={}), _patch_provider([legacy], kill):
        summary = await session_reaper.reap(now=NOW)

    kill.assert_awaited_once_with("sbx-legacy")
    assert summary.orphans_reaped == 1


async def test_a_store_that_cannot_answer_keeps_the_sandbox() -> None:
    """Failing to prove a sandbox is abandoned is not evidence that it is."""
    kill = AsyncMock()
    with _settings(), ExitStack() as stack:
        stack.enter_context(
            patch(
                "reporting.services.report_store.get_chat_session",
                AsyncMock(side_effect=RuntimeError("store down")),
            )
        )
        stack.enter_context(
            patch("reporting.services.report_store.list_idle_chat_sessions", AsyncMock(return_value=[]))
        )
        stack.enter_context(_patch_provider([_snapshot("sbx-1", thread="user:u1:thread:t1")], kill))
        await session_reaper.reap(now=NOW)

    kill.assert_not_awaited()


async def test_an_unparseable_thread_tag_counts_as_no_session() -> None:
    """A tag that cannot be split names nobody, and a sandbox whose owner cannot
    be named must not be attributed to whoever the fragments resemble."""
    kill = AsyncMock()
    with _settings(), _patch_store(sessions={}), _patch_provider([_snapshot("sbx-odd", thread="nonsense")], kill):
        await session_reaper.reap(now=NOW)

    kill.assert_awaited_once_with("sbx-odd")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


async def test_reaping_off_touches_neither_the_store_nor_the_provider() -> None:
    listing = AsyncMock(return_value=[])
    with _settings(CHAT_SESSION_REAP_ENABLED=False), _patch_store(), ExitStack() as stack:
        stack.enter_context(patch("reporting.services.session_reaper.list_paused_sandboxes", listing))
        summary = await session_reaper.reap(now=NOW)

    listing.assert_not_awaited()
    assert summary == session_reaper.ReapSummary()


async def test_a_zero_threshold_disables_reaping_rather_than_deleting_everything() -> None:
    """Read as "retire immediately", it would delete every chat session there is."""
    with _settings(CHAT_SESSION_REAP_IDLE_SECONDS=0):
        assert not session_reaper.reaping_configured()


async def test_reaping_is_off_when_chat_is() -> None:
    with _settings(CHAT_ENABLED=False):
        assert not session_reaper.reaping_configured()
