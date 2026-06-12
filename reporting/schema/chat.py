from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    # "interactive" sessions appear in the user's chat session list;
    # "scheduled" sessions are created by scheduled chat runs, are hidden from
    # that list, and are read-only in the web UI (viewable from the Scheduled
    # Chats page).
    origin: Literal["interactive", "scheduled"] = "interactive"
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


class ChatScheduleSpec(BaseModel):
    """When a scheduled chat runs. All times are UTC.

    - ``hourly``: every ``interval_hours`` hours, anchored to the last run
      (a new schedule runs immediately).
    - ``daily``: on the selected ``days_of_week`` (0=Monday..6=Sunday) at
      ``hour``.
    - ``monthly``: on the selected ``days_of_month`` (1-31) at 00:00. A day a
      month doesn't have runs on that month's last day instead (e.g. 31 in
      April runs on the 30th).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["hourly", "daily", "monthly"]
    interval_hours: int | None = Field(default=None, ge=1, le=720)
    days_of_week: list[int] = Field(default_factory=list)
    hour: int = Field(default=0, ge=0, le=23)
    days_of_month: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_type_fields(self) -> "ChatScheduleSpec":
        if self.type == "hourly" and not self.interval_hours:
            raise ValueError("interval_hours is required for hourly schedules")
        if self.type == "daily":
            if not self.days_of_week:
                raise ValueError("days_of_week is required for daily schedules")
            if any(day < 0 or day > 6 for day in self.days_of_week):
                raise ValueError("days_of_week values must be 0 (Monday) through 6 (Sunday)")
        if self.type == "monthly":
            if not self.days_of_month:
                raise ValueError("days_of_month is required for monthly schedules")
            if any(day < 1 or day > 31 for day in self.days_of_month):
                raise ValueError("days_of_month values must be 1 through 31")
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
        if not self.schedule and not self.watch_scans:
            raise ValueError("schedule or watch_scans is required")
        return self


class ScheduledChatsResponse(BaseModel):
    schedules: list[ScheduledChatItem]


class ScheduledChatVersionListResponse(BaseModel):
    versions: list[ScheduledChatVersion]
