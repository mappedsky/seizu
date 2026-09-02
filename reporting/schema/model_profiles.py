from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SelectableReasoningEffort = Literal["default", "none", "minimal", "low", "medium", "high", "xhigh"]
ConfiguredReasoningEffort = SelectableReasoningEffort
RESOLVED_MODEL_STAGES = (
    "assistant",
    "planner",
    "worker",
    "worker_summary",
    "sandbox_subagent",
    "synthesizer",
    "router",
    "verifier",
)
PROFILE_STAGES = frozenset(stage for stage in RESOLVED_MODEL_STAGES if stage != "assistant")


class ModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1, max_length=300)


class EconomyModelChoice(ModelChoice):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: ConfiguredReasoningEffort = "default"

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def read_legacy_provider_default(cls, value: Any) -> Any:
        return "default" if value == "" else value


class StageModelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str | None = Field(default=None, min_length=1, max_length=300)
    reasoning_effort: ConfiguredReasoningEffort | None = None

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def read_legacy_provider_default(cls, value: Any) -> Any:
        return "default" if value == "" else value

    @model_validator(mode="before")
    @classmethod
    def read_nested_choice_override(cls, value: Any) -> Any:
        if not isinstance(value, dict) or ("primary" not in value and "economy" not in value):
            return value
        primary = value.get("primary")
        primary = primary if isinstance(primary, dict) else {}
        return {
            "model_id": primary.get("model_id"),
            "reasoning_effort": (
                None if value.get("allow_user_reasoning") is True else primary.get("reasoning_effort")
            ),
        }


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: ModelChoice
    economy: EconomyModelChoice
    stage_overrides: dict[str, StageModelOverride] = Field(default_factory=dict)
    user_reasoning_efforts: tuple[SelectableReasoningEffort, ...] = Field(
        default=("low", "medium", "high"), min_length=1
    )
    default_reasoning_effort: SelectableReasoningEffort = "medium"
    run_cost_budget_usd: float = Field(gt=0, le=10_000)

    @model_validator(mode="before")
    @classmethod
    def read_reasoning_from_legacy_choices(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        primary = normalized.get("primary")
        if isinstance(primary, dict) and "reasoning_effort" in primary:
            primary = dict(primary)
            primary.pop("reasoning_effort", None)
            normalized["primary"] = primary
        return normalized

    @model_validator(mode="after")
    def validate_stage_names(self) -> "ModelProfileConfig":
        unknown = set(self.stage_overrides) - PROFILE_STAGES
        if unknown:
            raise ValueError(f"unknown profile stages: {', '.join(sorted(unknown))}")
        if len(set(self.user_reasoning_efforts)) != len(self.user_reasoning_efforts):
            raise ValueError("user reasoning levels must be unique")
        if self.default_reasoning_effort not in self.user_reasoning_efforts:
            raise ValueError("default user reasoning must be user-selectable")
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
    global_run_cost_budget_usd: float


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
    reasoning_efforts: tuple[SelectableReasoningEffort, ...]
    run_cost_budget_usd: float
    effective_cost_budget_usd: float


class SelectableModelProfilesResponse(BaseModel):
    profiles: list[SelectableModelProfile]
    default_profile_id: str | None = None


_RESOLVED_ROLE_TO_STAGE = {
    "default": "assistant",
    "assistant": "assistant",
    "worker_summary_retry": "worker_summary",
}


class ResolvedModelSpec(BaseModel):
    """One fully expanded model call configuration."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    reasoning_effort: str = ""
    role: str
    profile_id: str = ""
    profile_name: str = ""
    profile_version: int = Field(default=0, ge=0)


class ResolvedStageModels(BaseModel):
    """The normal and budget-degraded choices for one stage."""

    model_config = ConfigDict(extra="forbid")

    primary: ResolvedModelSpec
    economy: ResolvedModelSpec


class ResolvedModelProfile(BaseModel):
    """The complete immutable model and spend choice for one run."""

    source: Literal["environment", "profile"] = "environment"
    profile_id: str | None = None
    profile_name: str | None = None
    profile_version: int | None = None
    reasoning_effort: SelectableReasoningEffort | None = None
    cost_budget_usd: float = 0.0
    stages: dict[str, ResolvedStageModels]

    @model_validator(mode="before")
    @classmethod
    def read_legacy_spec_maps(cls, value: Any) -> Any:
        """Read snapshots written before choices were paired by stage."""
        if not isinstance(value, dict) or "stages" in value:
            return value
        primary = value.get("primary_specs")
        economy = value.get("economy_specs")
        if not isinstance(primary, dict) or not isinstance(economy, dict):
            return value
        legacy_keys = {"default" if stage == "assistant" else stage for stage in RESOLVED_MODEL_STAGES}
        if not legacy_keys.issubset(primary) or not legacy_keys.issubset(economy):
            raise ValueError("legacy resolved model configuration is incomplete")
        normalized = dict(value)
        normalized.pop("primary_specs", None)
        normalized.pop("economy_specs", None)
        normalized["stages"] = {
            stage: {
                "primary": primary["default" if stage == "assistant" else stage],
                "economy": economy["default" if stage == "assistant" else stage],
            }
            for stage in RESOLVED_MODEL_STAGES
        }
        return normalized

    @model_validator(mode="after")
    def require_every_stage(self) -> "ResolvedModelProfile":
        configured = set(self.stages)
        required = set(RESOLVED_MODEL_STAGES)
        if configured != required:
            missing = ", ".join(sorted(required - configured))
            unknown = ", ".join(sorted(configured - required))
            details = "; ".join(
                part
                for part in (
                    f"missing: {missing}" if missing else "",
                    f"unknown: {unknown}" if unknown else "",
                )
                if part
            )
            raise ValueError(f"resolved model stages are incomplete ({details})")
        return self

    def spec_for(self, role: str, *, economy: bool = False) -> ResolvedModelSpec:
        """Return the already-resolved choice for a runtime role."""
        stage = _RESOLVED_ROLE_TO_STAGE.get(role, role)
        configured = self.stages[stage]
        return configured.economy if economy else configured.primary

    def model_spec_payloads(self) -> list[dict[str, object]]:
        """Return every configured model choice for budget capability checks."""
        return [
            spec.model_dump(mode="json") for stage in self.stages.values() for spec in (stage.primary, stage.economy)
        ]
