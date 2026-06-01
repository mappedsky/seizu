from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHAT_THREAD_ID_PATTERN = r"^[0-9]+$"


class ChatStreamRequest(BaseModel):
    # Cap the message so a single turn can't store an unbounded payload in the
    # checkpoint (and, once a model is wired in, can't blow the token budget).
    message: str = Field(default="", max_length=32000)
    thread_id: str = Field(min_length=1, max_length=32, pattern=CHAT_THREAD_ID_PATTERN)
    resume_confirmation_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_message_or_resume(self) -> "ChatStreamRequest":
        if not self.message and not self.resume_confirmation_id:
            raise ValueError("message or resume_confirmation_id is required")
        return self


class ChatHistoryMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str


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
