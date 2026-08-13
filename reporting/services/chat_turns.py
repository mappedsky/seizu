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
stream route and the reconnecting one.

The producer is a Temporal workflow (``seizu_chat_turn``), so a turn outlives
both the request that asked for it and the web process that served it.
:func:`start_turn` admits the turn and hands it over; nothing here waits for it.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from temporalio.exceptions import WorkflowAlreadyStartedError

from reporting import settings
from reporting.authnz import CurrentUser
from reporting.schema.chat import (
    CHAT_TURN_MAX_BATCH_BYTES,
    ChatTurnAdmission,
    ChatTurnItem,
    ChatTurnRequest,
)
from reporting.services import report_store, schedule_reconciler
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

# How often a caller waiting for a turn to stop re-reads it.
_TURN_STOP_POLL_SECONDS = 0.05


# When this process last swept expired logs. Paced rather than run on every
# completed turn; see :func:`sweep_expired_turns`.
_last_sweep_monotonic = 0.0


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
        self._cancel_watch: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def stopped(self) -> asyncio.Event:
        """Set when the turn should stop: cancelled, or its record is gone."""
        return self._stopped

    async def __aenter__(self) -> "ChatTurnPublisher":
        # A periodic flusher rather than flushing only on append: a turn that
        # goes quiet mid-tool-call would otherwise leave its last partial batch
        # sitting in memory, and the viewer would see the stream stall.
        self._flusher = asyncio.create_task(self._flush_loop())
        self._cancel_watch = asyncio.create_task(self._cancel_watch_loop())
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for task in (self._flusher, self._cancel_watch):
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

    async def _cancel_watch_loop(self) -> None:
        """Watch for a stop that arrived while the turn is quiet.

        A turn is most likely to be stopped precisely while it is blocked on a
        slow model call or tool, where no chunk arrives for as long as the call
        takes. Temporal cancels the activity when the workflow is cancelled,
        which covers the ordinary path; this covers a stop recorded on the turn
        without one -- and it is why the flag exists at all.
        """
        interval = max(settings.CHAT_TURN_HEARTBEAT_SECONDS, 1)
        while True:
            await asyncio.sleep(interval)
            try:
                turn = await report_store.get_chat_turn(self._turn.turn_id)
                if turn is None or turn.cancel_requested or turn.status != "running":
                    self._stop_producer()
                    return
            except Exception:
                # A transient store failure must not stop a healthy turn.
                logger.warning("Chat turn cancel-watch failed", extra={"turn_id": self._turn.turn_id}, exc_info=True)

    def _stop_producer(self) -> None:
        """Ask the turn to stop.

        Only sets the event. The supervisor owns cancellation and does it
        exactly once, which is what removes the "a second cancel interrupts the
        first one's cleanup" problem rather than guarding against it.
        """
        self._stopped.set()

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


def build_graph_input(body: ChatTurnRequest, budget_controller: BudgetController) -> ChatState:
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


async def start_turn(thread_id: str, body: ChatTurnRequest, current: CurrentUser) -> ChatTurnAdmission:
    """Admit a turn, and hand it to the worker that will produce it.

    The store decides and reports which of ``created``, ``existing``, ``busy``
    or ``retired`` happened. Both ``created`` and ``existing`` then *ensure* the
    turn's workflow, which is what makes a retry able to repair a handoff that
    failed after the turn was already committed; the workflow id is derived from
    the turn id, so ensuring it twice is a no-op rather than a second producer.
    """
    # Reusing the client's message id for a continuation is what makes the
    # continued text land in the same assistant message rather than a new one.
    message_id = (
        body.continue_message_id if body.continue_response and body.continue_message_id else f"msg_{uuid.uuid4().hex}"
    )
    admission = await report_store.admit_chat_turn(
        current.user.user_id,
        thread_id,
        message_id,
        f"text_{uuid.uuid4().hex}",
        body.idempotency_key,
    )
    if admission.turn is None:
        return admission
    # `existing` reaches here too, and deliberately. The store commits the turn
    # before this call, so a handoff that fails leaves a turn recorded as
    # running with nothing producing it -- unreadable, and holding the thread
    # against a successor until its lease lapses. Retrying the request resolves
    # to that same turn, so this is the only place that can repair it.
    #
    # Only while it is still running, though: a repeat that arrives after the
    # turn finished must not start a second workflow over a completed one and
    # bill the answer twice.
    if admission.turn.status != "running":
        return admission

    from reporting.temporal_workflows.chat_turn import workflow_id_for
    from reporting.temporal_workflows.shared import ChatTurnInvocation

    client = await schedule_reconciler.get_client()
    try:
        await client.start_workflow(
            "seizu_chat_turn",
            ChatTurnInvocation(
                turn_id=admission.turn.turn_id,
                thread_id=thread_id,
                user_id=current.user.user_id,
                message=body.message,
                resume_confirmation_id=body.resume_confirmation_id,
                continue_response=body.continue_response,
                bypass_confirmations=body.bypass_confirmations,
                # The turn can never exceed these; the worker intersects them with
                # what the stored user has now.
                permissions=sorted(current.permissions),
                timeout_seconds=settings.CHAT_TURN_TIMEOUT_SECONDS,
            ),
            # Derived from the turn id, which is what makes this repeatable:
            # the second attempt names the same workflow as the first.
            id=workflow_id_for(admission.turn.turn_id),
            task_queue=settings.TEMPORAL_TASK_QUEUE,
        )
    except WorkflowAlreadyStartedError:
        # The first attempt got further than it managed to report. That is the
        # outcome we wanted, not a collision.
        logger.info(
            "Chat turn workflow was already started",
            extra={"turn_id": admission.turn.turn_id},
        )
    return admission


async def cancel_turn(turn_id: str) -> None:
    """Cancel the workflow producing a turn.

    Idempotent by construction: cancelling an already-finished workflow is not
    an error, and Temporal collapses repeats. That is what replaced the
    first-writer-wins guard the detached task needed -- there is no longer a
    second canceller to race.
    """
    from reporting.temporal_workflows.chat_turn import workflow_id_for

    try:
        client = await schedule_reconciler.get_client()
        await client.get_workflow_handle(workflow_id_for(turn_id)).cancel()
    except Exception:
        # The record already carries the stop; the producer reads it on its
        # next watch tick even if this did not land.
        logger.warning("Could not cancel the workflow for a chat turn", extra={"turn_id": turn_id}, exc_info=True)


async def produce_turn(
    turn: ChatTurnItem,
    thread_id: str,
    body: ChatTurnRequest,
    current: CurrentUser,
    on_progress: Callable[[], None] | None = None,
) -> tuple[str, int]:
    """Drive one turn to completion, publishing as it goes.

    A supervisor: the graph runs in a child task, and this cancels that child
    at most once, then always publishes the closing frames and records the
    terminal state. Nothing outside cancels *this*, so the cleanup cannot be
    interrupted part-way -- which is what the detached-task version needed
    ``uncancel()``, a first-writer-wins guard and an abandoned-task finalizer to
    approximate.
    """
    finish_reason = "stop"
    status = "completed"
    async with ChatTurnPublisher(turn) as publisher:
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
            thread_id,
            budget_controller=budget_controller,
            bypass_confirmations=body.bypass_confirmations,
        )

        async def _drive() -> None:
            nonlocal finish_reason
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
                if on_progress is not None:
                    on_progress()

        child = asyncio.create_task(_drive())
        stopped = asyncio.create_task(publisher.stopped.wait())
        try:
            done, _ = await asyncio.wait({child, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if child in done:
                child.result()
            else:
                # Asked to stop. Cancelling the child interrupts it wherever it
                # is, including mid-model-call, so a queued tool action does not
                # get to run first.
                status = "canceled"
        except asyncio.CancelledError:
            # Temporal cancelled the activity. Same outcome, different messenger.
            status = "canceled"
        except Exception:
            logger.exception("Chat turn failed", extra={"turn_id": turn.turn_id})
            status = "failed"
        finally:
            stopped.cancel()
            if not child.done():
                child.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await child

        if status == "failed":
            await publisher.publish(
                [
                    {"type": "text-end", "id": turn.text_id},
                    {"type": "error", "errorText": "Chat stream failed"},
                    *finish_parts("error"),
                ]
            )
        else:
            await publisher.publish(
                [
                    {"type": "text-end", "id": turn.text_id},
                    *finish_parts("stop" if status == "canceled" else finish_reason),
                ]
            )
        # Flushed here rather than left to __aexit__: last_seq has to name a
        # batch that is already stored, or a reader stops short of the final
        # frames believing it has them all.
        await publisher.flush()
        last_seq = publisher.last_seq

    await report_store.finish_chat_turn(turn.turn_id, status, last_seq)
    await sweep_expired_turns()
    return status, last_seq


async def await_turn_stopped(turn_id: str, timeout_seconds: float) -> bool:
    """Wait for a turn to reach a terminal state, or give up.

    A caller about to delete what a turn is writing into has to know it has
    actually stopped, not just been asked to. Simply polling for terminal is
    enough now that the workflow owns the turn: Temporal guarantees it ends,
    where a detached task could be gone with no one left to say so -- which is
    why this used to have to infer death from a lapsed lease.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        turn = await report_store.get_chat_turn(turn_id)
        if turn is None or turn.status != "running":
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(_TURN_STOP_POLL_SECONDS)


async def sweep_expired_turns() -> None:
    """Collect turn logs whose reconnect window has closed.

    Run at the end of each turn rather than from a scheduler. Deleting a session
    takes its logs with it, but a log belongs to a *turn*, and the turns of a
    conversation nobody deletes would otherwise accumulate for as long as the
    conversation exists. Hanging it off the producer rate-limits it to chat
    traffic and needs no schedule of its own, and it runs after the turn has
    finished, so no user waits for it. Each pass is bounded; a backlog drains
    over several turns.
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
