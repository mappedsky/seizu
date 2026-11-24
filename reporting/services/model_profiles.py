from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

from reporting import settings
from reporting.schema.model_profiles import (
    RESOLVED_MODEL_STAGES,
    ModelProfileItem,
    QueryConsoleModelOption,
    QueryConsoleModelOptions,
    ResolvedModelProfile,
    ResolvedStageModels,
    SelectableModelProfile,
    SelectableReasoningEffort,
)
from reporting.services import chat_models, report_store


class ModelProfileUnavailable(LookupError):
    """A selected model profile is missing or disabled."""


# Every message ModelProfileUnavailable is raised with. They name what is wrong
# with a selection and nothing else, so they are safe to show the caller.
_PROFILE_GONE = "The selected model profile is no longer available"
_REASONING_UNAVAILABLE = "The selected reasoning level is not available for this model profile"
_NO_DEFAULT_PROFILE = "No default model profile is configured"
_NO_QUERY_CONSOLE_PROFILE = "No model profile is enabled for the query console"
_QUERY_CONSOLE_PROFILE_UNAVAILABLE = "The selected model profile is not available in the query console"
_QUERY_CONSOLE_REASONING_UNAVAILABLE = "The selected reasoning level is not available in the query console"
UNAVAILABLE_MESSAGES = frozenset(
    {
        _PROFILE_GONE,
        _REASONING_UNAVAILABLE,
        _NO_DEFAULT_PROFILE,
        _NO_QUERY_CONSOLE_PROFILE,
        _QUERY_CONSOLE_PROFILE_UNAVAILABLE,
        _QUERY_CONSOLE_REASONING_UNAVAILABLE,
    }
)


def public_unavailable_message(exc: Exception) -> str | None:
    """The fixed message ``exc`` was raised with, or None if it is not one of them.

    The return value is the module's own constant, never text read from the
    exception, so it can go into a response as is.
    """
    text = str(exc)
    return next((message for message in UNAVAILABLE_MESSAGES if message == text), None)


_current_profile: ContextVar[ResolvedModelProfile | None] = ContextVar("chat_model_profile", default=None)

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
    def with_provenance(spec: chat_models.ModelSpec) -> chat_models.ModelSpec:
        # Stamped onto every stage's specs; carried into audit/telemetry and
        # Temporal payloads without changing which client is built.
        return replace(
            spec,
            profile_id=profile.profile_id,
            profile_name=profile.name,
            profile_version=profile.current_version,
        )

    stages: dict[str, ResolvedStageModels] = {}
    for stage in RESOLVED_MODEL_STAGES:
        role = _RUNTIME_ROLE.get(stage, stage)
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
        stages[stage] = ResolvedStageModels(
            primary=with_provenance(normal).to_payload(),
            economy=with_provenance(cheap).to_payload(),
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
            raise ModelProfileUnavailable(_PROFILE_GONE)
        selected_effort = reasoning_effort or profile.default_reasoning_effort
        if selected_effort not in profile.user_reasoning_efforts:
            raise ModelProfileUnavailable(_REASONING_UNAVAILABLE)
        return _profile_snapshot(profile, selected_effort)
    if not enabled:
        return environment_snapshot()
    default = next((item for item in enabled if item.is_default), None)
    if default is None:
        raise ModelProfileUnavailable(_NO_DEFAULT_PROFILE)
    selected_effort = reasoning_effort or default.default_reasoning_effort
    if selected_effort not in default.user_reasoning_efforts:
        raise ModelProfileUnavailable(_REASONING_UNAVAILABLE)
    return _profile_snapshot(default, selected_effort)


@dataclass(frozen=True)
class StandaloneModelChoice:
    """The model for one call made outside a chat run, and what it may spend."""

    spec: chat_models.ModelSpec
    cost_budget_usd: float


def _query_console_profiles(enabled: list[ModelProfileItem]) -> list[ModelProfileItem]:
    """Enabled profiles offered in the console, the one it starts on first."""
    offered = [profile for profile in enabled if profile.query_console.enabled]
    # The store lists the default profile first; a console-enabled default wins.
    return sorted(offered, key=lambda profile: not profile.is_default)


async def query_console_options() -> QueryConsoleModelOptions:
    enabled = await report_store.list_model_profiles(enabled_only=True)
    offered = _query_console_profiles(enabled)
    return QueryConsoleModelOptions(
        available=bool(offered) or not enabled,
        profiles=[
            QueryConsoleModelOption(
                profile_id=profile.profile_id,
                name=profile.name,
                description=profile.description,
                model_id=profile.query_console.model_id or profile.primary.model_id,
                default_reasoning_effort=profile.query_console.default_reasoning_effort,
                reasoning_efforts=profile.query_console.reasoning_efforts,
            )
            for profile in offered
        ],
        default_profile_id=offered[0].profile_id if offered else None,
    )


async def resolve_query_console(
    profile_id: str | None,
    reasoning_effort: SelectableReasoningEffort | None = None,
) -> StandaloneModelChoice:
    """Resolve the model the query console generates a query with.

    The profile's ``query_console`` settings decide: whether it is offered,
    its model (the primary when unset), and the reasoning levels a caller may
    pick. With no ``profile_id`` the console's starting profile is used; with
    no profile enabled at all, the ``QUERY_GENERATION_*`` settings apply, but
    only to a request that named no profile.
    """
    enabled = await report_store.list_model_profiles(enabled_only=True)
    if not enabled:
        # A request that named a profile is refused rather than sent to a
        # model the caller did not choose.
        if profile_id:
            raise ModelProfileUnavailable(_QUERY_CONSOLE_PROFILE_UNAVAILABLE)
        return StandaloneModelChoice(
            spec=chat_models.resolve_environment("query_generation"),
            cost_budget_usd=max(0.0, settings.CHAT_RUN_COST_BUDGET_USD),
        )
    offered = _query_console_profiles(enabled)
    if not offered:
        raise ModelProfileUnavailable(_NO_QUERY_CONSOLE_PROFILE)
    if profile_id:
        profile = next((item for item in offered if item.profile_id == profile_id), None)
        if profile is None:
            raise ModelProfileUnavailable(_QUERY_CONSOLE_PROFILE_UNAVAILABLE)
    else:
        profile = offered[0]
    console = profile.query_console
    effort = reasoning_effort or console.default_reasoning_effort
    if effort not in console.reasoning_efforts:
        raise ModelProfileUnavailable(_QUERY_CONSOLE_REASONING_UNAVAILABLE)
    spec = chat_models.resolve_environment(
        "query_generation",
        model_id=console.model_id or profile.primary.model_id,
        reasoning_effort=effort,
    )
    return StandaloneModelChoice(
        spec=replace(
            spec,
            profile_id=profile.profile_id,
            profile_name=profile.name,
            profile_version=profile.current_version,
        ),
        cost_budget_usd=effective_cost_budget(profile.run_cost_budget_usd),
    )


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
