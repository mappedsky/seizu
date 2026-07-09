from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from reporting.schema.reporting_config import ScheduleSpec

CHAT_THREAD_ID_PATTERN = r"^[0-9]+$"


class ChatStreamRequest(BaseModel):
    # Cap the message so a single turn can't store an unbounded payload in the
    # checkpoint (and, once a model is wired in, can't blow the token budget).
    message: str = Field(default="", max_length=32000)
    thread_id: str = Field(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN)
    resume_confirmation_id: str | None = Field(default=None, min_length=1, max_length=64)
    continue_response: bool = False
    continue_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    # Run the turn with action confirmations bypassed. Requires the
    # chat:bypass_permissions permission (403 otherwise); every bypassed tool
    # execution is audit-logged.
    bypass_confirmations: bool = False

    @model_validator(mode="after")
    def require_message_or_resume(self) -> "ChatStreamRequest":
        if not self.message and not self.resume_confirmation_id and not self.continue_response:
            raise ValueError("message, resume_confirmation_id, or continue_response is required")
        return self


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


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionItem]


class CreateChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)


class UpdateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


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
    # Set by "run now": the worker runs the schedule on its next poll when
    # this is newer than last_scheduled_at (even when the schedule is
    # disabled, so owners can test before enabling).
    run_requested_at: str | None = None


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
