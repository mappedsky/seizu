"""Resolve *which* model a call runs on, and what it may spend, in one place.

Every LLM call in the chat agent used to build its model by reading
``settings.*`` at the point of use, memoized per ``(role, economy)``. That makes
the model a process-global, and it is why the only per-turn variation that
existed was a boolean: there was nowhere to put anything else.

This module inverts that. A :class:`ModelSpec` is resolved once and then passed
down, and it is layered so the layers can be reasoned about separately:

1. **Model capability** -- what the model can actually do, read from litellm's
   database (:func:`capability`). Never configured, because it is a fact about
   the model rather than a preference: ``max_output_tokens`` spans 16k to 393k
   across the models we run, and asking above a provider's ceiling is refused
   outright rather than quietly reduced.
2. **Deployment defaults** -- per-role model and reasoning effort from settings.
3. **Per-call choice** -- explicit arguments, which is where a user-selected
   model and effort will arrive.

Because the spec is frozen and hashable it is also the model cache's key, so two
different choices cannot collide in one process the way ``(role, economy)``
would.

**Only two knobs are ever sent, and LiteLLM translates them per provider.**
Verified against litellm's own transformation:

===============  ==================================================
Provider         ``max_tokens=32768, reasoning_effort="low"`` becomes
===============  ==================================================
Anthropic        ``max_tokens`` + ``thinking={"type": "adaptive"}``
OpenAI           ``max_completion_tokens`` + ``reasoning_effort``
DeepSeek         ``max_tokens`` + ``thinking={"type": "enabled"}``
Gemini           ``thinkingConfig={"thinkingBudget": ...}``
===============  ==================================================

So ``reasoning_effort`` is the portable knob and ``thinking`` is not (OpenAI does
not accept it). Sending provider-native parameters here would re-introduce
exactly the cross-provider special-casing LiteLLM was adopted to remove.

Decisions: AGT-019 in ``docs/root/dev/decisions/chat-agent.md``.
"""

import logging
from dataclasses import dataclass
from functools import lru_cache

from reporting import settings

logger = logging.getLogger(__name__)

#: Effort levels LiteLLM accepts for every provider we checked. ``""`` leaves
#: the provider's own default in place and sends nothing.
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high")


@dataclass(frozen=True)
class ModelCapability:
    """What a model can do, as litellm reports it."""

    max_output_tokens: int = 0
    supports_reasoning: bool = False
    provider: str = ""
    #: Whether the model accepts a `temperature` at all. Reasoning models on
    #: OpenAI do not, and `litellm.drop_params` is False, so sending one raises.
    supports_temperature: bool = True


#: Stages that are not roles of their own: they run on a role's model but want
#: their own reasoning budget. A worker's ReAct loop is deciding what to do
#: next; its summary pass is writing down what it already did, and every
#: "reasoning ate the allowance" failure in this codebase is in the latter.
_STAGE_PARENT = {
    "worker_summary": "worker",
    "worker_summary_retry": "worker",
}


@dataclass(frozen=True)
class ModelSpec:
    """One resolved decision about how a call runs.

    Frozen so it can key the model cache: a spec fully determines the model that
    is built from it, which ``(role, economy)`` did not.
    """

    model_id: str
    max_output_tokens: int
    reasoning_effort: str = ""
    #: Which stage of the loop asked for it. Diagnostics only -- two roles that
    #: resolve to the same model and limits share a cache entry, as they should.
    role: str = "default"

    def to_payload(self) -> dict[str, object]:
        """A plain-dict form for a Temporal payload or a stored command."""
        return {
            "model_id": self.model_id,
            "max_output_tokens": self.max_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "role": self.role,
        }

    @classmethod
    def from_payload(cls, data: object) -> "ModelSpec | None":
        """Rebuild a spec another process resolved, or ``None`` if unusable.

        A distributed plan step must run on the model the turn was *admitted*
        with, not on whatever that worker's settings resolve to now -- the same
        rule that makes ``permission_cap`` travel rather than be re-derived
        (AGT-006). Tolerant of an older or malformed payload, because falling
        back to a locally resolved spec runs the step rather than failing it.
        """
        if not isinstance(data, dict) or not data.get("model_id"):
            return None
        return cls(
            model_id=str(data["model_id"]),
            max_output_tokens=max(0, int(data.get("max_output_tokens") or 0)),
            reasoning_effort=str(data.get("reasoning_effort") or ""),
            role=str(data.get("role") or "default"),
        )


@lru_cache(maxsize=64)
def capability(model_id: str) -> ModelCapability:
    """What litellm knows about a model. Empty for one it does not know."""
    if not model_id:
        return ModelCapability()
    try:
        from litellm import get_model_info

        info = get_model_info(model_id) or {}
    except Exception:
        # A self-hosted or custom deployment litellm has no entry for. The
        # configured cap then stands on its own, which is the safe direction.
        logger.info("no model info for %s; using configured limits", model_id)
        return ModelCapability(provider=_provider_of(model_id))
    return ModelCapability(
        max_output_tokens=int(info.get("max_output_tokens") or 0),
        supports_reasoning=bool(info.get("supports_reasoning")),
        provider=_provider_of(model_id),
        supports_temperature=_accepts_temperature(model_id),
    )


def _provider_of(model_id: str) -> str:
    """The provider litellm will route this model to, or ``""``."""
    try:
        from litellm import get_llm_provider

        return str(get_llm_provider(model=model_id)[1] or "")
    except Exception:
        return ""


def _accepts_temperature(model_id: str) -> bool:
    """Whether a temperature may be sent, asked of litellm rather than assumed.

    There is no capability flag for this and the model database is misleading --
    ``get_supported_openai_params`` reports ``temperature`` for ``gpt-5``, which
    then refuses it. The parameter transform is the authority, so this asks it.
    Offline and cached, so it costs one call per model per process.
    """
    try:
        from litellm.exceptions import UnsupportedParamsError
        from litellm.utils import get_optional_params

        model, provider = model_id, _provider_of(model_id)
        try:
            from litellm import get_llm_provider

            model = str(get_llm_provider(model=model_id)[0] or model_id)
        except Exception:
            pass
        try:
            get_optional_params(model=model, custom_llm_provider=provider, temperature=0.2)
        except UnsupportedParamsError:
            return False
    except Exception:
        # Anything else -- an unknown provider, an import problem -- is not
        # evidence that temperature is refused.
        return True
    return True


def derive_max_output_tokens(model_id: str) -> int:
    """How many output tokens a call on this model may ask for.

    The model's own ceiling, capped by ``CHAT_LLM_MAX_OUTPUT_TOKENS_CAP`` --
    **not** a flat constant. A constant is wrong in both directions at once: at
    4,096 it silently broke the planner on a model whose real ceiling is 393,216
    (every structured call returned ``chars=0, finish_reason=length`` and fell
    back to a one-step plan), while a constant large enough for that model is
    refused outright by one whose ceiling is 16,384.

    ``CHAT_LLM_MAX_TOKENS`` still overrides it outright when set, so a
    deployment that pinned a value keeps it.
    """
    override = max(0, settings.CHAT_LLM_MAX_TOKENS)
    ceiling = capability(model_id).max_output_tokens
    if override > 0:
        # Still clamped: an override above what the provider accepts is refused
        # rather than reduced, so honouring it literally would fail every call.
        return min(override, ceiling) if ceiling > 0 else override
    cap = max(1, settings.CHAT_LLM_MAX_OUTPUT_TOKENS_CAP)
    return min(cap, ceiling) if ceiling > 0 else cap


def _role_reasoning_effort(stage: str) -> str:
    """The configured effort for one stage, falling back to its role, then global.

    Per stage because they want opposite things and one global value cannot say
    so: decomposition and judgment (planner, verifier) are what reasoning is
    for, while classification (router) and transcription (worker summaries,
    synthesis) only lose output allowance to it -- on these providers reasoning
    and answer share one budget.

    A stage that is not a role of its own (``worker_summary``) falls back to its
    parent's value, so configuring only ``CHAT_LLM_WORKER_REASONING_EFFORT``
    still governs everything the worker does.
    """
    per_stage = {
        "router": settings.CHAT_LLM_ROUTER_REASONING_EFFORT,
        "planner": settings.CHAT_LLM_PLANNER_REASONING_EFFORT,
        "worker": settings.CHAT_LLM_WORKER_REASONING_EFFORT,
        "worker_summary": settings.CHAT_LLM_WORKER_SUMMARY_REASONING_EFFORT,
        "worker_summary_retry": settings.CHAT_LLM_WORKER_SUMMARY_REASONING_EFFORT,
        "verifier": settings.CHAT_LLM_VERIFIER_REASONING_EFFORT,
        "synthesizer": settings.CHAT_LLM_SYNTHESIZER_REASONING_EFFORT,
    }
    parent = _STAGE_PARENT.get(stage, "")
    return (
        per_stage.get(stage, "") or (per_stage.get(parent, "") if parent else "") or settings.CHAT_LLM_REASONING_EFFORT
    ).strip()


#: How much of the output allowance each level gives to thinking, for providers
#: that take a number rather than a word. Fractions of the call's own ceiling, so
#: they scale with the model instead of needing a table per model.
_EFFORT_BUDGET_SHARE = {"minimal": 0.05, "low": 0.15, "medium": 0.35, "high": 0.6}
#: Anthropic's floor for `budget_tokens`; below it the request is rejected.
_MIN_THINKING_BUDGET = 1_024


def thinking_budget_tokens(spec: "ModelSpec") -> int:
    """Tokens of thinking for an effort level, for providers that want a number.

    A share of the call's own ceiling rather than a constant, so one profile
    means the same thing on a 16k model and a 393k one. Held well below the
    ceiling because thinking and the answer come out of the same allowance and
    the answer still has to fit.
    """
    share = _EFFORT_BUDGET_SHARE.get(spec.reasoning_effort, 0.0)
    if share <= 0 or spec.max_output_tokens <= 0:
        return 0
    budget = int(spec.max_output_tokens * share)
    # Never more than half: past that the thinking can crowd out the answer it
    # exists to produce.
    return max(_MIN_THINKING_BUDGET, min(budget, spec.max_output_tokens // 2))


def reasoning_kwargs(spec: "ModelSpec") -> dict[str, object]:
    """The provider's own reasoning parameters for this spec.

    ``reasoning_effort`` alone is **not** enough, which is why this exists.
    LiteLLM's mapping is lossy on two of the four providers we run: it collapses
    every level to ``thinking: {"type": "enabled"}`` on DeepSeek and
    ``{"type": "adaptive"}`` on Anthropic, so a "high" and a "minimal" reach the
    provider identical. Graded control *is* available on both -- Anthropic takes
    ``budget_tokens``, DeepSeek passes anything through ``extra_body`` -- and
    OpenAI and Gemini are graded natively.

    Rendering it here keeps the provider knowledge in the one place that already
    knows the model, so no call site learns a provider name.
    """
    if not spec.reasoning_effort:
        return {}
    provider = capability(spec.model_id).provider
    if spec.reasoning_effort == "none":
        # Off, in each provider's spelling. Anthropic and DeepSeek need to be
        # told explicitly; for the natively-graded providers the level is a
        # value they already understand.
        if provider == "anthropic":
            return {"thinking": {"type": "disabled"}}
        if provider == "deepseek":
            return {"extra_body": {"reasoning_effort": "none"}}
        return {"reasoning_effort": "none"}
    if provider == "anthropic":
        return {"thinking": {"type": "enabled", "budget_tokens": thinking_budget_tokens(spec)}}
    if provider == "deepseek":
        # Straight through: litellm would otherwise flatten the level away.
        return {"extra_body": {"reasoning_effort": spec.reasoning_effort}}
    # OpenAI and Gemini grade this themselves, and litellm maps it correctly.
    return {"reasoning_effort": spec.reasoning_effort}


def temperature_for(spec: "ModelSpec") -> float | None:
    """The temperature to send, or ``None`` to send none at all.

    Two models refuse the configured value and there is no capability flag that
    says so. OpenAI's reasoning models reject a temperature outright -- and with
    ``litellm.drop_params`` False that raises rather than being dropped, so a
    deployment on ``gpt-5`` fails every call. Anthropic accepts one at the
    litellm layer but the API rejects any value but 1 once extended thinking is
    on, which is a failure that only appears when reasoning is enabled.
    """
    if not capability(spec.model_id).supports_temperature:
        return None
    if spec.reasoning_effort and spec.reasoning_effort != "none":
        if capability(spec.model_id).provider == "anthropic":
            # Extended thinking fixes it at 1; anything else is refused.
            return 1.0
    return settings.CHAT_LLM_TEMPERATURE


def model_id_for_role(role: str, *, economy: bool = False) -> str:
    if economy and settings.CHAT_LLM_ECONOMY_MODEL.strip():
        return settings.CHAT_LLM_ECONOMY_MODEL.strip()
    # A stage runs on its parent role's model; only the effort differs.
    role = _STAGE_PARENT.get(role, role)
    role_models = {
        "planner": settings.CHAT_LLM_PLANNER_MODEL,
        # The router shares the planner's model deliberately: both are small
        # structured calls made once per turn.
        "router": settings.CHAT_LLM_PLANNER_MODEL,
        "worker": settings.CHAT_LLM_WORKER_MODEL,
        "verifier": settings.CHAT_LLM_VERIFIER_MODEL,
        "synthesizer": settings.CHAT_LLM_SYNTHESIZER_MODEL,
    }
    return role_models.get(role, "").strip() or settings.CHAT_LLM_MODEL.strip()


def resolve(
    role: str = "default",
    *,
    economy: bool = False,
    model_id: str = "",
    reasoning_effort: str | None = None,
) -> ModelSpec:
    """Resolve the spec for one call.

    ``model_id`` and ``reasoning_effort`` are the per-call layer: they win over
    the deployment's settings, and they are where a user-selected model and
    effort will arrive. Nothing else needs to change to accept them.
    """
    resolved_id = (model_id or model_id_for_role(role, economy=economy)).strip()
    effort = (reasoning_effort if reasoning_effort is not None else _role_reasoning_effort(role)).strip()
    if effort and not capability(resolved_id).supports_reasoning:
        # Sending it to a model that does not reason is at best ignored and at
        # worst rejected, and either way it describes something that is not
        # happening.
        effort = ""
    if effort and effort not in REASONING_EFFORTS:
        logger.warning("ignoring unknown reasoning effort %r for %s", effort, role)
        effort = ""
    return ModelSpec(
        model_id=resolved_id,
        max_output_tokens=derive_max_output_tokens(resolved_id),
        reasoning_effort=effort,
        role=role,
    )
