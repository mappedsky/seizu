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
        "CHAT_SESSION_REAP_ENABLED": True,
        "CHAT_SESSION_REAP_INTERVAL_SECONDS": 3_600,
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
    claim: Any = None,
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
            "reporting.services.report_store.claim_chat_session_for_retirement",
            claim if claim is not None else AsyncMock(return_value=True),
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


async def test_a_session_used_since_it_was_listed_is_spared() -> None:
    """The listing is a snapshot, and the claim is what settles the race: a
    conditional write on updated_at, so a conversation the owner came back to
    cannot be deleted by a sweep that read it a moment earlier."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    delete_state = AsyncMock()
    with (
        _settings(),
        _patch_store(idle=idle, claim=AsyncMock(return_value=False), delete_state=delete_state),
        _patch_provider(),
    ):
        summary = await session_reaper.reap(now=NOW)

    delete_state.assert_not_awaited()
    assert (summary.sessions_reaped, summary.sessions_kept) == (0, 1)


async def test_the_claim_is_conditioned_on_the_timestamp_that_was_listed() -> None:
    """Anything else -- a fresh read, no condition at all -- reopens the window
    between deciding to delete and deleting."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    claim = AsyncMock(return_value=True)
    with _settings(), _patch_store(idle=idle, claim=claim), _patch_provider():
        await session_reaper.reap(now=NOW)

    claim.assert_awaited_once_with("u1", "t1", _age(24 * 40))


async def test_the_session_record_is_deleted_last() -> None:
    """The record is the only thing that makes a thread findable, so it is its
    own tombstone: delete it first and a failed checkpoint deletion strands a
    transcript with nothing left to retry from."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    order: list[str] = []
    delete_state = AsyncMock(side_effect=lambda *_a: order.append("state"))
    delete_session = AsyncMock(side_effect=lambda *_a: order.append("record"))
    with (
        _settings(),
        _patch_store(idle=idle, delete_state=delete_state, delete_session=delete_session),
        _patch_provider(),
    ):
        await session_reaper.reap(now=NOW)

    assert order == ["state", "record"]


async def test_a_half_finished_retirement_leaves_the_session_findable() -> None:
    """A checkpoint deletion that fails must not take the record with it, or the
    next sweep has nothing to resume from."""
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    delete_session = AsyncMock()
    with (
        _settings(),
        _patch_store(
            idle=idle,
            delete_state=AsyncMock(side_effect=RuntimeError("checkpoint store down")),
            delete_session=delete_session,
        ),
        _patch_provider(),
    ):
        summary = await session_reaper.reap(now=NOW)

    delete_session.assert_not_awaited()
    assert (summary.sessions_reaped, summary.failed) == (0, 1)


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


async def test_a_session_that_vanished_before_the_claim_is_not_an_error() -> None:
    idle = [IdleChatSession(user_id="u1", thread_id="t1", updated_at=_age(24 * 40))]
    with _settings(), _patch_store(idle=idle, claim=AsyncMock(return_value=False)), _patch_provider():
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


async def test_reaping_is_off_until_an_operator_turns_it_on() -> None:
    """The default is a safety property, not a preference: an upgrade must never
    start deleting transcripts on its own. Retention is the operator's policy.

    Asserted by reloading the settings module with the variable removed from the
    environment, rather than by reading the attribute -- a developer's own .env
    would otherwise decide whether this passes.
    """
    import importlib
    import os

    from reporting import settings as settings_module

    environment = {k: v for k, v in os.environ.items() if k != "CHAT_SESSION_REAP_ENABLED"}
    with patch.dict(os.environ, environment, clear=True):
        reloaded = importlib.reload(settings_module)
        try:
            assert reloaded.CHAT_SESSION_REAP_ENABLED is False
        finally:
            importlib.reload(settings_module)


async def test_reaping_survives_chat_being_turned_off() -> None:
    """A deployment that disables chat still holds every session and sandbox it
    made while chat was on -- and the flag is not even passed to some services,
    which is how the gate silently disabled the sweep everywhere."""
    with _settings(), patch("reporting.settings.CHAT_ENABLED", False):
        assert session_reaper.reaping_configured()


async def test_a_slow_rotation_against_a_short_threshold_warns(caplog) -> None:
    """Each session's effective retention is its idle window plus one rotation;
    silence would leave "sessions live longer than I set" unexplained."""
    with _settings(CHAT_SESSION_REAP_INTERVAL_SECONDS=21_600, CHAT_SESSION_REAP_IDLE_SECONDS=172_800):
        session_reaper.warn_if_coverage_is_too_slow()

    assert "visit every user" in caplog.text


async def test_the_default_cadence_does_not_warn(caplog) -> None:
    with _settings():
        session_reaper.warn_if_coverage_is_too_slow()

    assert "visit every user" not in caplog.text
