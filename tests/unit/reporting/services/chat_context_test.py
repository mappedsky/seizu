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
