"""Run an interactive chat turn independently of the connection watching it.

A turn used to *be* the HTTP request: the graph was iterated inside the
``StreamingResponse`` generator, so a dropped connection destroyed minutes of
work and a stalled worker truncated the response mid-sentence. Here the turn is
a producer writing an append-only log of already-rendered UI-stream parts, and
the request is a reader tailing that log. The two are only coupled by a turn id,
so a client can disconnect and come back, and the work carries on either way.

**Parts are rendered by the producer, not the reader.** The log holds the exact
JSON the live stream sent, so the first delivery and every replay are
byte-identical -- there is no second rendering path that can drift from the
first. It is also what makes the reader trivial enough to be shared by the
initial ``POST`` and the reconnecting ``GET``.

The producer runs as a detached task in this process today.
:func:`start_turn` is the seam where it becomes a Temporal workflow instead;
nothing above it has to change, because the reader only ever sees the log.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.schema.chat import (
    CHAT_TURN_MAX_BATCH_BYTES,
    ChatStreamRequest,
    ChatTurnItem,
    ChatTurnNotAdmittedError,
)
from reporting.services import report_store
from reporting.services.chat_budget import BudgetController, initial_budget_ledger
from reporting.services.chat_graph import ChatState, build_turn_config, get_chat_graph
from reporting.services.chat_messages import CONTINUATION_MARKDOC, MessageTag, tag_message

logger = logging.getLogger(__name__)

_CONTINUE_RESPONSE_PROMPT = (
    "Continue the previous assistant response from where it stopped because of the output limit. "
    "Do not repeat earlier content."
)

# Expired logs collected per sweep. Bounded so a backlog costs several cheap
# passes rather than one long one at the end of somebody's turn.
_EXPIRED_TURNS_PER_SWEEP = 25

# How often a caller waiting for a turn to stop re-reads it. The local fast
# path is immediate; a producer on another worker takes until its heartbeat.
_TURN_STOP_POLL_SECONDS = 0.05

# When this process last swept expired logs. Paced rather than run on every
# completed turn; see :func:`sweep_expired_turns`.
_last_sweep_monotonic = 0.0

# Detached producer tasks, held so the event loop keeps a strong reference. A
# task nobody holds can be garbage collected mid-run, which would look exactly
# like the failure this module exists to remove. Keyed by turn id so a cancel
# arriving on this worker can stop one without waiting for its heartbeat.
_running_producers: dict[str, asyncio.Task[None]] = {}
# Turns whose local cancellation has already been requested. See
# :func:`cancel_local_producer` for why a second request must not land.
_cancelling: set[str] = set()
# Finalizers for turns whose producer never got to run. Held for the same
# reason as the producers themselves.
_cleanup_tasks: set["asyncio.Task[None]"] = set()


class _TurnCanceled(Exception):
    """Raised inside the producer when the turn has been asked to stop."""


def _expires_within(expires_at: str, window: timedelta) -> bool:
    """True when the lease runs out inside ``window``.

    An unparsable timestamp counts as expiring: renewing a lease that did not
    need it costs one write, while failing to renew one that did loses the turn.
    """
    try:
        return datetime.fromisoformat(expires_at) - datetime.now(tz=UTC) <= window
    except ValueError:
        return True


def sse_frame(part: dict[str, Any]) -> str:
    return f"data: {json.dumps(part, separators=(',', ':'))}\n\n"


def render_parts(chunk: Any, text_id: str) -> list[dict[str, Any]]:
    """Map one ``stream_mode="custom"`` chunk onto UI message stream parts.

    The graph's stream vocabulary is small -- ``token``, ``detail`` and
    ``finish_reason`` -- and anything else is dropped rather than guessed at.
    ``finish_reason`` produces no part: it changes how the turn *ends*, which
    the publisher folds into the trailing ``finish`` frame.
    """
    if not isinstance(chunk, dict):
        return []
    kind = chunk.get("kind")
    if kind == "token":
        delta = chunk.get("content")
        if isinstance(delta, str) and delta:
            return [{"type": "text-delta", "id": text_id, "delta": delta}]
        return []
    if kind == "detail":
        detail_id = chunk.get("id")
        detail_data = chunk.get("data")
        if isinstance(detail_id, str) and isinstance(detail_data, dict):
            return [{"type": "data-seizu-detail", "id": detail_id, "data": detail_data}]
    return []


def finish_parts(finish_reason: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "finish",
            "finishReason": finish_reason,
            "messageMetadata": {
                "finish_reason": finish_reason,
                "response_cut_off": finish_reason == "length",
            },
        }
    ]


class ChatTurnPublisher:
    """Buffers rendered parts and appends them to a turn's event log.

    Batching is the whole point: a token-per-write log would be one store write
    per token. The flush interval is therefore the latency a viewer pays, and
    the batch size is what keeps the write count proportional to time rather
    than to output length.
    """

    def __init__(self, turn: ChatTurnItem) -> None:
        self._turn = turn
        self._buffer: list[dict[str, Any]] = []
        self._buffered_bytes = 0
        self._seq = 0
        self._lock = asyncio.Lock()
        self._flusher: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._producer: asyncio.Task[Any] | None = None

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def stopped(self) -> asyncio.Event:
        """Set when the turn should stop: cancelled, or its record is gone."""
        return self._stopped

    async def __aenter__(self) -> "ChatTurnPublisher":
        # Entered from inside the producer, so this *is* the producer's task --
        # which is what lets the heartbeat interrupt it rather than only ask it
        # to notice.
        self._producer = asyncio.current_task()
        # A periodic flusher rather than flushing only on append: a turn that
        # goes quiet mid-tool-call would otherwise leave its last partial batch
        # sitting in memory, and the viewer would see the stream stall.
        self._flusher = asyncio.create_task(self._flush_loop())
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for task in (self._flusher, self._heartbeat):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await self.flush()

    async def _flush_loop(self) -> None:
        interval = max(settings.CHAT_TURN_FLUSH_MS, 1) / 1000
        while True:
            await asyncio.sleep(interval)
            await self.flush()

    async def _heartbeat_loop(self) -> None:
        """Hold the lease and watch for a stop request.

        Both live here because both must happen while the turn is *quiet* — a
        long tool call is exactly when a lease lapses and when a user reaches
        for Stop, and neither can be driven by token output. The stop request
        arrives through the record rather than as a signal because the producer
        and the request asking it to stop are not necessarily in the same
        process.
        """
        interval = max(settings.CHAT_TURN_HEARTBEAT_SECONDS, 1)
        # Renew well before expiry so one failed heartbeat does not drop the
        # lease; a renewal is a single conditional write.
        renew_after = timedelta(seconds=settings.CHAT_TURN_RETENTION_SECONDS / 2)
        while True:
            await asyncio.sleep(interval)
            try:
                turn = await report_store.get_chat_turn(self._turn.turn_id)
                if turn is None or turn.cancel_requested or turn.status != "running":
                    self._stop_producer()
                    return
                if _expires_within(turn.expires_at, renew_after) and (
                    await report_store.renew_chat_turn_lease(self._turn.turn_id) is None
                ):
                    # The lease is gone: the thread has been taken, or the turn
                    # is no longer running. Carrying on would mean two producers
                    # writing one conversation.
                    logger.warning("Chat turn lost its lease", extra={"turn_id": self._turn.turn_id})
                    self._stop_producer()
                    return
            except Exception:
                # A transient store failure must not stop a healthy turn; the
                # lease has half the retention window of slack for exactly this.
                logger.warning("Chat turn heartbeat failed", extra={"turn_id": self._turn.turn_id}, exc_info=True)

    def _stop_producer(self) -> None:
        """End the turn now, wherever it happens to be.

        Setting the flag is not enough on its own: the producer only reads it
        between chunks, and a turn is most likely to be stopped precisely while
        it is blocked on a slow model call or tool. Cancelling the task
        interrupts that, so a Stop landing on another worker behaves like one
        landing on this one instead of waiting for the call to finish -- which
        would also let the tool's side effects happen first.
        """
        self._stopped.set()
        # Through the shared guard rather than cancelling the task directly, so
        # this and a concurrent request cannot both interrupt the same producer.
        cancel_local_producer(self._turn.turn_id)

    async def publish(self, parts: list[dict[str, Any]]) -> None:
        if not parts:
            return
        async with self._lock:
            for part in parts:
                encoded = json.dumps(part, separators=(",", ":"))
                # Flush before the batch would exceed what a single store item
                # can hold. A part larger than the whole budget cannot be split
                # any further, so it goes out alone and the store rejects it if
                # it is genuinely impossible -- better a loud failure than a
                # silently dropped detail.
                if self._buffer and self._buffered_bytes + len(encoded) > CHAT_TURN_MAX_BATCH_BYTES // 2:
                    await self._flush_locked()
                self._buffer.append(part)
                self._buffered_bytes += len(encoded) + 1
        if self._buffered_bytes >= CHAT_TURN_MAX_BATCH_BYTES // 2:
            await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        parts_json = json.dumps(self._buffer, separators=(",", ":"))
        seq = self._seq + 1
        await report_store.append_chat_turn_events(self._turn.turn_id, seq, parts_json)
        self._seq = seq
        self._buffer = []
        self._buffered_bytes = 0


def build_graph_input(body: ChatStreamRequest, budget_controller: BudgetController) -> ChatState:
    """Build the turn's opening message.

    Confirmation resumes and continuations are tagged ephemeral: they are
    plumbing the user never wrote and must not survive into the model's view of
    the conversation (AGT-003).
    """
    if body.resume_confirmation_id:
        resume_message = HumanMessage(
            content=f"Resume approved confirmation {body.resume_confirmation_id}",
            id=f"msg_{uuid.uuid4().hex}",
            additional_kwargs={"resume_confirmation_id": body.resume_confirmation_id},
        )
        tag_message(resume_message, MessageTag.EPHEMERAL)
        return {"messages": [resume_message], "budget": budget_controller.snapshot()}
    if body.continue_response:
        continue_message = HumanMessage(
            content=_CONTINUE_RESPONSE_PROMPT,
            id=f"msg_{uuid.uuid4().hex}",
            additional_kwargs={"continue_response": True},
        )
        tag_message(continue_message, MessageTag.EPHEMERAL)
        return {"messages": [continue_message], "budget": budget_controller.snapshot()}
    return {
        "messages": [HumanMessage(content=body.message, id=f"msg_{uuid.uuid4().hex}")],
        "budget": budget_controller.snapshot(),
    }


async def start_turn(body: ChatStreamRequest, current: CurrentUser) -> ChatTurnItem:
    """Open a turn's event log and start producing into it.

    Raises :class:`reporting.schema.chat.ChatTurnConflictError` when the thread
    already has a turn running -- the caller should reconnect to that one rather
    than start a second.
    """
    # Reusing the client's message id for a continuation is what makes the
    # continued text land in the same assistant message rather than a new one.
    message_id = (
        body.continue_message_id if body.continue_response and body.continue_message_id else f"msg_{uuid.uuid4().hex}"
    )
    # Admission happens inside this write, not before it: the store moves the
    # session's timestamp and creates the turn in one commit, so a delete cannot
    # claim the session in between and cascade over a turn about to exist.
    turn = await report_store.create_chat_turn(
        current.user.user_id,
        body.thread_id,
        message_id,
        f"text_{uuid.uuid4().hex}",
        body.client_token,
    )
    if turn is None:
        raise ChatTurnNotAdmittedError("This conversation has been retired")
    turn_id = turn.turn_id
    task = asyncio.create_task(run_turn_in_process(turn, body, current))
    _running_producers[turn_id] = task

    def _forget(finished: "asyncio.Task[None]") -> None:
        _running_producers.pop(turn_id, None)
        _cancelling.discard(turn_id)
        if finished.cancelled():
            # Cancelled before its coroutine first ran, so none of the terminal
            # cleanup happened and the record still says "running". Nothing else
            # will ever finish it: waiting for the lease to lapse would leave the
            # conversation neither usable nor deletable for minutes.
            cleanup = asyncio.create_task(_finalize_abandoned_turn(turn_id))
            # Held for the same reason the producers are: a task nobody
            # references can be collected before it runs, and this one is what
            # keeps the turn from reading as running until its lease expires.
            _cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(_cleanup_tasks.discard)

    task.add_done_callback(_forget)
    return turn


def cancel_local_producer(turn_id: str) -> bool:
    """Stop a producer running in *this* process, if it is here.

    A fast path, not the mechanism: with several workers the request asking a
    turn to stop usually lands somewhere else, and the store flag is what
    reaches it there. This just spares the common single-worker case a
    heartbeat interval of delay.

    **First writer wins.** Cancelling twice is not harmless: the producer clears
    its own cancellation before running its terminal cleanup, so a second
    ``cancel()`` -- from a retried request, or from the heartbeat noticing the
    flag the first one set -- lands *inside* that cleanup and leaves the turn
    recorded as running forever. Being idempotent over HTTP is not enough; it
    has to be idempotent here.
    """
    task = _running_producers.get(turn_id)
    if task is None or task.done() or turn_id in _cancelling:
        return False
    _cancelling.add(turn_id)
    task.cancel()
    return True


async def _finalize_abandoned_turn(turn_id: str) -> None:
    """Give a terminal status to a turn whose producer never got to run."""
    try:
        turn = await report_store.get_chat_turn(turn_id)
        if turn is not None and turn.status == "running":
            await report_store.finish_chat_turn(turn_id, "canceled", 0)
    except Exception:
        # The lease is the backstop; it expires either way.
        logger.warning("Could not finalize an abandoned chat turn", extra={"turn_id": turn_id}, exc_info=True)


async def await_turn_stopped(turn_id: str, timeout_seconds: float) -> bool:
    """Wait for a turn to reach a terminal state, or give up.

    Cancelling a turn is a request, and on another worker it takes until that
    producer's next heartbeat. A caller about to delete what the producer is
    writing into has to know it has actually stopped, not just been asked to.

    Returns False on timeout; the caller decides whether to proceed. Proceeding
    is safe but not free: the producer will find its header gone and clean up
    the batches it wrote in the meantime.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        turn = await report_store.get_chat_turn(turn_id)
        if turn is None or turn.status != "running":
            return True
        if _producer_is_gone(turn):
            # Nothing will ever move this record out of "running": the producer
            # died with its process, or was cancelled before its coroutine ever
            # ran and so never reached its terminal cleanup. Waiting for it is
            # waiting forever, and the caller would keep failing while holding a
            # session that cannot start another turn either.
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_TURN_STOP_POLL_SECONDS)


def _producer_is_gone(turn: ChatTurnItem) -> bool:
    """True when nothing is left to finish this turn.

    Two ways that happens. Locally we can be certain: the task is present and
    done, so its cleanup has run or can no longer run. Otherwise the lease is
    the evidence -- a live producer renews it every heartbeat with half the
    retention window of slack, so an expired one is not merely quiet.
    """
    task = _running_producers.get(turn.turn_id)
    if task is not None and task.done():
        return True
    return _expires_within(turn.expires_at, timedelta(0))


async def run_turn_in_process(turn: ChatTurnItem, body: ChatStreamRequest, current: CurrentUser) -> None:
    """Drive one turn to completion, publishing as it goes.

    Detached from the request, so a client that disconnects mid-turn neither
    stops the work nor loses it. What this does *not* survive is the process
    itself going away -- that is what moving the producer onto Temporal buys.
    """
    finish_reason = "stop"
    status = "completed"
    try:
        async with ChatTurnPublisher(turn) as publisher:
            try:
                opening: list[dict[str, Any]] = [
                    # The turn id rides on the opening frame so the client can
                    # address a stop at *this* turn. A stop can be delayed or
                    # retried, and by the time it lands this turn may have
                    # finished and a successor started; naming the thread alone
                    # would stop the wrong one.
                    {
                        "type": "start",
                        "messageId": turn.message_id,
                        "messageMetadata": {"turn_id": turn.turn_id},
                    },
                    {"type": "text-start", "id": turn.text_id},
                ]
                if body.continue_response:
                    opening.append({"type": "text-delta", "id": turn.text_id, "delta": CONTINUATION_MARKDOC})
                await publisher.publish(opening)

                budget_controller = BudgetController(initial_budget_ledger())
                config = build_turn_config(
                    current,
                    body.thread_id,
                    budget_controller=budget_controller,
                    bypass_confirmations=body.bypass_confirmations,
                )
                graph = get_chat_graph()
                async for chunk in graph.astream(
                    build_graph_input(body, budget_controller),
                    config,
                    stream_mode="custom",
                ):
                    if publisher.stopped.is_set():
                        # Stop was pressed, or the conversation was deleted.
                        # Breaking out abandons the rest of the turn: no further
                        # tokens are bought and no queued tool action runs.
                        raise _TurnCanceled
                    if isinstance(chunk, dict) and chunk.get("kind") == "finish_reason":
                        if chunk.get("finish_reason") == "length":
                            finish_reason = "length"
                        continue
                    await publisher.publish(render_parts(chunk, turn.text_id))
            except (_TurnCanceled, asyncio.CancelledError):
                status = "canceled"
                # Clear the pending cancellation before the cleanup awaits.
                # Without this every await below -- publishing the closing
                # frames, flushing, recording the terminal status -- is
                # re-cancelled the moment it suspends, and the turn would be
                # left reading as "running" until its lease lapsed.
                task = asyncio.current_task()
                if task is not None:
                    task.uncancel()
                await publisher.publish([{"type": "text-end", "id": turn.text_id}, *finish_parts("stop")])
            except Exception:
                logger.exception("Chat turn failed", extra={"turn_id": turn.turn_id})
                status = "failed"
                await publisher.publish(
                    [
                        {"type": "text-end", "id": turn.text_id},
                        {"type": "error", "errorText": "Chat stream failed"},
                        *finish_parts("error"),
                    ]
                )
            else:
                await publisher.publish([{"type": "text-end", "id": turn.text_id}, *finish_parts(finish_reason)])
            # Flushed here, not left to __aexit__: last_seq has to name a batch
            # that is already in the store, or a reader would stop short of the
            # final frames while believing it had consumed them all.
            await publisher.flush()
            last_seq = publisher.last_seq
        if await report_store.finish_chat_turn(turn.turn_id, status, last_seq) is None:
            # The turn record vanished mid-flight -- the conversation was
            # deleted. Anything published since then is an orphan whose parent
            # the cascade has already removed, so clear it up rather than leave
            # rows nothing will ever collect.
            await report_store.delete_chat_turn(turn.turn_id)
    except Exception:
        # The log is now unfinishable, so nothing will release the thread until
        # the turn expires. Recorded loudly rather than swallowed.
        logger.exception("Failed to record chat turn completion", extra={"turn_id": turn.turn_id})
    await sweep_expired_turns()


async def sweep_expired_turns() -> None:
    """Collect turn logs whose reconnect window has closed.

    Run at the end of each turn rather than from a scheduler. Deleting a session
    takes its logs with it, but a log belongs to a *turn*, and the turns of a
    conversation nobody deletes would otherwise accumulate for as long as the
    conversation exists. Hanging the sweep off the producer keeps that from
    needing a Temporal worker -- which is optional -- while rate-limiting it to
    chat traffic, and it runs after the turn has finished, so no user waits for
    it. Each pass is bounded; a backlog drains over several turns.
    """
    global _last_sweep_monotonic
    now = time.monotonic()
    if now - _last_sweep_monotonic < settings.CHAT_TURN_SWEEP_INTERVAL_SECONDS:
        # Once per completed turn is far more often than expiry needs, and each
        # pass is a query plus a read per candidate. Pacing it per process keeps
        # the total proportional to time and replicas rather than to chat
        # volume, without needing a shared lease to coordinate.
        return
    _last_sweep_monotonic = now
    try:
        expired = await report_store.list_expired_chat_turns(
            datetime.now(tz=UTC).isoformat(),
            limit=_EXPIRED_TURNS_PER_SWEEP,
        )
        for entry in expired:
            await report_store.delete_chat_turn(entry.turn_id)
    except Exception:
        # Housekeeping. A failure here costs storage, never a turn.
        logger.warning("Failed to sweep expired chat turns", exc_info=True)


async def tail_turn(turn_id: str, after_seq: int = 0) -> AsyncIterator[str]:
    """Yield a turn's event log as SSE, following it until the turn finishes.

    Terminating needs both halves of the store's answer: a terminal status *and*
    a cursor that has reached ``last_seq``. Stopping on the status alone would
    cut the answer off at whatever the last poll happened to see.
    """
    cursor = after_seq
    page_limit = 200
    # Adaptive: a turn is quiet for most of its life -- tool calls, model
    # latency -- and a fixed 200ms poll spends the same reads per viewer during
    # that quiet as it does mid-sentence. Back off while nothing arrives and
    # snap back the moment it does, so responsiveness costs reads only when
    # there is something to be responsive to.
    min_poll = max(settings.CHAT_TURN_POLL_MS, 1) / 1000
    max_poll = max(settings.CHAT_TURN_POLL_MAX_MS, settings.CHAT_TURN_POLL_MS) / 1000
    poll = min_poll
    deadline = time.monotonic() + settings.CHAT_TURN_TAIL_MAX_SECONDS
    while True:
        page = await report_store.read_chat_turn_events(turn_id, cursor, limit=page_limit)
        if page is None:
            # The turn was swept or deleted underneath us. Nothing further is
            # coming, and the client keeps whatever it already received.
            break
        for batch in page.batches:
            for part in json.loads(batch.parts_json):
                yield sse_frame(part)
            cursor = batch.seq
        poll = min_poll if page.batches else min(poll * 2, max_poll)
        turn = page.turn
        if turn.status != "running" and (turn.last_seq is None or cursor >= turn.last_seq):
            break
        if len(page.batches) == page_limit:
            # A full page means there is probably more waiting; a replay of a
            # long turn should not be paced at one page per poll interval.
            continue
        if time.monotonic() >= deadline:
            logger.warning("Chat turn tail exceeded its deadline", extra={"turn_id": turn_id})
            yield sse_frame({"type": "error", "errorText": "Chat stream timed out"})
            yield sse_frame(finish_parts("error")[0])
            break
        await asyncio.sleep(poll)
    yield "data: [DONE]\n\n"
