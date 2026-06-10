from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ConfirmationSource = Literal["mcp", "chat"]
ConfirmationStatus = Literal["pending", "approved", "denied", "expired", "executed"]
ConfirmationDecision = Literal["approved", "denied"]


class ActionConfirmationTarget(BaseModel):
    """Action/resource tuple used for confirmation grant scope."""

    action: str
    resource_type: str
    resource_id: str


class ActionConfirmation(BaseModel):
    """Server-side record for a pending or decided action confirmation."""

    confirmation_id: str
    user_id: str
    source: ConfirmationSource
    session_key: str
    tool_name: str
    action: str
    resource_type: str
    resource_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_hash: str = ""
    status: ConfirmationStatus
    batch_id: str | None = None
    created_at: str
    expires_at: str
    decided_at: str | None = None
    decided_by: str | None = None


class ActionConfirmationPublic(BaseModel):
    """Redacted confirmation record safe to send to browser clients."""

    confirmation_id: str
    source: ConfirmationSource
    tool_name: str
    action: str
    resource_type: str
    resource_id: str
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments shown to the user. These must not contain secrets or highly sensitive data.",
    )
    status: ConfirmationStatus
    batch_id: str | None = None
    thread_id: str | None = None
    created_at: str
    expires_at: str
    decided_at: str | None = None

    @classmethod
    def from_confirmation(cls, confirmation: ActionConfirmation) -> "ActionConfirmationPublic":
        data = confirmation.model_dump(exclude={"user_id", "session_key", "arguments_hash", "decided_by"})
        if confirmation.source == "chat":
            data["thread_id"] = confirmation.session_key
        return cls.model_validate(data)


class ConfirmationListResponse(BaseModel):
    confirmations: list[ActionConfirmationPublic]


class ConfirmationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: ConfirmationDecision


class ConfirmationResponse(BaseModel):
    confirmation: ActionConfirmationPublic
