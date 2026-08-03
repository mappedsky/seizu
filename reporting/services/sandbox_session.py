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

    async def backend(self) -> SandboxBackend:
        if self._backend is None:
            self._stack = AsyncExitStack()
            self._backend = await self._stack.enter_async_context(
                open_backend(
                    api_key=settings.SANDBOX_API_KEY,
                    domain=settings.SANDBOX_DOMAIN,
                    # An explicit lifetime, because a shared sandbox has to
                    # outlive a whole step rather than a single delegation, and
                    # the provider default would kill it mid-step.
                    timeout_seconds=settings.SANDBOX_SESSION_TIMEOUT_SECONDS,
                )
            )
        return self._backend

    @property
    def opened(self) -> bool:
        return self._backend is not None

    async def aclose(self) -> None:
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
