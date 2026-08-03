import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from reporting.services import sandbox_session


def _fake_open_backend(opened: list[Any], closed: list[Any]) -> Any:
    @asynccontextmanager
    async def _open(**_kwargs: Any):
        backend = MagicMock()
        backend.run_python = AsyncMock(return_value="")
        opened.append(backend)
        try:
            yield backend
        finally:
            closed.append(backend)

    return _open


async def test_the_same_sandbox_serves_every_delegation_in_a_step(mocker):
    """The point: a file written by one delegation is still there for the next.

    Each delegation used to open and destroy its own sandbox, so an oversized
    result written to a file was gone before anything could read it.
    """
    opened: list[Any] = []
    closed: list[Any] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_open_backend(opened, closed))

    session = sandbox_session.start_sandbox_session()
    first = await session.backend()
    second = await session.backend()

    assert first is second
    assert len(opened) == 1
    assert not closed  # still live while the step runs

    await sandbox_session.close_sandbox_session()
    assert len(closed) == 1


async def test_a_step_that_never_delegates_opens_nothing(mocker):
    opened: list[Any] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_open_backend(opened, []))

    session = sandbox_session.start_sandbox_session()
    assert not session.opened

    await sandbox_session.close_sandbox_session()
    # Opening one eagerly would charge every step for a sandbox it never used.
    assert opened == []


async def test_parallel_steps_do_not_share_a_sandbox(mocker):
    opened: list[Any] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_open_backend(opened, []))
    seen: dict[str, Any] = {}

    async def _step(name: str) -> None:
        sandbox_session.start_sandbox_session()
        await asyncio.sleep(0)
        session = sandbox_session.current_sandbox_session()
        assert session is not None
        seen[name] = await session.backend()
        await sandbox_session.close_sandbox_session()

    await asyncio.gather(_step("a"), _step("b"))

    # One user's step must not be able to read another's files.
    assert seen["a"] is not seen["b"]
    assert len(opened) == 2


async def test_no_ambient_session_outside_a_step():
    await sandbox_session.close_sandbox_session()
    assert sandbox_session.current_sandbox_session() is None


async def test_a_teardown_failure_does_not_fail_the_step(mocker):
    @asynccontextmanager
    async def _open(**_kwargs: Any):
        try:
            yield MagicMock()
        finally:
            raise RuntimeError("provider went away")

    mocker.patch("reporting.services.sandbox_session.open_backend", _open)
    session = sandbox_session.start_sandbox_session()
    await session.backend()

    # The step's work already succeeded; the provider reaps the sandbox anyway.
    await sandbox_session.close_sandbox_session()
    assert sandbox_session.current_sandbox_session() is None
