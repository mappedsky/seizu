"""One sandbox for a conversation, instead of one per delegation or per turn.

A session is opened lazily on the first delegation, shared by every delegation
and step of the turn, then **suspended** at the end of the turn and resumed by
id on the next one — so the data an earlier turn gathered is still on disk and
:mod:`reporting.services.episodic_memory` carries the receipts that say what is
there. A turn that never delegates pays nothing.

Persistence widens the blast radius deliberately: untrusted code's output lives
across the turns of one conversation, bounded to a single thread of a single
user (the resume id lives in that thread's already user-namespaced checkpoint)
and holding no credentials. ``SANDBOX_SESSION_PERSIST`` turns it off.

The scope (turn-level, not step-level), the resume-failure rules, and why error
paths abandon rather than destroy are **SBX-005 through SBX-007** in
``docs/root/dev/decisions/sandbox.md``. Read those before changing the
lifecycle: each rule is there because the obvious alternative loses either a
conversation's accumulated work or a paid-for sandbox.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from contextvars import ContextVar
from dataclasses import dataclass

from reporting import settings
from reporting.services.sandbox_backend import SandboxBackend, kill_sandbox, open_backend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxTeardown:
    """What closing a session leaves behind, and what the thread should record.

    Three outcomes, and they are not interchangeable. A suspended sandbox gives
    the thread an id to resume. A sandbox that was opened and *not* suspended
    leaves the thread's stored id naming something dead, so it has to be
    cleared -- omitting the key does not clear it, because the reducer
    overwrites rather than merges. And a turn that never opened one must leave
    the stored id alone, or a turn that simply did not delegate would throw away
    the conversation's sandbox.
    """

    opened: bool = False
    suspended_id: str = ""


class SandboxSession:
    """A sandbox opened on demand and reused for the life of one turn.

    With ``persist`` set the sandbox is suspended rather than destroyed when the
    turn ends, and ``sandbox_id`` is the handle the next turn resumes it by.
    """

    def __init__(self, *, resume_sandbox_id: str | None = None, persist: bool = False, thread: str = "") -> None:
        self._stack: AsyncExitStack | None = None
        self._backend: SandboxBackend | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._resume_sandbox_id = resume_sandbox_id or ""
        self._persist = persist
        self._thread = thread
        self._sandbox_id = ""
        # Decided at close, not at open: whether the sandbox is worth keeping
        # depends on how the turn ended.
        self._suspend = True
        # Set on teardown: what actually happened, which is not always what was
        # asked for.
        self._suspended = False

    async def backend(self) -> SandboxBackend:
        """The session's sandbox, opening it on first use.

        Locked, because the delegations sharing a session run concurrently: a
        tool batch dispatches them through ``asyncio.gather``. A plain
        check-then-act across the ``await`` lets two callers both see no backend
        and both open one, which defeats the file continuity this exists for --
        each would write into a different sandbox -- and leaves the exit stack
        holding whichever finished last, so the other is never torn down.
        """
        if self._backend is not None:
            return self._backend
        async with self._lock:
            if self._closed:
                # The turn that owned this session has ended. Opening now would
                # produce a sandbox nobody holds a reference to, alive until the
                # provider reaps it half an hour later.
                raise RuntimeError("sandbox session is closed")
            # Re-check inside the lock: a caller that waited here while another
            # opened the sandbox must use that one, not open a second.
            if self._backend is None:
                stack = AsyncExitStack()
                backend = await stack.enter_async_context(
                    open_backend(
                        api_key=settings.SANDBOX_API_KEY,
                        domain=settings.SANDBOX_DOMAIN,
                        # An explicit lifetime, because a shared sandbox has to
                        # outlive a whole turn rather than a single delegation,
                        # and the provider default would kill it mid-turn.
                        timeout_seconds=settings.SANDBOX_SESSION_TIMEOUT_SECONDS,
                        resume_sandbox_id=self._resume_sandbox_id or None,
                        suspend_on_exit=self._suspend_on_exit,
                        on_teardown=self._record_teardown,
                        # The one kind of sandbox that outlives the run that
                        # created it, so the one the reaper exists for -- and
                        # the thread tag is how the reaper finds the session
                        # whose lifetime it now shares.
                        purpose="chat-session",
                        thread=self._thread,
                    )
                )
                # Publish all three together, so the stack can never belong to a
                # different backend than the one callers are handed and the id
                # never describes a sandbox that is not the open one.
                self._stack, self._backend = stack, backend
                self._sandbox_id = getattr(backend, "sandbox_id", "") or ""
                self._suspended = False
            return self._backend

    @property
    def opened(self) -> bool:
        return self._backend is not None

    @property
    def sandbox_id(self) -> str:
        """The open sandbox's id, or ``""`` before one is opened."""
        return self._sandbox_id

    @property
    def expected_sandbox_id(self) -> str:
        """The sandbox this session is in, or the one it will try to resume.

        For callers that must describe the sandbox's contents *before* anything
        has opened it -- a prompt built at the top of a turn -- where the honest
        answer is the id we are about to ask for. If that resume fails the id
        changes, and the sub-agent's own recall, built after the sandbox is
        open, is the one that corrects the record.
        """
        return self._sandbox_id or self._resume_sandbox_id

    @property
    def resumed(self) -> bool:
        """Whether the open sandbox is the one this session asked to resume.

        False for a fresh sandbox -- including one created because the resume
        failed -- which is what tells callers that files an earlier turn
        recorded are gone rather than merely unread.
        """
        return bool(self._resume_sandbox_id) and self._sandbox_id == self._resume_sandbox_id

    def _suspend_on_exit(self) -> bool:
        return self._persist and self._suspend

    def _record_teardown(self, suspended: bool) -> None:
        """Whether the sandbox was actually left suspended.

        A pause can fail, and the fallback is to kill -- so intent is not
        outcome. Without this the session returned the id of a sandbox it had
        just destroyed, and the next turn paid a failed resume for it.
        """
        self._suspended = suspended

    async def aclose(self, *, suspend: bool = True) -> SandboxTeardown:
        """Tear the session down; return the id to resume, if it was suspended.

        ``suspend=False`` destroys the sandbox even on a persistent session. The
        error paths use it: a turn that raised is not going to write its resume
        id anywhere, and a paused sandbox nobody holds the id for is a leak that
        outlives the process.
        """
        # Under the lock, so a delegation part-way through opening cannot
        # publish its sandbox into a session that has already been torn down.
        async with self._lock:
            self._closed = True
            self._suspend = suspend
            stack, self._stack, self._backend = self._stack, None, None
            sandbox_id = self._sandbox_id
        if stack is None:
            return SandboxTeardown(opened=False)
        try:
            await stack.aclose()
        except Exception:
            # Teardown failure must not fail the turn that already succeeded;
            # the provider reaps the sandbox on its own timeout. Report no
            # resume id: whether the suspend took effect is exactly what just
            # became unknown, and a bad id costs the next turn a failed resume.
            logger.warning("sandbox session teardown failed", exc_info=True)
            return SandboxTeardown(opened=True)
        # ``_suspended``, not the intent: a pause that failed fell back to a
        # kill, and returning that id would checkpoint a dead sandbox.
        return SandboxTeardown(opened=True, suspended_id=sandbox_id if self._suspended and sandbox_id else "")


_current_sandbox_session: ContextVar[SandboxSession | None] = ContextVar("_current_sandbox_session", default=None)


def start_sandbox_session(
    *,
    resume_sandbox_id: str | None = None,
    persist: bool | None = None,
    thread: str = "",
) -> SandboxSession:
    """Make a fresh session ambient for the current turn.

    ``resume_sandbox_id`` is the id a previous turn suspended, read back out of
    the thread's checkpoint. ``persist`` defaults to ``SANDBOX_SESSION_PERSIST``;
    callers outside a conversation turn pass ``False``, because there is no
    thread to store the id in and a paused sandbox with no resume id is a leak.
    ``thread`` is the namespaced chat thread the sandbox belongs to, stamped on
    it so the session reaper can tell whose it is.
    """
    session = SandboxSession(
        resume_sandbox_id=resume_sandbox_id,
        persist=settings.SANDBOX_SESSION_PERSIST if persist is None else persist,
        thread=thread,
    )
    _current_sandbox_session.set(session)
    return session


def current_sandbox_session() -> SandboxSession | None:
    return _current_sandbox_session.get()


async def close_sandbox_session(*, suspend: bool = True) -> SandboxTeardown:
    """Close the ambient session; report what the thread should record."""
    session = _current_sandbox_session.get()
    _current_sandbox_session.set(None)
    if session is None:
        return SandboxTeardown(opened=False)
    return await session.aclose(suspend=suspend)


async def abandon_sandbox_session() -> SandboxTeardown:
    """Close the session after a turn that failed.

    Keep a sandbox the thread already knows about; destroy one it does not.

    The distinction is the whole point. A turn that *resumed* a sandbox is
    working in one whose id a previous successful turn already wrote to the
    checkpoint, so failing changes nothing about its recoverability -- and
    destroying it would throw away every earlier turn's accumulated work because
    one turn raised. A single broken turn should not empty a twenty-turn
    session's disk. A turn that *created* its sandbox is the opposite case: it
    raised before anything could store the id, so pausing it would strand a
    sandbox nobody can ever find again.
    """
    session = _current_sandbox_session.get()
    if session is None:
        return SandboxTeardown(opened=False)
    return await close_sandbox_session(suspend=session.resumed)


async def discard_sandbox(sandbox_id: str) -> None:
    """Destroy a suspended sandbox nobody will resume (best effort).

    Deleting a thread drops the only record of its sandbox id, so without this
    the sandbox stays paused -- consuming provider-side storage -- until
    :mod:`reporting.services.session_reaper` picks it up as an orphan, which is
    a scheduled pass rather than the immediate cleanup a deletion deserves.
    """
    if not sandbox_id:
        return
    try:
        await kill_sandbox(sandbox_id)
    except Exception:
        # Warning, not info: the id is about to stop being recorded anywhere,
        # so this line is the only thing that can lead an operator to the
        # orphaned sandbox.
        logger.warning("could not discard sandbox %s; it may be orphaned", sandbox_id, exc_info=True)
