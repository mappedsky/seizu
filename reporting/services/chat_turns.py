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
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.schema.chat import CHAT_TURN_MAX_BATCH_BYTES, ChatStreamRequest, ChatTurnItem
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

# Detached producer tasks, held so the event loop keeps a strong reference. A
# task nobody holds can be garbage collected mid-run, which would look exactly
# like the failure this module exists to remove.
_running_producers: set[asyncio.Task[None]] = set()


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

    @property
    def last_seq(self) -> int:
        return self._seq

    async def __aenter__(self) -> "ChatTurnPublisher":
        # A periodic flusher rather than flushing only on append: a turn that
        # goes quiet mid-tool-call would otherwise leave its last partial batch
        # sitting in memory, and the viewer would see the stream stall.
        self._flusher = asyncio.create_task(self._flush_loop())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._flusher is not None:
            self._flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flusher
        await self.flush()

    async def _flush_loop(self) -> None:
        interval = max(settings.CHAT_TURN_FLUSH_MS, 1) / 1000
        while True:
            await asyncio.sleep(interval)
            await self.flush()

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
    turn = await report_store.create_chat_turn(
        current.user.user_id,
        body.thread_id,
        message_id,
        f"text_{uuid.uuid4().hex}",
    )
    task = asyncio.create_task(run_turn_in_process(turn, body, current))
    _running_producers.add(task)
    task.add_done_callback(_running_producers.discard)
    return turn


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
                    {"type": "start", "messageId": turn.message_id},
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
                    if isinstance(chunk, dict) and chunk.get("kind") == "finish_reason":
                        if chunk.get("finish_reason") == "length":
                            finish_reason = "length"
                        continue
                    await publisher.publish(render_parts(chunk, turn.text_id))
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
        await report_store.finish_chat_turn(turn.turn_id, status, last_seq)
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
    poll = max(settings.CHAT_TURN_POLL_MS, 1) / 1000
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
