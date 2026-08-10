"""Retire chat sessions nobody has come back to, and the sandboxes they hold.

A chat thread's sandbox is destroyed when the thread is deleted. A thread the
user simply stops replying to is never deleted, so its sandbox stays suspended
-- holding a full memory snapshot (SBX-005) -- indefinitely.

**The session is the unit, not the sandbox.** A sandbox belongs to a thread for
as long as that thread exists, so retiring one without the other would leave a
conversation whose accumulated files silently vanished. So this reaps *sessions*
past an idle threshold, and the sandbox goes with the session, through the same
`delete_thread_state` path a user's own delete takes.

Two passes, because the two failure modes are different:

- **Idle sessions** (:func:`reap_idle_sessions`) — driven by Seizu's own store,
  where ``updated_at`` is authoritative last activity. No provider timestamps,
  no inference.
- **Orphan sandboxes** (:func:`reap_orphan_sandboxes`) — suspended sandboxes
  whose thread has no session: a deleted thread whose kill failed, a run that
  died before its session record was written, a database restored from a
  backup. Nothing but the provider's own listing can find these, which is why
  the sweep looks there at all.

Rationale, and the alternatives rejected: **SBX-011** in
``docs/root/dev/decisions/sandbox.md``.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from reporting import settings
from reporting.services import report_store
from reporting.services.sandbox_backend import (
    SandboxSnapshot,
    kill_sandbox,
    list_paused_sandboxes,
    sandbox_is_paused,
)

logger = logging.getLogger(__name__)

#: Ceiling on sessions retired per pass, so one sweep cannot spend an unbounded
#: time deleting and the next pass simply picks up where this one stopped.
_MAX_SESSIONS_PER_PASS = 500


@dataclass
class ReapSummary:
    """What one sweep saw and did. Returned for logging and for tests."""

    sessions_seen: int = 0
    sessions_reaped: int = 0
    sessions_kept: int = 0
    sandboxes_seen: int = 0
    orphans_reaped: int = 0
    failed: int = 0


def reaping_configured() -> bool:
    """Whether a sweep can run at all, so the schedule need not exist.

    Deliberately *not* gated on ``SANDBOX_ENABLED``: a deployment that turns
    delegation off still has the sessions and sandboxes it created while it was
    on, and that is the moment they most need collecting.

    A threshold of zero reads as **off**, not as "retire everything now".
    """
    if not settings.CHAT_SESSION_REAP_ENABLED or settings.CHAT_SESSION_REAP_IDLE_SECONDS <= 0:
        return False
    return bool(settings.CHAT_ENABLED)


async def reap(*, now: datetime | None = None) -> ReapSummary:
    """Run both passes. Best effort: a failing pass is logged, never raised."""
    summary = ReapSummary()
    if not reaping_configured():
        return summary
    now = now or datetime.now(UTC)
    await reap_idle_sessions(summary, now=now)
    await reap_orphan_sandboxes(summary, now=now)
    if summary.sessions_reaped or summary.orphans_reaped or summary.failed:
        logger.info(
            "Session reap pass complete",
            extra={
                "sessions_seen": summary.sessions_seen,
                "sessions_reaped": summary.sessions_reaped,
                "sandboxes_seen": summary.sandboxes_seen,
                "orphans_reaped": summary.orphans_reaped,
                "failed": summary.failed,
            },
        )
    return summary


async def reap_idle_sessions(summary: ReapSummary, *, now: datetime) -> None:
    """Delete sessions untouched for ``CHAT_SESSION_REAP_IDLE_SECONDS``."""
    cutoff = (now - timedelta(seconds=settings.CHAT_SESSION_REAP_IDLE_SECONDS)).isoformat()
    try:
        idle = await report_store.list_idle_chat_sessions(cutoff, _MAX_SESSIONS_PER_PASS)
    except Exception:
        logger.warning("could not list idle chat sessions; skipping this pass", exc_info=True)
        return
    summary.sessions_seen = len(idle)
    for session in idle:
        # Re-read immediately before deleting. The listing is a snapshot, and a
        # session the owner replied to while this pass was working through the
        # ones before it is no longer idle -- this is the check that keeps a
        # sweep from deleting a conversation that came back to life during it.
        try:
            current = await report_store.get_chat_session(session.user_id, session.thread_id)
        except Exception:
            summary.failed += 1
            logger.warning("could not re-read chat session before reaping it", exc_info=True)
            continue
        if current is None:
            # Already gone; its sandbox, if any, is the orphan pass's problem.
            continue
        if current.updated_at >= cutoff:
            summary.sessions_kept += 1
            continue
        try:
            await _delete_session(session.user_id, session.thread_id)
        except Exception:
            summary.failed += 1
            logger.warning(
                "could not reap idle chat session",
                extra={"thread_id": session.thread_id},
                exc_info=True,
            )
            continue
        summary.sessions_reaped += 1
        logger.info(
            "Reaped idle chat session",
            extra={"thread_id": session.thread_id, "updated_at": current.updated_at},
        )


async def _delete_session(user_id: str, thread_id: str) -> None:
    """Delete the record, then the checkpoint and the sandbox with it.

    Same order as the user-facing delete route: the record first, so a failure
    part-way through leaves a session that is gone from the UI and a checkpoint
    the next pass will collect, rather than a session pointing at state that no
    longer exists.
    """
    from reporting.services.chat_graph import delete_thread_state

    await report_store.delete_chat_session(user_id, thread_id)
    await delete_thread_state(user_id, thread_id)


async def reap_orphan_sandboxes(summary: ReapSummary, *, now: datetime) -> None:
    """Destroy suspended sandboxes whose session no longer exists."""
    try:
        snapshots = await list_paused_sandboxes(all_owners=settings.SANDBOX_REAP_UNTAGGED)
    except Exception:
        logger.warning("could not list sandboxes; skipping the orphan pass", exc_info=True)
        return
    summary.sandboxes_seen = len(snapshots)
    for snapshot in snapshots:
        if not snapshot.sandbox_id:
            continue
        if not snapshot.ours and not settings.SANDBOX_REAP_UNTAGGED:
            # Another deployment's sandbox on shared credentials. Killing
            # someone else's is worse than leaking one of ours.
            continue
        if not _is_old_enough(snapshot, now):
            continue
        if await _has_live_session(snapshot):
            continue
        # Last look before the kill: the listing may be minutes old, and a
        # sandbox someone resumed in between is a live delegation, not storage.
        if not await sandbox_is_paused(snapshot.sandbox_id):
            continue
        try:
            await kill_sandbox(snapshot.sandbox_id)
        except Exception:
            summary.failed += 1
            logger.warning("could not reap orphan sandbox %s", snapshot.sandbox_id, exc_info=True)
            continue
        summary.orphans_reaped += 1
        logger.info(
            "Reaped orphan sandbox",
            extra={
                "sandbox_id": snapshot.sandbox_id,
                "purpose": snapshot.purpose or "unknown",
                # Not "thread": logging reserves that attribute on every record
                # and raises rather than letting an extra shadow it.
                "chat_thread": snapshot.thread or "none",
            },
        )


def _is_old_enough(snapshot: SandboxSnapshot, now: datetime) -> bool:
    """Whether a sandbox has existed long enough to be judged an orphan.

    A session record is written by a separate call from the sandbox that serves
    it, so a sandbox seconds old can genuinely have no session yet. Waiting out
    ``SANDBOX_SESSION_TIMEOUT_SECONDS`` -- the longest a sandbox can be in use
    by the turn that made it -- means anything still unclaimed was never going
    to be claimed. A sandbox with no usable timestamp is left alone entirely.
    """
    seen = [t for t in (snapshot.started_at, snapshot.end_at) if t is not None and t <= now]
    if not seen:
        return False
    return (now - max(seen)).total_seconds() >= settings.SANDBOX_SESSION_TIMEOUT_SECONDS


async def _has_live_session(snapshot: SandboxSnapshot) -> bool:
    """Whether a session still owns this sandbox.

    An untagged sandbox (created before thread tagging, or by something that is
    not a chat turn) can never be matched to a session, so it counts as an
    orphan -- which is correct: nothing but a chat thread ever leaves one
    suspended.

    A store that cannot answer counts as *live*. Being unable to prove a
    sandbox is abandoned is not evidence that it is.
    """
    user_id, thread_id = _split_thread(snapshot.thread)
    if not user_id or not thread_id:
        return False
    try:
        return await report_store.get_chat_session(user_id, thread_id) is not None
    except Exception:
        logger.warning("could not resolve the session for sandbox %s; leaving it", snapshot.sandbox_id, exc_info=True)
        return True


def _split_thread(thread: str) -> tuple[str, str]:
    """Split ``user:<user_id>:thread:<thread_id>`` back into its two ids.

    The inverse of ``chat_graph.thread_namespace``. Kept tolerant: a tag that
    does not parse yields no ids, and a sandbox whose owner cannot be named is
    treated as unowned rather than as belonging to whoever the fragments
    resemble.
    """
    if not thread.startswith("user:"):
        return "", ""
    remainder = thread[len("user:") :]
    user_id, separator, thread_id = remainder.partition(":thread:")
    if not separator or not user_id or not thread_id:
        return "", ""
    return user_id, thread_id
