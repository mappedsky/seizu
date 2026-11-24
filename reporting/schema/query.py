from typing import Any

from pydantic import BaseModel, Field, model_validator

from reporting.schema.model_profiles import SelectableReasoningEffort


class QueryRequest(BaseModel):
    query: str
    params: dict[str, Any] | None = None


class ReportQueryRequest(BaseModel):
    token: str
    params: dict[str, Any] | None = None


class QueryResponse(BaseModel):
    results: list[dict[str, Any]]
    errors: list[str] = []
    warnings: list[str] = []
    history_id: str | None = None


class HistoryQueryRequest(BaseModel):
    history_id: str


class ValidationResponse(BaseModel):
    errors: list[str]
    warnings: list[str]


class GraphIndex(BaseModel):
    name: str
    type: str
    entity_type: str
    labels_or_types: list[str]
    properties: list[str]
    state: str


class GraphSchemaResponse(BaseModel):
    labels: list[str]
    relationship_types: list[str]
    property_keys: list[str]
    indexes: list[GraphIndex] = []


class GenerateQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    # What the console editor holds, so a follow-up question can refine it.
    current_query: str = Field(default="", max_length=20_000)
    # The profile and reasoning level to generate with, as a chat turn takes
    # them; omitted means the default profile at its default level.
    model_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    reasoning_effort: SelectableReasoningEffort | None = None

    @model_validator(mode="after")
    def require_profile_with_effort(self) -> "GenerateQueryRequest":
        if self.reasoning_effort is not None and self.model_profile_id is None:
            raise ValueError("model_profile_id is required with reasoning_effort")
        return self


class GenerateQueryResponse(BaseModel):
    query: str
    explanation: str
    errors: list[str] = []
    warnings: list[str] = []
    # Set when the query validated and was recorded in the caller's history,
    # carrying the question as its prompt; the console runs it by this id.
    history_id: str | None = None
    # What generated it: the model, the reasoning effort sent (empty when the
    # provider default applied), and the model profile's name (empty when the
    # deployment has none).
    model_id: str = ""
    reasoning_effort: str = ""
    profile_name: str = ""
