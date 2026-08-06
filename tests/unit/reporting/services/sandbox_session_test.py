import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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


async def test_concurrent_delegations_open_only_one_sandbox(mocker):
    """Delegations in a step run through asyncio.gather, so this is the real path.

    Unsynchronized lazy init let both callers see no backend and open their own:
    two sandboxes, two filesystems, and an exit stack holding only one of them.
    """
    opened: list[Any] = []
    closed: list[Any] = []

    @asynccontextmanager
    async def _slow_open(**_kwargs: Any):
        backend = MagicMock()
        # Yield control while opening, which is what makes the race reachable.
        await asyncio.sleep(0)
        opened.append(backend)
        try:
            yield backend
        finally:
            closed.append(backend)

    mocker.patch("reporting.services.sandbox_session.open_backend", _slow_open)
    session = sandbox_session.start_sandbox_session()

    first, second, third = await asyncio.gather(session.backend(), session.backend(), session.backend())

    assert len(opened) == 1
    assert first is second is third

    await sandbox_session.close_sandbox_session()
    # The one that was opened is the one that gets torn down.
    assert closed == opened


async def test_opening_after_close_is_refused_rather_than_orphaned(mocker):
    """A delegation still queued when its step unwinds must not open a sandbox
    into a session nobody holds -- it would live to the provider timeout."""
    opened: list[Any] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_open_backend(opened, []))
    session = sandbox_session.start_sandbox_session()
    await sandbox_session.close_sandbox_session()

    with pytest.raises(RuntimeError, match="closed"):
        await session.backend()
    assert opened == []


def _fake_persistent_open_backend(calls: list[dict[str, Any]]) -> Any:
    """An open_backend that records how it was opened and how it was closed.

    Mirrors the real one closely enough to matter: it honours resume_sandbox_id
    (so the id comes back unchanged) and evaluates suspend_on_exit on the way
    out, which is where the suspend-or-kill decision actually lives.
    """

    @asynccontextmanager
    async def _open(**kwargs: Any):
        call: dict[str, Any] = {"kwargs": kwargs}
        calls.append(call)
        backend = MagicMock()
        backend.sandbox_id = kwargs.get("resume_sandbox_id") or f"sbx-{len(calls)}"
        try:
            yield backend
        finally:
            suspend = kwargs.get("suspend_on_exit")
            call["suspended"] = suspend() if callable(suspend) else bool(suspend)
            # The real one reports what actually happened, not what was asked.
            on_teardown = kwargs.get("on_teardown")
            if on_teardown is not None:
                on_teardown(call["suspended"])

    return _open


async def test_a_persistent_session_hands_back_the_id_to_resume(mocker):
    """The whole point of suspending: the next turn opens the same disk."""
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(persist=True)
    await session.backend()
    teardown = await sandbox_session.close_sandbox_session()

    assert calls[0]["suspended"] is True
    assert (teardown.opened, teardown.suspended_id) == (True, "sbx-1")

    # Next turn, same conversation.
    sandbox_session.start_sandbox_session(resume_sandbox_id=teardown.suspended_id, persist=True)
    next_session = sandbox_session.current_sandbox_session()
    assert next_session is not None
    await next_session.backend()
    assert calls[1]["kwargs"]["resume_sandbox_id"] == "sbx-1"
    assert next_session.resumed is True
    await sandbox_session.close_sandbox_session()


async def test_a_turn_that_never_delegated_carries_no_id_forward(mocker):
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    sandbox_session.start_sandbox_session(persist=True)
    # No sandbox was opened, so there is nothing to resume and nothing paused --
    # and nothing for the thread to record, which must leave any stored id alone.
    teardown = await sandbox_session.close_sandbox_session()
    assert (teardown.opened, teardown.suspended_id) == (False, "")
    assert calls == []


async def test_a_failed_turn_destroys_its_sandbox_rather_than_pausing_it(mocker):
    """Nothing will store the id, so a paused sandbox would be unreachable."""
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(persist=True)
    await session.backend()

    teardown = await sandbox_session.close_sandbox_session(suspend=False)
    assert (teardown.opened, teardown.suspended_id) == (True, "")
    assert calls[0]["suspended"] is False


async def test_a_replacement_sandbox_is_not_reported_as_resumed(mocker):
    """A resume that failed yields a different id, and the caller must be able
    to tell -- every file an earlier turn recorded is gone with the old one."""

    @asynccontextmanager
    async def _open(**kwargs: Any):
        backend = MagicMock()
        backend.sandbox_id = "sbx-new"  # not the one that was asked for
        yield backend
        on_teardown = kwargs.get("on_teardown")
        if on_teardown is not None:
            on_teardown(True)

    mocker.patch("reporting.services.sandbox_session.open_backend", _open)
    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-old", persist=True)
    await session.backend()

    assert session.resumed is False
    assert session.sandbox_id == "sbx-new"
    assert (await sandbox_session.close_sandbox_session()).suspended_id == "sbx-new"


async def test_a_non_persistent_session_still_destroys_its_sandbox(mocker):
    """The default outside a conversation turn: there is no thread to store an
    id in, so a paused sandbox would leak."""
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(persist=False)
    await session.backend()

    teardown = await sandbox_session.close_sandbox_session()
    assert (teardown.opened, teardown.suspended_id) == (True, "")
    assert calls[0]["suspended"] is False


async def test_the_expected_id_is_the_resume_target_before_anything_opens(mocker):
    """Prompts are built at the top of a turn, before any delegation."""
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-7", persist=True)
    assert session.expected_sandbox_id == "sbx-7"
    await session.backend()
    assert session.expected_sandbox_id == "sbx-7"
    await sandbox_session.close_sandbox_session()


async def test_a_failed_turn_keeps_a_sandbox_the_thread_already_knows_about(mocker):
    """A single broken turn must not empty a twenty-turn session's disk.

    The id was written to the checkpoint by an earlier successful turn, so
    failing changes nothing about whether it can be found again -- destroying it
    would throw away every earlier turn's accumulated work to prevent a leak
    that cannot happen.
    """
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-1", persist=True)
    await session.backend()
    assert session.resumed is True

    teardown = await sandbox_session.abandon_sandbox_session()

    assert calls[0]["suspended"] is True
    assert (teardown.opened, teardown.suspended_id) == (True, "sbx-1")


async def test_a_failed_turn_destroys_a_sandbox_it_created_itself(mocker):
    """The opposite case: it raised before anything could store the id, so
    pausing would strand a sandbox nobody can ever find again."""
    calls: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _fake_persistent_open_backend(calls))

    session = sandbox_session.start_sandbox_session(persist=True)
    await session.backend()
    assert session.resumed is False

    teardown = await sandbox_session.abandon_sandbox_session()
    assert (teardown.opened, teardown.suspended_id) == (True, "")
    assert calls[0]["suspended"] is False


async def test_a_failed_turn_whose_resume_failed_destroys_the_replacement(mocker):
    """The replacement's id is nowhere either -- the checkpoint still names the
    sandbox that had already gone."""

    @asynccontextmanager
    async def _open(**kwargs: Any):
        backend = MagicMock()
        backend.sandbox_id = "sbx-replacement"
        try:
            yield backend
        finally:
            suspend = kwargs.get("suspend_on_exit")
            kwargs["_suspended"] = suspend() if callable(suspend) else bool(suspend)
            opened.append(kwargs)
            on_teardown = kwargs.get("on_teardown")
            if on_teardown is not None:
                on_teardown(kwargs["_suspended"])

    opened: list[dict[str, Any]] = []
    mocker.patch("reporting.services.sandbox_session.open_backend", _open)

    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-gone", persist=True)
    await session.backend()

    teardown = await sandbox_session.abandon_sandbox_session()
    assert (teardown.opened, teardown.suspended_id) == (True, "")
    assert opened[0]["_suspended"] is False


async def test_abandoning_without_a_session_is_a_no_op():
    await sandbox_session.close_sandbox_session()
    assert (await sandbox_session.abandon_sandbox_session()).opened is False


async def test_a_pause_that_failed_does_not_hand_back_the_dead_id(mocker):
    """Intent is not outcome: a failed pause falls back to a kill, and returning
    that id would checkpoint a sandbox that no longer exists -- costing the next
    turn a failed resume and stranding the data it thought it had."""

    @asynccontextmanager
    async def _open(**kwargs: Any):
        backend = MagicMock()
        backend.sandbox_id = "sbx-1"
        try:
            yield backend
        finally:
            on_teardown = kwargs.get("on_teardown")
            if on_teardown is not None:
                on_teardown(False)  # pause failed; the backend killed it instead

    mocker.patch("reporting.services.sandbox_session.open_backend", _open)
    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-1", persist=True)
    await session.backend()

    assert (await sandbox_session.close_sandbox_session()).suspended_id == ""


async def test_a_killed_sandbox_reports_that_the_thread_must_clear_its_id(mocker):
    """Omitting the key does not clear it: the reducer overwrites rather than
    merges, so a caller that only writes non-empty ids leaves the thread naming
    a dead sandbox -- and its receipts advertising files under that id."""

    @asynccontextmanager
    async def _open(**kwargs: Any):
        backend = MagicMock()
        backend.sandbox_id = "sbx-1"
        try:
            yield backend
        finally:
            on_teardown = kwargs.get("on_teardown")
            if on_teardown is not None:
                on_teardown(False)  # pause failed; killed instead

    mocker.patch("reporting.services.sandbox_session.open_backend", _open)
    session = sandbox_session.start_sandbox_session(resume_sandbox_id="sbx-1", persist=True)
    await session.backend()

    teardown = await sandbox_session.close_sandbox_session()

    assert teardown.opened is True  # so the caller writes the key...
    assert teardown.suspended_id == ""  # ...with an empty value, clearing it
