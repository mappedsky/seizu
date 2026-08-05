"""How much context a model will actually take, and how much of it we are using.

Every context cap in chat used to be a fixed number of *characters*, and nothing
anywhere read the model's context window. That is wrong at both ends. Measured
against real tool payloads (graph JSON, not prose) the conversion is 3.0
characters per token, not the 4 the code assumed, so every character cap
admitted about a third more tokens than intended -- and worst exactly when
payloads are largest, because structured data tokenizes worse than text. Against
real windows, one fixed cap is simultaneously unsafe and wasteful: the default
120,000-character history cap is ~40,000 tokens, which overflows a 32k model's
entire window on history alone, and is 4% of a 1M-token model's.

So: budget in tokens, count them with the provider's own tokenizer, and take the
window from the model rather than from configuration.

**Fitting is not the same as filling.** The derived window is a *ceiling*, not a
target. ``CHAT_LLM_CONTEXT_MAX_TOKENS`` stays the "how much history is useful and
affordable" knob and the window only clamps it down, so pointing Seizu at a
million-token model does not silently multiply the cost of every call.
"""

import hashlib
import logging
import math
from typing import Any

from reporting import settings

logger = logging.getLogger(__name__)

# Counting is not free: a trim pass sizes every message, and the loop trims on
# every call. Cache on the content itself so repeated passes over an unchanged
# history are free, and bound it so a long-running process cannot accumulate one
# entry per message ever seen.
_TOKEN_CACHE: dict[tuple[str, str], int] = {}
_TOKEN_CACHE_MAX = 4_096
# Fallback when there is no tokenizer to ask. Deliberately the measured ratio for
# this workload rather than the conventional 4: over-counting is safe here and
# under-counting is what overflows a window.
_FALLBACK_CHARS_PER_TOKEN = 3.0


def model_name_of(model: Any) -> str:
    return str(getattr(model, "model_name", None) or getattr(model, "model", "") or "")


def count_tokens(model: Any, text: str) -> int:
    """Token count for ``text`` under ``model``'s tokenizer, cached by content."""
    if not text:
        return 0
    model_name = model_name_of(model)
    if not model_name:
        return math.ceil(len(text) / _FALLBACK_CHARS_PER_TOKEN)
    key = (model_name, hashlib.blake2b(text.encode(errors="replace"), digest_size=16).hexdigest())
    cached = _TOKEN_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from litellm import token_counter

        tokens = max(1, int(token_counter(model=model_name, text=text)))
    except Exception:
        tokens = math.ceil(len(text) / _FALLBACK_CHARS_PER_TOKEN)
    if len(_TOKEN_CACHE) >= _TOKEN_CACHE_MAX:
        # Whole-cache eviction rather than LRU bookkeeping: entries are cheap to
        # recompute and this runs once per few thousand distinct payloads.
        _TOKEN_CACHE.clear()
    _TOKEN_CACHE[key] = tokens
    return tokens


def context_window_tokens(model: Any) -> int:
    """The model's usable input window, in tokens.

    ``CHAT_LLM_CONTEXT_WINDOW_TOKENS`` overrides it outright. Otherwise it comes
    from litellm's model database, which knows every provider model we have
    checked (deepseek-chat 131k, claude-sonnet-4.5 200k, gpt-4o-mini 128k,
    gemini-2.0-flash and deepseek-v4-pro 1M). litellm raises for a model it does
    not know -- typically a self-hosted or custom deployment -- and that falls
    back to ``CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS``, deliberately small:
    guessing low wastes part of a window, guessing high fails the turn.
    """
    if settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS > 0:
        return settings.CHAT_LLM_CONTEXT_WINDOW_TOKENS
    model_name = model_name_of(model)
    fallback = max(1_024, settings.CHAT_LLM_CONTEXT_WINDOW_FALLBACK_TOKENS)
    if not model_name:
        return fallback
    cached = _WINDOW_CACHE.get(model_name)
    if cached is not None:
        return cached
    window = fallback
    try:
        from litellm import get_model_info

        info = get_model_info(model_name) or {}
        reported = int(info.get("max_input_tokens") or info.get("max_tokens") or 0)
        if reported > 0:
            window = reported
        else:
            logger.info("no context window reported for %s; using %d", model_name, fallback)
    except Exception:
        logger.info("unknown model %s; assuming a %d-token context window", model_name, fallback)
    _WINDOW_CACHE[model_name] = window
    return window


_WINDOW_CACHE: dict[str, int] = {}


def history_token_budget(model: Any) -> int:
    """Tokens of prior conversation one call may carry.

    The smaller of what is configured and what the model's window can spare, so
    a small model is clamped down to fit and a large one is not talked into
    spending its whole window on history.
    """
    configured = max(0, settings.CHAT_LLM_CONTEXT_MAX_TOKENS)
    share = min(max(settings.CHAT_LLM_CONTEXT_WINDOW_SHARE, 0.05), 0.95)
    allowed = int(context_window_tokens(model) * share)
    if configured <= 0:
        return allowed
    return min(configured, allowed)


def chars_for_tokens(model: Any, sample: str, tokens: int) -> int:
    """A character budget worth roughly ``tokens``, calibrated on ``sample``.

    For the places that must truncate *text* (a digest line) against a budget
    expressed in tokens. Measuring the ratio on the content in hand beats a
    constant, because the constant is wrong by a third on structured payloads
    and right on prose.
    """
    if tokens <= 0:
        return 0
    counted = count_tokens(model, sample) if sample else 0
    ratio = (len(sample) / counted) if counted else _FALLBACK_CHARS_PER_TOKEN
    return max(1, int(tokens * ratio))
