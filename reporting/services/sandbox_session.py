"""One sandbox shared by every delegation in a step, instead of one per call.

``sandbox__delegate`` opened its own sandbox and destroyed it on return, so the
filesystem was wiped between delegations. Nothing an inner agent saved survived
to the next one: an oversized tool result written to a file, together with the
receipt telling the agent where to find it, was gone before anything could read
it. The result-file mechanism was built on storage that did not outlive a single
use, and a measured turn made between 31 and 79 delegations -- that many sandbox
creations, and that many times starting from an empty disk.

A session is opened lazily on the first delegation and closed when the step
ends, so a step that never delegates pays nothing and one that delegates
repeatedly pays once.

**Isolation.** The per-call teardown was an isolation property even if an
incidental one, and this widens it: untrusted code now persists across the
delegations of one step. It stays bounded to a single step of a single user's
turn, holds no credentials, and is destroyed when the step ends -- but it is a
deliberate widening of the blast radius, not a side effect.
"""

import asyncio
import logging
from contextlib import AsyncExitStack
from contextvars import ContextVar

from reporting import settings
from reporting.services.sandbox_backend import SandboxBackend, open_backend

logger = logging.getLogger(__name__)


class SandboxSession:
    """A sandbox opened on demand and reused for the life of one step."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._backend: SandboxBackend | None = None
        self._lock = asyncio.Lock()
        self._closed = False

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
                # The step that owned this session has ended. Opening now would
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
                        # outlive a whole step rather than a single delegation,
                        # and the provider default would kill it mid-step.
                        timeout_seconds=settings.SANDBOX_SESSION_TIMEOUT_SECONDS,
                    )
                )
                # Publish both together, so the stack can never belong to a
                # different backend than the one callers are handed.
                self._stack, self._backend = stack, backend
            return self._backend

    @property
    def opened(self) -> bool:
        return self._backend is not None

    async def aclose(self) -> None:
        # Under the lock, so a delegation part-way through opening cannot
        # publish its sandbox into a session that has already been torn down.
        async with self._lock:
            self._closed = True
            stack, self._stack, self._backend = self._stack, None, None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                # Teardown failure must not fail the step that already
                # succeeded; the provider reaps the sandbox on its own timeout.
                logger.warning("sandbox session teardown failed", exc_info=True)


_current_sandbox_session: ContextVar[SandboxSession | None] = ContextVar("_current_sandbox_session", default=None)


def start_sandbox_session() -> SandboxSession:
    """Make a fresh session ambient for the current step."""
    session = SandboxSession()
    _current_sandbox_session.set(session)
    return session


def current_sandbox_session() -> SandboxSession | None:
    return _current_sandbox_session.get()


async def close_sandbox_session() -> None:
    session = _current_sandbox_session.get()
    _current_sandbox_session.set(None)
    if session is not None:
        await session.aclose()
