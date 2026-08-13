from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reporting.schema.reporting_config import ScheduleSpec

CHAT_THREAD_ID_PATTERN = r"^[0-9]+$"


class ChatTurnRequest(BaseModel):
    # Cap the message so a single turn can't store an unbounded payload in the
    # checkpoint (and, once a model is wired in, can't blow the token budget).
    message: str = Field(default="", max_length=32000)
    resume_confirmation_id: str | None = Field(default=None, min_length=1, max_length=64)
    continue_response: bool = False
    continue_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Client-minted key making admission idempotent. A repeat of this request
    # returns the turn it already admitted rather than starting a second one,
    # which is how a lost response is resolved -- by asking again, not by
    # racing a different kind of write.
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)
    # Run the turn with action confirmations bypassed. Requires the
    # chat:bypass_permissions permission (403 otherwise); every bypassed tool
    # execution is audit-logged.
    bypass_confirmations: bool = False

    @model_validator(mode="after")
    def require_message_or_resume(self) -> "ChatTurnRequest":
        if not self.message and not self.resume_confirmation_id and not self.continue_response:
            raise ValueError("message, resume_confirmation_id, or continue_response is required")
        return self


class ChatTurnAdmissionResponse(BaseModel):
    """What admission did, and the turn to attach to."""

    turn_id: str
    # "created" started a turn; "existing" resolved a repeat of the same
    # request to the turn it already made.
    status: Literal["created", "existing"]


class ChatHistoryMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    metadata: dict[str, object] | None = None


class ChatHistoryResponse(BaseModel):
    messages: list[ChatHistoryMessage]


class ChatSessionItem(BaseModel):
    thread_id: str
    title: str
    created_at: str
    updated_at: str
    # "interactive" sessions appear in the user's chat session list. Headless
    # "scheduled" and "workflow" sessions are hidden there and read-only.
    origin: Literal["interactive", "scheduled", "workflow"] = "interactive"
    scheduled_chat_id: str | None = None
    run_status: str | None = None
    run_errors: list[str] = Field(default_factory=list)


class IdleChatSession(BaseModel):
    """A session the reaper may collect, with the owner needed to delete it.

    Carries ``user_id`` because every other session read is already scoped to a
    user, while a sweep starts from no user at all -- and the id is what the
    thread's checkpoint namespace and its sandbox are keyed by.
    """

    user_id: str
    thread_id: str
    updated_at: str


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionItem]


class CreateChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)


class UpdateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


# A batch has to fit one DynamoDB item (400KB hard limit), with room left for the
# keys and the rest of the item. The producer splits rather than the store, so
# this is a validation bound, not a chunking hint.
CHAT_TURN_MAX_BATCH_BYTES = 320_000
# Ceiling on batches per turn, so a runaway producer cannot write without bound.
CHAT_TURN_MAX_SEQ = 5_000


class ChatTurnAdmission(BaseModel):
    """What happened when a turn was asked for, and the turn if there is one.

    An outcome rather than an exception per outcome. Admission used to raise
    four different errors, each inferred after the fact from whichever database
    constraint happened to reject the write -- so a single uniqueness collision
    could mean "duplicate request", "a stop got here first", "another turn is
    running" or "the conversation is being deleted", and both backends read
    mutable state afterwards to guess which. The store knows which it did; this
    is it saying so.
    """

    # created  -- a new turn, admitted and reserved.
    # existing -- this exact request was already admitted; here is that turn.
    #             Never a cancellation: a repeat of a request is a repeat.
    # busy     -- another turn holds the thread.
    # retired  -- the conversation is gone or being deleted.
    outcome: Literal["created", "existing", "busy", "retired", "mismatched"]
    turn: "ChatTurnItem | None" = None


class ChatTurnItem(BaseModel):
    """The header of one in-flight chat turn's replayable event log.

    Ephemeral: retained only long enough for a dropped SSE connection to come
    back, then swept by ``expires_at``. ``message_id``/``text_id`` live here
    rather than being minted per delivery because a replay has to reproduce the
    ids the first delivery used -- a fresh id reads to the client as a second
    assistant message rather than the same one.
    """

    turn_id: str
    thread_id: str = Field(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN)
    user_id: str
    message_id: str = Field(min_length=1, max_length=128)
    text_id: str = Field(min_length=1, max_length=128)
    # The key the admitting request carried, if any. Kept so a repeat of that
    # request resolves to this turn. It is *not* a way to address the turn:
    # everything acting on a turn names its ``turn_id``, which the client has
    # from the moment admission returns.
    idempotency_key: str | None = None
    # Fingerprint of the request admitted under that key. A repeat carrying the
    # same key but a different fingerprint is refused rather than resolving
    # here: the key names a turn that may already be running, so honouring the
    # new body would change what that turn executes.
    request_hash: str | None = None
    status: Literal["running", "completed", "failed", "canceled"] = "running"
    # None until the turn finishes. A reader may stop only once the status is
    # terminal *and* it has consumed through last_seq: a terminal status on its
    # own races the visibility of the final batches.
    last_seq: int | None = None
    # Set by Stop, and by deleting the conversation. The producer may be in
    # another process, so it is asked through the record rather than signalled
    # directly; it checks on its heartbeat and stops.
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    # Two meanings, one field. While the turn runs this is its claim on the
    # thread, derived from the turn's own timeout so it always outlasts every
    # way the turn can still legitimately be running -- admission retires a
    # lapsed one, and retiring a live turn would put two producers on one
    # conversation. Once the turn ends it is re-stamped as the reconnect window,
    # which is far shorter.
    expires_at: str


ChatTurnAdmission.model_rebuild()


class ChatTurnEventBatch(BaseModel):
    """One flush: the exact JSON array text the live stream sent."""

    seq: int = Field(ge=1, le=CHAT_TURN_MAX_SEQ)
    parts_json: str


class ChatTurnEventPage(BaseModel):
    """A tail read: the turn's current state plus the batches after a cursor."""

    turn: ChatTurnItem
    batches: list[ChatTurnEventBatch] = Field(default_factory=list)


class ExpiredChatTurn(BaseModel):
    """A turn the sweeper may collect, with the owner needed to scope the delete."""

    turn_id: str
    user_id: str
    thread_id: str
    expires_at: str


class ChatScheduleSpec(ScheduleSpec):
    """When a scheduled chat runs: a ``ScheduleSpec`` limited to hourly
    granularity (no ``interval`` type and no minute-of-hour offset)."""

    type: Literal["hourly", "daily", "monthly"]

    @model_validator(mode="after")
    def require_hourly_granularity(self) -> "ChatScheduleSpec":
        if self.minute != 0:
            raise ValueError("scheduled chats do not support minute-of-hour offsets")
        return self


class ScheduledChatItem(BaseModel):
    """A scheduled chat record: a recurring headless agent run owned by a user.

    The worker runs the prompt as the owner; each run creates a regular chat
    session in the owner's session list.
    """

    scheduled_chat_id: str
    name: str
    prompt: str
    # When to run (hourly/daily/monthly), or None when watch_scans is used.
    schedule: ChatScheduleSpec | None = None
    # SyncMetadata filters (same shape as scheduled query watch_scans): run
    # when a matching Cartography scan completes after the last run.
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    current_version: int = 0
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default_factory=list)
    last_scheduled_at: str | None = None
    # Set by "run now"; also consumed by schedule reconciliation to recover a
    # request whose immediate start failed. A run-now runs even when the
    # schedule is disabled, so owners can test before enabling.
    run_requested_at: str | None = None
    # Temporal Schedule reconciliation state, mirroring ScheduledQueryItem.
    schedule_sync_status: Literal["synced", "pending", "error"] = "pending"
    schedule_sync_error: str | None = None
    schedule_synced_at: str | None = None


class ScheduledChatVersion(BaseModel):
    """A point-in-time snapshot of a scheduled chat's configuration."""

    scheduled_chat_id: str
    version: int
    name: str
    prompt: str
    schedule: ChatScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    created_at: str
    created_by: str
    comment: str | None = None


class CreateScheduledChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=32000)
    schedule: ChatScheduleSpec | None = None
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    # Version comment; only meaningful on update.
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_trigger(self) -> "CreateScheduledChatRequest":
        has_schedule = self.schedule is not None
        has_watch_scans = bool(self.watch_scans)
        if has_schedule == has_watch_scans:
            raise ValueError("exactly one of schedule or watch_scans is required")
        return self


class ScheduledChatsResponse(BaseModel):
    schedules: list[ScheduledChatItem]


class ScheduledChatRunRequestedResponse(BaseModel):
    """Acknowledgement that a manual run was requested."""

    scheduled_chat_id: str
    run_requested_at: str


class ScheduledChatVersionListResponse(BaseModel):
    versions: list[ScheduledChatVersion]
