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
from dataclasses import dataclass
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


def max_output_tokens(model: Any) -> int:
    """The most one response may be asked for, in tokens.

    Delegates to :func:`chat_models.derive_max_output_tokens`, so the ceiling a
    call *asks* for and the ceiling its model was *built* with cannot disagree
    -- they used to be computed separately from the same settings, which is a
    silent contradiction waiting to happen. An unknown model falls back to the
    configured cap: guessing low truncates an answer, where guessing high is
    refused outright by some providers.

    Exists because call sites were picking constants. A summary pass asking for
    1,024 tokens produced no text at all on a reasoning model -- the allowance
    went on reasoning and nothing was left to say -- and the number had no
    relationship to what that model could have given.
    """
    from reporting.services import chat_models

    return chat_models.derive_max_output_tokens(model_name_of(model))


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


# Providers frame each message with role/delimiter tokens we never see, and a
# tokenizer chosen by name can differ slightly from the one the endpoint runs.
# Both make our count an under-estimate, which is the direction that fails a
# call, so a margin comes off the window before anything is allowed to use it.
_PER_MESSAGE_FRAMING_TOKENS = 4


def message_allowance_tokens(
    model: Any,
    *,
    system_prompt: str,
    tool_schemas: Any,
    max_output_tokens: int,
    message_count: int = 0,
) -> int:
    """Tokens the conversation may occupy in one call, after the fixed parts.

    History was the only thing ever bounded, while the system prompt, tool
    schemas and the reply grew independently on top of it -- so "fits the model"
    was true of a part rather than of the request. This subtracts what the call
    must carry from the window and returns what is left for messages.
    """
    window = context_window_tokens(model)
    margin = min(max(settings.CHAT_LLM_CONTEXT_SAFETY_MARGIN, 0.0), 0.5)
    fixed = count_tokens(model, system_prompt)
    if tool_schemas:
        fixed += count_tokens(model, str(tool_schemas))
    fixed += max(0, message_count) * _PER_MESSAGE_FRAMING_TOKENS
    available = window - fixed - max(0, max_output_tokens) - int(window * margin)
    return max(0, available)


def is_context_overflow(exc: BaseException) -> bool:
    """Whether a provider rejected a call for exceeding its context window.

    Matched on the exception chain by type where litellm gives us one, and by
    message otherwise -- the error reaches us through langchain_litellm, which
    may have wrapped it, and providers word it differently ("context length",
    "prompt is too long", "maximum context").
    """
    overflow_type: type[BaseException] | None = None
    try:
        from litellm import ContextWindowExceededError

        overflow_type = ContextWindowExceededError
    except Exception:
        overflow_type = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if overflow_type is not None and isinstance(current, overflow_type):
            return True
        text = str(current).lower()
        if any(
            phrase in text
            for phrase in (
                "context length",
                "context_length_exceeded",
                "maximum context",
                "context window",
                "prompt is too long",
                "too many tokens",
                "reduce the length of the messages",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


# Anthropic caches nothing unless a content block is marked, and caches
# everything up to and including the marked block. Four marks are allowed per
# request; we use at most three.
_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}
SESSION_MEMORY_KEY = "seizu_session_memory"


def supports_cache_breakpoints(model: Any) -> bool:
    """Whether this model needs explicit cache markers to cache anything at all.

    Automatic prefix caches (DeepSeek, OpenAI, Gemini) need only a stable
    prefix and are left untouched -- marking their content would mean rewriting
    every message into block form for no gain, and blocks carrying an unknown
    key are exactly the sort of thing a provider transformer chokes on.
    """
    if not settings.CHAT_LLM_PROMPT_CACHE_ENABLED:
        return False
    name = model_name_of(model).lower()
    return "anthropic" in name or "claude" in name


def _marked(message: Any) -> Any:
    """The same message with a cache breakpoint on its text, or unchanged.

    Handles both shapes a message reaches us in: plain string content, and
    content already rewritten into blocks (the sandbox sub-agent normalizes tool
    results into text blocks before the call, so a string-only version silently
    marked nothing on the very path that carries most of a turn's tokens).

    An AI message mid-tool-call carries its payload in ``tool_calls`` rather
    than ``content``, and rewriting those shapes risks more than a cache hit is
    worth, so it is left alone.
    """
    content = getattr(message, "content", None)
    if isinstance(content, str):
        if not content.strip():
            return message
        return message.model_copy(
            update={"content": [{"type": "text", "text": content, "cache_control": _CACHE_CONTROL}]}
        )
    if isinstance(content, list) and content:
        blocks = [dict(block) if isinstance(block, dict) else block for block in content]
        for block in reversed(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                block["cache_control"] = _CACHE_CONTROL
                return message.model_copy(update={"content": blocks})
    return message


def with_message_cache_breakpoints(model: Any, messages: list[Any]) -> list[Any]:
    """Roll a cache breakpoint along a growing message list.

    For a loop that owns its whole request as one list -- the sandbox
    sub-agent's ``create_react_agent``, which carries the system prompt as the
    first message rather than as a separate argument. Marking the last message
    each call means the next one reads everything before it and writes only its
    own delta, which is the shape a tool loop has.

    This path was left out when breakpoints were first added, and it is not a
    minor one: measured on a real turn, the sub-agent was 200,761 of 246,210
    input tokens and read *none* of them, while the outer loop that did have
    breakpoints read 48%.
    """
    if not supports_cache_breakpoints(model) or not messages:
        return messages
    last = len(messages) - 1
    return [_marked(message) if index == last else message for index, message in enumerate(messages)]


def with_cache_breakpoints(model: Any, system_prompt: str, messages: list[Any]) -> tuple[Any, list[Any]]:
    """Place cache breakpoints for a provider that requires them.

    Three, in order of how much they are worth:

    1. **The system prompt.** The largest stable block, and Anthropic orders
       tool schemas ahead of it, so this one breakpoint covers both.
    2. **The message before the session digest.** The digest changes every turn,
       so a prefix that includes it can never be read back; ending the cached
       prefix just before it is what makes the conversation itself cacheable
       across turns.
    3. **The last message.** Within a turn the tool loop calls repeatedly with a
       growing list, so each call reads the previous prefix and writes only its
       own delta.

    Below ``CHAT_LLM_PROMPT_CACHE_MIN_TOKENS`` the provider will not cache a
    prefix at all, so a short system prompt is left unmarked rather than
    reshaped for nothing.
    """
    if not supports_cache_breakpoints(model) or not messages:
        return system_prompt, messages
    minimum = max(0, settings.CHAT_LLM_PROMPT_CACHE_MIN_TOKENS)
    system_content: Any = system_prompt
    if count_tokens(model, system_prompt) >= minimum:
        system_content = [{"type": "text", "text": system_prompt, "cache_control": _CACHE_CONTROL}]

    marks: set[int] = {len(messages) - 1}
    for index, message in enumerate(messages):
        if getattr(message, "additional_kwargs", None) and message.additional_kwargs.get(SESSION_MEMORY_KEY):
            if index > 0:
                marks.add(index - 1)
            break
    marked = [_marked(message) if index in marks else message for index, message in enumerate(messages)]
    return system_content, marked


# Bounded, and hashes only -- never prompt content. Keyed by caller-supplied
# label (phase + thread), so consecutive calls of the same kind are what get
# compared. Process-local and best-effort: this is a debugging aid, not a ledger.
_FINGERPRINTS: dict[str, "RequestFingerprint"] = {}
_FINGERPRINT_MAX = 512


def _digest(value: Any) -> str:
    return hashlib.blake2b(str(value).encode(errors="replace"), digest_size=8).hexdigest()


@dataclass(frozen=True)
class RequestFingerprint:
    """What a request was made of, as hashes -- enough to say what changed."""

    model: str
    system: str
    tools: str
    messages: tuple[str, ...]


def fingerprint_request(model: Any, system: Any, tools: Any, messages: list[Any]) -> RequestFingerprint:
    return RequestFingerprint(
        model=model_name_of(model),
        system=_digest(system),
        tools=_digest(tools),
        messages=tuple(_digest(getattr(m, "content", m)) for m in messages),
    )


def diagnose_cache_divergence(
    key: str, model: Any, system: Any, tools: Any, messages: list[Any]
) -> tuple[str, int] | None:
    """Say which part of this request differs from the last one under ``key``.

    Prompt caching matches the longest common *prefix*, so a cache miss is
    always "something before this point changed" -- and the only thing the usage
    numbers tell you is that it dropped to zero. This answers the question they
    do not: *what* changed. The components are the same four the provider's own
    diagnostics report (model, system, tools, messages), because those are the
    four parts a request has.

    Deliberately built rather than borrowed. Anthropic ships this as a beta, but
    it needs a beta header that our LiteLLM version constructs itself from
    feature detection and will not accept from a caller -- the body parameter
    reaches the API without it and the request is rejected outright. This works
    on every provider instead, including the ones with automatic prefix caching
    where no such feature exists.

    Returns the component and a rough count of the tokens sitting behind the
    divergence, or ``None`` when nothing changed. Token order assumes the
    provider puts tools ahead of the system prompt (Anthropic's layout); for
    providers that order them the other way the count is approximate, and the
    component name -- the actionable part -- is right regardless.
    """
    if not settings.CHAT_LLM_CACHE_DIAGNOSTICS:
        return None
    current = fingerprint_request(model, system, tools, messages)
    # Scoped to a lineage -- the opening message -- as well as to the caller's
    # key. A phase label alone compares things that were never going to match: a
    # plan reuses step ids across turns, so turn 2's "worker:s2" was diffed
    # against turn 1's unrelated "worker:s2" and reported a divergence on every
    # first call, which is noise in the one place a reader is looking for signal.
    # The cost is that a request whose *opening* message changed reads as a new
    # lineage rather than a divergence; between turns that is exactly what it is,
    # and callers whose opening message is constant (the sandbox sub-agent, whose
    # first message is its system prompt) pass a key precise enough to separate
    # their own instances.
    lineage = _digest(current.messages[:1])
    scoped_key = f"{key}:{lineage}"
    previous = _FINGERPRINTS.get(scoped_key)
    if len(_FINGERPRINTS) >= _FINGERPRINT_MAX:
        _FINGERPRINTS.clear()
    _FINGERPRINTS[scoped_key] = current
    if previous is None:
        return None

    behind_everything = count_tokens(model, str(tools)) + count_tokens(model, str(system))
    behind_everything += sum(count_tokens(model, str(getattr(m, "content", m))) for m in messages)
    if previous.model != current.model:
        return "model_changed", behind_everything
    if previous.tools != current.tools:
        return "tools_changed", behind_everything
    if previous.system != current.system:
        return "system_changed", behind_everything - count_tokens(model, str(tools))
    for index, (was, now) in enumerate(zip(previous.messages, current.messages, strict=False)):
        if was != now:
            return "messages_changed", sum(count_tokens(model, str(getattr(m, "content", m))) for m in messages[index:])
    if len(current.messages) < len(previous.messages):
        # Truncation: the history was rewritten rather than appended to, which
        # is the one message change that is not a plain extension.
        return "messages_truncated", behind_everything
    return None


def log_cache_divergence(key: str, model: Any, system: Any, tools: Any, messages: list[Any]) -> None:
    """Diagnose and log, for callers that only want the side effect."""
    diagnosis = diagnose_cache_divergence(key, model, system, tools, messages)
    if diagnosis is None:
        return
    reason, tokens = diagnosis
    logger.warning("cache diagnostic [%s]: %s, ~%d tokens behind the divergence", key, reason, tokens)


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
