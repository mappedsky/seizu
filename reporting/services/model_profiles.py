from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from reporting import settings
from reporting.schema.model_profiles import (
    ModelChoice,
    ModelProfileItem,
    ResolvedModelProfile,
    SelectableModelProfile,
)
from reporting.services import chat_models, report_store


class ModelProfileUnavailable(LookupError):
    """A selected model profile is missing or disabled."""


_current_profile: ContextVar[ResolvedModelProfile | None] = ContextVar("chat_model_profile", default=None)

_CONTROLLED_ROLES = {
    "default": "assistant",
    "planner": "planner",
    "worker": "worker",
    "worker_summary": "worker_summary",
    "worker_summary_retry": "worker_summary",
    "sandbox_subagent": "sandbox_subagent",
    "synthesizer": "synthesizer",
}
_ALL_ROLES = (*_CONTROLLED_ROLES, "router", "verifier")


def effective_cost_budget(profile_budget: float) -> float:
    limits = [value for value in (profile_budget, settings.CHAT_RUN_COST_BUDGET_USD) if value > 0]
    return min(limits) if limits else 0.0


def environment_snapshot() -> ResolvedModelProfile:
    primary = {role: chat_models.resolve(role).to_payload() for role in _ALL_ROLES}
    economy = {role: chat_models.resolve(role, economy=True).to_payload() for role in _ALL_ROLES}
    return ResolvedModelProfile(
        source="environment",
        cost_budget_usd=max(0.0, settings.CHAT_RUN_COST_BUDGET_USD),
        primary_specs=primary,
        economy_specs=economy,
    )


def _choice_for(profile: ModelProfileItem, role: str, *, economy: bool) -> ModelChoice:
    stage = _CONTROLLED_ROLES[role]
    base = profile.economy if economy else profile.primary
    override = profile.stage_overrides.get(stage)
    selected = (override.economy if economy else override.primary) if override else None
    if selected is None:
        return base
    return ModelChoice(
        model_id=selected.model_id or base.model_id,
        reasoning_effort=(base.reasoning_effort if selected.reasoning_effort is None else selected.reasoning_effort),
    )


def _profile_snapshot(profile: ModelProfileItem) -> ResolvedModelProfile:
    provenance = {
        "profile_id": profile.profile_id,
        "profile_name": profile.name,
        "profile_version": profile.current_version,
    }
    primary: dict[str, dict[str, object]] = {}
    economy: dict[str, dict[str, object]] = {}
    for role in _ALL_ROLES:
        if role in _CONTROLLED_ROLES:
            normal_choice = _choice_for(profile, role, economy=False)
            economy_choice = _choice_for(profile, role, economy=True)
            normal = chat_models.resolve(
                role,
                model_id=normal_choice.model_id,
                reasoning_effort=normal_choice.reasoning_effort,
            )
            cheap = chat_models.resolve(
                role,
                model_id=economy_choice.model_id,
                reasoning_effort=economy_choice.reasoning_effort,
            )
        else:
            normal = chat_models.resolve(role)
            cheap = chat_models.resolve(role, economy=True)
        primary[role] = replace(normal, **provenance).to_payload()
        economy[role] = replace(cheap, **provenance).to_payload()
    return ResolvedModelProfile(
        source="profile",
        profile_id=profile.profile_id,
        profile_name=profile.name,
        profile_version=profile.current_version,
        cost_budget_usd=effective_cost_budget(profile.run_cost_budget_usd),
        primary_specs=primary,
        economy_specs=economy,
    )


async def resolve(profile_id: str | None) -> ResolvedModelProfile:
    enabled = await report_store.list_model_profiles(enabled_only=True)
    if profile_id:
        profile = next((item for item in enabled if item.profile_id == profile_id), None)
        if profile is None:
            raise ModelProfileUnavailable("The selected model profile is no longer available")
        return _profile_snapshot(profile)
    if not enabled:
        return environment_snapshot()
    default = next((item for item in enabled if item.is_default), None)
    if default is None:
        raise ModelProfileUnavailable("No default model profile is configured")
    return _profile_snapshot(default)


async def selectable_profiles() -> list[SelectableModelProfile]:
    profiles = await report_store.list_model_profiles(enabled_only=True)
    return [
        SelectableModelProfile(
            profile_id=profile.profile_id,
            name=profile.name,
            description=profile.description,
            is_default=profile.is_default,
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
    payloads = resolved.economy_specs if economy else resolved.primary_specs
    payload = payloads.get(role)
    if payload is None and role == "worker_summary_retry":
        payload = payloads.get("worker_summary")
    return chat_models.ModelSpec.from_payload(payload)


def current() -> ResolvedModelProfile | None:
    return _current_profile.get()
