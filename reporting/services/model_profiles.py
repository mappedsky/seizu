from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from reporting import settings
from reporting.schema.model_profiles import (
    RESOLVED_MODEL_STAGES,
    ModelProfileItem,
    ResolvedModelProfile,
    ResolvedStageModels,
    SelectableModelProfile,
    SelectableReasoningEffort,
)
from reporting.services import chat_models, report_store


class ModelProfileUnavailable(LookupError):
    """A selected model profile is missing or disabled."""


_current_profile: ContextVar[ResolvedModelProfile | None] = ContextVar("chat_model_profile", default=None)

_PROFILE_STAGES = frozenset({"assistant", "planner", "worker", "worker_summary", "sandbox_subagent", "synthesizer"})
_RUNTIME_ROLE = {"assistant": "default"}


def effective_cost_budget(profile_budget: float) -> float:
    limits = [value for value in (profile_budget, settings.CHAT_RUN_COST_BUDGET_USD) if value > 0]
    return min(limits) if limits else 0.0


def environment_snapshot() -> ResolvedModelProfile:
    stages = {
        stage: ResolvedStageModels(
            primary=chat_models.resolve_environment(_RUNTIME_ROLE.get(stage, stage)).to_payload(),
            economy=chat_models.resolve_environment(_RUNTIME_ROLE.get(stage, stage), economy=True).to_payload(),
        )
        for stage in RESOLVED_MODEL_STAGES
    }
    return ResolvedModelProfile(
        source="environment",
        cost_budget_usd=max(0.0, settings.CHAT_RUN_COST_BUDGET_USD),
        stages=stages,
    )


def _primary_choice_for(
    profile: ModelProfileItem,
    stage: str,
    user_reasoning_effort: SelectableReasoningEffort,
) -> tuple[str, str]:
    override = profile.stage_overrides.get(stage)
    model_id = override.model_id if override and override.model_id else profile.primary.model_id
    configured_reasoning = override.reasoning_effort if override else None
    return model_id, configured_reasoning if configured_reasoning is not None else user_reasoning_effort


def _profile_snapshot(
    profile: ModelProfileItem,
    reasoning_effort: SelectableReasoningEffort,
) -> ResolvedModelProfile:
    provenance = {
        "profile_id": profile.profile_id,
        "profile_name": profile.name,
        "profile_version": profile.current_version,
    }
    stages: dict[str, ResolvedStageModels] = {}
    for stage in RESOLVED_MODEL_STAGES:
        role = _RUNTIME_ROLE.get(stage, stage)
        if stage in _PROFILE_STAGES:
            primary_model_id, primary_reasoning = _primary_choice_for(profile, stage, reasoning_effort)
            normal = chat_models.resolve_environment(
                role,
                model_id=primary_model_id,
                reasoning_effort=primary_reasoning,
            )
            cheap = chat_models.resolve_environment(
                role,
                model_id=profile.economy.model_id,
                reasoning_effort=profile.economy.reasoning_effort,
            )
        else:
            normal = chat_models.resolve_environment(role)
            cheap = chat_models.resolve_environment(role, economy=True)
        stages[stage] = ResolvedStageModels(
            primary=replace(normal, **provenance).to_payload(),
            economy=replace(cheap, **provenance).to_payload(),
        )
    return ResolvedModelProfile(
        source="profile",
        profile_id=profile.profile_id,
        profile_name=profile.name,
        profile_version=profile.current_version,
        reasoning_effort=reasoning_effort,
        cost_budget_usd=effective_cost_budget(profile.run_cost_budget_usd),
        stages=stages,
    )


async def resolve(
    profile_id: str | None,
    reasoning_effort: SelectableReasoningEffort | None = None,
) -> ResolvedModelProfile:
    enabled = await report_store.list_model_profiles(enabled_only=True)
    if profile_id:
        profile = next((item for item in enabled if item.profile_id == profile_id), None)
        if profile is None:
            raise ModelProfileUnavailable("The selected model profile is no longer available")
        selected_effort = reasoning_effort or profile.default_reasoning_effort
        if selected_effort not in profile.user_reasoning_efforts:
            raise ModelProfileUnavailable("The selected reasoning level is not available for this model profile")
        return _profile_snapshot(profile, selected_effort)
    if not enabled:
        return environment_snapshot()
    default = next((item for item in enabled if item.is_default), None)
    if default is None:
        raise ModelProfileUnavailable("No default model profile is configured")
    selected_effort = reasoning_effort or default.default_reasoning_effort
    if selected_effort not in default.user_reasoning_efforts:
        raise ModelProfileUnavailable("The selected reasoning level is not available for this model profile")
    return _profile_snapshot(default, selected_effort)


async def selectable_profiles() -> list[SelectableModelProfile]:
    profiles = await report_store.list_model_profiles(enabled_only=True)
    return [
        SelectableModelProfile(
            profile_id=profile.profile_id,
            name=profile.name,
            description=profile.description,
            is_default=profile.is_default,
            default_reasoning_effort=profile.default_reasoning_effort,
            reasoning_efforts=profile.user_reasoning_efforts,
            run_cost_budget_usd=profile.run_cost_budget_usd,
            effective_cost_budget_usd=effective_cost_budget(profile.run_cost_budget_usd),
        )
        for profile in profiles
    ]


@contextmanager
def use(resolved: ResolvedModelProfile) -> Iterator[None]:
    token = _current_profile.set(resolved)
    try:
        yield
    finally:
        _current_profile.reset(token)


def current_spec(role: str, *, economy: bool = False) -> chat_models.ModelSpec | None:
    resolved = _current_profile.get()
    if resolved is None:
        return None
    return chat_models.ModelSpec.from_payload(resolved.spec_for(role, economy=economy).model_dump(mode="json"))


def require_current_spec(role: str, *, economy: bool = False) -> chat_models.ModelSpec:
    """Return a stage from the active immutable run configuration."""
    resolved = current_spec(role, economy=economy)
    if resolved is None:
        raise RuntimeError("model stage requested outside a resolved run configuration")
    return resolved


def current() -> ResolvedModelProfile | None:
    return _current_profile.get()
