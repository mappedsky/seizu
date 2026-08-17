"""Resolving which model a call runs on, and what it may spend (AGT-019).

The rules pinned here are the ones whose failure is *silent*: a ceiling below
what a reasoning model needs does not shorten the answer, it removes it, and the
caller cannot tell that from a model that could not satisfy the request.
"""

import pytest

from reporting import settings
from reporting.services import chat_models


@pytest.fixture(autouse=True)
def _clear_capability_cache():
    def _clear() -> None:
        # Guarded: a test that patched `capability` with a plain function leaves
        # no cache to clear, and this fixture tears down before mocker undoes it.
        clear = getattr(chat_models.capability, "cache_clear", None)
        if clear is not None:
            clear()

    _clear()
    yield
    _clear()


def _capability(mocker, *, max_output_tokens: int, supports_reasoning: bool = True) -> None:
    mocker.patch.object(
        chat_models,
        "capability",
        lambda _model_id: chat_models.ModelCapability(
            max_output_tokens=max_output_tokens, supports_reasoning=supports_reasoning
        ),
    )


# --- deriving the output ceiling ------------------------------------------------


def test_the_ceiling_comes_from_the_model_not_a_constant(mocker):
    """A constant is wrong in both directions at once: too low for a model whose
    ceiling is 393,216, and refused outright by one whose ceiling is 16,384."""
    mocker.patch.object(settings, "CHAT_LLM_MAX_TOKENS", 0)
    mocker.patch.object(settings, "CHAT_LLM_MAX_OUTPUT_TOKENS_CAP", 32_768)

    _capability(mocker, max_output_tokens=393_216)
    assert chat_models.derive_max_output_tokens("big") == 32_768

    _capability(mocker, max_output_tokens=16_384)
    assert chat_models.derive_max_output_tokens("small") == 16_384


def test_an_unknown_model_keeps_the_configured_cap(mocker):
    """Guessing low truncates an answer; guessing high is refused outright."""
    mocker.patch.object(settings, "CHAT_LLM_MAX_TOKENS", 0)
    mocker.patch.object(settings, "CHAT_LLM_MAX_OUTPUT_TOKENS_CAP", 32_768)
    _capability(mocker, max_output_tokens=0)

    assert chat_models.derive_max_output_tokens("self-hosted-thing") == 32_768


def test_an_explicit_override_wins_but_is_still_clamped(mocker):
    """A deployment that pinned a value keeps it -- but not above what the
    provider will accept, or every call fails rather than being reduced."""
    mocker.patch.object(settings, "CHAT_LLM_MAX_TOKENS", 100_000)
    _capability(mocker, max_output_tokens=16_384)

    assert chat_models.derive_max_output_tokens("small") == 16_384


def test_an_override_below_the_ceiling_is_honoured(mocker):
    mocker.patch.object(settings, "CHAT_LLM_MAX_TOKENS", 8_000)
    _capability(mocker, max_output_tokens=393_216)

    assert chat_models.derive_max_output_tokens("big") == 8_000


# --- reasoning effort -----------------------------------------------------------


def test_effort_is_not_sent_to_a_model_that_does_not_reason(mocker):
    """At best ignored, at worst rejected -- and either way it describes
    something that is not happening."""
    mocker.patch.object(settings, "CHAT_LLM_REASONING_EFFORT", "high")
    _capability(mocker, max_output_tokens=16_384, supports_reasoning=False)

    assert chat_models.resolve("planner").reasoning_effort == ""


def test_a_role_overrides_the_global_effort(mocker):
    """The stages want opposite things: reasoning is what decomposition is for,
    while a classifier only loses answer allowance to it."""
    mocker.patch.object(settings, "CHAT_LLM_REASONING_EFFORT", "high")
    mocker.patch.object(settings, "CHAT_LLM_ROUTER_REASONING_EFFORT", "minimal")
    mocker.patch.object(settings, "CHAT_LLM_PLANNER_REASONING_EFFORT", "")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("router").reasoning_effort == "minimal"
    assert chat_models.resolve("planner").reasoning_effort == "high"


def test_an_unknown_effort_is_dropped_rather_than_sent(mocker):
    mocker.patch.object(settings, "CHAT_LLM_REASONING_EFFORT", "turbo")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("worker").reasoning_effort == ""


def test_a_per_call_effort_beats_every_setting(mocker):
    """The layer a user-selected effort will arrive in."""
    mocker.patch.object(settings, "CHAT_LLM_REASONING_EFFORT", "high")
    mocker.patch.object(settings, "CHAT_LLM_WORKER_REASONING_EFFORT", "medium")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("worker", reasoning_effort="low").reasoning_effort == "low"


def test_a_per_call_model_beats_every_setting(mocker):
    mocker.patch.object(settings, "CHAT_LLM_MODEL", "configured/model")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("worker", model_id="chosen/model").model_id == "chosen/model"


# --- the spec as a carried decision ---------------------------------------------


def test_a_spec_round_trips_through_a_payload(mocker):
    """A distributed plan step must run on the model its turn was admitted with,
    not on whatever that worker's settings resolve to now."""
    _capability(mocker, max_output_tokens=32_768)
    spec = chat_models.resolve("worker", reasoning_effort="low")

    assert chat_models.ModelSpec.from_payload(spec.to_payload()) == spec


@pytest.mark.parametrize("payload", [None, {}, {"max_output_tokens": 10}, "nonsense", {"model_id": ""}])
def test_an_unusable_payload_resolves_to_nothing(payload):
    """The caller falls back to a local resolve, which runs the step rather than
    failing it -- so this must report "unusable", never raise."""
    assert chat_models.ModelSpec.from_payload(payload) is None


def test_a_spec_is_hashable_so_it_can_key_the_model_cache(mocker):
    """Keying on (role, economy) instead would hand one user's chosen model to
    another once model choice is per-turn."""
    _capability(mocker, max_output_tokens=32_768)
    a = chat_models.resolve("worker")
    b = chat_models.resolve("worker", reasoning_effort="high")

    assert len({a, b}) == 2
    assert hash(a) == hash(chat_models.resolve("worker"))


def test_economy_selects_the_economy_model(mocker):
    mocker.patch.object(settings, "CHAT_LLM_ECONOMY_MODEL", "cheap/model")
    mocker.patch.object(settings, "CHAT_LLM_WORKER_MODEL", "good/model")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("worker", economy=True).model_id == "cheap/model"
    assert chat_models.resolve("worker").model_id == "good/model"


# --- the wiring that actually delivers it ---------------------------------------


def test_reasoning_effort_goes_through_model_kwargs(mocker):
    """`ChatLiteLLM` does not declare `reasoning_effort`, so passing it as a
    constructor argument is silently swallowed -- no attribute, absent from
    model_kwargs, absent from _default_params. It shipped that way once, and
    every measurement taken against it was measuring nothing."""
    from reporting.services import chat_graph

    built = object()
    factory = mocker.patch("langchain_litellm.ChatLiteLLM", return_value=built)
    mocker.patch.object(settings, "CHAT_LLM_PROVIDER", "openai")
    chat_graph.build_chat_model.cache_clear()
    spec = chat_models.ModelSpec(model_id="gpt-5", max_output_tokens=32_768, reasoning_effort="low")

    try:
        assert chat_graph.build_chat_model(spec) is built
    finally:
        chat_graph.build_chat_model.cache_clear()

    kwargs = factory.call_args.kwargs
    assert kwargs["model_kwargs"] == {"reasoning_effort": "low"}
    assert "reasoning_effort" not in kwargs
    assert kwargs["max_tokens"] == 32_768


def test_no_effort_sends_no_reasoning_parameter(mocker):
    """`litellm.drop_params` is False, so a parameter a model does not support
    raises rather than being dropped."""
    from reporting.services import chat_graph

    factory = mocker.patch("langchain_litellm.ChatLiteLLM", return_value=object())
    mocker.patch.object(settings, "CHAT_LLM_PROVIDER", "openai")
    chat_graph.build_chat_model.cache_clear()

    try:
        chat_graph.build_chat_model(chat_models.ModelSpec(model_id="gpt-4o", max_output_tokens=16_384))
    finally:
        chat_graph.build_chat_model.cache_clear()

    assert "model_kwargs" not in factory.call_args.kwargs


# --- rendering an effort level into provider-native parameters ------------------


def _spec(model_id: str, effort: str, max_output: int = 32_768) -> chat_models.ModelSpec:
    return chat_models.ModelSpec(model_id=model_id, max_output_tokens=max_output, reasoning_effort=effort)


@pytest.mark.parametrize(
    ("model_id", "effort", "expected"),
    [
        # Graded natively; litellm maps the level correctly.
        ("openai/gpt-5", "low", {"reasoning_effort": "low"}),
        ("gemini/gemini-2.5-pro", "high", {"reasoning_effort": "high"}),
        # Not graded by litellm's mapping -- it collapses every level to one
        # value -- so the level is rendered into the provider's own parameter.
        ("deepseek/deepseek-v4-pro", "high", {"extra_body": {"reasoning_effort": "high"}}),
        # Off, in each provider's spelling.
        ("anthropic/claude-sonnet-4-6", "none", {"thinking": {"type": "disabled"}}),
        ("deepseek/deepseek-v4-pro", "none", {"extra_body": {"reasoning_effort": "none"}}),
        ("openai/gpt-5", "none", {"reasoning_effort": "none"}),
        # Nothing configured sends nothing.
        ("openai/gpt-5", "", {}),
    ],
)
def test_effort_renders_into_the_providers_own_parameters(model_id, effort, expected):
    """`reasoning_effort` alone is not enough: litellm flattens every level to
    one value on DeepSeek and Anthropic, so "high" and "minimal" would reach
    those providers identical."""
    assert chat_models.reasoning_kwargs(_spec(model_id, effort)) == expected


def test_anthropic_gets_a_graded_budget_that_scales_with_the_ceiling():
    """A share of the call's own ceiling, so one profile means the same thing on
    a 16k model and a 393k one."""
    low = chat_models.reasoning_kwargs(_spec("anthropic/claude-sonnet-4-6", "low"))
    high = chat_models.reasoning_kwargs(_spec("anthropic/claude-sonnet-4-6", "high"))

    assert low["thinking"]["budget_tokens"] < high["thinking"]["budget_tokens"]
    # Never more than half, or the thinking crowds out the answer it exists for.
    assert high["thinking"]["budget_tokens"] <= 32_768 // 2


def test_a_thinking_budget_never_falls_below_the_providers_floor():
    """Anthropic rejects a budget under 1024 outright."""
    budget = chat_models.thinking_budget_tokens(_spec("anthropic/claude-sonnet-4-6", "minimal", max_output=4_096))

    assert budget >= 1_024


# --- temperature ----------------------------------------------------------------


def test_temperature_is_omitted_for_a_model_that_refuses_one():
    """OpenAI's reasoning models reject a temperature outright, and
    `litellm.drop_params` is False -- so sending the configured value fails
    every call. There is no capability flag for this; litellm's own parameter
    transform is the authority."""
    assert chat_models.temperature_for(_spec("openai/gpt-5", "")) is None
    assert chat_models.temperature_for(_spec("openai/gpt-4o", "")) is not None


def test_anthropic_thinking_forces_temperature_to_one():
    """Accepted at the litellm layer but rejected by the API: extended thinking
    fixes temperature at 1, and litellm does not strip a different value."""
    assert chat_models.temperature_for(_spec("anthropic/claude-sonnet-4-6", "low")) == 1.0
    # Without thinking the configured value stands.
    assert chat_models.temperature_for(_spec("anthropic/claude-sonnet-4-6", "")) != 1.0


# --- stages ---------------------------------------------------------------------


def test_a_summary_stage_runs_on_its_roles_model_with_its_own_effort(mocker):
    """The worker's ReAct loop decides what to do next; its summary pass writes
    down what it already did. Same model, different job."""
    mocker.patch.object(settings, "CHAT_LLM_WORKER_MODEL", "worker/model")
    mocker.patch.object(settings, "CHAT_LLM_WORKER_REASONING_EFFORT", "high")
    mocker.patch.object(settings, "CHAT_LLM_WORKER_SUMMARY_REASONING_EFFORT", "minimal")
    _capability(mocker, max_output_tokens=32_768)

    summary = chat_models.resolve("worker_summary")

    assert summary.model_id == "worker/model"
    assert summary.reasoning_effort == "minimal"
    assert chat_models.resolve("worker").reasoning_effort == "high"


def test_a_stage_inherits_its_roles_effort_when_unset(mocker):
    """Configuring only the worker still governs everything the worker does."""
    mocker.patch.object(settings, "CHAT_LLM_WORKER_REASONING_EFFORT", "low")
    mocker.patch.object(settings, "CHAT_LLM_WORKER_SUMMARY_REASONING_EFFORT", "")
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("worker_summary").reasoning_effort == "low"


def test_the_router_defaults_to_no_reasoning(mocker):
    """The one stage with a measured default: its whole output is a single
    label, so reasoning cannot change anything downstream except that label.
    Measured 21/21 correct with output halved."""
    _capability(mocker, max_output_tokens=32_768)

    assert chat_models.resolve("router").reasoning_effort == "none"
    # Every other stage stays on the provider's default until measured.
    for stage in ("planner", "worker", "worker_summary", "verifier", "synthesizer"):
        assert chat_models.resolve(stage).reasoning_effort == ""
