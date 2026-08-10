"""Reclaim suspended sandboxes nobody is coming back for.

A chat thread's sandbox is destroyed when the thread is deleted. A thread the
user simply stops replying to is never deleted, so its sandbox stays suspended
-- holding a full memory snapshot (SBX-005) -- until the provider's own
retention reclaims it, if it has any. Deployments with many chat users
accumulate those continuously.

This is a sweep over the *provider's* listing rather than over Seizu's
checkpoints, because the sandboxes that matter most are exactly the ones no
checkpoint points at any more: a thread whose checkpoint was trimmed, a run that
died between creating a sandbox and storing its id, a deployment restored from a
backup. Anything derived from Seizu's own records can only find sandboxes Seizu
still remembers.

**Idle time is inferred from the provider's timestamps, never assumed.** The
provider offers no way to amend a sandbox's metadata after creation, so there is
nowhere to stamp a last-used time on resume. What a listing does give is
``started_at`` and ``end_at``, and whether either advances when a paused sandbox
is resumed is the provider's business, not something Seizu can pin. So the sweep
takes the *latest* timestamp that is not in the future and treats it as the last
thing known to have happened: if resuming refreshes one of them this is a true
idle reap, and if it refreshes neither it degrades to a maximum-age reap. Both
are correct; only the first is precise. The failure mode of the degraded case is
bounded -- a live conversation loses accumulated files and the next turn opens a
fresh sandbox, which is exactly what a resume failure already does.

Rationale and the alternatives that were rejected: **SBX-011** in
``docs/root/dev/decisions/sandbox.md``.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from reporting import settings
from reporting.services.sandbox_backend import SandboxSnapshot, kill_sandbox, list_paused_sandboxes

logger = logging.getLogger(__name__)


@dataclass
class ReapSummary:
    """What one sweep saw and did. Returned for logging and for tests."""

    listed: int = 0
    reaped: int = 0
    failed: int = 0
    #: Suspended sandboxes on the same credentials that Seizu did not create
    #: (or created before it started tagging them). Left alone unless
    #: ``SANDBOX_REAP_UNTAGGED`` says the credentials are Seizu's alone.
    skipped_foreign: int = 0


def reaping_configured() -> bool:
    """Whether a sweep can run at all, so the loop hosting it need not start.

    Deliberately *not* gated on ``SANDBOX_ENABLED``: turning delegation off is
    the moment a deployment most wants its leftover sandboxes collected, and
    that flag would stop the collection along with the feature. What is needed
    is credentials to talk to the provider with.

    A threshold of zero reads as **off**, not as "reap immediately" -- taken
    literally it would destroy the sandbox the turn running right now is about
    to resume.
    """
    if not settings.SANDBOX_REAP_ENABLED or settings.SANDBOX_REAP_IDLE_SECONDS <= 0:
        return False
    return bool(settings.SANDBOX_API_KEY or settings.SANDBOX_DOMAIN)


def idle_seconds(snapshot: SandboxSnapshot, now: datetime) -> float | None:
    """Seconds since the most recent thing known to have happened, or ``None``.

    ``None`` means undatable, and undatable means untouched: a sandbox we cannot
    date is one we cannot claim is abandoned.

    Future timestamps are ignored rather than clamped. E2B's ``end_at`` on a
    suspended sandbox is the moment it was paused, which is exactly the signal
    wanted -- but a provider may just as well report the expiry the sandbox
    *would* have had while running there, which is a statement about the future
    and says nothing about when it was last used. Taken at face value it would
    make the sandbox permanently unreapable.
    """
    seen = [t for t in (snapshot.started_at, snapshot.end_at) if t is not None and t <= now]
    if not seen:
        return None
    return (now - max(seen)).total_seconds()


async def reap_abandoned_sandboxes(*, now: datetime | None = None) -> ReapSummary:
    """Destroy suspended sandboxes idle beyond ``SANDBOX_REAP_IDLE_SECONDS``.

    Best effort throughout: a provider that cannot be listed, or a single kill
    that fails, is logged and counted rather than raised, because the caller is
    a periodic loop whose next pass will see the same sandbox again.
    """
    if not reaping_configured():
        return ReapSummary()
    idle_after = settings.SANDBOX_REAP_IDLE_SECONDS
    now = now or datetime.now(UTC)
    try:
        snapshots = await list_paused_sandboxes()
    except Exception:
        logger.warning("could not list sandboxes; skipping this reap pass", exc_info=True)
        return ReapSummary()

    summary = ReapSummary(listed=len(snapshots))
    for snapshot in snapshots:
        if not snapshot.sandbox_id:
            continue
        if not snapshot.managed and not settings.SANDBOX_REAP_UNTAGGED:
            # The credentials may be shared with another deployment, another
            # tool, or a person's own experiments. Killing someone else's
            # sandbox is worse than leaking one of ours.
            summary.skipped_foreign += 1
            continue
        idle = idle_seconds(snapshot, now)
        if idle is None or idle < idle_after:
            continue
        try:
            await kill_sandbox(snapshot.sandbox_id)
        except Exception:
            summary.failed += 1
            logger.warning("could not reap sandbox %s", snapshot.sandbox_id, exc_info=True)
            continue
        summary.reaped += 1
        logger.info(
            "Reaped abandoned sandbox",
            extra={
                "sandbox_id": snapshot.sandbox_id,
                "purpose": snapshot.purpose or "unknown",
                "idle_seconds": int(idle),
            },
        )
    if summary.reaped or summary.failed:
        logger.info(
            "Sandbox reap pass complete",
            extra={
                "listed": summary.listed,
                "reaped": summary.reaped,
                "failed": summary.failed,
                "skipped_foreign": summary.skipped_foreign,
            },
        )
    return summary
