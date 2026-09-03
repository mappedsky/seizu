import pytest
from pydantic import ValidationError

from reporting.schema.model_profiles import CreateModelProfileRequest
from reporting.schema.reporting_config import ReportingConfig


def _profile(**updates):
    value = {
        "name": "Balanced",
        "is_default": True,
        "primary": {"model_id": "anthropic/claude-sonnet-4-6"},
        "economy": {"model_id": "openai/gpt-5-mini", "reasoning_effort": "low"},
        "stage_overrides": {
            "planner": {"reasoning_effort": "high"},
            "worker_summary": {"model_id": "openai/gpt-5-mini", "reasoning_effort": "none"},
        },
        "user_reasoning_efforts": ["low", "high"],
        "default_reasoning_effort": "high",
        "run_cost_budget_usd": 2.5,
    }
    value.update(updates)
    return value


def test_reporting_config_accepts_model_profiles() -> None:
    config = ReportingConfig.model_validate({"model_profiles": {"balanced": _profile()}})

    assert config.model_profiles["balanced"].stage_overrides["planner"].reasoning_effort == "high"


def test_yaml_profile_carries_every_create_request_field() -> None:
    config = ReportingConfig.model_validate({"model_profiles": {"balanced": _profile()}})

    assert set(type(config.model_profiles["balanced"]).model_fields) == set(CreateModelProfileRequest.model_fields)


@pytest.mark.parametrize(
    "profiles",
    [
        {"balanced": _profile(is_default=False)},
        {"one": _profile(name="One"), "two": _profile(name="Two")},
        {"balanced": _profile(enabled=False)},
    ],
)
def test_reporting_config_requires_one_enabled_default(profiles) -> None:
    with pytest.raises(ValidationError, match="default model profile|enabled model_profiles"):
        ReportingConfig.model_validate({"model_profiles": profiles})


def test_reporting_config_allows_only_disabled_profiles_without_a_default() -> None:
    config = ReportingConfig.model_validate({"model_profiles": {"paused": _profile(enabled=False, is_default=False)}})

    assert not config.model_profiles["paused"].enabled


def test_reporting_config_rejects_unknown_profile_stage() -> None:
    with pytest.raises(ValidationError, match="unknown profile stages: assistant"):
        ReportingConfig.model_validate(
            {"model_profiles": {"balanced": _profile(stage_overrides={"assistant": {"model_id": "x"}})}}
        )
