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


class ChatSessionsResponse(BaseModel):
    sessions: list[ChatSessionItem]


class CreateChatSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=200)


class UpdateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ScheduledChatItem(BaseModel):
    """A scheduled chat record: a recurring headless agent run owned by a user.

    The worker runs the prompt as the owner; each run creates a regular chat
    session in the owner's session list.
    """

    scheduled_chat_id: str
    name: str
    prompt: str
    # Minutes between runs (frequency trigger), or None when watch_scans is used.
    frequency: int | None = None
    # SyncMetadata filters (same shape as scheduled query watch_scans): run
    # when a matching Cartography scan completes after the last run.
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    created_at: str
    updated_at: str
    created_by: str
    last_run_status: str | None = None
    last_run_at: str | None = None
    last_errors: list[dict[str, str]] = Field(default_factory=list)
    last_scheduled_at: str | None = None


class CreateScheduledChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=32000)
    frequency: int | None = Field(default=None, ge=1)
    watch_scans: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def require_trigger(self) -> "CreateScheduledChatRequest":
        if not self.frequency and not self.watch_scans:
            raise ValueError("frequency or watch_scans is required")
        return self


class ScheduledChatsResponse(BaseModel):
    schedules: list[ScheduledChatItem]
