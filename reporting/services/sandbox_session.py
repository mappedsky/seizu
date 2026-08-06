"""One sandbox for a conversation, instead of one per delegation or per turn.

``sandbox__delegate`` opened its own sandbox and destroyed it on return, so the
filesystem was wiped between delegations. Nothing an inner agent saved survived
to the next one: an oversized tool result written to a file, together with the
receipt telling the agent where to find it, was gone before anything could read
it. The result-file mechanism was built on storage that did not outlive a single
use, and a measured turn made between 31 and 79 delegations -- that many sandbox
creations, and that many times starting from an empty disk.

Sharing one sandbox across a *step* fixed that within a turn and left the same
hole between turns. A follow-up question that builds on the previous answer
arrived at an empty disk, so the turn re-ran the queries the last turn had
already run and re-derived what it had already derived, on top of its own work.
So a session is now **suspended** at the end of a turn rather than destroyed,
and resumed by id on the next one: the data an earlier turn gathered is still on
disk, and :mod:`reporting.services.episodic_memory` carries the receipts that
say what is there.

A session is opened lazily on the first delegation and suspended when the turn
ends, so a turn that never delegates pays nothing and one that delegates
repeatedly pays once.

**Isolation.** The per-call teardown was an isolation property even if an
incidental one, and persistence widens it further: untrusted code now persists
across the turns of one conversation. It stays bounded to a single thread of a
single user -- the resume id lives in that thread's checkpoint, which is already
namespaced per user -- and it holds no credentials. But it is a deliberate
widening of the blast radius, not a side effect, and ``SANDBOX_SESSION_PERSIST``
turns it off.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import Any

from reporting import settings
from reporting.services.sandbox_backend import SandboxBackend, open_backend

logger = logging.getLogger(__name__)


class SandboxSession:
    """A sandbox opened on demand and reused for the life of one turn.

    With ``persist`` set the sandbox is suspended rather than destroyed when the
    turn ends, and ``sandbox_id`` is the handle the next turn resumes it by.
    """

    def __init__(self, *, resume_sandbox_id: str | None = None, persist: bool = False) -> None:
        self._stack: AsyncExitStack | None = None
        self._backend: SandboxBackend | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._resume_sandbox_id = resume_sandbox_id or ""
        self._persist = persist
        self._sandbox_id = ""
        # Decided at close, not at open: whether the sandbox is worth keeping
        # depends on how the turn ended.
        self._suspend = True

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
                    )
                )
                # Publish all three together, so the stack can never belong to a
                # different backend than the one callers are handed and the id
                # never describes a sandbox that is not the open one.
                self._stack, self._backend = stack, backend
                self._sandbox_id = getattr(backend, "sandbox_id", "") or ""
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

    async def aclose(self, *, suspend: bool = True) -> str | None:
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
            return None
        try:
            await stack.aclose()
        except Exception:
            # Teardown failure must not fail the turn that already succeeded;
            # the provider reaps the sandbox on its own timeout. Report no
            # resume id: whether the suspend took effect is exactly what just
            # became unknown, and a bad id costs the next turn a failed resume.
            logger.warning("sandbox session teardown failed", exc_info=True)
            return None
        return sandbox_id if self._suspend_on_exit() and sandbox_id else None


_current_sandbox_session: ContextVar[SandboxSession | None] = ContextVar("_current_sandbox_session", default=None)


def start_sandbox_session(*, resume_sandbox_id: str | None = None, persist: bool | None = None) -> SandboxSession:
    """Make a fresh session ambient for the current turn.

    ``resume_sandbox_id`` is the id a previous turn suspended, read back out of
    the thread's checkpoint. ``persist`` defaults to ``SANDBOX_SESSION_PERSIST``;
    callers outside a conversation turn pass ``False``, because there is no
    thread to store the id in and a paused sandbox with no resume id is a leak.
    """
    session = SandboxSession(
        resume_sandbox_id=resume_sandbox_id,
        persist=settings.SANDBOX_SESSION_PERSIST if persist is None else persist,
    )
    _current_sandbox_session.set(session)
    return session


def current_sandbox_session() -> SandboxSession | None:
    return _current_sandbox_session.get()


async def close_sandbox_session(*, suspend: bool = True) -> str | None:
    """Close the ambient session; return the sandbox id the next turn resumes."""
    session = _current_sandbox_session.get()
    _current_sandbox_session.set(None)
    if session is None:
        return None
    return await session.aclose(suspend=suspend)


async def abandon_sandbox_session() -> str | None:
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
        return None
    return await close_sandbox_session(suspend=session.resumed)


async def discard_sandbox(sandbox_id: str) -> None:
    """Destroy a suspended sandbox nobody will resume (best effort).

    Deleting a thread drops the only record of its sandbox id, so without this
    the sandbox stays paused -- consuming provider-side storage -- until the
    provider's own retention reaps it.
    """
    if not sandbox_id:
        return
    try:
        from e2b_code_interpreter import AsyncSandbox

        kwargs: dict[str, Any] = {}
        if settings.SANDBOX_API_KEY:
            kwargs["api_key"] = settings.SANDBOX_API_KEY
        if settings.SANDBOX_DOMAIN:
            kwargs["domain"] = settings.SANDBOX_DOMAIN
            kwargs["validate_api_key"] = False
        await AsyncSandbox.kill(sandbox_id, **kwargs)
    except Exception:
        # Warning, not info: the id is about to stop being recorded anywhere,
        # so this line is the only thing that can lead an operator to the
        # orphaned sandbox.
        logger.warning("could not discard sandbox %s; it may be orphaned", sandbox_id, exc_info=True)
