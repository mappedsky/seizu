import pytest

from reporting.schema.model_profiles import ModelProfileItem, ResolvedModelProfile
from reporting.services import model_profiles
from reporting.services.chat_models import ModelSpec


def _profile(**updates) -> ModelProfileItem:
    data = {
        "profile_id": "profile-1",
        "name": "Accurate",
        "description": "",
        "enabled": True,
        "is_default": True,
        "primary": {"model_id": "openai/gpt-5"},
        "economy": {"model_id": "openai/gpt-5-mini", "reasoning_effort": "low"},
        "stage_overrides": {
            "router": {"model_id": "openai/gpt-5-router", "reasoning_effort": "none"},
            "worker": {"model_id": "openai/gpt-5-worker"},
            "worker_summary": {"reasoning_effort": "minimal"},
            "verifier": {"model_id": "openai/gpt-5-verifier"},
        },
        "default_reasoning_effort": "medium",
        "run_cost_budget_usd": 1.5,
        "current_version": 3,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "admin",
    }
    data.update(updates)
    return ModelProfileItem.model_validate(data)


def test_profile_default_reasoning_must_be_user_selectable():
    with pytest.raises(ValueError, match="default user reasoning must be user-selectable"):
        _profile(user_reasoning_efforts=("low", "high"))


def test_profile_rejects_an_assistant_override_because_primary_is_the_assistant_model():
    with pytest.raises(ValueError, match="unknown profile stages: assistant"):
        _profile(stage_overrides={"assistant": {"model_id": "openai/other"}})


def test_a_resolved_profile_requires_every_runtime_stage():
    with pytest.raises(ValueError, match="resolved model stages are incomplete"):
        ResolvedModelProfile(source="environment", stages={})


def test_runtime_model_access_requires_a_captured_configuration():
    with pytest.raises(RuntimeError, match="outside a resolved run configuration"):
        model_profiles.require_current_spec("worker")


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

    mocker.patch("reporting.services.model_profiles.chat_models.resolve_environment", side_effect=resolve)
    mocker.patch("reporting.services.model_profiles.settings.CHAT_RUN_COST_BUDGET_USD", 2.0)

    result = await model_profiles.resolve(None)

    assert result.profile_id == "profile-1"
    assert result.profile_version == 3
    assert result.cost_budget_usd == 1.5
    assert result.spec_for("worker").model_id == "openai/gpt-5-worker"
    assert result.spec_for("worker").reasoning_effort == "medium"
    assert result.spec_for("worker_summary").reasoning_effort == "minimal"
    assert result.spec_for("worker_summary", economy=True).reasoning_effort == "low"
    assert result.spec_for("worker", economy=True).model_id == "openai/gpt-5-mini"
    assert result.spec_for("router").model_id == "openai/gpt-5-router"
    assert result.spec_for("router").reasoning_effort == "none"
    assert result.spec_for("verifier").model_id == "openai/gpt-5-verifier"
    assert result.spec_for("planner").model_id == "openai/gpt-5"
    assert result.spec_for("assistant").model_id == "openai/gpt-5"
    assert result.spec_for("worker").profile_name == "Accurate"
    with model_profiles.use(result):
        assert model_profiles.current_spec("assistant") == model_profiles.current_spec("default")

    high = await model_profiles.resolve("profile-1", "high")
    assert high.reasoning_effort == "high"
    assert high.spec_for("planner").reasoning_effort == "high"
    assert high.spec_for("synthesizer", economy=True).reasoning_effort == "low"
    assert high.spec_for("worker_summary").reasoning_effort == "minimal"


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
        "reporting.services.model_profiles.chat_models.resolve_environment",
        return_value=ModelSpec(model_id="environment/model", max_output_tokens=1000),
    )

    result = await model_profiles.resolve(None)

    assert result.source == "environment"
    assert result.profile_id is None


async def test_selectable_profiles_expose_only_the_profiles_allowed_efforts(mocker):
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        mocker.AsyncMock(return_value=[_profile()]),
    )

    [profile] = await model_profiles.selectable_profiles()

    assert profile.reasoning_efforts == ("low", "medium", "high")


async def test_profile_rejects_a_reasoning_level_the_admin_did_not_offer(mocker):
    mocker.patch(
        "reporting.services.model_profiles.report_store.list_model_profiles",
        mocker.AsyncMock(return_value=[_profile(user_reasoning_efforts=("low", "medium"))]),
    )

    try:
        await model_profiles.resolve("profile-1", "high")
    except model_profiles.ModelProfileUnavailable as exc:
        assert "reasoning level is not available" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("an unavailable reasoning level was accepted")


def test_global_cost_cap_remains_the_hard_ceiling(mocker):
    mocker.patch("reporting.services.model_profiles.settings.CHAT_RUN_COST_BUDGET_USD", 0.75)

    assert model_profiles.effective_cost_budget(2.0) == 0.75
    assert model_profiles.effective_cost_budget(0.25) == 0.25
