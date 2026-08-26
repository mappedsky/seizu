from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SelectableReasoningEffort = Literal["low", "medium", "high"]
PROFILE_STAGES = frozenset(
    {
        "assistant",
        "planner",
        "worker",
        "worker_summary",
        "sandbox_subagent",
        "synthesizer",
    }
)


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=300)


class ModelChoiceOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=300)


class StageModelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: ModelChoiceOverride | None = None
    economy: ModelChoiceOverride | None = None


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: ModelChoice
    economy: ModelChoice
    stage_overrides: dict[str, StageModelOverride] = Field(default_factory=dict)
    default_reasoning_effort: SelectableReasoningEffort = "medium"
    run_cost_budget_usd: float = Field(gt=0, le=10_000)

    @model_validator(mode="after")
    def validate_stage_names(self) -> "ModelProfileConfig":
        unknown = set(self.stage_overrides) - PROFILE_STAGES
        if unknown:
            raise ValueError(f"unknown profile stages: {', '.join(sorted(unknown))}")
        return self


class CreateModelProfileRequest(ModelProfileConfig):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    enabled: bool = True
    is_default: bool = False


class UpdateModelProfileRequest(CreateModelProfileRequest):
    comment: str | None = Field(default=None, max_length=500)


class ModelProfileItem(CreateModelProfileRequest):
    profile_id: str
    current_version: int
    created_at: str
    updated_at: str
    created_by: str
    updated_by: str | None = None


class ModelProfileVersion(ModelProfileConfig):
    profile_id: str
    version: int
    name: str
    description: str = ""
    enabled: bool
    is_default: bool
    created_at: str
    created_by: str
    comment: str | None = None


class ModelProfileListResponse(BaseModel):
    profiles: list[ModelProfileItem]


class ModelProfileVersionListResponse(BaseModel):
    versions: list[ModelProfileVersion]


class ModelProfileIdResponse(BaseModel):
    profile_id: str


class SelectableModelProfile(BaseModel):
    profile_id: str
    name: str
    description: str
    is_default: bool
    default_reasoning_effort: SelectableReasoningEffort
    reasoning_efforts: tuple[SelectableReasoningEffort, ...] = ("low", "medium", "high")
    run_cost_budget_usd: float
    effective_cost_budget_usd: float


class SelectableModelProfilesResponse(BaseModel):
    profiles: list[SelectableModelProfile]
    default_profile_id: str | None = None


class ResolvedModelProfile(BaseModel):
    """The complete immutable model and spend choice for one run."""

    source: Literal["environment", "profile"] = "environment"
    profile_id: str | None = None
    profile_name: str | None = None
    profile_version: int | None = None
    reasoning_effort: SelectableReasoningEffort | None = None
    cost_budget_usd: float = 0.0
    primary_specs: dict[str, dict[str, object]] = Field(default_factory=dict)
    economy_specs: dict[str, dict[str, object]] = Field(default_factory=dict)
