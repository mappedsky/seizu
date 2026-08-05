from unittest.mock import MagicMock

import pytest

from reporting.services import chat_context


@pytest.fixture(autouse=True)
def _clear_caches():
    chat_context._TOKEN_CACHE.clear()
    chat_context._WINDOW_CACHE.clear()
    yield
    chat_context._TOKEN_CACHE.clear()
    chat_context._WINDOW_CACHE.clear()


def _model(name: str = "deepseek/deepseek-chat") -> MagicMock:
    model = MagicMock()
    model.model_name = name
    return model


def test_the_window_comes_from_the_model_not_from_configuration(mocker):
    """Nothing used to read the model's window at all: one fixed character cap
    was applied to every model, overflowing small ones and wasting large ones."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 0)
    mocker.patch("litellm.get_model_info", return_value={"max_input_tokens": 131_072})

    assert chat_context.context_window_tokens(_model()) == 131_072


def test_an_unknown_model_falls_back_low_rather_than_high(mocker):
    """Guessing low wastes part of a window; guessing high fails the turn."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 0)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS", 32_768)
    mocker.patch("litellm.get_model_info", side_effect=Exception("unknown model"))

    assert chat_context.context_window_tokens(_model("self-hosted/mystery")) == 32_768
    # A model with no name at all (tests, the mock provider) gets the same floor.
    assert chat_context.context_window_tokens(MagicMock(model_name="", model="")) == 32_768


def test_the_window_can_be_overridden_outright(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 200_000)
    info = mocker.patch("litellm.get_model_info", return_value={"max_input_tokens": 8_192})

    assert chat_context.context_window_tokens(_model()) == 200_000
    info.assert_not_called()


def test_the_window_is_looked_up_once_per_model(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 0)
    info = mocker.patch("litellm.get_model_info", return_value={"max_input_tokens": 131_072})

    for _ in range(5):
        chat_context.context_window_tokens(_model())

    assert info.call_count == 1


def test_the_history_budget_is_a_ceiling_not_a_target(mocker):
    """Fitting is not filling: pointing Seizu at a million-token model must not
    silently multiply the cost of every call."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 0)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_MAX_TOKENS", 40_000)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_SHARE", 0.5)

    mocker.patch("litellm.get_model_info", return_value={"max_input_tokens": 1_000_000})
    assert chat_context.history_token_budget(_model("big/model")) == 40_000

    # ...and a small model clamps the configured budget down to what fits.
    chat_context._WINDOW_CACHE.clear()
    mocker.patch("litellm.get_model_info", return_value={"max_input_tokens": 32_768})
    assert chat_context.history_token_budget(_model("small/model")) == 16_384


def test_a_zero_history_budget_means_whatever_the_window_allows(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 100_000)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_MAX_TOKENS", 0)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_SHARE", 0.5)

    assert chat_context.history_token_budget(_model()) == 50_000


def test_tokens_are_counted_with_the_providers_tokenizer(mocker):
    counter = mocker.patch("litellm.token_counter", return_value=1234)

    assert chat_context.count_tokens(_model(), "some text") == 1234
    counter.assert_called_once()


def test_counting_the_same_content_twice_costs_one_count(mocker):
    """A trim pass sizes every message and the loop trims on every call."""
    counter = mocker.patch("litellm.token_counter", return_value=7)

    for _ in range(4):
        chat_context.count_tokens(_model(), "repeated payload")

    assert counter.call_count == 1


def test_counting_falls_back_to_the_measured_ratio_without_a_tokenizer(mocker):
    """3.0 chars/token, measured on real tool payloads -- not the conventional
    4, which under-counts by a third and is what overflows a window."""
    mocker.patch("litellm.token_counter", side_effect=Exception("no tokenizer"))

    assert chat_context.count_tokens(_model(), "x" * 300) == 100
    # No model name at all takes the same path without calling litellm.
    assert chat_context.count_tokens(MagicMock(model_name="", model=""), "x" * 300) == 100
    assert chat_context.count_tokens(_model(), "") == 0


def test_the_token_cache_is_bounded(mocker):
    mocker.patch("litellm.token_counter", return_value=1)
    mocker.patch.object(chat_context, "_TOKEN_CACHE_MAX", 4)

    for index in range(10):
        chat_context.count_tokens(_model(), f"payload {index}")

    assert len(chat_context._TOKEN_CACHE) <= 4


def test_a_character_budget_is_calibrated_on_the_text_it_will_cut(mocker):
    """Truncating a line is a character operation, but the budget is in tokens,
    and the ratio differs between prose and structured payloads."""
    mocker.patch("litellm.token_counter", return_value=100)  # 300 chars -> 100 tokens

    assert chat_context.chars_for_tokens(_model(), "x" * 300, tokens=10) == 30
    assert chat_context.chars_for_tokens(_model(), "x" * 300, tokens=0) == 0
    # No sample to measure: the fallback ratio, not a crash.
    assert chat_context.chars_for_tokens(_model(), "", tokens=10) == 30


# --- The whole request, not just history ---------------------------------------


def test_the_allowance_subtracts_everything_the_call_must_carry(mocker):
    """History was the only thing ever bounded, while the system prompt, tool
    schemas and the reply grew independently on top of it."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 10_000)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_SAFETY_MARGIN", 0.0)
    mocker.patch("litellm.token_counter", side_effect=lambda model, text: len(text))

    allowance = chat_context.message_allowance_tokens(
        _model(),
        system_prompt="s" * 1_000,
        tool_schemas="t" * 2_000,
        max_output_tokens=4_000,
        message_count=0,
    )

    assert allowance == 10_000 - 1_000 - 2_000 - 4_000


def test_the_allowance_holds_back_a_margin_and_per_message_framing(mocker):
    """Our count is an under-estimate by construction, and under-counting is the
    direction that fails the call."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 10_000)
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_SAFETY_MARGIN", 0.05)
    mocker.patch("litellm.token_counter", side_effect=lambda model, text: len(text))

    allowance = chat_context.message_allowance_tokens(
        _model(), system_prompt="", tool_schemas="", max_output_tokens=0, message_count=10
    )

    assert allowance == 10_000 - 500 - (10 * chat_context._PER_MESSAGE_FRAMING_TOKENS)


def test_an_allowance_cannot_go_negative(mocker):
    """The fixed parts alone can exceed the window; callers must get 0, not a
    negative budget that reads as 'unbounded' further down."""
    mocker.patch("reporting.settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS", 1_000)
    mocker.patch("litellm.token_counter", side_effect=lambda model, text: len(text))

    assert (
        chat_context.message_allowance_tokens(_model(), system_prompt="s" * 5_000, tool_schemas="", max_output_tokens=0)
        == 0
    )


# --- Recognising a context overflow --------------------------------------------


def test_a_context_overflow_is_recognised_through_a_wrapper():
    """The error reaches us through langchain_litellm, which may have wrapped
    it, and providers word it differently."""
    for message in (
        "This model's maximum context length is 8192 tokens",
        "context_length_exceeded",
        "prompt is too long: 250000 tokens > 200000",
        "Please reduce the length of the messages",
    ):
        assert chat_context.is_context_overflow(ValueError(message)), message

    wrapped = RuntimeError("litellm call failed")
    wrapped.__cause__ = ValueError("maximum context length exceeded")
    assert chat_context.is_context_overflow(wrapped) is True


def test_a_context_overflow_is_recognised_by_type(mocker):
    from litellm import ContextWindowExceededError

    exc = ContextWindowExceededError(message="too big", model="m", llm_provider="p")
    assert chat_context.is_context_overflow(exc) is True


def test_other_failures_are_not_mistaken_for_overflow():
    """Retrying a smaller context cannot fix a rate limit or a bad key, and
    treating them as overflow would silently discard conversation."""
    for message in ("rate limit exceeded", "invalid api key", "connection reset by peer", "the tool call was invalid"):
        assert chat_context.is_context_overflow(ValueError(message)) is False


def test_overflow_detection_survives_a_self_referential_cause():
    exc = ValueError("something else")
    exc.__cause__ = exc
    assert chat_context.is_context_overflow(exc) is False


# --- Explicit cache breakpoints -------------------------------------------------


def _human(text: str, session_memory: bool = False):
    from langchain_core.messages import HumanMessage

    kwargs = {chat_context.SESSION_MEMORY_KEY: True} if session_memory else {}
    return HumanMessage(content=text, additional_kwargs=kwargs)


def test_only_providers_that_need_breakpoints_get_them(mocker):
    """Automatic prefix caches need a stable prefix and nothing else; rewriting
    their messages into blocks would risk a transformer for no gain."""
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)

    assert chat_context.supports_cache_breakpoints(_model("anthropic/claude-sonnet-4-6")) is True
    assert chat_context.supports_cache_breakpoints(_model("bedrock/anthropic.claude-3")) is True
    assert chat_context.supports_cache_breakpoints(_model("deepseek/deepseek-chat")) is False
    assert chat_context.supports_cache_breakpoints(_model("openai/gpt-4o")) is False

    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", False)
    assert chat_context.supports_cache_breakpoints(_model("anthropic/claude-sonnet-4-6")) is False


def test_a_non_anthropic_request_is_left_exactly_as_it_was(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    messages = [_human("one"), _human("two")]

    system, out = chat_context.with_cache_breakpoints(_model("deepseek/deepseek-chat"), "sys", messages)

    assert system == "sys"
    assert out is messages


def test_breakpoints_land_on_the_system_prompt_and_the_last_message(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_MIN_TOKENS", 0)
    mocker.patch("litellm.token_counter", return_value=5_000)

    system, out = chat_context.with_cache_breakpoints(
        _model("anthropic/claude-sonnet-4-6"), "sys", [_human("one"), _human("two")]
    )

    assert system == [{"type": "text", "text": "sys", "cache_control": {"type": "ephemeral"}}]
    assert out[0].content == "one"  # untouched
    assert out[-1].content[0]["cache_control"] == {"type": "ephemeral"}


def test_the_cached_prefix_ends_before_the_session_digest(mocker):
    """The digest differs every turn, so a prefix containing it can never be
    read back; ending just before it is what makes history cacheable at all."""
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_MIN_TOKENS", 0)
    mocker.patch("litellm.token_counter", return_value=5_000)

    messages = [_human("h1"), _human("a1"), _human("digest", session_memory=True), _human("latest")]
    _, out = chat_context.with_cache_breakpoints(_model("anthropic/claude-sonnet-4-6"), "sys", messages)

    assert isinstance(out[1].content, list)  # the message just before the digest
    assert out[2].content == "digest"  # the digest itself is never a breakpoint
    assert isinstance(out[3].content, list)  # and the rolling within-turn mark
    # Four is the provider's limit; we stay well inside it.
    assert sum(1 for m in out if isinstance(m.content, list)) <= 3


def test_a_short_system_prompt_is_not_reshaped_for_nothing(mocker):
    """Below the provider's minimum a prefix is not cached at all."""
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_MIN_TOKENS", 1_024)
    mocker.patch("litellm.token_counter", return_value=10)

    system, _ = chat_context.with_cache_breakpoints(_model("anthropic/claude-sonnet-4-6"), "short", [_human("one")])

    assert system == "short"


def test_messages_without_plain_text_are_left_alone(mocker):
    """An AI message mid-tool-call carries its payload in tool_calls, and
    reshaping those into blocks risks more than a cache hit is worth."""
    from langchain_core.messages import AIMessage

    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_MIN_TOKENS", 0)
    mocker.patch("litellm.token_counter", return_value=5_000)

    tool_call = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
    _, out = chat_context.with_cache_breakpoints(
        _model("anthropic/claude-sonnet-4-6"), "sys", [_human("one"), tool_call]
    )

    assert out[-1].content == ""
    assert out[-1].tool_calls == tool_call.tool_calls


def test_a_message_list_gets_a_rolling_breakpoint_on_its_last_entry(mocker):
    """The sandbox sub-agent owns its whole request as one list, so it needs a
    mark rolled along the end rather than a system/messages split. Leaving this
    path out cost the most: measured on one turn, the sub-agent was 200,761 of
    246,210 input tokens and read none of them."""
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    messages = [_human("system-ish"), _human("one"), _human("two")]

    out = chat_context.with_message_cache_breakpoints(_model("anthropic/claude-sonnet-4-6"), messages)

    assert out[0].content == "system-ish"
    assert out[1].content == "one"
    assert out[-1].content[0]["cache_control"] == {"type": "ephemeral"}


def test_content_already_in_block_form_is_marked_in_place(mocker):
    """The sub-agent normalizes tool results into text blocks before the call,
    so a string-only implementation silently marked nothing on that path."""
    from langchain_core.messages import ToolMessage

    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    blocks = [{"type": "text", "text": "tool output"}]
    messages = [_human("q"), ToolMessage(content=blocks, tool_call_id="c1")]

    out = chat_context.with_message_cache_breakpoints(_model("anthropic/claude-sonnet-4-6"), messages)

    assert out[-1].content[-1]["cache_control"] == {"type": "ephemeral"}
    assert out[-1].content[-1]["text"] == "tool output"
    # The original list is not mutated in place.
    assert blocks[-1] == {"type": "text", "text": "tool output"}


def test_a_non_anthropic_message_list_is_left_alone(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_PROMPT_CACHE_ENABLED", True)
    messages = [_human("one")]

    assert chat_context.with_message_cache_breakpoints(_model("deepseek/deepseek-chat"), messages) is messages
    assert chat_context.with_message_cache_breakpoints(_model("anthropic/claude-sonnet-4-6"), []) == []


# --- Cache divergence diagnostics ----------------------------------------------


@pytest.fixture
def _diagnostics_on(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CACHE_DIAGNOSTICS", True)
    mocker.patch("litellm.token_counter", side_effect=lambda model, text: max(1, len(text)))
    chat_context._FINGERPRINTS.clear()
    yield
    chat_context._FINGERPRINTS.clear()


def test_the_first_call_has_nothing_to_compare_against(_diagnostics_on):
    assert chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("one")]) is None


def test_an_unchanged_request_reports_no_divergence(_diagnostics_on):
    args = (_model(), "sys", "tools", [_human("one")])
    chat_context.diagnose_cache_divergence("k", *args)

    assert chat_context.diagnose_cache_divergence("k", *args) is None


def test_each_component_is_named_when_it_changes(_diagnostics_on):
    """The four parts a request has, which are the four a cache miss can blame."""
    base = (_model(), "sys", "tools", [_human("one"), _human("two")])

    for key, changed, expected in (
        ("model", (_model("other/model"), "sys", "tools", base[3]), "model_changed"),
        ("system", (_model(), "sys CHANGED", "tools", base[3]), "system_changed"),
        ("tools", (_model(), "sys", "tools CHANGED", base[3]), "tools_changed"),
        ("messages", (_model(), "sys", "tools", [_human("one"), _human("DIFFERENT")]), "messages_changed"),
    ):
        chat_context.diagnose_cache_divergence(key, *base)
        result = chat_context.diagnose_cache_divergence(key, *changed)
        assert result is not None and result[0] == expected, key
        assert result[1] > 0  # tokens behind the divergence


def test_appending_to_the_conversation_is_not_a_divergence(_diagnostics_on):
    """Growth is the normal shape of a tool loop and must not read as a miss."""
    chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("one")])

    grown = [_human("one"), _human("two"), _human("three")]
    assert chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", grown) is None


def test_truncating_history_is_reported(_diagnostics_on):
    """Dropping the oldest turns rewrites the prefix rather than extending it,
    which is exactly the sliding-window behaviour that costs a cache hit."""
    chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("a"), _human("b"), _human("c")])

    result = chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("a"), _human("b")])

    assert result is not None and result[0] == "messages_truncated"


def test_only_the_earliest_divergence_is_reported(_diagnostics_on):
    """Later ones hide behind it; fixing the first is what changes anything."""
    chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("one")])

    result = chat_context.diagnose_cache_divergence("k", _model(), "sys CHANGED", "tools CHANGED", [_human("X")])

    assert result is not None and result[0] == "tools_changed"  # tools precede the system prompt


def test_separate_keys_do_not_compare_against_each_other(_diagnostics_on):
    """A worker step and a synthesizer call have different prompts by design;
    comparing them would report a divergence on every call."""
    chat_context.diagnose_cache_divergence("worker", _model(), "worker sys", "tools", [_human("one")])

    assert chat_context.diagnose_cache_divergence("synth", _model(), "synth sys", "tools", [_human("one")]) is None


def test_nothing_is_recorded_while_diagnostics_are_off(mocker):
    mocker.patch("reporting.settings.CHAT_LLM_CACHE_DIAGNOSTICS", False)
    chat_context._FINGERPRINTS.clear()

    assert chat_context.diagnose_cache_divergence("k", _model(), "sys", "tools", [_human("one")]) is None
    assert chat_context._FINGERPRINTS == {}


def test_fingerprints_are_hashes_and_bounded(_diagnostics_on, mocker):
    """Never prompt content, and never unbounded: this runs per call."""
    mocker.patch.object(chat_context, "_FINGERPRINT_MAX", 4)
    for index in range(10):
        chat_context.diagnose_cache_divergence(f"k{index}", _model(), f"secret-{index}", "tools", [_human("body")])

    assert len(chat_context._FINGERPRINTS) <= 4
    stored = next(iter(chat_context._FINGERPRINTS.values()))
    assert "secret" not in stored.system
