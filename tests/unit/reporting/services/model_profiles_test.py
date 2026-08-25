from reporting.schema.model_profiles import ModelProfileItem
from reporting.services import model_profiles
from reporting.services.chat_models import ModelSpec


def _profile(**updates) -> ModelProfileItem:
    data = {
        "profile_id": "profile-1",
        "name": "Accurate",
        "description": "",
        "enabled": True,
        "is_default": True,
        "primary": {"model_id": "openai/gpt-5", "reasoning_effort": "high"},
        "economy": {"model_id": "openai/gpt-5-mini", "reasoning_effort": "low"},
        "stage_overrides": {"worker_summary": {"primary": {"reasoning_effort": "minimal"}}},
        "run_cost_budget_usd": 1.5,
        "current_version": 3,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "admin",
    }
    data.update(updates)
    return ModelProfileItem.model_validate(data)


async def test_resolve_expands_profile_and_preserves_structural_roles(mocker):
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        mocker.AsyncMock(return_value=[_profile()]),
    )

    def resolve(role, *, economy=False, model_id=None, reasoning_effort=None):
        return ModelSpec(
            model_id=model_id or f"environment/{role}",
            max_output_tokens=10_000,
            reasoning_effort=reasoning_effort or "",
        )

    mocker.patch("reporting.services.model_profiles.chat_models.resolve", side_effect=resolve)
    mocker.patch("reporting.services.model_profiles.settings.CHAT_RUN_COST_BUDGET_USD", 2.0)

    result = await model_profiles.resolve(None)

    assert result.profile_id == "profile-1"
    assert result.profile_version == 3
    assert result.cost_budget_usd == 1.5
    assert result.primary_specs["worker"]["model_id"] == "openai/gpt-5"
    assert result.primary_specs["worker_summary"]["reasoning_effort"] == "minimal"
    assert result.economy_specs["worker"]["model_id"] == "openai/gpt-5-mini"
    assert result.primary_specs["router"]["model_id"] == "environment/router"
    assert result.primary_specs["worker"]["profile_name"] == "Accurate"


async def test_explicit_unavailable_profile_never_falls_back(mocker):
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        mocker.AsyncMock(return_value=[_profile()]),
    )
    try:
        await model_profiles.resolve("deleted-profile")
    except model_profiles.ModelProfileUnavailable as exc:
        assert "no longer available" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("unavailable profile was accepted")


async def test_empty_catalog_uses_environment_snapshot(mocker):
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        mocker.AsyncMock(return_value=[]),
    )
    mocker.patch(
        "reporting.services.model_profiles.chat_models.resolve",
        return_value=ModelSpec(model_id="environment/model", max_output_tokens=1000),
    )

    result = await model_profiles.resolve(None)

    assert result.source == "environment"
    assert result.profile_id is None


def test_global_cost_cap_remains_the_hard_ceiling(mocker):
    mocker.patch("reporting.services.model_profiles.settings.CHAT_RUN_COST_BUDGET_USD", 0.75)

    assert model_profiles.effective_cost_budget(2.0) == 0.75
    assert model_profiles.effective_cost_budget(0.25) == 0.25
